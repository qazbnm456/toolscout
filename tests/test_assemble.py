"""assemble_outcome — re-source facts from the trace, cross-check the self-report, flag fabrication."""

from __future__ import annotations

from toolscout.assemble import assemble_outcome, outcome_from_events
from toolscout.schema import TaskOutcome

from tests.conftest import run_recorded


def _calls():
    return [
        ("load_server", {"server": "math"}),
        ("describe_tools", {"names": ["add"]}),
        ("call_tool", {"server": "math", "tool": "add", "args": {"a": 6, "b": 7}}),
    ]


def test_assemble_resources_servers_and_tools_from_trace(tmp_path):
    events = run_recorded(tmp_path, _calls(), outcome={"answer": "13"})
    a = outcome_from_events(events)
    assert a is not None
    assert a.servers_loaded == ["math"]           # from the trace, not the SUBMIT
    assert a.tools_used == ["math:add"]
    assert a.answer == "13" and a.task == "add six and seven"
    assert a.metrics["call_ok"] == 1 and a.metrics["turns"] == 1


def test_assemble_flags_unbacked_self_report(tmp_path):
    events = run_recorded(tmp_path, _calls(), outcome={
        "answer": "13",
        "servers_loaded": ["math", "ghost"],      # claims a server it never loaded
        "tools_used": ["add", "delete_everything"],  # claims a tool it never called
    })
    a = outcome_from_events(events)
    assert a.unbacked_servers == ["ghost"]
    assert a.unbacked_tools == ["delete_everything"]  # bare "add" matches math:add, so it is backed


def test_assemble_tolerates_legacy_cited_criteria(tmp_path):
    """A legacy trace whose result payload still carries the removed `cited_criteria` key loads fine —
    `_outcome_from_payload` filters to known fields, so the stale key is ignored, not a crash."""
    events = run_recorded(tmp_path, _calls(), outcome={
        "answer": "13", "cited_criteria": ["answers_the_task", "not_a_real_criterion"]})
    a = outcome_from_events(events)
    assert a is not None and a.answer == "13"
    assert not hasattr(a, "cited_unknown")  # the field is gone entirely


def test_assemble_carries_criteria_facts(tmp_path):
    events = run_recorded(tmp_path, _calls(), outcome={"answer": "13"})
    a = outcome_from_events(events)
    assert len(a.criteria_facts) == 4  # one per default-rubric category
    assert a.judge_observations == []  # no judge ran


def test_assemble_resolves_judge_observations(tmp_path):
    judge_ev = {"tool": "rubric_judge", "ok": True,
                "observations": [{"criterion": "answers_the_task", "note": "yes", "met": True}],
                "summary": "looks grounded"}
    events = run_recorded(tmp_path, _calls(), outcome={"answer": "13"}, judge_events=[judge_ev])
    a = outcome_from_events(events)
    assert a.judge_observations and a.judge_observations[0]["criterion"] == "answers_the_task"
    assert a.metrics["judge_ran"] is True


def test_outcome_from_events_none_without_result(tmp_path):
    events = run_recorded(tmp_path, _calls(), outcome=None)  # never finalized
    assert outcome_from_events(events) is None


def test_assemble_tolerates_legacy_result_keys(tmp_path):
    events = run_recorded(tmp_path, _calls(), outcome={
        "answer": "13", "legacy_field": "ignored", "score": 0.9})  # extra keys tolerated + dropped
    a = assemble_outcome(TaskOutcome(answer="13"), events)
    assert a.answer == "13"
