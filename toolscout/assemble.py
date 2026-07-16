"""Assemble the canonical outcome from the trace — the read-time step that makes EVIDENCE a fact.

The planner's SUBMIT (`TaskOutcome`) is judgement + CITATIONS only. `assemble_outcome` re-sources the
heavy facts from the recorded `tool_call`s so the policy cannot self-report them:

- `servers_loaded` / `tools_used` are re-derived from the trace (successful `load_server` / `call_tool`
  events), NOT trusted from the SUBMIT.
- the planner's OWN self-reported `servers_loaded` / `tools_used` are cross-checked against that truth;
  anything it claims but the trace does not back lands in `unbacked_servers` / `unbacked_tools`.
- `criteria_facts` are the deterministic per-criterion facts (`rubric.criteria_facts`) — the rubric is a
  trainer/eval-side artifact the agent never cites, so there is nothing to cross-check there — and
  `judge_observations` are the opt-in judge tool's per-criterion LABELS (resolved via `judge_call_id`).

Nothing here scores or rewards — it re-sources facts and flags fabrication. This is the assemble-on-read
pattern (a deterministic reduction over trace facts, never a model
judgement in the read path). Runs everywhere the result is consumed — live (cli), re-render, and export —
so labels read facts too. Pure stdlib + pydantic; no dspy.
"""

from __future__ import annotations

from typing import Optional

from rlm_kit.trace import EVENT_RESULT

from . import rubric as rubric_mod
from .schema import AssembledOutcome, Criterion, TaskOutcome


def _outcome_from_payload(out: dict) -> TaskOutcome:
    """Build the SUBMIT model tolerantly from a result payload (ignore legacy keys, default the required)."""
    fields = {k: out[k] for k in TaskOutcome.model_fields if k in out}
    fields.setdefault("answer", "")
    return TaskOutcome(**fields)


def _judge_observations(events: list[dict], judge_call_id: Optional[str]) -> list[dict]:
    """The per-criterion observations from the opt-in judge tool_call.

    Resolve by the planner's cited `judge_call_id` when it matches a recorded `rubric_judge` event; else
    fall back to the LAST `rubric_judge` call (the planner returns a string and cannot always echo the
    exact step_id, so the citation is a hint, not the sole key)."""
    judges = [e for e in events
              if e.get("type") == "tool_call" and e.get("payload", {}).get("tool") == "rubric_judge"]
    if not judges:
        return []
    chosen = None
    if judge_call_id:
        chosen = next((e for e in judges if e.get("step_id") == judge_call_id), None)
    if chosen is None:
        chosen = judges[-1]
    obs = chosen["payload"].get("observations")
    return obs if isinstance(obs, list) else []


def assemble_outcome(outcome: TaskOutcome, events: list[dict], *,
                     criteria: Optional[list[Criterion]] = None) -> AssembledOutcome:
    """Attach re-sourced facts + fabrication tells to the planner's judgement."""
    facts = rubric_mod.trace_facts(events)
    servers_loaded = facts["servers_loaded"]
    tools_used = facts["tools_called_ok"]

    # Cross-check the planner's self-report against the deterministic truth.
    backed_servers = set(servers_loaded)
    unbacked_servers = sorted({s for s in (outcome.servers_loaded or []) if s not in backed_servers})
    backed_tool_forms = set(tools_used) | {t.split(":")[-1] for t in tools_used}
    unbacked_tools = sorted({t for t in (outcome.tools_used or []) if t not in backed_tool_forms})

    # Resolve the run's rubric to attach deterministic per-criterion facts (the real per-criterion signal;
    # the agent never cites the rubric — there is no cited_criteria to cross-check).
    crit_list = criteria if criteria is not None else rubric_mod.rubric_from_meta(events).criteria
    criteria_facts = rubric_mod.criteria_facts(events, crit_list)
    judge_observations = _judge_observations(events, outcome.judge_call_id)

    metrics = {
        "servers_loaded": len(servers_loaded),
        "tools_used": len(tools_used),
        "tools_described": len(facts["tools_described"]),
        "call_ok": facts["call_ok_count"],
        "call_fail": facts["call_fail_count"],
        "arg_errors": facts["arg_error_count"],
        "backend_errors": facts["backend_error_count"],
        "specialist_escalations": sum(1 for e in events if e.get("type") == "sub_call"),
        "turns": sum(1 for e in events if e.get("type") == "main_step"),
        "finalized": facts["finalized"],
        "judge_ran": bool(judge_observations),
    }

    return AssembledOutcome(
        task=_task_of(events),
        answer=outcome.answer,
        summary=outcome.summary,
        servers_loaded=servers_loaded,
        tools_used=tools_used,
        criteria_facts=criteria_facts,
        judge_observations=judge_observations,
        unbacked_servers=unbacked_servers,
        unbacked_tools=unbacked_tools,
        metrics=metrics,
    )


def _task_of(events: list[dict]) -> str:
    for e in events:
        if e.get("type") == "run_start":
            meta = (e.get("payload") or {}).get("meta") or {}
            return str(meta.get("task", ""))
    return ""


def outcome_from_events(events: list[dict]) -> Optional[AssembledOutcome]:
    """Reconstruct the assembled outcome from a saved trace's result event, or None if it never finalized."""
    results = [e for e in events if e["type"] == EVENT_RESULT]
    if not results:
        return None
    out = results[-1]["payload"].get("output")
    if not isinstance(out, dict):
        return None
    return assemble_outcome(_outcome_from_payload(out), events)
