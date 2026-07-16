# toolscout

**Solve a task over a LARGE MCP toolspace with a small planner — a traced [ATLAS](https://arxiv.org/html/2603.06713v1)-style rollout harness built on [rlm-kit](https://github.com/qazbnm456/rlm-kit).**

A small language model cannot hold the schemas of hundreds of tools in its context. ATLAS (Microsoft
Research) shows how to make it work anyway: let the model **discover** the toolspace progressively and
**compute over tool results as code**. toolscout implements that as a downstream *consumer* of rlm-kit —
it declares one `RLMTask`, adds four fixed meta-tools, and inherits the sandbox, tracing, retry, and
dataset export from the kit.

```
task ─▶ [planner (small LM) in a sandboxed REPL]
             │  list_servers()          ← ISL: the server index (no schemas)
             │  load_server("math")     ← ISL: materialize the servers you need
             │  describe_tools(["add"]) ← ITL: pull a few tool schemas, just-in-time
             │  r = call_tool("math","add",{"a":6,"b":7}); r*2   ← PTC: compute in the REPL
             ▼
      TaskOutcome  (judgement + citations only)
             │  assemble-on-read: re-source servers/tools from the trace, flag fabrication
             ▼
      TaskResponse  +  reward-free trajectory dataset  (rubric + per-criterion facts as LABELS)
```

## The ATLAS mechanisms, mapped onto rlm-kit

| ATLAS component | toolscout | where |
| --- | --- | --- |
| **PTC** — Programmatic Tool Calling (tools as code in a persistent REPL) | inherited — `dspy.RLM` *is* a persistent tools-as-code REPL | rlm-kit |
| **ISL** — Iterative Server Loading | `list_servers()` → `load_server(name)` meta-tools; server selection is a recorded decision | `toolspace.py` |
| **ITL** — Iterative Tool Loading | `describe_tools([names])` — tool schemas disclosed just-in-time | `toolspace.py` |
| **Scaffolding** — normalize heterogeneous schemas + informative errors | uniform Python signatures, arg coercion, fixable error strings | `scaffolding.py` |
| **Rubric decomposition** (TF / TA / TG / PA) | rubric generated host-side; per-criterion **facts** re-sourced from the trace | `rubric.py` |

Because the whole toolspace is reached through four fixed meta-tools, every ISL/ITL/PTC decision lands in
the JSONL trace as a `tool_call` — the exact signal ATLAS's rubric scores.

## `trajectories, never reward` — how an RFT paper fits a rollout kit

ATLAS is a **training** (rubric-based RFT) paper. rlm-kit's hardest invariant is that it produces
**trajectories, never reward**. toolscout resolves the tension by being the **rollout stage only**:

- The rubric is decomposed into criteria (Task Fulfillment / Tool Appropriateness / Tool Grounding /
  Parameter Accuracy) and carried in the trace as **labels** (`run_start` meta) — *structure*, never a
  score.
- Per-criterion **facts** (`rubric.criteria_facts`) are re-sourced deterministically from the trace
  (which servers loaded, which tools succeeded, argument errors) — observations, not `dᵢ∈[0,1]`.
- The optional `rubric_judge` self-check is a **tool** the planner calls (recorded as a `tool_call`); it
  emits per-criterion **observations** (a note + met/unmet), never an aggregate reward.

Reward composition, the rubric's numeric scoring, credit assignment, and GRPO/SFT live in a **separate
fine-tuning project**. Every exporter here carries `reward=None`.

## Install & run

toolscout uses [uv](https://docs.astral.sh/uv/). rlm-kit is pulled from git (pinned in `uv.lock`).

```bash
uv sync                       # planner/specialist over an OpenAI-compatible proxy
uv sync --extra judge         # + the opt-in rubric self-check + `toolscout rubric` generator
uv sync --extra subscription  # + run planner/specialist on a Claude Pro/Max subscription
```

Copy `.env.example` to `.env`, set the model roles, then:

```bash
# A live run needs model creds (TS_* env) AND a Deno sandbox: brew install deno
toolscout solve "what is 6 * 7, then uppercase the word 'ok'?"

# Offline — no model, no Deno, no network:
toolscout render output/traces/task.jsonl task     # re-render a response from a trace
toolscout export "output/traces/*.jsonl" ds.json   # reward-free SFT/RL dataset
toolscout rubric "summarize the repo's open issues" # decompose a task into a rubric
```

With no `TS_TOOLSPACE` set, toolscout runs against a small **built-in demo catalog** (`echo`/`math`/
`memory`/`text` servers — offline, deterministic), so `solve` works end-to-end without any external MCP
server once you have model creds + Deno.

### The toolspace (external MCP servers)

Point `TS_TOOLSPACE` at a JSON list of MCP server specs (a **trust declaration** — server-authored
names/descriptions/schemas enter the planner's context as untrusted input, and are length-capped):

```json
[
  {"name": "fs", "description": "read-only filesystem", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]},
  {"name": "search", "description": "web search", "url": "https://mcp.example.com/mcp"}
]
```

Servers connect **eagerly, host-side, before the run** (`TS_CONNECT=eager`) — a subprocess spawn *inside*
the RLM loop can hang asyncio. toolscout is an MCP **client only**; it never runs a server.

## Model roles

Three roles, configured by env, never hardcoded:

- **planner** (`TS_ROOT_LM`, the RLM root): the small, cheap orchestrator driving ISL→ITL→PTC.
- **specialist** (`TS_SUB_LM`, reached via `llm_query`): an expensive brain for a subtle sub-question;
  intercepted for tracing only, so every escalation is a `sub_call`.
- **judge** (`TS_JUDGE_LM`, the opt-in `rubric_judge` tool): a swappable rubric self-check on its own
  OpenAI-compatible endpoint. It may **not** use the subscription sentinel (mixed auth by design).

Give the planner or specialist a `claude-agent-sdk/<id>` model to run it on your personal Claude Pro/Max
subscription (rlm-kit's `ClaudeAgentLM`, the `[subscription]` extra) instead of a metered API.

## Judgement-only SUBMIT + assemble-on-read

The planner submits a `TaskOutcome` that is **judgement + citations only** — its `answer`/`summary` plus
reference lists (`servers_loaded`, `tools_used`). It structurally has **no field** for raw tool outputs or
a score. On read, `assemble_outcome` re-sources the heavy facts from the trace and cross-checks the
self-report: anything the planner claims but the trace does not back lands in `unbacked_servers` /
`unbacked_tools` — the fabrication tells. The policy cannot self-report evidence. (There is no
`cited_criteria`: the rubric is a trainer/eval-side artifact the agent never sees at inference, so the
per-criterion signal is the deterministic `criteria_facts`, not a policy self-citation.)

## Layout

```
toolscout/
  config.py        # ToolscoutConfig + from_env (dspy-free; model ROLES, TS_*)
  schema.py        # pydantic shapes: TaskOutcome (SUBMIT), AssembledOutcome, TaskResponse, rubric
  catalog.py       # the toolspace abstraction: Catalog/StaticCatalog, demo_catalog, load_catalog
  scaffolding.py   # normalize schemas → uniform signatures; arg coercion; informative errors
  toolspace.py     # the four ISL/ITL/PTC meta-tools (list_servers/load_server/describe_tools/call_tool)
  mcp_toolspace.py # McpCatalog — adapter over rlm-kit's rlm_kit.mcp.McpCatalog (external MCP; live path)
  rubric.py        # rubric generation + validate_rubric lint + deterministic criteria_facts (read-time)
  assemble.py      # re-source outcome from the trace; flag fabrication
  render.py        # human-readable outcome/response text
  response.py      # the TaskResponse envelope
  rl_export.py     # reward-free SFT/RL dataset export (rubric signal as labels)
  judge_tool.py    # the opt-in rubric_judge tool (make_model_tool)
  agent.py         # SolveTask(RLMTask) + INSTRUCTIONS + setup + subscription wiring
  cli.py           # solve / render / export / rubric / rubric-batch
  skills/          # progressive-disclosure tactics KB (read_skill)
studio/            # the visualization console (uv workspace member, not in the wheel)
eval/              # toolscout-eval — offline, reward-free 4-category (TF/TA/TG/PA) 0–10 judge scorer
```

## Evaluation (`toolscout-eval`)

The `eval/` workspace member reproduces ATLAS's evaluation: a 4-category (Task Fulfillment / Tool
Appropriateness / Tool Grounding / Parameter Accuracy) 0–10 LLM-as-judge that scores completed
trajectories and reports a per-category scorecard (TF primary). It is a **measurement** tool — it flows
`trace → judge → report` and never feeds a reward back into a trajectory or dataset (compatible with
"trajectories, never reward", which the paper itself mandates by keeping the eval judge separate from the
training reward). It lives out of the toolscout wheel and reads the trace/`AssembledOutcome` contract
one-way. Training (rubric reward → GRPO) lives in a separate downstream project, not here.

```bash
uv run --package toolscout-eval python -m toolscout_eval score "output/traces/*.jsonl" taskset.json
```

## Relationship to rlm-kit

toolscout **vendors nothing** — it consumes rlm-kit's public surface (`RLMTask`, the trace schema, the
exporters, `make_model_tool`, `ClaudeAgentLM`) and extends it the sanctioned way: subclass `RLMTask`,
add tools via the base/wrap split, read results through the trace. See `VENDOR.md`.

## License

MIT — see `LICENSE`.
