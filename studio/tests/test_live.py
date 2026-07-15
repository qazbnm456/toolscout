"""The live-run driver — verified with `cli.run` STUBBED, so it needs no LLM, dspy, Deno, or toolscout.
The action stream comes from the recorder's `on_event` (no dspy callback — toolscout's cli.run has none),
so these tests exercise the sink mapping and the finalize path."""

import json
import types
from pathlib import Path

import pytest

from toolscout_studio.live import (
    _describe_exc,
    _failed_dict,
    run_live,
    trace_event_sink,
)


# ---- trace_event_sink: the sandbox-invoked tools + specialist, from the recorder's on_event ----

def test_trace_event_sink_maps_tools_and_sub_calls_and_skips_the_rest():
    sunk = []
    on_event = trace_event_sink(sunk.append)
    on_event({"type": "tool_call", "payload": {
        "tool": "load_server", "args": {"server": "math"}, "server": "math", "ok": True,
        "tool_names": ["add"]}})
    on_event({"type": "tool_call", "payload": {
        "tool": "call_tool", "server": "math", "args": {"tool": "add", "args": {"a": 1, "b": 2}},
        "ok": True, "result": "3"}})
    on_event({"type": "sub_call", "payload": {"input": "q", "processed": "a"}})
    on_event({"type": "main_step", "payload": {"reasoning": "r", "code": "c"}})   # SKIP — post-hoc burst
    on_event({"type": "run_start", "payload": {}})                               # SKIP — endpoint owns created
    assert [e["event"] for e in sunk] == [
        "task.server.loaded", "task.tool.called", "task.specialist.escalation"]
    assert sunk[0]["data"]["server"] == "math" and sunk[1]["data"]["tool"] == "add"


# ---- run_live: drive cli.run, stream the action events, prefer the durable response ----

def test_run_live_streams_actions_then_prefers_the_response_file(tmp_path):
    sunk, final = [], {}
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "r1.json").write_text(json.dumps({"status": "ok", "id": "r1"}))

    def cli_run(task, *, run_id="task", outdir="./output", config=None, on_event=None,
                catalog=None, extra_tools=()):
        # the tools stream via on_event exactly as the recorder would
        assert task == "what is 6*7?"
        on_event({"type": "tool_call", "payload": {
            "tool": "load_server", "args": {"server": "math"}, "server": "math", "ok": True,
            "tool_names": ["add"]}})
        on_event({"type": "sub_call", "payload": {"input": "q", "processed": "a"}})
        return types.SimpleNamespace(response_path=str(Path(outdir) / "responses" / f"{run_id}.json"))

    run_live({"task": "what is 6*7?"}, "r1", sunk.append, final.update,
             artifacts_dir=tmp_path, cli_run=cli_run)
    assert [e["event"] for e in sunk] == ["task.server.loaded", "task.specialist.escalation"]
    assert final == {"status": "ok", "id": "r1"}     # the on-disk artifact wins


def test_run_live_calls_cli_run_with_the_real_run_signature(tmp_path):
    # REGRESSION guard for the zero-harness-change promise: this fake MIRRORS toolscout's real
    # `run(task, *, run_id, outdir, config, on_event, catalog, extra_tools)` EXACTLY. If run_live ever
    # passed an unexpected kwarg (callbacks=, emit=), the call would TypeError → a failed response, and
    # this test would fail.
    final = {}

    def cli_run(task, *, run_id="task", outdir="./output", config=None, on_event=None,
                catalog=None, extra_tools=()):
        (Path(outdir) / "responses").mkdir(parents=True, exist_ok=True)
        (Path(outdir) / "responses" / f"{run_id}.json").write_text(json.dumps({"status": "ok", "id": run_id}))
        return types.SimpleNamespace(response_path=str(Path(outdir) / "responses" / f"{run_id}.json"))

    run_live({"task": "t"}, "r2", lambda e: None, final.update, artifacts_dir=tmp_path, cli_run=cli_run)
    assert final == {"status": "ok", "id": "r2"}     # no TypeError → the call shape is valid


def test_run_live_failure_becomes_an_informative_failed_response(tmp_path):
    final = {}

    def cli_run(task, **kw):
        raise RuntimeError("boom in the run")

    run_live({"task": "t"}, "r", lambda e: None, final.update, artifacts_dir=tmp_path, cli_run=cli_run,
             build_failed_response=lambda run_id, events, detail: types.SimpleNamespace(
                 model_dump=lambda: {"status": "failed", "detail": detail}))
    assert final["status"] == "failed" and "boom in the run" in final["detail"]


def test_run_live_missing_live_extra_completes_as_failed_not_hang():
    # server started WITHOUT the `live` extra → `from toolscout.cli import run` raises in the worker.
    # on_done MUST still fire (else the SSE hangs forever). cli_run defaults to None → the real import,
    # absent in a replay-only env.
    import importlib.util
    if importlib.util.find_spec("toolscout") is not None:
        pytest.skip("toolscout present (live extra) — this repro requires it ABSENT")
    final = {}
    run_live({"task": "t"}, "r", lambda e: None, final.update)
    assert final["status"] == "failed" and "toolscout" in final["error"]


def test_describe_exc_surfaces_the_underlying_cause():
    try:
        try:
            raise RuntimeError("BadGatewayError: all channels failed")
        except RuntimeError as cause:
            raise ValueError("Failed to produce a valid 'result' after 1 attempts") from cause
    except ValueError as exc:
        d = _describe_exc(exc)
    assert "Failed to produce a valid 'result'" in d and "caused by RuntimeError" in d and "BadGateway" in d


def test_describe_exc_without_a_cause_is_just_the_error():
    assert _describe_exc(ValueError("boom")) == "ValueError: boom"


def test_failed_dict_prefers_build_failed_response(tmp_path):
    # build_failed_response is injected here to stay import-light; run_live defaults it to toolscout's.
    d = _failed_dict("r", "detail here", lambda run_id, events, detail: types.SimpleNamespace(
        model_dump=lambda: {"status": "failed", "detail": detail}))
    assert d == {"status": "failed", "detail": "detail here"}


def test_failed_dict_minimal_literal_shape():
    # force the except branch by handing a bfr that raises (simulating toolscout absent)
    def boom(*a, **k):
        raise ModuleNotFoundError("No module named 'toolscout'")
    d = _failed_dict("r", "the detail", boom)
    assert d["status"] == "failed" and d["object"] == "toolscout.task_response"
    assert d["outcome"] is None and d["refusal"]["reason"] == "the detail" and d["error"] == "the detail"


def test_run_live_disables_litellm_aiohttp_transport(tmp_path):
    litellm = pytest.importorskip("litellm")
    litellm.disable_aiohttp_transport = False
    run_live({"task": "t"}, "r", lambda e: None, lambda f: None, artifacts_dir=tmp_path,
             cli_run=lambda task, **k: types.SimpleNamespace(response_path=None),
             build_failed_response=lambda *a, **k: types.SimpleNamespace(model_dump=lambda: {"status": "failed"}))
    assert litellm.disable_aiohttp_transport is True
