"""`toolscout-eval` — score toolscout runs against the ATLAS 4-category judge (a terminal scorecard).

    toolscout-eval score "output/traces/*.jsonl" demo            # score EXISTING traces (offline-capable)
    toolscout-eval run demo                                      # drive toolscout per task, then score

`score` needs judge creds at most (none with the stub); `run` additionally needs toolscout's full solve
stack (TS_* creds + Deno), imported lazily so `score` never pulls it. The taskset argument is a JSON
path or the literal `demo` (the built-in offline set). Runs pair to tasks by run_id == task id.

The judge: live iff TSEVAL_MODEL is set (TSEVAL_BASE_URL/API_KEY/TIMEOUT alongside), else the
deterministic stub; `--stub` forces the stub. Everything is written under --out (default ./output/eval)
— never into output/traces/ or a dataset export. The report is a measurement, never a reward.
"""

from __future__ import annotations

import argparse
import glob
import os

from . import __version__
from .judge import PROMPT_VERSION, EvalJudgeConfig, make_eval_judge, stub_judge
from .schema import EvalReport
from .score import aggregate, score_run
from .taskset import EvalTask, demo_taskset, load_taskset


def _load_tasks(spec: str) -> list[EvalTask]:
    return demo_taskset() if spec == "demo" else load_taskset(spec)


def _pick_judge(force_stub: bool):
    """The live judge iff TSEVAL_MODEL is configured (and not --stub), else the offline stub."""
    config = EvalJudgeConfig.from_env()
    if force_stub or not config.model:
        return stub_judge, "stub", ""
    return make_eval_judge(config), config.model, PROMPT_VERSION


def _write_report(report: EvalReport, outdir: str) -> str:
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "report.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report.model_dump_json(indent=2) + "\n")
    return path


def render_report(report: EvalReport) -> str:
    """The mono scorecard: one row per task, per-category means in the footer. TF is the primary column."""
    header = f"{'task':<24} {'TF':>5} {'TA':>5} {'TG':>5} {'PA':>5}  {'turns':>5} {'calls':>5} {'tells':>5}"
    lines = [header, "-" * len(header)]
    for row in report.rows:
        if row.unscored or row.score is None:
            lines.append(f"{row.task_id:<24} {'--':>5} {'--':>5} {'--':>5} {'--':>5}  "
                         f"unscored: {row.unscored_reason}")
            continue
        s = row.score
        calls = int(row.metrics.get("call_ok", 0)) + int(row.metrics.get("call_fail", 0))
        lines.append(f"{row.task_id:<24} {s.TF:>5.1f} {s.TA:>5.1f} {s.TG:>5.1f} {s.PA:>5.1f}  "
                     f"{int(row.metrics.get('turns', 0)):>5} {calls:>5} {row.fabrication_tells:>5}")
    lines.append("-" * len(header))
    m = report.means
    if m:
        lines.append(f"{'MEAN':<24} {m.get('TF', 0):>5.1f} {m.get('TA', 0):>5.1f} "
                     f"{m.get('TG', 0):>5.1f} {m.get('PA', 0):>5.1f}   (primary: {report.primary})")
    lines.append(f"n={report.n} ({report.n_unscored} unscored)  judge={report.judge_model or '?'}"
                 + (f"  prompt={report.prompt_version}" if report.prompt_version else ""))
    return "\n".join(lines)


def _score_and_emit(rows, *, taskset: str, judge_model: str, prompt_version: str, outdir: str) -> int:
    report = aggregate(rows, taskset=taskset, judge_model=judge_model, prompt_version=prompt_version)
    path = _write_report(report, outdir)
    print(render_report(report))
    print(f"\n-> {path}")
    # A batch where NOTHING scored (no rows, or a dead judge / all-off-schema → every row unscored) is not
    # a green run — exit non-zero so a CI gate keying on the exit code doesn't read an empty scorecard as pass.
    if not rows or report.n_unscored == report.n:
        return 1
    return 0


def _cmd_score(args) -> int:
    """Score EXISTING traces: glob -> load_events -> group_by_run -> pair by run_id == task id -> judge."""
    from rlm_kit.trace import group_by_run, load_events

    tasks = {t.id: t for t in _load_tasks(args.taskset)}
    paths = sorted(glob.glob(args.traces))
    if not paths:
        print(f"no trace files match {args.traces!r}")
        return 1
    runs: dict[str, list[dict]] = {}
    for path in paths:
        for run_id, events in group_by_run(load_events(path)).items():
            runs[str(run_id)] = events
    judge, judge_model, prompt_version = _pick_judge(args.stub)
    rows, skipped = [], []
    for run_id in sorted(runs):
        task = tasks.get(run_id)
        if task is None:
            skipped.append(run_id)
            continue
        rows.append(score_run(runs[run_id], task, judge))
    if skipped:
        print(f"(skipped {len(skipped)} run(s) with no matching task id: {', '.join(skipped)})")
    return _score_and_emit(rows, taskset=args.taskset, judge_model=judge_model,
                           prompt_version=prompt_version, outdir=args.out)


def _cmd_run(args) -> int:
    """Run-then-score: drive `toolscout.run` per task (run_id = task id), then score the fresh traces.

    toolscout is imported LAZILY here — this is the only mode that pulls the solve stack (dspy, Deno,
    TS_* creds). Its artifacts (traces/responses) land under --out too, so everything this command
    writes stays inside output/eval/."""
    from toolscout.cli import run as toolscout_run

    tasks = _load_tasks(args.taskset)
    judge, judge_model, prompt_version = _pick_judge(args.stub)
    rows = []
    for task in tasks:
        artifacts = toolscout_run(task.task, run_id=task.id, outdir=args.out)
        rows.append(score_run(artifacts.events, task, judge))
    return _score_and_emit(rows, taskset=args.taskset, judge_model=judge_model,
                           prompt_version=prompt_version, outdir=args.out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toolscout-eval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("score", help="score existing traces against a taskset (offline with the stub judge)")
    s.add_argument("traces", help="trace file glob, e.g. 'output/traces/*.jsonl'")
    s.add_argument("taskset", help="taskset JSON path, or 'demo' for the built-in offline set")
    s.add_argument("--out", default="./output/eval", help="report directory (default ./output/eval)")
    s.add_argument("--stub", action="store_true", help="force the deterministic stub judge")
    s.set_defaults(func=_cmd_score)

    r = sub.add_parser("run", help="drive toolscout per task, then score (needs TS_* creds + Deno)")
    r.add_argument("taskset", help="taskset JSON path, or 'demo' for the built-in offline set")
    r.add_argument("--out", default="./output/eval", help="output directory for runs + report")
    r.add_argument("--stub", action="store_true", help="force the deterministic stub judge")
    r.set_defaults(func=_cmd_run)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
