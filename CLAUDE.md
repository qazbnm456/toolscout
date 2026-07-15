# toolscout — agent guide

`toolscout` solves ONE task over a possibly LARGE MCP toolspace with a SMALL planner LM, as a traced
rollout harness. It is a downstream *consumer* of [`rlm-kit`](https://github.com/qazbnm456/rlm-kit) (a
scaffold over `dspy.RLM`, Recursive Language Models) and **vendors nothing** — it declares one `RLMTask`,
adds four fixed meta-tools, and inherits the sandbox, tracing, retry, budgets, and dataset export from the
kit. See `VENDOR.md` for the extension contract.

toolscout implements the **ATLAS** approach (Microsoft Research,
https://arxiv.org/html/2603.06713v1): a small planner cannot hold hundreds of tool schemas in context, so
it **discovers** the toolspace progressively — Iterative Server Loading (ISL) → Iterative Tool Loading
(ITL) — and **computes over tool results as code** in the sandboxed persistent REPL (Programmatic Tool
Calling, PTC, inherited free from `dspy.RLM`). `README.md` is the user-facing overview (the ATLAS mapping,
the "trajectories, never reward" resolution, layout, roles); this file is the invariants for anyone
editing the code.

One companion rule ships under `.claude/rules/`:

- `@.claude/rules/handoff.md` — what must survive context compaction, and how it routes into the tracked
  docs (invariants → this file, resolved changes → `CHANGELOG.md`). Read it before auto-compacting or when
  asked for a recap.

## Verify

- Run BOTH gates before pushing (the repo pins Python 3.13 via `.python-version`):
  - `uvx ruff check .` — lint (ruff defaults, line-length 110). It is NOT part of the pytest suite, so a
    green `pytest` is not enough on its own; keep it green as its own gate.
  - `uv run --group dev python -m pytest -q` — the package suite. Fully OFFLINE: no live model, no Deno, no
    network. dspy-bearing paths use `DummyLM` + rlm-kit's `ScriptedInterpreter` (the offline forward path)
    or skip; the toolspace defaults to the built-in demo catalog, and the `McpCatalog` backing is unit-
    tested offline with a fake `_ServerBridge` (`tests/test_mcp_toolspace.py`) — no live MCP server.
  - The **studio** (`studio/`, a uv workspace member) has its OWN suite — `uv run --group dev python -m
    pytest studio/tests` (or from `studio/`). It reads this package's trace/`TaskResponse` contract, so run
    it too when you touch `schema.py`, `response.py`, `render.py`, or the trace payloads it renders.
- A *live* `solve` run needs model creds (`TS_*` env, see `.env.example`) AND a Deno sandbox
  (`brew install deno`). `render` / `export` / `rubric` are fully offline. Don't run a live model in CI; it
  costs money. Before claiming done, actually run BOTH gates and paste the output.

## Running — always through the CLI

- **Drive runs via `cli` (`solve` / `render` / `export` / `rubric`), never an ad-hoc script.**
  `cli.run(task, …)` is THE programmatic entry: it resets + records `<out>/traces/{run_id}.jsonl`
  (`TraceRecorder` appends, so a re-run drops the stale file first), assembles the outcome from the trace
  on read, and writes `<out>/responses/{run_id}.json`. It NEVER raises on a failed run — a crash still
  writes an informative `status=failed` response. Don't drive `SolveTask` / `assemble_outcome` from a
  private script — extend `cli.py`.
- Offline re-derivation: `toolscout render <trace> <run_id>` re-renders a response; `toolscout export
  "output/traces/*.jsonl" ds.json` exports the reward-free dataset; `toolscout rubric "<task>"` decomposes
  a task into a rubric (deterministic default unless `TS_RUBRIC_LM` is set).

## Invariants — do not break

- **The sandbox is the security boundary (inherited from rlm-kit).** The default interpreter is the
  sandboxed `pyodide`/`deno`; the toolspace and every computation run only there. Tool RESULTS are native
  Python values the planner holds in REPL variables — data, never trusted instructions. Never route the
  interpreter to `local`; never weaken the kit's guard. `config.interpreter` defaults to `"pyodide"`.

- **Keep the dspy-free modules dspy-free.** `config.py`, `schema.py`, `catalog.py`, `scaffolding.py`,
  `toolspace.py`, `rubric.py`, `assemble.py`, `render.py`, `response.py`, `rl_export.py` must NOT import
  `dspy` at module top — they stay unit-testable in isolation (they use only stdlib + pydantic + the
  dspy-free rlm-kit modules `rlm_kit.trace` / `rlm_kit.tools` / `rlm_kit.dataset`). The heavier-dep modules
  are `agent.py` (dspy via `RLMTask`), `cli.py` (imports dspy lazily), `judge_tool.py` (dspy-free at top —
  built on `rlm_kit.tools.make_model_tool`; imports `openai` lazily inside the chat fn), and
  `mcp_toolspace.py` (dspy-free; imported LAZILY by `catalog.load_catalog` only when a real toolspace is
  configured, so the offline path never pulls the `mcp` SDK). **`import toolscout` must NOT import dspy**:
  `SolveTask` / `setup` / `run` / `solve_task` / `make_rubric_judge_tool` are lazy PEP-562 re-exports in
  `__init__.py` (`__getattr__`). Don't make them eager.

- **The four meta-tools are the ONLY way to reach the toolspace** (`toolspace.py`): `list_servers` (ISL:
  the server index, no schemas) → `load_server` (ISL: materialize one server) → `describe_tools` (ITL:
  pull a few tool schemas just-in-time) → `call_tool` (PTC: invoke a loaded tool, return a native value).
  dspy.RLM registers tools at CONSTRUCTION — there is no mid-run tool registration — so the whole
  toolspace routes through these four, and every ISL/ITL/PTC decision lands in the trace as a `tool_call`.
  Each is **SYNC** (dspy invokes tools with no `await`; an `async def` tool returns an un-awaited
  coroutine) and takes **EXPLICIT** params (no `*args`/`**kwargs` — dspy's stub generator turns a
  VAR_KEYWORD into a required positional). Each factory **PINS `fn.__name__`** to the exact name the prompt
  uses (dspy registers a tool under `fn.__name__`; a rename makes the model's call raise `NameError` in the
  loop). A tool ERROR is returned as informative TEXT the planner recovers from, never raised into the loop.

- **Judgement-only SUBMIT + assemble-on-read.** The planner's SUBMIT type `TaskOutcome` (`schema.py`) is
  **citation-only**: `answer`/`summary` plus reference lists (`servers_loaded`, `tools_used`,
  `cited_criteria`, optional `judge_call_id`). It structurally has **no field** for raw tool outputs,
  per-criterion met/unmet, a score, or a reward — so the policy CANNOT self-report evidence.
  `assemble.assemble_outcome` re-sources the heavy facts from the trace's `tool_call`s (successful
  `load_server`/`call_tool`), cross-checks the self-report, and flags fabrication: a claimed-but-unbacked
  server/tool lands in `unbacked_servers` / `unbacked_tools`, a cited criterion with no recorded rubric
  entry lands in `cited_unknown`. This assembly runs at EVERY read path (live `cli`, `render`, `export`),
  so labels are facts. Do NOT add an evidence/score field to the SUBMIT type or a second facts derivation.

- **rlm-kit's hardest invariant holds here: toolscout produces TRAJECTORIES, never reward.** ATLAS is a
  rubric-based RFT (*training*) paper; toolscout is the **rollout stage ONLY**. The rubric is decomposed
  into criteria across the four ATLAS categories (TF / TA / TG / PA) and carried as **LABELS** in the run's
  `run_start` meta (`rubric.rubric_to_meta`) — structure, never a score. Per-criterion
  `rubric.criteria_facts` are **deterministic observations** re-sourced from the trace (which servers
  loaded, which tools succeeded, argument errors), never `dᵢ∈[0,1]`. The OPT-IN `rubric_judge` is a
  **TOOL** the planner chooses to call (recorded as a `tool_call`); it emits per-criterion OBSERVATIONS (a
  note + met/unmet), never an aggregate reward. Every exporter passes `reward=None` (`rl_export.py`).
  Reward composition, the rubric's numeric scoring, credit assignment, and GRPO/SFT live in a SEPARATE
  fine-tuning project. A prompt/policy convention that improves rollout QUALITY is in scope; a reward is not.

- **MCP is CLIENT-ONLY; external servers connect EAGERLY, host-side, pre-run.** toolscout never IS an MCP
  server and never bundles one — `TS_TOOLSPACE` points it at someone else's servers (a JSON list of specs).
  The `McpCatalog` (`mcp_toolspace.py`) connects each server host-side BEFORE the run (`connect="eager"`,
  the default and proven path) — a live subprocess spawn INSIDE the RLM/asyncio loop can hang dspy
  (`connect="lazy"` is opt-in/experimental for that reason). Server-authored names, descriptions, and
  schemas — AND tool outputs — are **UNTRUSTED** LM context (a prompt-injection surface, like a fetched
  page); all rendered text is length-capped (`max_desc_chars`, `scaffolding._cap`). Each MCP call records
  exactly ONE `tool_call`, emitted by the `call_tool` meta-tool via `rlm_kit.trace.record_tool_call`; the
  catalog is a pure transport that records nothing itself.

- **The specialist (sub-LM) intercept is tracing-only; model-judgement is a TOOL.** The specialist
  (`TS_SUB_LM`, reached via dspy.RLM's built-in `llm_query`) is intercepted with `intercept_sub_lm` for
  TRACING ONLY — zero transforms — so every escalation lands as a `sub_call`. A model that GRADES the run
  against a rubric is an agentic judgement, so the `rubric_judge` is a **tool** (`judge_tool.py`, the
  base/wrap split over `rlm_kit.tools.make_model_tool`), never smuggled into the sub-LM intercept. Do NOT
  put a model-judgement in the intercept.

- **Models are ROLES, configured by env** (`config.py`): planner `TS_ROOT_LM`, specialist `TS_SUB_LM`,
  judge `TS_JUDGE_LM` (defaults to the specialist). Refer to them by role in code, docs, and the prompt —
  no hardcoded model name. A role whose model carries the `claude-agent-sdk/` sentinel
  (`SUBSCRIPTION_PREFIX`) runs on the user's Claude Pro/Max SUBSCRIPTION via rlm-kit's `ClaudeAgentLM`
  (opt-in `[subscription]` extra), imported LAZILY inside the sentinel branch only. The judge **may not**
  use that sentinel — it is a separate OpenAI-compatible endpoint, and `config.from_env` REJECTS a
  subscription judge model (explicit or inherited) with an actionable error (mixed auth by design).

- **The budget is HARD — `max_retries=1`, no whole-RLM retry** (set in `agent.setup`). One task = one
  trajectory, so the trace stays valid training data. `max_iterations` is a HARD budget, NEVER multiplied
  by an outer loop; `max_llm_calls` caps ONLY specialist (`llm_query`) escalations. An `RLMTaskError` is
  almost always infra (a planner-endpoint hiccup / adapter parse failure), NOT a schema bug — check the
  endpoint first.

- **The trace is a VERSIONED wire format (rlm-kit `trace/v1`) — additive-only.** The schema, the event
  types, and the envelope are rlm-kit's contract; offline readers (`render`, `export`, the studio) build on
  them. toolscout adds only OPTIONAL payload fields within v1 — e.g. `call_tool`'s `reason` / `server` /
  `result` / `ok`, and the `rubric` / role names / budgets carried in `run_start` meta. You may add an
  optional field; you may NOT remove, rename, or re-type an existing event type, envelope key, or
  established payload field (that silently breaks the studio + every dataset consumer). Any new per-run
  config that affects read-time derivation MUST ride in `run_start` meta (so `render`/`export` re-derive
  the SAME facts the live run saw — `max_iterations` already does, for `hit_iteration_cap`).

- **Keep the public surface vendor-neutral.** toolscout has no downstream consumers of its own yet; refer
  to any future one GENERICALLY ("a downstream trainer", "a consumer"). Never hardcode a
  downstream-of-toolscout project name, schema, or product term in the package, docs, or commit messages.
  Model names, endpoints, and the toolspace are the OPERATOR's own values, set by env — never baked in.

## Versioning

- Keep `pyproject.toml` `[project].version` and `toolscout.__version__` in sync. On a bump, fold the
  release's changes into `CHANGELOG.md` (under the new version).

## Relationship to rlm-kit / promote-back

- toolscout is the dogfooding consumer that drives rlm-kit's design loop: when toolscout forces a
  workaround, log the **reusable** gap and fix it GENERICALLY in the kit (the base/wrap split — a generic
  base + syntactic guard + factory in rlm-kit, the provider + tracing here). Never special-case toolscout
  in the kit; consumer-specific values (`TS_*` roles, the `TaskOutcome` schema, the rubric categories, the
  toolspace) stay HERE.
- Candidates toolscout surfaces to promote INTO rlm-kit (tracked in `CHANGELOG.md`):
  - **A multi-server MCP ISL/ITL catalog bridge.** rlm-kit's single-server bridge (`_MCPBridge`) and
    `mcp_tools` are private / the wrong shape (one server's tools as dspy.Tools) for a MANY-server
    progressive catalog, so `mcp_toolspace.py` carries a self-contained bridge. Generalizing it into the
    kit would let that module shrink to a thin adapter.
  - **A generic rubric / criteria-facts helper** (if it proves reusable) — the rubric-as-labels +
    deterministic per-criterion facts surface, with `category` kept an opaque string in the kit so the
    ATLAS TF/TA/TG/PA taxonomy stays toolscout's domain.
