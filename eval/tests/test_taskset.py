"""Tasksets: the built-in demo set over the demo catalog, and the JSON loader's validation."""

from __future__ import annotations

import json

import pytest

from toolscout_eval.taskset import EvalTask, demo_taskset, load_taskset


def test_demo_taskset_covers_the_demo_servers():
    tasks = demo_taskset()
    ids = [t.id for t in tasks]
    assert len(ids) == len(set(ids)) and len(tasks) >= 4
    assert all(t.task and t.reference for t in tasks)     # every task carries a judge-only reference
    references = " ".join(t.reference for t in tasks)
    for server in ("echo", "math", "memory", "text"):     # the demo_catalog servers
        assert server in references


def test_load_taskset_round_trip(tmp_path):
    path = tmp_path / "taskset.json"
    path.write_text(json.dumps([{"id": "a", "task": "do a", "reference": "did a"},
                                {"id": "b", "task": "do b"}]), encoding="utf-8")
    tasks = load_taskset(str(path))
    assert tasks == [EvalTask(id="a", task="do a", reference="did a"),
                     EvalTask(id="b", task="do b")]


def test_load_taskset_accepts_a_tasks_wrapper(tmp_path):
    path = tmp_path / "taskset.json"
    path.write_text(json.dumps({"tasks": [{"id": "a", "task": "do a"}]}), encoding="utf-8")
    assert load_taskset(str(path)) == [EvalTask(id="a", task="do a")]


def test_load_taskset_rejects_malformed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"task": "no id"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_taskset(str(path))
    path.write_text(json.dumps([{"id": "a", "task": "x"}, {"id": "a", "task": "y"}]), encoding="utf-8")
    with pytest.raises(ValueError):                        # duplicate ids break run pairing
        load_taskset(str(path))
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_taskset(str(path))
