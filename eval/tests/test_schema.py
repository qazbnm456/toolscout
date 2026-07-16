"""The report shapes stay reward-free BY CONSTRUCTION — asserted structurally, not by convention."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from toolscout_eval.schema import CATEGORIES, EvalReport, EvalRow, EvalScore

FORBIDDEN_FIELDS = {"reward", "rewards", "composite", "composite_score", "weighted", "weighted_score",
                    "total", "total_score", "overall", "overall_score", "r_tau", "score_sum"}


def test_no_composite_or_reward_field_anywhere():
    """The fence: no model in the eval schema may grow a reward-shaped field."""
    for model in (EvalScore, EvalRow, EvalReport):
        overlap = set(model.model_fields) & FORBIDDEN_FIELDS
        assert not overlap, f"{model.__name__} grew a reward-shaped field: {overlap}"


def test_report_aggregate_is_per_category_means_only():
    """The only aggregate surface is a per-category dict — a single number has nowhere to live."""
    report = EvalReport(taskset="demo", n=0)
    assert isinstance(report.means, dict)
    assert report.primary == "TF"


def test_eval_score_bounds_are_enforced():
    with pytest.raises(ValidationError):
        EvalScore(TF=11, TA=5, TG=5, PA=5)
    with pytest.raises(ValidationError):
        EvalScore(TF=5, TA=-1, TG=5, PA=5)
    score = EvalScore(TF=0, TA=10, TG=4.5, PA=7)
    assert score.TF == 0.0 and score.TA == 10.0 and score.TG == 4.5


def test_categories_match_atlas():
    assert CATEGORIES == ("TF", "TA", "TG", "PA")


def test_report_round_trips_json():
    row = EvalRow(task_id="demo-math", run_id="demo-math",
                  score=EvalScore(TF=5, TA=5, TG=5, PA=5), metrics={"turns": 1}, fabrication_tells=0)
    report = EvalReport(taskset="demo", n=1, n_unscored=0, judge_model="stub",
                        prompt_version="atlas-eval-v1",
                        means={"TF": 5.0, "TA": 5.0, "TG": 5.0, "PA": 5.0}, rows=[row])
    again = EvalReport.model_validate_json(report.model_dump_json())
    assert again == report
