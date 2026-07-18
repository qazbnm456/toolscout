# toolscout

**Solve a task over a LARGE MCP toolspace with a small planner — a traced [ATLAS](https://arxiv.org/html/2603.06713v1)-style rollout harness built on [rlm-kit](https://github.com/qazbnm456/rlm-kit).**

https://github.com/user-attachments/assets/afa8fb71-8c41-4b47-acb8-9c4165345411

_The studio: hand it one task over a large MCP toolspace, watch the small planner discover servers and compute over tool results (ISL→ITL→PTC) live in the trace, then read the grounded, fabrication-checked outcome._

A small language model cannot hold the schemas of hundreds of tools in its context. ATLAS (Adaptive Tool
Loading and Scoped Context, Microsoft Research) shows how to make it work anyway: let the model
**discover** the toolspace progressively and **compute over tool results as code**. That method is general
to any large toolspace — MCP is its testbed. toolscout implements the **rollout + evaluation** side of it
as a downstream *consumer* of rlm-kit: it declares one `RLMTask`, adds four fixed meta-tools, and inherits
the sandbox, tracing, retry, and dataset export from the kit. (The reward-composition + RFT *training* side
is a separate project — see [the guide](toolscout/README.md#what-toolscout-implements--and-what-it-leaves-to-you).)

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
export TS_TOOLSPACE=./toolspace.example.json   # the curated no-key default (2 hosted, first-party servers)
toolscout solve "What is the most recent breach recorded in Have I Been Pwned?"
```

```
ANSWER:
  The most recent breach recorded in Have I Been Pwned is Fluke.

TOOLSPACE USE: servers_loaded=1 tools_used=1 described=1 calls(ok/fail)=1/0 turns=4
  servers: ['hibp']
  tools:   ['hibp:hibp_get_latest_breach']
```

That is the whole loop in one command: the planner picked the server out of the index (**ISL**), pulled
just that one tool's schema (**ITL**), called it and computed on the result in its REPL (**PTC**) — and the
answer is one no model can produce from training data. `TOOLSPACE USE` is re-sourced from the trace, not
self-reported.

```bash
# No external server at all — the built-in demo catalog (echo/math/memory/text; offline, deterministic):
toolscout solve "Use the memory server to store 'blue' under the key 'color', then read it back."

# Fully offline — no model, no Deno, no network:
toolscout render output/traces/task.jsonl task     # re-render a response from a trace
toolscout export "output/traces/*.jsonl" ds.json   # reward-free SFT/RL dataset
toolscout rubric "summarize the repo's open issues" # decompose a task into a rubric
```

With no `TS_TOOLSPACE` set, toolscout falls back to that built-in demo catalog, so `solve` works
end-to-end without any external MCP server once you have model creds + Deno. Point `TS_TOOLSPACE` at your
own MCP servers for the real path. (Pick a task the planner genuinely *needs* a tool for — asked to compute
`6 * 7`, a competent planner just does it in its REPL and touches no server at all, which is correct PTC
behaviour but demonstrates nothing.)

## What's in the box

- **The ATLAS discovery loop as four fixed meta-tools.** `list_servers` → `load_server` (ISL) →
  `describe_tools` (ITL) → `call_tool` (PTC) — the whole toolspace is reached through these four, so every
  discovery decision lands in the trace.
- **Scaffolding over a heterogeneous toolspace.** Uniform Python signatures, argument coercion, fixable
  error strings, a declared-return + example-output hint, and an optional `MCPServer` call proxy.
- **Judgement-only SUBMIT + assemble-on-read.** The planner submits citations, not evidence; the facts are
  re-sourced from the trace on read, and fabrication is flagged.
- **The rubric carried as LABELS, never a reward.** TF/TA/TG/PA criteria + deterministic per-criterion
  facts + an opt-in judge tool — all `reward=None`. Scoring belongs to your downstream trainer.
- **A reward-free trajectory dataset.** SFT / planner-toolspace-ops / judge splits, exported from the trace.
- **An offline 4-category evaluation scorecard.** `toolscout-eval` reproduces ATLAS's LLM-as-judge
  evaluation (measurement only, separate from any training reward).
- **A trajectory studio.** An SSE server + web console that replays a run's ISL/ITL/PTC trajectory.

## Documentation — the guide

The deep documentation lives in [**`toolscout/README.md`**](toolscout/README.md):

- [What ATLAS is — and what it isn't](toolscout/README.md#what-atlas-is--and-what-it-isnt) — not MCP-only, not only fine-tuning; the three separable pieces.
- [The ATLAS mechanisms, mapped onto rlm-kit](toolscout/README.md#the-atlas-mechanisms-mapped-onto-rlm-kit) — ISL/ITL/PTC/scaffolding/rubric, and where each lives.
- [What toolscout implements — and what it leaves to you](toolscout/README.md#what-toolscout-implements--and-what-it-leaves-to-you) — the done/deliberately-not-done boundary.
- [Reproducing the ATLAS experiments](toolscout/README.md#reproducing-the-atlas-experiments) — what you get free vs. what you must bring (toolspace, tasks, reward function, RFT trainer).
- [`trajectories, never reward`](toolscout/README.md#trajectories-never-reward) — how an RFT paper fits a rollout kit.
- [The toolspace](toolscout/README.md#the-toolspace-external-mcp-servers), [model roles](toolscout/README.md#model-roles), [judgement-only SUBMIT](toolscout/README.md#judgement-only-submit--assemble-on-read), and [evaluation](toolscout/README.md#evaluation-toolscout-eval) — the operational surfaces.
- [Layout](toolscout/README.md#layout) — what each module owns.

## Relationship to rlm-kit

toolscout **vendors nothing** — it consumes rlm-kit's public surface (`RLMTask`, the trace schema, the
exporters, `make_model_tool`, `ClaudeAgentLM`) and extends it the sanctioned way: subclass `RLMTask`,
add tools via the base/wrap split, read results through the trace. See [`VENDOR.md`](VENDOR.md).

## Develop

```bash
uvx ruff check .                         # lint (ruff defaults, line-length 110)
uv run --group dev python -m pytest -q   # the package suite — fully offline (no model, no Deno, no network)
```

dspy-bearing paths use `DummyLM` + rlm-kit's offline forward harness or skip; the toolspace defaults to the
built-in demo catalog. The `studio/` and `eval/` workspace members carry their own suites — see
[`CLAUDE.md`](CLAUDE.md) for those commands and the invariants when editing.

## License

MIT — see [`LICENSE`](LICENSE).
