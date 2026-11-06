"""check(): pass/fail plus a reason for every way an answer can miss."""

import pytest
from toolscout_synth import check, check_outcome, render_gold

INT = {"gold_kind": "int", "gold": 47889, "tolerance": 0.0}
NUM = {"gold_kind": "number", "gold": 1654.6, "tolerance": 0.01}
ID = {"gold_kind": "id", "gold": "CUS-0439", "tolerance": 0.0}
SET = {"gold_kind": "id_set", "gold": ["SKU-4352", "SKU-6906"], "tolerance": 0.0}
CC = {"gold_kind": "cannot_complete", "gold": {"cannot_complete": True}, "tolerance": 0.0}


@pytest.mark.parametrize("task,answer,cc,passed,reason", [
    (INT, "47889", False, True, "ok"),
    (INT, "47,889 credits", False, True, "ok"),
    (INT, "47888", False, False, "wrong_value"),
    (INT, "In March 2031 it was 47889", False, False, "ambiguous_answer"),
    (INT, "no idea", False, False, "no_value_found"),
    (INT, "", False, False, "no_outcome"),
    (INT, None, False, False, "no_outcome"),
    (INT, "cannot be answered", True, False, "unexpected_cannot_complete"),
    (NUM, "1654.60", False, True, "ok"),
    (NUM, "1654.61", False, True, "ok"),
    (NUM, "1654.63", False, False, "out_of_tolerance"),
    (ID, "CUS-0439", False, True, "ok"),
    (ID, "The top spender is CUS-0439.", False, True, "ok"),
    (ID, "CUS-0001", False, False, "wrong_value"),
    (ID, "CUS-0439 or CUS-0001", False, False, "ambiguous_answer"),
    (SET, "SKU-6906, SKU-4352", False, True, "ok"),
    (SET, "SKU-4352", False, False, "wrong_set"),
    (SET, "SKU-4352, SKU-6906, SKU-1111", False, False, "wrong_set"),
    (CC, "No tool exposes salaries.", True, True, "ok"),
    (CC, "120000", False, False, "missing_cannot_complete"),
])
def test_check_reasons(task, answer, cc, passed, reason):
    r = check(task, answer, cannot_complete=cc)
    assert (r.passed, r.reason) == (passed, reason)


def test_details_explain_the_miss():
    r = check(SET, "SKU-4352, SKU-1111")
    assert r.detail == {"missing": ["SKU-6906"], "extra": ["SKU-1111"]}
    assert check(INT, "2031: 47889").detail == {"gold_among_them": True}
    assert check(NUM, "1655").detail["abs_diff"] == pytest.approx(0.4)


def test_render_gold_round_trips_through_check(taskset):
    for t in taskset:
        r = check(t, render_gold(t), cannot_complete=t["gold_kind"] == "cannot_complete")
        assert r.passed, (t["id"], r)


def test_check_outcome_reads_answer_and_decline():
    class O:
        answer, cannot_complete = "CUS-0439", False

    assert check_outcome(ID, O()).passed
    assert check_outcome(ID, None).reason == "no_outcome"
    assert check_outcome(CC, None).reason == "no_outcome"
