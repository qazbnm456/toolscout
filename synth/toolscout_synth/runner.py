"""Run ONE synth task through toolscout and check it: the per-task entry a downstream driver calls.

    from toolscout_synth import load_taskset, opaque_run_id, run_task
    for task in load_taskset(data_dir):
        res = run_task(task, data_dir=data_dir, outdir="out/b30", run_id=opaque_run_id(task, "b30"))
        res.check.passed, res.check.reason, res.artifacts.trace_path

It goes through `toolscout.cli.run` (THE programmatic entry: it records the trace, assembles on read,
writes the response, and never raises on a failed run) with the synth catalog injected. Only the task TEXT
is passed in: the gold answer and params never reach toolscout, so they never land in the trace.

The run id IS written into every trace event, the response file and the exported dataset, so it is
OPAQUE by default (`opaque_run_id`): a readable id like `unsupported_lookup-L1-003` would label the
template, the decline tasks and the held-out split inside the data a trainer consumes. Keep the
run id → task id mapping on the driver side.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .catalog import build_catalog
from .check import CheckResult, check_outcome


def opaque_run_id(task: dict, suffix: str = "") -> str:
    """A stable run id that reveals nothing about the task: `syn-` + 12 hex chars of sha256(id, suffix).
    Use the suffix to separate runs of one task (a budget, a sample index)."""
    return "syn-" + hashlib.sha256(f"{task['id']}|{suffix}".encode()).hexdigest()[:12]


@dataclass
class SynthRun:
    task_id: str
    run_id: str
    artifacts: Any          # toolscout.cli.RunArtifacts
    check: CheckResult


def run_task(task: dict, *, data_dir: str | None = None, outdir: str = "./output", run_id: str | None = None,
             config: Any = None, distractors: bool = True) -> SynthRun:
    """Solve `task["task"]` over the synth toolspace and check the outcome against `task["gold"]`.

    `config` is a `toolscout.config.ToolscoutConfig` (default: `ToolscoutConfig.from_env()`, i.e. the TS_*
    env); set its `max_iterations` per budget. `run_id` defaults to `opaque_run_id(task)`."""
    from toolscout.cli import run

    run_id = run_id or opaque_run_id(task)
    artifacts = run(task["task"], run_id=run_id, outdir=outdir, config=config,
                    catalog=build_catalog(data_dir, distractors=distractors))
    return SynthRun(task["id"], run_id, artifacts, check_outcome(task, artifacts.outcome))
