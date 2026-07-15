"""The opt-in rubric_judge tool — an injected chat_fn, JSON validation, per-criterion LABELS (no score)."""

from __future__ import annotations

from toolscout.config import ToolscoutConfig
from toolscout.judge_tool import _parse_judge_json, make_rubric_judge_tool
from toolscout.rubric import default_rubric


def _cfg():
    return ToolscoutConfig(main_model="x", sub_model="y", enable_judge=True,
                           judge_transient_retries=0, judge_circuit_break=2)


def test_parse_judge_json_valid_and_invalid():
    ok = _parse_judge_json('{"observations": [{"criterion": "c", "note": "n", "met": true}], "summary": "s"}')
    assert ok.ok and ok.observations[0]["met"] is True and ok.summary == "s"
    assert not _parse_judge_json("not json").ok
    assert not _parse_judge_json('{"observations": []}').ok  # empty → unusable


def test_rubric_judge_records_observations(tmp_path):
    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    criteria = default_rubric("do a thing").criteria

    def fake_chat(_prompt: str) -> str:
        return '{"observations": [{"criterion": "answers_the_task", "note": "grounded", "met": true}], ' \
               '"summary": "ok"}'

    tool = make_rubric_judge_tool(_cfg(), criteria, chat_fn=fake_chat)
    path = str(tmp_path / "r.jsonl")
    with TraceRecorder(path, run_id="r", meta={"task": "t"}):
        out = tool("my draft answer, used math.add")
    assert "answers_the_task" in out and "met" in out
    events = load_events(path, "r")
    judge = [e for e in events if e["payload"].get("tool") == "rubric_judge"][0]["payload"]
    assert judge["ok"] is True
    assert judge["observations"][0]["criterion"] == "answers_the_task"
    # a LABEL surface only — never an aggregate score/reward
    assert "score" not in judge and "reward" not in judge


def test_rubric_judge_handles_unusable_reply(tmp_path):
    from rlm_kit import TraceRecorder

    tool = make_rubric_judge_tool(_cfg(), default_rubric("t").criteria, chat_fn=lambda _p: "garbage")
    with TraceRecorder(str(tmp_path / "r.jsonl"), run_id="r", meta={"task": "t"}):
        out = tool("draft")
    assert "unusable" in out or "CIRCUIT BREAKER" in out
