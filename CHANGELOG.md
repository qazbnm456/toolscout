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

### Promote-back candidates (proposals to rlm-kit — not yet done)

These are reusable gaps toolscout surfaced while dogfooding the kit. They are PROPOSALS to promote INTO
rlm-kit generically (never a special-case for toolscout); until then they live self-contained here.

- **A multi-server MCP ISL/ITL catalog bridge.** `mcp_toolspace.py` carries its own per-server sync
  bridge (async `ClientSession` on a background thread/loop, `run_coroutine_threadsafe(...).result()`)
  because rlm-kit's single-server bridge (`_MCPBridge`) is private and its `mcp_tools` yields one server's
  tools as dspy.Tools — the wrong shape for a MANY-server *progressive* (load-on-demand) catalog. Proposal:
  a public multi-server catalog bridge in the kit, which would let `mcp_toolspace.py` shrink to a thin
  adapter. `proposed`.
- **A generic rubric / criteria-facts surface.** The rubric-as-labels decomposition + deterministic
  per-criterion facts (`rubric.py`) may be reusable beyond toolscout. Proposal: a kit-level rubric/criteria
  helper with `category` kept an OPAQUE string, so the ATLAS TF/TA/TG/PA taxonomy stays toolscout's domain.
  `proposed`.

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
  `unbacked_servers` / `unbacked_tools` / `cited_unknown`. Runs at every read path (live, render, export).
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
