# toolscout: agent guide

`toolscout` solves ONE task over a possibly LARGE MCP toolspace with a SMALL planner LM, as a traced rollout harness. It implements the ATLAS approach (https://arxiv.org/html/2603.06713v1): the planner *discovers* the toolspace progressively through four fixed meta-tools (list servers → load one → describe a few tools → call one), and computes over tool results as code in a sandboxed persistent REPL. It is a downstream consumer of [`rlm-harness`](https://github.com/qazbnm456/rlm-harness) and vendors nothing: one `RLMTask`, four meta-tools, and everything else (sandbox, tracing, budgets, dataset export) inherited from the kit. It produces TRAJECTORIES, never reward.

Python, managed with `uv`. The repo pins 3.13 via `.python-version`, and CI runs 3.11, 3.12 and 3.13. It is a uv workspace: the root package `toolscout` plus three members that are NOT in its wheel. `studio/` is a visualization console, `eval/` an LLM-as-judge scorer, and `synth/` an offline gold-answer taskset.

## Verify

Run all of these before pushing. CI gates on each, and a green `pytest` does not imply a green lint:

```bash
uvx ruff check .
uv run --group dev python -m pytest -q                                          # package (offline)
uv run --group dev python -m pytest studio/tests                                # studio
uv run --package toolscout-eval --extra dev python -m pytest eval/tests         # eval
uv run --package toolscout-synth --extra dev python -m pytest synth/tests       # synth
```

- **`--package` is load-bearing.** A plain root `uv run` does not install a member, and neither member is a dependency of `toolscout`. CI gets them via `uv sync --all-packages`.
- **Every suite is OFFLINE.** None uses a live model, Deno, the network or a live MCP server. dspy paths run through rlm-harness's `ScriptedInterpreter` + `DummyLM`.
- **Live runs.** A live `toolscout solve` needs model creds (`TS_*`, see `.env.example`) and a Deno sandbox (`brew install deno`). Never run one in CI: it costs money.
- **Before claiming a change is done,** run the gates and paste the output.

## Running: always through the CLI

Drive runs through `toolscout` (`solve` / `render` / `export` / `rubric`), or programmatically through `toolscout.cli.run(task, …)`, never an ad-hoc script around `SolveTask`. `cli.run` resets and records `<out>/traces/{run_id}.jsonl`, assembles the outcome from the trace on read, writes `<out>/responses/{run_id}.json`, and never raises on a failed run. Extend `cli.py` rather than bypass it.

## Before you change anything

**Read [`docs/INVARIANTS.md`](docs/INVARIANTS.md) before editing anything under `toolscout/`, `studio/`, `eval/` or `synth/`.** It is the authority on:
- the sandbox as the security boundary, including host-side sub-agents being off
- which modules stay dspy-free, and why `import toolscout` must not import dspy
- the four meta-tools as the only path to the toolspace (sync, explicit params, pinned names, errors as text)
- the citation-only SUBMIT with assemble-on-read, and the principled decline
- trajectories-never-reward, and why `eval/` and `synth/` don't break it
- MCP as client-only and eager-connect
- models as roles configured by env, and the hard budget
- the trace as an additive-only wire format
- a vendor-neutral public surface

**These moments have their own trigger:**
- **Touching the trace or response contract.** That means `schema.py`, `assemble.py`, `response.py`, `render.py`, `rubric.py`, or a trace payload. Also run the studio and eval suites; both are one-way readers of that contract.
- **Changing `synth/`'s generator or a template.** Regenerate with `toolscout-synth gen` and commit the data together with the code. Its suite fails on byte drift (`toolscout-synth verify`).
- **Bumping the rlm-harness pin.** Read the kit's CHANGELOG for every release in the range and check each entry against this repo. Several changes in the past needed code here (a new default, a reclassified outcome, a new event the studio must ignore).
- **Bumping the version.** `pyproject.toml` `[project].version` and `toolscout.__version__` stay in sync, and the release's changes fold into `CHANGELOG.md` under it.

**There is no `CLAUDE.md`.** Claude Code reads this file when a project has none. Adding one, at the root or in any directory, would make Claude read that file instead, and this guide would go unread there. `tests/test_agents_md.py` fails if one appears.

## Before compacting

**Read [`docs/HANDOFF.md`](docs/HANDOFF.md) before auto-compacting, or when asked for a recap.** It says what must survive and where it belongs: invariants into `docs/INVARIANTS.md`, resolved changes into `CHANGELOG.md`. A summary then carries only the in-flight state those files miss.

## Where things are

- [`README.md`](README.md): the lean user-facing overview.
- [`toolscout/README.md`](toolscout/README.md): "the guide". What ATLAS is and isn't, the ATLAS → rlm-harness mapping, reproducing the experiments, the toolspace and roles, and the layout.
- [`VENDOR.md`](VENDOR.md): the extension contract with rlm-harness.
- [`studio/README.md`](studio/README.md), [`eval/README.md`](eval/README.md), [`synth/README.md`](synth/README.md): the workspace members.
- [`CHANGELOG.md`](CHANGELOG.md): what changed and why.
- [`.env.example`](.env.example): every `TS_*` setting.
