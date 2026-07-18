# Changelog

All notable changes to toolscout. This project solves ONE task over a possibly LARGE MCP toolspace with a
SMALL planner — an [ATLAS](https://arxiv.org/html/2603.06713v1)-style rollout harness on
[`rlm-kit`](https://github.com/qazbnm456/rlm-kit): the planner DISCOVERS the toolspace progressively
(Iterative Server/Tool Loading) and computes over tool results as code in a sandboxed persistent REPL
(Programmatic Tool Calling), emits a judgement-only outcome whose evidence is re-sourced from the trace on
read, and exports a REWARD-FREE trajectory dataset.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **`cli.run` surfaces the chained cause on a failed run** (`cli.py`): a wrapped exception (e.g. an
  `RLMTaskError` that wraps the real `AdapterParseError`/endpoint error via `raise … from`) now appends
  `(caused by <Type>: <msg>)` to the response `error` string, so `output/responses/<run>.json` is
  self-diagnosing instead of showing only the generic wrapper text.

## [0.1.0] - 2026-07-19

The initial public release: a task string in → a structured, grounded outcome over a large MCP toolspace
out, recorded as a reward-free trajectory. The ATLAS approach mapped onto rlm-kit, fully offline-testable.

### Added

- **The ATLAS discovery loop as four fixed meta-tools** (`toolspace.py`): `list_servers` (ISL — the server
  index, no schemas) → `load_server` (ISL — materialize one server) → `describe_tools` (ITL — pull a few
  tool schemas just-in-time) → `call_tool` (PTC — invoke a loaded tool, return a native REPL value). All
  sync, explicit-param, `fn.__name__`-pinned; the whole toolspace routes through these four, so every
  ISL/ITL/PTC decision lands in the trace as a `tool_call`. PTC itself is inherited free — `dspy.RLM` is
  already a persistent tools-as-code REPL.
- **Scaffolding** (`scaffolding.py`): heterogeneous tool schemas normalized to uniform Python signatures,
  argument coercion, and informative (fixable) error strings the planner recovers from in-loop.
- **The `MCPServer` proxy + observed-example ITL disclosure** (ATLAS scaffolding Phase 2). The planner can
  define a tiny `MCPServer` proxy (source in its instructions) so a per-server call reads as
  `srv.add(a=2, b=3)` — pure sugar over `call_tool` (every proxy call still records exactly one canonical
  `tool_call`; `call_tool` stays the primitive), with `ast.literal_eval` re-nativizing number/list-shaped
  results across dspy's REPL boundary; `load_server`'s first result carries a one-line proxy hint. For a
  non-identifier tool name (e.g. `search-web`) the instructions say to call `call_tool` directly. Separately,
  `describe_tools` discloses a tool's DECLARED return type (mapped from an MCP tool's `outputSchema`) and
  ONE example output — observed this run if the tool was already called, else a static example — both
  `_cap`-bounded so untrusted output stays a single flat line.
- **A PTC repeat-call guard in `call_tool`** (`TS_MAX_REPEAT_CALLS`, default 3; `0` disables): past the
  budget, an IDENTICAL `(server, tool, args)` re-call is refused PRE-dispatch with guiding TEXT (recorded
  as `ok=False, reason="repeat_call"`, counted into the PA `predispatch_reject_count`), and the budget
  rides `run_start` meta like the other caps. Motivated by a live run whose planner re-paginated the full
  HIBP breach catalog once per keyword — 10 identical 0→1000 sweeps, 210 calls in 46s against a
  third-party server; the guard breaks such unconscious re-fetch loops (and identical-args retry storms)
  after 3 attempts while keeping the refusals in the trace as a training-visible signal. The planner
  instructions also say to fetch a dataset ONCE into a variable and filter in the REPL.
- **Judgement-only SUBMIT + assemble-on-read** (`schema.py`, `assemble.py`): `TaskOutcome` is
  citation-only (no field for raw outputs, scores, or reward), so the policy cannot self-report evidence.
  `assemble_outcome` re-sources `servers_loaded` / `tools_used` from the trace and flags fabrication in
  `unbacked_servers` / `unbacked_tools`. Runs at every read path (live, render, export). The SUBMIT has
  deliberately no `cited_criteria` — the rubric is a trainer/eval-side artifact the agent never sees at
  inference (as in ATLAS), so the per-criterion signal is the deterministic `criteria_facts`, never a
  policy self-citation.
- **A need-based CHECKPOINT (verify) step in the planner workflow** (`agent.py` `INSTRUCTIONS`): after each
  successful call the planner re-derives every distinct value its FINAL answer needs — **across ALL the
  servers the task spans** — acts on any still missing, and SUBMITs the instant none are (a value in hand
  is DONE — no re-read/re-verify/re-summarize/polish). The task-scoped need-gate curbs post-grounding
  wheel-spin WITHOUT the premature-submit risk of a turn-count heuristic: the small planner never sees the
  live step number, and a genuine multi-server task keeps discovering because the gate stays unsatisfied.
  The budget HARD RULE is anchored to a COMPLETE answer (submitting before querying the servers the task
  needs is the second-worst outcome). Prompt-only; SUBMIT stays judgement-only, no reward.
- **Rubric decomposition carried as LABELS** (`rubric.py`, `schema.py`): a task is decomposed into criteria
  across the four ATLAS categories (Task Fulfillment / Tool Appropriateness / Tool Grounding / Parameter
  Accuracy) and stored in `run_start` meta — structure, never a score. `default_rubric` is a deterministic,
  model-free skeleton (offline demo + CI); `generate_rubric` decomposes via one host-side frontier-model
  call. `criteria_facts` re-sources deterministic per-criterion observations from the trace.
  `validate_rubric` is a DETERMINISTIC structural lint (category coverage, unique names, non-empty +
  plausibly-observable descriptions), NOT a semantic-quality judge.
- **`toolscout rubric-batch <taskset> <out-dir>`** — batch per-task rubric generation for the rollout
  workflow (generate offline once, then `solve --rubric <that task's rubric>` so a live run's labels vary
  per task instead of carrying the generic skeleton).
- **Reward-free dataset export** (`rl_export.py`): trajectory splits (SFT turns / planner toolspace-ops /
  judge), per-run intrinsic labels + objective metrics, and the ATLAS rubric signal (the rubric + its
  deterministic per-criterion facts + the opt-in judge's observations) — all with `reward=None`. Reward,
  scoring, credit assignment, and GRPO/SFT live in a separate fine-tuning project.
- **The opt-in `rubric_judge` tool** (`judge_tool.py`, OFF by default): a verify-before-finalize self-check
  the planner CHOOSES to call, built on rlm-kit's `make_model_tool` (chat → transient-retry → validate →
  circuit-break). It emits per-criterion OBSERVATIONS (a note + met/unmet) as labels, never an aggregate
  reward. Its endpoint is always a separate OpenAI-compatible client, never the subscription.
- **Model ROLES by env** (`config.py`): planner (`TS_ROOT_LM`), specialist (`TS_SUB_LM`, reached via
  `llm_query`, intercepted for tracing only), judge (`TS_JUDGE_LM`). No hardcoded model name. The default
  `max_iterations` is **45** — a HARD per-run budget, never multiplied by an outer loop (`max_retries=1`);
  it rides in `run_start` meta so `render`/`export` re-derive the same `hit_iteration_cap`.
- **Claude Pro/Max subscription support** (`agent._maybe_subscription_lm`, opt-in `[subscription]` extra):
  give the planner or specialist a `claude-agent-sdk/<id>` model to run it on a personal Claude login via
  rlm-kit's `ClaudeAgentLM`, injected through `configure(main_lm=…, sub_lm=…)`. Imported lazily so a
  proxy-only install never pulls the SDK. The judge may not use the sentinel — mixed auth by design; the
  guard fires only when the judge is actually ENABLED (`TS_ENABLE_JUDGE=1`), so a subscription
  planner+specialist with the judge off stays a valid config.
- **The toolspace backends** (`catalog.py`, `mcp_toolspace.py`): a `Catalog` abstraction with a built-in
  **demo catalog** (`echo` / `math` / `memory` / `text` — offline, deterministic, so `solve` works
  end-to-end with only model creds + Deno) and an `McpCatalog` over external MCP servers (client-only;
  eager host-side connect pre-run; a per-server sync bridge). Untrusted server text is length-capped.
  `load_server` guards the catalog connect: a wedged/refused server returns a short `connect_error` message
  (recorded `ok=False, reason="connect_error"`) instead of raising into the RLM loop — errors are text,
  never a raise. This pairs with the kit's per-transport `connect="lazy"` (a URL server defers and is
  bounded/cancel-reaped; a stdio server stays eager). Server-authored error text is length-capped too.
- **A curated default toolspace — `toolspace.example.json`** (tracked; a local `toolspace.json` is
  git-ignored, mirroring `.env`/`.env.example`). It declares two **hosted, no-key, first-party**
  streamable-HTTP security servers — ProjectDiscovery **Security Context** (`securitycontext.dev/mcp`:
  repo security context + CVE/variant-lead search over public repos) and the **official Have I Been
  Pwned** MCP (`haveibeenpwned.com/mcp`: breach catalog + k-anonymity password check) — so a new user gets
  a real ATLAS ISL→ITL→PTC demo with zero signup. Both were security due-diligenced as accountable
  first-party operators; the guide's "The toolspace" section documents the trust disclosure (Security
  Context publishes results + logs IPs; HIBP's account/stealer tools need the user's own OAuth and fail
  closed) and a trust-ordered **opt-in catalog** (hosted vendor-official servers first; community
  `npx`/`uvx` stdio servers flagged as HOST-RCE, opt-in, never default-on). `.env.example` keeps
  `TS_TOOLSPACE` commented, so the toolspace stays opt-in-by-choice (no silent first-run egress).
- **The CLI** (`cli.py`): `solve` (live), `render` / `export` / `rubric` / `rubric-batch` (offline).
  `run()` is the programmatic entry — records the trace, assembles on read, writes the response, and never
  raises on a failed run (it writes a `status=failed` response instead).
- **Progressive-disclosure skills KB** (`toolscout/skills/`, `read_skill`): toolspace tactics (planning,
  server selection, when to describe a tool, PTC/REPL discipline, error recovery, grounding) pulled
  just-in-time — knowledge only, no script execution. Ship in the wheel via `packages = ["toolscout"]`.
- **The studio** (`studio/`, a uv workspace member, NOT in the `toolscout` wheel): an SSE server + web
  frontend that serves a run's `TaskResponse`, replays its trace, and renders the ISL/ITL/PTC trajectory,
  the answer, and the rubric criteria facts. It reads this package's trace/`TaskResponse` contract; its web
  stack stays behind its own `live` extra. The run id is visible and overridable before a solve: the Task
  panel's `RUN ID` row live-previews the id the run will file under (`RunCore.deriveRunId`/`slugId` are
  exact JS mirrors of the server's `_derive_run_id`/`_slug_id`, parity-pinned in `run-core.test.js`), and
  `solve()` always sends the previewed id explicitly, so the shown id is the filed id.
- **`toolscout-eval` workspace member** — an OFFLINE, reward-free, 4-category (TF/TA/TG/PA) 0–10
  LLM-as-judge evaluation scorer reproducing ATLAS's evaluation methodology. It lives OUT of the toolscout
  wheel, is a one-way reader of the trace/`TaskResponse` contract (`toolscout` never imports it), reuses
  `rlm_kit.tools.make_model_tool`, and emits a scorecard of per-category MEANS (TF primary) — never a
  composite reward. Measurement flows trace → judge → report (terminal); it never feeds back into a
  trace/dataset/export. This is compatible with "trajectories, never reward" precisely because the paper
  itself mandates the eval judge be separate from the training reward.
- **Offline CI-ready test posture**: dspy-bearing paths use `DummyLM` + rlm-kit's `ScriptedInterpreter`;
  MCP paths use in-process fakes; the demo catalog needs no network. Two gates — `uvx ruff check .`
  (line-length 110) and `uv run --group dev python -m pytest -q` — plus the studio's and eval's own suites.

### Documentation

- **A lean top-level `README.md` + a deep `toolscout/README.md` guide** (mirroring rlm-kit's
  `README.md` / `rlm_kit/README.md` split). The overview covers what it is, install, run, capability
  bullets, and deep-links; the guide holds the reference — including **"What ATLAS is — and what it
  isn't"** (ATLAS is not MCP-specific — MCP is its testbed, and the method generalizes to any large
  toolspace — and not only fine-tuning: three separable pieces, the inference architecture, the rubric/
  SLM-judge methodology, and the RFT that learns them), a **"What toolscout implements — and what it
  leaves to you"** boundary, and a **"Reproducing the ATLAS experiments"** walkthrough.
