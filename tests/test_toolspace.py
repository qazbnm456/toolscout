"""The four ISL/ITL/PTC meta-tools — names, ISL gating, error recovery, and trace recording."""

from __future__ import annotations

from toolscout.catalog import demo_catalog
from toolscout.config import ToolscoutConfig
from toolscout.toolspace import Toolspace, build_toolspace_tools


def _tools():
    ts = Toolspace(demo_catalog(), ToolscoutConfig(main_model="x", sub_model="y"))
    tools = {t.__name__: t for t in build_toolspace_tools(ts)}
    return ts, tools


def test_factory_pins_the_registered_names():
    """dspy registers a tool under fn.__name__; a rename would NameError in the loop."""
    _ts, tools = _tools()
    assert set(tools) == {"list_servers", "load_server", "describe_tools", "call_tool"}


def test_list_servers_returns_index_without_schemas():
    _ts, tools = _tools()
    out = tools["list_servers"]()
    assert "echo" in out and "math" in out and "add(" not in out  # index only, no tool signatures


def test_isl_gate_describe_requires_load():
    _ts, tools = _tools()
    # describing a tool whose server is not loaded discloses nothing, points at the load step
    blocked = tools["describe_tools"](["add"])
    assert "load these servers first" in blocked and "add(a: int" not in blocked
    tools["load_server"]("math")
    ok = tools["describe_tools"](["add"])
    assert "add(a: int, b: int)" in ok


def test_isl_gate_call_requires_load():
    _ts, tools = _tools()
    assert "not loaded" in tools["call_tool"]("math", "add", {"a": 1, "b": 2})
    tools["load_server"]("math")
    assert tools["call_tool"]("math", "add", {"a": 6, "b": 7}) == 13


def test_ptc_returns_native_value():
    _ts, tools = _tools()
    tools["load_server"]("math")
    result = tools["call_tool"]("math", "mul", {"a": 4, "b": 5})
    assert result == 20 and isinstance(result, int)  # a native value to compute on, not a string


def test_call_tool_error_strings_are_recoverable():
    _ts, tools = _tools()
    assert "No server named 'mth'" in tools["call_tool"]("mth", "add", {})
    tools["load_server"]("math")
    assert "has no tool 'ad'" in tools["call_tool"]("math", "ad", {})
    assert "argument error" in tools["call_tool"]("math", "add", {"a": 1})  # missing b


def test_unknown_server_load_is_friendly():
    _ts, tools = _tools()
    out = tools["load_server"]("nope")
    assert "No server named 'nope'" in out


def test_repeat_call_guard_refuses_identical_args(tmp_path):
    """The PTC repeat guard: past max_repeat_calls IDENTICAL dispatches, call_tool refuses with guiding
    TEXT (pre-dispatch, reason="repeat_call") — an unconscious re-fetch loop breaks instead of storming
    the backend. The key is the COERCED args in canonical JSON, so dict key order must not matter."""
    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    path = str(tmp_path / "r.jsonl")
    with TraceRecorder(path, run_id="r"):
        _ts, tools = _tools()
        tools["load_server"]("math")
        for _ in range(3):   # up to the budget, identical args dispatch normally
            assert tools["call_tool"]("math", "add", {"a": 6, "b": 7}) == 13
        blocked = tools["call_tool"]("math", "add", {"b": 7, "a": 6})   # same call, swapped key order
        assert "repeat-call guard" in blocked and "IDENTICAL args" in blocked
        # the key is the COERCED args: "6"/"7" coerce to 6/7, so this is the same identity — and the
        # refusal persists past the first block (attempt #5).
        still = tools["call_tool"]("math", "add", {"a": "6", "b": "7"})
        assert "repeat-call guard" in still and "#5" in still
        assert tools["call_tool"]("math", "add", {"a": 1, "b": 2}) == 3  # different args still dispatch
    calls = [e["payload"] for e in load_events(path, "r")
             if e["type"] == "tool_call" and e["payload"]["tool"] == "call_tool"]
    fails = [p for p in calls if not p["ok"]]
    assert len(fails) == 2 and {p["reason"] for p in fails} == {"repeat_call"}
    assert sum(1 for p in calls if p["ok"]) == 4   # 3 budgeted identical + 1 different-args


def test_repeat_call_guard_key_never_raises():
    """The guard's KEY CONSTRUCTION must degrade, never raise: a dict/Any-typed param passes an
    arbitrary planner dict through coercion untouched, and json.dumps(sort_keys=True) TypeErrors on
    mixed-type nested keys, ValueErrors on circular values, RecursionErrors on pathological nesting —
    each must fall back, and identical dicts must still share a key. (Scope note: this pins the key
    helper only. Serializing such exotic args in the trace RECORD is a separate, pre-existing rlm-kit
    gap — a raise-proof serialize belongs in the kit's TraceRecorder, not here.)"""
    from toolscout.toolspace import _args_key, _canonical_args

    assert _canonical_args({"b": 1, "a": 2}) == _canonical_args({"a": 2, "b": 1})  # order-insensitive
    weird = {"filters": {1: "a", "b": 2}}   # json.dumps(sort_keys=True) raises TypeError on this
    assert _canonical_args(weird) == _canonical_args(weird)   # falls back to repr, still deterministic
    circular: dict = {}
    circular["self"] = circular              # json.dumps raises ValueError on this
    assert _canonical_args(circular)         # repr handles it ({'self': {...}})
    deep: list = []
    for _ in range(50_000):                  # py3.13 C-stack guard: dumps AND repr both RecursionError
        deep = [deep]
    assert _canonical_args({"v": deep}).startswith("<uncanonicalizable:")
    assert _args_key({"s": "\ud800"})        # a lone surrogate must not make the digest .encode raise


def test_repeat_call_guard_zero_disables():
    ts = Toolspace(demo_catalog(), ToolscoutConfig(main_model="x", sub_model="y", max_repeat_calls=0))
    tools = {t.__name__: t for t in build_toolspace_tools(ts)}
    tools["load_server"]("math")
    for _ in range(6):   # no guard: every identical call dispatches
        assert tools["call_tool"]("math", "add", {"a": 6, "b": 7}) == 13


def test_describe_respects_batch_cap():
    ts = Toolspace(demo_catalog(), ToolscoutConfig(main_model="x", sub_model="y", max_describe_batch=1))
    tools = {t.__name__: t for t in build_toolspace_tools(ts)}
    tools["load_server"]("math")
    out = tools["describe_tools"](["add", "mul"])
    assert "showing the first 1 of 2" in out


def test_meta_tools_record_tool_calls(tmp_path):
    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    ts = Toolspace(demo_catalog(), ToolscoutConfig(main_model="x", sub_model="y"))
    tools = {t.__name__: t for t in build_toolspace_tools(ts)}
    path = str(tmp_path / "r.jsonl")
    with TraceRecorder(path, run_id="r", meta={"task": "t"}):
        tools["list_servers"]()
        tools["load_server"]("math")
        tools["describe_tools"](["add"])
        tools["call_tool"]("math", "add", {"a": 6, "b": 7})
    events = load_events(path, "r")
    by_tool = [e["payload"]["tool"] for e in events if e["type"] == "tool_call"]
    assert by_tool == ["list_servers", "load_server", "describe_tools", "call_tool"]
    call = [e for e in events if e["payload"].get("tool") == "call_tool"][0]["payload"]
    # canonical shape: meta-tool name at payload["tool"], inner tool inside args, server top-level (B1/B2)
    assert call["tool"] == "call_tool" and call["server"] == "math"
    assert call["args"]["tool"] == "add" and call["ok"] is True and call["result"] == "13"


def test_call_tool_failure_records_reason(tmp_path):
    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    ts = Toolspace(demo_catalog(), ToolscoutConfig(main_model="x", sub_model="y"))
    tools = {t.__name__: t for t in build_toolspace_tools(ts)}
    path = str(tmp_path / "r.jsonl")
    with TraceRecorder(path, run_id="r", meta={"task": "t"}):
        tools["load_server"]("math")
        tools["call_tool"]("math", "add", {"a": 1})  # arg error → reason tag for the PA signal
    events = load_events(path, "r")
    fail = [e for e in events if e["payload"].get("tool") == "call_tool" and not e["payload"]["ok"]][0]
    assert fail["payload"]["reason"] == "arg_error"


# ---- (ii) the MCPServer proxy is transparent to the trace + the moment-of-need hint ----

def test_proxy_call_matches_direct_call_payload(tmp_path):
    import json

    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    from toolscout.scaffolding import PROXY_SOURCE

    def _boundary(r):   # dspy's REPL boundary: json path for list/dict, else str() (None -> "")
        if isinstance(r, (list, dict)):
            return json.loads(json.dumps(r))
        return str(r) if r is not None else ""

    path = str(tmp_path / "r.jsonl")
    with TraceRecorder(path, run_id="r"):
        ts, tools = _tools()
        tools["load_server"]("math")
        direct = tools["call_tool"]("math", "add", {"a": 6, "b": 7})
        ns = {"call_tool": lambda s, t, a: _boundary(tools["call_tool"](s, t, a))}
        exec(PROXY_SOURCE, ns)
        proxied = ns["MCPServer"]("math").add(a=6, b=7)
    assert direct == 13 and proxied == 13
    adds = [e["payload"] for e in load_events(path, "r")
            if e["type"] == "tool_call" and e["payload"]["tool"] == "call_tool"
            and e["payload"]["args"]["tool"] == "add"]
    assert len(adds) == 2 and adds[0] == adds[1]   # one direct, one via proxy — identical recorded payload


def test_load_server_proxy_hint_rides_output_once(tmp_path):
    import json

    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    path = str(tmp_path / "r.jsonl")
    with TraceRecorder(path, run_id="r"):
        _ts, tools = _tools()
        first = tools["load_server"]("math")
        second = tools["load_server"]("echo")
    assert "MCPServer" in first and "MCPServer" not in second   # the hint rides the FIRST load only
    loads = [e["payload"] for e in load_events(path, "r")
             if e["type"] == "tool_call" and e["payload"]["tool"] == "load_server"]
    assert all("MCPServer" not in json.dumps(p) for p in loads)  # hint is in the output, not the payload


# ---- (iv) observed-example disclosure ----

def test_describe_tools_discloses_observed_example(tmp_path):
    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    path = str(tmp_path / "r.jsonl")
    with TraceRecorder(path, run_id="r"):
        _ts, tools = _tools()
        tools["load_server"]("math")
        before = tools["describe_tools"](["add"])            # no call yet → no example line
        tools["call_tool"]("math", "add", {"a": 6, "b": 7})  # → 13, cached this run
        after = tools["describe_tools"](["add"])
    assert "observed this run" not in before
    assert "example (observed this run) → 13" in after
    describes = [e["payload"] for e in load_events(path, "r")
                 if e["type"] == "tool_call" and e["payload"]["tool"] == "describe_tools"]
    assert "examples_included" not in describes[0]            # idle → key ABSENT (byte-identical)
    assert describes[1]["examples_included"] == ["math:add"]  # present once an example exists


# ---- (D) a lazy connect failure surfaces as fixable TEXT, never a raise into the loop ----

def test_load_server_surfaces_connect_error_as_text(tmp_path):
    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    from toolscout.catalog import Catalog, ServerInfo

    class _RaisingCatalog(Catalog):
        def servers(self):
            return [ServerInfo("remote", "d")]

        def has_server(self, s):
            return s == "remote"

        def load(self, s):
            raise RuntimeError("connection refused")   # e.g. a wedged/refused lazy HTTP connect

        def tool_names(self, s):
            return []

        def describe(self, names):
            return []

        def call(self, s, t, a):
            return None

    path = str(tmp_path / "r.jsonl")
    with TraceRecorder(path, run_id="r"):
        ts = Toolspace(_RaisingCatalog(), ToolscoutConfig(main_model="x", sub_model="y"))
        tools = {t.__name__: t for t in build_toolspace_tools(ts)}
        out = tools["load_server"]("remote")
    assert "Could not connect to server 'remote'" in out and "connection refused" in out
    assert not ts.is_loaded("remote")   # a failed connect must NOT mark the server loaded
    ev = [e["payload"] for e in load_events(path, "r")
          if e["type"] == "tool_call" and e["payload"]["tool"] == "load_server"][0]
    assert ev["ok"] is False and ev["reason"] == "connect_error"


def test_load_server_connect_error_text_is_capped(tmp_path):
    # a server-authored error message is UNTRUSTED LM context — a huge one must be length-capped in
    # BOTH the returned text and the recorded `error` payload, never flooded verbatim into the prompt.
    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    from toolscout.catalog import Catalog, ServerInfo

    class _FloodCatalog(Catalog):
        def servers(self):
            return [ServerInfo("remote", "d")]

        def has_server(self, s):
            return s == "remote"

        def load(self, s):
            raise RuntimeError("X" * 10_000)   # a malicious/verbose server error

        def tool_names(self, s):
            return []

        def describe(self, names):
            return []

        def call(self, s, t, a):
            return None

    path = str(tmp_path / "r.jsonl")
    with TraceRecorder(path, run_id="r"):
        ts = Toolspace(_FloodCatalog(), ToolscoutConfig(main_model="x", sub_model="y"))
        tools = {t.__name__: t for t in build_toolspace_tools(ts)}
        out = tools["load_server"]("remote")
    assert "…" in out and out.count("X") < 400   # capped, not 10k chars of untrusted text
    ev = [e["payload"] for e in load_events(path, "r")
          if e["type"] == "tool_call" and e["payload"]["tool"] == "load_server"][0]
    assert len(ev["error"]) <= 300 and ev["error"].endswith("…")
