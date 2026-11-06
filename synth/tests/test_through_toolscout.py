"""The gold answers are reachable through toolscout's REAL toolspace, inside the REAL RLM loop.

1. For EVERY task, the reference solver re-run through toolscout's `call_tool` meta-tool (after ISL
   `load_server`) reproduces the committed gold, and touches only the task's `servers_required`.
2. For one task per template, a scripted planner runs the full dspy.RLM loop (rlm-harness's offline
   `ScriptedInterpreter` + `DummyLM`; no model, no Deno, no network): list → load → describe → one REPL step
   that loops the fetches and computes → SUBMIT. It finishes in ≤ 12 steps, `check()` passes on the
   ASSEMBLED outcome, and the gold never appears in the trace's `run_start` meta.
"""

import asyncio
import json

import pytest
from toolscout_synth import TEMPLATES, build_catalog, check_outcome, render_gold

from toolscout.config import ToolscoutConfig
from toolscout.toolspace import Toolspace, build_toolspace_tools


def _meta_tools(catalog):
    ts = Toolspace(catalog, ToolscoutConfig(main_model="x", sub_model="y"))
    return {f.__name__: f for f in build_toolspace_tools(ts)}


def test_every_gold_reproduces_through_the_meta_tools(taskset):
    catalog = build_catalog()
    for t in taskset:
        tools = _meta_tools(catalog)          # fresh toolspace per task: nothing loaded, no repeat budget
        for s in t["servers_required"]:
            assert tools["load_server"](s).startswith("Loaded")
        touched = set()

        def call(server, tool, args, _touched=touched, _tools=tools):
            _touched.add(f"{server}:{tool}")
            out = _tools["call_tool"](server, tool, args)
            assert not isinstance(out, str) or not out.startswith(("tool ", "argument error", "Server ")), out
            return out

        got = TEMPLATES[t["template"]].solve(call, t["params"])
        assert json.loads(json.dumps(got)) == t["gold"], t["id"]
        assert touched <= set(t["tools_required"]), (t["id"], touched)
        assert {x.split(":")[0] for x in touched} <= set(t["servers_required"])


def _scripted_run(tmp_path, task, max_iterations=15):
    from rlm_harness import RLMConfig, TraceRecorder, configure
    from rlm_harness.testing import ScriptedInterpreter, call, scripted_lm, submit
    from rlm_harness.trace import load_events

    from toolscout.agent import SolveTask
    from toolscout.rubric import default_rubric, rubric_to_meta

    tpl = TEMPLATES[task["template"]]
    servers = list(tpl.servers) or [task["params"]["server"]]
    tool_names = [x.split(":")[1] for x in tpl.tools]

    def compute(tools, _variables):
        result = tpl.solve(lambda s, n, a: tools["call_tool"](server=s, tool=n, args=a), task["params"])
        return repr(result)

    steps = [call("list_servers")] + [call("load_server", server=s) for s in servers]
    turns = [{"reasoning": "index", "code": "print(list_servers())"}]
    turns += [{"reasoning": f"load {s}", "code": f"print(load_server({s!r}))"} for s in servers]
    if tool_names:
        steps.append(call("describe_tools", names=tool_names))
        turns.append({"reasoning": "signatures", "code": f"print(describe_tools({tool_names!r}))"})
        steps.append(compute)
        turns.append({"reasoning": "page through everything and compute in the REPL",
                      "code": "# loop call_tool over every page, filter/join/aggregate"})
    declined = task["gold_kind"] == "cannot_complete"
    steps.append(submit({"outcome": {
        "answer": render_gold(task), "summary": "computed over paginated tool results",
        "servers_loaded": list(tpl.servers), "tools_used": tool_names, "cannot_complete": declined}}))
    turns.append({"reasoning": "submit", "code": "SUBMIT(outcome=...)"})

    configure(RLMConfig(main_model="x", sub_model="x", interpreter="mock", observe=False,
                        max_iterations=max_iterations),
              main_lm=scripted_lm(turns), sub_lm=scripted_lm([{"reasoning": "r", "outcome": "{}"}]))
    cfg = ToolscoutConfig(main_model="x", sub_model="x", interpreter="mock", enable_skills=False,
                          max_iterations=max_iterations)
    solver = SolveTask(config=cfg, catalog=build_catalog(), interpreter=ScriptedInterpreter(steps))
    path = str(tmp_path / f"{task['id']}.jsonl")
    meta = {"task": task["task"], "instructions": solver.instructions,
            "rubric": rubric_to_meta(default_rubric(task["task"])), "max_iterations": max_iterations}

    async def go():
        try:
            with TraceRecorder(path, run_id=task["id"], meta=meta):
                return await solver.arun(task=task["task"])
        finally:
            solver.close()

    asyncio.run(go())
    return len(steps), load_events(path, task["id"])


@pytest.mark.parametrize("template", sorted(TEMPLATES))
def test_reference_trajectory_through_the_rlm_loop(tmp_path, taskset, template):
    from toolscout.assemble import outcome_from_events
    from toolscout.response import build_response

    task = next(t for t in taskset if t["template"] == template and t["level"] == max(TEMPLATES[template].levels))
    n_steps, events = _scripted_run(tmp_path, task)
    assert n_steps <= 12
    assembled = outcome_from_events(events)
    res = check_outcome(task, assembled)
    assert res.passed, res
    assert assembled.unbacked_servers == [] and assembled.unbacked_tools == []
    ok_calls = {f"{e['payload']['server']}:{e['payload']['args']['tool']}" for e in events
                if e["type"] == "tool_call" and e["payload"].get("tool") == "call_tool" and e["payload"]["ok"]}
    assert ok_calls == set(task["tools_required"])
    start = next(e for e in events if e["type"] == "run_start")
    assert json.dumps(task["gold"]) not in json.dumps(start["payload"])
    status = build_response(assembled, events, task["id"]).status
    assert status == ("refused" if task["gold_kind"] == "cannot_complete" else "ok")
