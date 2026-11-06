"""Run ONE synth task through toolscout and check it: the per-task entry a downstream driver calls.

    from toolscout_synth import load_taskset, run_task
    for task in load_taskset(data_dir):
        res = run_task(task, data_dir=data_dir, outdir="out/b30", run_id=f"{task['id']}-b30")
        res.check.passed, res.check.reason, res.artifacts.trace_path

It goes through `toolscout.cli.run` (THE programmatic entry: it records the trace, assembles on read,
writes the response, and never raises on a failed run) with the synth catalog injected. Only the task TEXT
is passed in: the gold answer and params never reach toolscout, so they never land in the trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog import build_catalog
from .check import CheckResult, check_outcome


@dataclass
class SynthRun:
    task_id: str
    artifacts: Any          # toolscout.cli.RunArtifacts
    check: CheckResult


def run_task(task: dict, *, data_dir: str | None = None, outdir: str = "./output", run_id: str | None = None,
             config: Any = None, distractors: bool = True) -> SynthRun:
    """Solve `task["task"]` over the synth toolspace and check the outcome against `task["gold"]`.

    `config` is a `toolscout.config.ToolscoutConfig` (default: `ToolscoutConfig.from_env()`, i.e. the TS_*
    env); set its `max_iterations` per budget."""
    from toolscout.cli import run

    artifacts = run(task["task"], run_id=run_id or task["id"], outdir=outdir, config=config,
                    catalog=build_catalog(data_dir, distractors=distractors))
    return SynthRun(task["id"], artifacts, check_outcome(task, artifacts.outcome))
