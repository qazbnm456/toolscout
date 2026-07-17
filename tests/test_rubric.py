"""Rubrics — generation/parse, the default skeleton, and deterministic trace facts (never a score)."""

from __future__ import annotations

from toolscout.rubric import (
    CATEGORY_MEANING,
    criteria_facts,
    default_rubric,
    parse_rubric,
    rubric_from_meta,
    rubric_to_meta,
    trace_facts,
    validate_rubric,
)
from toolscout.schema import CRITERION_CATEGORIES, Criterion, RubricCriteria

from tests.conftest import run_recorded


def test_default_rubric_covers_all_categories():
    r = default_rubric("do a thing")
    cats = {c.category for c in r.criteria}
    assert cats == set(CRITERION_CATEGORIES)
    assert all(c.name and c.description for c in r.criteria)


def test_category_meaning_complete():
    assert set(CATEGORY_MEANING) == set(CRITERION_CATEGORIES)


def test_parse_rubric_tolerates_fences_and_bad_categories():
    raw = '```json\n{"criteria": [' \
          '{"name":"a","description":"d","weight":2,"category":"TF"},' \
          '{"name":"bad","description":"d","category":"ZZ"},' \
          '{"name":"c","description":"d","category":"pa"}]}\n```'
    r = parse_rubric(raw)
    names = {c.name for c in r.criteria}
    assert names == {"a", "c"}  # ZZ dropped; lowercase 'pa' normalized to PA
    assert next(c for c in r.criteria if c.name == "a").weight == 2.0
    assert next(c for c in r.criteria if c.name == "c").category == "PA"


def test_parse_rubric_garbage_is_empty():
    assert parse_rubric("not json at all").criteria == []


def test_validate_rubric_clean_default():
    assert validate_rubric(default_rubric("do a thing")) == []
    assert validate_rubric(RubricCriteria(criteria=[])) == ["rubric has no criteria"]


def test_validate_rubric_flags_structural_issues():
    r = RubricCriteria(criteria=[
        Criterion(name="a", category="TF", description="answers the task using tool outputs"),
        Criterion(name="a", category="TA", description="loaded the right server"),  # dup name
        Criterion(name="c", category="TG", description=""),                          # empty desc
        Criterion(name="d", category="PA", description="the vibes are immaculate"),  # not observable
    ])
    issues = " ".join(validate_rubric(r))
    assert "duplicate criterion names" in issues
    assert "empty descriptions" in issues
    assert "not be trace-observable" in issues
    assert "categories not represented" not in issues  # all four present here


def test_validate_rubric_flags_missing_category():
    r = RubricCriteria(criteria=[
        Criterion(name="a", category="TF", description="answers using tool calls")])
    assert any("categories not represented" in i for i in validate_rubric(r))


def test_rubric_meta_roundtrip(tmp_path):
    r = default_rubric("t")
    events = run_recorded(tmp_path, calls=[], rubric=r)
    recovered = rubric_from_meta(events)
    assert [c.name for c in recovered.criteria] == [c.name for c in r.criteria]
    assert rubric_to_meta(r)[0]["category"] in CRITERION_CATEGORIES


def test_trace_facts_are_deterministic_counts(tmp_path):
    events = run_recorded(tmp_path, calls=[
        ("load_server", {"server": "math"}),
        ("describe_tools", {"names": ["add"]}),
        ("call_tool", {"server": "math", "tool": "add", "args": {"a": 6, "b": 7}}),
        ("call_tool", {"server": "math", "tool": "add", "args": {"a": 1}}),  # arg error
    ], outcome={"answer": "13"})
    f = trace_facts(events)
    assert f["servers_loaded"] == ["math"]
    assert f["tools_called_ok"] == ["math:add"]
    assert f["call_ok_count"] == 1 and f["arg_error_count"] == 1
    assert f["finalized"] is True


def test_trace_facts_count_repeat_call_as_predispatch(tmp_path):
    """A repeat-guard refusal is a PRE-dispatch reject (PA signal), like unknown_*/not_loaded — the
    offline facts must classify it deterministically from the recorded reason tag."""
    same = ("call_tool", {"server": "math", "tool": "add", "args": {"a": 6, "b": 7}})
    events = run_recorded(tmp_path, calls=[("load_server", {"server": "math"})] + [same] * 4,
                          outcome={"answer": "13"})
    f = trace_facts(events)
    assert f["call_ok_count"] == 3                 # the budgeted identical dispatches
    assert f["call_fail_count"] == 1 and f["predispatch_reject_count"] == 1  # the 4th, reason=repeat_call
    assert f["backend_error_count"] == 0 and f["arg_error_count"] == 0


def test_criteria_facts_one_per_criterion_no_score(tmp_path):
    events = run_recorded(tmp_path, calls=[("load_server", {"server": "math"})],
                          outcome={"answer": "x"})
    facts = criteria_facts(events)
    assert {f.category for f in facts} == set(CRITERION_CATEGORIES)
    for f in facts:
        assert isinstance(f.observed, dict)
        # observed holds raw facts, never a score/met verdict
        assert "score" not in f.observed and "met" not in f.observed
