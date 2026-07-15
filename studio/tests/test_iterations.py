"""The trajectory breakdown + the re-derived ISL/ITL/PTC ops — pure over the trace, no server."""

from toolscout_studio.iterations import build_iterations, toolspace_ops


def _trace():
    return [
        {"type": "run_start", "step_id": 0, "ts": 1.0, "payload": {"meta": {
            "task": "what is 6 * 7?", "instructions": "you are a planner", "planner": "P",
            "specialist": "S", "judge": "J", "toolspace": "demo", "max_iterations": 30,
            "rubric": [{"name": "answers_the_task", "category": "TF"}]}}},
        {"type": "main_step", "step_id": 1, "ts": 1.0,
         "payload": {"turn": 0, "reasoning": "explore", "code": "list_servers()", "output": "servers"}},
        {"type": "tool_call", "step_id": 2, "ts": 2.0, "payload": {
            "tool": "list_servers", "servers": ["echo", "math", "text"]}},
        {"type": "tool_call", "step_id": 3, "ts": 3.0, "payload": {
            "tool": "load_server", "args": {"server": "math"}, "server": "math", "ok": True,
            "tool_names": ["add", "mul"]}},
        {"type": "tool_call", "step_id": 4, "ts": 4.0, "payload": {
            "tool": "call_tool", "server": "math", "args": {"tool": "add", "args": {"a": 6, "b": 7}},
            "ok": True, "result": "13"}},
        {"type": "result", "step_id": 5, "ts": 4.5, "payload": {"output": {"answer": "13"}}},
        {"type": "run_end", "step_id": 6, "ts": 5.0, "payload": {}},
    ]


def test_build_iterations_surfaces_task_models_and_timeline():
    d = build_iterations(_trace())
    assert d["initial"]["task"] == "what is 6 * 7?"
    assert d["initial"]["models"] == {"planner": "P", "specialist": "S", "judge": "J"}
    assert d["initial"]["toolspace"] == "demo" and len(d["initial"]["criteria"]) == 1
    assert d["total_s"] == 4.0
    assert len(d["iterations"]) == 1 and d["iterations"][0]["reasoning"] == "explore"
    labels = [t["label"] for t in d["timeline"]]
    assert labels == ["list", "load", "call"]
    call = d["timeline"][2]
    assert call["target"] == "math:add" and call["ok"] is True and call["rel_s"] == 3.0


def test_specialist_escalation_lands_in_the_timeline():
    trace = [{"type": "run_start", "step_id": 0, "ts": 1.0, "payload": {"meta": {}}},
             {"type": "sub_call", "step_id": 1, "ts": 2.0,
              "payload": {"input": "which server?", "processed": "math"}}]
    sub = build_iterations(trace)["timeline"][0]
    assert sub["kind"] == "specialist" and sub["input"] == "which server?" and sub["output"] == "math"


def test_toolspace_ops_reconstructs_the_isl_itl_ptc_narrative():
    ops = toolspace_ops(_trace())
    assert ops["listed"] == ["echo", "math", "text"]
    math = next(s for s in ops["servers"] if s["name"] == "math")
    assert math["loaded"] is True and math["tool_names"] == ["add", "mul"]
    assert len(math["calls"]) == 1 and math["calls"][0]["tool"] == "add" and math["calls"][0]["ok"] is True
    assert ops["counts"] == {"servers_loaded": 1, "servers_listed": 3, "tools_described": 0,
                             "calls_ok": 1, "calls_fail": 0}


def test_toolspace_ops_groups_describe_and_counts_failed_calls():
    trace = [
        {"type": "run_start", "step_id": 0, "ts": 1.0, "payload": {"meta": {}}},
        {"type": "tool_call", "step_id": 1, "ts": 2.0, "payload": {
            "tool": "load_server", "args": {"server": "math"}, "server": "math", "ok": True,
            "tool_names": ["add"]}},
        {"type": "tool_call", "step_id": 2, "ts": 3.0, "payload": {
            "tool": "describe_tools", "args": {"names": ["add"]}, "described": ["math:add"]}},
        {"type": "tool_call", "step_id": 3, "ts": 4.0, "payload": {
            "tool": "call_tool", "server": "math", "args": {"tool": "add", "args": {}}, "ok": False,
            "reason": "arg_error", "error": "missing a"}}]
    ops = toolspace_ops(trace)
    math = next(s for s in ops["servers"] if s["name"] == "math")
    assert math["described"] == ["add"]
    assert ops["counts"]["tools_described"] == 1 and ops["counts"]["calls_fail"] == 1
    assert math["calls"][0]["reason"] == "arg_error" and math["calls"][0]["error"] == "missing a"


def test_per_turn_timing_off_when_main_steps_cluster():
    # older-style trace: main_steps flushed at finalize (ts cluster) → no per-turn timing, no fake durations
    trace = [{"type": "run_start", "step_id": 0, "ts": 1.0, "payload": {"meta": {}}},
             {"type": "main_step", "step_id": 1, "ts": 9.0, "payload": {"turn": 0, "reasoning": "a"}},
             {"type": "main_step", "step_id": 2, "ts": 9.0, "payload": {"turn": 1, "reasoning": "b"}}]
    d = build_iterations(trace)
    assert d["per_turn_timing"] is False
    assert "duration_s" not in d["iterations"][0]
