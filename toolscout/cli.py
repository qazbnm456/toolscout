"""THE entry point: a task string in → a structured, grounded outcome over the toolspace out.

    toolscout solve "what is 6 * 7, then uppercase the word 'ok'?"   # run (needs TS_* creds + Deno)
    toolscout render traces/demo.jsonl demo                          # re-render a response from a trace
    toolscout export "traces/*.jsonl" ds.json                        # reward-free dataset export (offline)
    toolscout rubric "summarize the repo's open issues"              # host-side rubric decomposition

`run()` is the programmatic entry. It records `traces/{run_id}.jsonl`, writes `responses/{run_id}.json`,
and assembles the outcome from the trace on read (facts re-sourced, fabrication flagged). `render`/`export`
/`rubric` work offline; `solve` needs model creds (TS_* env) + a Deno sandbox (`brew install deno`).
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional

from . import __version__
from .config import ToolscoutConfig
from .schema import AssembledOutcome, TaskResponse


async def solve_task(
    task: str,
    config: ToolscoutConfig,
    *,
    trace_path: Optional[str] = None,
    run_id: str = "task",
    on_event: Optional[Callable[[dict], None]] = None,
    catalog=None,
    interpreter=None,
    rubric=None,
    extra_tools=(),
):
    """Run the planner on one task and return its JUDGEMENT (`TaskOutcome`). Call
    `assemble.assemble_outcome(outcome, events)` to attach the re-sourced facts + fabrication tells.

    `rubric` (a `RubricCriteria`) is carried in the trace as LABELS and drives the opt-in judge. Pass the
    ATLAS decomposition from `toolscout rubric` here so a live run's labels vary per task; None falls back
    to the deterministic `default_rubric` skeleton."""
    import rlm_kit

    from .agent import SolveTask, setup
    from .rubric import default_rubric, rubric_to_meta

    setup(config)
    rubric = rubric if rubric is not None else default_rubric(task)
    task_kw = {} if interpreter is None else {"interpreter": interpreter}
    solver = SolveTask(config=config, catalog=catalog, criteria=rubric.criteria,
                       extra_tools=extra_tools, **task_kw)

    async def _run():
        try:
            return await solver.arun(task=task)
        finally:
            solver.close()

    if trace_path:
        with rlm_kit.TraceRecorder(trace_path, run_id=run_id, on_event=on_event, meta={
            # The run's INITIAL STATE (the task is a REPL variable) + the prompt actually used.
            "task": task,
            "instructions": solver.instructions,
            # The rubric decomposition carried as LABELS (structure only — the trainer scores dᵢ).
            "rubric": rubric_to_meta(rubric),
            # The models actually used this run (roles → concrete names) — a self-describing trace.
            "planner": config.main_model,
            "specialist": config.sub_model,
            "judge": config.judge_model if config.enable_judge else None,
            # The budget THIS run ran under, so an offline reader computes hit_iteration_cap correctly.
            "max_iterations": config.max_iterations,
            "max_llm_calls": config.max_llm_calls,
            "toolspace": config.toolspace_path or "demo",
        }):
            return await _run()
    return await _run()


def _write(path_parts: tuple[str, str], content: str) -> str:
    d, name = path_parts
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _reset_trace(trace_path: str) -> None:
    """Drop any stale trace for this run_id (TraceRecorder appends; one trace per run)."""
    if os.path.exists(trace_path):
        os.remove(trace_path)


@dataclass
class RunArtifacts:
    outcome: Optional[AssembledOutcome]
    response: TaskResponse
    events: list
    run_id: str
    trace_path: str
    response_path: str
    status: str = "ok"


def run(
    task: str,
    *,
    run_id: str = "task",
    outdir: str = "./output",
    config: Optional[ToolscoutConfig] = None,
    on_event: Optional[Callable[[dict], None]] = None,
    catalog=None,
    rubric=None,
    extra_tools=(),
) -> RunArtifacts:
    """THE programmatic entry: solve one task, assemble on read, write the response. Never raises on a run
    failure — a failed run still writes an informative response and returns an outcome-less RunArtifacts.

    `rubric` (a `RubricCriteria`, e.g. from `toolscout rubric`) is carried as run labels; None → the
    deterministic default skeleton."""
    from rlm_kit.trace import load_events

    from .assemble import outcome_from_events
    from .response import build_failed_response, build_response

    config = config or ToolscoutConfig.from_env()
    trace_path = os.path.join(outdir, "traces", f"{run_id}.jsonl")
    _reset_trace(trace_path)

    try:
        asyncio.run(solve_task(task, config, trace_path=trace_path, run_id=run_id, on_event=on_event,
                               catalog=catalog, rubric=rubric, extra_tools=extra_tools))
    except Exception as exc:  # noqa: BLE001 — a failed run is still self-contained + navigable
        events = load_events(trace_path, run_id) if os.path.exists(trace_path) else []
        resp = build_failed_response(run_id, events, f"{type(exc).__name__}: {exc}", task=task)
        response_path = _write((os.path.join(outdir, "responses"), f"{run_id}.json"),
                               resp.model_dump_json(indent=2) + "\n")
        return RunArtifacts(None, resp, events, run_id, trace_path, response_path, status="failed")

    events = load_events(trace_path, run_id)
    assembled = outcome_from_events(events)
    resp = (build_response(assembled, events, run_id) if assembled is not None
            else build_failed_response(run_id, events, "the run finalized without a result event", task=task))
    response_path = _write((os.path.join(outdir, "responses"), f"{run_id}.json"),
                           resp.model_dump_json(indent=2) + "\n")
    return RunArtifacts(assembled, resp, events, run_id, trace_path, response_path, status=resp.status)


# ---- subcommands -------------------------------------------------------------

def _cmd_solve(args) -> int:
    from .render import render_response
    from .schema import RubricCriteria

    config = ToolscoutConfig.from_env()
    rubric = None
    if args.rubric:
        # A rubric JSON from `toolscout rubric <task> --out r.json` — its ATLAS decomposition then rides
        # in the run's trace as labels (instead of the generic default skeleton).
        with open(args.rubric, encoding="utf-8") as fh:
            rubric = RubricCriteria(**json.load(fh))
    arts = run(args.task, run_id=args.run_id, outdir=args.out, config=config, rubric=rubric)
    print(render_response(arts.response))
    print(f"\n→ {arts.response_path}")
    return 0 if arts.outcome is not None else 1


def _cmd_render(args) -> int:
    from rlm_kit.trace import load_events

    from .assemble import outcome_from_events
    from .render import render_response
    from .response import build_failed_response, build_response

    events = load_events(args.trace, args.run_id)
    assembled = outcome_from_events(events)
    resp = (build_response(assembled, events, args.run_id) if assembled is not None
            else build_failed_response(args.run_id, events, "trace has no result event"))
    out = resp.model_dump_json(indent=2) if args.json else render_response(resp)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print(f"wrote {args.out}")
    else:
        print(out)
    return 0


def _cmd_export(args) -> int:
    from .rl_export import export_dataset, load_runs

    paths = [p for g in args.trace for p in glob.glob(g)]
    runs = load_runs(*paths)
    bundle = export_dataset(runs)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False, indent=2, default=str)
    print(f"runs={len(runs)} | actions={len(bundle['actions'])} | sft_turns={len(bundle['sft_turns'])} "
          f"| reward-free → {args.out}")
    return 0


def _rubric_chat_fn():
    """A chat closure for host-side rubric generation when TS_RUBRIC_LM is set, else None (→ skeleton)."""
    model = os.getenv("TS_RUBRIC_LM", "")
    if not model:
        return None

    def _chat(prompt: str) -> str:
        from openai import OpenAI

        client = OpenAI(base_url=os.getenv("TS_RUBRIC_BASE_URL") or os.getenv("TS_BASE_URL"),
                        api_key=os.getenv("TS_RUBRIC_API_KEY") or os.getenv("TS_API_KEY") or "EMPTY",
                        max_retries=0, timeout=60.0)
        resp = client.chat.completions.create(
            model=model, temperature=0.0, max_tokens=2048,
            messages=[{"role": "user", "content": prompt}])
        return resp.choices[0].message.content or ""

    return _chat


def _rubric_for(task: str, chat_fn):
    """Generate a rubric for one task: the frontier model when configured (fall back to the deterministic
    skeleton on an empty/malformed reply), else the offline skeleton — so the command never yields nothing."""
    from .rubric import default_rubric, generate_rubric

    if chat_fn is not None:
        rubric = generate_rubric(task, chat_fn)
        if rubric.criteria:
            return rubric
    return default_rubric(task)


def _cmd_rubric(args) -> int:
    """Host-side rubric decomposition (dataset-prep). Deterministic default unless TS_RUBRIC_LM is set."""
    from .rubric import validate_rubric

    rubric = _rubric_for(args.task, _rubric_chat_fn())
    out = json.dumps(rubric.model_dump(), ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print(f"wrote {args.out} ({len(rubric.criteria)} criteria)")
    else:
        print(out)
    for issue in validate_rubric(rubric):  # a structural lint, non-fatal
        print(f"  ! rubric lint: {issue}")
    return 0


def _cmd_rubric_batch(args) -> int:
    """Generate a rubric per task in a taskset, offline once, for the rollout workflow: batch-generate here,
    then `solve --rubric <that task's rubric>`. Taskset JSON: a list of strings OR {"id","task"} objects."""
    from .rubric import validate_rubric

    with open(args.taskset, encoding="utf-8") as fh:
        raw = json.load(fh)
    tasks = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if isinstance(item, str):
            tid, task = f"task-{i}", item
        elif isinstance(item, dict) and item.get("task"):
            tid, task = str(item.get("id") or f"task-{i}"), str(item["task"])
        else:
            raise ValueError(f"taskset item {i} must be a string or an object with a 'task' field")
        # The id becomes a filename under out_dir — sanitize so an id like '../x' or 'a/b' can't escape it,
        # and reject a post-sanitize collision instead of silently overwriting another entry's rubric (symmetry
        # with eval's load_taskset, which rejects duplicate ids for the same reason).
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", tid).lstrip(".") or f"task-{i}"
        if safe in seen:
            raise ValueError(f"duplicate rubric id {safe!r} (from {tid!r}) — ids must be unique after sanitizing")
        seen.add(safe)
        tasks.append((safe, task))
    chat_fn = _rubric_chat_fn()
    os.makedirs(args.out_dir, exist_ok=True)
    total_issues = 0
    for tid, task in tasks:
        rubric = _rubric_for(task, chat_fn)
        issues = validate_rubric(rubric)
        total_issues += len(issues)
        path = os.path.join(args.out_dir, f"{tid}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rubric.model_dump(), fh, ensure_ascii=False, indent=2)
        flag = f"  ⚠ {len(issues)} lint issue(s)" if issues else ""
        print(f"  {tid}: {len(rubric.criteria)} criteria → {path}{flag}")
    print(f"wrote {len(tasks)} rubric(s) to {args.out_dir}"
          + (f" ({total_issues} lint issue(s) total)" if total_issues else ""))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="toolscout", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("solve", help="solve a task over the toolspace (needs TS_* creds + Deno)")
    s.add_argument("task", help="the task to solve, as a string")
    s.add_argument("--run-id", default="task")
    s.add_argument("--out", default="./output")
    s.add_argument("--rubric", default=None,
                   help="path to a rubric JSON (from `toolscout rubric ... --out`); carried as run labels")
    s.set_defaults(func=_cmd_solve)

    r = sub.add_parser("render", help="re-render the response from a trace (offline)")
    r.add_argument("trace")
    r.add_argument("run_id")
    r.add_argument("--out", default=None)
    r.add_argument("--json", action="store_true", help="emit the JSON response instead of text")
    r.set_defaults(func=_cmd_render)

    e = sub.add_parser("export", help="export reward-free SFT/RL datasets from traces (offline)")
    e.add_argument("trace", nargs="+", help="trace file glob(s)")
    e.add_argument("out", help="output json path")
    e.set_defaults(func=_cmd_export)

    rb = sub.add_parser("rubric", help="decompose a task into a grading rubric (host-side, dataset-prep)")
    rb.add_argument("task", help="the task to decompose")
    rb.add_argument("--out", default=None)
    rb.set_defaults(func=_cmd_rubric)

    rbb = sub.add_parser("rubric-batch",
                         help="decompose every task in a taskset into a per-task rubric (dataset-prep)")
    rbb.add_argument("taskset", help="JSON list of task strings or {id, task} objects")
    rbb.add_argument("out_dir", help="directory to write <id>.json rubrics into")
    rbb.set_defaults(func=_cmd_rubric_batch)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
