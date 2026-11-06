"""Round-2 templates: harder on reasoning, not on paging.

Each one needs at least one of: a join across 3 or 4 servers, a currency conversion through `fx_rates`, a
filter whose values come from another server's lookup, a stated tie rule, or a decline that has to be earned
because the data LOOKS like it should answer. Paging stays moderate (a month or a quarter of orders, a few
weeks of timesheets). Servers and tools are derived per task from the reference solver's calls.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .templates import (
    Call,
    Template,
    _delivered,
    _period,
    _skus_in,
    customers_in_region,
    fetch_all,
    region_cities,
)
from .world import CATEGORIES, PRIORITIES, TIERS, YEAR


def _round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def _line_total(o: dict) -> int:
    return sum(ln["qty"] * ln["unit_price"] for ln in o["lines"])


class _Rates:
    """Daily CR → currency rates fetched per (currency, month) on demand. 1 CR = rate units."""

    def __init__(self, call: Call):
        self.call, self.cache = call, {}

    def __call__(self, cur: str, day: str) -> float:
        if cur == "CR":
            return 1.0
        key = (cur, day[:7])
        if key not in self.cache:
            self.cache[key] = self.call("fx_rates", "rate_history", {"currency": cur, "month": day[:7]})["rates"]
        return self.cache[key][day]


def _valid(c: dict, day: str) -> bool:
    return c["valid_from"] <= day <= c["valid_to"]


def _cheapest(contracts: list[dict], day: str, rates: _Rates) -> tuple[float, str] | None:
    """(cost in CR, supplier_id) of the cheapest contract valid on `day`; ties → lowest supplier id."""
    best = sorted((c["unit_cost"] / rates(c["currency"], day), c["supplier_id"])
                  for c in contracts if _valid(c, day))
    return best[0] if best else None


def _months(level: int, rng) -> tuple[list[str], str]:
    return _period(1 if level == 1 else 2, rng)


def _quarter_label(q: int) -> str:
    return f"Q{q} {YEAR} (calendar quarter)"


def _weeks(rng, span: int) -> list[str]:
    start = rng.randrange(1, 27 - span + 1)
    return [f"{YEAR}-W{w:02d}" for w in range(start, start + span)]


# ── margin_by_supplier (train) ────────────────────────────────────────────────────────────────────

def _mg_sample(rng, world, level):
    sup = rng.choice([s for s in world["suppliers"] if s["currency"] != "CR"])
    months, label = _months(level, rng)
    p = {"supplier_id": sup["supplier_id"], "supplier_name": sup["name"], "months": months, "period": label}
    if level == 3:
        p["tiers"] = ["gold", "platinum"]
    return p


def _mg_render(p):
    tiers = " Count only orders from gold- or platinum-tier customers." if "tiers" in p else ""
    return (f"What was the gross margin, in credits rounded to the nearest whole credit, on delivered order lines "
            f"placed in {p['period']} for products that {p['supplier_name']} ({p['supplier_id']}) supplies "
            f"under contract? For each such line, margin = quantity × (charged unit price − unit cost), where "
            f"unit cost is that supplier's contract unit_cost valid on the order date, converted from the "
            f"contract currency to credits at that date's rate (1 CR = rate units). Skip lines with no valid "
            f"contract from this supplier on the order date.{tiers} Reply with only the number.")


def _mg_solve(call, p):
    contracts = fetch_all(call, "supplier_registry", "list_contracts", {"supplier_id": p["supplier_id"]})
    by_sku = defaultdict(list)
    for c in contracts:
        by_sku[c["sku"]].append(c)
    who = None
    if "tiers" in p:
        who = {c["customer_id"] for t in p["tiers"]
               for c in fetch_all(call, "customer_directory", "search_customers", {"tier": t})}
    rates, total, n = _Rates(call), 0.0, 0
    for o in _delivered(call, p["months"]):
        if who is not None and o["customer_id"] not in who:
            continue
        for ln in o["lines"]:
            valid = [c for c in by_sku.get(ln["sku"], []) if _valid(c, o["order_date"])]
            if valid:
                c = valid[0]
                total += ln["qty"] * (ln["unit_price"] - c["unit_cost"] / rates(c["currency"], o["order_date"]))
                n += 1
    return _round_half_up(total) if n >= 3 else None


# ── promo_window_revenue (train) ──────────────────────────────────────────────────────────────────

def _pw_sample(rng, world, level):
    promos = world["promotions"]
    if level == 3:
        q = rng.randrange(1, 5)
        return {"category": rng.choice(CATEGORIES), "quarter": q}
    same = [x for x in promos if (x["start_date"][:7] == x["end_date"][:7]) == (level == 1)]
    return {"promo_code": rng.choice(same)["promo_code"]}


def _pw_render(p):
    tail = ("on orders placed while that promotion was running (start and end dates inclusive), counting only "
            "line items for products in the promotion's own category, at the charged unit prices? Reply with "
            "only the number.")
    if "quarter" in p:
        return (f"Find the longest-running promotion for the '{p['category']}' category that started in "
                f"{_quarter_label(p['quarter'])} (if two run equally long, take the earlier start, then the lower "
                f"promo code). What was the delivered revenue, in credits, " + tail)
    return f"What was the delivered revenue, in credits, for promotion {p['promo_code']}: " + tail


def _pw_pick(call, p) -> dict | None:
    if "promo_code" in p:
        return call("promo_calendar", "get_promotion", {"promo_code": p["promo_code"]})
    lo, hi = f"{YEAR}-{3 * p['quarter'] - 2:02d}-01", f"{YEAR}-{3 * p['quarter']:02d}-31"
    cands = [x for x in call("promo_calendar", "list_promotions", {"category": p["category"]})
             if lo <= x["start_date"] <= hi]
    if not cands:
        return None

    def length(x):
        from datetime import date
        return (date.fromisoformat(x["end_date"]) - date.fromisoformat(x["start_date"])).days

    return min(cands, key=lambda x: (-length(x), x["start_date"], x["promo_code"]))


def _pw_solve(call, p):
    promo = _pw_pick(call, p)
    if promo is None:
        return None
    months = sorted({promo["start_date"][:7], promo["end_date"][:7]})
    skus = _skus_in(call, [promo["category"]])
    total = sum(ln["qty"] * ln["unit_price"] for o in _delivered(call, months)
                if promo["start_date"] <= o["order_date"] <= promo["end_date"]
                for ln in o["lines"] if ln["sku"] in skus)
    return total or None


# ── region_top_category (train, label) ────────────────────────────────────────────────────────────

def _rt_sample(rng, world, level):
    months, label = _months(level, rng)
    p = {"region": rng.choice(world["regions"])["region"], "months": months, "period": label}
    if level == 3:
        p["tier"] = rng.choice(TIERS)
    return p


def _rt_render(p):
    tier = f"{p['tier']}-tier " if "tier" in p else ""
    return (f"Which product category earned the most delivered revenue in {p['period']} from {tier}customers whose "
            f"home city is in the {p['region']} region? If categories tie, give the alphabetically first. Reply "
            f"with only the category name.")


def _rt_solve(call, p):
    who = customers_in_region(call, p["region"], tier=p.get("tier"))
    cat = {x["sku"]: x["category"] for x in fetch_all(call, "product_catalog", "list_products")}
    rev = defaultdict(int)
    for o in _delivered(call, p["months"]):
        if o["customer_id"] in who:
            for ln in o["lines"]:
                rev[cat[ln["sku"]]] += ln["qty"] * ln["unit_price"]
    if not rev:
        return None
    return min(rev.items(), key=lambda kv: (-kv[1], kv[0]))[0]


# ── return_rate (train, number) ───────────────────────────────────────────────────────────────────

def _rr_sample(rng, world, level):
    months, label = _months(level, rng)
    p = {"category": rng.choice(CATEGORIES), "months": months, "period": label}
    if level == 3:
        p["tiers"] = ["gold", "platinum"]
    return p


def _rr_render(p):
    tiers = " Count only orders from gold- or platinum-tier customers." if "tiers" in p else ""
    return (f"Of the '{p['category']}' units on orders placed in {p['period']} whose status is delivered or "
            f"returned, what percentage were returned according to the returns desk? Units sold = the sum of "
            f"those order lines' quantities; units returned = the returned quantities recorded against those "
            f"same orders for '{p['category']}' SKUs.{tiers} Round to 1 decimal place and reply with only the "
            f"number.")


def _rr_solve(call, p):
    skus = _skus_in(call, [p["category"]])
    who = None
    if "tiers" in p:
        who = {c["customer_id"] for t in p["tiers"]
               for c in fetch_all(call, "customer_directory", "search_customers", {"tier": t})}
    orders = [o for m in p["months"] for st in ("delivered", "returned")
              for o in fetch_all(call, "order_ledger", "list_orders", {"month": m, "status": st})
              if who is None or o["customer_id"] in who]
    ids = {o["order_id"] for o in orders}
    sold = sum(ln["qty"] for o in orders for ln in o["lines"] if ln["sku"] in skus)
    last = max(p["months"])
    proc_months = sorted(set(p["months"]) | ({f"{YEAR}-{int(last[5:]) + 1:02d}"} if last[5:] != "12" else set()))
    back = sum(r["qty"] for m in proc_months for r in fetch_all(call, "returns_desk", "list_returns", {"month": m})
               if r["order_id"] in ids and r["sku"] in skus)
    return round(100 * back / sold, 1) if sold and back else None


# ── ticket_assignee_leader (train, id) ────────────────────────────────────────────────────────────

def _ta_sample(rng, world, level):
    months, label = _months(level, rng)
    p = {"priority": rng.choice(PRIORITIES[1:]), "months": months, "period": label}
    if level >= 2:
        p["tier"] = rng.choice(TIERS)
    if level == 3:
        p["region"] = rng.choice(world["regions"])["region"]
    return p


def _ta_render(p):
    who = "customers"
    if "tier" in p:
        who = f"{p['tier']}-tier customers"
    if "region" in p:
        who += f" whose home city is in the {p['region']} region"
    return (f"Which Customer Care employee was assigned the most {p['priority']}-priority support tickets opened "
            f"in {p['period']} by {who}? Count only tickets assigned to employees in the Customer Care "
            f"department. If two or more tie, give the lowest employee id. Reply with only the employee id.")


def _ta_solve(call, p):
    care = {e["employee_id"] for e in fetch_all(call, "staff_directory", "list_employees",
                                                {"department": "Customer Care"})}
    who = None
    if "region" in p:
        who = customers_in_region(call, p["region"], tier=p.get("tier"))
    elif "tier" in p:
        who = {c["customer_id"] for c in fetch_all(call, "customer_directory", "search_customers",
                                                   {"tier": p["tier"]})}
    n = defaultdict(int)
    for m in p["months"]:
        for t in fetch_all(call, "support_desk", "list_tickets", {"opened_month": m, "priority": p["priority"]}):
            if t["assigned_employee_id"] in care and (who is None or t["customer_id"] in who):
                n[t["assigned_employee_id"]] += 1
    if not n:
        return None
    best = sorted(n.items(), key=lambda kv: (-kv[1], kv[0]))
    return best[0][0] if best[0][1] >= 2 else None


# ── restock_slow_supplier (train, id_set) ─────────────────────────────────────────────────────────

def _ss_sample(rng, world, level):
    wh = rng.choice(world["warehouses"])
    p = {"warehouse_name": wh["name"], "day": f"{YEAR}-{rng.randrange(1, 13):02d}-15",
         "max_lead": rng.choice([10, 15, 20])}
    if level >= 2:
        p["category"] = rng.choice(CATEGORIES)
    if level == 3:
        p["cheapest"] = True
    return p


def _ss_render(p):
    cat = f" and belong to the '{p['category']}' category" if "category" in p else ""
    if "cheapest" in p:
        rule = (f"whose cheapest supply contract valid on {p['day']} (comparing unit costs in credits at that "
                f"date's rates; ties go to the lower supplier id) comes from a supplier with a lead time of more "
                f"than {p['max_lead']} days")
    else:
        rule = (f"that have no supply contract valid on {p['day']} from any supplier with a lead time of "
                f"{p['max_lead']} days or less")
    return (f"Which SKUs at the {p['warehouse_name']} warehouse are at or below their reorder point{cat}, and "
            f"{rule}? Reply with the SKUs only, comma-separated.")


def _ss_solve(call, p):
    wid = next(w["warehouse_id"] for w in call("warehouse_inventory", "list_warehouses", {})
               if w["name"] == p["warehouse_name"])
    low = sorted(s["sku"] for s in fetch_all(call, "warehouse_inventory", "stock_report", {"warehouse_id": wid})
                 if s["qty_on_hand"] <= s["reorder_point"])
    if "category" in p:
        cats = _skus_in(call, [p["category"]])
        low = [s for s in low if s in cats]
    lead = {s["supplier_id"]: s["lead_time_days"] for s in fetch_all(call, "supplier_registry", "list_suppliers")}
    rates, out = _Rates(call), []
    for sku in low:
        valid = [c for c in fetch_all(call, "supplier_registry", "list_contracts", {"sku": sku})
                 if _valid(c, p["day"])]
        if "cheapest" in p:
            best = _cheapest(valid, p["day"], rates)
            if best and lead[best[1]] > p["max_lead"]:
                out.append(sku)
        elif not any(lead[c["supplier_id"]] <= p["max_lead"] for c in valid):
            out.append(sku)
    return out


# ── city_refunds (train, int) ─────────────────────────────────────────────────────────────────────

def _cr_sample(rng, world, level):
    months, label = _months(level, rng)
    if level == 3:
        return {"region": rng.choice(world["regions"])["region"], "months": months, "period": label}
    return {"city": rng.choice(world["customers"])["city"], "months": months, "period": label}


def _cr_render(p):
    where = f"is in the {p['region']} region" if "region" in p else f"is {p['city']}"
    return (f"How many credits in total were refunded, for returns processed in {p['period']}, on orders placed "
            f"by customers whose home city {where}? Reply with only the number.")


def _cr_solve(call, p):
    rets = [r for m in p["months"] for r in fetch_all(call, "returns_desk", "list_returns", {"month": m})]
    if "region" in p:
        who = customers_in_region(call, p["region"])
    else:
        who = {c["customer_id"] for c in fetch_all(call, "customer_directory", "search_customers",
                                                   {"city": p["city"]})}
    owner = {}
    for oid in sorted({r["order_id"] for r in rets}):
        owner[oid] = call("order_ledger", "get_order", {"order_id": oid})["customer_id"]
    total = sum(r["refund_amount"] for r in rets if owner[r["order_id"]] in who)
    return total or None


# ── cross_dept_hours (train, int) ─────────────────────────────────────────────────────────────────

def _xd_sample(rng, world, level):
    e = rng.choice(world["employees"])
    p = {"department": e["department"], "office": e["office"], "weeks": _weeks(rng, 2 if level == 1 else 4)}
    if level == 3:
        p["min_budget"] = rng.choice([130, 150, 170])
    return p


def _xd_render(p):
    w = p["weeks"]
    extra = (f" Count only projects whose weekly budget is at least {p['min_budget']} hours."
             if "min_budget" in p else "")
    return (f"How many hours did {p['department']} employees whose office is in {p['office']} log on projects "
            f"owned by a different department during weeks {w[0]} through {w[-1]} (inclusive)?{extra} Reply with "
            f"only the number.")


def _xd_solve(call, p):
    staff = {e["employee_id"] for e in fetch_all(call, "staff_directory", "list_employees",
                                                 {"department": p["department"]}) if e["office"] == p["office"]}
    proj = {x["project_code"]: x for x in call("project_registry", "list_projects", {})}
    total = 0
    for wk in p["weeks"]:
        for t in fetch_all(call, "timesheets", "list_entries", {"week": wk}):
            pr = proj[t["project_code"]]
            if (t["employee_id"] in staff and pr["owner_department"] != p["department"]
                    and pr["weekly_budget_hours"] >= p.get("min_budget", 0)):
                total += t["hours"]
    return total or None


# ── shortfall_cost (HELD OUT, int) ────────────────────────────────────────────────────────────────

def _sf_sample(rng, world, level):
    p = {"warehouse_name": rng.choice(world["warehouses"])["name"], "day": f"{YEAR}-{rng.randrange(1, 13):02d}-10"}
    if level == 2:
        p["category"] = rng.choice(CATEGORIES)
    return p


def _sf_render(p):
    cat = f" in the '{p['category']}' category" if "category" in p else ""
    return (f"At the {p['warehouse_name']} warehouse, every SKU{cat} at or below its reorder point has a shortfall "
            f"of (reorder_point − qty_on_hand + 1) units. What would it cost, in credits rounded to the nearest "
            f"whole credit, to buy every shortfall from that SKU's cheapest supply contract valid on {p['day']}? "
            f"Compare and convert unit costs to credits at that date's rates, ignore minimum order quantities, "
            f"and skip SKUs with no valid contract. Reply with only the number.")


def _sf_solve(call, p):
    wid = next(w["warehouse_id"] for w in call("warehouse_inventory", "list_warehouses", {})
               if w["name"] == p["warehouse_name"])
    low = [s for s in fetch_all(call, "warehouse_inventory", "stock_report", {"warehouse_id": wid})
           if s["qty_on_hand"] <= s["reorder_point"]]
    if "category" in p:
        cats = _skus_in(call, [p["category"]])
        low = [s for s in low if s["sku"] in cats]
    rates, total, n = _Rates(call), 0.0, 0
    for s in low:
        best = _cheapest(fetch_all(call, "supplier_registry", "list_contracts", {"sku": s["sku"]}), p["day"], rates)
        if best:
            total += best[0] * (s["reorder_point"] - s["qty_on_hand"] + 1)
            n += 1
    return _round_half_up(total) if n >= 2 else None


# ── repeat_buyers (train, int) ────────────────────────────────────────────────────────────────────

def _rb_sample(rng, world, level):
    q = rng.randrange(1, 5)
    p = {"tier": rng.choice(TIERS), "quarter": q, "k": 3 if level == 2 else 2}
    if level == 3:
        p["region"] = rng.choice(world["regions"])["region"]
    return p


def _rb_render(p):
    where = f" whose home city is in the {p['region']} region" if "region" in p else ""
    return (f"How many distinct {p['tier']}-tier customers{where} placed delivered orders in at least {p['k']} "
            f"different calendar months of {_quarter_label(p['quarter'])}? Reply with only the number.")


def _rb_solve(call, p):
    if "region" in p:
        who = customers_in_region(call, p["region"], tier=p["tier"])
    else:
        who = {c["customer_id"] for c in fetch_all(call, "customer_directory", "search_customers",
                                                   {"tier": p["tier"]})}
    months = [f"{YEAR}-{m:02d}" for m in range(3 * p["quarter"] - 2, 3 * p["quarter"] + 1)]
    seen = defaultdict(set)
    for o in _delivered(call, months):
        if o["customer_id"] in who:
            seen[o["customer_id"]].add(o["order_date"][:7])
    return sum(1 for v in seen.values() if len(v) >= p["k"]) or None


# ── price_drift (train, int): the archive is a legitimate source here ─────────────────────────────

def _pd_sample(rng, world, level):
    p = {"categories": rng.sample(CATEGORIES, 1 if level == 1 else 2), "pct": rng.choice([10, 15, 20])}
    if level == 3:
        p["warehouse_name"] = rng.choice(world["warehouses"])["name"]
    return p


def _pd_render(p):
    cats = " or ".join(f"'{c}'" for c in p["categories"])
    stocked = f" and are stocked (any quantity) at the {p['warehouse_name']} warehouse" if "warehouse_name" in p else ""
    return (f"How many products in the {cats} categor{'y' if len(p['categories']) == 1 else 'ies'} have a current "
            f"list price more than {p['pct']}% above their list price in the archived 2029 price list{stocked}? "
            f"Count only SKUs that appear in both lists. Reply with only the number.")


def _pd_solve(call, p):
    cur = {x["sku"]: x["list_price"] for c in p["categories"]
           for x in fetch_all(call, "product_catalog", "list_products", {"category": c})}
    old = {x["sku"]: x["list_price"] for x in fetch_all(call, "product_archive", "list_archived_products")}
    keep = set(cur)
    if "warehouse_name" in p:
        wid = next(w["warehouse_id"] for w in call("warehouse_inventory", "list_warehouses", {})
                   if w["name"] == p["warehouse_name"])
        keep = {s["sku"] for s in fetch_all(call, "warehouse_inventory", "stock_report", {"warehouse_id": wid})}
    return sum(1 for s, price in cur.items() if s in old and s in keep and price > old[s] * (1 + p["pct"] / 100)) or None


# ── forecast_gap (train, int): the forecast is a legitimate source here ───────────────────────────

def _fg_sample(rng, world, level):
    if level == 3:
        return {"region": rng.choice(world["regions"])["region"], "categories": rng.sample(CATEGORIES, 2)}
    return {"warehouse_name": rng.choice(world["warehouses"])["name"],
            "categories": rng.sample(CATEGORIES, level)}


def _fg_render(p):
    cats = " or ".join(f"'{c}'" for c in p["categories"])
    if "region" in p:
        where, unit = f"Across every warehouse located in the {p['region']} region", "warehouse–SKU pairs"
    else:
        where, unit = f"At the {p['warehouse_name']} warehouse", "SKUs"
    return (f"{where}, how many {unit} in the {cats} categor{'y' if len(p['categories']) == 1 else 'ies'} are "
            f"currently above their reorder point but are projected by the demand forecast to be at or below "
            f"it? Reply with only the number.")


def _fg_solve(call, p):
    skus = _skus_in(call, p["categories"])
    whs = call("warehouse_inventory", "list_warehouses", {})
    if "region" in p:
        cities = region_cities(call, p["region"])
        wids = [w["warehouse_id"] for w in whs if w["city"] in cities]
    else:
        wids = [next(w["warehouse_id"] for w in whs if w["name"] == p["warehouse_name"])]
    n = 0
    for wid in wids:
        now = {s["sku"]: s for s in fetch_all(call, "warehouse_inventory", "stock_report", {"warehouse_id": wid})}
        for f in fetch_all(call, "demand_forecast", "forecast_report", {"warehouse_id": wid}):
            s = now.get(f["sku"])
            if s and f["sku"] in skus and s["qty_on_hand"] > s["reorder_point"] and f["projected_qty"] <= f["reorder_point"]:
                n += 1
    return n or None


# ── ticket_resolution (HELD OUT, number) ──────────────────────────────────────────────────────────

def _tr_sample(rng, world, level):
    months, label = _months(level, rng)
    return {"priority": rng.choice(PRIORITIES), "region": rng.choice(world["regions"])["region"],
            "months": months, "period": label}


def _tr_render(p):
    return (f"What was the average resolution time, in days, of {p['priority']}-priority support tickets opened in "
            f"{p['period']} by customers whose home city is in the {p['region']} region? Use closed tickets only; "
            f"resolution time is closed_date minus opened_date in days. Round to 2 decimal places and reply with "
            f"only the number.")


def _tr_solve(call, p):
    from datetime import date
    who = customers_in_region(call, p["region"])
    d = [(date.fromisoformat(t["closed_date"]) - date.fromisoformat(t["opened_date"])).days
         for m in p["months"]
         for t in fetch_all(call, "support_desk", "list_tickets", {"opened_month": m, "priority": p["priority"]})
         if t["customer_id"] in who and t["closed_date"]]
    return round(sum(d) / len(d), 2) if len(d) >= 3 else None


# ── unsupported_partial (train, cannot_complete): the data looks like it should answer ────────────

def _up_sample(rng, world, level):
    if level == 1:
        kind = rng.choice(["ret", "promo", "sup", "tck"])
        if kind == "ret":
            r = rng.choice(world["returns"])
            return {"server": "returns_desk", "text": f"What shipping cost was charged on return {r['return_id']}?"}
        if kind == "promo":
            x = rng.choice(world["promotions"])
            return {"server": "promo_calendar", "text": f"Which employee approved promotion {x['promo_code']}?"}
        if kind == "sup":
            s = rng.choice(world["suppliers"])
            return {"server": "supplier_registry",
                    "text": f"What is the on-time delivery rate of supplier {s['supplier_id']} ({s['name']})?"}
        t = rng.choice(world["tickets"])
        return {"server": "support_desk",
                "text": f"What satisfaction score did the customer give on support ticket {t['ticket_id']}?"}
    kind = rng.choice(["week", "fx", "spend"])
    if kind == "week":
        e = rng.choice(world["employees"])
        wk = rng.randrange(30, 53)
        return {"server": "timesheets",
                "text": f"How many hours did employee {e['employee_id']} ({e['name']}) log in week {YEAR}-W{wk:02d}?"}
    if kind == "fx":
        cur = rng.choice(sorted(world["fx"]))
        return {"server": "fx_rates", "text": f"What was the exchange rate from credits to {cur} on {YEAR - 1}-12-"
                                               f"{rng.randrange(1, 32):02d}?"}
    c = rng.choice(world["customers"])
    return {"server": "order_ledger",
            "text": f"How much did customer {c['customer_id']} ({c['name']}) spend on delivered orders in {YEAR - 1}?"}


def _up_render(p):
    return p["text"]


def _up_solve(call, p):
    return {"cannot_complete": True}


# ── order_cheapest_suppliers (train, id_set) ──────────────────────────────────────────────────────

def _oc_sample(rng, world, level):
    want = {1: (2, 2), 2: (3, 4), 3: (2, 3)}[level]
    pool = [o for o in world["orders"] if want[0] <= len(o["lines"]) <= want[1]]
    n = 2 if level == 3 else 1
    return {"order_ids": sorted(o["order_id"] for o in rng.sample(pool, n))}


def _oc_render(p):
    ids = " and ".join(p["order_ids"])
    return (f"For order{'s' if len(p['order_ids']) > 1 else ''} {ids}, find for each order line the supplier with "
            f"the cheapest supply contract for that line's SKU valid on the order date (compare unit costs in "
            f"credits at that date's rates; if two tie, take the lower supplier id). Skip a line whose SKU has no "
            f"valid contract. Which suppliers are they? Reply with the supplier ids only, comma-separated.")


def _oc_solve(call, p):
    rates, out = _Rates(call), set()
    for oid in p["order_ids"]:
        o = call("order_ledger", "get_order", {"order_id": oid})
        for ln in o["lines"]:
            best = _cheapest(fetch_all(call, "supplier_registry", "list_contracts", {"sku": ln["sku"]}),
                             o["order_date"], rates)
            if best:
                out.add(best[1])
    return sorted(out) or None


# ── tier_share_shift (HELD OUT, number) ───────────────────────────────────────────────────────────

def _ts_sample(rng, world, level):
    q1 = rng.randrange(1, 4)
    return {"q1": q1, "q2": q1 + 1}


def _ts_render(p):
    return (f"By how many percentage points did gold- and platinum-tier customers' share of delivered revenue "
            f"change from {_quarter_label(p['q1'])} to {_quarter_label(p['q2'])}? Give Q{p['q2']}'s share minus "
            f"Q{p['q1']}'s share, rounded to 1 decimal place (negative if it fell). Reply with only the number.")


def _ts_solve(call, p):
    top = {c["customer_id"] for t in ("gold", "platinum")
           for c in fetch_all(call, "customer_directory", "search_customers", {"tier": t})}

    def share(q):
        months = [f"{YEAR}-{m:02d}" for m in range(3 * q - 2, 3 * q + 1)]
        orders = _delivered(call, months)
        total = sum(_line_total(o) for o in orders)
        return 100 * sum(_line_total(o) for o in orders if o["customer_id"] in top) / total

    return round(share(p["q2"]) - share(p["q1"]), 1)


# ── project_breadth (train, id) ───────────────────────────────────────────────────────────────────

def _pb_sample(rng, world, level):
    p = {"department": rng.choice(world["departments"])["department"],
         "weeks": _weeks(rng, 2 if level == 1 else 4)}
    if level == 3:
        p["other_only"] = True
    return p


def _pb_render(p):
    w = p["weeks"]
    other = " Count only projects owned by a department other than the employee's own." if "other_only" in p else ""
    return (f"Which {p['department']} employee logged hours on the most distinct projects during weeks {w[0]} "
            f"through {w[-1]} (inclusive)?{other} If two or more tie, give the lowest employee id. Reply with only "
            f"the employee id.")


def _pb_solve(call, p):
    staff = {e["employee_id"] for e in fetch_all(call, "staff_directory", "list_employees",
                                                 {"department": p["department"]})}
    owner = ({x["project_code"]: x["owner_department"] for x in call("project_registry", "list_projects", {})}
             if "other_only" in p else {})
    seen = defaultdict(set)
    for wk in p["weeks"]:
        for t in fetch_all(call, "timesheets", "list_entries", {"week": wk}):
            if t["employee_id"] in staff and owner.get(t["project_code"]) != p["department"]:
                seen[t["employee_id"]].add(t["project_code"])
    if not seen:
        return None
    return min(seen.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0]


# ── top_return_reason (train, id) ─────────────────────────────────────────────────────────────────

def _rn_sample(rng, world, level):
    months, label = _months(level, rng)
    p = {"category": rng.choice(CATEGORIES), "months": months, "period": label}
    if level == 3:
        p["city"] = rng.choice(world["customers"])["city"]
    return p


def _rn_render(p):
    city = (f" Count only returns on orders from customers whose home city is {p['city']}." if "city" in p else "")
    return (f"Among returns processed in {p['period']} for products in the '{p['category']}' category, which "
            f"return reason code accounts for the most returned units?{city} If two or more tie, give the lowest "
            f"code. Reply with only the reason code.")


def _rn_solve(call, p):
    skus = _skus_in(call, [p["category"]])
    rets = [r for m in p["months"] for r in fetch_all(call, "returns_desk", "list_returns", {"month": m})
            if r["sku"] in skus]
    if "city" in p:
        who = {c["customer_id"] for c in fetch_all(call, "customer_directory", "search_customers",
                                                   {"city": p["city"]})}
        owner = {oid: call("order_ledger", "get_order", {"order_id": oid})["customer_id"]
                 for oid in sorted({r["order_id"] for r in rets})}
        rets = [r for r in rets if owner[r["order_id"]] in who]
    n = defaultdict(int)
    for r in rets:
        n[r["reason_code"]] += r["qty"]
    if not n:
        return None
    return min(n.items(), key=lambda kv: (-kv[1], kv[0]))[0]


# ── region_customer_pressure (HELD OUT, label) ────────────────────────────────────────────────────

def _rp_sample(rng, world, level):
    return {"tier": rng.choice(TIERS)}


def _rp_render(p):
    return (f"Which region has the most {p['tier']}-tier customers per warehouse located in it? Divide the number "
            f"of {p['tier']}-tier customers whose home city is in the region by the number of warehouses in the "
            f"region's cities; skip regions with no warehouse. If regions tie, give the alphabetically first. Reply "
            f"with only the region name.")


def _rp_solve(call, p):
    whs = call("warehouse_inventory", "list_warehouses", {})
    best = []
    for r in call("region_directory", "list_regions", {}):
        n_wh = sum(1 for w in whs if w["city"] in r["cities"])
        if n_wh:
            who = customers_in_region(call, r["region"], tier=p["tier"], cities=set(r["cities"]))
            best.append((-len(who) / n_wh, r["region"]))
    return min(best)[1]


def _regions(world):
    return [r["region"] for r in world["regions"]]


R2_TEMPLATES = [
    Template("margin_by_supplier", "train", "int", None, None, (1, 2, 3), _mg_sample, _mg_render, _mg_solve,
             accept=lambda g: g is not None, tolerance=1.0),
    Template("promo_window_revenue", "train", "int", None, None, (1, 2, 3), _pw_sample, _pw_render, _pw_solve,
             accept=lambda g: g is not None),
    Template("region_top_category", "train", "label", None, None, (1, 2, 3), _rt_sample, _rt_render, _rt_solve,
             accept=lambda g: g is not None, choices=lambda w: list(CATEGORIES)),
    Template("return_rate", "train", "number", None, None, (1, 2, 3), _rr_sample, _rr_render, _rr_solve,
             accept=lambda g: g is not None, tolerance=0.05),
    Template("ticket_assignee_leader", "train", "id", None, None, (1, 2, 3), _ta_sample, _ta_render, _ta_solve,
             accept=lambda g: g is not None),
    Template("restock_slow_supplier", "train", "id_set", None, None, (1, 2, 3), _ss_sample, _ss_render, _ss_solve,
             accept=lambda g: 1 <= len(g) <= 12),
    Template("city_refunds", "train", "int", None, None, (1, 2, 3), _cr_sample, _cr_render, _cr_solve,
             accept=lambda g: g is not None),
    Template("cross_dept_hours", "train", "int", None, None, (1, 2, 3), _xd_sample, _xd_render, _xd_solve,
             accept=lambda g: g is not None),
    Template("shortfall_cost", "heldout", "int", None, None, (1, 2), _sf_sample, _sf_render, _sf_solve,
             accept=lambda g: g is not None, tolerance=1.0),
    Template("repeat_buyers", "train", "int", None, None, (1, 2, 3), _rb_sample, _rb_render, _rb_solve,
             accept=lambda g: g is not None),
    Template("price_drift", "train", "int", None, None, (1, 2, 3), _pd_sample, _pd_render, _pd_solve,
             accept=lambda g: g is not None),
    Template("forecast_gap", "train", "int", None, None, (1, 2, 3), _fg_sample, _fg_render, _fg_solve,
             accept=lambda g: g is not None),
    Template("ticket_resolution", "heldout", "number", None, None, (1, 2), _tr_sample, _tr_render, _tr_solve,
             accept=lambda g: g is not None, tolerance=0.01),
    Template("unsupported_partial", "train", "cannot_complete", (), (), (1, 2), _up_sample, _up_render, _up_solve),
    Template("order_cheapest_suppliers", "train", "id_set", None, None, (1, 2, 3), _oc_sample, _oc_render,
             _oc_solve, accept=lambda g: g is not None),
    Template("tier_share_shift", "heldout", "number", None, None, (1,), _ts_sample, _ts_render, _ts_solve,
             tolerance=0.05, per_level=3),
    Template("project_breadth", "train", "id", None, None, (1, 2, 3), _pb_sample, _pb_render, _pb_solve,
             accept=lambda g: g is not None),
    Template("top_return_reason", "train", "id", None, None, (1, 2, 3), _rn_sample, _rn_render, _rn_solve,
             accept=lambda g: g is not None),
    Template("region_customer_pressure", "heldout", "label", None, None, (1,), _rp_sample, _rp_render, _rp_solve,
             choices=_regions, per_level=4),
]
