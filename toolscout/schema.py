"""Pydantic shapes for toolscout — a JUDGEMENT-ONLY SUBMIT + assemble-on-read.

The planner's SUBMIT type (`TaskOutcome`) is deliberately CITATION-ONLY: it carries the planner's own
`answer`/`summary` and REFERENCE lists (`servers_loaded`, `tools_used`, an optional `judge_call_id`) —
and structurally has NO field for raw tool outputs, per-criterion met/unmet, an aggregate score, or a
reward. So the policy cannot self-report or fabricate evidence: the heavy facts are re-sourced from the
trace on read (`assemble.assemble_outcome`), self-reported reference lists are cross-checked against the
recorded `tool_call`s, and a claimed server/tool with no recorded event lands in `unbacked_servers` /
`unbacked_tools` — the fabrication tell. (There is no `cited_criteria`: the rubric is a trainer/eval-side
artifact the agent never sees at inference, so citing it would be meaningless; the per-criterion signal
is the deterministic `criteria_facts`.) This is the rlm-kit judgement-only pattern (README "Building a
consumer").

No dspy import — these are plain pydantic models, unit-testable in isolation and passed to dspy only as
`output_model` (resolved via `custom_types=`, never call-stack name resolution).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# reward-free rubric TYPES — now rlm-kit's shared, taxonomy-agnostic primitives, re-exported here so
# toolscout's own `from .schema import Criterion, ...` call sites are unchanged.
from rlm_kit.rubric import Criterion, CriterionFact, RubricCriteria  # noqa: F401 (re-export, back-compat)

# ATLAS rubric categories: Task Fulfillment, Tool Appropriateness, Tool Grounding, Parameter Accuracy.
# The rubric TYPES above are rlm-kit's (category is opaque to the kit); toolscout owns only this ATLAS
# category set + the criterion descriptions + the lens (see rubric.py).
CRITERION_CATEGORIES = ("TF", "TA", "TG", "PA")


class TaskOutcome(BaseModel):
    """The planner's SUBMIT — judgement + CITATIONS only (see module docstring). The `output_model`.

    Deliberately NO `cited_criteria`: the rubric is a TRAINER/EVAL-side artifact the agent never sees at
    inference (as in ATLAS), so asking the policy to cite criterion names it cannot know is meaningless.
    The real per-criterion signal is the deterministic `criteria_facts`, re-sourced from the trace.
    """

    answer: str = Field(..., description="the final answer to the task, grounded in loaded tool outputs")
    summary: str = Field("", description="one or two sentences on how the toolspace was used")
    servers_loaded: list[str] = Field(
        default_factory=list, description="MCP servers the planner loaded (cross-checked against the trace)"
    )
    tools_used: list[str] = Field(
        default_factory=list, description="tools actually called (cross-checked against the trace)"
    )
    judge_call_id: Optional[str] = Field(
        None, description="step_id of the rubric_judge tool_call, if the opt-in self-check ran"
    )


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
    # Deterministic per-criterion facts from `rubric.criteria_facts(events)`.
    criteria_facts: list[CriterionFact] = Field(default_factory=list)
    # The opt-in judge tool's per-criterion observations (labels, LM-decided), if `judge_call_id` resolved.
    judge_observations: list[dict] = Field(default_factory=list)
    # Fabrication tells: self-reported servers/tools NOT backed by a recorded event.
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
