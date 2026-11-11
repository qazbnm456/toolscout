"""Round-3 templates: level 4 on six round-2 templates, more level-3 instances, and eight new templates.

Hardness comes from composition, not paging: each L4 adds a filter or conversion that needs another server, and
every ranking resamples when its top ties (the task still states the tie rule). Round-3 solvers fetch every rate
through `fx_rates`, CR included, so the gold-through-`call_tool` test proves each value is reachable. Earlier
levels and templates are untouched: `apply_round3` wraps a round-2 template so only `level == 4` takes new code.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from datetime import date, timedelta

from .templates import (
    _MONTH_NAMES,
    Call,
    Template,
    _delivered,
    _skus_in,
    customers_in_region,
    fetch_all,
    region_cities,
)
from .templates_r2 import _months, _quarter_label, _round_half_up, _valid, _weeks
from .world import CATEGORIES, PRIORITIES, YEAR


class _Rates3:
    """Daily rates fetched through `fx_rates` for every currency, CR included. 1 CR = rate units."""

    def __init__(self, call: Call):
        self.call, self.cache = call, {}

    def __call__(self, cur: str, day: str) -> float:
        key = (cur, day[:7])
        if key not in self.cache:
            self.cache[key] = self.call("fx_rates", "rate_history", {"currency": cur, "month": day[:7]})["rates"]
        return self.cache[key][day]


def _cheapest3(contracts: list[dict], day: str, rates: _Rates3) -> tuple[tuple[float, str] | None, bool]:
    """((cost in CR, supplier_id) of the cheapest contract valid on `day`, whether the top two costs tie)."""
    best = sorted((c["unit_cost"] / rates(c["currency"], day), c["supplier_id"]) for c in contracts if _valid(c, day))
    if not best:
        return None, False
    return best[0], len(best) > 1 and abs(best[0][0] - best[1][0]) < 1e-9


def _unique_top(scores: dict, *, minimum=None):
    """The key with the highest score, or None when the top two tie (the instance is resampled)."""
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    if minimum is not None and ranked[0][1] < minimum:
        return None
    return ranked[0][0]


def _region(rng, world) -> str:
    return rng.choice(world["regions"])["region"]


def _next_month(m: str) -> str | None:
    return None if m.endswith("-12") else f"{YEAR}-{int(m[5:]) + 1:02d}"


def _returns_against(call: Call, months: list[str], order_ids: set[str]) -> list[dict]:
    """Returns recorded against `order_ids`, whenever processed (processing takes at most 20 days)."""
    proc = sorted(set(months) | {m2 for m in months if (m2 := _next_month(m))})
    return [r for m in proc for r in fetch_all(call, "returns_desk", "list_returns", {"month": m})
            if r["order_id"] in order_ids]


def _weekday(day: str) -> int:
    return date.fromisoformat(day).weekday()


# ════════════════════════════════════════════ level 4 on round-2 templates ════════════════════════════════════

# margin_by_supplier L4: quarter, gold/platinum customers whose home city is in a region.
def _mg4_sample(rng, world, level):
    sup = rng.choice([s for s in world["suppliers"] if s["currency"] != "CR"])
    months, label = _months(2, rng)
    return {"supplier_id": sup["supplier_id"], "supplier_name": sup["name"], "months": months, "period": label,
            "tiers": ["gold", "platinum"], "region": _region(rng, world)}


def _mg4_render(p):
    return (f"What was the gross margin, in credits rounded to the nearest whole credit, on delivered order lines "
            f"placed in {p['period']} for products that {p['supplier_name']} ({p['supplier_id']}) supplies under "
            f"contract? For each such line, margin = quantity × (charged unit price − unit cost), where unit cost is "
            f"that supplier's contract unit_cost valid on the order date, converted from the contract currency to "
            f"credits at that date's rate (1 CR = rate units). Skip lines with no valid contract from this supplier on "
            f"the order date. Count only orders from gold- or platinum-tier customers whose home city is in the "
            f"{p['region']} region. Reply with only the number.")


def _supplier_line_margins(call, p, who):
    """[(order, line, contract)] for the supplier's valid contracts on delivered lines of `who`."""
    by_sku = defaultdict(list)
    for c in fetch_all(call, "supplier_registry", "list_contracts", {"supplier_id": p["supplier_id"]}):
        by_sku[c["sku"]].append(c)
    out = []
    for o in _delivered(call, p["months"]):
        if who is not None and o["customer_id"] not in who:
            continue
        for ln in o["lines"]:
            valid = [c for c in by_sku.get(ln["sku"], []) if _valid(c, o["order_date"])]
            if valid:
                out.append((o, ln, valid[0]))
    return out


def _tier_region_customers(call, tiers, region):
    tiered = {c["customer_id"] for t in tiers
              for c in fetch_all(call, "customer_directory", "search_customers", {"tier": t})}
    return tiered & customers_in_region(call, region) if region else tiered


def _mg4_solve(call, p):
    who = _tier_region_customers(call, p["tiers"], p["region"])
    rates, total = _Rates3(call), 0.0
    lines = _supplier_line_margins(call, p, who)
    for o, ln, c in lines:
        total += ln["qty"] * (ln["unit_price"] - c["unit_cost"] / rates(c["currency"], o["order_date"]))
    return _round_half_up(total) if len(lines) >= 3 else None


# order_cheapest_suppliers L4: three 2-3-line orders; only suppliers with lead time ≤ N are compared.
def _oc4_sample(rng, world, level):
    pool = [o for o in world["orders"] if 2 <= len(o["lines"]) <= 3]
    return {"order_ids": sorted(o["order_id"] for o in rng.sample(pool, 3)), "max_lead": rng.choice([10, 20, 30])}


def _oc4_render(p):
    a, b, c = p["order_ids"]
    return (f"For orders {a}, {b} and {c}, find for each order line the supplier with the cheapest supply contract "
            f"for that line's SKU valid on the order date, comparing only contracts from suppliers with a lead time of "
            f"{p['max_lead']} days or less (compare unit costs in credits at that date's rates; if two tie, take the "
            f"lower supplier id). Skip a line with no such contract. Which suppliers are they? Reply with the supplier "
            f"ids only, comma-separated.")


def _oc4_solve(call, p):
    lead = {s["supplier_id"]: s["lead_time_days"] for s in fetch_all(call, "supplier_registry", "list_suppliers")}
    rates, out = _Rates3(call), set()
    for oid in p["order_ids"]:
        o = call("order_ledger", "get_order", {"order_id": oid})
        for ln in o["lines"]:
            cs = [c for c in fetch_all(call, "supplier_registry", "list_contracts", {"sku": ln["sku"]})
                  if lead[c["supplier_id"]] <= p["max_lead"]]
            best, tie = _cheapest3(cs, o["order_date"], rates)
            if tie:
                return None
            if best:
                out.add(best[1])
    return sorted(out) or None


# restock_slow_supplier L4: L3's cheapest-contract rule over every warehouse of a region, plus a category.
def _ss4_sample(rng, world, level):
    return {"region": _region(rng, world), "category": rng.choice(CATEGORIES),
            "day": f"{YEAR}-{rng.randrange(1, 13):02d}-15", "max_lead": rng.choice([10, 15, 20])}


def _ss4_render(p):
    return (f"Which SKUs in the '{p['category']}' category are at or below their reorder point at one or more "
            f"warehouses located in the {p['region']} region, and have their cheapest supply contract valid on "
            f"{p['day']} (comparing unit costs in credits at that date's rates; ties go to the lower supplier id) from "
            f"a supplier with a lead time of more than {p['max_lead']} days? Reply with the SKUs only, comma-separated.")


def _ss4_solve(call, p):
    cities = region_cities(call, p["region"])
    wids = [w["warehouse_id"] for w in call("warehouse_inventory", "list_warehouses", {}) if w["city"] in cities]
    cats = _skus_in(call, [p["category"]])
    low = sorted({s["sku"] for wid in wids
                  for s in fetch_all(call, "warehouse_inventory", "stock_report", {"warehouse_id": wid})
                  if s["qty_on_hand"] <= s["reorder_point"] and s["sku"] in cats})
    lead = {s["supplier_id"]: s["lead_time_days"] for s in fetch_all(call, "supplier_registry", "list_suppliers")}
    rates, out = _Rates3(call), []
    for sku in low:   # deduped: one contract fetch per SKU, however many warehouses list it
        best, tie = _cheapest3(fetch_all(call, "supplier_registry", "list_contracts", {"sku": sku}), p["day"], rates)
        if tie:
            return None
        if best and lead[best[1]] > p["max_lead"]:
            out.append(sku)
    return out if 1 <= len(out) <= 12 else None


# return_rate L4: quarter, a region's customers, one reason code.
def _rr4_sample(rng, world, level):
    months, label = _months(2, rng)
    return {"category": rng.choice(CATEGORIES), "months": months, "period": label, "region": _region(rng, world),
            "reason_code": rng.choice(sorted(r["reason_code"] for r in world["reason_codes"]))}


def _rr4_render(p):
    return (f"Of the '{p['category']}' units on orders placed in {p['period']} by customers whose home city is in the "
            f"{p['region']} region, on orders whose status is delivered or returned, what percentage were returned "
            f"with reason code {p['reason_code']}? Units sold = the sum of those order lines' quantities; units "
            f"returned = the quantities the returns desk recorded against those same orders for '{p['category']}' "
            f"SKUs with that reason code, whenever processed. Round to 1 decimal place and reply with only the number.")


def _rr4_solve(call, p):
    skus = _skus_in(call, [p["category"]])
    who = customers_in_region(call, p["region"])
    orders = [o for m in p["months"] for st in ("delivered", "returned")
              for o in fetch_all(call, "order_ledger", "list_orders", {"month": m, "status": st})
              if o["customer_id"] in who]
    sold = sum(ln["qty"] for o in orders for ln in o["lines"] if ln["sku"] in skus)
    back = sum(r["qty"] for r in _returns_against(call, p["months"], {o["order_id"] for o in orders})
               if r["sku"] in skus and r["reason_code"] == p["reason_code"])
    return round(100 * back / sold, 1) if sold and back else None


# region_top_category L4: quarter, region and tier, revenue net of refunds against those orders.
def _rt4_sample(rng, world, level):
    months, label = _months(2, rng)
    return {"region": _region(rng, world), "months": months, "period": label,
            "tier": rng.choice(["bronze", "silver", "gold", "platinum"])}


def _rt4_render(p):
    return (f"Which product category earned the most delivered revenue NET of refunds in {p['period']} from "
            f"{p['tier']}-tier customers whose home city is in the {p['region']} region? Net revenue = the charged "
            f"line totals of those delivered orders minus the refunds the returns desk recorded against those same "
            f"orders, whenever processed. If categories tie, give the alphabetically first. Reply with only the "
            f"category name.")


def _rt4_solve(call, p):
    who = customers_in_region(call, p["region"], tier=p["tier"])
    cat = {x["sku"]: x["category"] for x in fetch_all(call, "product_catalog", "list_products")}
    orders = [o for o in _delivered(call, p["months"]) if o["customer_id"] in who]
    rev = defaultdict(int)
    for o in orders:
        for ln in o["lines"]:
            rev[cat[ln["sku"]]] += ln["qty"] * ln["unit_price"]
    for r in _returns_against(call, p["months"], {o["order_id"] for o in orders}):
        rev[cat[r["sku"]]] -= r["refund_amount"]
    return _unique_top(dict(rev))


# ticket_assignee_leader L4: quarter, region, priority; weekend-opened tickets only.
def _ta4_sample(rng, world, level):
    months, label = _months(2, rng)
    return {"priority": rng.choice(PRIORITIES[1:]), "months": months, "period": label, "region": _region(rng, world)}


def _ta4_render(p):
    return (f"Which Customer Care employee was assigned the most {p['priority']}-priority support tickets that were "
            f"opened on a Saturday or Sunday in {p['period']} by customers whose home city is in the {p['region']} "
            f"region? Count only tickets assigned to employees in the Customer Care department. If two or more tie, "
            f"give the lowest employee id. Reply with only the employee id.")


def _ta4_solve(call, p):
    care = {e["employee_id"] for e in fetch_all(call, "staff_directory", "list_employees",
                                                {"department": "Customer Care"})}
    who = customers_in_region(call, p["region"])
    n = defaultdict(int)
    for m in p["months"]:
        for t in fetch_all(call, "support_desk", "list_tickets", {"opened_month": m, "priority": p["priority"]}):
            if t["assigned_employee_id"] in care and t["customer_id"] in who and _weekday(t["opened_date"]) >= 5:
                n[t["assigned_employee_id"]] += 1
    return _unique_top(dict(n), minimum=2)


# ═══════════════════════════════════════════════════ new templates ═════════════════════════════════════════════

# promo_lift (train, number): revenue change during a promotion vs the same number of days before it.
def _pl_sample(rng, world, level):
    p = {"promo_code": rng.choice(world["promotions"])["promo_code"]}
    if level == 4:
        p["region"] = _region(rng, world)
    return p


def _pl_render(p):
    who = f" from customers whose home city is in the {p['region']} region" if "region" in p else ""
    return (f"By what percentage did delivered revenue{who} for products in promotion {p['promo_code']}'s category "
            f"change during the promotion compared with the period just before it? The promotion period runs from its "
            f"start date to its end date inclusive; the period before it has the same number of days and ends the day "
            f"before the start date. Use charged line totals on delivered orders, by order date. Give (during − "
            f"before) / before × 100, rounded to 1 decimal place (negative if revenue fell). Reply with only the "
            f"number.")


def _pl_solve(call, p):
    promo = call("promo_calendar", "get_promotion", {"promo_code": p["promo_code"]})
    start, end = date.fromisoformat(promo["start_date"]), date.fromisoformat(promo["end_date"])
    days = (end - start).days + 1
    pre_start = start - timedelta(days=days)
    if pre_start.year != YEAR:
        return None
    months = sorted({(pre_start + timedelta(days=i)).isoformat()[:7] for i in range((end - pre_start).days + 1)})
    skus = _skus_in(call, [promo["category"]])
    who = customers_in_region(call, p["region"]) if "region" in p else None
    during = before = 0
    for o in _delivered(call, months):
        if who is not None and o["customer_id"] not in who:
            continue
        amount = sum(ln["qty"] * ln["unit_price"] for ln in o["lines"] if ln["sku"] in skus)
        d = date.fromisoformat(o["order_date"])
        if start <= d <= end:
            during += amount
        elif pre_start <= d < start:
            before += amount
    return round(100 * (during - before) / before, 1) if before and during else None


# open_ticket_backlog (train, int): tickets opened in month M still open at the end of day D in month M+1.
def _ob_sample(rng, world, level):
    m = rng.randrange(1, 12)
    p = {"month": f"{YEAR}-{m:02d}", "day": f"{YEAR}-{m + 1:02d}-{rng.randrange(1, 29):02d}"}
    if level == 4:   # a region, across every priority (one priority per region left too few distinct counts)
        p["region"] = _region(rng, world)
    else:
        p["priority"] = rng.choice(PRIORITIES)
    return p


def _ob_render(p):
    who = f" by customers whose home city is in the {p['region']} region" if "region" in p else ""
    pri = f"{p['priority']}-priority " if "priority" in p else ""
    return (f"Of the {pri}support tickets opened in {_MONTH_NAMES[int(p['month'][5:]) - 1]} {YEAR}"
            f"{who}, how many were still open at the end of {p['day']}? A ticket is open if it has no closed_date, or "
            f"its closed_date is after {p['day']}. Reply with only the number.")


def _ob_solve(call, p):
    who = customers_in_region(call, p["region"]) if "region" in p else None
    args = {"opened_month": p["month"], **({"priority": p["priority"]} if "priority" in p else {})}
    n = sum(1 for t in fetch_all(call, "support_desk", "list_tickets", args)
            if (who is None or t["customer_id"] in who) and (t["closed_date"] is None or t["closed_date"] > p["day"]))
    return n or None


# net_margin_after_refunds (train, int ±1).
def _nm_sample(rng, world, level):
    sup = rng.choice([s for s in world["suppliers"] if s["currency"] != "CR"])
    months, label = _months(2, rng)
    p = {"supplier_id": sup["supplier_id"], "supplier_name": sup["name"], "months": months, "period": label}
    if level == 4:
        p["tiers"] = ["gold", "platinum"]
    return p


def _nm_render(p):
    tiers = " Count only orders from gold- or platinum-tier customers." if "tiers" in p else ""
    return (f"What was the gross margin NET of returns, in credits rounded to the nearest whole credit, on delivered "
            f"order lines placed in {p['period']} for products that {p['supplier_name']} ({p['supplier_id']}) supplies "
            f"under contract? For each such line, margin = quantity × (charged unit price − unit cost), where unit "
            f"cost is that supplier's contract unit_cost valid on the order date, converted to credits at that date's "
            f"rate; skip lines with no valid contract from this supplier. Then, for every unit the returns desk "
            f"recorded as returned against one of those lines (whenever processed), subtract (charged unit price − "
            f"unit cost) for that unit.{tiers} Reply with only the number.")


def _nm_solve(call, p):
    who = _tier_region_customers(call, p["tiers"], None) if "tiers" in p else None
    rates = _Rates3(call)
    lines = _supplier_line_margins(call, p, who)
    unit = {(o["order_id"], ln["sku"]): ln["unit_price"] - c["unit_cost"] / rates(c["currency"], o["order_date"])
            for o, ln, c in lines}
    total = sum(ln["qty"] * unit[(o["order_id"], ln["sku"])] for o, ln, _ in lines)
    refunded = 0
    for r in _returns_against(call, p["months"], {o["order_id"] for o, _, _ in lines}):
        if (r["order_id"], r["sku"]) in unit:
            total -= r["qty"] * unit[(r["order_id"], r["sku"])]
            refunded += 1
    return _round_half_up(total) if refunded else None


# cross_dept_share_leader (train, id).
def _cs_sample(rng, world, level):
    e = rng.choice(world["employees"])
    p = {"department": e["department"], "weeks": _weeks(rng, 4)}
    if level == 4:
        p["office"] = e["office"]
    return p


def _cs_render(p):
    w = p["weeks"]
    office = f" whose office is in {p['office']}" if "office" in p else ""
    return (f"Among {p['department']} employees{office} who logged at least 20 hours in total during weeks {w[0]} "
            f"through {w[-1]} (inclusive), who spent the largest share of their own logged hours on projects owned by "
            f"a different department? If two or more tie, give the lowest employee id. Reply with only the employee "
            f"id.")


def _cs_solve(call, p):
    staff = {e["employee_id"] for e in fetch_all(call, "staff_directory", "list_employees",
                                                 {"department": p["department"]})
             if "office" not in p or e["office"] == p["office"]}
    owner = {x["project_code"]: x["owner_department"] for x in call("project_registry", "list_projects", {})}
    tot, other = defaultdict(int), defaultdict(int)
    for wk in p["weeks"]:
        for t in fetch_all(call, "timesheets", "list_entries", {"week": wk}):
            if t["employee_id"] in staff:
                tot[t["employee_id"]] += t["hours"]
                if owner[t["project_code"]] != p["department"]:
                    other[t["employee_id"]] += t["hours"]
    share = {e: other[e] / h for e, h in tot.items() if h >= 20}
    best = _unique_top(share)
    return best if best is not None and share[best] > 0 else None


# unsupported_hard (train, cannot_complete): fields no table holds, next to records that look like they do.
def _uh_sample(rng, world, level):
    kind = rng.choice(["payment", "channel", "warranty", "manager", "tax"])
    if kind == "payment":
        o = rng.choice(world["orders"])
        return {"server": "order_ledger", "text": f"Which payment method was used for order {o['order_id']}?"}
    if kind == "channel":
        t = rng.choice(world["tickets"])
        return {"server": "support_desk",
                "text": f"Through which contact channel (phone, email or chat) was support ticket {t['ticket_id']} "
                        f"opened?"}
    if kind == "warranty":
        x = rng.choice(world["products"])
        return {"server": "product_catalog", "text": f"How long is the warranty period of {x['sku']} ({x['name']})?"}
    s = rng.choice(world["suppliers"])
    if kind == "manager":
        return {"server": "supplier_registry",
                "text": f"Who is the account manager for supplier {s['supplier_id']} ({s['name']})?"}
    return {"server": "supplier_registry", "text": f"What is the tax id of supplier {s['supplier_id']} ({s['name']})?"}


def _uh_render(p):
    return p["text"]


def _uh_solve(call, p):
    return {"cannot_complete": True}


# renegotiation_impact (HELD OUT, int ±1, signed).
def _ri_sample(rng, world, level):
    m = rng.randrange(7, 13)
    p = {"month": f"{YEAR}-{m:02d}", "category": rng.choice(CATEGORIES)}
    if level == 4:
        p["max_lead"] = rng.choice([10, 20, 30])
    return p


def _ri_render(p):
    among = (f" among suppliers with a lead time of {p['max_lead']} days or less" if "max_lead" in p else "")
    return (f"For delivered order lines placed in {_MONTH_NAMES[int(p['month'][5:]) - 1]} {YEAR} for products in the "
            f"'{p['category']}' category, take each line's cheapest supply contract valid on the order date{among} "
            f"(compare unit costs in credits at that date's rates; if two tie, take the lower supplier id). Consider "
            f"only lines where that contract is the SECOND of two contracts the same supplier holds for that SKU (a "
            f"mid-year renegotiation). What is the signed total of (second-contract unit cost − first-contract unit "
            f"cost) × quantity over those lines, converted to credits at each order date's rate and rounded to the "
            f"nearest whole credit? Negative if costs fell. Reply with only the number.")


def _ri_solve(call, p):
    skus = _skus_in(call, [p["category"]])
    lead = None
    if "max_lead" in p:
        lead = {s["supplier_id"]: s["lead_time_days"] for s in fetch_all(call, "supplier_registry", "list_suppliers")}
    rates, total, n, cache = _Rates3(call), 0.0, 0, {}
    for o in _delivered(call, [p["month"]]):
        for ln in o["lines"]:
            if ln["sku"] not in skus:
                continue
            if ln["sku"] not in cache:
                cache[ln["sku"]] = fetch_all(call, "supplier_registry", "list_contracts", {"sku": ln["sku"]})
            cs = [c for c in cache[ln["sku"]] if lead is None or lead[c["supplier_id"]] <= p["max_lead"]]
            best, tie = _cheapest3(cs, o["order_date"], rates)
            if tie:
                return None
            if not best:
                continue
            same = sorted((c for c in cs if c["supplier_id"] == best[1]), key=lambda c: c["valid_from"])
            if len(same) == 2 and _valid(same[1], o["order_date"]):
                total += ln["qty"] * (same[1]["unit_cost"] - same[0]["unit_cost"]) / rates(same[1]["currency"],
                                                                                          o["order_date"])
                n += 1
    return _round_half_up(total) if n >= 3 else None


# ticket_sla_miss (HELD OUT, number).
def _sb_sample(rng, world, level):
    q = rng.randrange(1, 5)
    p = {"priority": rng.choice(["high", "urgent"]), "quarter": q, "region": _region(rng, world),
         "days": rng.choice([5, 10, 15])}
    if level == 4:
        p["care_only"] = True
    return p


def _sb_render(p):
    care = " and assigned to a Customer Care employee" if "care_only" in p else ""
    return (f"What percentage of the {p['priority']}-priority support tickets opened in {_quarter_label(p['quarter'])} "
            f"by customers whose home city is in the {p['region']} region{care} were NOT closed within {p['days']} "
            f"days? A ticket misses if closed_date − opened_date is more than {p['days']} days, or it has no "
            f"closed_date. Round to 1 decimal place and reply with only the number.")


def _sb_solve(call, p):
    who = customers_in_region(call, p["region"])
    care = None
    if "care_only" in p:
        care = {e["employee_id"] for e in fetch_all(call, "staff_directory", "list_employees",
                                                    {"department": "Customer Care"})}
    pool = [t for m in range(3 * p["quarter"] - 2, 3 * p["quarter"] + 1)
            for t in fetch_all(call, "support_desk", "list_tickets",
                               {"opened_month": f"{YEAR}-{m:02d}", "priority": p["priority"]})
            if t["customer_id"] in who and (care is None or t["assigned_employee_id"] in care)]
    if len(pool) < 8:
        return None
    late = sum(1 for t in pool if t["closed_date"] is None or
               (date.fromisoformat(t["closed_date"]) - date.fromisoformat(t["opened_date"])).days > p["days"])
    x = round(100 * late / len(pool), 1)
    return x if 0 < x < 100 else None


# supplier_concentration (HELD OUT, id).
def _sc_sample(rng, world, level):
    months, label = _months(1 if level == 3 else 2, rng)
    p = {"region": _region(rng, world), "months": months, "period": label}
    if level == 4:
        p["tier"] = rng.choice(["bronze", "silver", "gold", "platinum"])
    return p


def _sc_render(p):
    tier = f"{p['tier']}-tier " if "tier" in p else ""
    return (f"For delivered order lines placed in {p['period']} by {tier}customers whose home city is in the "
            f"{p['region']} region, find each line's cheapest supply contract valid on the order date (compare unit "
            f"costs in credits at that date's rates; if two tie, take the lower supplier id), and credit the line's "
            f"charged total to that contract's supplier. Skip lines with no valid contract. Which supplier is credited "
            f"with the most revenue? If two or more tie, give the lowest supplier id. Reply with only the supplier id.")


def _sc_solve(call, p):
    who = customers_in_region(call, p["region"], tier=p.get("tier"))
    by_sku = defaultdict(list)
    for c in fetch_all(call, "supplier_registry", "list_contracts"):
        by_sku[c["sku"]].append(c)
    rates, credit = _Rates3(call), defaultdict(int)
    for o in _delivered(call, p["months"]):
        if o["customer_id"] not in who:
            continue
        for ln in o["lines"]:
            best, tie = _cheapest3(by_sku.get(ln["sku"], []), o["order_date"], rates)
            if tie:
                return None
            if best:
                credit[best[1]] += ln["qty"] * ln["unit_price"]
    return _unique_top(dict(credit))


R3_TEMPLATES = [
    Template("promo_lift", "train", "number", None, None, (3, 4), _pl_sample, _pl_render, _pl_solve,
             accept=lambda g: g is not None, tolerance=0.05, per_level=20),
    Template("open_ticket_backlog", "train", "int", None, None, (3, 4), _ob_sample, _ob_render, _ob_solve,
             accept=lambda g: g is not None, per_level=20),
    Template("net_margin_after_refunds", "train", "int", None, None, (3, 4), _nm_sample, _nm_render, _nm_solve,
             accept=lambda g: g is not None, tolerance=1.0, per_level=20),
    Template("cross_dept_share_leader", "train", "id", None, None, (3, 4), _cs_sample, _cs_render, _cs_solve,
             accept=lambda g: g is not None, per_level=20),
    Template("unsupported_hard", "train", "cannot_complete", (), (), (3,), _uh_sample, _uh_render, _uh_solve,
             per_level=20),
    Template("renegotiation_impact", "heldout", "int", None, None, (3, 4), _ri_sample, _ri_render, _ri_solve,
             accept=lambda g: g is not None, tolerance=1.0),
    Template("ticket_sla_miss", "heldout", "number", None, None, (3, 4), _sb_sample, _sb_render, _sb_solve,
             accept=lambda g: g is not None, tolerance=0.05),
    Template("supplier_concentration", "heldout", "id", None, None, (3, 4), _sc_sample, _sc_render, _sc_solve,
             accept=lambda g: g is not None),
]

# Level 4 on round-2 templates: (sample, render, solve, L4 count).
_L4 = {
    "margin_by_supplier": (_mg4_sample, _mg4_render, _mg4_solve, 20),
    "order_cheapest_suppliers": (_oc4_sample, _oc4_render, _oc4_solve, 20),
    "restock_slow_supplier": (_ss4_sample, _ss4_render, _ss4_solve, 20),
    "return_rate": (_rr4_sample, _rr4_render, _rr4_solve, 20),
    "region_top_category": (_rt4_sample, _rt4_render, _rt4_solve, 20),
    "ticket_assignee_leader": (_ta4_sample, _ta4_render, _ta4_solve, 12),
}
# Ten more level-3 instances (ids L3-010 .. L3-019) on these train templates.
_MORE_L3 = ("revenue_by_category", "top_customer", "ticket_assignee_leader", "cross_dept_hours", "repeat_buyers",
            "price_drift", "forecast_gap", "project_breadth", "top_return_reason")


def apply_round3(templates: dict[str, Template]) -> None:
    """Extend `templates` in place. Existing levels keep their code path and instance seeds."""
    for tid, (sample, render, solve, n4) in _L4.items():
        t = templates[tid]
        templates[tid] = dataclasses.replace(
            t, levels=(*t.levels, 4),
            # Level-4 params carry "_level": 4, so render and solve (which see only params) can route.
            sample=lambda rng, world, level, s=sample, o=t.sample: (
                {**s(rng, world, level), "_level": 4} if level == 4 else o(rng, world, level)),
            render=lambda p, r=render, o=t.render: (r if p.get("_level") == 4 else o)(p),
            solve=lambda call, p, v=solve, o=t.solve: (v if p.get("_level") == 4 else o)(call, p),
            accept=lambda g, a=t.accept: g is not None and a(g),
            level_counts={**(t.level_counts or {}), 4: n4})
    for tid in _MORE_L3:
        t = templates[tid]
        templates[tid] = dataclasses.replace(t, level_counts={**(templates[tid].level_counts or {}), 3: 20})
    templates.update({t.id: t for t in R3_TEMPLATES})
