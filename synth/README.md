# toolscout-synth

A deterministic, offline taskset with **checkable gold answers** for toolscout. It contains:

- a fictional multi-server toolspace, built as a toolscout `StaticCatalog` over committed data snapshots;
- tasks whose gold answers are computed in code by calling those same tools;
- a pure `check()` that reports pass/fail and a reason.

No network, no live MCP server, no model in the tests.

It is **reward-free**. `check()` reports a fact. What a trainer does with that fact (keep or drop a trajectory) is the trainer's call. Gold answers never enter a trace: the runner passes toolscout only the task text, so you join gold to a run by task id, outside the trace.

## The world

The world is an invented outfitter that trades in an invented currency (credits, `CR`) across invented cities in 2031, plus its workforce and four invented foreign currencies. Names are built from syllables. There are no real people or companies, and no security domain.

There are 12 servers. Eight answer real questions:

| Server | Serves |
| --- | --- |
| `product_catalog` | the current product catalog |
| `warehouse_inventory` | live stock levels |
| `order_ledger` | customer orders |
| `customer_directory` | customer accounts |
| `staff_directory` | current employees |
| `project_registry` | internal projects |
| `timesheets` | hours logged per project |
| `fx_rates` | daily exchange rates |

Four are **distractors**: `product_archive`, `demand_forecast`, `order_sandbox` and `staff_alumni`. Each looks like a real server but answers a different question. Their descriptions say honestly what they are.

The data also contains traps:

- **Charged price vs. list price.** An order line's `unit_price` is what was charged, and differs from the current list price on more than a third of lines.
- **Order status.** Orders that were not delivered are excluded by most questions.

Every list endpoint paginates, at most 50 items per page. That is what forces the RLM strategy: a task means looping over pages and then filtering, joining or aggregating in REPL code. Printed results cannot fit the REPL output cap.

## Templates (first round)

| Template | Split | Gold | Servers | Level 2 makes it harder by |
| --- | --- | --- | --- | --- |
| `revenue_by_category` | train | int | order_ledger, product_catalog | a quarter instead of a month; drops the charged-price hint |
| `restock_shortlist` | train | id set | warehouse_inventory, product_catalog | two categories; drops the "not a forecast" hint |
| `top_customer` | train | id | customer_directory, order_ledger | a quarter instead of a month |
| `unsupported_lookup` | train | `cannot_complete` | none (no server has the field) | (level 1 only) |
| `project_overrun` | **heldout** | id set | project_registry, timesheets | 8 weeks instead of 4 |
| `fx_avg_order` | **heldout** | number ±0.01 | customer_directory, order_ledger, fx_rates | a quarter instead of a month |

- **Held out:** `fx_avg_order` and `project_overrun`. Never train on them. `MANIFEST.json` lists them under `heldout_templates`, and every task carries a `split` field.
- **Levels are rough.** They are not calibrated against any model yet.
- **Step budget:** each template's reference trajectory finishes in 12 RLM steps or fewer. It lists servers, loads them, describes the tools, runs one REPL step that loops the fetches and computes, then submits. The suite runs that trajectory through the real dspy.RLM loop.

## Use

```python
from toolscout.config import ToolscoutConfig
from toolscout_synth import load_taskset, run_task

data = "path/to/data"            # default: the packaged toolscout_synth/data
for task in load_taskset(data):
    if task["split"] != "train":
        continue
    cfg = ToolscoutConfig.from_env()   # TS_* env: planner/specialist model, base_url, api key
    cfg.max_iterations = 15
    res = run_task(task, data_dir=data, outdir="out/b15", run_id=f"{task['id']}-b15", config=cfg)
    res.check.passed, res.check.reason, res.check.detail, res.artifacts.trace_path
```

`run_task` is `toolscout.cli.run(task["task"], catalog=build_catalog(data_dir), ...)` followed by `check_outcome`. Because it goes through `cli.run`, it never raises on a failed run.

### Gold kinds and `check()`

Each record in `taskset.jsonl` has these fields: `id`, `template`, `split`, `level`, `task`, `gold_kind`, `gold`, `tolerance`, `servers_required`, `tools_required`, `params`, `snapshot_sha256`, `generator_version`.

`check()` parses answers strictly, because every task asks for a bare value. The reasons it can return are listed below.

**Pass:**

- `ok`

**Problems with the outcome itself:**

- `no_outcome`: the run produced no answer.
- `missing_cannot_complete`: the task is unanswerable but the run answered it.
- `unexpected_cannot_complete`: the run declined a task that has an answer.

**Problems with the answer value:**

- `no_value_found`: no number or id could be parsed from the answer.
- `ambiguous_answer`: more than one number or id was found. `detail.gold_among_them` says whether the right one was in there.
- `wrong_value`: exact mismatch.
- `out_of_tolerance`: a number outside the tolerance. `detail.abs_diff` gives the gap.
- `wrong_set`: the id set differs. `detail.missing` and `detail.extra` list the differences.

## Shipping the data

`toolscout_synth/data/` is self-contained and can be tarred as-is:

- `snapshots/*.json`
- `taskset.jsonl`
- `MANIFEST.json`, which records the seed, a sha256 per file, the held-out templates and the task counts.

Two commands manage it:

```bash
toolscout-synth verify [DIR]   # regenerate from the recorded seed; compare byte-for-byte
toolscout-synth gen [DIR]      # rewrite it (only when the generator or templates change)
```

Regeneration is byte-identical on CPython 3.11, 3.12 and 3.13, and the suite checks this.

## Tests

```bash
uv run --package toolscout-synth --extra dev python -m pytest synth/tests
```
