"""Gold answers re-derived INDEPENDENTLY: straight from the snapshot tables, no tools, no pagination, and
no code shared with `templates.py`. The other gold test re-runs the templates' own solvers through the
meta-tools, which proves the TRANSPORT; this one proves the ARITHMETIC (a bug in a solver would be baked
into the committed gold and agree with itself there, but not here).
"""

from collections import defaultdict


def _derive(t, w):
    p = t["params"]
    product_cat = {x["sku"]: x["category"] for x in w["products"]}
    delivered = [o for o in w["orders"] if o["status"] == "delivered"
                 and o["order_date"][:7] in p.get("months", [])]

    def total(o):
        return sum(ln["qty"] * ln["unit_price"] for ln in o["lines"])

    if t["template"] == "revenue_by_category":
        return sum(ln["qty"] * ln["unit_price"] for o in delivered for ln in o["lines"]
                   if product_cat[ln["sku"]] == p["category"])
    if t["template"] == "restock_shortlist":
        wid = {x["name"]: x["warehouse_id"] for x in w["warehouses"]}[p["warehouse_name"]]
        return sorted(s["sku"] for s in w["stock"] if s["warehouse_id"] == wid
                      and s["qty_on_hand"] <= s["reorder_point"] and product_cat[s["sku"]] in p["categories"])
    if t["template"] == "top_customer":
        who = {c["customer_id"] for c in w["customers"] if c["city"] == p["city"] and c["tier"] == p["tier"]}
        spend = defaultdict(int)
        for o in delivered:
            if o["customer_id"] in who:
                spend[o["customer_id"]] += total(o)
        return max(spend, key=spend.get)
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
    assert checked >= 100


def test_unsupported_tasks_ask_for_a_field_no_table_has(taskset, world):
    """A decline gold is only right if the data genuinely lacks the field."""
    fields = {k for table in ("orders", "products", "employees", "customers") for row in world[table]
              for k in row}
    for t in taskset:
        if t["template"] == "unsupported_lookup":
            assert not fields & {"carrier", "tracking", "weight", "salary", "email"}
