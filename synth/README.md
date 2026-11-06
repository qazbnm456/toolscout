# toolscout-synth

A deterministic, offline taskset with **checkable gold answers** for toolscout. It contains:

- a fictional multi-server toolspace, built as a toolscout `StaticCatalog` over committed data snapshots;
- tasks whose gold answers are computed in code by calling those same tools;
- a pure `check()` that reports pass/fail and a reason.

No network, no live MCP server, no model in the tests.

It is **reward-free**. `check()` reports a fact. What a trainer does with that fact (keep or drop a trajectory) is the trainer's call. Gold answers never enter a trace: the runner passes toolscout only the task text, so you join gold to a run by task id, outside the trace.

## The world

The world is an invented outfitter that trades in an invented currency (credits, `CR`) across invented cities in 2031, plus its workforce and four invented foreign currencies. Names are built from syllables. There are no real people or companies, and no security domain.

There are 19 servers. Thirteen answer real questions:

| Server | Serves |
| --- | --- |
| `product_catalog` | the current product catalog |
| `warehouse_inventory` | live stock levels |
| `order_ledger` | customer orders, with the unit price actually charged |
| `customer_directory` | customer accounts: home city and loyalty tier |
| `region_directory` | which cities each sales region covers |
| `staff_directory` | current employees |
| `project_registry` | internal projects and their weekly hour budgets |
| `timesheets` | hours logged per project, weeks 2031-W01 to 2031-W26 |
| `fx_rates` | daily rates from credits to four foreign currencies |
| `supplier_registry` | suppliers and supply contracts, priced in the supplier's currency with validity windows |
| `promo_calendar` | promotions that ran, per category and date range |
| `returns_desk` | processed returns, refunds and reason codes |
| `support_desk` | customer support tickets and their assignees |

Six are **distractors**: `product_archive`, `demand_forecast`, `order_sandbox`, `staff_alumni`, `supplier_prospects` and `promo_drafts`. Each looks like a real server but answers a different question, and its description says so. Two templates (`price_drift`, `forecast_gap`) need the archive or the forecast on purpose, so the planner has to read descriptions rather than avoid names.

The data also contains traps:

- **Charged price vs. list price.** An order line's `unit_price` is what was charged, and differs from the current list price on more than a third of lines.
- **Order status.** Orders that were not delivered are excluded by most questions.
- **Contract windows and currencies.** A quarter of SKUs were renegotiated mid-year, so the valid contract depends on the date, and its cost must be converted to credits at that date's rate.
- **Data that stops.** Timesheets end at week 26, and orders and rates cover 2031 only, so a question outside that range has no answer.

Every list endpoint paginates, at most 50 items per page, so a task means looping over pages and then filtering, joining or aggregating in REPL code. Printed results cannot fit the REPL output cap.

## Templates

25 templates, 627 tasks (540 train, 87 held out). Most have three levels: level 2 widens the period or adds a category, and level 3 adds a filter that needs another server (usually a region, a tier, or a budget threshold). Where a question can tie, the task states the tie rule.

| Template | Split | Gold | What the planner has to do |
| --- | --- | --- | --- |
| `revenue_by_category` | train | int | sum charged revenue for a category; L3 limits it to customers in a region |
| `restock_shortlist` | train | id set | find SKUs at or below reorder point; L3 spans every warehouse in a region |
| `top_customer` | train | id | rank customers by spend; L3 ranks a region's tier with a tie rule |
| `unsupported_lookup` | train | `cannot_complete` | decline: no server has the field |
| `margin_by_supplier` | train | int ±1 | join orders to a supplier's valid contracts and convert costs through fx |
| `promo_window_revenue` | train | int | look up a promotion's window and category, then sum revenue inside it; L3 picks the promotion first |
| `region_top_category` | train | label | rank categories by revenue from a region's customers |
| `return_rate` | train | number ±0.05 | match returned units to the orders that sold them |
| `ticket_assignee_leader` | train | id | count tickets per Customer Care employee, filtered by customer tier and region |
| `restock_slow_supplier` | train | id set | combine low stock with supplier lead times; L3 compares contracts in credits |
| `city_refunds` | train | int | trace each refund back to the ordering customer's city or region |
| `cross_dept_hours` | train | int | sum hours a department's office logs on other departments' projects |
| `repeat_buyers` | train | int | count customers who ordered in at least k months of a quarter |
| `price_drift` | train | int | compare current prices with the archived 2029 list |
| `forecast_gap` | train | int | find SKUs above reorder point now but projected below it |
| `unsupported_partial` | train | `cannot_complete` | decline although the data looks close: a missing field, or a date outside the data |
| `order_cheapest_suppliers` | train | id set | pick the cheapest valid contract per order line, in credits, with a tie rule |
| `project_breadth` | train | id | rank employees by distinct projects, with a tie rule |
| `top_return_reason` | train | id | rank reason codes by returned units |
| `project_overrun` | **heldout** | id set | compare logged hours with weekly budgets over a span |
| `fx_avg_order` | **heldout** | number ±0.01 | average order value converted at each order date's rate |
| `shortfall_cost` | **heldout** | int ±1 | price every stock shortfall at the cheapest valid contract |
| `ticket_resolution` | **heldout** | number ±0.01 | average resolution days for a region's tickets |
| `tier_share_shift` | **heldout** | number ±0.05 | change in the top tiers' revenue share between quarters |
| `region_customer_pressure` | **heldout** | label | customers per warehouse by region |

- **Held out:** the six **heldout** templates above. Never train on them. `MANIFEST.json` lists them under `heldout_templates`, and every task carries a `split` field.
- **Required servers per task:** each record lists the `servers_required` and `tools_required` its reference solution used, which vary by level.
- **Step budget:** each template has a reference strategy of 12 RLM steps or fewer, using at most 4 servers: list servers, load them, describe the tools, ONE REPL step that loops the fetches and computes, then submit. The suite runs it through the real dspy.RLM loop. This shows the task fits the budget for a planner that writes the loop in one step, not that a given model will.

## Use

```python
import dataclasses

from toolscout.config import ToolscoutConfig
from toolscout_synth import load_taskset, opaque_run_id, run_task

data = "path/to/data"            # default: the packaged toolscout_synth/data
for task in load_taskset(data):
    if task["split"] != "train":
        continue
    # TS_* env: planner/specialist model, base_url, api key, TS_ROOT_LM_KWARGS, TS_REQUEST_TIMEOUT, ...
    cfg = dataclasses.replace(ToolscoutConfig.from_env(), max_iterations=15)   # the config is frozen
    res = run_task(task, data_dir=data, outdir="out/b15", run_id=opaque_run_id(task, "b15"), config=cfg)
    res.run_id, res.check.passed, res.check.reason, res.check.detail, res.artifacts.trace_path
```

**Keep run ids opaque.** The run id is written into every trace event, the response file and the exported dataset. A readable id such as `unsupported_lookup-L1-003` would label the template, the decline tasks and the held-out split inside training data. `opaque_run_id` is the default; keep the run id → task id map on your side.

`run_task` is `toolscout.cli.run(task["task"], catalog=build_catalog(data_dir), ...)` followed by `check_outcome`. Because it goes through `cli.run`, it never raises on a failed run.

### Gold kinds and `check()`

Each record in `taskset.jsonl` has these fields: `id`, `template`, `split`, `level`, `task`, `gold_kind`, `gold`, `tolerance`, `servers_required`, `tools_required`, `params`, `snapshot_sha256`, `generator_version`, and `choices` for a label gold.

`check()` judges the **computed value**; answer **format** is reported separately as `detail.bare`. The tasks ask for a bare value, but toolscout's planner is told to ground its answer in tool values, so a correct answer often carries context ("47,889 CR from 212 delivered orders"). For a number, the task's own period labels (`March 2031`, `Q1 2031`, ISO dates and weeks) are removed and the FIRST remaining number is read. For an id, the first id (case-insensitive); for a label, the first of the task's `choices` named in the answer. An id or label set must match exactly, so naming an extra id fails even in "not SKU-1111". To keep only clean-format trajectories, filter on `check.passed and check.detail.get("bare", True)`.

**Pass:**

- `ok`

**Problems with the outcome itself:**

- `no_outcome`: the run produced no answer.
- `missing_cannot_complete`: the task is unanswerable but the run answered it.
- `unexpected_cannot_complete`: the run declined a task that has an answer.

**Problems with the answer value:**

- `no_value_found`: no number or id could be parsed from the answer.
- `wrong_value`: the first number or id is wrong. `detail.gold_among_them` says whether the right one appeared later.
- `out_of_tolerance`: a number outside the tolerance. `detail.abs_diff` gives the gap.
- `wrong_set`: the id set differs. `detail.missing` and `detail.extra` list the differences.

## Known gaps

- **The charged-price trap does not bite in `top_customer`.** Ranking by current list price picks the same customer in 20 of 20 tasks, because the price factors average out. It does bite in the revenue totals.
- **Levels are not calibrated yet.** Level 3 adds a join, and the global `distractors` toggle on `build_catalog` changes server selection, but neither is tuned against measured solve rates.
- **Some tasks make many tool calls.** `city_refunds` resolves each refund's order individually, about 100 `get_order` calls for a quarter. They fit in one REPL loop, but they are slow if the planner makes them one step at a time.

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
