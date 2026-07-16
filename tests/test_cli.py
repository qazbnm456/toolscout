"""The CLI — parser wiring + the OFFLINE subcommands (render / export / rubric)."""

from __future__ import annotations

import json

from toolscout.cli import build_parser, main

from tests.conftest import run_recorded


def test_parser_has_all_subcommands():
    p = build_parser()
    for cmd in ("solve", "render", "export", "rubric"):
        assert p.parse_args([cmd, "x"] if cmd in ("solve", "rubric") else
                            [cmd, "a", "b"] if cmd == "render" else [cmd, "g", "o"])


def test_render_offline_from_trace(tmp_path, capsys):
    events = run_recorded(tmp_path, [
        ("load_server", {"server": "math"}),
        ("call_tool", {"server": "math", "tool": "add", "args": {"a": 6, "b": 7}}),
    ], outcome={"answer": "13"}, run_id="demo")
    assert events  # written to tmp/demo.jsonl by run_recorded
    rc = main(["render", str(tmp_path / "demo.jsonl"), "demo", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok" and payload["outcome"]["answer"] == "13"


def test_export_offline(tmp_path, capsys):
    run_recorded(tmp_path, [
        ("load_server", {"server": "math"}),
        ("call_tool", {"server": "math", "tool": "add", "args": {"a": 6, "b": 7}}),
    ], outcome={"answer": "13"}, run_id="demo")
    out = str(tmp_path / "ds.json")
    rc = main(["export", str(tmp_path / "demo.jsonl"), out])
    assert rc == 0
    bundle = json.loads(open(out).read())
    assert "actions" in bundle and all(a.get("reward") is None for a in bundle["actions"])
    assert "reward-free" in capsys.readouterr().out


def test_rubric_offline_default(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TS_RUBRIC_LM", raising=False)
    rc = main(["rubric", "summarize the open issues", "--out", str(tmp_path / "r.json")])
    assert rc == 0
    rubric = json.loads((tmp_path / "r.json").read_text())
    cats = {c["category"] for c in rubric["criteria"]}
    assert cats == {"TF", "TA", "TG", "PA"}


def test_rubric_batch_offline(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TS_RUBRIC_LM", raising=False)
    taskset = tmp_path / "tasks.json"
    taskset.write_text(json.dumps(["find the CVE severity", {"id": "search", "task": "search vulns"}]))
    out_dir = tmp_path / "rubrics"
    rc = main(["rubric-batch", str(taskset), str(out_dir)])
    assert rc == 0
    assert (out_dir / "task-0.json").exists() and (out_dir / "search.json").exists()
    r = json.loads((out_dir / "search.json").read_text())
    assert {c["category"] for c in r["criteria"]} == {"TF", "TA", "TG", "PA"}
