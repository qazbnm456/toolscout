"""Shared test fixtures + helpers for toolscout.

`run_recorded` drives the REAL meta-tools under a REAL `TraceRecorder` (no dspy, no model, no Deno) and
returns the events the read path (assemble/rubric/response/export) consumes — so those tests read a trace
the tools actually wrote, not a hand-built one.
"""

from __future__ import annotations

import pytest

from toolscout.catalog import demo_catalog
from toolscout.config import ToolscoutConfig
from toolscout.rubric import default_rubric, rubric_to_meta
from toolscout.toolspace import Toolspace, build_toolspace_tools


@pytest.fixture
def config() -> ToolscoutConfig:
    return ToolscoutConfig(main_model="planner-x", sub_model="specialist-y", judge_model="judge-z")


@pytest.fixture
def toolspace(config) -> Toolspace:
    return Toolspace(demo_catalog(), config)


def run_recorded(tmp_path, calls, outcome=None, *, run_id="t", task="add six and seven",
                 rubric=None, main_steps=1, sub_calls=0, judge_events=(), ts=None):
    """Record a scripted sequence of meta-tool calls + an optional result, and return the events.

    `calls` is a list of (tool_name, kwargs) dispatched through the REAL tools built over a fresh demo
    toolspace. `outcome` (a dict) is recorded as the result. `judge_events` is a list of payload dicts
    recorded as extra `tool_call`s (e.g. a rubric_judge event)."""
    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    ts = ts if ts is not None else Toolspace(demo_catalog(), ToolscoutConfig(main_model="x", sub_model="y"))
    tools = {t.__name__: t for t in build_toolspace_tools(ts)}
    rubric = rubric if rubric is not None else default_rubric(task)
    path = str(tmp_path / f"{run_id}.jsonl")
    meta = {"task": task, "rubric": rubric_to_meta(rubric), "planner": "p", "specialist": "s",
            "judge": None, "max_iterations": 30, "max_llm_calls": 8, "toolspace": "demo"}
    with TraceRecorder(path, run_id=run_id, meta=meta) as rec:
        for _ in range(main_steps):
            rec.record("main_step", {})
        for name, kwargs in calls:
            tools[name](**kwargs)
        for payload in judge_events:
            rec.record("tool_call", payload)
        for _ in range(sub_calls):
            rec.record("sub_call", {"escalation": "q"})
        if outcome is not None:
            rec.record_result(outcome)
    return load_events(path, run_id)
