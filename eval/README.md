# toolscout-eval

An **offline, reward-free measurement harness** for toolscout: score recorded runs against the ATLAS
4-category LLM-as-judge (TF / TA / TG / PA, each 0–10, TF primary) and render a terminal scorecard.

**The boundary:** this is a reward-free measurement judge, separate from the rollout; it never feeds
training. Data flows one way — `trace → judge → report` — and the report is terminal (read by a human,
a CI gate, or a leaderboard render). It carries per-category **means only**, never a composite R(τ);
it never writes into a trace, a dataset, or a toolscout export; and `toolscout` never imports
`toolscout_eval` (test-enforced). This mirrors the paper's own split between the training reward and
the fixed external evaluation judge.

## Usage

```sh
# Score EXISTING traces against a taskset (offline with the stub judge; judge creds at most).
# The `--package toolscout-eval` is required from the repo root — a plain `uv run` won't install the
# workspace member (it's deliberately not a dependency of the toolscout wheel).
uv run --package toolscout-eval python -m toolscout_eval score "output/traces/*.jsonl" demo
uv run --package toolscout-eval python -m toolscout_eval score "output/traces/*.jsonl" taskset.example.json --out output/eval

# Run-then-score: drive `toolscout.run` per task (run_id = task id), then score the fresh traces.
# Needs the full solve stack (TS_* creds + Deno) on top of the judge env.
uv run --package toolscout-eval python -m toolscout_eval run taskset.example.json --out output/eval
```

Runs pair to tasks by the `run_id == task id` convention. The taskset argument is a JSON list of
`{id, task, reference}` objects, or the literal `demo` for the built-in offline set over toolscout's
demo catalog (echo/math/memory/text). The repo-root **`taskset.example.json`** is a committed starter of
REAL tasks (already the `{id, task, reference}` shape the eval consumes) — the same paired set `toolscout
solve` uses; point the eval straight at it. `reference` is the concrete expected-behavior description the
**judge alone** sees — the planner only ever gets `task` (ATLAS's fuzzy-vs-concrete split).

Everything is written under `--out` (default `./output/eval/`): `report.json` plus, for `run`, the
toolscout traces/responses it produced.

## Judge environment (`TSEVAL_*`)

The external judge is role-based and swappable — no model name is hardcoded. With no `TSEVAL_MODEL`
set (or with `--stub`), the deterministic stub judge runs instead: fully offline, zero creds, fixed
mid-scale scores — the CI path.

```sh
# The eval judge — an o4-mini-class model on any OpenAI-compatible endpoint (needs the `judge` extra).
TSEVAL_MODEL=            # judge model id; empty = use the offline stub judge
TSEVAL_BASE_URL=         # OpenAI-compatible base URL (empty = the openai default)
TSEVAL_API_KEY=          # API key for that endpoint
TSEVAL_TIMEOUT=60        # per-call hard timeout, seconds
```

Every `report.json` pins `judge_model` + `prompt_version`, so a number is reproducible and comparable.
Runs the judge cannot score (never finalized, endpoint failure, off-schema output) are reported as
`unscored` and excluded from the means — never silently a 0.

## Tests

```sh
uv run --package toolscout-eval --extra dev python -m pytest eval/tests   # offline: stub, demo catalog, no creds
```
