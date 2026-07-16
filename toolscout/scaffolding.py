"""The scaffolding layer (ATLAS) — normalize heterogeneous tool schemas into a clean, uniform surface.

Three jobs, all deterministic + pure (dspy-free, mcp-free):
  1. RENDER a tool/server into compact, Python-style text the planner reads (ITL disclosure) — with a
     length cap, because server-authored names/descriptions/schemas are UNTRUSTED input entering the
     planner's context (a prompt-injection surface — untrusted preview text must be capped before entry).
  2. COERCE the planner's `args` dict to the hinted native types, with an INFORMATIVE error string on a
     bad/missing arg ("tool `add` needs `b` (int); got none") — the ATLAS "errors are fixable by a
     localized edit" principle. Errors are TEXT the RLM reacts to, never a raise into the loop.
  3. Convert a serialized tool OUTPUT to a native Python value (`ast.literal_eval`, best-effort).

No `*args`/`**kwargs` anywhere in a signature we render or dispatch — dspy's sandbox stub generation
flattens a `VAR_KEYWORD` into a required positional, so the meta-tools take explicit params only.
"""

from __future__ import annotations

import ast
from typing import Any

from .catalog import Param, ServerInfo, ToolSpec


def _cap(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def signature(tool: ToolSpec) -> str:
    """A Python-style call signature, e.g. `add(a: int, b: int)` / `set(key: str, value: Any = None)`."""
    parts: list[str] = []
    for p in tool.params:
        piece = f"{p.name}: {p.type}"
        if not p.required:
            piece += f" = {p.default!r}"
        parts.append(piece)
    return f"{tool.name}({', '.join(parts)})"


RETURNS_CAP = 200   # cap for a tool's declared return-type hint
EXAMPLE_CAP = 300   # cap for an example output line (observed or static)


def render_tool(tool: ToolSpec, max_chars: int = 1200, *, observed_example: str = "") -> str:
    """Full ITL disclosure for one tool: signature + capped description + per-param notes, and — when
    available — a declared return-type hint and ONE example output (observed this run if given, else the
    catalog's static example). `returns`/example are UNTRUSTED (server schema / tool output), so `_cap`'s
    whitespace-collapse renders each as a single flat line — never a fake schema block or code fence."""
    lines = [f"{tool.server}:{tool.name}  →  {signature(tool)}"]
    if tool.description:
        lines.append(f"    {_cap(tool.description, max_chars)}")
    for p in tool.params:
        note = f"    - {p.name} ({p.type}{'' if p.required else ', optional'})"
        if p.description:
            note += f": {_cap(p.description, 200)}"
        lines.append(note)
    if tool.returns:
        # "declared": on the MCP path call_tool returns result_text-flattened TEXT, which may not match
        # the tool's declared structured schema — this hint describes the schema, not the runtime type.
        lines.append(f"    declared returns: {_cap(tool.returns, RETURNS_CAP)}")
    if observed_example:
        lines.append(f"    example (observed this run) → {_cap(observed_example, EXAMPLE_CAP)}")
    elif tool.example_output:
        lines.append(f"    example → {_cap(tool.example_output, EXAMPLE_CAP)}")
    return "\n".join(lines)


def render_server_index(servers: list[ServerInfo], max_chars: int = 1200) -> str:
    """The ISL server index the planner sees first: names + capped descriptions, no tool schemas."""
    if not servers:
        return "(no servers configured)"
    return "\n".join(f"- {s.name}: {_cap(s.description, max_chars)}" for s in servers)


# A tiny sandbox-side proxy the planner defines (from its instructions) so a multi-call orchestration
# reads as `srv = MCPServer("math"); srv.add(a=2, b=3)` instead of repeated call_tool(...). It is pure
# SUGAR: every attribute call routes through the real `call_tool` meta-tool, so each invocation still
# records exactly one canonical `tool_call`. `_name` lives in __dict__ (so __getattr__ never recurses)
# and a leading-underscore guard stops repr/inspect probes from dispatching a spurious call. It
# re-nativizes a number/list-shaped TEXT result (dspy's REPL boundary str()s non-list/dict tool returns)
# — the same best-effort semantics as `to_native`; an error STRING fails literal_eval and passes through.
PROXY_SOURCE = '''\
class MCPServer:
    """srv.tool(**named_args) == call_tool("<server>", "tool", {named args}). Named arguments ONLY.
    Sugar over call_tool — every call still routes through it. Define once, reuse per server."""
    def __init__(self, name):
        self._name = name
    def __getattr__(self, tool):
        if tool.startswith("_"):
            raise AttributeError(tool)
        def _invoke(**kwargs):
            out = call_tool(self._name, tool, kwargs)
            if isinstance(out, str):
                try:
                    import ast
                    return ast.literal_eval(out)
                except (ValueError, SyntaxError):
                    pass
            return out
        return _invoke
'''


def render_proxy_hint(server: str) -> str:
    """A one-line moment-of-need nudge appended to the FIRST load_server result: the MCPServer proxy
    (defined from the instructions) makes per-server calls read as `srv.<tool>(<named args>)`."""
    return (f"Tip: define the MCPServer proxy from your instructions, then "
            f"`srv = MCPServer({server!r}); srv.<tool>(<named args>)` — sugar over call_tool.")


class ArgError(ValueError):
    """A friendly, planner-actionable argument error. Callers turn it into an error STRING, never raise."""


def _coerce_one(p: Param, value: Any) -> Any:
    t = (p.type or "str").lower()
    try:
        if t == "int":
            return int(value)
        if t == "float":
            return float(value)
        if t == "bool":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if t == "str":
            return str(value)
        if t in ("list", "dict"):
            if isinstance(value, (list, dict)):
                return value
            return ast.literal_eval(value) if isinstance(value, str) else value
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ArgError(f"argument `{p.name}` must be {p.type}; got {value!r} ({exc})") from None
    return value  # "Any" / unknown hint → pass through


def coerce_args(tool: ToolSpec, args: dict) -> dict:
    """Validate + coerce `args` against `tool.params`. Raises `ArgError` (a friendly message) on trouble."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ArgError(f"`args` for {tool.name} must be a dict of named parameters, got {type(args).__name__}")
    known = {p.name for p in tool.params}
    unknown = [k for k in args if k not in known]
    if unknown:
        raise ArgError(
            f"tool `{tool.name}` got unknown argument(s) {unknown}; its parameters are: {sorted(known)}"
        )
    out: dict = {}
    for p in tool.params:
        if p.name in args and args[p.name] is not None:
            out[p.name] = _coerce_one(p, args[p.name])
        elif p.required:
            raise ArgError(f"tool `{tool.name}` needs `{p.name}` ({p.type}); it was not provided")
        elif p.default is not None:
            out[p.name] = p.default
    return out


def unknown_server_error(server: str, available: list[str]) -> str:
    hint = _did_you_mean(server, available)
    return f"No server named {server!r}. Available servers: {available}." + hint


def unknown_tool_error(server: str, tool: str, available: list[str]) -> str:
    hint = _did_you_mean(tool, available)
    return f"Server {server!r} has no tool {tool!r}. Its tools: {available}." + hint


def _did_you_mean(name: str, options: list[str]) -> str:
    import difflib

    close = difflib.get_close_matches(name, options, n=1, cutoff=0.6)
    return f" Did you mean {close[0]!r}?" if close else ""


def to_native(value: Any) -> Any:
    """Best-effort: turn a serialized tool output string into a native Python value; pass non-strings."""
    if not isinstance(value, str):
        return value
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value
