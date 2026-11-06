"""`check()`: does a run's final answer match a task's gold? A pure, deterministic FACT with a reason.

This is a measurement, not a reward: it never writes into a trace or an export. What a trainer does with
it (keep/drop a trajectory, weight it) is the trainer's business.

Answers are parsed strictly, because the task text asks for a bare value:
- `int` / `number`: the answer must contain exactly ONE number (thousands separators allowed). Several
  numbers is `ambiguous_answer`, even when one of them is right; `detail` says whether gold was among them.
- `id`: exactly one distinct id token (like `CUS-0042`).
- `id_set`: the set of id tokens must equal the gold set; `detail` lists missing and extra ids.
- `cannot_complete`: passes only when the run declined (`TaskOutcome.cannot_complete=True`).
A run that declined a solvable task fails with `unexpected_cannot_complete`; a run with no outcome fails
with `no_outcome`.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

REASONS = ("ok", "no_outcome", "missing_cannot_complete", "unexpected_cannot_complete", "no_value_found",
           "ambiguous_answer", "wrong_value", "out_of_tolerance", "wrong_set")

_NUM = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
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
    return [float(m.group(0).replace(",", "")) for m in _NUM.finditer(text)]


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
        tol = float(task.get("tolerance") or 0.0)
        if not nums:
            return CheckResult(False, "no_value_found", expected=gold, got=text)
        if len(nums) > 1:
            return CheckResult(False, "ambiguous_answer", expected=gold, got=nums,
                               detail={"gold_among_them": any(abs(n - gold) <= tol for n in nums)})
        n = nums[0]
        if kind == "int":
            return CheckResult(n == gold, "ok" if n == gold else "wrong_value", expected=gold, got=n)
        diff = abs(n - gold)
        if diff <= tol + 1e-9:
            return CheckResult(True, "ok", expected=gold, got=n, detail={"abs_diff": round(diff, 6)})
        return CheckResult(False, "out_of_tolerance", expected=gold, got=n,
                           detail={"abs_diff": round(diff, 6), "tolerance": tol})

    ids = sorted(set(_ID.findall(text)))
    if kind == "id":
        if not ids:
            return CheckResult(False, "no_value_found", expected=gold, got=text)
        if len(ids) > 1:
            return CheckResult(False, "ambiguous_answer", expected=gold, got=ids,
                               detail={"gold_among_them": gold in ids})
        return CheckResult(ids[0] == gold, "ok" if ids[0] == gold else "wrong_value", expected=gold, got=ids[0])

    if kind == "id_set":
        if not ids:
            return CheckResult(False, "no_value_found", expected=gold, got=text)
        want = set(gold)
        if set(ids) == want:
            return CheckResult(True, "ok", expected=sorted(want), got=ids)
        return CheckResult(False, "wrong_set", expected=sorted(want), got=ids,
                           detail={"missing": sorted(want - set(ids)), "extra": sorted(set(ids) - want)})

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
    if kind == "id_set":
        return ", ".join(gold)
    return "The configured toolspace has no tool that provides this information."
