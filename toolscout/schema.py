"""Pydantic shapes for toolscout — a JUDGEMENT-ONLY SUBMIT + assemble-on-read.

The planner's SUBMIT type (`TaskOutcome`) is deliberately CITATION-ONLY: it carries the planner's own
`answer`/`summary` and REFERENCE lists (`servers_loaded`, `tools_used`, `cited_criteria`, an optional
`judge_call_id`) — and structurally has NO field for raw tool outputs, per-criterion met/unmet, an
aggregate score, or a reward. So the policy cannot self-report or fabricate evidence: the heavy facts
are re-sourced from the trace on read (`assemble.assemble_outcome`), self-reported reference lists are
cross-checked against the recorded `tool_call`s, and a cited id/criterion with no recorded event lands
in `cited_unknown` — the fabrication tell. This is the rlm-kit judgement-only pattern (README "Building
a consumer"), the same structural boundary a sibling rlm-kit consumer's verdict type enforces.

No dspy import — these are plain pydantic models, unit-testable in isolation and passed to dspy only as
`output_model` (resolved via `custom_types=`, never call-stack name resolution).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# ATLAS rubric categories: Task Fulfillment, Tool Appropriateness, Tool Grounding, Parameter Accuracy.
# The category values are toolscout's domain (the kit, if PB3 lands, keeps `category` an opaque string).
CRITERION_CATEGORIES = ("TF", "TA", "TG", "PA")


class Criterion(BaseModel):
    """One rubric criterion — the STRUCTURE only. Scoring (`dᵢ∈[0,1]`) is the TRAINER's job, never here."""

    name: str = Field(..., description="short unique criterion id, e.g. 'answers_all_parts'")
    description: str = Field(..., description="what the trajectory must satisfy, observable from the trace")
    weight: float = Field(1.0, description="relative weight WITHIN its category (the trainer aggregates)")
    category: str = Field(..., description="one of TF / TA / TG / PA")


class RubricCriteria(BaseModel):
    """The per-task rubric: a set of criteria generated offline (host-side), stored in `run_start` meta."""

    criteria: list[Criterion] = Field(default_factory=list)


class TaskOutcome(BaseModel):
    """The planner's SUBMIT — judgement + CITATIONS only (see module docstring). The `output_model`."""

    answer: str = Field(..., description="the final answer to the task, grounded in loaded tool outputs")
    summary: str = Field("", description="one or two sentences on how the toolspace was used")
    servers_loaded: list[str] = Field(
        default_factory=list, description="MCP servers the planner loaded (cross-checked against the trace)"
    )
    tools_used: list[str] = Field(
        default_factory=list, description="tools actually called (cross-checked against the trace)"
    )
    cited_criteria: list[str] = Field(
        default_factory=list, description="rubric criterion NAMES the planner claims it satisfied (names only)"
    )
    judge_call_id: Optional[str] = Field(
        None, description="step_id of the rubric_judge tool_call, if the opt-in self-check ran"
    )


class CriterionFact(BaseModel):
    """A DETERMINISTIC observation about one criterion, extracted from the trace (a FACT, not a score)."""

    criterion: str
    category: str
    weight: float
    observed: dict = Field(default_factory=dict, description="deterministic facts (counts, ids, cited outputs)")


class AssembledOutcome(BaseModel):
    """`TaskOutcome` re-hydrated from the trace: citations resolved, facts re-sourced, fabrication flagged.

    Built by `assemble.assemble_outcome` and read by render / response / export. Nothing here is a score
    or a reward — `criteria_facts` are deterministic observations; `judge_observations` are the opt-in
    judge's per-criterion LABELS (if it ran); the trainer scores from these.
    """

    task: str
    answer: str
    summary: str = ""
    servers_loaded: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    cited_criteria: list[str] = Field(default_factory=list)
    # Deterministic per-criterion facts from `rubric.criteria_facts(events)`.
    criteria_facts: list[CriterionFact] = Field(default_factory=list)
    # The opt-in judge tool's per-criterion observations (labels, LM-decided), if `judge_call_id` resolved.
    judge_observations: list[dict] = Field(default_factory=list)
    # Fabrication tells: cited criteria/ids or self-reported servers/tools NOT backed by a recorded event.
    cited_unknown: list[str] = Field(default_factory=list)
    unbacked_servers: list[str] = Field(default_factory=list)
    unbacked_tools: list[str] = Field(default_factory=list)
    # Objective effort/coverage counters (labels, not reward).
    metrics: dict = Field(default_factory=dict)


class RefusalInfo(BaseModel):
    refused: bool = False
    reason: str = ""


class ProcessInfo(BaseModel):
    """How the run went — effort surface for an operator/UI (not reward)."""

    run_id: str = ""
    turns: int = 0
    servers_loaded: int = 0
    tools_described: int = 0
    tool_calls: int = 0
    specialist_escalations: int = 0
    judge_ran: bool = False
    status: str = "ok"


class TaskResponse(BaseModel):
    """The API envelope (OpenAI-Responses-flavored) a UI / caller consumes for one run."""

    id: str
    object: str = "toolscout.task_response"
    status: str = "ok"                       # ok | refused | failed
    task: str = ""
    outcome: Optional[AssembledOutcome] = None
    process: ProcessInfo = Field(default_factory=ProcessInfo)
    refusal: RefusalInfo = Field(default_factory=RefusalInfo)
    error: str = ""
