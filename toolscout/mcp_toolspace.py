"""McpCatalog — the toolspace backed by EXTERNAL MCP servers (the live, non-demo path).

This is the `Catalog` the meta-tools sit on when `TS_TOOLSPACE` names a JSON list of MCP server specs.
It connects to each server host-side (client-only; toolscout never runs a server) and disperses the
ISL/ITL/PTC surface:

- ISL: `servers()` lists the declared servers; `load(server)` MATERIALIZES one (connect + list its tools).
- ITL: `describe(names)` returns the scaffolded `ToolSpec`s of already-materialized tools.
- PTC: `call(server, tool, args)` bridges one call to that server and returns the flattened result TEXT.

Why a bridge: the MCP SDK is ASYNC (`ClientSession.call_tool` is a coroutine) but RLM tools must be SYNC
(dspy invokes them with no await), so each server runs a `ClientSession` in a dedicated background thread
+ event loop kept alive for the catalog's lifetime, and a sync call bridges one coroutine across the
thread boundary via `run_coroutine_threadsafe(...).result(timeout)` — the SAME shape as rlm-kit's
`mcp.py`. It does NOT reuse rlm-kit's private `_MCPBridge` (a `_`-name is not the public surface) nor
`mcp_tools` (that yields one server's tools as dspy.Tools, the wrong shape for a MANY-server progressive
catalog). This module records NOTHING itself — the single canonical `tool_call` is emitted by the
`call_tool` meta-tool in `toolspace.py`; the catalog is a pure transport.

*rlm-kit already owns a single-server bridge; a multi-server ISL/ITL catalog bridge could one day be
generalized into the kit, which would let this module shrink to a thin adapter.* Until then this
self-contained bridge is the honest workaround.

CONNECTION SAFETY: connect EAGER (host-side, pre-run) by default — a live subprocess spawn INSIDE the
RLM's `aforward` loop can hang dspy/asyncio (a hard-won live-run lesson). `connect="lazy"` defers
a server's connection to its first `load_server`, which spawns a subprocess mid-loop; keep it opt-in and
experimental until a live-safety spike clears it.

SECURITY: MCP servers execute HOST-SIDE (outside the sandbox); a stdio server is a spawned subprocess.
Treat the server as a TRUSTED dependency and its output as UNTRUSTED LM context (a prompt-injection
surface, like a fetched page) — the scaffolding layer length-caps what enters the planner's REPL.

dspy-free (uses only the mcp SDK + stdlib); imported LAZILY by `catalog.load_catalog` only when a real
toolspace is configured, so `import toolscout` and the offline tests never pull the mcp SDK.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import threading
from typing import Any, Optional

from .catalog import Catalog, Param, ServerInfo, ToolSpec

# JSON-Schema primitive → the scaffolding hint string (`str | int | float | bool | list | dict | Any`).
_JSON_TYPE = {"string": "str", "integer": "int", "number": "float", "boolean": "bool",
              "array": "list", "object": "dict"}


def _require_mcp() -> None:
    try:
        import mcp  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "The MCP toolspace requires the mcp SDK. It is a core dependency of toolscout; run "
            "`uv sync` (or `pip install 'mcp>=1.0'`) in this environment."
        ) from exc


def _result_text(result: Any) -> str:
    """Flatten a `CallToolResult` to text: join TextContent blocks; fall back to structuredContent JSON."""
    parts = [b.text for b in (getattr(result, "content", None) or []) if getattr(b, "text", None) is not None]
    out = "\n".join(parts).strip()
    if not out:
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            try:
                out = json.dumps(structured, ensure_ascii=False, default=str)
            except Exception:  # noqa: BLE001
                out = str(structured)
    if getattr(result, "isError", False):
        out = f"[tool reported an error] {out}".strip()
    return out


def _params_from_schema(input_schema: Any) -> list[Param]:
    """Map an MCP tool's `inputSchema` (JSON Schema) to scaffolding `Param`s."""
    if not isinstance(input_schema, dict):
        return []
    props = input_schema.get("properties")
    if not isinstance(props, dict):
        return []
    required = set(input_schema.get("required") or [])
    out: list[Param] = []
    for name, frag in props.items():
        frag = frag if isinstance(frag, dict) else {}
        jtype = frag.get("type")
        hint = _JSON_TYPE.get(jtype if isinstance(jtype, str) else "", "Any")
        out.append(Param(name=str(name), type=hint, required=name in required,
                         default=frag.get("default"), description=str(frag.get("description", ""))))
    return out


class _ServerBridge:
    """One MCP `ClientSession` on a background thread + loop, kept alive until `close`. Sync callers
    bridge a coroutine across the thread boundary. No tracing here — a pure transport."""

    def __init__(self, server: dict, *, timeout: float) -> None:
        self._server = server
        self._timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="toolscout-mcp", daemon=True)
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._stop: Optional[asyncio.Event] = None
        self._session: Any = None
        self.tools: list = []

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001 — surfaced to start()
            self._error = exc
            self._ready.set()
        finally:
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    async def _serve(self) -> None:
        from mcp import ClientSession

        self._stop = asyncio.Event()
        async with self._transport() as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                self._session = session
                self.tools = list(listed.tools)
                self._ready.set()
                await self._stop.wait()

    def _transport(self):
        srv = self._server
        if srv.get("url"):
            import mcp.client.streamable_http as _sh

            streamable_client = getattr(_sh, "streamable_http_client", None) or _sh.streamablehttp_client
            return streamable_client(srv["url"])
        if not srv.get("command"):
            raise ValueError(f"MCP server {srv.get('name')!r} needs a 'url' or a 'command'")
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        return stdio_client(StdioServerParameters(
            command=srv["command"], args=list(srv.get("args", [])), env=srv.get("env")))

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(self._timeout):
            raise TimeoutError(f"MCP server {self._server.get('name')!r} not ready within {self._timeout}s")
        if self._error is not None:
            raise self._error

    def call(self, name: str, arguments: dict) -> Any:
        if self._session is None:
            raise RuntimeError("MCP session is not connected")
        fut = asyncio.run_coroutine_threadsafe(self._session.call_tool(name, arguments or {}), self._loop)
        try:
            return fut.result(self._timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()  # don't leave a hung coroutine wedging the serial session
            raise TimeoutError(f"MCP tool {name!r} timed out after {self._timeout}s") from None

    def close(self) -> None:
        if self._stop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(self._timeout)


class McpCatalog(Catalog):
    """A `Catalog` over a set of external MCP servers, connected via per-server sync bridges."""

    def __init__(self, specs: list[dict], *, connect: str = "eager", timeout: float = 60.0) -> None:
        _require_mcp()
        self._specs: dict[str, dict] = {}
        for s in specs:
            if not isinstance(s, dict) or not s.get("name"):
                raise ValueError("each toolspace spec must be a dict with a 'name'")
            self._specs[str(s["name"])] = s
        self._timeout = timeout
        self._bridges: dict[str, _ServerBridge] = {}
        if connect == "eager":
            try:
                for name in self._specs:
                    self._connect(name)
            except Exception:
                # A later server's connect failed — the servers already connected are live threads +
                # subprocesses with no object left for the caller to close(). Tear them down before
                # propagating, so a partial eager connect never leaks (the long-lived studio retries this).
                self.close()
                raise

    def _connect(self, server: str) -> _ServerBridge:
        if server in self._bridges:
            return self._bridges[server]
        if server not in self._specs:
            raise KeyError(server)
        bridge = _ServerBridge(self._specs[server], timeout=self._timeout)
        try:
            bridge.start()
        except Exception:
            with contextlib.suppress(Exception):
                bridge.close()  # a failed start still spawned a thread/subprocess — don't leak it
            raise
        self._bridges[server] = bridge
        return bridge

    def servers(self) -> list[ServerInfo]:
        return [ServerInfo(name, str(spec.get("description", ""))) for name, spec in self._specs.items()]

    def has_server(self, server: str) -> bool:
        return server in self._specs

    def load(self, server: str) -> None:
        self._connect(server)  # no-op if already connected (eager); connects on demand (lazy)

    def _mcp_tools(self, server: str) -> list:
        bridge = self._bridges.get(server)
        return bridge.tools if bridge is not None else []

    def tool_names(self, server: str) -> list[str]:
        return [t.name for t in self._mcp_tools(server)]

    def _spec_of(self, server: str, mcp_tool: Any) -> ToolSpec:
        return ToolSpec(server=server, name=mcp_tool.name, description=mcp_tool.description or "",
                        params=_params_from_schema(getattr(mcp_tool, "inputSchema", None)))

    def describe(self, names: list[str]) -> list[ToolSpec]:
        wanted = set(names)
        out: list[ToolSpec] = []
        for server in self._bridges:  # only CONNECTED servers are describable (ISL discipline)
            for t in self._mcp_tools(server):
                if t.name in wanted:
                    out.append(self._spec_of(server, t))
        return out

    def call(self, server: str, tool: str, args: dict) -> Any:
        bridge = self._bridges.get(server)
        if bridge is None:
            raise RuntimeError(f"server {server!r} is not connected (load it first)")
        return _result_text(bridge.call(tool, args or {}))

    def close(self) -> None:
        for bridge in self._bridges.values():
            with contextlib.suppress(Exception):
                bridge.close()
        self._bridges.clear()
