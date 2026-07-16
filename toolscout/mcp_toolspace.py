"""McpCatalog — the toolspace backed by EXTERNAL MCP servers (the live, non-demo path).

A thin adapter over rlm-kit's public `rlm_kit.mcp.McpCatalog` (its multi-server MCP transport). The
kit owns the load-bearing mechanics — the per-server async→sync bridge, the connect lifecycle, the
partial-eager-connect cleanup, and the per-call timeout+cancel; toolscout maps the kit's RAW MCP
tools onto its own scaffolded `ToolSpec`/`Param` shape and disperses the ISL/ITL/PTC surface:

- ISL: `servers()` lists the declared servers; `load(server)` MATERIALIZES one (the kit connects + lists tools).
- ITL: `describe(names)` returns the scaffolded `ToolSpec`s of already-materialized tools.
- PTC: `call(server, tool, args)` dispatches via the kit and returns the flattened result TEXT.

Earlier this module hand-copied rlm-kit's single-server bridge, because the kit only exposed a PRIVATE
`_MCPBridge` and a `dspy.Tool`-shaped `mcp_tools` (the wrong shape for a MANY-server progressive
catalog). The kit now ships a public, multi-server `McpCatalog` (a raw-tool transport that records
nothing), so this drops the copied bridge and keeps only the scaffolding MAPPING (raw MCP `Tool` →
`ToolSpec`) — the promoted-back generalization the earlier workaround anticipated.

This module records NOTHING itself — the single canonical `tool_call` is emitted by the `call_tool`
meta-tool in `toolspace.py`; the kit catalog is a pure transport too.

CONNECTION SAFETY: the kit connects EAGER (host-side, pre-run) by default — a live subprocess spawn
INSIDE the RLM's `aforward` loop can hang dspy/asyncio (a hard-won live-run lesson). `connect="lazy"`
defers a server's connection to its first `load`, which spawns a subprocess mid-loop; keep it opt-in
and experimental until a live-safety spike clears it.

SECURITY: MCP servers execute HOST-SIDE (outside the sandbox); a stdio server is a spawned subprocess.
Treat the server as a TRUSTED dependency and its output as UNTRUSTED LM context (a prompt-injection
surface, like a fetched page) — the scaffolding layer length-caps what enters the planner's REPL.

dspy-free (uses only `rlm_kit.mcp` + stdlib; the kit pulls the mcp SDK lazily on connect); imported
LAZILY by `catalog.load_catalog` only when a real toolspace is configured, so `import toolscout` and
the offline tests never pull the mcp SDK.
"""

from __future__ import annotations

from typing import Any

from rlm_kit.mcp import McpCatalog as _KitMcpCatalog

from .catalog import Catalog, Param, ServerInfo, ToolSpec

# JSON-Schema primitive → the scaffolding hint string (`str | int | float | bool | list | dict | Any`).
_JSON_TYPE = {"string": "str", "integer": "int", "number": "float", "boolean": "bool",
              "array": "list", "object": "dict"}


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


def _returns_from_schema(output_schema: Any) -> str:
    """Map an MCP tool's declared `outputSchema` (JSON Schema) to a compact one-line return hint:
    an object → `{field: hint, ...}`; a scalar/array → its bare hint; absent/opaque → ``""``."""
    if not isinstance(output_schema, dict):
        return ""
    jtype = output_schema.get("type")
    if jtype == "object":
        props = output_schema.get("properties")
        if isinstance(props, dict) and props:
            fields = []
            for k, v in props.items():
                vt = v.get("type") if isinstance(v, dict) else None
                fields.append(f"{k}: {_JSON_TYPE.get(vt if isinstance(vt, str) else '', 'Any')}")
            return "{" + ", ".join(fields) + "}"
        return "dict"
    if isinstance(jtype, str):
        return _JSON_TYPE.get(jtype, "Any")
    return ""


class McpCatalog(Catalog):
    """A `Catalog` over external MCP servers — a thin adapter mapping rlm-kit's `McpCatalog` (the
    raw-tool transport) onto toolscout's scaffolded `ToolSpec` surface. The kit owns spec validation,
    connect lifecycle, and hang-safety; this maps its raw MCP `Tool`s to `ToolSpec`/`Param` and
    delegates the rest."""

    def __init__(self, specs: list[dict], *, connect: str = "eager", timeout: float = 60.0) -> None:
        # The kit validates specs ('name' required) + `connect`, connects eagerly, and tears down a
        # partial eager connect on failure — the transport mechanics toolscout used to own live there now.
        self._mcp = _KitMcpCatalog(specs, connect=connect, timeout=timeout)

    def servers(self) -> list[ServerInfo]:
        return [ServerInfo(name, desc) for name, desc in self._mcp.servers()]

    def has_server(self, server: str) -> bool:
        return self._mcp.has_server(server)

    def load(self, server: str) -> None:
        self._mcp.load(server)  # no-op if already connected (eager); connects on demand (lazy)

    def tool_names(self, server: str) -> list[str]:
        return self._mcp.tool_names(server)

    def describe(self, names: list[str]) -> list[ToolSpec]:
        wanted = set(names)
        out: list[ToolSpec] = []
        for name, _desc in self._mcp.servers():
            for t in self._mcp.tools(name):  # only CONNECTED servers return tools (ISL discipline)
                if t.name in wanted:
                    out.append(ToolSpec(server=name, name=t.name, description=t.description or "",
                                        params=_params_from_schema(getattr(t, "inputSchema", None)),
                                        returns=_returns_from_schema(getattr(t, "outputSchema", None))))
        return out

    def call(self, server: str, tool: str, args: dict) -> Any:
        return self._mcp.call(server, tool, args or {})  # returns the flattened result TEXT

    def close(self) -> None:
        self._mcp.close()
