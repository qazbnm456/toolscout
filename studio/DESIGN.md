# toolscout-studio: visual & UX spec

The web frontend's design contract. Implementation (`static/{index.html,app.js,style.css,trajectory.js}`)
follows this file. Architecture is locked in the README (zero-build vanilla, a vendored font, served
same-origin by the FastAPI app). This doc owns the *look and feel* only.

## 1. Theme

**A toolspace console.** An instrument where a small planner DISCOVERS a large MCP toolspace and solves
one task over it. The story is the **ISL → ITL → PTC** loop: list the servers, LOAD the ones the task
needs, DESCRIBE a few tools, CALL them with args and compute on the results — ending in a **grounded
answer** whose heavy facts are re-sourced from the trace. Azure signal-light on deep slate, sharp 2px
geometry, mono-forward type. Energy: focused, instrumented. Not playful, not corporate.

Utility mode (no marketing hero): orient (header) , input (task) , status (live feed) , result
(answer + toolspace + task).

## 2. The signature: grounding ≠ self-report (made visual)

toolscout's load-bearing invariant is that the planner SUBMITs a judgement + CITATIONS only; the heavy
facts (which servers loaded, which tools were called) are **re-sourced from the trace on read**, and a
cited criterion / claimed server / claimed tool with **no recorded event** is a **fabrication tell**. The
console must make this legible, so the frame alloy is keyed to the **trace-derived grounding, NOT the
planner's self-report** (a policy can claim it used tools it never touched; the trace cannot):

| derived state | frame alloy | when |
|---|---|---|
| **grounded** | green | `status == ok`, a non-empty `answer`, and NO fabrication tell |
| **flagged** | amber | an answer, but the self-report OVER-CLAIMS — `cited_unknown` ∪ `unbacked_servers` ∪ `unbacked_tools` is non-empty |
| **refusal / failed** | iron | `status` failed/refused, or no usable answer |

When the self-report over-claims — the planner says it loaded `payments` but the trace has no
`load_server(payments)`, or it cites a criterion the rubric never had — the card carries a prominent
**fabrication marker** (`⚠ the self-report is not fully backed by the trace … the facts below are
re-sourced from the trace`). That is toolscout's money shot: the answer is framed by what the run
ACTUALLY did, not what the policy claims it did. The tells also show as **red chips** in the Fabrication
tells module and as **fabrication wells** in the Toolspace view.

## 3. Palette

Dark default, with a **light theme** toggled from the header (`[data-theme="light"]` overrides the tokens;
persisted, seeded from `prefers-color-scheme`). Live tokens are `style.css :root` + `[data-theme=light]`
(source of truth).

```
--bg:#0a0e15  --surface-1:#131b28  --surface-2:#1b2534  --surface-3:#243247
--border:#313f52  --border-strong:#46586e
--text:#e9eef6  --text-dim:#a4b4c8  --text-faint:#6b7d92
--signal:#4d9fff  --signal-dim:#7bb5ff  --signal-glow:rgba(77,159,255,0.24)   /* THE brand accent (azure) */
--ok:#3fb950  --bad:#f85149  --warn:#d9a520
--specialist:#a371f7   /* the expensive sub-LM escalation (violet = rare/costly) */
--judge:#e3b341        /* the opt-in rubric judge (gold) */
--skill:#7d8fb3
/* card-alloy (the frame signature — keyed to DERIVED grounding, §2) */
--grounded-1:#56d364; --grounded-2:#2ea043; --grounded-glow:rgba(63,185,80,0.22)   /* answer, grounded */
--flag-1:#ffd56b; --flag-2:#b8860b; --flag-glow:rgba(255,204,85,0.28)              /* self-report over-claims */
--iron-1:#5a6672;  --iron-2:#2e3742;  --iron-glow:rgba(70,80,92,0.20)              /* refusal / failed */
```

Rules: `--signal` (azure) is for interactive + "live" things (links, the Solve button, ISL/list/created
chips). Card-alloy metals are ONLY on the card frame. Tool families carry their own accent (`--ok` for a
PTC call, `--bad` for a failure, `--specialist` violet, `--judge` gold, `--skill` slate). Do not
cross-use. Nested surfaces step (bg → surface-1 → surface-2 → surface-3) each with a 1px `--border`.

## 4. Typography

Mono-forward (a console reads as a terminal). `JetBrains Mono` (vendored woff2 400/700) for the wordmark,
all labels, ids, stats, badges, tool calls, the toolspace ops. System sans
(`ui-sans-serif,-apple-system,…`) ONLY for human prose: the `answer`, `summary`, criterion notes, refusal
detail, and the task text. The mono-frame / sans-prose contrast is the hierarchy.

## 5. Components

### 5.1 Header
`⌖ toolscout studio` wordmark (`⌖` in `--signal`). Right: three role chips `planner / specialist / judge`,
each a mono pill showing the configured model name from `GET /v1/config` on page load (steady `--signal`
dot when set, NO pulse; truncates with ellipsis). Then a theme toggle (`☾`/`☀`).

### 5.2 Task input (left rail, top) — no feed
`▾ TASK` panel: a `<textarea>` (sans, for the human task), a **⚡ load an example task** control (from
`GET /v1/examples`, wired to the offline demo toolspace so a live solve resolves), a **Solve ▶** button
(primary, `--signal`, disabled until the task is non-empty / while a run streams — "Solving…"), and a
note that live solve needs the `live` extra + Deno + creds. A divider `or load a past run` then a run-id
input bound to a `<datalist>` from `GET /v1/runs` + Load.

### 5.3 Live feed (left rail, fills remaining height)
`▾ TRAJECTORY LOG`. A scroll container, newest row at the bottom, live (each SSE event appends),
auto-scroll only when already at the bottom. Each row: an inline-SVG icon chip tinted by family, a mono
primary line, a right-meta. Families:
- `task.servers.listed` , rows-icon , `--signal` (label **List servers** · meta `N servers`)
- `task.server.loaded` , download-into-box , `--signal`/`--bad` (label **Load server** · meta `server`; ISL)
- `task.tools.described` , doc-icon , `--signal-dim` (label **Describe tools**; ITL)
- `task.tool.called` , bolt , `--ok`/`--bad` (label **Call tool** · meta `server:tool`; failure → `· reason`; PTC)
- `task.judge` , checked-circle , `--judge`/`--bad` (label **Rubric judge**)
- `task.skill.read` , book , `--skill` (label **Read skill**)
- `task.specialist.escalation` , radial , `--specialist` (label **Ask specialist**)
- `task.run.created` / `completed` , flag , `--signal` (**Solve started** / **Finalized**)

Live plan.step reasoning is NOT in this feed (toolscout's `main_step` flushes post-hoc — see the README);
it lives in the Trajectory drawer. The live feed is the ACTION stream, which for a toolspace agent is the
core story (which servers it loaded, which tools it called, did it escalate).

### 5.4 The result: middle stage + right modules
Page-level **3 columns**: process rail | stage | modules.

**Middle stage:** ONE **grounding-alloy card** (frame per §2), fixed at page height — content scrolls
INSIDE the card, the page never grows — with a top-right **Answer / Toolspace / Task** switch, in read
order:
1. **Answer view** (the landing view): the derived-state headline (GROUNDED / FLAGGED / FAILED), a badge
   row (servers · tool calls · turns · escalations · judge ✓ · fabrication tells), the **fabrication
   marker** when the self-report over-claims (§2), then the `answer` (sans, in a well) and `summary`.
2. **Toolspace view** (the star — ALWAYS present): the ISL/ITL/PTC exploration from `toolspace_ops`. An
   `index seen` line (the servers listed), then per server a well (`--signal` left border when LOADED)
   with its tool names, its DESCRIBED tools (ITL), and each CALL as `tool(args) → result` (green well) or
   `tool(args) ✕ reason` (red well) — the PTC timeline. Then any **fabrication wells** (servers/tools the
   self-report claimed that the trace does not back). When no trace-derived ops exist (a replay without a
   trace), it falls back to the flat `servers_loaded`/`tools_used` chips so it is never blank.
3. **Task view** (always, once a run is on screen): the task string under solve (sans, in a well).

**Right modules** (`.module`: thin top accent, uppercase label head, `--surface-1` body), in order:
1. `RUN TELEMETRY` (top-right — the run's signature, the family convention across the sibling consoles)
   (`process`): a `turns` headline, then a grid — turns · servers · tool calls · escalations (violet if
   >0). `rubric judge ran` as a chip when the opt-in judge fired.
2. `RUBRIC CRITERIA FACTS`: per criterion `[CAT] name (w=…)` + the deterministic `observed` facts (counts
   /ids pulled straight from the trace — a FACT surface, **not** a score). Then any **judge observations**
   (`[met]`/`[UNMET]` labels) below — labels only; the trainer scores.
3. `FABRICATION TELLS`: `cited_unknown` / `unbacked_servers` / `unbacked_tools` as red chips, grouped by
   kind. When empty → a green `✓ no fabrication tells — the self-report matches the trace` note.
4. `SUMMARY`: the one-line recap, a closing module.

### 5.5 States (every state explicit)
| status | frame | body |
|---|---|---|
| `ok` (no tell) | green | GROUNDED headline, the answer, the toolspace ops, criteria facts |
| `ok` (over-claims) | amber | FLAGGED headline, the fabrication marker + red tell chips, the answer still shown |
| `refused` | iron | REFUSED refusal card + reason + the counters gathered |
| `failed` | iron | FAILED refusal card ("run did not finalize") + detail + the counters gathered |

### 5.6 Empty / running / error
- Empty: a dim scope/crosshair placeholder: "Solve a task over the toolspace: type one, load an example,
  or open a past run." The right column collapses (`.no-meta`).
- Running: a skeleton card (iron frame) + "Solving…" in the stage; the right column is empty (`.no-meta`).
  The live activity IS the Trajectory log (left rail), which streams actions as they happen — the run's
  telemetry + toolspace render in the right/stage once the result lands.
- Stream error mid-run: the backend emits a `failed` response → render the refusal card. Never blank.
  (Even without the `live` extra, the worker completes the SSE with a `failed` refusal — the stream never
  hangs.)

### 5.7 Trajectory drawer (bottom sheet)
Replays a finished run turn by turn — `GET /v1/runs/{id}/iterations`. Opened by a `▤ Trajectory` handle
once a run is on screen. Built on rlm-kit's `trace/v1` contract (additive-only within v1, so it degrades
gracefully on older traces). Tool timeline (segment width ∝ `duration_s`, colored by family
list/load/describe/call/judge/skill/specialist), left nav of turns, a detail pane (Init → the task +
instructions + model roles + toolspace + budgets + rubric count; a turn → reasoning + REPL; a tool → its
structured content — list index, load tool names, describe result, call `args`→`result`, judge
observations, specialist Q→A) and a replay transport `⏮ ▶/⏸ ⏭ N×` (pure decisions in `replay-core.js`,
unit-tested). Degrades gracefully; two-clocks honesty (`per_turn_timing`).

## 6. Depth / motion
2px geometry (radius 2px chips/buttons/panels, 3px card). Depth via surface steps + 1px hairlines; the
card gets one soft lift + its alloy frame. No glassmorphism, no purple/blue marketing gradients (the only
gradient is the card-alloy frame + its one-time sweep on mount, and the thin module top-accent). All
motion respects `prefers-reduced-motion`. Alloy sweep 600ms on card mount; feed-row enter 180ms; running
dot spin.

## 7. Do / Don't
Do: mono for structure, sans for prose; frame keyed to the TRACE-DERIVED grounding (§2), never the raw
self-report; make the TOOLSPACE view the star; render every state explicitly (refusal is first-class);
show the fabrication marker when the self-report over-claims; label criteria facts as FACTS, never scores.
Don't: no Inter, no centered-hero, no three-identical-cards, no purple/blue gradient; don't invent response
fields a run lacks (hide, don't fake); don't block the UI on the font (it degrades); don't key the frame
on the planner's self-report (that is the assemble-on-read violation this design exists to prevent);
don't present `criteria_facts` / `judge_observations` as a reward — they are labels.

## 8. Acceptance (in a browser)
1. First screen is unmistakably this product: a scope glyph + mono wordmark + model-name chips.
2. A demo like "What is 6 * 7, then upper-case 'ok'?" solves to a **green** GROUNDED card; the TOOLSPACE
   view shows `math` LOADED with `add(a=6,b=7) → 42` and `text` with `upper(...) → OK`.
3. A run whose self-report claims a server/tool the trace lacks is an **amber** FLAGGED card with the
   fabrication marker + red tell chips; the answer still shows, framed by the trace facts.
4. The TOOLSPACE module is the visual star, rendering the ISL/ITL/PTC ops grouped by server.
5. `failed`/`refused` show the iron refusal card with the reason + the counters gathered.
6. The live feed streams the action families (list/load/describe/call/judge/skill/specialist); newest at
   bottom.
7. The `▤ Trajectory` handle opens the drawer with a working timeline + replay transport.
8. No overflow at 375px; the grounding-alloy frame survives mobile.
