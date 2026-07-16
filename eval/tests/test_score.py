"""score_run over REAL recorded traces: judge-input reconstruction, unscored paths, aggregation."""

from __future__ import annotations

from conftest import math_calls, record_run

from toolscout_eval.judge import JudgeVerdict, stub_judge
from toolscout_eval.schema import CATEGORIES, EvalRow, EvalScore
from toolscout_eval.score import aggregate, build_judge_inputs, score_run
from toolscout_eval.taskset import EvalTask

TASK = EvalTask(id="demo-math", task="what is 6 times 7?",
                reference="(1) call math mul(6, 7) -> 42; (2) answer 42.")


def _capturing_judge(seen: list):
    def judge(inputs: dict) -> JudgeVerdict:
        seen.append(inputs)
        return JudgeVerdict(ok=True, score=EvalScore(TF=8, TA=7, TG=6, PA=5))
    return judge


def test_score_run_reconstructs_judge_inputs_from_the_trace(tmp_path):
    events = record_run(tmp_path, math_calls(), outcome={"answer": "42", "summary": "used math.mul"},
                        run_id="demo-math")
    seen: list = []
    row = score_run(events, TASK, _capturing_judge(seen))
    assert isinstance(row, EvalRow) and not row.unscored
    assert row.task_id == "demo-math" and row.run_id == "demo-math"
    inputs = seen[0]
    assert inputs["task"] == "what is 6 times 7?"        # from run_start meta — the fuzzy task
    assert inputs["reference"] == TASK.reference          # judge-only, from the EvalTask
    assert "math:mul" in inputs["execution_summary"]      # the PTC call, reconstructed from tool_calls
    assert "42" in inputs["execution_summary"]            # the tool's returned value rides along
    assert "42" in inputs["final_solution"]               # AssembledOutcome.answer
    assert inputs["total_rounds"] == 1                    # metrics["turns"]
    assert row.score is not None and row.score.TF == 8.0
    assert row.metrics["turns"] == 1 and row.metrics["call_ok"] == 1
    assert row.fabrication_tells == 0


def test_fabrication_tells_counts_unbacked_servers_plus_tools(tmp_path):
    events = record_run(tmp_path, math_calls(), outcome={
        "answer": "42",
        "servers_loaded": ["math", "ghost"],              # claims a server it never loaded
        "tools_used": ["mul", "ghost:zap"],               # bare "mul" is backed; "ghost:zap" is not
    }, run_id="demo-math")
    row = score_run(events, TASK, stub_judge)
    assert row.fabrication_tells == 2                     # unbacked_servers + unbacked_tools


def test_never_finalized_run_is_unscored_not_a_crash(tmp_path):
    events = record_run(tmp_path, math_calls(), outcome=None, run_id="demo-math")
    called: list = []
    row = score_run(events, TASK, _capturing_judge(called))
    assert row.unscored and row.score is None
    assert "never finalized" in row.unscored_reason
    assert called == []                                   # the judge is never consulted


def test_build_judge_inputs_returns_none_for_never_finalized(tmp_path):
    events = record_run(tmp_path, math_calls(), outcome=None, run_id="demo-math")
    assert build_judge_inputs(events, TASK) is None


def test_judge_failure_yields_unscored_row_with_reason(tmp_path):
    events = record_run(tmp_path, math_calls(), outcome={"answer": "42"}, run_id="demo-math")

    def failing_judge(inputs: dict) -> JudgeVerdict:
        return JudgeVerdict(ok=False, reason="judge endpoint error: boom")

    row = score_run(events, TASK, failing_judge)
    assert row.unscored and "boom" in row.unscored_reason
    assert row.metrics["turns"] == 1                      # deterministic facts still ride along


def _scored_row(task_id: str, tf: float, ta: float, tg: float, pa: float) -> EvalRow:
    return EvalRow(task_id=task_id, run_id=task_id, score=EvalScore(TF=tf, TA=ta, TG=tg, PA=pa))


def test_aggregate_computes_per_category_means_tf_primary():
    rows = [_scored_row("a", 8, 6, 4, 2), _scored_row("b", 4, 2, 6, 8),
            EvalRow(task_id="c", run_id="c", unscored=True, unscored_reason="never finalized")]
    report = aggregate(rows, taskset="demo", judge_model="stub", prompt_version="atlas-eval-v1")
    assert report.n == 3 and report.n_unscored == 1       # the unscored row counts, but not in the means
    assert report.primary == "TF"
    assert report.means == {"TF": 6.0, "TA": 4.0, "TG": 5.0, "PA": 5.0}
    assert set(report.means) == set(CATEGORIES)           # per-category only — nowhere for a composite
    assert report.judge_model == "stub" and report.prompt_version == "atlas-eval-v1"


def test_aggregate_of_nothing_scored_has_empty_means():
    rows = [EvalRow(task_id="c", run_id="c", unscored=True, unscored_reason="x")]
    report = aggregate(rows, taskset="demo")
    assert report.means == {} and report.n == 1 and report.n_unscored == 1


def test_end_to_end_report_dump_carries_no_reward_key(tmp_path):
    events = record_run(tmp_path, math_calls(), outcome={"answer": "42"}, run_id="demo-math")
    report = aggregate([score_run(events, TASK, stub_judge)], taskset="demo", judge_model="stub")
    dumped = report.model_dump_json().lower()
    assert "reward" not in dumped and "composite" not in dumped
