"""Export toolscout run traces as REWARD-FREE trajectory datasets.

toolscout is the ROLLOUT source (rollout → reward → training), NOT the trainer. This module emits raw
materials only: the trajectory splits (sft_turns / planner tool-ops / judge), per-run intrinsic LABELS
(coverage/fabrication facts), per-run objective METRICS, and — the ATLAS contribution — the per-run RUBRIC
plus its deterministic per-criterion FACTS and the opt-in judge's per-criterion OBSERVATIONS (labels). No
reward scalar is attached (`reward=None` to rlm-kit's exporters); reward composition, the rubric's `dᵢ`
scoring, credit assignment, and GRPO/SFT live in the SEPARATE fine-tuning project.

Usage: python -m toolscout.rl_export "traces/*.jsonl" dataset.json
"""

from __future__ import annotations

import glob
import json
import sys

from rlm_kit.dataset import export_actions, export_sft_turns
from rlm_kit.trace import group_by_run, load_events

# The four ISL/ITL/PTC meta-tools are the PLANNER's toolspace ops; rubric_judge is the opt-in judge tool.
META_TOOLS = ("list_servers", "load_server", "describe_tools", "call_tool")
JUDGE_TOOL = "rubric_judge"
_DEFAULT_MAX_ITERATIONS = 30


def _meta(events: list[dict]) -> dict:
    for e in events:
        if e.get("type") == "run_start":
            return e.get("payload", {}).get("meta") or {}
    return {}


def _resolve_max_iterations(events: list[dict]) -> int:
    m = _meta(events).get("max_iterations")
    return m if isinstance(m, int) and m > 0 else _DEFAULT_MAX_ITERATIONS


def load_runs(*trace_paths: str) -> dict[str, list[dict]]:
    events: list[dict] = []
    for path in trace_paths:
        events.extend(load_events(path))
    return group_by_run(events)


def run_labels(events: list[dict]) -> dict:
    """Intrinsic OUTCOME labels for one run — facts, NOT a reward. Derived from the ASSEMBLED outcome so
    coverage + fabrication read the deterministic trace, never the planner's self-report."""
    from .assemble import outcome_from_events

    assembled = outcome_from_events(events)
    if assembled is None:
        return {"finalized": False, "servers_loaded": 0, "tools_used": 0,
                "unbacked_servers": 0, "unbacked_tools": 0, "judge_ran": False}
    return {
        "finalized": True,
        "servers_loaded": len(assembled.servers_loaded),
        "tools_used": len(assembled.tools_used),
        "unbacked_servers": len(assembled.unbacked_servers),
        "unbacked_tools": len(assembled.unbacked_tools),
        "judge_ran": bool(assembled.judge_observations),
    }


def run_metrics(events: list[dict]) -> dict:
    """Objective EFFORT metrics — the raw material a trainer shapes into a reward. Facts, never a score."""
    from .rubric import trace_facts

    facts = trace_facts(events)
    cap = _resolve_max_iterations(events)
    steps = sum(1 for e in events if e["type"] == "main_step")
    ts = [e["ts"] for e in events if isinstance(e.get("ts"), (int, float))]
    return {
        "steps": steps,
        "list_servers_calls": sum(1 for e in events if e["type"] == "tool_call"
                                  and e["payload"].get("tool") == "list_servers"),
        "load_calls": facts["load_count"],
        "describe_calls": facts["describe_count"],
        "call_ok": facts["call_ok_count"],
        "call_fail": facts["call_fail_count"],
        "arg_errors": facts["arg_error_count"],
        "backend_errors": facts["backend_error_count"],
        "predispatch_rejects": facts["predispatch_reject_count"],
        "specialist_escalations": sum(1 for e in events if e["type"] == "sub_call"),
        "judge_calls": sum(1 for e in events if e["type"] == "tool_call"
                           and e["payload"].get("tool") == JUDGE_TOOL),
        "skill_reads": sum(1 for e in events if e["type"] == "tool_call"
                           and e["payload"].get("tool") == "read_skill"),
        "elapsed_s": round(max(ts) - min(ts), 3) if len(ts) >= 2 else None,
        "hit_iteration_cap": steps >= cap,
    }


def rubric_signal(events: list[dict]) -> dict:
    """The ATLAS rubric surface for one run — the per-criterion FACTS + opt-in judge OBSERVATIONS + the
    rubric itself. All LABELS: the trainer computes `dᵢ∈[0,1]` and aggregates; this kit never does."""
    from .assemble import outcome_from_events
    from .rubric import rubric_from_meta

    assembled = outcome_from_events(events)
    return {
        "rubric": [c.model_dump() for c in rubric_from_meta(events).criteria],
        "criteria_facts": [f.model_dump() for f in (assembled.criteria_facts if assembled else [])],
        "judge_observations": assembled.judge_observations if assembled else [],
    }


def export_dataset(runs: dict[str, list[dict]]) -> dict:
    """Build the REWARD-FREE trajectory bundle. The PLANNER is the RLM root (ONE multi-turn policy —
    `sft_turns` for SFT, `planner`/`actions` for RL); its toolspace ops split from the opt-in judge tool.
    Records carry `reward=None`; the rubric signal rides alongside as per-run LABELS."""
    actions = export_actions(runs, reward=None)
    tool_acts = [a for a in actions if a["kind"] == "tool"]
    return {
        "actions": actions,
        "planner": [a for a in actions if a["kind"] == "planner"],
        "toolspace_ops": [a for a in tool_acts if a.get("tool") in META_TOOLS],
        "judge": [a for a in tool_acts if a.get("tool") == JUDGE_TOOL],
        "sft_turns": export_sft_turns(runs),
        "labels": {rid: run_labels(ev) for rid, ev in runs.items()},
        "metrics": {rid: run_metrics(ev) for rid, ev in runs.items()},
        "rubric_signal": {rid: rubric_signal(ev) for rid, ev in runs.items()},
    }


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: python -m toolscout.rl_export <trace-glob...> <out.json>")
        raise SystemExit(2)
    *globs, out = sys.argv[1:]
    paths = [p for g in globs for p in glob.glob(g)]
    runs = load_runs(*paths)
    bundle = export_dataset(runs)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=2, default=str)
    finalized = sum(1 for lab in bundle["labels"].values() if lab["finalized"])
    print(f"runs={len(runs)} ({finalized} finalized) | actions={len(bundle['actions'])} "
          f"(toolspace_ops={len(bundle['toolspace_ops'])}, judge={len(bundle['judge'])}) | "
          f"sft_turns={len(bundle['sft_turns'])} | reward-free -> {out}")


if __name__ == "__main__":
    main()
