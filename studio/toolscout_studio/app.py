"""The SSE server: two modes.

- **Replay** (`GET /v1/runs/{id}` + `/events`) — serve a finished run's structured `TaskResponse` and
  replay its stored trace as SSE. A thin bridge over toolscout's per-run artifacts (`responses/`,
  `traces/`); defaults to this in-repo workspace root, override with `TS_ARTIFACTS_DIR`.
- **Live** (`POST /v1/solve`) — drive a LIVE solve via toolscout's `cli.run` on a task string and stream
  the ISL/ITL/PTC ACTION trajectory as SSE (the `TraceRecorder`'s `on_event` observer; see `live.py`),
  ending with `task.run.completed` carrying the durable `TaskResponse`.

The console reads toolscout's trace/v1 + TaskResponse contract; it re-implements no harness logic. NO
cancel/Stop in v1 (a solve is one bounded RLM episode): a terminal Ctrl+C makes uvicorn wait for the live
SSE to close (the run to finish) before exiting — set `--timeout-graceful-shutdown` as a bound.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .iterations import build_iterations, toolspace_ops
from .live import run_live
from .mapper import to_event

# The workspace ROOT that owns this studio/ member (parents[2] of studio/toolscout_studio/app.py).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Where toolscout's runs live, resolved to an ABSOLUTE path so it is stable regardless of the process
# CWD. Default: `<root>/output` — toolscout's `cli` defaults `--out ./output` (and cli.run's `outdir`
# default is "./output"), so a CLI run from the repo root writes `output/{traces,responses}`; the studio's
# own live worker writes there too (it passes this dir as `outdir`). So zero-config replay-only just works
# on CLI-produced runs. `TS_ARTIFACTS_DIR` overrides to point at any other output dir / checkout.
ARTIFACTS = Path(
    os.environ.get("TS_ARTIFACTS_DIR") or REPO_ROOT / "output"
).expanduser().resolve()


def _abs_toolspace(raw: str, root: Path) -> str:
    """A RELATIVE TS_TOOLSPACE (the shipped `./toolspace.json`) → absolute, anchored at `root`. Absolute
    and `~` paths pass through (expanded)."""
    p = Path(raw).expanduser()
    return str((p if p.is_absolute() else root / p).resolve())


# Give TS_TOOLSPACE the same CWD-independent treatment as ARTIFACTS. The live worker delegates to
# toolscout's cli.run → config.from_env → load_catalog, which resolves a relative spec against the process
# CWD — but a long-running server's CWD is wherever it was launched, not necessarily the workspace root.
# Anchor a relative value at REPO_ROOT (rewriting os.environ, which from_env reads) so live solves find it.
if os.environ.get("TS_TOOLSPACE"):
    os.environ["TS_TOOLSPACE"] = _abs_toolspace(os.environ["TS_TOOLSPACE"], REPO_ROOT)

STATIC = Path(__file__).resolve().parent.parent / "static"

# Built-in example TASKS — one-click demos wired to the offline demo catalog (echo / math / memory / text
# servers), so a live solve against the default toolspace actually resolves. A REPO asset (ships with the
# studio), vendor-neutral + generic.
EXAMPLES = [
    {"name": "arithmetic + text",
     "task": "What is 6 * 7, and then upper-case the word 'ok'?",
     "note": "exercises the math + text servers across the ISL → ITL → PTC loop."},
    {"name": "stateful memory",
     "task": "Store the number 42 under the key 'answer', then read it back and add 8 to it.",
     "note": "exercises the memory + math servers — PTC state persists across calls in the REPL."},
    {"name": "echo + wordcount",
     "task": "Echo the text 'hello there world' unchanged, then count how many words it has.",
     "note": "exercises the echo + text servers."},
]

app = FastAPI(title="toolscout-studio", version="0.1.0")


class _RevalidateStatic(StaticFiles):
    """Serve static assets with `Cache-Control: no-cache` so the browser ALWAYS revalidates — it still
    304s when unchanged (via the ETag StaticFiles already sends, so it's cheap). Without this the
    zero-build `app.js`/`style.css` cache indefinitely, so a shipped frontend change silently shows the
    OLD UI until a manual hard-refresh."""

    async def get_response(self, path: str, scope):  # noqa: ANN001 — Starlette's Scope type
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


# The zero-build vanilla frontend (index.html + app.js + trajectory.js + style.css + the pure
# replay-core/run-core modules + a vendored font), served same-origin so no CORS. Guarded so a
# backend-only deploy without the dir still boots.
if STATIC.is_dir():
    app.mount("/static", _RevalidateStatic(directory=str(STATIC)), name="static")


@app.get("/")
def index() -> FileResponse:
    """Serve the single-page frontend shell (the toolspace console)."""
    idx = STATIC / "index.html"
    if not idx.exists():
        raise HTTPException(404, "frontend not present (static/index.html missing)")
    return FileResponse(str(idx), headers={"Cache-Control": "no-cache"})  # revalidate; never a stale shell


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# The hard cap on a slug id's length. The slug is pure ASCII by construction, so chars == bytes: the
# artifact filename (`{slug}.jsonl`) stays far under the filesystem's 255-byte NAME_MAX, instead of an
# over-long explicit run_id failing the trace/response write with ENAMETOOLONG. Mirrored client-side by
# `RunCore.slugId` (run-core.js), so the console's preview/`→ runs as` hint shows the capped id up front.
_RUN_ID_MAX = 120


def _slug_id(raw: str) -> str:
    """A filesystem-/URL-safe id token: keep [A-Za-z0-9._-], fold the rest (incl. `/`) to '-', strip
    leading/trailing '.'/'-' so it can NEVER become a traversal segment (`..`, an absolute path, a nested
    dir), and cap at `_RUN_ID_MAX` chars (re-stripped so truncation never leaves a trailing '-'/'.'). A
    run_id can embed user input (a derived task slug), and it becomes a file path — the console must not
    open a path traversal on itself."""
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "").strip("-.")
    token = token[:_RUN_ID_MAX].rstrip("-.")
    return token or "unknown"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _response_path(run_id: str) -> Path:
    return ARTIFACTS / "responses" / f"{_slug_id(run_id)}.json"


def _trace_path(run_id: str) -> Path:
    return ARTIFACTS / "traces" / f"{_slug_id(run_id)}.jsonl"


def _step_key(event: dict) -> int:
    s = str(event.get("step_id", ""))
    return int(s) if s.lstrip("-").isdigit() else 1 << 30


def _load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


@app.get("/v1/config")
def config() -> JSONResponse:
    """The three model ROLES → their configured model names (from env), so the UI can show them on page
    load. Read env DIRECTLY (never `ToolscoutConfig.from_env`, which RAISES without TS_ROOT_LM/TS_SUB_LM)
    so a replay-only deploy still answers. `judge` mirrors toolscout's `TS_JUDGE_LM or specialist`
    fallback. `toolspace`/`max_iterations`/`enable_judge` let the UI frame the run."""
    specialist = os.environ.get("TS_SUB_LM")
    return JSONResponse({
        "models": {
            "planner": os.environ.get("TS_ROOT_LM"),
            "specialist": specialist,
            "judge": os.environ.get("TS_JUDGE_LM") or specialist,
        },
        "toolspace": os.environ.get("TS_TOOLSPACE") or "demo",
        # Fallback MIRRORS toolscout config.py's `max_iterations` default (read env directly to stay
        # replay-only-safe, so keep this in sync when that default changes).
        "max_iterations": _int_env("TS_MAX_ITERATIONS", 45),
        "enable_judge": (os.environ.get("TS_ENABLE_JUDGE", "").strip().lower() in {"1", "true", "yes", "on"}),
    })


@app.get("/v1/runs")
def list_runs() -> JSONResponse:
    """Run ids that have a stored response, sorted — feeds the Load picker so the user can discover what
    is loadable instead of guessing a run id."""
    d = ARTIFACTS / "responses"
    runs = sorted((p.stem for p in d.glob("*.json")), key=lambda s: s.lower()) if d.is_dir() else []
    return JSONResponse({"runs": runs})


@app.get("/v1/examples")
def examples() -> JSONResponse:
    """The bundled example tasks as one-click demo inputs — wired to the offline demo toolspace so a live
    solve resolves without a real MCP server. Read-only, from a repo constant."""
    return JSONResponse({"examples": EXAMPLES})


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str) -> JSONResponse:
    """The durable, structured `TaskResponse` for a finished run, AUGMENTED with `toolspace_ops` (the
    per-op ISL/ITL/PTC discovery narrative — re-derived from the trace, since the response envelope carries
    only flat `servers_loaded`/`tools_used`; see iterations.toolspace_ops). Missing trace → `null`."""
    p = _response_path(run_id)
    if not p.exists():
        raise HTTPException(404, f"no response for run {run_id!r}")
    try:
        resp = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:  # a corrupt/half-written response file must not 500 opaquely
        raise HTTPException(502, f"stored response for run {run_id!r} is unreadable: {e}") from e
    tp = _trace_path(run_id)
    resp["toolspace_ops"] = toolspace_ops(_load_events(tp)) if tp.exists() else None
    return JSONResponse(resp)


@app.get("/v1/runs/{run_id}/iterations")
def get_iterations(run_id: str) -> JSONResponse:
    """The per-iteration trajectory breakdown — planner reasoning + REPL code/output + each turn's tool
    calls + timing — behind the Trajectory drawer. Built from the stored trace, read-only."""
    p = _trace_path(run_id)
    if not p.exists():
        raise HTTPException(404, f"no trace for run {run_id!r}")
    return JSONResponse(build_iterations(_load_events(p)))


@app.get("/v1/runs/{run_id}/events")
async def stream_run(run_id: str, delay: float = Query(0.0, ge=0, le=10)) -> StreamingResponse:
    """Replay the run's trace as SSE — the process build-up. `delay` (seconds) paces it to feel live.
    Wait for `task.run.completed`, then GET `/v1/runs/{run_id}` for the full result."""
    p = _trace_path(run_id)
    if not p.exists():
        raise HTTPException(404, f"no trace for run {run_id!r}")
    # Sort by step_id (matches toolscout's read order). Ordering caveat: tool_calls are written live but
    # `main_step`s flush post-hoc with trailing step_ids, so a REPLAY streams the action timeline first,
    # then the reasoning turns — the stored trace does not preserve true think→act interleaving.
    # Read + parse the JSONL off the event loop — a multi-MB trace would otherwise block every
    # concurrent request for the whole parse.
    events = sorted(await asyncio.to_thread(_load_events, p), key=_step_key)

    async def gen():
        saw_completed = False
        for event in events:
            out = to_event(event)
            if out is None:
                continue
            if out["event"] == "task.run.completed":
                saw_completed = True
            yield _sse(out["event"], out["data"])
            if delay:
                await asyncio.sleep(delay)
        if not saw_completed:
            # The trace never finalized — no `run_end` (e.g. a hard-killed run: SIGKILL skips the
            # recorder's __exit__, truncating the JSONL). Still close the replay with the terminal event,
            # so the client GETs the stored response instead of waiting forever "Solving…".
            yield _sse("task.run.completed", {})

    return StreamingResponse(gen(), media_type="text/event-stream")


class SolveRequest(BaseModel):
    """One live run's request. `task` is the task string to solve. `run_id` names the artifacts; when
    absent it is derived from the task + sanitized. `overwrite` guards a re-solve from silently clobbering
    a finalized run."""
    task: str
    run_id: str | None = None
    overwrite: bool = False


def _derive_run_id(req: "SolveRequest") -> str:
    """The run id, sanitized (it becomes a file path). An explicit `run_id` wins; otherwise a short slug of
    the task's leading words, so re-solving the same task collides on the same id (the overwrite guard)."""
    if req.run_id and req.run_id.strip():
        return _slug_id(req.run_id.strip())
    words = (req.task or "").split()
    # Join with spaces (not '-') so `_slug_id` collapses any whitespace/punctuation run to a SINGLE dash
    # ("6 * 7" → "6-7", not "6---7").
    base = " ".join(words[:6])[:48] if words else "task"
    return _slug_id(base) or "task"


@app.post("/v1/solve")
async def solve(req: SolveRequest) -> StreamingResponse:
    """Drive a LIVE solve and stream its ISL/ITL/PTC ACTION trajectory as SSE, ending with
    `task.run.completed` carrying the durable `TaskResponse`. The run executes in a worker thread (it has
    blocking parts); the `on_event` observer pushes events onto a thread-safe queue this coroutine drains.
    The run writes the usual toolscout artifacts (so it is later GET-replayable). 409 if a FINALIZED run
    already owns this run_id and `overwrite` is not set — toolscout's `run` resets the trace per run_id, so
    a re-solve would clobber the stored response/trace."""
    run_id = _derive_run_id(req)
    if not req.overwrite and _response_path(run_id).exists():
        raise HTTPException(409, f"run {run_id!r} already exists — pass overwrite=true to replace it")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    def sink(event: dict) -> None:                       # called from the worker thread
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def on_done(final: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"event": "task.run.completed", "data": final})
        loop.call_soon_threadsafe(queue.put_nowait, done)

    request = {"task": req.task}
    worker = threading.Thread(target=run_live, args=(request, run_id, sink, on_done),
                              kwargs={"artifacts_dir": ARTIFACTS}, daemon=True)
    worker.start()

    async def stream():
        yield _sse("task.run.created", {"run_id": run_id})
        while True:
            item = await queue.get()
            if item is done:
                break
            yield _sse(item["event"], item["data"])

    return StreamingResponse(stream(), media_type="text/event-stream")
