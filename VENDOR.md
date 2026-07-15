# Vendored / external dependencies

toolscout deliberately vendors **nothing**. It is a downstream *consumer* of
[`rlm-kit`](https://github.com/qazbnm456/rlm-kit): it consumes the kit's PUBLIC surface and extends it the
sanctioned way — it never forks the harness, never re-implements tracing, and never copies kit source into
this tree.

## What it consumes from rlm-kit (public surface only)

- **`RLMTask`** — subclassed once as `SolveTask` (declaration: `signature` / `output_field` /
  `output_model` / `instructions` / `tools`). Retry, validation, sandbox selection, and budget caps are
  inherited.
- **`configure` / `RLMConfig`** — the process-level wiring seam (`agent.setup`), including the
  `main_lm=` / `sub_lm=` injection points.
- **The trace schema (`trace/v1`)** and `rlm_kit.trace` helpers (`record_tool_call`, `load_events`,
  `group_by_run`, `EVENT_*`) — toolscout reads and writes the kit's wire format additively, never a fork
  of it.
- **The exporters** (`rlm_kit.dataset.export_actions` / `export_sft_turns`) — the reward-free dataset is
  built on these, with `reward=None`.
- **`make_model_tool`** (`rlm_kit.tools`) — the generic chat → transient-retry → validate → circuit-break
  base; the opt-in `rubric_judge` is toolscout's wrap over it (the base/wrap split — provider + validator +
  tracing here).
- **The skills loader** (`load_skills_as_tools` / `render_skills_manifest`) — progressive-disclosure
  tactics KB, knowledge-only.
- **The sub-LM tracing seam** (`intercept_sub_lm` / `get_sub_lm`) — the specialist is intercepted for
  tracing only.
- **`ClaudeAgentLM`** (optional `rlm-kit[subscription]`) — used to run the planner/specialist on a Claude
  Pro/Max subscription; imported lazily, injected through `configure(main_lm=…, sub_lm=…)`. Not vendored —
  it ships in the rlm-kit wheel.

## The three sanctioned extension points (and only these)

1. **Subclass `RLMTask`** — `SolveTask` (the declaration).
2. **Add tools the base/wrap way** — the four ISL/ITL/PTC meta-tools (`toolspace.py`) and the opt-in
   `rubric_judge` (`judge_tool.py`, over `make_model_tool`).
3. **Read results through the trace + exporters** — `assemble.py` re-sources facts from the trace;
   `rl_export.py` builds the dataset; `render.py` renders it. Never reach into a kit `_private` name.

Contrast with a hypothetical fork: copying rlm-kit's harness/tracing into this repo to tweak it would
duplicate the wire format, drift from the kit's invariants, and forfeit the ability to upstream fixes.
Instead, when a real seam is missing, toolscout adds a NAMED hook in rlm-kit and consumes it.

## How rlm-kit is pinned

rlm-kit is public but not yet on PyPI, so it comes in via a **commit-pinned git source**
(`[tool.uv.sources]` → GitHub, `branch = "main"`; `uv.lock` pins the exact commit). Never `pip install`
it. When co-developing the kit locally, overlay an **editable**
install (`uv pip install -e ../rlm-kit`) or bump the pinned ref after pushing, so a fix that surfaces here
can be promoted into the kit. Swap to a version spec once rlm-kit ships on PyPI.

## External boundaries toolscout crosses (all opt-in, none bundled)

- **The MCP toolspace** (`mcp_toolspace.py`): external MCP servers the operator declares via
  `TS_TOOLSPACE`. Client-only — toolscout never runs a server. Server-authored names/descriptions/schemas
  and tool outputs are untrusted LM context, length-capped.
- **The model endpoints** (planner / specialist / judge): the operator's own, by env (`TS_*`). No default
  vendor. The judge is always a separate OpenAI-compatible endpoint (never the subscription).
