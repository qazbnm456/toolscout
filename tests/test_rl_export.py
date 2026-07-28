"""rl_export — REWARD-FREE trajectory bundle: splits, labels/metrics, the ATLAS rubric signal."""

from __future__ import annotations

from tests.conftest import run_recorded
from toolscout.rl_export import export_dataset, rubric_signal, run_labels, run_metrics


def _run(tmp_path, run_id, outcome, *, judge=()):
    return run_recorded(tmp_path, [
        ("list_servers", {}),
        ("load_server", {"server": "math"}),
        ("describe_tools", {"names": ["add"]}),
        ("call_tool", {"server": "math", "tool": "add", "args": {"a": 6, "b": 7}}),
    ], outcome=outcome, run_id=run_id, judge_events=judge, sub_calls=1)


def test_run_labels_are_facts(tmp_path):
    events = _run(tmp_path, "r1", {"answer": "13", "servers_loaded": ["ghost"]})
    lab = run_labels(events)
    assert lab["finalized"] is True
    assert lab["servers_loaded"] == 1 and lab["tools_used"] == 1
    assert lab["unbacked_servers"] == 1
    assert lab["cannot_complete"] is False
    assert "reward" not in lab and "score" not in lab


def test_run_labels_flag_a_principled_decline(tmp_path):
    """A DECLINE lands as the reward-free `cannot_complete` FACT (mirroring a clean negative) — no score."""
    events = _run(tmp_path, "r1", {"answer": "no server here exposes weather data", "cannot_complete": True})
    lab = run_labels(events)
    assert lab["finalized"] is True and lab["cannot_complete"] is True
    assert "reward" not in lab and "score" not in lab


def test_run_metrics_are_facts(tmp_path):
    events = _run(tmp_path, "r1", {"answer": "13"})
    m = run_metrics(events)
    assert m["load_calls"] == 1 and m["describe_calls"] == 1 and m["call_ok"] == 1
    assert m["specialist_escalations"] == 1 and m["list_servers_calls"] == 1
    assert m["hit_iteration_cap"] is False
    assert "reward" not in m


def test_rubric_signal_carries_labels_not_scores(tmp_path):
    judge = [{"tool": "rubric_judge", "ok": True,
              "observations": [{"criterion": "answers_the_task", "note": "ok", "met": True}]}]
    events = _run(tmp_path, "r1", {"answer": "13"}, judge=judge)
    sig = rubric_signal(events)
    assert len(sig["rubric"]) == 4  # the four default criteria
    assert len(sig["criteria_facts"]) == 4
    assert sig["judge_observations"][0]["criterion"] == "answers_the_task"
    # a criterion fact is observations only, no dᵢ
    assert all("score" not in cf["observed"] for cf in sig["criteria_facts"])


def test_call_tool_action_carries_its_output(tmp_path):
    """The PTC action's tool RESULT must reach the action dataset (TG/PA grounding signal) — the kit's
    export_actions reads `raw` for outcome.output, so the meta-tool records it there too."""
    events = _run(tmp_path, "r1", {"answer": "13"})
    bundle = export_dataset({"r1": events})
    calls = [a for a in bundle["toolspace_ops"] if a["tool"] == "call_tool"]
    assert calls and (calls[0].get("outcome") or {}).get("output") == "13"


def test_export_dataset_is_reward_free_and_split(tmp_path):
    runs = {
        "r1": _run(tmp_path, "r1", {"answer": "13"}),
        "r2": _run(tmp_path, "r2", {"answer": "20"}),
    }
    bundle = export_dataset(runs)
    # every action record carries reward=None (the trainer fills it)
    assert bundle["actions"] and all(a.get("reward") is None for a in bundle["actions"])
    # toolspace ops are the four meta-tools; judge split is separate + empty here
    assert bundle["toolspace_ops"] and all(
        a["tool"] in ("list_servers", "load_server", "describe_tools", "call_tool")
        for a in bundle["toolspace_ops"])
    assert bundle["judge"] == []
    assert set(bundle["labels"]) == {"r1", "r2"} and set(bundle["rubric_signal"]) == {"r1", "r2"}
    assert bundle["planner"] and bundle["sft_turns"] is not None


# ---- cause-splitting: a break is not a call, and a flaky last judge is not "no judge" -------


def _judge(**kw):
    return {"type": "tool_call", "ts": 1.0, "step_id": 1,
            "payload": {"tool": "rubric_judge", **kw}}


def test_a_circuit_broken_judge_is_not_counted_as_a_judge_call():
    """A break invoked neither the model nor the validator, so counting it as a call overstates the
    effort the judge actually cost. This was the only unfiltered call count in the dict."""
    m = run_metrics([_judge(ok=True, observations=[{"criterion": "c", "met": True}]),
                     _judge(ok=False, cause="circuit_broken", circuit_broken=True)])

    assert m["judge_calls"] == 1
    assert m["judge_circuit_breaks"] == 1


def test_the_judge_counts_partition_by_cause():
    m = run_metrics([_judge(ok=True, observations=[]),
                     _judge(ok=False, errors=["off-schema"]),
                     _judge(error="502 bad gateway"),
                     _judge(ok=False, circuit_broken=True)])

    assert m["judge_declines"] == 1
    assert m["judge_endpoint_errors"] == 1
    assert m["judge_circuit_breaks"] == 1
    assert m["judge_calls"] == 3, "everything but the break reached the endpoint"


def test_a_flaky_final_judge_does_not_blank_a_successful_earlier_one():
    """`judges[-1]` was taken blindly over a HETEROGENEOUS population: a `rubric_judge` event may be
    a real verdict, an endpoint failure, or a circuit break, and the last two carry no
    `observations`. One flaky final call therefore erased a successful judge — which flipped
    `judge_ran` to False in the dataset and dropped the "rubric judge ran" chip in the studio."""
    from toolscout.assemble import _judge_observations

    events = [_judge(ok=True, observations=[{"criterion": "grounded", "met": True}]),
              _judge(error="502 bad gateway")]

    obs = _judge_observations(events, None)

    assert obs == [{"criterion": "grounded", "met": True}]


def test_an_explicit_judge_call_id_still_wins():
    """The negative control for the change above: the explicit selector must not be overridden by
    the new "last one that produced observations" fallback."""
    from toolscout.assemble import _judge_observations

    events = [{"type": "tool_call", "ts": 1.0, "step_id": 7,
               "payload": {"tool": "rubric_judge", "ok": True, "observations": [{"criterion": "a"}]}},
              {"type": "tool_call", "ts": 2.0, "step_id": 9,
               "payload": {"tool": "rubric_judge", "ok": True, "observations": [{"criterion": "b"}]}}]

    assert _judge_observations(events, 7) == [{"criterion": "a"}]
