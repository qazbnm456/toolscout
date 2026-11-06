"""Gold answers re-derived INDEPENDENTLY: straight from the snapshot tables, no tools, no pagination, and
no code shared with `templates.py`. The other gold test re-runs the templates' own solvers through the
meta-tools, which proves the TRANSPORT; this one proves the ARITHMETIC (a bug in a solver would be baked
into the committed gold and agree with itself there, but not here).
"""

import math
from collections import defaultdict
from datetime import date

_NOT_R2 = object()


def _rate(w, cur, day):
    return 1.0 if cur == "CR" else w["fx"][cur][day]


def _cheapest(w, sku, day):
    c = sorted((x["unit_cost"] / _rate(w, x["currency"], day), x["supplier_id"]) for x in w["contracts"]
               if x["sku"] == sku and x["valid_from"] <= day <= x["valid_to"])
    return c[0] if c else None


def _derive_r2(t, w, p, delivered, cust, city_region, wh_by_name, product_cat, total):
    name = t["template"]
    lead = {x["supplier_id"]: x["lead_time_days"] for x in w["suppliers"]}
    proj = {x["project_code"]: x for x in w["projects"]}
    emp = {e["employee_id"]: e for e in w["employees"]}
    order_by_id = {o["order_id"]: o for o in w["orders"]}

    def region_of(cid):
        return city_region[cust[cid]["city"]]

    if name == "margin_by_supplier":
        m = 0.0
        for o in delivered:
            if "tiers" in p and cust[o["customer_id"]]["tier"] not in p["tiers"]:
                continue
            for ln in o["lines"]:
                for c in w["contracts"]:
                    if (c["supplier_id"] == p["supplier_id"] and c["sku"] == ln["sku"]
                            and c["valid_from"] <= o["order_date"] <= c["valid_to"]):
                        m += ln["qty"] * (ln["unit_price"] - c["unit_cost"] / _rate(w, c["currency"], o["order_date"]))
        return math.floor(m + 0.5)
    if name == "promo_window_revenue":
        if "promo_code" in p:
            pr = next(x for x in w["promotions"] if x["promo_code"] == p["promo_code"])
        else:
            q = p["quarter"]
            cands = [x for x in w["promotions"] if x["category"] == p["category"]
                     and 3 * q - 2 <= int(x["start_date"][5:7]) <= 3 * q]
            pr = min(cands, key=lambda x: (-(date.fromisoformat(x["end_date"]) - date.fromisoformat(x["start_date"])).days,
                                           x["start_date"], x["promo_code"]))
        return sum(ln["qty"] * ln["unit_price"] for o in w["orders"] if o["status"] == "delivered"
                   and pr["start_date"] <= o["order_date"] <= pr["end_date"]
                   for ln in o["lines"] if product_cat[ln["sku"]] == pr["category"])
    if name == "region_top_category":
        rev = defaultdict(int)
        for o in delivered:
            c = cust[o["customer_id"]]
            if region_of(c["customer_id"]) == p["region"] and ("tier" not in p or c["tier"] == p["tier"]):
                for ln in o["lines"]:
                    rev[product_cat[ln["sku"]]] += ln["qty"] * ln["unit_price"]
        return min(rev, key=lambda k: (-rev[k], k))
    if name == "return_rate":
        orders = [o for o in w["orders"] if o["status"] in ("delivered", "returned")
                  and o["order_date"][:7] in p["months"]
                  and ("tiers" not in p or cust[o["customer_id"]]["tier"] in p["tiers"])]
        ids = {o["order_id"] for o in orders}
        sold = sum(ln["qty"] for o in orders for ln in o["lines"] if product_cat[ln["sku"]] == p["category"])
        back = sum(r["qty"] for r in w["returns"] if r["order_id"] in ids and product_cat[r["sku"]] == p["category"])
        return round(100 * back / sold, 1)
    if name == "ticket_assignee_leader":
        n = defaultdict(int)
        for tk in w["tickets"]:
            e = emp.get(tk["assigned_employee_id"])
            c = cust[tk["customer_id"]]
            if (e and e["department"] == "Customer Care" and tk["priority"] == p["priority"]
                    and tk["opened_date"][:7] in p["months"] and ("tier" not in p or c["tier"] == p["tier"])
                    and ("region" not in p or region_of(c["customer_id"]) == p["region"])):
                n[e["employee_id"]] += 1
        return min(n, key=lambda k: (-n[k], k))
    if name == "restock_slow_supplier":
        wid = wh_by_name[p["warehouse_name"]]["warehouse_id"]
        out = []
        for s in sorted(w["stock"], key=lambda s: s["sku"]):
            if s["warehouse_id"] != wid or s["qty_on_hand"] > s["reorder_point"]:
                continue
            if "category" in p and product_cat[s["sku"]] != p["category"]:
                continue
            valid = [c for c in w["contracts"] if c["sku"] == s["sku"] and c["valid_from"] <= p["day"] <= c["valid_to"]]
            if "cheapest" in p:
                best = _cheapest(w, s["sku"], p["day"])
                if best and lead[best[1]] > p["max_lead"]:
                    out.append(s["sku"])
            elif not any(lead[c["supplier_id"]] <= p["max_lead"] for c in valid):
                out.append(s["sku"])
        return out
    if name == "city_refunds":
        def ok(cid):
            return region_of(cid) == p["region"] if "region" in p else cust[cid]["city"] == p["city"]
        return sum(r["refund_amount"] for r in w["returns"] if r["processed_date"][:7] in p["months"]
                   and ok(order_by_id[r["order_id"]]["customer_id"]))
    if name == "cross_dept_hours":
        return sum(x["hours"] for x in w["timesheets"] if x["week"] in p["weeks"]
                   and emp[x["employee_id"]]["department"] == p["department"]
                   and emp[x["employee_id"]]["office"] == p["office"]
                   and proj[x["project_code"]]["owner_department"] != p["department"]
                   and proj[x["project_code"]]["weekly_budget_hours"] >= p.get("min_budget", 0))
    if name == "shortfall_cost":
        wid = wh_by_name[p["warehouse_name"]]["warehouse_id"]
        tot = 0.0
        for s in w["stock"]:
            if s["warehouse_id"] == wid and s["qty_on_hand"] <= s["reorder_point"] and (
                    "category" not in p or product_cat[s["sku"]] == p["category"]):
                best = _cheapest(w, s["sku"], p["day"])
                if best:
                    tot += best[0] * (s["reorder_point"] - s["qty_on_hand"] + 1)
        return math.floor(tot + 0.5)
    if name == "repeat_buyers":
        months = {f"2031-{m:02d}" for m in range(3 * p["quarter"] - 2, 3 * p["quarter"] + 1)}
        seen = defaultdict(set)
        for o in w["orders"]:
            c = cust[o["customer_id"]]
            if (o["status"] == "delivered" and o["order_date"][:7] in months and c["tier"] == p["tier"]
                    and ("region" not in p or region_of(c["customer_id"]) == p["region"])):
                seen[c["customer_id"]].add(o["order_date"][:7])
        return sum(1 for v in seen.values() if len(v) >= p["k"])
    if name == "price_drift":
        old = {x["sku"]: x["list_price"] for x in w["archive_products"]}
        stocked = None
        if "warehouse_name" in p:
            wid = wh_by_name[p["warehouse_name"]]["warehouse_id"]
            stocked = {s["sku"] for s in w["stock"] if s["warehouse_id"] == wid}
        return sum(1 for x in w["products"] if x["category"] in p["categories"] and x["sku"] in old
                   and (stocked is None or x["sku"] in stocked) and x["list_price"] > old[x["sku"]] * (1 + p["pct"] / 100))
    if name == "forecast_gap":
        if "region" in p:
            wids = {x["warehouse_id"] for x in w["warehouses"] if city_region[x["city"]] == p["region"]}
        else:
            wids = {wh_by_name[p["warehouse_name"]]["warehouse_id"]}
        now = {(s["warehouse_id"], s["sku"]): s for s in w["stock"]}
        return sum(1 for f in w["forecast"] if f["warehouse_id"] in wids and product_cat[f["sku"]] in p["categories"]
                   and now[(f["warehouse_id"], f["sku"])]["qty_on_hand"] > now[(f["warehouse_id"], f["sku"])]["reorder_point"]
                   and f["projected_qty"] <= f["reorder_point"])
    if name == "ticket_resolution":
        d = [(date.fromisoformat(x["closed_date"]) - date.fromisoformat(x["opened_date"])).days for x in w["tickets"]
             if x["priority"] == p["priority"] and x["opened_date"][:7] in p["months"] and x["closed_date"]
             and region_of(x["customer_id"]) == p["region"]]
        return round(sum(d) / len(d), 2)
    if name == "order_cheapest_suppliers":
        out = set()
        for oid in p["order_ids"]:
            o = order_by_id[oid]
            for ln in o["lines"]:
                best = _cheapest(w, ln["sku"], o["order_date"])
                if best:
                    out.add(best[1])
        return sorted(out)
    if name == "tier_share_shift":
        def share(q):
            months = {f"2031-{m:02d}" for m in range(3 * q - 2, 3 * q + 1)}
            os_ = [o for o in w["orders"] if o["status"] == "delivered" and o["order_date"][:7] in months]
            return 100 * sum(total(o) for o in os_ if cust[o["customer_id"]]["tier"] in ("gold", "platinum")) / sum(
                total(o) for o in os_)
        return round(share(p["q2"]) - share(p["q1"]), 1)
    if name == "project_breadth":
        seen = defaultdict(set)
        for x in w["timesheets"]:
            if (x["week"] in p["weeks"] and emp[x["employee_id"]]["department"] == p["department"]
                    and ("other_only" not in p or proj[x["project_code"]]["owner_department"] != p["department"])):
                seen[x["employee_id"]].add(x["project_code"])
        return min(seen, key=lambda k: (-len(seen[k]), k))
    if name == "top_return_reason":
        n = defaultdict(int)
        for r in w["returns"]:
            if (r["processed_date"][:7] in p["months"] and product_cat[r["sku"]] == p["category"]
                    and ("city" not in p or cust[order_by_id[r["order_id"]]["customer_id"]]["city"] == p["city"])):
                n[r["reason_code"]] += r["qty"]
        return min(n, key=lambda k: (-n[k], k))
    if name == "region_customer_pressure":
        best = []
        for r in w["regions"]:
            n_wh = sum(1 for x in w["warehouses"] if x["city"] in r["cities"])
            n_c = sum(1 for c in w["customers"] if c["tier"] == p["tier"] and c["city"] in r["cities"])
            if n_wh:
                best.append((-n_c / n_wh, r["region"]))
        return min(best)[1]
    return _NOT_R2


def _derive(t, w):
    p = t["params"]
    product_cat = {x["sku"]: x["category"] for x in w["products"]}
    delivered = [o for o in w["orders"] if o["status"] == "delivered"
                 and o["order_date"][:7] in p.get("months", [])]
    cust = {c["customer_id"]: c for c in w["customers"]}
    city_region = {c: r["region"] for r in w["regions"] for c in r["cities"]}
    wh_by_name = {x["name"]: x for x in w["warehouses"]}

    def total(o):
        return sum(ln["qty"] * ln["unit_price"] for ln in o["lines"])

    def in_region(cid):
        return city_region[cust[cid]["city"]] == p["region"]

    if t["template"] == "revenue_by_category":
        return sum(ln["qty"] * ln["unit_price"] for o in delivered for ln in o["lines"]
                   if product_cat[ln["sku"]] == p["category"] and ("region" not in p or in_region(o["customer_id"])))
    if t["template"] == "restock_shortlist":
        if "region" in p:
            wids = {x["warehouse_id"] for x in w["warehouses"] if city_region[x["city"]] == p["region"]}
        else:
            wids = {wh_by_name[p["warehouse_name"]]["warehouse_id"]}
        return sorted({s["sku"] for s in w["stock"] if s["warehouse_id"] in wids
                       and s["qty_on_hand"] <= s["reorder_point"] and product_cat[s["sku"]] in p["categories"]})
    if t["template"] == "top_customer":
        if "region" in p:
            who = {c for c, x in cust.items() if x["tier"] == p["tier"] and in_region(c)}
        else:
            who = {c["customer_id"] for c in w["customers"] if c["city"] == p["city"] and c["tier"] == p["tier"]}
        spend = defaultdict(int)
        for o in delivered:
            if o["customer_id"] in who:
                spend[o["customer_id"]] += total(o)
        return min(spend, key=lambda c: (-spend[c], c))
    r2 = _derive_r2(t, w, p, delivered, cust, city_region, wh_by_name, product_cat, total)
    if r2 is not _NOT_R2:
        return r2
    if t["template"] == "project_overrun":
        mine = {x["project_code"]: x["weekly_budget_hours"] for x in w["projects"]
                if x["owner_department"] == p["department"]}
        hours = defaultdict(int)
        for e in w["timesheets"]:
            if e["week"] in p["weeks"] and e["project_code"] in mine:
                hours[e["project_code"]] += e["hours"]
        return sorted(c for c, h in hours.items() if h > mine[c] * len(p["weeks"]))
    if t["template"] == "fx_avg_order":
        who = {c["customer_id"] for c in w["customers"] if c["tier"] == p["tier"]}
        vals = [total(o) * w["fx"][p["currency"]][o["order_date"]] for o in delivered if o["customer_id"] in who]
        return round(sum(vals) / len(vals), 2)
    raise AssertionError(t["template"])


def test_every_gold_matches_an_independent_derivation(taskset, world):
    checked = 0
    for t in taskset:
        if t["gold_kind"] == "cannot_complete":
            continue
        assert _derive(t, world) == t["gold"], t["id"]
        checked += 1
    assert checked >= 550


def _all_fields(world):
    out = set()
    for table in world.values():
        rows = table if isinstance(table, list) else [table]
        for row in rows:
            if isinstance(row, dict):
                out |= {k.lower() for k in row}
    return out


def test_unsupported_tasks_ask_for_data_the_world_lacks(taskset, world):
    """A decline gold is only right if the data genuinely lacks the answer: no table has the field, and the
    partial-data variants ask outside the covered range (timesheets W01-W26, fx and orders in 2031 only)."""
    fields = _all_fields(world)
    assert not {f for f in fields if any(w in f for w in ("carrier", "tracking", "weight", "salary", "email",
                                                          "shipping", "approv", "on_time", "satisf"))}
    assert max(t["week"] for t in world["timesheets"]) == "2031-W26"
    assert all(d.startswith("2031-") for cur in world["fx"].values() for d in cur)
    assert all(o["order_date"].startswith("2031-") for o in world["orders"])
    texts = [t["task"] for t in taskset if t["gold_kind"] == "cannot_complete"]
    assert len(texts) >= 30
