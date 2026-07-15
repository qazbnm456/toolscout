"""The pydantic shapes — TaskOutcome is citation-only; the assembled/response shapes carry facts."""

from __future__ import annotations

from toolscout.schema import (
    CRITERION_CATEGORIES,
    AssembledOutcome,
    Criterion,
    CriterionFact,
    RubricCriteria,
    TaskOutcome,
    TaskResponse,
)


def test_task_outcome_is_citation_only():
    """Structurally NO field for raw tool outputs, a per-criterion score, or a reward — the policy cannot
    self-report evidence."""
    fields = set(TaskOutcome.model_fields)
    assert fields == {"answer", "summary", "servers_loaded", "tools_used", "cited_criteria", "judge_call_id"}
    assert "score" not in fields and "reward" not in fields and "tool_outputs" not in fields


def test_task_outcome_defaults():
    o = TaskOutcome(answer="42")
    assert o.summary == "" and o.servers_loaded == [] and o.tools_used == []
    assert o.cited_criteria == [] and o.judge_call_id is None


def test_criterion_and_rubric():
    c = Criterion(name="answers", description="d", category="TF")
    assert c.weight == 1.0 and c.category in CRITERION_CATEGORIES
    r = RubricCriteria(criteria=[c])
    assert r.criteria[0].name == "answers"


def test_criterion_fact_is_facts_not_score():
    f = CriterionFact(criterion="x", category="TG", weight=2.0, observed={"call_ok_count": 3})
    assert f.observed["call_ok_count"] == 3
    assert "score" not in CriterionFact.model_fields and "met" not in CriterionFact.model_fields


def test_assembled_outcome_shape():
    a = AssembledOutcome(task="t", answer="a")
    for name in ("criteria_facts", "judge_observations", "cited_unknown", "unbacked_servers",
                 "unbacked_tools", "metrics"):
        assert name in AssembledOutcome.model_fields
    assert a.metrics == {} and a.cited_unknown == []


def test_task_response_envelope():
    r = TaskResponse(id="run-1")
    assert r.object == "toolscout.task_response" and r.status == "ok"
    assert r.outcome is None and r.refusal.refused is False
