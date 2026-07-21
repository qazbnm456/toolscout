"""Build the API-shaped `TaskResponse` from an assembled outcome + the trace — a read-time presentation
carrying no new judgement. `build_failed_response` is the crash/cancel path (no outcome → a refusal that
still reports whatever the run managed before it died).

Pure stdlib + pydantic; no dspy.
"""

from __future__ import annotations

from typing import Optional

from rlm_kit.trace import EVENT_RUN_START

from .schema import AssembledOutcome, ProcessInfo, RefusalInfo, TaskResponse


def _meta(events: list[dict]) -> dict:
    for e in events:
        if e.get("type") == EVENT_RUN_START:
            return (e.get("payload") or {}).get("meta") or {}
    return {}


def _process(events: list[dict], run_id: str, *, status: str = "ok") -> ProcessInfo:
    def _tool(name: str) -> int:
        return sum(1 for e in events
                   if e["type"] == "tool_call" and e["payload"].get("tool") == name)

    return ProcessInfo(
        run_id=run_id,
        turns=sum(1 for e in events if e["type"] == "main_step"),
        servers_loaded=sum(1 for e in events if e["type"] == "tool_call"
                           and e["payload"].get("tool") == "load_server" and e["payload"].get("ok")),
        tools_described=_tool("describe_tools"),
        tool_calls=sum(1 for e in events if e["type"] == "tool_call"
                       and e["payload"].get("tool") == "call_tool" and e["payload"].get("ok")),
        specialist_escalations=sum(1 for e in events if e["type"] == "sub_call"),
        judge_ran=_tool("rubric_judge") > 0,
        status=status,
    )


def build_response(assembled: AssembledOutcome, events: list[dict], run_id: str) -> TaskResponse:
    """Serialize a completed run as a `TaskResponse`.

    A planner that finalized with `cannot_complete=True` — a principled "this toolspace cannot serve the
    task" DECLINE — reads as `refused` (a legitimate negative), NOT `ok` and NOT a crash `failed`. The
    reason it wrote into `answer` becomes the refusal `error`; `refusal.reason` is the stable `unsupported`
    code. The outcome stays attached, so the trajectory's coverage facts survive."""
    task = assembled.task or str(_meta(events).get("task", ""))
    if assembled.cannot_complete:
        return TaskResponse(
            id=run_id,
            status="refused",
            task=task,
            outcome=assembled,
            process=_process(events, run_id, status="refused"),
            refusal=RefusalInfo(refused=True, reason="unsupported"),
            error=(assembled.answer or "").strip() or "The toolspace cannot serve this task.",
        )
    status = "ok" if (assembled.answer or "").strip() else "failed"
    return TaskResponse(
        id=run_id,
        status=status,
        task=task,
        outcome=assembled,
        process=_process(events, run_id, status=status),
        error="" if status == "ok" else "The run finalized without a usable answer.",
    )


def build_failed_response(run_id: str, events: list[dict], detail: str, *,
                          reason: str = "run_failed", task: Optional[str] = None) -> TaskResponse:
    """The crash/cancel path — no outcome, but still reports the process counters gathered so far."""
    return TaskResponse(
        id=run_id,
        status="failed",
        task=task if task is not None else str(_meta(events).get("task", "")),
        outcome=None,
        process=_process(events, run_id, status=reason),
        refusal=RefusalInfo(),
        error=detail,
    )
