# Context preservation (read before auto-compacting)

toolscout keeps durable knowledge in its tracked docs. When the conversation is about to compact, preserve only what they do NOT already hold:

- **Stable invariants** → [`docs/INVARIANTS.md`](INVARIANTS.md).
- **Resolved decisions / shipped changes** → `CHANGELOG.md`, under the current version.
- **Open / proposed work** → the issue tracker, or the CHANGELOG's `[Unreleased]` section.

A handoff summary carries the *in-flight session state* those files miss. Prioritize, in order:

1. **Decisions agreed this session** that are not yet in `CHANGELOG.md` or `docs/INVARIANTS.md`, with the *reason*. Examples: "the four meta-tools stay the ONLY toolspace path, no per-MCP-tool dspy tool", "the rubric is LABELS in run_start meta, scoring stays downstream", "connect stays eager: a mid-loop subprocess spawn hangs asyncio", "the judge is a TOOL, never the sub-LM intercept". Promote the durable ones into `docs/INVARIANTS.md` (invariant) or `CHANGELOG.md` (change) before they fade.
2. **Files / symbols changed**, as `path:symbol` one-liners on the *final* shape, e.g. `toolspace.py:make_call_tool_tool: PTC, records call_tool with reason/server/result`. Drop diffs and intermediate revisions.
3. **Current status.** What passes the suites (and the counts), what's broken, and the last command with its result, in one paragraph. The gates are listed under "Verify" in `AGENTS.md`.
4. **Open suggestions / TODOs** not yet tracked. Mark each `proposed`, `accepted-not-done`, or `rejected`, and move the durable ones to the issue tracker or the CHANGELOG `[Unreleased]` section.
5. **The seams' status:** (a) the toolspace backend: demo catalog or a live `McpCatalog`, and whether `connect="lazy"` was touched; (b) the opt-in `rubric_judge`: off or enabled, and its endpoint; (c) the subscription path: proxy-only or a `claude-agent-sdk/` role; (d) the rlm-harness dep: the exact PyPI pin or an overlaid editable (`../rlm-harness`). A resumed session must not re-open a seam that moved.
6. **In-flight user intent and acceptance criteria** for this session. Without them a resumed session drifts.

**Do NOT preserve** what can be reconstructed or is already durable:

- Anything already in `AGENTS.md`, `docs/INVARIANTS.md`, `CHANGELOG.md`, `README.md`, `VENDOR.md`, `.env.example`, or `pyproject.toml`.
- Tool-call transcripts, `grep` output, file listings, and full file contents readable from disk.
- Step-by-step exploration narration, and speculative reasoning that led to no decision.

**Format for a handoff summary** (use it when compaction is imminent or the user asks for a recap):

```
## Session state
- Goal: <one sentence>
- Status: <what passes the suites, what doesn't, last command + result>
- Seams: <toolspace backend | rubric_judge | subscription | rlm-harness dep: one line each if touched>

## Decisions
- <decision>: <why>   (→ promote to docs/INVARIANTS.md / CHANGELOG.md)

## Changed
- <path:symbol>: <what & why>

## Open
- [proposed|accepted-not-done|rejected] <item>   (→ issue tracker / CHANGELOG [Unreleased] if durable)
```

Keep it under about 40 lines. If something fits one of the tracked docs, put it THERE instead of in the summary.
