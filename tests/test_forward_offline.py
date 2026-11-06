"""L2 — offline INTEGRATION through the REAL dspy.RLM.aforward loop (no live model, no Deno, no network).

Uses rlm-harness's `rlm_harness.testing` seam: a scripted DummyLM drives the planner, a `ScriptedInterpreter`
runs each turn's step (dispatching the REAL injected meta-tools, so their tracing runs) and SUBMITs. This
is the layer the unit tests can't reach — it exercises `planner → list_servers → load_server →
describe_tools → call_tool → outcome → assemble → response` on a trace the real loop produced, and
regresses the meta-tool NAME pinning end-to-end (a wrong registered name would KeyError in the call step).
"""

from __future__ import annotations

import asyncio

from rlm_harness import RLMConfig, TraceRecorder, configure
from rlm_harness.testing import ScriptedInterpreter, call, scripted_lm, submit
from rlm_harness.trace import load_events

from toolscout.agent import SolveTask
from toolscout.assemble import outcome_from_events
from toolscout.catalog import demo_catalog
from toolscout.config import ToolscoutConfig
from toolscout.response import build_response
from toolscout.rubric import default_rubric, rubric_to_meta


def _run_scripted(tmp_path, steps, planner_turns, *, run_id="task", task="add six and seven",
                  max_iterations=30):
    """Drive one full scripted solve through the real loop and return (planner_outcome, events)."""
    configure(RLMConfig(main_model="x", sub_model="x", interpreter="mock", observe=False,
                        max_iterations=max_iterations),
              main_lm=scripted_lm(planner_turns), sub_lm=scripted_lm([{"reasoning": "r", "outcome": "{}"}]))
    cfg = ToolscoutConfig(main_model="x", sub_model="x", interpreter="mock", enable_skills=False,
                          max_iterations=max_iterations)
    solver = SolveTask(config=cfg, catalog=demo_catalog(), interpreter=ScriptedInterpreter(steps))
    rubric = default_rubric(task)
    path = str(tmp_path / f"{run_id}.jsonl")
    meta = {"task": task, "instructions": solver.instructions, "rubric": rubric_to_meta(rubric),
            "planner": "p", "specialist": "s", "judge": None, "max_iterations": max_iterations}

    async def _go():
        try:
            with TraceRecorder(path, run_id=run_id, meta=meta):
                return await solver.arun(task=task)
        finally:
            solver.close()

    return asyncio.run(_go()), load_events(path, run_id)


def test_full_isl_itl_ptc_flow_offline(tmp_path):
    """The whole chain through the real loop: list → load → describe → call → SUBMIT, then assemble.
    Proves the four meta-tools are registered under the names the planner uses (the __name__ pinning)."""
    outcome, events = _run_scripted(
        tmp_path,
        steps=[
            call("list_servers"),
            call("load_server", server="math"),
            call("describe_tools", names=["add"]),
            call("call_tool", server="math", tool="add", args={"a": 6, "b": 7}),
            submit({"outcome": {"answer": "13", "summary": "used math.add",
                                "servers_loaded": ["math"], "tools_used": ["add"]}}),
        ],
        planner_turns=[
            {"reasoning": "index", "code": "print(list_servers())"},
            {"reasoning": "load math", "code": "print(load_server('math'))"},
            {"reasoning": "describe add", "code": "print(describe_tools(['add']))"},
            {"reasoning": "compute", "code": "r = call_tool('math','add',{'a':6,'b':7})"},
            {"reasoning": "submit", "code": "SUBMIT(outcome=...)"},
        ])

    assert outcome.answer == "13"  # SUBMIT coerced into TaskOutcome
    # every meta-tool ran inside the loop, registered under the name the prompt uses
    tool_names = [e["payload"]["tool"] for e in events if e["type"] == "tool_call"]
    assert tool_names == ["list_servers", "load_server", "describe_tools", "call_tool"]

    a = outcome_from_events(events)
    assert a.servers_loaded == ["math"]        # re-sourced from the trace, not the SUBMIT
    assert a.tools_used == ["math:add"]
    assert a.unbacked_servers == [] and a.unbacked_tools == []
    resp = build_response(a, events, "task")
    assert resp.status == "ok" and resp.process.tool_calls == 1


def test_call_tool_before_load_is_recoverable_in_the_loop(tmp_path):
    """A call to an unloaded server returns a fixable error string (ISL gate) and the run still finishes —
    the error is data the planner recovers from, never a raise into the loop."""
    outcome, events = _run_scripted(
        tmp_path,
        steps=[
            call("call_tool", server="math", tool="add", args={"a": 1, "b": 2}),  # not loaded yet
            call("load_server", server="math"),
            call("call_tool", server="math", tool="add", args={"a": 1, "b": 2}),
            submit({"outcome": {"answer": "3", "servers_loaded": ["math"], "tools_used": ["add"]}}),
        ],
        planner_turns=[
            {"reasoning": "call early", "code": "call_tool('math','add',{'a':1,'b':2})"},
            {"reasoning": "load", "code": "load_server('math')"},
            {"reasoning": "retry", "code": "call_tool('math','add',{'a':1,'b':2})"},
            {"reasoning": "submit", "code": "SUBMIT(outcome=...)"},
        ])
    assert outcome.answer == "3"
    calls = [e["payload"] for e in events if e["payload"].get("tool") == "call_tool"]
    assert calls[0]["ok"] is False and calls[0]["reason"] == "not_loaded"
    assert calls[1]["ok"] is True and calls[1]["result"] == "3"


def test_sft_turns_adapter_render_reproduces_the_sent_requests(tmp_path):
    """rlm-harness >= 1.25 records `rlm_setup` + per-call fields, so `export_dataset(sft_render="adapter")`
    rebuilds each planner request with dspy's own adapter and checks it against the recorded hash.
    `TaskOutcome` is flat (no `$defs`), so it rebuilds with NO `custom_types` — `verified` must be True."""
    from toolscout.rl_export import export_dataset

    _, events = _run_scripted(
        tmp_path,
        steps=[call("list_servers"), submit({"outcome": {"answer": "13"}})],
        planner_turns=[{"reasoning": "index", "code": "print(list_servers())"},
                       {"reasoning": "submit", "code": "SUBMIT(outcome=...)"}])
    assert any(e["type"] == "rlm_setup" for e in events)
    recs = export_dataset({"task": events}, sft_render="adapter")["sft_turns"]
    assert [r["kind"] for r in recs] == ["action", "action"]
    assert all(r["verified"] is True and r["assistant_source"] == "completion" for r in recs)
    assert all([m["role"] for m in r["messages"]] == ["system", "user"] for r in recs)
    # the default shape is unchanged
    assert set(export_dataset({"task": events})["sft_turns"][0]) >= {"input", "output"}


def test_sft_turns_adapter_render_covers_the_extract_fallback(tmp_path):
    """When the iteration budget runs out, dspy's `extract` call fills the TaskOutcome; 1.25 records it
    as an `extract` event and the adapter export emits it as its own verified record."""
    from toolscout.rl_export import export_dataset

    outcome, events = _run_scripted(
        tmp_path,
        steps=[call("list_servers")],
        planner_turns=[{"reasoning": "index", "code": "print(list_servers())"},
                       {"reasoning": "out of budget",
                        "outcome": {"answer": "unknown", "summary": "budget hit"}}],
        max_iterations=1)
    assert outcome.answer == "unknown"
    assert any(e["type"] == "extract" for e in events)
    recs = export_dataset({"task": events}, sft_render="adapter")["sft_turns"]
    assert [r["kind"] for r in recs] == ["action", "extract"]
    assert all(r["verified"] is True for r in recs)
