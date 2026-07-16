# toolscout — the guide

The deep documentation for toolscout. The top-level [`README.md`](../README.md) is the lean overview
(what it is, install, run, the capability bullets); this file is the full reference — what ATLAS actually
is, what toolscout implements of it, what it deliberately leaves to you, and every surface in detail.

- [What ATLAS is — and what it isn't](#what-atlas-is--and-what-it-isnt)
- [The ATLAS mechanisms, mapped onto rlm-kit](#the-atlas-mechanisms-mapped-onto-rlm-kit)
- [What toolscout implements — and what it leaves to you](#what-toolscout-implements--and-what-it-leaves-to-you)
- [Reproducing the ATLAS experiments](#reproducing-the-atlas-experiments)
- [`trajectories, never reward`](#trajectories-never-reward)
- [The toolspace (external MCP servers)](#the-toolspace-external-mcp-servers)
- [Model roles](#model-roles)
- [Judgement-only SUBMIT + assemble-on-read](#judgement-only-submit--assemble-on-read)
- [Evaluation (`toolscout-eval`)](#evaluation-toolscout-eval)
- [Layout](#layout)
- [Relationship to rlm-kit](#relationship-to-rlm-kit)

## What ATLAS is — and what it isn't

toolscout is built on **ATLAS** (Adaptive Tool Loading and Scoped Context), from *Scaling Agentic
Capabilities, Not Context: Efficient Reinforcement Finetuning for Large Toolspaces* (Gupta, Vajreshwari,
Pandya, Magazine, Nambi, Awadallah — Microsoft Research,
[arXiv:2603.06713v1](https://arxiv.org/html/2603.06713v1)). Two things about its scope are easy to get
wrong, and they decide what toolscout can and can't give you:

**It is not MCP-specific.** The title says *Large Toolspaces*, not "MCP". The method — expose a compact
capability index, materialize tool schemas only when needed, and represent long-horizon tool use as
executable code instead of turn-by-turn JSON — is a general answer to "the toolspace is too big to fit in
a small model's context". MCP is the **testbed**, not the essence: every experiment in the paper runs on
MCP (≈300 synthetic-but-realistic tasks across 28 live MCP servers), because MCP is where "one request
spans hundreds of tools" is a real, live problem. The ideas transfer to any large keyed tool surface.

**It is not only fine-tuning.** ATLAS is, at its core, a reinforcement-finetuning (RFT) paper — that is
its headline contribution and its 4B-approaches-frontier result. But it bundles **three separable
pieces**, and only the last one needs training:

| ATLAS piece | Needs training? | What it is |
| --- | --- | --- |
| **(a) The inference architecture** — ISL / ITL / PTC / scoped context | **No** — runs on any model | The staged discovery loop + code orchestration that bounds context growth. In the paper this is the *baseline* rows. |
| **(b) The reward + judging methodology** — rubric decomposition + the finding that a small open judge (Qwen3-30B) beats GPT-4o-based generic judging | Partly — rubrics are a training signal, but the *judging* is an evaluation method | Decompose "did it succeed?" into task-aligned criteria (completeness, grounding, tool appropriateness, parameter precision) so an SLM can score reliably. |
| **(c) RFT** — GRPO-style cold-start finetuning that *learns* (a)+(b) into the model weights | **Yes — this is the training** | The central claim: these behaviors should be *learned* via RL, not fixed by hand. A 4B SLM trained this way approaches frontier-agent performance. |

The paper's own framing (§1): context acquisition and execution structure are treated as *learnable
decisions, optimized through reinforcement learning rather than fixed architectural choices*. So the
scaffolding alone (an untrained model using the meta-tools) is the starting line; RFT is what closes the
gap to frontier agents.

## The ATLAS mechanisms, mapped onto rlm-kit

toolscout implements the **inference architecture** (a) and the **rubric/judging methodology** (b) as a
downstream consumer of rlm-kit — one `RLMTask`, four fixed meta-tools, everything else inherited.

| ATLAS component | toolscout | where |
| --- | --- | --- |
| **PTC** — Programmatic Tool Calling (tools as code in a persistent REPL) | inherited — `dspy.RLM` *is* a persistent tools-as-code REPL | rlm-kit |
| **ISL** — Iterative Server Loading | `list_servers()` → `load_server(name)` meta-tools; server selection is a recorded decision | `toolspace.py` |
| **ITL** — Iterative Tool Loading | `describe_tools([names])` — tool schemas disclosed just-in-time (with a declared return type + one example output) | `toolspace.py` |
| **Scaffolding** — normalize heterogeneous schemas + informative errors | uniform Python signatures, arg coercion, fixable error strings, the optional `MCPServer` call proxy | `scaffolding.py` |
| **Rubric decomposition** (TF / TA / TG / PA) | rubric generated host-side; per-criterion **facts** re-sourced from the trace | `rubric.py` |
| **SLM-as-judge evaluation** | the 4-category 0–10 scorer, `toolscout-eval` (a separate workspace member) | `eval/` |

Because the whole toolspace is reached through four fixed meta-tools, every ISL/ITL/PTC decision lands in
the JSONL trace as a `tool_call` — the exact signal ATLAS's rubric scores.

## What toolscout implements — and what it leaves to you

Nothing below is "can't". Each gap is a deliberate boundary — either an rlm-kit invariant (the kit
produces **trajectories, never reward**), or something that is inherently the operator's to supply (your
toolspace, your tasks, your models).

| ATLAS pipeline stage | In toolscout? | Why |
| --- | --- | --- |
| ISL / ITL / PTC / scaffolding over a real toolspace → a trajectory | **Yes, in full** | The whole point of the harness |
| Rubric criteria decomposition (TF/TA/TG/PA structure per task) | **Yes** — emitted as structured **labels** in `run_start` meta | Structure, not a score |
| Deterministic per-criterion facts from the trace | **Yes** — reward-free via `rl_export` | Observations, not `dᵢ∈[0,1]` |
| Optional in-trajectory rubric self-check | **Yes, opt-in** — a tool-LM the planner chooses to call | Recorded as a `tool_call`, emits per-criterion labels, never a reward |
| 4-category LLM-as-judge **evaluation** scorecard | **Yes** — `toolscout-eval` | Measurement, kept separate from the training reward (as the paper mandates) |
| Turning the rubric into a numeric **reward** (compose `dᵢ`, weights, credit assignment) | **No — deliberate** | rlm-kit invariant: trajectories, never reward. Every exporter carries `reward=None`. |
| The **RFT training loop** (GRPO cold-start, policy-weight updates) | **No — deliberate** | A separate fine-tuning project consumes the reward-free dataset |
| The paper's **task set + toolspace** (≈300 tasks, 28 servers) | **No — not bundled** | Your toolspace is your trust declaration; benchmarks are yours to bring |
| The paper's **models** (Qwen2.5-7B / Qwen3-4B policy, Qwen3-30B judge) | **No — not hardcoded** | Roles are set by env; no default vendor |

## Reproducing the ATLAS experiments

There are two ambitions, and toolscout takes you different distances on each.

**If you want to reproduce the *evaluation* (run trajectories and score them with the 4-category judge):**
toolscout gets you almost all the way. You bring:

- **A toolspace + tasks** — point `TS_TOOLSPACE` at your MCP servers and feed a task set (your own, or a
  reconstruction of the paper's ≈300 synthetic tasks / MCPBench-style held-out sets).
- **Models + a sandbox** — set the planner / specialist / judge roles (`TS_*`) and provide credentials +
  Deno (`brew install deno`).

Then: `toolscout solve …` produces trajectories, and `toolscout-eval score …` reports the per-category
(TF primary) scorecard — that *is* the paper's evaluation methodology.

**If you want to reproduce the full *training* experiment (actually finetune an SLM and draw the
before/after RL curves):** toolscout takes you through rollout + reward-free label export + evaluation, and
then you add the training half:

1. **A reward function** — compose a scalar (or per-step) reward from the rubric labels + judge
   observations toolscout emits (`dᵢ∈[0,1]`, weights, aggregation). toolscout ships the *ingredients*
   (labels/facts), never the recipe.
2. **An RFT trainer** — a GRPO-style (cold-start, group-relative advantage) loop that consumes toolscout's
   reward-free trajectory dataset + your reward function and updates the policy model's weights. This is
   the *separate fine-tuning project*.
3. **Model choices** — a policy SLM (the paper used Qwen2.5-7B / Qwen3-4B) and a judge SLM (Qwen3-30B).

The dividing line is exact: toolscout is the **rollout + measurement** stage; the **reward composition +
GRPO** stage lives downstream. That split is not a shortcut — it is how the paper itself separates the
evaluation judge from the training reward.

## `trajectories, never reward`

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

## The toolspace (external MCP servers)

Point `TS_TOOLSPACE` at a JSON list of MCP server specs (a **trust declaration** — server-authored
names/descriptions/schemas enter the planner's context as untrusted input, and are length-capped):

```json
[
  {"name": "fs", "description": "read-only filesystem", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]},
  {"name": "search", "description": "web search", "url": "https://mcp.example.com/mcp"}
]
```

By default servers connect **eagerly, host-side, before the run** (`TS_CONNECT=eager`, the proven path).
The opt-in `TS_CONNECT=lazy` is **per-transport** (a property of the underlying rlm-kit MCP client): a URL
(streamable-HTTP) server defers its connect to first `load_server` — bounded, and a wedged connect is
cancel-reaped by the kit — while a stdio server still connects eagerly (deferring a local subprocess spawn
buys nothing). Either way, `load_server` wraps the connect so a failure surfaces as fixable **text** (a
`connect_error` `tool_call`), never a raise into the RLM loop. toolscout is an MCP **client only**; it
never runs a server.

With no `TS_TOOLSPACE` set, toolscout runs against a small built-in **demo catalog** (`echo` / `math` /
`memory` / `text` — offline, deterministic), so `solve` works end-to-end without any external MCP server
once you have model credentials + Deno.

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

## Layout

```
toolscout/
  config.py        # ToolscoutConfig + from_env (dspy-free; model ROLES, TS_*)
  schema.py        # pydantic shapes: TaskOutcome (SUBMIT), AssembledOutcome, TaskResponse, rubric
  catalog.py       # the toolspace abstraction: Catalog/StaticCatalog, demo_catalog, load_catalog
  scaffolding.py   # normalize schemas → uniform signatures; arg coercion; informative errors; call proxy
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

## Relationship to rlm-kit

toolscout **vendors nothing** — it consumes rlm-kit's public surface (`RLMTask`, the trace schema, the
exporters, `make_model_tool`, `ClaudeAgentLM`) and extends it the sanctioned way: subclass `RLMTask`,
add tools via the base/wrap split, read results through the trace. See [`VENDOR.md`](../VENDOR.md) for the
full extension contract and the three sanctioned extension points.
