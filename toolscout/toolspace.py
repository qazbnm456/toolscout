"""The ISL/ITL/PTC meta-tools — the four fixed tools the planner uses to explore a large toolspace.

ATLAS's core move is that the planner does NOT get every server's every tool schema up front (that blows
the context and drowns a small model). Instead it gets four SMALL, FIXED meta-tools and drives disclosure
itself:

- `list_servers()`      — ISL step 1: the server INDEX (names + capped descriptions, no schemas).
- `load_server(server)` — ISL step 2: MATERIALIZE one server (a decision recorded in the trajectory);
                          returns just its tool NAMES.
- `describe_tools(names)`— ITL: pull the full signature/params for a FEW named tools, just-in-time.
- `call_tool(server, tool, args)` — PTC: invoke a materialized tool; the result is a native Python value
                          the planner keeps in a REPL variable and computes on.

Why FIXED meta-tools and not one dspy tool per MCP tool: dspy.RLM registers its tools at CONSTRUCTION
(`PythonInterpreter` stubs are generated once); there is no mid-run tool registration. So the whole
toolspace is reached THROUGH these four, and every ISL/ITL/PTC decision lands in the trace as a
`tool_call` — the RL signal ATLAS's rubric scores (Tool Appropriateness / Grounding / Parameter Accuracy).

Every tool here is SYNC (dspy invokes tools synchronously — an async tool would return an un-awaited
coroutine) and takes EXPLICIT params (no `*args`/`**kwargs` — dspy's stub generator turns a VAR_KEYWORD
into a required positional). Each factory PINS `fn.__name__` to the exact name the prompt/instructions use,
because dspy registers a tool under `fn.__name__` — a rename would make the model's call raise `NameError`.

Server-authored names/descriptions/schemas are UNTRUSTED input entering the planner's context; all
rendered text is length-bounded (`scaffolding._cap` via `max_desc_chars`) — a model's context preview
leaks untrusted text, so cap what enters it. Tool
ERRORS are returned as informative TEXT the planner reacts to, never raised into the loop.
"""

from __future__ import annotations

import hashlib
import json
from typing import Callable, Optional

from rlm_kit.trace import record_tool_call

from . import scaffolding
from .catalog import Catalog
from .scaffolding import ArgError, coerce_args, render_server_index, render_tool, to_native, unknown_server_error, unknown_tool_error


def _canonical_args(args: dict) -> str:
    """A canonical, key-order-insensitive string of a coerced-args dict — the repeat guard's identity.
    Best-effort and MUST NOT raise on planner-supplied values ("errors are text, never a raise"):
    sort_keys TypeErrors on mixed-type dict keys, circular values ValueError, pathological nesting
    RecursionErrors — degrade to repr (order-sensitive but deterministic for the identical-loop case the
    guard exists to break), then to a type tag. `default=str` may merge exotic values that render
    identically; acceptable for a loop-breaker."""
    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        try:
            return repr(args)
        except RecursionError:   # pathologically deep: give up on identity, keep the loop alive
            return f"<uncanonicalizable:{type(args).__name__}>"


def _args_key(args: dict) -> str:
    """Digest of the canonical args — a BOUNDED key for the per-run attempts counter (a huge arg value
    must not be retained host-side per distinct call). surrogatepass: a lone surrogate in a planner
    string must not make .encode raise."""
    return hashlib.sha256(_canonical_args(args).encode("utf-8", "surrogatepass")).hexdigest()


def _cap_result(value, limit: int = 4000) -> str:
    """A bounded repr of a tool result for the TRACE (the REPL itself keeps the native value)."""
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _exc_text(exc: BaseException, limit: int = 300) -> str:
    """A bounded `Type: message` for an exception whose message may be UNTRUSTED — a server-authored
    error string is LM context, a prompt-injection surface, so it obeys toolscout's "all rendered text
    is length-capped" invariant (mirrors the kit's own `str(exc)[:N]` cap in `_make_tool`)."""
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Toolspace:
    """Binds a `Catalog` + config into the state the four meta-tools share (the ISL 'loaded' set).

    The `_loaded` set makes server selection a REAL, recorded decision: `describe_tools`/`call_tool`
    against an un-materialized server return a guiding error, so the planner must `load_server` first —
    exactly ATLAS's Iterative Server Loading, and the load choice becomes a `tool_call` in the trace.
    """

    def __init__(self, catalog: Catalog, config, *, max_desc_chars: Optional[int] = None,
                 max_describe_batch: Optional[int] = None) -> None:
        self.catalog = catalog
        self.config = config
        self.max_desc_chars = int(max_desc_chars if max_desc_chars is not None
                                  else getattr(config, "max_desc_chars", 1200))
        self.max_describe_batch = int(max_describe_batch if max_describe_batch is not None
                                      else getattr(config, "max_describe_batch", 8))
        self.max_repeat_calls = int(getattr(config, "max_repeat_calls", 3))
        self._loaded: set[str] = set()
        self._proxy_shown: bool = False   # the MCPServer-proxy hint rides only the FIRST load_server
        self._observed: dict[tuple[str, str], str] = {}  # (server, tool) -> last capped result, this run
        # (server, tool, canonical args) -> dispatch attempts, this run — the repeat guard's counter.
        self._call_attempts: dict[tuple[str, str, str], int] = {}

    def is_loaded(self, server: str) -> bool:
        return server in self._loaded

    def observed_example(self, server: str, tool: str) -> str:
        """The last successful (capped) result seen for a tool THIS run, or "" — an ITL example."""
        return self._observed.get((server, tool), "")

    def close(self) -> None:
        self.catalog.close()


def make_list_servers_tool(ts: Toolspace) -> Callable[[], str]:
    def list_servers() -> str:
        """ISL step 1. Return the toolspace's server INDEX: each server's name + a short description, and
        NO tool schemas. Read this first, then `load_server` the one(s) you actually need."""
        servers = ts.catalog.servers()
        record_tool_call("list_servers", servers=[s.name for s in servers])
        return render_server_index(servers, ts.max_desc_chars)

    list_servers.__name__ = "list_servers"
    return list_servers


def make_load_server_tool(ts: Toolspace) -> Callable[[str], str]:
    def load_server(server: str) -> str:
        """ISL step 2. MATERIALIZE one server by name so its tools become describable/callable. Returns
        that server's tool NAMES only (use `describe_tools` for a tool's full signature). Load only the
        servers the task needs — each load is a recorded decision."""
        server = str(server)
        if not ts.catalog.has_server(server):
            names = [s.name for s in ts.catalog.servers()]
            record_tool_call("load_server", args={"server": server}, server=server, ok=False)
            return unknown_server_error(server, names)
        try:
            ts.catalog.load(server)   # under connect="lazy" this CONNECTS now — a wedged/refused server
        except Exception as exc:      # must surface as fixable TEXT, not raise into the RLM loop
            record_tool_call("load_server", args={"server": server}, server=server, ok=False,
                             reason="connect_error", error=_exc_text(exc))
            return (f"Could not connect to server {server!r}: {_exc_text(exc)}. "
                    f"Try another server if this repeats.")
        ts._loaded.add(server)
        names = ts.catalog.tool_names(server)
        record_tool_call("load_server", args={"server": server}, server=server, ok=True, tool_names=names)
        result = f"Loaded {server!r}. Tools: {names}. Use describe_tools([...]) for signatures."
        if not ts._proxy_shown:   # the moment-of-need proxy nudge, once per run (rides only the output)
            ts._proxy_shown = True
            result += "\n" + scaffolding.render_proxy_hint(server)
        return result

    load_server.__name__ = "load_server"
    return load_server


def make_describe_tools_tool(ts: Toolspace) -> Callable[[list], str]:
    def describe_tools(names: list) -> str:
        """ITL. Given a list of tool names (from a loaded server), return their full signatures, param
        types, and capped descriptions — just-in-time, so only the tools you name enter your context.
        Describe a FEW at a time (a small batch), not a whole server."""
        if isinstance(names, str):
            names = [names]
        names = [str(n) for n in (names or [])]
        if not names:
            record_tool_call("describe_tools", args={"names": []}, described=[])
            return "Pass a non-empty list of tool names to describe (from a server you loaded)."
        if len(names) > ts.max_describe_batch:
            capped = names[: ts.max_describe_batch]
            note = (f"(showing the first {ts.max_describe_batch} of {len(names)}; describe the rest in a "
                    f"follow-up call)\n")
            names = capped
        else:
            note = ""
        specs = ts.catalog.describe(names)
        found = {s.name for s in specs}
        # Only disclose tools whose server the planner has actually loaded (ISL discipline).
        visible = [s for s in specs if ts.is_loaded(s.server)]
        blocked = sorted({s.server for s in specs if not ts.is_loaded(s.server)})
        described = [f"{s.server}:{s.name}" for s in visible]
        # `examples_included` rides the payload ONLY when non-empty, so an idle feature keeps existing
        # describe_tools traces byte-identical (additive within trace/v1).
        examples_included = [f"{s.server}:{s.name}" for s in visible
                             if ts.observed_example(s.server, s.name)]
        extra = {"examples_included": examples_included} if examples_included else {}
        record_tool_call("describe_tools", args={"names": names}, described=described, **extra)
        chunks = [render_tool(s, ts.max_desc_chars, observed_example=ts.observed_example(s.server, s.name))
                  for s in visible]
        missing = [n for n in names if n not in found]
        if blocked:
            chunks.append(f"(load these servers first to see their tools: {blocked})")
        if missing:
            chunks.append(f"(no such tool on a loaded server: {missing})")
        return note + ("\n\n".join(chunks) if chunks else "Nothing to describe.")

    describe_tools.__name__ = "describe_tools"
    return describe_tools


def make_call_tool_tool(ts: Toolspace) -> Callable[[str, str, Optional[dict]], object]:
    def call_tool(server: str, tool: str, args: Optional[dict] = None):
        """PTC. Invoke `tool` on a loaded `server` with `args` (a dict of named parameters). Returns the
        tool's result as a NATIVE Python value — keep it in a variable and compute on it; do not re-call
        to re-read (identical re-calls are refused past a small per-run budget). On a bad server/tool/arg
        you get a short, fixable error STRING instead of a result."""
        server, tool = str(server), str(tool)
        if not ts.catalog.has_server(server):
            names = [s.name for s in ts.catalog.servers()]
            record_tool_call("call_tool", args={"tool": tool, "args": args}, server=server, ok=False,
                             reason="unknown_server")
            return unknown_server_error(server, names)
        if not ts.is_loaded(server):
            record_tool_call("call_tool", args={"tool": tool, "args": args}, server=server, ok=False,
                             reason="not_loaded")
            return f"Server {server!r} is not loaded. Call load_server({server!r}) first (ISL)."
        tool_names = ts.catalog.tool_names(server)
        if tool not in tool_names:
            record_tool_call("call_tool", args={"tool": tool, "args": args}, server=server, ok=False,
                             reason="unknown_tool")
            return unknown_tool_error(server, tool, tool_names)
        # A well-behaved Catalog lists a tool in tool_names IFF describe() returns its spec; guard the
        # lookup anyway so a THIRD-PARTY catalog that is inconsistent yields an error STRING, never a
        # StopIteration raised into the loop ("errors are text, never a raise" stays airtight).
        spec = next((s for s in ts.catalog.describe([tool]) if s.server == server and s.name == tool), None)
        if spec is None:
            record_tool_call("call_tool", args={"tool": tool, "args": args}, server=server, ok=False,
                             reason="unknown_tool")
            return unknown_tool_error(server, tool, tool_names)
        try:
            coerced = coerce_args(spec, args or {})
        except ArgError as exc:
            record_tool_call("call_tool", args={"tool": tool, "args": args}, server=server, ok=False,
                             reason="arg_error")
            return f"argument error: {exc}"
        # PTC repeat guard: an IDENTICAL (server, tool, args) re-call can only re-read a value the planner
        # already holds — or re-hammer a failing backend with the same input. Refuse past the per-run
        # budget, PRE-dispatch, as guiding TEXT (never a raise), so an unconscious re-fetch loop breaks
        # after max_repeat_calls instead of storming a third-party MCP server. Keyed on a DIGEST of the
        # COERCED args (canonical, key-order-insensitive, bounded); counts dispatch attempts, so a
        # failing call is bounded too. Disabled (≤0) skips the counting entirely.
        if ts.max_repeat_calls > 0:
            key = (server, tool, _args_key(coerced))
            attempts = ts._call_attempts[key] = ts._call_attempts.get(key, 0) + 1
            if attempts > ts.max_repeat_calls:
                record_tool_call("call_tool", args={"tool": tool, "args": coerced}, server=server,
                                 ok=False, reason="repeat_call")
                return (f"repeat-call guard: attempt #{attempts} of {server}:{tool} with IDENTICAL args "
                        f"this run (limit {ts.max_repeat_calls}). Re-calling cannot return anything new "
                        f"— reuse the value already in your REPL variable (fetch once, filter/compute in "
                        f"the REPL; never re-fetch inside a loop), or change the args if you need "
                        f"different data.")
        try:
            result = ts.catalog.call(server, tool, coerced)
        except Exception as exc:  # a backend/tool failure is data the planner recovers from, not a crash
            record_tool_call("call_tool", args={"tool": tool, "args": coerced}, server=server, ok=False,
                             reason="backend_error", error=_exc_text(exc))
            return f"tool {server}:{tool} raised {_exc_text(exc)}"
        native = to_native(result)
        capped = _cap_result(native)
        ts._observed[(server, tool)] = capped   # cache the (capped) result as an ITL example, this run
        # Record under BOTH `result` (toolscout's canonical read key) and `raw` (what rlm-kit's generic
        # export_actions reads for a tool action's outcome.output) — so the PTC action dataset carries the
        # tool's output, the TG/PA grounding signal. Same value; additive within trace/v1.
        record_tool_call("call_tool", args={"tool": tool, "args": coerced}, server=server, ok=True,
                         result=capped, raw=capped)
        return native

    call_tool.__name__ = "call_tool"
    return call_tool


def build_toolspace_tools(ts: Toolspace) -> list[Callable]:
    """The four ISL/ITL/PTC meta-tools, in the order the planner uses them. Pass straight to `RLMTask`."""
    return [
        make_list_servers_tool(ts),
        make_load_server_tool(ts),
        make_describe_tools_tool(ts),
        make_call_tool_tool(ts),
    ]


# Re-exported for tests that assert the module stays scaffolding-backed (no logic duplicated here).
_scaffolding = scaffolding
