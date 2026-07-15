"""build_response / build_failed_response — the TaskResponse envelope over an assembled outcome."""

from __future__ import annotations

from toolscout.assemble import outcome_from_events
from toolscout.response import build_failed_response, build_response

from tests.conftest import run_recorded


def _events(tmp_path, outcome):
    return run_recorded(tmp_path, [
        ("load_server", {"server": "math"}),
        ("describe_tools", {"names": ["add"]}),
        ("call_tool", {"server": "math", "tool": "add", "args": {"a": 6, "b": 7}}),
    ], outcome=outcome, sub_calls=1)


def test_build_response_ok(tmp_path):
    events = _events(tmp_path, {"answer": "13"})
    resp = build_response(outcome_from_events(events), events, "run-1")
    assert resp.status == "ok" and resp.id == "run-1"
    assert resp.outcome.answer == "13"
    assert resp.process.servers_loaded == 1 and resp.process.tool_calls == 1
    assert resp.process.specialist_escalations == 1 and resp.process.turns == 1


def test_build_response_empty_answer_is_failed(tmp_path):
    events = _events(tmp_path, {"answer": "   "})
    resp = build_response(outcome_from_events(events), events, "run-2")
    assert resp.status == "failed" and resp.error


def test_build_failed_response_carries_process(tmp_path):
    events = _events(tmp_path, None)
    resp = build_failed_response("run-3", events, "boom", task="my task")
    assert resp.status == "failed" and resp.error == "boom"
    assert resp.task == "my task" and resp.outcome is None
    assert resp.process.tool_calls == 1  # still reports what the run managed


def test_build_failed_response_refusal(tmp_path):
    events = _events(tmp_path, None)
    resp = build_failed_response("run-4", events, "not allowed", reason="refused")
    assert resp.status == "refused" and resp.refusal.refused is True
