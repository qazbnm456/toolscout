"""Task templates: params → English task text, and params → GOLD computed by calling the SAME tools.

Each template's `solve(call, params)` is the reference solution. It reaches the data ONLY through
`call(server, tool, args)`: the catalog directly when the taskset is generated, or toolscout's real
`call_tool` meta-tool in the offline tests. So a gold answer is, by construction, reachable through the
toolspace the planner gets, and the tests prove both paths agree.

Every non-trivial template needs the RLM strategy: the data spans many pages, so a planner has to loop the
fetch and filter/join/aggregate in REPL code. Reading results in the prompt does not fit the output cap.

`level` is a rough difficulty knob (more months or weeks to read, more categories, fewer hints). It is NOT
calibrated against any model yet. `split="heldout"` templates must never be trained on.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .world import CATEGORIES, TIERS, YEAR

Call = Callable[[str, str, dict], Any]

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                "October", "November", "December"]


@dataclass(frozen=True)
class Template:
    id: str
    split: str                      # "train" | "heldout"
    gold_kind: str                  # "int" | "number" | "id" | "id_set" | "cannot_complete"
    servers: tuple[str, ...]        # servers a correct solution loads
    tools: tuple[str, ...]          # "server:tool" a correct solution calls
    levels: tuple[int, ...]
    sample: Callable[[random.Random, dict, int], dict]     # (rng, world, level) -> params
    render: Callable[[dict], str]
    solve: Callable[[Call, dict], Any]
    accept: Callable[[Any], bool] = lambda gold: True     # reject degenerate instances
    tolerance: float = 0.0


def fetch_all(call: Call, server: str, tool: str, args: dict | None = None) -> list:
    """Page through a list endpoint (page_size 50) and return every item."""
    items, page = [], 1
    while True:
        res = call(server, tool, {**(args or {}), "page": page, "page_size": 50})
        if not isinstance(res, dict):
            raise RuntimeError(f"{server}:{tool} returned {res!r}")
        items.extend(res["items"])
        if page >= res["total_pages"]:
            return items
        page += 1


def _period(level: int, rng: random.Random) -> tuple[list[str], str]:
    if level == 1:
        m = rng.randrange(1, 13)
        return [f"{YEAR}-{m:02d}"], f"{_MONTH_NAMES[m - 1]} {YEAR}"
    q = rng.randrange(1, 5)
    return [f"{YEAR}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1)], f"Q{q} {YEAR} (calendar quarter)"


def _delivered(call: Call, months: list[str]) -> list[dict]:
    out = []
    for m in months:
        out.extend(fetch_all(call, "order_ledger", "list_orders", {"month": m, "status": "delivered"}))
    return out


def _skus_in(call: Call, categories: list[str]) -> set[str]:
    return {p["sku"] for c in categories for p in fetch_all(call, "product_catalog", "list_products",
                                                            {"category": c})}


# ── 1. revenue_by_category (train) ────────────────────────────────────────────────────────────────

def _rev_sample(rng, world, level):
    months, label = _period(level, rng)
    return {"months": months, "period": label, "category": rng.choice(CATEGORIES), "hint": level == 1}


def _rev_render(p):
    hint = " Use the unit price actually charged on each order line." if p["hint"] else ""
    return (f"What was the total revenue, in credits, from delivered orders placed in {p['period']}, "
            f"counting only line items for products in the '{p['category']}' category?{hint} "
            f"Reply with only the number.")


def _rev_solve(call, p):
    skus = _skus_in(call, [p["category"]])
    return sum(ln["qty"] * ln["unit_price"] for o in _delivered(call, p["months"])
               for ln in o["lines"] if ln["sku"] in skus)


# ── 2. restock_shortlist (train) ──────────────────────────────────────────────────────────────────

def _rs_sample(rng, world, level):
    wh = rng.choice(world["warehouses"])
    cats = rng.sample(CATEGORIES, level)
    return {"warehouse_name": wh["name"], "categories": cats, "hint": level == 1}


def _rs_render(p):
    cats = p["categories"]
    which = (f"the '{cats[0]}' category" if len(cats) == 1
             else "the " + " or ".join(f"'{c}'" for c in cats) + " categories")
    hint = " Use actual stock on hand, not a forecast." if p["hint"] else ""
    return (f"Which SKUs at the {p['warehouse_name']} warehouse are at or below their reorder point and belong "
            f"to {which}?{hint} Reply with the SKUs only, comma-separated.")


def _rs_solve(call, p):
    wid = next(w["warehouse_id"] for w in call("warehouse_inventory", "list_warehouses", {})
               if w["name"] == p["warehouse_name"])
    low = {s["sku"] for s in fetch_all(call, "warehouse_inventory", "stock_report", {"warehouse_id": wid})
           if s["qty_on_hand"] <= s["reorder_point"]}
    return sorted(low & _skus_in(call, p["categories"]))


# ── 3. top_customer (train) ───────────────────────────────────────────────────────────────────────

def _tc_sample(rng, world, level):
    c = rng.choice(world["customers"])
    months, label = _period(level, rng)
    return {"city": c["city"], "tier": c["tier"], "months": months, "period": label}


def _tc_render(p):
    return (f"Among {p['tier']}-tier customers whose home city is {p['city']}, which one spent the most on "
            f"delivered orders placed in {p['period']}? Spend is the sum of quantity times charged unit "
            f"price over their order lines. Reply with only the customer id.")


def _tc_spend(call, p) -> dict[str, int]:
    ids = {c["customer_id"] for c in fetch_all(call, "customer_directory", "search_customers",
                                               {"city": p["city"], "tier": p["tier"]})}
    spend: dict[str, int] = {}
    for o in _delivered(call, p["months"]):
        if o["customer_id"] in ids:
            spend[o["customer_id"]] = spend.get(o["customer_id"], 0) + sum(
                ln["qty"] * ln["unit_price"] for ln in o["lines"])
    return spend


def _tc_solve(call, p):
    spend = _tc_spend(call, p)
    if len(spend) < 2:
        return None
    ranked = sorted(spend.items(), key=lambda kv: -kv[1])
    return ranked[0][0] if ranked[0][1] != ranked[1][1] else None   # a tie has no single answer


# ── 4. unsupported_lookup (train): the toolspace has no such field → principled decline ───────────

_UNSUPPORTED = [
    ("order_ledger", "orders", "order_id",
     "Which shipping carrier delivered order {order_id}, and what was its tracking number?"),
    ("product_catalog", "products", "sku",
     "What is the shipping weight in kilograms of product {sku} ({name})?"),
    ("staff_directory", "employees", "employee_id",
     "What is the annual salary of employee {employee_id} ({name})?"),
    ("customer_directory", "customers", "customer_id",
     "What is the email address on file for customer {customer_id} ({name})?"),
]


def _un_sample(rng, world, level):
    server, table, key, text = rng.choice(_UNSUPPORTED)
    row = rng.choice(world[table])
    return {"server": server, "text": text.format(**row), "entity": row[key]}


def _un_render(p):
    return p["text"]


def _un_solve(call, p):
    return {"cannot_complete": True}


# ── 5. project_overrun (HELD OUT) ─────────────────────────────────────────────────────────────────

def _po_sample(rng, world, level):
    span = 4 if level == 1 else 8
    start = rng.randrange(1, 27 - span + 1)
    return {"department": rng.choice(world["departments"])["department"],
            "weeks": [f"{YEAR}-W{w:02d}" for w in range(start, start + span)]}


def _po_render(p):
    w = p["weeks"]
    return (f"Which projects owned by the {p['department']} department logged more total hours over weeks "
            f"{w[0]} through {w[-1]} (inclusive) than their weekly budget allows over that span (weekly "
            f"budget hours times the number of weeks)? Count hours from every employee. Reply with the "
            f"project codes only, comma-separated.")


def _po_solve(call, p):
    projects = call("project_registry", "list_projects", {"owner_department": p["department"]})
    budget = {x["project_code"]: x["weekly_budget_hours"] * len(p["weeks"]) for x in projects}
    hours: dict[str, int] = {}
    for wk in p["weeks"]:
        for e in fetch_all(call, "timesheets", "list_entries", {"week": wk}):
            if e["project_code"] in budget:
                hours[e["project_code"]] = hours.get(e["project_code"], 0) + e["hours"]
    over = sorted(c for c, h in hours.items() if h > budget[c])
    return over if 0 < len(over) < len(budget) else None


# ── 6. fx_avg_order (HELD OUT) ────────────────────────────────────────────────────────────────────

def _fx_sample(rng, world, level):
    months, label = _period(level, rng)
    return {"months": months, "period": label, "tier": rng.choice(TIERS[1:]),
            "currency": rng.choice(sorted(world["fx"]))}


def _fx_render(p):
    return (f"What was the average order total of delivered orders placed in {p['period']} by "
            f"{p['tier']}-tier customers, converted from credits to {p['currency']} using the exchange rate "
            f"on each order's date? An order's total is the sum of quantity times charged unit price. Reply "
            f"with only the number, rounded to 2 decimals.")


def _fx_solve(call, p):
    ids = {c["customer_id"] for c in fetch_all(call, "customer_directory", "search_customers",
                                               {"tier": p["tier"]})}
    rates: dict[str, float] = {}
    for m in p["months"]:
        rates.update(call("fx_rates", "rate_history", {"currency": p["currency"], "month": m})["rates"])
    totals = [sum(ln["qty"] * ln["unit_price"] for ln in o["lines"]) * rates[o["order_date"]]
              for o in _delivered(call, p["months"]) if o["customer_id"] in ids]
    return round(sum(totals) / len(totals), 2) if len(totals) >= 5 else None


TEMPLATES: dict[str, Template] = {t.id: t for t in [
    Template("revenue_by_category", "train", "int", ("order_ledger", "product_catalog"),
             ("order_ledger:list_orders", "product_catalog:list_products"), (1, 2),
             _rev_sample, _rev_render, _rev_solve, accept=lambda g: g > 0),
    Template("restock_shortlist", "train", "id_set", ("warehouse_inventory", "product_catalog"),
             ("warehouse_inventory:list_warehouses", "warehouse_inventory:stock_report",
              "product_catalog:list_products"), (1, 2),
             _rs_sample, _rs_render, _rs_solve, accept=lambda g: 1 <= len(g) <= 12),
    Template("top_customer", "train", "id", ("customer_directory", "order_ledger"),
             ("customer_directory:search_customers", "order_ledger:list_orders"), (1, 2),
             _tc_sample, _tc_render, _tc_solve, accept=lambda g: g is not None),
    Template("unsupported_lookup", "train", "cannot_complete", (), (), (1,),
             _un_sample, _un_render, _un_solve),
    Template("project_overrun", "heldout", "id_set", ("project_registry", "timesheets"),
             ("project_registry:list_projects", "timesheets:list_entries"), (1, 2),
             _po_sample, _po_render, _po_solve, accept=lambda g: g is not None),
    Template("fx_avg_order", "heldout", "number", ("customer_directory", "order_ledger", "fx_rates"),
             ("customer_directory:search_customers", "order_ledger:list_orders", "fx_rates:rate_history"),
             (1, 2), _fx_sample, _fx_render, _fx_solve, accept=lambda g: g is not None, tolerance=0.01),
]}

HELDOUT_TEMPLATES = sorted(t.id for t in TEMPLATES.values() if t.split == "heldout")
