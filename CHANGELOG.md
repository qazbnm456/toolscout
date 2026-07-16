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

### Added

- **`toolscout-eval` workspace member** — an OFFLINE, reward-free, 4-category (TF/TA/TG/PA) 0–10
  LLM-as-judge evaluation scorer reproducing ATLAS's evaluation methodology. It lives OUT of the toolscout
  wheel, is a one-way reader of the trace/`TaskResponse` contract (`toolscout` never imports it), reuses
  `rlm_kit.tools.make_model_tool`, and emits a scorecard of per-category MEANS (TF primary) — never a
  composite reward. Measurement flows trace → judge → report (terminal); it never feeds back into a
  trace/dataset/export. This is compatible with "trajectories, never reward" precisely because the paper
  itself mandates the eval judge be separate from the training reward.
- `rubric.validate_rubric` — a DETERMINISTIC structural lint of a rubric (category coverage, unique names,
  non-empty + plausibly-observable descriptions), NOT a semantic-quality judge.
- `toolscout rubric-batch <taskset> <out-dir>` — batch per-task rubric generation for the rollout
  workflow (generate offline once, then `solve --rubric <that task's rubric>` so a live run's labels vary
  per task instead of carrying the generic skeleton).

### Changed

- The config judge-sentinel guard now fires ONLY when the judge is ENABLED (`TS_ENABLE_JUDGE=1`). With the
  judge off (the default) its model is inert, so a Claude-subscription planner+specialist is a valid
  config — surfaced by the first live subscription run against a real MCP server.

### Removed

- **`cited_criteria`** from `TaskOutcome` and `AssembledOutcome` (with `cited_unknown`). The agent never
  sees the rubric at inference (as in ATLAS), so a policy self-citation of criterion names is meaningless
  and produced a spurious fabrication tell; the per-criterion signal is the deterministic `criteria_facts`.
  The fabrication tells are now `unbacked_servers` / `unbacked_tools` only, and `run_labels` no longer
  carries `cited_unknown` (a dataset-shape note for a downstream training consumer).

## [0.1.0] - 2026-07-16

The initial release: a task string in → a structured, grounded outcome over a large MCP toolspace out,
recorded as a reward-free trajectory. The ATLAS approach mapped onto rlm-kit, fully offline-testable.

### Added

- **The ATLAS discovery loop as four fixed meta-tools** (`toolspace.py`): `list_servers` (ISL — the server
  index, no schemas) → `load_server` (ISL — materialize one server) → `describe_tools` (ITL — pull a few
  tool schemas just-in-time) → `call_tool` (PTC — invoke a loaded tool, return a native REPL value). All
  sync, explicit-param, `fn.__name__`-pinned; the whole toolspace routes through these four, so every
  ISL/ITL/PTC decision lands in the trace as a `tool_call`. PTC itself is inherited free — `dspy.RLM` is
  already a persistent tools-as-code REPL.
- **Scaffolding** (`scaffolding.py`): heterogeneous tool schemas normalized to uniform Python signatures,
  argument coercion, and informative (fixable) error strings the planner recovers from in-loop.
- **Rubric decomposition carried as LABELS** (`rubric.py`, `schema.py`): a task is decomposed into criteria
  across the four ATLAS categories (Task Fulfillment / Tool Appropriateness / Tool Grounding / Parameter
  Accuracy) and stored in `run_start` meta — structure, never a score. `default_rubric` is a deterministic,
  model-free skeleton (offline demo + CI); `generate_rubric` decomposes via one host-side frontier-model
  call. `criteria_facts` re-sources deterministic per-criterion observations from the trace.
- **Judgement-only SUBMIT + assemble-on-read** (`schema.py`, `assemble.py`): `TaskOutcome` is
  citation-only (no field for raw outputs, scores, or reward), so the policy cannot self-report evidence.
  `assemble_outcome` re-sources `servers_loaded` / `tools_used` from the trace and flags fabrication in
  `unbacked_servers` / `unbacked_tools`. Runs at every read path (live, render, export). The SUBMIT has
  deliberately no `cited_criteria` — the rubric is a trainer/eval-side artifact the agent never sees at
  inference (as in ATLAS), so the per-criterion signal is the deterministic `criteria_facts`, never a
  policy self-citation.
- **Reward-free dataset export** (`rl_export.py`): trajectory splits (SFT turns / planner toolspace-ops /
  judge), per-run intrinsic labels + objective metrics, and the ATLAS rubric signal (the rubric + its
  deterministic per-criterion facts + the opt-in judge's observations) — all with `reward=None`. Reward,
  scoring, credit assignment, and GRPO/SFT live in a separate fine-tuning project.
- **The opt-in `rubric_judge` tool** (`judge_tool.py`, OFF by default): a verify-before-finalize self-check
  the planner CHOOSES to call, built on rlm-kit's `make_model_tool` (chat → transient-retry → validate →
  circuit-break). It emits per-criterion OBSERVATIONS (a note + met/unmet) as labels, never an aggregate
  reward. Its endpoint is always a separate OpenAI-compatible client, never the subscription.
- **Model ROLES by env** (`config.py`): planner (`TS_ROOT_LM`), specialist (`TS_SUB_LM`, reached via
  `llm_query`, intercepted for tracing only), judge (`TS_JUDGE_LM`). No hardcoded model name.
- **Claude Pro/Max subscription support** (`agent._maybe_subscription_lm`, opt-in `[subscription]` extra):
  give the planner or specialist a `claude-agent-sdk/<id>` model to run it on a personal Claude login via
  rlm-kit's `ClaudeAgentLM`, injected through `configure(main_lm=…, sub_lm=…)`. Imported lazily so a
  proxy-only install never pulls the SDK. The judge may not use the sentinel (`config.from_env` rejects it,
  explicit or inherited) — mixed auth by design.
- **The toolspace backends** (`catalog.py`, `mcp_toolspace.py`): a `Catalog` abstraction with a built-in
  **demo catalog** (`echo` / `math` / `memory` / `text` — offline, deterministic, so `solve` works
  end-to-end with only model creds + Deno) and an `McpCatalog` over external MCP servers (client-only;
  eager host-side connect pre-run; a per-server sync bridge). Untrusted server text is length-capped.
- **The CLI** (`cli.py`): `solve` (live), `render` / `export` / `rubric` (offline). `run()` is the
  programmatic entry — records the trace, assembles on read, writes the response, and never raises on a
  failed run (it writes a `status=failed` response instead).
- **Progressive-disclosure skills KB** (`toolscout/skills/`, `read_skill`): toolspace tactics (planning,
  server selection, when to describe a tool, PTC/REPL discipline, error recovery, grounding) pulled
  just-in-time — knowledge only, no script execution. Ship in the wheel via `packages = ["toolscout"]`.
- **The studio** (`studio/`, a uv workspace member, NOT in the `toolscout` wheel): an SSE server + web
  frontend that serves a run's `TaskResponse`, replays its trace, and renders the ISL/ITL/PTC trajectory,
  the answer, and the rubric criteria facts. It reads this package's trace/`TaskResponse` contract; its web
  stack stays behind its own `live` extra.
- **Offline CI-ready test posture**: dspy-bearing paths use `DummyLM` + rlm-kit's `ScriptedInterpreter`;
  MCP paths use in-process fakes; the demo catalog needs no network. Two gates — `uvx ruff check .`
  (line-length 110) and `uv run --group dev python -m pytest -q` — plus the studio's own suite.
