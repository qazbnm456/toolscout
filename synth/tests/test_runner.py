"""run_task drives toolscout.cli.run with ONLY the task text + the synth catalog, then checks."""

from types import SimpleNamespace

from toolscout_synth import opaque_run_id, run_task

import toolscout.cli
from toolscout.catalog import StaticCatalog


def test_run_task_passes_only_the_task_text_and_checks(monkeypatch, taskset):
    task = next(t for t in taskset if t["gold_kind"] == "id")
    seen = {}

    def fake_run(text, **kw):
        seen.update(kw, text=text)
        return SimpleNamespace(outcome=SimpleNamespace(answer=task["gold"], cannot_complete=False))

    monkeypatch.setattr(toolscout.cli, "run", fake_run)
    res = run_task(task, outdir="o", run_id="x-b15", config="CFG")
    assert res.check.passed and res.task_id == task["id"]
    assert seen["text"] == task["task"] and seen["run_id"] == "x-b15" and seen["config"] == "CFG"
    assert isinstance(seen["catalog"], StaticCatalog)
    assert set(seen) == {"text", "run_id", "outdir", "config", "catalog"}   # no gold, no params


def test_a_failed_run_is_a_checked_failure(monkeypatch, taskset):
    task = taskset[0]
    monkeypatch.setattr(toolscout.cli, "run", lambda text, **kw: SimpleNamespace(outcome=None))
    assert run_task(task).check.reason == "no_outcome"


def test_default_run_id_is_opaque_and_stable(monkeypatch, taskset):
    """The run id lands in every trace event and the exported dataset, so it must not name the template
    (which would label decline tasks and the held-out split in training data)."""
    seen = {}
    monkeypatch.setattr(toolscout.cli, "run",
                        lambda text, **kw: seen.update(kw) or SimpleNamespace(outcome=None))
    for task in taskset:
        res = run_task(task)
        assert seen["run_id"] == res.run_id == opaque_run_id(task)
        assert task["template"] not in res.run_id and not any(c.isupper() for c in res.run_id)
    assert opaque_run_id(taskset[0], "b15") != opaque_run_id(taskset[0], "b30")
    assert len({opaque_run_id(t) for t in taskset}) == len(taskset)
