"""Shared fixtures for the eval-harness suite — all offline (stub judge, demo catalog, no network).

`record_run` drives toolscout's REAL meta-tools under a REAL `TraceRecorder` (no dspy, no model, no
Deno) and returns the events — so the harness is tested against the actual trace contract the rollout
writes, not a hand-built imitation. It mirrors toolscout's own `tests/conftest.run_recorded`, which is
not importable from this separate suite.
"""

from __future__ import annotations

from toolscout.catalog import demo_catalog
from toolscout.config import ToolscoutConfig
from toolscout.rubric import default_rubric, rubric_to_meta
from toolscout.toolspace import Toolspace, build_toolspace_tools


def record_run(tmp_path, calls, outcome=None, *, run_id="t", task="what is 6 times 7?", main_steps=1):
    """Record a scripted sequence of meta-tool calls + an optional result; return the run's events.

    `calls` is a list of (tool_name, kwargs) dispatched through the real ISL/ITL/PTC tools over a fresh
    demo toolspace. `outcome` (a dict) is recorded as the result event; None leaves the run
    never-finalized. The trace lands at `tmp_path/<run_id>.jsonl`."""
    from rlm_kit import TraceRecorder
    from rlm_kit.trace import load_events

    toolspace = Toolspace(demo_catalog(), ToolscoutConfig(main_model="x", sub_model="y"))
    tools = {t.__name__: t for t in build_toolspace_tools(toolspace)}
    path = str(tmp_path / f"{run_id}.jsonl")
    meta = {"task": task, "rubric": rubric_to_meta(default_rubric(task)), "planner": "p",
            "specialist": "s", "judge": None, "max_iterations": 30, "max_llm_calls": 8,
            "toolspace": "demo"}
    with TraceRecorder(path, run_id=run_id, meta=meta) as recorder:
        for _ in range(main_steps):
            recorder.record("main_step", {})
        for name, kwargs in calls:
            tools[name](**kwargs)
        if outcome is not None:
            recorder.record_result(outcome)
    return load_events(path, run_id)


def math_calls():
    """The canonical demo run: load math, describe, multiply 6*7."""
    return [
        ("load_server", {"server": "math"}),
        ("describe_tools", {"names": ["mul"]}),
        ("call_tool", {"server": "math", "tool": "mul", "args": {"a": 6, "b": 7}}),
    ]
