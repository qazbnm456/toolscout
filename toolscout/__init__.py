"""toolscout — solve a task over a LARGE MCP toolspace with a small planner, as a traced RLM harness.

A downstream *consumer* of rlm-kit (git dep, editable for co-dev) implementing the ATLAS approach
(Iterative Server/Tool Loading + Programmatic Tool Calling + rubric decomposition): a small planner
DISCOVERS a big toolspace progressively through four fixed meta-tools, computes over tool results as code
in the sandboxed REPL, and emits a judgement-only structured outcome whose heavy facts are re-sourced from
the trace on read. ATLAS is a TRAINING paper; toolscout is the ROLLOUT stage only — it emits the rubric +
per-criterion FACTS as LABELS and never a reward (the trainer scores).

Public surface::

    from toolscout import ToolscoutConfig, run, solve_task              # drive a run
    from toolscout import TaskOutcome, AssembledOutcome, TaskResponse   # shapes
    from toolscout import RubricCriteria, Criterion, default_rubric     # rubric
    from toolscout import Catalog, StaticCatalog, demo_catalog, load_catalog   # the toolspace
    from toolscout import assemble_outcome, build_response, export_dataset

`config`, `schema`, `catalog`, `scaffolding`, `toolspace`, `rubric`, `assemble`, `response`, `render`,
`rl_export` import NO dspy at module top (unit-testable in isolation). `SolveTask` / `setup` / `run` /
`solve_task` pull in dspy lazily (via RLMTask).
"""

from __future__ import annotations

from .assemble import assemble_outcome, outcome_from_events
from .catalog import Catalog, Param, ServerInfo, StaticCatalog, ToolSpec, demo_catalog, load_catalog
from .config import SUBSCRIPTION_PREFIX, ToolscoutConfig
from .render import render_outcome, render_response
from .response import build_failed_response, build_response
from .rl_export import export_dataset, run_labels, run_metrics, rubric_signal
from .rubric import (
    criteria_facts,
    default_rubric,
    generate_rubric,
    rubric_from_meta,
    rubric_to_meta,
    trace_facts,
)
from .scaffolding import coerce_args, render_server_index, render_tool, signature
from .schema import (
    CRITERION_CATEGORIES,
    AssembledOutcome,
    Criterion,
    CriterionFact,
    ProcessInfo,
    RefusalInfo,
    RubricCriteria,
    TaskOutcome,
    TaskResponse,
)
from .toolspace import Toolspace, build_toolspace_tools

__all__ = [
    "ToolscoutConfig",
    "SUBSCRIPTION_PREFIX",
    # shapes
    "TaskOutcome",
    "AssembledOutcome",
    "TaskResponse",
    "ProcessInfo",
    "RefusalInfo",
    "Criterion",
    "CriterionFact",
    "RubricCriteria",
    "CRITERION_CATEGORIES",
    # the toolspace
    "Catalog",
    "StaticCatalog",
    "ToolSpec",
    "Param",
    "ServerInfo",
    "demo_catalog",
    "load_catalog",
    "Toolspace",
    "build_toolspace_tools",
    # scaffolding
    "signature",
    "render_tool",
    "render_server_index",
    "coerce_args",
    # rubric
    "default_rubric",
    "generate_rubric",
    "criteria_facts",
    "trace_facts",
    "rubric_from_meta",
    "rubric_to_meta",
    # assemble / render / response / export
    "assemble_outcome",
    "outcome_from_events",
    "build_response",
    "build_failed_response",
    "render_outcome",
    "render_response",
    "export_dataset",
    "run_labels",
    "run_metrics",
    "rubric_signal",
    # dspy-bearing (lazy):
    "SolveTask",
    "setup",
    "run",
    "solve_task",
    "make_rubric_judge_tool",
]

__version__ = "0.1.0"


def __getattr__(name: str):  # PEP 562 — defer the dspy import to first use
    if name in ("SolveTask", "setup"):
        from . import agent

        return getattr(agent, name)
    if name in ("run", "solve_task"):
        from . import cli

        return getattr(cli, name)
    if name == "make_rubric_judge_tool":
        from .judge_tool import make_rubric_judge_tool

        return make_rubric_judge_tool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
