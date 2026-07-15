"""Render an assembled outcome (or a full response) as human-readable text for the CLI.

Presentation only — pure stdlib + pydantic, no dspy, no new judgement. Fabrication tells
(`cited_unknown`, `unbacked_*`) are surfaced prominently: they are the honest read of the trajectory.
"""

from __future__ import annotations

from .schema import AssembledOutcome, TaskResponse


def render_outcome(outcome: AssembledOutcome) -> str:
    lines: list[str] = []
    lines.append(f"TASK: {outcome.task}" if outcome.task else "TASK: (unspecified)")
    lines.append("")
    lines.append("ANSWER:")
    lines.append(f"  {outcome.answer or '(none)'}")
    if outcome.summary:
        lines.append(f"\nSUMMARY: {outcome.summary}")

    m = outcome.metrics or {}
    lines.append("")
    lines.append(
        "TOOLSPACE USE: "
        f"servers_loaded={m.get('servers_loaded', 0)} "
        f"tools_used={m.get('tools_used', 0)} "
        f"described={m.get('tools_described', 0)} "
        f"calls(ok/fail)={m.get('call_ok', 0)}/{m.get('call_fail', 0)} "
        f"arg_errors={m.get('arg_errors', 0)} "
        f"escalations={m.get('specialist_escalations', 0)} "
        f"turns={m.get('turns', 0)}"
    )
    if outcome.servers_loaded:
        lines.append(f"  servers: {outcome.servers_loaded}")
    if outcome.tools_used:
        lines.append(f"  tools:   {outcome.tools_used}")

    if outcome.criteria_facts:
        lines.append("")
        lines.append("RUBRIC CRITERIA (facts only — scoring is the trainer's job):")
        for f in outcome.criteria_facts:
            lines.append(f"  [{f.category}] {f.criterion} (w={f.weight}): {f.observed}")
    if outcome.judge_observations:
        lines.append("")
        lines.append("JUDGE OBSERVATIONS (opt-in, labels — not a score):")
        for o in outcome.judge_observations:
            met = o.get("met")
            mark = "?" if met is None else ("+" if met else "-")
            lines.append(f"  [{mark}] {o.get('criterion', '?')}: {o.get('note', '')}")

    tells: list[str] = []
    if outcome.cited_unknown:
        tells.append(f"cited criteria with no recorded rubric entry: {outcome.cited_unknown}")
    if outcome.unbacked_servers:
        tells.append(f"claimed servers not in the trace: {outcome.unbacked_servers}")
    if outcome.unbacked_tools:
        tells.append(f"claimed tools not in the trace: {outcome.unbacked_tools}")
    if tells:
        lines.append("")
        lines.append("FABRICATION TELLS (self-report contradicted by the trace):")
        lines.extend(f"  ! {t}" for t in tells)
    return "\n".join(lines)


def render_response(resp: TaskResponse) -> str:
    header = f"[{resp.status}] run={resp.id}"
    if resp.status != "ok" and resp.error:
        header += f"  — {resp.error}"
    if resp.outcome is None:
        return header
    return header + "\n\n" + render_outcome(resp.outcome)
