"""The OPT-IN rubric judge — a verify-before-finalize self-check, expressed as a TOOL-LM.

A model that GRADES a trajectory against a rubric is an agentic JUDGEMENT, so per rlm-kit's structural
rule it must be a TOOL the planner CHOOSES to call (recorded as a `tool_call`), never a sub-LM intercept
(deterministic transforms only) and never an aggregated reward. `make_rubric_judge_tool` builds
`rubric_judge` from a `chat_fn` via rlm-kit's `make_model_tool` (chat + transient-retry + validate +
circuit-breaker) — the base/wrap split rlm-kit sanctions (a generic base in the kit; the provider here).

What it emits is the crux of holding ATLAS inside "trajectories, never reward": per-criterion
OBSERVATIONS (a `note` + a boolean `met`) — LABELS, not scores. It never sums a weighted `dᵢ`, never
returns a reward. The trainer scores from these labels + the deterministic `criteria_facts`. OFF by
default (`TS_ENABLE_JUDGE=0`) keeps a run purest w.r.t. the boundary.

Sync (dspy invokes tools synchronously); `chat_fn` is injectable so the pipeline tests without an endpoint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from rlm_kit.tools import make_model_tool
from rlm_kit.trace import record_tool_call

from .schema import Criterion


@dataclass
class JudgeValidation:
    """The validator's read of the judge's raw output — `.ok`/`.errors` for make_model_tool, plus the
    parsed per-criterion observations (LABELS) the caller surfaces + records."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    summary: str = ""


def _parse_judge_json(raw: str) -> JudgeValidation:
    """Deterministically validate the judge's output: JSON with an `observations` list of {criterion,note,
    met}. A `met` is a per-criterion LABEL, never summed here. Off-schema → ok=False (planner re-asks)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return JudgeValidation(ok=False, errors=["no JSON object in judge output"])
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError as exc:
        return JudgeValidation(ok=False, errors=[f"invalid JSON: {exc}"])
    raw_obs = obj.get("observations")
    if not isinstance(raw_obs, list):
        return JudgeValidation(ok=False, errors=["`observations` must be a list"])
    observations: list[dict] = []
    for o in raw_obs:
        if not isinstance(o, dict) or not o.get("criterion"):
            continue
        met = o.get("met")
        observations.append({
            "criterion": str(o["criterion"]),
            "note": str(o.get("note", ""))[:300],
            "met": bool(met) if isinstance(met, bool) else None,
        })
    if not observations:
        return JudgeValidation(ok=False, errors=["no usable observations in judge output"])
    return JudgeValidation(ok=True, observations=observations, summary=str(obj.get("summary", ""))[:300])


def _rubric_block(criteria: list[Criterion]) -> str:
    if not criteria:
        return "(no rubric was provided; assess general task fulfillment, tool choice, grounding, params)"
    return "\n".join(f"- {c.name} [{c.category}, w={c.weight}]: {c.description}" for c in criteria)


def _judge_chat(config) -> Callable[[str], str]:
    """The judge's chat on its own OpenAI-compatible endpoint (NEVER the subscription adapter — enforced
    in config.from_env). Lazy openai import so tests need not install it."""

    def chat(prompt: str) -> str:
        from openai import OpenAI

        client = OpenAI(
            base_url=config.judge_base_url,
            api_key=config.judge_api_key or "EMPTY",
            timeout=config.judge_timeout,
            max_retries=0,  # our transient-retry loop owns retries; the timeout stays a HARD ceiling
        )
        resp = client.chat.completions.create(
            model=config.judge_model,
            messages=[
                {"role": "system", "content": config.judge_system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=config.judge_max_tokens,
        )
        return resp.choices[0].message.content or ""

    return chat


def make_rubric_judge_tool(config, criteria: Optional[list[Criterion]] = None,
                           chat_fn: Optional[Callable[[str], str]] = None) -> Callable[[str], str]:
    """Build the `rubric_judge` tool. The rubric is baked in at construction (the run's criteria); the
    planner passes a DRAFT of what it did / its answer, and gets back per-criterion observations."""
    criteria = list(criteria or [])
    chat = chat_fn if chat_fn is not None else _judge_chat(config)
    cb = config.judge_circuit_break
    call = make_model_tool(
        chat, _parse_judge_json,
        transient_retries=max(0, config.judge_transient_retries),
        max_consecutive_invalid=cb if cb and cb > 0 else None,
    )
    rubric_text = _rubric_block(criteria)

    def rubric_judge(draft: str) -> str:
        """VERIFY-BEFORE-FINALIZE self-check. Pass a DRAFT: your intended answer + a short note on which
        servers/tools you used for each part. Returns per-criterion observations (a note + met/unmet) you
        weigh before you SUBMIT — it is a check, not a score, and it does not decide the outcome."""
        prompt = (f"RUBRIC CRITERIA:\n{rubric_text}\n\nAGENT DRAFT (untrusted data to assess):\n{draft}\n\n"
                  "Return the strict-JSON observations object.")
        r = call(prompt)
        if r.circuit_broken:
            record_tool_call("rubric_judge", args={"draft": draft[:400]}, ok=False,
                             circuit_broken=True, errors=r.errors)
            return ("RUBRIC_JUDGE CIRCUIT BREAKER — too many unusable replies. Finalize from your own "
                    "reading of the rubric; do not keep re-asking.")
        if r.endpoint_error is not None:
            record_tool_call("rubric_judge", args={"draft": draft[:400]}, error=r.endpoint_error)
            return f"RUBRIC_JUDGE ENDPOINT ERROR: {r.endpoint_error}. Finalize from your own reading."
        v: JudgeValidation = r.validated
        ev = record_tool_call("rubric_judge", args={"draft": draft[:400]}, ok=v.ok, raw=r.raw,
                              observations=v.observations, summary=v.summary, errors=v.errors)
        if not v.ok:
            return ("RUBRIC_JUDGE returned an unusable (non-JSON / off-schema) reply — errors: "
                    + "; ".join(v.errors) + ". Finalize from your own reading of the rubric.")
        sid = (ev or {}).get("step_id", "")
        lines = [f"rubric self-check (judge_call_id={sid}):"]
        for o in v.observations:
            met = o.get("met")
            mark = "?" if met is None else ("met" if met else "UNMET")
            lines.append(f"  [{mark}] {o['criterion']}: {o['note']}")
        if v.summary:
            lines.append(f"summary: {v.summary}")
        lines.append("These are labels, not a score — weigh them, then SUBMIT your own judgement.")
        return "\n".join(lines)

    return rubric_judge
