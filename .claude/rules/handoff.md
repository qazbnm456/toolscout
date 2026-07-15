# Context preservation (read before auto-compacting)

toolscout routes durable knowledge into its tracked docs — keep using them, and when the conversation is
about to compact, preserve only what they do NOT already hold:

- **Stable invariants** → the **Invariants** section of `CLAUDE.md`.
- **Resolved decisions / shipped changes** → `CHANGELOG.md` (under the current version).
- **Open / proposed work** → the issue tracker, or the CHANGELOG's `[Unreleased]` section (the
  promote-back candidates live there).

So a handoff summary should carry the *in-flight session state* those files miss. Prioritize, in order:

1. **Decisions we agreed on this session** not yet in CHANGELOG/CLAUDE — design choices ("the four
   meta-tools stay the ONLY toolspace path — no per-MCP-tool dspy tool", "the rubric is LABELS in
   run_start meta, scoring stays downstream", "connect stays eager — a mid-loop subprocess spawn hangs
   asyncio", "the judge is a TOOL, never the sub-LM intercept") and the *reason*. Promote durable ones into
   CLAUDE.md (invariant) or CHANGELOG.md (change) before they fade.
2. **Files / symbols changed**, as `path:symbol` one-liners on the *final* shape — e.g.
   `toolspace.py:make_call_tool_tool — PTC, records call_tool with reason/server/result`,
   `assemble.py:assemble_outcome — re-sources servers/tools, flags unbacked_* / cited_unknown`,
   `rl_export.py:export_dataset — reward=None; rubric_signal rides as per-run labels`. Drop diffs and
   intermediate revisions.
3. **Current status.** What passes the suite (and the count), what's broken, last command + result. One
   paragraph. (Suite: `uv run --group dev python -m pytest -q` from the repo root, the studio's
   `uv run --group dev python -m pytest studio/tests`, plus `uvx ruff check .`.)
4. **Open suggestions / TODOs** not yet tracked — mark each `proposed`, `accepted-not-done`, or
   `rejected`; move durable ones to the issue tracker or the CHANGELOG `[Unreleased]` section.
5. **The seams' status.** (a) the toolspace backend — demo catalog vs a live `McpCatalog` (and whether
   `connect="lazy"` was touched); (b) the opt-in `rubric_judge` — off vs enabled, and its endpoint; (c) the
   subscription path — proxy-only vs a `claude-agent-sdk/` role; (d) the rlm-kit dep — still the
   commit-pinned git source or overlaid editable (`../rlm-kit`); (e) any promote-back candidate that moved.
   A resumed session must not re-open a seam that moved.
6. **In-flight user intent + acceptance criteria** for this session. Without it a resumed session drifts.

**Do NOT preserve** (reconstructable / already durable):

- Anything already in `CLAUDE.md`, `CHANGELOG.md`, `README.md`, `VENDOR.md`, `.env.example`, or
  `pyproject.toml`.
- Tool-call transcripts, `grep` output, file listings, full file contents readable from disk.
- Step-by-step exploration narration; speculative reasoning that led to no decision.

**Format for a handoff summary** (use when compaction is imminent or the user asks for a recap):

```
## Session state
- Goal: <one sentence>
- Status: <what passes the suite, what doesn't, last command + result>
- Seams: <toolspace backend | rubric_judge | subscription | rlm-kit dep — one line each if touched>

## Decisions
- <decision> — <why>   (→ promote to CLAUDE.md invariant / CHANGELOG.md)

## Changed
- <path:symbol> — <what & why>

## Open
- [proposed|accepted-not-done|rejected] <item>   (→ issue tracker / CHANGELOG [Unreleased] if durable)
```

Keep it under ~40 lines. If something fits one of the tracked docs, put it THERE instead of the summary.
