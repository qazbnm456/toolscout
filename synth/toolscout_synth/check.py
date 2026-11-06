"""`check()`: does a run's final answer match a task's gold? A pure, deterministic FACT with a reason.

This is a measurement, not a reward: it never writes into a trace or an export. What a trainer does with
it (keep/drop a trajectory, weight it) is the trainer's business.

`passed` judges the COMPUTED VALUE; answer FORMAT is reported separately in `detail["bare"]`. The tasks ask
for a bare value, but toolscout's planner is told to ground its answer in tool values, so a correct answer
often carries context ("47,889 CR from 212 delivered orders"). Failing those would measure formatting, not
the computation, so:
- `int` / `number`: the task's own period labels are removed first ("March 2031", "Q1 2031", ISO dates
  and weeks), then the FIRST remaining number is the answer. Thousands separators may be `,`, a space,
  or a narrow/no-break space. `detail`: `bare` (it was the only number), `other_numbers`,
  `gold_among_them`.
- `id`: the FIRST id token, case-insensitive (`cus-0439` reads as `CUS-0439`); same `detail` keys.
- `id_set`: the SET of all id tokens must equal the gold set, so naming an extra id fails (even "not
  SKU-1111"): a set answer cannot be read leniently without guessing. `detail` lists missing and extra.
- `label`: the FIRST of the task's `choices` (a category or region name) named in the answer, case-insensitive.
- `label_set`: the set of `choices` named must equal the gold set, as strictly as `id_set`.
- `cannot_complete`: passes only when the run declined (`TaskOutcome.cannot_complete=True`).
A run that declined a solvable task fails with `unexpected_cannot_complete`; a run with no answer fails
with `no_outcome`.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

REASONS = ("ok", "no_outcome", "missing_cannot_complete", "unexpected_cannot_complete", "no_value_found",
           "wrong_value", "out_of_tolerance", "wrong_set")

_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December|"
           "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
_CONTEXT = re.compile(
    rf"\b(?:(?:{_MONTHS})\.?\s+\d{{4}}|Q[1-4]\s*\d{{4}}|Q[1-4]\b|\d{{4}}-W\d{{2}}|\d{{4}}-\d{{2}}(?:-\d{{2}})?)",
    re.IGNORECASE)
_SEP = r"[, \u00a0\u202f]"
_NUM = re.compile(rf"(?<![\w.])-?\d{{1,3}}(?:{_SEP}\d{{3}})+(?:\.\d+)?(?![\w])|(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
_ID = re.compile(r"\b[A-Z]{2,4}-\d{3,6}\b")


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    reason: str
    expected: Any = None
    got: Any = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _numbers(text: str) -> list[float]:
    text = _CONTEXT.sub(" ", text.replace("\u2212", "-").replace("\u2013", "-"))   # Unicode minus / en dash
    return [float(re.sub(_SEP, "", m.group(0))) for m in _NUM.finditer(text)]


def _ids(text: str) -> list[str]:
    out: list[str] = []
    for i in _ID.findall(text.upper()):
        if i not in out:
            out.append(i)
    return out


def _labels(text: str, choices: list[str]) -> list[str]:
    """The choices named in `text`, in order of first appearance (whole words, case-insensitive)."""
    hits = []
    for c in choices:
        m = re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", text, re.IGNORECASE)
        if m:
            hits.append((m.start(), c))
    return [c for _, c in sorted(hits)]


def check(task: dict, answer: str | None, *, cannot_complete: bool = False) -> CheckResult:
    """Compare a final `answer` (and the decline flag) with `task["gold"]` per `task["gold_kind"]`."""
    kind, gold = task["gold_kind"], task["gold"]
    if kind == "cannot_complete":
        if cannot_complete:
            return CheckResult(True, "ok", expected="cannot_complete", got="cannot_complete")
        return CheckResult(False, "missing_cannot_complete", expected="cannot_complete", got=answer)
    if cannot_complete:
        return CheckResult(False, "unexpected_cannot_complete", expected=gold, got=answer)
    if answer is None or not str(answer).strip():
        return CheckResult(False, "no_outcome", expected=gold, got=answer)
    text = str(answer)

    if kind in ("int", "number"):
        nums = _numbers(text)
        if not nums:
            return CheckResult(False, "no_value_found", expected=gold, got=text)
        tol = float(task.get("tolerance") or 0.0) + 1e-9
        n, rest = nums[0], nums[1:]
        detail = {"bare": not rest, "other_numbers": rest,
                  "gold_among_them": any(abs(x - gold) <= tol for x in nums)}
        diff = abs(n - gold)
        if diff <= tol:
            return CheckResult(True, "ok", expected=gold, got=n, detail=detail)
        if kind == "int":
            return CheckResult(False, "wrong_value", expected=gold, got=n, detail=detail)
        return CheckResult(False, "out_of_tolerance", expected=gold, got=n,
                           detail={**detail, "abs_diff": round(diff, 6), "tolerance": task.get("tolerance")})

    if kind in ("label", "label_set"):
        named = _labels(text, task["choices"])
        if not named:
            return CheckResult(False, "no_value_found", expected=gold, got=text)
        if kind == "label":
            detail = {"bare": len(named) == 1, "other_labels": named[1:], "gold_among_them": gold in named}
            return CheckResult(named[0] == gold, "ok" if named[0] == gold else "wrong_value", expected=gold,
                               got=named[0], detail=detail)
        want, got = set(gold), set(named)
        if got == want:
            return CheckResult(True, "ok", expected=sorted(want), got=sorted(got))
        return CheckResult(False, "wrong_set", expected=sorted(want), got=sorted(got),
                           detail={"missing": sorted(want - got), "extra": sorted(got - want)})

    ids = _ids(text)
    if not ids:
        return CheckResult(False, "no_value_found", expected=gold, got=text)
    if kind == "id":
        detail = {"bare": len(ids) == 1, "other_ids": ids[1:], "gold_among_them": gold in ids}
        return CheckResult(ids[0] == gold, "ok" if ids[0] == gold else "wrong_value", expected=gold,
                           got=ids[0], detail=detail)

    if kind == "id_set":
        want, got = set(gold), set(ids)
        if got == want:
            return CheckResult(True, "ok", expected=sorted(want), got=sorted(got))
        return CheckResult(False, "wrong_set", expected=sorted(want), got=sorted(got),
                           detail={"missing": sorted(want - got), "extra": sorted(got - want)})

    raise ValueError(f"unknown gold_kind {kind!r}")


def check_outcome(task: dict, outcome: Any) -> CheckResult:
    """`check` over a toolscout outcome (an `AssembledOutcome` or `TaskOutcome`, or None for a failed run)."""
    if outcome is None:
        return check(task, None) if task["gold_kind"] != "cannot_complete" else CheckResult(
            False, "no_outcome", expected="cannot_complete")
    return check(task, getattr(outcome, "answer", None),
                 cannot_complete=bool(getattr(outcome, "cannot_complete", False)))


def render_gold(task: dict) -> str:
    """The gold answer as a bare string that `check` accepts (for reference trajectories and demos).
    A `cannot_complete` task has no answer value: decline with `cannot_complete=True` instead."""
    kind, gold = task["gold_kind"], task["gold"]
    if kind == "int":
        return str(gold)
    if kind == "number":
        return f"{gold:.2f}"
    if kind == "id":
        return gold
    if kind in ("id_set", "label_set"):
        return ", ".join(gold)
    if kind == "label":
        return gold
    return "The configured toolspace has no tool that provides this information."
