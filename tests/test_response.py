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


def test_build_response_decline_is_refused(tmp_path):
    """A planner that finalized with `cannot_complete=True` reads as a PRINCIPLED `refused` negative —
    the previously-dead RefusalInfo path, now reachable from an ordinary SUBMIT (not a crash)."""
    events = _events(tmp_path, {"answer": "no server here exposes weather data", "cannot_complete": True})
    resp = build_response(outcome_from_events(events), events, "run-decline")
    assert resp.status == "refused"
    assert resp.refusal.refused is True and resp.refusal.reason == "unsupported"
    assert resp.error == "no server here exposes weather data"     # the planner's reason surfaces
    assert resp.process.status == "refused"
    assert resp.outcome is not None and resp.outcome.cannot_complete is True   # facts survive


def test_build_response_ok_unchanged_when_not_declined(tmp_path):
    """REGRESSION: a normal answered run with cannot_complete defaulting False stays `ok`."""
    events = _events(tmp_path, {"answer": "13", "cannot_complete": False})
    resp = build_response(outcome_from_events(events), events, "run-ok")
    assert resp.status == "ok" and resp.refusal.refused is False and resp.error == ""


def test_build_failed_response_carries_process(tmp_path):
    events = _events(tmp_path, None)
    resp = build_failed_response("run-3", events, "boom", task="my task")
    assert resp.status == "failed" and resp.error == "boom"
    assert resp.task == "my task" and resp.outcome is None
    assert resp.process.tool_calls == 1  # still reports what the run managed
