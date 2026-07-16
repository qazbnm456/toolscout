"""The CLI: `score` over a tmp trace with the stub judge; `run`'s lazy-toolscout wiring, offline."""

from __future__ import annotations

import json
from types import SimpleNamespace

from conftest import math_calls, record_run

from toolscout_eval.cli import main


def test_score_writes_report_over_tmp_trace(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TSEVAL_MODEL", raising=False)      # no live judge configured -> the stub
    record_run(tmp_path, math_calls(), outcome={"answer": "42"}, run_id="demo-math")
    out = tmp_path / "eval"
    code = main(["score", str(tmp_path / "*.jsonl"), "demo", "--out", str(out)])
    assert code == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["n"] == 1 and report["n_unscored"] == 0
    assert report["judge_model"] == "stub" and report["primary"] == "TF"
    assert report["means"] == {"TF": 5.0, "TA": 5.0, "TG": 5.0, "PA": 5.0}
    assert report["rows"][0]["task_id"] == "demo-math"
    assert "reward" not in json.dumps(report).lower()
    printed = capsys.readouterr().out
    assert "demo-math" in printed and "MEAN" in printed    # the terminal scorecard rendered


def test_score_skips_runs_with_no_matching_task(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TSEVAL_MODEL", raising=False)
    record_run(tmp_path, math_calls(), outcome={"answer": "42"}, run_id="not-in-taskset")
    code = main(["score", str(tmp_path / "*.jsonl"), "demo", "--out", str(tmp_path / "eval")])
    assert code == 1                                       # nothing scored
    assert "skipped 1 run(s)" in capsys.readouterr().out


def test_score_handles_no_trace_files(tmp_path, monkeypatch):
    monkeypatch.delenv("TSEVAL_MODEL", raising=False)
    code = main(["score", str(tmp_path / "nothing-*.jsonl"), "demo", "--out", str(tmp_path / "eval")])
    assert code == 1


def test_stub_flag_forces_offline_judge_even_with_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TSEVAL_MODEL", "some-live-judge")
    record_run(tmp_path, math_calls(), outcome={"answer": "42"}, run_id="demo-math")
    out = tmp_path / "eval"
    code = main(["score", str(tmp_path / "*.jsonl"), "demo", "--out", str(out), "--stub"])
    assert code == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["judge_model"] == "stub"                 # the env judge was never touched


def test_run_drives_toolscout_lazily_then_scores(tmp_path, monkeypatch):
    """`run` wiring, offline: `toolscout.cli.run` is monkeypatched (the in-function lazy import
    resolves the patched attribute at call time) to replay a recorded demo trace per task."""
    monkeypatch.delenv("TSEVAL_MODEL", raising=False)
    import toolscout.cli as toolscout_cli

    driven: list = []

    def fake_run(task, *, run_id, outdir, **kwargs):
        driven.append((task, run_id, outdir))
        events = record_run(tmp_path, math_calls(), outcome={"answer": "42"}, run_id=run_id)
        return SimpleNamespace(events=events)

    monkeypatch.setattr(toolscout_cli, "run", fake_run)
    out = tmp_path / "eval"
    code = main(["run", "demo", "--out", str(out)])
    assert code == 0
    assert len(driven) == 4                                # one solve per demo task, run_id = task id
    assert all(outdir == str(out) for _, _, outdir in driven)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["n"] == 4 and report["n_unscored"] == 0
