"""The trace-event → SSE mapping (the public event surface), verified per event type. Pure — no server."""

from toolscout_studio.mapper import to_event


def test_run_start_carries_models_task_and_criteria_count():
    ev = to_event({"type": "run_start", "payload": {"meta": {
        "planner": "P", "specialist": "S", "judge": "J",
        "task": "solve me", "toolspace": "demo", "max_iterations": 30,
        "rubric": [{"name": "a"}, {"name": "b"}]}}})
    assert ev["event"] == "task.run.created"
    assert ev["data"]["models"] == {"planner": "P", "specialist": "S", "judge": "J"}
    assert ev["data"]["task"] == "solve me" and ev["data"]["toolspace"] == "demo"
    assert ev["data"]["criteria"] == 2 and ev["data"]["max_iterations"] == 30


def test_run_start_drops_absent_roles():
    # the judge role is optional (OFF by default) — a null model must not appear in the roles map.
    ev = to_event({"type": "run_start", "payload": {"meta": {"planner": "P", "specialist": "S"}}})
    assert ev["data"]["models"] == {"planner": "P", "specialist": "S"}


def test_main_step_is_a_plan_step():
    ev = to_event({"type": "main_step", "payload": {"turn": 2, "reasoning": "r", "code": "c"}})
    assert ev == {"event": "task.plan.step", "data": {"turn": 2, "reasoning": "r", "has_code": True}}


def test_sub_call_is_a_specialist_escalation_with_input_processed_keys():
    # rlm-kit's sub-LM records input / processed / raw — NOT question/answer.
    ev = to_event({"type": "sub_call", "payload": {"input": "which server?", "processed": "the math one"}})
    assert ev["event"] == "task.specialist.escalation"
    assert ev["data"] == {"question": "which server?", "answer": "the math one"}


def test_list_servers_reports_the_server_index():
    ev = to_event({"type": "tool_call", "payload": {
        "tool": "list_servers", "servers": ["echo", "math", "memory", "text"]}})
    assert ev["event"] == "task.servers.listed"
    assert ev["data"]["n"] == 4 and ev["data"]["servers"][1] == "math"


def test_load_server_is_the_isl_decision():
    ev = to_event({"type": "tool_call", "payload": {
        "tool": "load_server", "args": {"server": "math"}, "server": "math", "ok": True,
        "tool_names": ["add", "mul"]}})
    assert ev["event"] == "task.server.loaded"
    assert ev["data"]["server"] == "math" and ev["data"]["ok"] is True and ev["data"]["tools"] == ["add", "mul"]


def test_load_server_failure_keeps_the_server_and_marks_not_ok():
    ev = to_event({"type": "tool_call", "payload": {
        "tool": "load_server", "args": {"server": "nope"}, "server": "nope", "ok": False}})
    assert ev["data"]["server"] == "nope" and ev["data"]["ok"] is False and ev["data"]["tools"] == []


def test_describe_tools_is_the_itl_step():
    ev = to_event({"type": "tool_call", "payload": {
        "tool": "describe_tools", "args": {"names": ["add", "mul"]}, "described": ["math:add", "math:mul"]}})
    assert ev["event"] == "task.tools.described"
    assert ev["data"]["n"] == 2 and ev["data"]["described"] == ["math:add", "math:mul"]


def test_call_tool_is_the_ptc_invocation_with_nested_tool_name():
    # the call_tool payload nests the invocation as args={tool, args} alongside a top-level server/ok.
    ev = to_event({"type": "tool_call", "payload": {
        "tool": "call_tool", "server": "math", "args": {"tool": "add", "args": {"a": 6, "b": 7}},
        "ok": True, "result": "13"}})
    assert ev["event"] == "task.tool.called"
    assert ev["data"]["server"] == "math" and ev["data"]["tool"] == "add" and ev["data"]["ok"] is True


def test_call_tool_failure_surfaces_the_reason():
    ev = to_event({"type": "tool_call", "payload": {
        "tool": "call_tool", "server": "math", "args": {"tool": "add", "args": {}}, "ok": False,
        "reason": "arg_error"}})
    assert ev["data"]["ok"] is False and ev["data"]["reason"] == "arg_error"


def test_rubric_judge_reports_ok_and_observation_count():
    ev = to_event({"type": "tool_call", "payload": {
        "tool": "rubric_judge", "ok": True, "observations": [{"criterion": "x", "met": True}]}})
    assert ev["event"] == "task.judge" and ev["data"]["ok"] is True and ev["data"]["n"] == 1


def test_rubric_judge_circuit_break_variant():
    ev = to_event({"type": "tool_call", "payload": {
        "tool": "rubric_judge", "ok": False, "circuit_broken": True}})
    assert ev["data"]["ok"] is False and ev["data"]["circuit_broken"] is True


def test_skill_reads():
    assert to_event({"type": "tool_call", "payload": {"tool": "read_skill", "args": {"name": "ptc"}}}) == {
        "event": "task.skill.read", "data": {"name": "ptc"}}
    assert to_event({"type": "tool_call", "payload": {"tool": "list_skills", "args": {}}})["data"]["name"] == "(catalog)"


def test_result_and_run_end_and_final():
    assert to_event({"type": "result", "payload": {}}) == {"event": "task.result.done", "data": {}}
    assert to_event({"type": "run_end", "payload": {}})["event"] == "task.run.completed"
    # `final` is SKIPPED: a real trace holds both `final` and `run_end`, so mapping both would emit the
    # terminal event twice (and the `final` copy before `result`). `run_end` is the sole terminal.
    assert to_event({"type": "final", "payload": {}}) is None


def test_unknown_type_and_unsurfaced_tool_are_skipped():
    assert to_event({"type": "something_else", "payload": {}}) is None
    assert to_event({"type": "tool_call", "payload": {"tool": "mystery_tool", "args": {}}}) is None
