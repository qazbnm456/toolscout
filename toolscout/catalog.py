"""The toolspace catalog — the abstraction ISL/ITL disclose progressively.

A `Catalog` is the source the meta-tools (`toolspace.py`) sit on: a SERVER INDEX (ISL) whose per-server
tool schemas materialize on demand (ITL), plus a `call` dispatcher (PTC). Two backings:

- `StaticCatalog` — a fixed dict of servers → pure-Python tools. The offline DEFAULT (a small demo
  toolspace) and the CI/test fixture: no MCP subprocess, deterministic, hang-proof.
- the MCP-backed catalog (`mcp_toolspace.McpCatalog`, imported lazily only when `TS_TOOLSPACE` is set) —
  connects to EXTERNAL MCP servers host-side (eager-connect, pre-run) and dispatches over rlm-kit's
  sync bridge. Client-only; toolscout never runs a server.

dspy-free and mcp-free at module top (the MCP backing is a lazy import in `load_catalog`), so this
module + the meta-tools stay unit-testable without dspy, mcp, or a live server.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Param:
    name: str
    type: str = "str"           # a hint string: str | int | float | bool | list | dict | Any
    required: bool = True
    default: Any = None
    description: str = ""


@dataclass
class ToolSpec:
    server: str
    name: str
    description: str = ""
    params: list[Param] = field(default_factory=list)
    invoke: Optional[Callable[[dict], Any]] = None  # (args_dict) -> result; None for a not-yet-materialized MCP tool
    # Appended AFTER invoke on purpose: demo_catalog constructs ToolSpec positionally with invoke 5th,
    # so inserting these earlier would rebind those positions. Both are optional ITL disclosure hints.
    returns: str = ""          # a compact return-type hint, e.g. from an MCP tool's declared outputSchema
    example_output: str = ""   # a static example of the tool's output, when the catalog supplies one


@dataclass(frozen=True)
class ServerInfo:
    name: str
    description: str = ""


class Catalog:
    """Interface the meta-tools use. Backends override `describe` + `call` (and optionally `load`)."""

    def servers(self) -> list[ServerInfo]:
        raise NotImplementedError

    def tool_names(self, server: str) -> list[str]:
        raise NotImplementedError

    def describe(self, names: list[str]) -> list[ToolSpec]:
        raise NotImplementedError

    def call(self, server: str, tool: str, args: dict) -> Any:
        raise NotImplementedError

    def load(self, server: str) -> None:
        """ISL hook. Eager backends no-op (already connected pre-run); a lazy backend connects here."""
        return None

    def has_server(self, server: str) -> bool:
        return any(s.name == server for s in self.servers())

    def close(self) -> None:
        return None


class StaticCatalog(Catalog):
    """A catalog over an in-process dict of servers → `ToolSpec`s with pure-Python `invoke`s."""

    def __init__(self, servers: dict[str, tuple[str, list[ToolSpec]]]) -> None:
        # {server_name: (server_description, [ToolSpec, ...])}
        self._servers = servers

    def servers(self) -> list[ServerInfo]:
        return [ServerInfo(name, desc) for name, (desc, _tools) in self._servers.items()]

    def _tools(self, server: str) -> list[ToolSpec]:
        if server not in self._servers:
            raise KeyError(server)
        return self._servers[server][1]

    def tool_names(self, server: str) -> list[str]:
        return [t.name for t in self._tools(server)]

    def describe(self, names: list[str]) -> list[ToolSpec]:
        wanted = set(names)
        out: list[ToolSpec] = []
        for _server, (_desc, tools) in self._servers.items():
            for t in tools:
                if t.name in wanted:
                    out.append(t)
        return out

    def call(self, server: str, tool: str, args: dict) -> Any:
        for t in self._tools(server):
            if t.name == tool:
                if t.invoke is None:
                    raise RuntimeError(f"tool {tool!r} on server {server!r} has no invoke")
                return t.invoke(args)
        raise KeyError(f"{server}:{tool}")


def demo_catalog() -> StaticCatalog:
    """A small, deterministic, offline toolspace — the keyless demo AND the test fixture.

    Four servers exercise the full ISL/ITL/PTC surface (multiple servers, multiple tools, and stateful
    tools that demonstrate PTC's persistent REPL state). No network, no clock, no external deps."""
    store: dict[str, Any] = {}

    def _echo(a: dict) -> str:
        return str(a.get("text", ""))

    def _add(a: dict) -> int:
        return int(a["a"]) + int(a["b"])

    def _mul(a: dict) -> int:
        return int(a["a"]) * int(a["b"])

    def _mset(a: dict) -> str:
        store[str(a["key"])] = a.get("value")
        return "ok"

    def _mget(a: dict) -> Any:
        return store.get(str(a["key"]))

    def _upper(a: dict) -> str:
        return str(a.get("text", "")).upper()

    def _wordcount(a: dict) -> int:
        return len(str(a.get("text", "")).split())

    servers: dict[str, tuple[str, list[ToolSpec]]] = {
        "echo": ("Echo text back — a trivial connectivity check.", [
            ToolSpec("echo", "echo", "Return the given text unchanged.",
                     [Param("text", "str", True, None, "text to echo")], _echo),
        ]),
        "math": ("Integer arithmetic.", [
            ToolSpec("math", "add", "Add two integers.",
                     [Param("a", "int"), Param("b", "int")], _add),
            ToolSpec("math", "mul", "Multiply two integers.",
                     [Param("a", "int"), Param("b", "int")], _mul),
        ]),
        "memory": ("A tiny key/value store, persistent across calls within one run.", [
            ToolSpec("memory", "set", "Store a value under a key.",
                     [Param("key", "str"), Param("value", "Any", False, None, "value to store")], _mset),
            ToolSpec("memory", "get", "Fetch the value stored under a key (None if unset).",
                     [Param("key", "str")], _mget),
        ]),
        "text": ("String utilities.", [
            ToolSpec("text", "upper", "Upper-case the given text.",
                     [Param("text", "str")], _upper),
            ToolSpec("text", "wordcount", "Count whitespace-separated words.",
                     [Param("text", "str")], _wordcount),
        ]),
    }
    return StaticCatalog(servers)


def load_catalog(config) -> Catalog:
    """The DEFAULT (no `TS_TOOLSPACE`) is the offline demo catalog; a toolspace JSON → the MCP backing.

    The MCP backing is imported LAZILY here so this module (and the meta-tools + tests) stay mcp-free.
    """
    path = getattr(config, "toolspace_path", "") or ""
    if not path:
        return demo_catalog()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"TS_TOOLSPACE points at {path!r} (resolved to {os.path.abspath(path)!r}), which does not "
            "exist — a relative path is resolved against the current working directory"
        )
    with open(path, encoding="utf-8") as fh:
        specs = json.load(fh)
    from .mcp_toolspace import McpCatalog  # lazy: pulls the mcp SDK only for a real toolspace

    return McpCatalog(specs, connect=getattr(config, "connect", "eager"),
                      timeout=getattr(config, "mcp_timeout", 60.0))
