"""The FastAPI surface — verified with `run_live` STUBBED, so it needs no LLM, dspy, Deno, or toolscout.
Exercises the live endpoint's worker-thread → async-queue → SSE glue and the replay endpoints' file
handling, plus the path-traversal, no-cache, and toolspace-ops-augmentation guards."""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from toolscout_studio import app as appmod  # noqa: E402

client = TestClient(appmod.app)


# ---- TS_TOOLSPACE is anchored at the workspace root, not the CWD (live worker delegates to cli.run) ----

def test_abs_toolspace_anchors_relative_at_root(tmp_path):
    from pathlib import Path
    # a relative spec (the shipped ./toolspace.json) resolves under `root`, regardless of CWD
    rel = appmod._abs_toolspace("./toolspace.json", tmp_path)
    assert rel == str((tmp_path / "toolspace.json").resolve()) and Path(rel).is_absolute()
    assert appmod._abs_toolspace("toolspace.json", tmp_path) == str((tmp_path / "toolspace.json").resolve())
    # an absolute spec passes through (resolved), never re-anchored under `root`
    abs_in = str(tmp_path / "x.json")
    assert appmod._abs_toolspace(abs_in, Path("/other/root")) == str(Path(abs_in).resolve())


# ---- /v1/config: never raises (unlike ToolscoutConfig.from_env), reads env directly ----

def test_config_exposes_model_roles_from_env(monkeypatch):
    monkeypatch.setenv("TS_ROOT_LM", "planner-m")
    monkeypatch.setenv("TS_SUB_LM", "specialist-m")
    monkeypatch.setenv("TS_JUDGE_LM", "judge-m")
    monkeypatch.setenv("TS_MAX_ITERATIONS", "42")
    cfg = client.get("/v1/config").json()
    assert cfg["models"] == {"planner": "planner-m", "specialist": "specialist-m", "judge": "judge-m"}
    assert cfg["max_iterations"] == 42


def test_config_none_when_unset_does_not_raise(monkeypatch):
    for k in ("TS_ROOT_LM", "TS_SUB_LM", "TS_JUDGE_LM"):
        monkeypatch.delenv(k, raising=False)
    cfg = client.get("/v1/config").json()   # from_env() would RAISE here; the studio must not
    assert cfg["models"] == {"planner": None, "specialist": None, "judge": None}


def test_config_judge_falls_back_to_specialist(monkeypatch):
    monkeypatch.setenv("TS_SUB_LM", "specialist-m")
    monkeypatch.delenv("TS_JUDGE_LM", raising=False)
    assert client.get("/v1/config").json()["models"]["judge"] == "specialist-m"


def test_config_judge_never_surfaces_a_subscription_specialist(monkeypatch):
    # the judge is a make_model_tool endpoint and from_env REJECTS a subscription judge, so the panel
    # must NOT show a subscription-sentinel specialist as the judge — that would be a config a run
    # couldn't use. (Regression: judge = TS_JUDGE_LM or specialist surfaced the subscription lifeline.)
    monkeypatch.setenv("TS_SUB_LM", "claude-agent-sdk/claude-fable-5")
    monkeypatch.delenv("TS_JUDGE_LM", raising=False)
    cfg = client.get("/v1/config").json()["models"]
    assert cfg["specialist"] == "claude-agent-sdk/claude-fable-5"   # the specialist role CAN be subscription
    assert cfg["judge"] is None                                    # but the judge fallback is suppressed
    # an EXPLICIT non-subscription judge still shows through
    monkeypatch.setenv("TS_JUDGE_LM", "openai/gpt-4o")
    assert client.get("/v1/config").json()["models"]["judge"] == "openai/gpt-4o"


def test_config_exposes_toolspace_and_flags(monkeypatch):
    monkeypatch.delenv("TS_TOOLSPACE", raising=False)
    monkeypatch.delenv("TS_ENABLE_JUDGE", raising=False)
    monkeypatch.setenv("TS_MAX_ITERATIONS", "not-a-number")   # must not 500 — falls back to the default
    cfg = client.get("/v1/config").json()
    assert cfg["toolspace"] == "demo" and cfg["enable_judge"] is False and cfg["max_iterations"] == 45


# ---- /v1/runs + /v1/runs/{id} (augmented with the re-derived toolspace ops) ----

def test_list_runs_lists_stored_responses(tmp_path, monkeypatch):
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "add-6-7.json").write_text("{}")
    (tmp_path / "responses" / "echo-hi.json").write_text("{}")
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    assert client.get("/v1/runs").json()["runs"] == ["add-6-7", "echo-hi"]


def test_get_run_augments_toolspace_ops_from_the_trace(tmp_path, monkeypatch):
    (tmp_path / "responses").mkdir()
    (tmp_path / "traces").mkdir()
    (tmp_path / "responses" / "r.json").write_text(json.dumps(
        {"id": "r", "status": "ok", "outcome": {"answer": "13"}}))
    (tmp_path / "traces" / "r.jsonl").write_text(
        json.dumps({"type": "run_start", "step_id": 0, "payload": {"meta": {}}}) + "\n"
        + json.dumps({"type": "tool_call", "step_id": 1, "payload": {
            "tool": "load_server", "args": {"server": "math"}, "server": "math", "ok": True,
            "tool_names": ["add"]}}) + "\n"
        + json.dumps({"type": "tool_call", "step_id": 2, "payload": {
            "tool": "call_tool", "server": "math", "args": {"tool": "add", "args": {"a": 6, "b": 7}},
            "ok": True, "result": "13"}}) + "\n")
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    body = client.get("/v1/runs/r").json()
    assert body["status"] == "ok"
    ops = body["toolspace_ops"]                          # re-derived; response envelope carries only flat lists
    assert ops["counts"]["servers_loaded"] == 1 and ops["counts"]["calls_ok"] == 1
    assert ops["servers"][0]["name"] == "math" and ops["servers"][0]["calls"][0]["tool"] == "add"


def test_get_run_toolspace_ops_null_when_no_trace(tmp_path, monkeypatch):
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "r.json").write_text(json.dumps({"id": "r", "status": "ok"}))
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    assert client.get("/v1/runs/r").json()["toolspace_ops"] is None


def test_get_run_404_when_absent():
    assert client.get("/v1/runs/does-not-exist").status_code == 404
    assert client.get("/v1/runs/does-not-exist/events").status_code == 404
    assert client.get("/v1/runs/does-not-exist/iterations").status_code == 404


def test_run_id_path_is_slug_sanitized_against_traversal(tmp_path, monkeypatch):
    # a run_id can embed user input (a derived task slug) and becomes a file path. A traversal attempt must
    # fold to a harmless slug that resolves inside ARTIFACTS (→ 404), never escape it.
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    assert appmod._slug_id("../../etc/passwd") == "etc-passwd"
    assert appmod._slug_id("..") == "unknown" and "/" not in appmod._slug_id("a/b/c")
    assert client.get("/v1/runs/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_run_id_is_length_capped(tmp_path, monkeypatch):
    # an over-long explicit run_id becomes a filename; without the cap the artifact write dies with
    # ENAMETOOLONG. The cap re-strips so truncation never leaves a trailing '-'/'.' (kept a valid slug).
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    long = appmod._slug_id("x" * 500)
    assert len(long) == appmod._RUN_ID_MAX and len(appmod._response_path("y" * 500).name) < 255
    edge = appmod._slug_id("a" * (appmod._RUN_ID_MAX - 1) + "-tail")   # cut lands on the '-'
    assert len(edge) <= appmod._RUN_ID_MAX and not edge.endswith(("-", "."))
    # idempotent: re-slugging a capped id (every read path re-slugs) is identity
    assert appmod._slug_id(long) == long and appmod._slug_id(edge) == edge


# ---- replay SSE: always ends with completed (truncated trace guard) ----

def _write_trace(tmp_path, lines):
    (tmp_path / "traces").mkdir(exist_ok=True)
    (tmp_path / "traces" / "r.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_replay_streams_mapped_events(tmp_path, monkeypatch):
    # A REAL finished trace holds both `final` (record_main_trajectory) and `run_end` (recorder __exit__),
    # in that order, with `result` between them. The mapper must skip `final` so the terminal event fires
    # exactly ONCE — and after `task.result.done`, not before it.
    _write_trace(tmp_path, [
        {"type": "run_start", "step_id": 0, "payload": {"meta": {"planner": "P", "task": "t"}}},
        {"type": "tool_call", "step_id": 1, "payload": {
            "tool": "load_server", "args": {"server": "math"}, "server": "math", "ok": True}},
        {"type": "final", "step_id": 2, "payload": {}},
        {"type": "result", "step_id": 3, "payload": {"output": {"answer": "13"}}},
        {"type": "run_end", "step_id": 4, "payload": {}}])
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    with client.stream("GET", "/v1/runs/r/events") as resp:
        body = "".join(resp.iter_text())
    assert "event: task.run.created" in body and "event: task.server.loaded" in body
    assert body.count("event: task.run.completed") == 1     # `final` skipped → run_end is the sole terminal
    assert body.index("task.result.done") < body.index("task.run.completed")  # terminal is LAST


def test_replay_of_truncated_trace_still_ends_with_completed(tmp_path, monkeypatch):
    # a hard-killed run (SIGKILL) leaves a trace with NO run_end; replay must still emit the terminal
    # `completed` so the client stops "Solving…" and GETs the stored response instead of hanging.
    _write_trace(tmp_path, [
        {"type": "run_start", "step_id": 0, "payload": {"meta": {"planner": "P"}}},
        {"type": "tool_call", "step_id": 1, "payload": {"tool": "list_servers", "servers": ["math"]}}])
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    with client.stream("GET", "/v1/runs/r/events") as resp:
        body = "".join(resp.iter_text())
    assert body.count("event: task.run.completed") == 1     # synthesized terminal event
    assert body.rstrip().endswith("data: {}")               # and it is the LAST event


def test_replay_delay_is_bounded(tmp_path, monkeypatch):
    # the SSE pacing `delay` is bounded [0, 10]: an out-of-range value 422s at validation (before the
    # handler runs), so a huge value can't park a streaming connection. Regression guard — the old
    # unbounded param accepted `?delay=1e9` (200) and slept ~forever between events.
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    assert client.get("/v1/runs/r/events?delay=1e9").status_code == 422   # over le=10 → rejected, not parked
    assert client.get("/v1/runs/r/events?delay=-1").status_code == 422    # under ge=0 → rejected
    # an in-range delay is still accepted and terminates cleanly (the backend knob stays even though the
    # UI no longer paces the main feed — the Trajectory drawer owns step-through replay)
    _write_trace(tmp_path, [
        {"type": "run_start", "step_id": 0, "payload": {"meta": {"planner": "P"}}},
        {"type": "result", "step_id": 1, "payload": {"output": {"answer": "ok"}}}])
    with client.stream("GET", "/v1/runs/r/events?delay=0.15") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert body.count("event: task.run.completed") == 1


# ---- /v1/solve: worker-thread → queue → SSE glue (run_live stubbed) ----

def test_solve_streams_live_events_then_completed(tmp_path, monkeypatch):
    def fake_run_live(request, run_id, sink, on_done, *, artifacts_dir=None):
        assert request == {"task": "what is 6 * 7?"}
        sink({"event": "task.server.loaded", "data": {"server": "math", "ok": True}})
        sink({"event": "task.tool.called", "data": {"server": "math", "tool": "add", "ok": True}})
        on_done({"status": "ok", "id": run_id, "outcome": {"answer": "42"}})

    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(appmod, "run_live", fake_run_live)
    with client.stream("POST", "/v1/solve", json={"task": "what is 6 * 7?"}) as r:
        body = "".join(r.iter_text())
    assert "event: task.run.created" in body and "what-is-6-7" in body     # run_id derived + slugged
    assert "event: task.server.loaded" in body and "event: task.tool.called" in body
    assert "event: task.run.completed" in body and '"answer": "42"' in body
    assert body.index("created") < body.index("task.server.loaded") < body.index("completed")


def test_solve_uses_explicit_run_id_when_given(tmp_path, monkeypatch):
    seen = {}

    def fake_run_live(request, run_id, sink, on_done, *, artifacts_dir=None):
        seen["run_id"], seen["request"] = run_id, request
        on_done({"status": "ok", "id": run_id})

    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(appmod, "run_live", fake_run_live)
    with client.stream("POST", "/v1/solve", json={"task": "t", "run_id": "My Run/01"}) as r:
        "".join(r.iter_text())
    assert seen["run_id"] == "My-Run-01"                    # explicit id wins, sanitized
    assert seen["request"] == {"task": "t"}                 # only the task reaches the worker


def test_solve_409_when_run_exists_without_overwrite(tmp_path, monkeypatch):
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "add-6-7.json").write_text("{}")
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    started = {"n": 0}
    monkeypatch.setattr(appmod, "run_live", lambda *a, **k: started.__setitem__("n", started["n"] + 1))
    r = client.post("/v1/solve", json={"task": "t", "run_id": "add 6 7"})
    assert r.status_code == 409 and "add-6-7" in r.json()["detail"]
    assert started["n"] == 0                                # guard rejected BEFORE the worker started


def test_solve_overwrites_when_overwrite_true(tmp_path, monkeypatch):
    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "add-6-7.json").write_text("{}")
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(appmod, "run_live",
                        lambda request, run_id, sink, on_done, **k: on_done({"status": "ok", "id": run_id}))
    with client.stream("POST", "/v1/solve",
                       json={"task": "t", "run_id": "add 6 7", "overwrite": True}) as r:
        body = "".join(r.iter_text())
    assert "event: task.run.completed" in body and '"status": "ok"' in body


# ---- /v1/runs/{id}/iterations + /v1/examples ----

def test_iterations_breakdown_from_trace(tmp_path, monkeypatch):
    _write_trace(tmp_path, [
        {"type": "run_start", "step_id": 0, "ts": 1.0, "payload": {"meta": {"task": "the task", "planner": "P"}}},
        {"type": "main_step", "step_id": 1, "ts": 1.0, "payload": {"turn": 0, "reasoning": "r", "code": "c", "output": "o"}},
        {"type": "tool_call", "step_id": 2, "ts": 3.0, "payload": {"tool": "list_servers", "servers": ["math"]}},
        {"type": "run_end", "step_id": 3, "ts": 5.0, "payload": {}}])
    monkeypatch.setattr(appmod, "ARTIFACTS", tmp_path)
    d = client.get("/v1/runs/r/iterations").json()
    assert d["initial"]["task"] == "the task" and d["total_s"] == 4.0
    assert len(d["iterations"]) == 1 and d["timeline"][0]["label"] == "list" and d["timeline"][0]["rel_s"] == 2.0


def test_examples_lists_demo_tasks():
    ex = client.get("/v1/examples").json()["examples"]
    assert len(ex) >= 1 and all("task" in e and "name" in e for e in ex)


# ---- the zero-build frontend is served same-origin, no-cache ----

def test_frontend_shell_and_assets_are_served_and_revalidate():
    root = client.get("/")
    assert root.status_code == 200 and "text/html" in root.headers["content-type"]
    assert "toolscout" in root.text and 'src="/static/app.js"' in root.text
    # the pure modules must load BEFORE app.js (which reads their ReplayCore / RunCore globals)
    assert root.text.index('src="/static/replay-core.js"') < root.text.index('src="/static/app.js"')
    assert root.text.index('src="/static/run-core.js"') < root.text.index('src="/static/app.js"')
    assert root.headers.get("cache-control") == "no-cache"
    for asset in ("app.js", "replay-core.js", "run-core.js", "trajectory.js", "style.css",
                  "vendor/fonts/jetbrains-mono-400.woff2"):
        resp = client.get(f"/static/{asset}")
        assert resp.status_code == 200 and resp.headers.get("cache-control") == "no-cache"
    assert client.get("/v1/runs/does-not-exist").status_code == 404   # static mount did not shadow the API
