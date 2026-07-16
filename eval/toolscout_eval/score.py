"""Score one recorded run against its EvalTask, then aggregate rows into the terminal scorecard.

This is the READ side of the harness: it reconstructs the ATLAS judge's inputs deterministically from
the existing `trace/v1` contract — `toolscout.assemble.outcome_from_events` for the answer + re-sourced
servers/tools + metrics (so the judge grades the same trace-grounded facts a human sees, not the
planner's self-report), `toolscout.rubric.trace_facts` + the recorded `tool_call` events for the
execution summary, and the EvalTask's judge-only `reference`. NO new trace field is read or written.

Reward-free throughout: `score_run` produces a row of independent category scores next to deterministic
facts; `aggregate` computes per-category MEANS (TF primary) — never a weighted composite, never a
pass/fail. A never-finalized run becomes an `unscored` row, not a crash and not a fake 0.
"""

from __future__ import annotations

from statistics import fmean
from typing import Callable, Optional

from toolscout.assemble import outcome_from_events
from toolscout.rubric import trace_facts
from toolscout.schema import AssembledOutcome

from .judge import JudgeVerdict
from .schema import CATEGORIES, EvalReport, EvalRow
from .taskset import EvalTask


def _run_id(events: list[dict]) -> str:
    for event in events:
        rid = event.get("run_id")
        if rid:
            return str(rid)
    return ""


def _execution_summary(events: list[dict], outcome: AssembledOutcome) -> str:
    """The ISL/ITL/PTC narrative the judge reads — servers loaded and tools used (re-sourced by
    assemble, not self-reported), each recorded `call_tool` with its args -> result/error, and the
    deterministic error totals from `trace_facts`."""
    facts = trace_facts(events)
    lines = [
        f"servers loaded (per the trace): {', '.join(outcome.servers_loaded) or '(none)'}",
        f"tools used ok (per the trace): {', '.join(outcome.tools_used) or '(none)'}",
    ]
    for event in events:
        if event.get("type") != "tool_call":
            continue
        payload = event.get("payload") or {}
        if payload.get("tool") != "call_tool":
            continue
        args = payload.get("args") or {}
        target = f"{payload.get('server')}:{args.get('tool')}"
        call_args = args.get("args")
        if payload.get("ok"):
            lines.append(f"call {target}({call_args!r}) -> ok: {str(payload.get('result'))[:200]}")
        else:
            reason = payload.get("reason") or "error"
            detail = str(payload.get("error") or "")[:200]
            lines.append(f"call {target}({call_args!r}) -> FAILED ({reason}){': ' + detail if detail else ''}")
    lines.append(
        f"totals: {facts['call_ok_count']} ok call(s), {facts['call_fail_count']} failed call(s) "
        f"({facts['arg_error_count']} argument error(s), {facts['backend_error_count']} backend error(s))"
    )
    return "\n".join(lines)


def build_judge_inputs(events: list[dict], eval_task: EvalTask,
                       outcome: Optional[AssembledOutcome] = None) -> Optional[dict]:
    """Reconstruct the ATLAS judge's inputs from the trace, or None for a never-finalized run.

    `outcome` may be passed when the caller already assembled it (score_run does); otherwise it is
    re-derived here via `outcome_from_events`.
    """
    outcome = outcome if outcome is not None else outcome_from_events(events)
    if outcome is None:
        return None
    final = outcome.answer or "(empty answer)"
    if outcome.summary:
        final = f"{final}\n\n(the agent's own summary of how: {outcome.summary})"
    return {
        "task": outcome.task or eval_task.task,
        "reference": eval_task.reference or "(no reference provided; grade against the task itself)",
        "execution_summary": _execution_summary(events, outcome),
        "final_solution": final,
        "total_rounds": int(outcome.metrics.get("turns", 0)),
    }


def score_run(events: list[dict], eval_task: EvalTask,
              judge: Callable[[dict], JudgeVerdict]) -> EvalRow:
    """One run -> one EvalRow: judge inputs from the trace, the judge's verdict, plus the deterministic
    facts (metrics + fabrication tells) surfaced side by side as a cross-check. Never raises on a bad
    run: never-finalized or judge-failed -> an `unscored` row with the reason."""
    run_id = _run_id(events) or eval_task.id
    outcome = outcome_from_events(events)
    if outcome is None:
        return EvalRow(task_id=eval_task.id, run_id=run_id, unscored=True,
                       unscored_reason="run never finalized (no result event in the trace)")
    tells = len(outcome.unbacked_servers) + len(outcome.unbacked_tools)
    inputs = build_judge_inputs(events, eval_task, outcome)
    verdict = judge(inputs)
    if not verdict.ok or verdict.score is None:
        return EvalRow(task_id=eval_task.id, run_id=run_id, metrics=dict(outcome.metrics),
                       fabrication_tells=tells, unscored=True,
                       unscored_reason=verdict.reason or "judge returned no score")
    return EvalRow(task_id=eval_task.id, run_id=run_id, score=verdict.score,
                   metrics=dict(outcome.metrics), fabrication_tells=tells)


def aggregate(rows: list[EvalRow], *, taskset: str, judge_model: str = "",
              prompt_version: str = "") -> EvalReport:
    """Rows -> the scorecard: per-category ARITHMETIC MEANS over the scored rows, TF primary.

    Unscored rows count in `n`/`n_unscored` but never enter the means (unscored is not 0). There is no
    composite and no threshold here by design — the report is a measurement, not a signal.
    """
    scored = [r for r in rows if not r.unscored and r.score is not None]
    means: dict[str, float] = {}
    if scored:
        for cat in CATEGORIES:
            means[cat] = round(fmean(getattr(r.score, cat) for r in scored), 2)
    return EvalReport(taskset=taskset, n=len(rows), n_unscored=len(rows) - len(scored),
                      judge_model=judge_model, prompt_version=prompt_version,
                      means=means, rows=list(rows))
