"""Map a toolscout trace event → a public SSE event (the single source of truth for the streamed event
surface). Pure function, no web deps — unit-tested independently of the server.

A trace event is `{type, step_id, ts, payload}` (rlm-kit's frozen trace/v1). We surface only the events a
UI needs and rename them to a stable `task.<noun>.<verb>` vocabulary (OpenAI-Responses-flavored). Unknown
/ internal events return None (skipped). The full structured result is NOT streamed — the client GETs
`/v1/runs/{run_id}` after `task.run.completed`.

The surfaced tools are toolscout's ISL/ITL/PTC meta-tools plus the opt-in judge + skills:
`list_servers` (the server index), `load_server` (ISL — materialize a server), `describe_tools` (ITL —
pull signatures), `call_tool` (PTC — invoke a materialized tool), `rubric_judge`, `read_skill`. A
`call_tool` payload nests the invocation as `args = {tool, args}` alongside a top-level `server` +
`ok`/`reason` + `result`/`error` (see `toolscout.toolspace`); a `sub_call` carries `input`/`processed`
/`raw` (rlm-kit's sub-LM specialist escalation), not question/answer.
"""

from __future__ import annotations

from typing import Any, Optional

_ROLES = ("planner", "specialist", "judge")


def to_event(trace_event: dict) -> Optional[dict[str, Any]]:
    """Return `{"event": <name>, "data": {...}}` for a surfaced trace event, else None."""
    t = trace_event.get("type")
    p = trace_event.get("payload") or {}

    if t == "run_start":
        meta = p.get("meta") or {}
        return _ev("task.run.created", {
            "models": {k: meta[k] for k in _ROLES if meta.get(k)},
            "task": meta.get("task"),                  # the task under solve (the run's initial state)
            "toolspace": meta.get("toolspace"),        # the toolspace path, or "demo"
            "max_iterations": meta.get("max_iterations"),
            "criteria": len(meta.get("rubric") or []),  # the rubric criteria count (LABELS, not a score)
        })
    if t == "main_step":
        # Surfaced for the REPLAY (step-sorted) stream. The LIVE endpoint's sink drops main_step (it
        # flushes post-hoc, so it would arrive as a trailing burst — see live.trace_event_sink).
        return _ev("task.plan.step", {
            "turn": p.get("turn"),
            "reasoning": p.get("reasoning"),
            "has_code": bool(p.get("code")),
        })
    if t == "sub_call":
        return _ev("task.specialist.escalation", {
            "question": p.get("input"),
            "answer": p.get("processed") or p.get("raw"),
        })
    if t == "result":
        return _ev("task.result.done", {})           # signal — client GETs the full response
    if t == "run_end":
        return _ev("task.run.completed", {})          # the ONE terminal event
    if t == "final":
        # A real finished trace holds BOTH `final` (from rlm-kit's record_main_trajectory) and `run_end`
        # (from the recorder's __exit__). Mapping BOTH to the terminal event would emit
        # `task.run.completed` TWICE per replay — and the `final` copy lands BEFORE `result`, so a client
        # acting on the first `completed` fires before `task.result.done`. `run_end` is the canonical
        # terminal; a truncated trace with `final` but no `run_end` is covered by the replay endpoint's
        # synthesized-terminal fallback. So skip `final`.
        return None

    if t == "tool_call":
        tool = p.get("tool")
        args = p.get("args") or {}
        if tool == "list_servers":
            servers = p.get("servers") or []
            return _ev("task.servers.listed", {"n": len(servers), "servers": servers})
        if tool == "load_server":                     # ISL — the server-selection decision
            return _ev("task.server.loaded", {
                "server": p.get("server") or args.get("server"),
                "ok": bool(p.get("ok")),
                "tools": p.get("tool_names") or [],
            })
        if tool == "describe_tools":                  # ITL — pull signatures just-in-time
            described = p.get("described") or []
            return _ev("task.tools.described", {"n": len(described), "described": described})
        if tool == "call_tool":                       # PTC — invoke a materialized tool
            return _ev("task.tool.called", {
                "server": p.get("server"),
                "tool": args.get("tool"),
                "ok": bool(p.get("ok")),
                "reason": p.get("reason"),             # arg_error / not_loaded / unknown_* / backend_error
                "error": p.get("error"),
            })
        if tool == "rubric_judge":
            obs = p.get("observations") or []
            return _ev("task.judge", {
                "ok": bool(p.get("ok")),
                "circuit_broken": bool(p.get("circuit_broken", False)),
                "error": p.get("error"),
                "n": len(obs),
            })
        if tool in ("read_skill", "list_skills"):
            return _ev("task.skill.read", {"name": args.get("name") or "(catalog)"})

    return None  # unknown type / unsurfaced tool — skip


def _ev(name: str, data: dict) -> dict[str, Any]:
    return {"event": name, "data": data}
