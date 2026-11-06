"""check(): pass/fail plus a reason for every way an answer can miss."""

import pytest
from toolscout_synth import check, check_outcome, render_gold

INT = {"gold_kind": "int", "gold": 47889, "tolerance": 0.0}
NUM = {"gold_kind": "number", "gold": 1654.6, "tolerance": 0.01}
ID = {"gold_kind": "id", "gold": "CUS-0439", "tolerance": 0.0}
SET = {"gold_kind": "id_set", "gold": ["SKU-4352", "SKU-6906"], "tolerance": 0.0}
CC = {"gold_kind": "cannot_complete", "gold": {"cannot_complete": True}, "tolerance": 0.0}
LAB = {"gold_kind": "label", "gold": "tents", "tolerance": 0.0, "choices": ["boots", "tents", "ropes"]}
LSET = {"gold_kind": "label_set", "gold": ["Tiszor Vale", "Vibra Vale"], "tolerance": 0.0,
        "choices": ["Andelgar Vale", "Tiszor Vale", "Vibra Vale"]}


@pytest.mark.parametrize("task,answer,cc,passed,reason", [
    (INT, "47889", False, True, "ok"),
    (INT, "47,889 credits", False, True, "ok"),
    (INT, "47888", False, False, "wrong_value"),
    (INT, "In March 2031 it was 47889", False, True, "ok"),
    (INT, "47889 CR (Q1 2031)", False, True, "ok"),
    (INT, "47,889 credits from 212 delivered orders", False, True, "ok"),
    (INT, "47 889", False, True, "ok"),
    (INT, "Across 2031-03-01..2031-03-31: 47889", False, True, "ok"),
    (INT, "212 delivered orders brought in 47,889", False, False, "wrong_value"),
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
    (ID, "cus-0439", False, True, "ok"),
    (ID, "CUS-0439 (spent 2,894 CR) ahead of CUS-8657", False, True, "ok"),
    (ID, "CUS-0001, not CUS-0439", False, False, "wrong_value"),
    (NUM, "1654.6 VEL per order (based on 37 orders)", False, True, "ok"),
    (SET, "SKU-6906, SKU-4352", False, True, "ok"),
    (SET, "SKU-4352", False, False, "wrong_set"),
    (SET, "SKU-4352, SKU-6906, SKU-1111", False, False, "wrong_set"),
    (SET, "SKU-4352 and SKU-6906. Not SKU-1111", False, False, "wrong_set"),   # strict by design
    (SET, "sku-6906 and SKU-4352", False, True, "ok"),
    (CC, "No tool exposes salaries.", True, True, "ok"),
    (LAB, "tents", False, True, "ok"),
    (LAB, "Tents earned the most, ahead of boots.", False, True, "ok"),
    (LAB, "boots", False, False, "wrong_value"),
    (LAB, "tent", False, False, "no_value_found"),
    (LSET, "vibra vale, Tiszor Vale", False, True, "ok"),
    (LSET, "Tiszor Vale", False, False, "wrong_set"),
    (CC, "120000", False, False, "missing_cannot_complete"),
])
def test_check_reasons(task, answer, cc, passed, reason):
    r = check(task, answer, cannot_complete=cc)
    assert (r.passed, r.reason) == (passed, reason)


def test_details_explain_the_miss():
    r = check(SET, "SKU-4352, SKU-1111")
    assert r.detail == {"missing": ["SKU-6906"], "extra": ["SKU-1111"]}
    assert check(INT, "47889").detail == {"bare": True, "other_numbers": [], "gold_among_them": True}
    ctx = check(INT, "47,889 credits from 212 delivered orders")
    assert ctx.passed and ctx.detail["bare"] is False and ctx.detail["other_numbers"] == [212.0]
    miss = check(INT, "212 delivered orders brought in 47,889")
    assert not miss.passed and miss.got == 212 and miss.detail["gold_among_them"] is True
    assert check(NUM, "1655").detail["abs_diff"] == pytest.approx(0.4)
    assert check(ID, "CUS-0439 ahead of CUS-8657").detail == {
        "bare": False, "other_ids": ["CUS-8657"], "gold_among_them": True}


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
