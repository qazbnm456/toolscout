# toolscout-studio

The web layer for [`toolscout`](..) (ships in-repo as a **uv workspace member**): turn one solve over a
large MCP toolspace into something a user *watches happen* and then *reads* — a live **trajectory log**,
the grounded **answer**, the **ISL → ITL → PTC** discovery (which servers were loaded, which tools were
described, which were called with what args and what result), the **rubric criteria facts**, and any
**fabrication tells** — the way the AI-studio playgrounds feel, not a CLI dump.

Two pieces:

1. **SSE server** — serves a run's structured `TaskResponse` and **replays the run's trace as
   Server-Sent Events**, so the frontend can render the build-up step by step. It can also drive ONE
   live solve and stream its **action** trajectory.
2. **Web frontend** — a toolspace console: a task-input box (type one, or load an example) with a
   **run-id field that previews the auto-derived id and takes a manual override**, a live event
   feed, an answer card whose **frame is keyed to the trace-derived grounding** (not the planner's
   self-report), the **toolspace exploration** as the star view, and a Trajectory drawer that replays the
   RLM run turn by turn.

It does **not** re-implement any toolscout logic. toolscout owns the contract; this serves it. The one
hard rule it honors visually: **the card frame is derived from the trace-re-sourced facts, never from the
planner's self-report** — a policy can claim it used servers/tools/criteria it never touched; the trace
cannot. That's toolscout's assemble-on-read boundary (`schema.py`), made visual. See `DESIGN.md` for the
full visual contract.

## The contract it serves

toolscout writes two per-run artifacts this server reads (from `<workspace-root>/output` by default —
where the `toolscout` CLI writes with its default `--out ./output`; override with `TS_ARTIFACTS_DIR`):

- `responses/{run_id}.json` — the **`TaskResponse`** (`toolscout.schema`): `status`
  (`ok` / `refused` / `failed`), the assembled `outcome` (the grounded `answer` + `summary`, the
  trace-re-sourced `servers_loaded` / `tools_used`, the per-criterion `criteria_facts` and opt-in
  `judge_observations` as **labels**, and the fabrication tells `unbacked_servers` / `unbacked_tools`),
  `process` (effort counters), and any `refusal`/`error`. This is the **final,
  durable** output.
- `traces/{run_id}.jsonl` — the append-only run trace; its events are replayed as SSE and drive the
  Trajectory drawer.

**`toolspace_ops`** (the per-op ISL/ITL/PTC discovery narrative — which servers were listed, loaded,
described, and called with what args + result) is **not** in the envelope (`AssembledOutcome` carries
only flat `servers_loaded`/`tools_used`); the server re-derives it from the trace and augments
`GET /v1/runs/{id}` with it (see `iterations.toolspace_ops`). Missing trace → `null`.

## Endpoints

| Method | Path | Returns |
|---|---|---|
| `GET` | `/` | the toolspace console (zero-build vanilla page) |
| `POST` | `/v1/solve` | `text/event-stream` — **drive ONE LIVE solve**, ending with `task.run.completed` carrying the durable `TaskResponse`. Body `{task, run_id?, overwrite?}`. **409** if a finalized run already owns `run_id` and `overwrite` is not `true` (a re-solve resets the trace, so it would clobber the stored run). Needs the `live` extra |
| `GET` | `/v1/examples` | the bundled example tasks as one-click demo inputs (wired to the offline demo toolspace so a live solve resolves without a real MCP server) |
| `GET` | `/v1/runs/{run_id}` | the stored `TaskResponse` JSON, **augmented with `toolspace_ops`** (404 if absent) |
| `GET` | `/v1/runs/{run_id}/iterations` | the per-iteration trajectory breakdown (behind the Trajectory drawer) |
| `GET` | `/v1/runs/{run_id}/events?delay=0.0` | `text/event-stream` — a finished run's trace **replayed** as SSE; `delay` (s) paces it |
| `GET` | `/v1/runs` | run ids that have a stored response (the Load picker) |
| `GET` | `/v1/config` | the configured model per role, the toolspace, and the iteration budget the UI seeds from (fault-tolerant: never raises if `TS_*` is unset) |

Best practice (per OpenAI's streaming guidance): the stream carries the **process**; wait for
`task.run.completed` (it carries the full result on the live endpoint; the UI then re-`GET`s
`/v1/runs/{id}` for the `toolspace_ops` augmentation, or GETs it after a replay).

## SSE event vocabulary (trace event → public event)

| trace event | SSE `event` | `data` |
|---|---|---|
| `run_start` | `task.run.created` | `{models, task, toolspace, max_iterations, criteria}` |
| `main_step` | `task.plan.step` | `{turn, reasoning, has_code}` (replay only) |
| `tool_call: list_servers` | `task.servers.listed` | `{n, servers}` (the server index) |
| `tool_call: load_server` | `task.server.loaded` | `{server, ok, tools}` (ISL) |
| `tool_call: describe_tools` | `task.tools.described` | `{n, described}` (ITL) |
| `tool_call: call_tool` | `task.tool.called` | `{server, tool, ok, reason, error}` (PTC) |
| `tool_call: rubric_judge` | `task.judge` | `{ok, circuit_broken, error, n}` |
| `tool_call: read_skill`/`list_skills` | `task.skill.read` | `{name}` |
| `sub_call` (specialist) | `task.specialist.escalation` | `{question, answer}` |
| `result` | `task.result.done` | `{}` — signal; client then GETs the full response |
| `run_end` | `task.run.completed` | the `TaskResponse` (live) / `{}` (replay) |

The mapping lives in `toolscout_studio/mapper.py` (a pure function, unit-tested) — the single source of
truth for the public event surface.

### Honest caveat — the LIVE feed is actions-only

toolscout's `cli.run` exposes exactly one live observer: the `TraceRecorder`'s `on_event`, which fires
for **tool_calls and sub_calls** (the sandbox-invoked actions) as they happen. It has **no**
planner-reasoning callback, so the **live** feed shows the *actions* (list / load / describe / call /
judge / skill / specialist) in real time, **not** the planner's reasoning turns. The reasoning is
recovered **post-hoc from the trace** — visible on **replay** (`task.plan.step`) and in the **Trajectory
drawer**, never in the live feed. This is the deliberate **zero-harness-change** v1: the studio adds no
tool and no callback to the solve path. Surfacing live reasoning is a future **rlm-kit** increment (a
generic planner-step observer on the RLM), **not** a toolscout-specific callback bolted on here.

### Ordering caveat (replay)

A stored trace does **not** preserve true `think → act` interleaving: tool_calls are written live
(step_ids 1…N) but the planner's `main_step` reasoning turns flush **post-hoc at finalize** (trailing
step_ids), so a *replay* streams the **action timeline first, then the reasoning turns**. The server
replays in `step_id` order (deterministic); the Trajectory drawer presents turns and their tools together.

## Run

The studio shares ONE venv with the root `toolscout`, so every command runs from the **repo root** (the
workspace root).

**Replay-only** (serve + replay stored runs — no toolscout runtime needed). The artifacts dir defaults to
`<repo-root>/output` — exactly where a `toolscout` CLI run writes — so no `TS_ARTIFACTS_DIR` is needed
when you run both from the repo root:

```bash
uv sync --package toolscout-studio           # fastapi + uvicorn into the shared workspace venv
uv run --package toolscout-studio uvicorn toolscout_studio.app:app --reload
open http://127.0.0.1:8000/                   # type a task → live feed → answer + toolspace
curl http://127.0.0.1:8000/v1/runs
uv run --package toolscout-studio --extra dev python -m pytest studio/tests   # the contract tests (no toolscout needed)
for t in studio/tests/*.test.js; do node "$t"; done                          # the node core-tests (zero-dep, pure JS)
```

**Subscription mode** — if a role runs on a Claude Pro/Max subscription (`.env` has
`TS_ROOT_LM`/`TS_SUB_LM=claude-agent-sdk/<id>`), the live worker also needs the Claude Agent SDK, so add
`--extra subscription` **to every `uv sync`/`uv run`** (it forwards `toolscout`'s own `subscription`
extra); it **must** ride with `--extra live` in the **same** command, or a later bare sync prunes the SDK
back out:
```bash
uv run --package toolscout-studio --extra live --extra subscription \
  uvicorn toolscout_studio.app:app --port 8731 --timeout-graceful-shutdown 12
```
Without it a subscription run raises `ImportError: ClaudeAgentLM requires the optional dependency … No
module named 'claude_agent_sdk'`.

**Live** (`POST /v1/solve` drives a REAL solve) needs `toolscout` importable AND its env (`TS_ROOT_LM`
planner / `TS_SUB_LM` specialist / `TS_BASE_URL` … see the root `.env.example`), a Deno sandbox
(`brew install deno`) for the pyodide REPL, and — for a non-demo toolspace — `TS_TOOLSPACE` pointing at
your MCP server specs. Without `toolscout` the live worker raises `ModuleNotFoundError` — the stream still
completes with a `failed` card, but nothing runs.

`toolscout` consumes **rlm-kit as a commit-pinned git source**, so `uv sync` is self-contained — no
sibling checkout needed. Co-developing rlm-kit locally? Overlay it editable (`uv pip install -e
../rlm-kit`) so your local edits are picked up.

The studio reads `os.environ` directly and does **not** auto-load `.env`, so source it into your shell:

```bash
set -a && source .env && set +a               # TS_ROOT_LM / TS_SUB_LM / TS_BASE_URL … (use `source`, not `.`)
uv run --package toolscout-studio --extra live \
  uvicorn toolscout_studio.app:app --port 8791
```

(Artifacts default to `<repo-root>/output`, where the CLI writes them, so the studio's own live runs land
next to CLI runs and are mutually replayable; override with `TS_ARTIFACTS_DIR`. Skip `--reload` for live
runs — editing a `.py` restarts the server mid-stream.)

**No cooperative cancel (v1).** toolscout's `cli.run` takes no `cancel_event`, so the studio has no Stop
button and no graceful-cancel wiring: a live run runs to completion (bounded by the RLM's own
`max_iterations`). Ctrl+C on the server kills the process; an in-flight run's worker thread is a daemon
and dies with it, which may leave a **partial trace and no stored response** for that run_id (it is simply
not in the Load picker). Adding cooperative cancel is a future increment and, like live reasoning, belongs
in **rlm-kit** (a generic run-cancel seam), not as a consumer-specific hack here.

The frontend is served from the repo checkout (`static/` resolved next to the package). It is a
**zero-build vanilla** page (no node/npm/bundler): `static/{index.html,app.js,style.css,trajectory.js}`
plus the pure, unit-tested `replay-core.js` / `run-core.js` and a vendored JetBrains Mono. The same
FastAPI app serves it (same-origin, no CORS); `/static/*` are the assets, `/v1/*` the API.

## Web frontend (the toolspace console)

`GET /` is a single page (see `DESIGN.md`):

- **Task** — type a task, or pick a **⚡ example** (wired to the offline demo toolspace). **Solve** drives
  a live run; the **Load** box replays a stored run id (a `<datalist>` from `GET /v1/runs`). A **light/dark
  toggle** (persisted; honors `prefers-color-scheme`) sits in the header with the role chips.
- **Trajectory log** — the live SSE feed of *actions* as they happen (list / load / describe / call /
  judge / skill / specialist). Newest at the bottom. (Planner reasoning is in the Trajectory drawer.)
- **The result** (two columns) — the middle **stage** is ONE page-height **answer card** whose alloy is
  the **derived grounding, not the self-report**: `grounded` (an answer, no fabrication tell), `flag` (an
  answer whose self-report over-claims — a **fabrication marker** spells out which servers/tools/criteria
  the trace does not back), `iron` (a refusal / failed run). A top-right **Answer / Toolspace / Task**
  switch walks the read order: the answer, then **Toolspace** (the **star** — the ISL/ITL/PTC exploration
  grouped by server, each call as `tool(args) → result`, plus the fabrication wells), then the task
  string. The right column carries **Run telemetry** (turns · servers · tool calls · escalations),
  **Rubric criteria facts** (per-criterion deterministic facts + any judge observations, as labels),
  **Fabrication tells**, and **Summary**.
- Every `status` is explicit — a `failed`/`refused` run shows an **iron refusal card** with the reason and
  the counters gathered, never a blank screen.

Both live and replay use one `streamSSE()` (fetch + `ReadableStream`) since native `EventSource` cannot
POST. There is also a **Trajectory** drawer (a bottom-sheet) that replays the run iteration by iteration —
the planner's REPL turns, a tool timeline (segment width ∝ time), and a transport to step/play through it
— built from `GET /v1/runs/{id}/iterations`.

## Not built yet (deferred)

- **Cooperative cancel / graceful shutdown** — no Stop button (see above); a killed live run may leave a
  partial trace and no stored response. The fix is a generic run-cancel seam in **rlm-kit**.
- **Live planner reasoning** — the live feed is actions-only; interleaving reasoning needs a generic
  planner-step observer in **rlm-kit** (the same reason).
- **Wheel-packaged static** — the frontend is served from the repo checkout (the supported run mode);
  bundling `static/` into the wheel for a `pip install`-only deploy is deferred. The `/` route + mount are
  guarded, so a backend-only install without the dir still boots.
- **Per-run isolation under concurrency** — the live endpoint runs one job per request in its own thread +
  queue; heavy concurrent multi-run isolation (they share the process CWD/artifacts dir) is a later
  refinement.
