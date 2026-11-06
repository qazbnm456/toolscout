"""The fictional world the synth servers serve — generated ONCE from a seed, then committed as snapshots.

Everything here is invented: a fictional outfitter trading in a fictional currency (credits, "CR") across
fictional cities in the fictional year 2031, plus its workforce and a few fictional foreign currencies. No
real people, companies, products, or security data. Names are built from syllables, so they are
pronounceable but not real.

Determinism: only `random.Random(seed)` methods whose output is stable across CPython 3.11-3.13 are used
(`random`, `randrange`, `choice`, `sample`, `shuffle`); floats are rounded before they are stored, and the
JSON is written with sorted keys and fixed separators. The committed snapshots carry a sha256 per file in
`MANIFEST.json`, and the suite regenerates them and compares bytes, so any drift is caught.

The world deliberately contains TRAPS a careful planner must handle, not just read past:
- an order line's `unit_price` is the price CHARGED, which differs from the product's current list price;
- orders have non-`delivered` statuses that most questions exclude;
- distractor tables (an archived price list, a demand FORECAST, QA sandbox orders, former staff) look like
  the real ones but answer a different question.
"""

from __future__ import annotations

import datetime as _dt
import json
import random
from typing import Any

GENERATOR_VERSION = 1
DEFAULT_SEED = 2031
YEAR = 2031

CATEGORIES = ["lanterns", "tents", "ropes", "cookware", "packs", "boots", "maps", "filters"]
TIERS = ["bronze", "silver", "gold", "platinum"]
STATUSES = [("delivered", 75), ("cancelled", 10), ("returned", 8), ("pending", 7)]
DEPARTMENTS = ["Field Research", "Logistics", "Product Design", "Customer Care", "Finance",
               "Platform", "Retail Ops"]
CURRENCIES = {"VEL": 3.7, "ORM": 0.42, "TASK": 118.0, "QIN": 9.15}   # 1 CR = this many units, at Jan 1

_ON = ["bra", "ve", "lo", "tan", "nick", "qui", "len", "dar", "mo", "sel", "rin", "ka", "thu", "pe",
       "zor", "vi", "an", "del", "fen", "ost", "ul", "mar", "ish", "cal", "bel", "nor", "tis", "gar"]
_PRODUCT_ADJ = ["Quillfern", "Ashgrove", "Brightmere", "Coldwick", "Dunmoor", "Emberlay", "Frostvale",
                "Glimmerden", "Hollowpine", "Ironbark", "Juniperline", "Kestrelwing", "Lowmarsh",
                "Mistral", "Nettlecombe", "Oakhallow", "Pebbleford", "Ravensill", "Saltreach", "Thornby"]
_PRODUCT_NOUN = {
    "lanterns": ["Lantern", "Lamp", "Beacon", "Glowcell"],
    "tents": ["Tent", "Shelter", "Bivy", "Canopy"],
    "ropes": ["Rope", "Cord", "Line", "Tether"],
    "cookware": ["Pot", "Kettle", "Skillet", "Stove"],
    "packs": ["Pack", "Satchel", "Rucksack", "Duffel"],
    "boots": ["Boot", "Trailshoe", "Gaiter", "Clog"],
    "maps": ["Atlas", "Chart", "Map", "Gazetteer"],
    "filters": ["Filter", "Purifier", "Straw", "Sieve"],
}


def _word(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(_ON) for _ in range(n)).capitalize()


def _unique_names(rng: random.Random, count: int, parts: tuple[int, int], two_words: bool) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    while len(out) < count:
        name = _word(rng, rng.randrange(*parts))
        if two_words:
            name += " " + _word(rng, rng.randrange(*parts))
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _days(year: int) -> list[_dt.date]:
    d, out = _dt.date(year, 1, 1), []
    while d.year == year:
        out.append(d)
        d += _dt.timedelta(days=1)
    return out


def generate(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Build every table of the world. Pure: same seed → same dict, byte-for-byte once serialized."""
    rng = random.Random(seed)

    cities = _unique_names(rng, 10, (2, 4), two_words=False)

    # ── products (current) + the archived 2029 price list (distractor) ────────────────────────────
    products: list[dict] = []
    names_seen: set[str] = set()
    sku_numbers = rng.sample(range(1000, 9999), 420)
    for i, num in enumerate(sku_numbers):
        cat = CATEGORIES[i % len(CATEGORIES)]
        while True:
            name = (f"{rng.choice(_PRODUCT_ADJ)} {rng.choice(_PRODUCT_NOUN[cat])} "
                    f"{rng.choice('ABCDEFGHJK')}{rng.randrange(1, 10)}")
            if name not in names_seen:
                names_seen.add(name)
                break
        products.append({"sku": f"SKU-{num}", "name": name, "category": cat,
                         "list_price": rng.randrange(8, 640)})
    products.sort(key=lambda p: p["sku"])
    archive = []
    for p in rng.sample(products, 260):
        archive.append({"sku": p["sku"], "name": p["name"], "category": p["category"],
                        "list_price": max(5, round(p["list_price"] * rng.choice([0.7, 0.8, 0.9, 1.15]))),
                        "price_list": "2029"})
    for num in rng.sample(sorted(set(range(1000, 9999)) - set(sku_numbers)), 60):   # discontinued
        cat = rng.choice(CATEGORIES)
        archive.append({"sku": f"SKU-{num}", "name": f"{rng.choice(_PRODUCT_ADJ)} "
                        f"{rng.choice(_PRODUCT_NOUN[cat])} Legacy", "category": cat,
                        "list_price": rng.randrange(8, 400), "price_list": "2029"})
    archive.sort(key=lambda p: p["sku"])

    # ── warehouses, live stock, and the next-quarter FORECAST (distractor) ────────────────────────
    warehouses = [{"warehouse_id": f"WH-{c[:3].upper()}{i}", "name": f"{c} Depot", "city": c}
                  for i, c in enumerate(rng.sample(cities, 6), start=1)]
    stock, forecast = [], []
    for wh in warehouses:
        for p in products:
            if rng.random() < 0.62:
                reorder = rng.randrange(10, 61)
                qty = rng.randrange(0, 40) if rng.random() < 0.18 else rng.randrange(reorder + 1, 260)
                stock.append({"warehouse_id": wh["warehouse_id"], "sku": p["sku"], "qty_on_hand": qty,
                              "reorder_point": reorder})
                forecast.append({"warehouse_id": wh["warehouse_id"], "sku": p["sku"],
                                 "projected_qty": max(0, qty + rng.randrange(-60, 61)),
                                 "reorder_point": reorder, "horizon": f"{YEAR}-Q3"})

    # ── customers ─────────────────────────────────────────────────────────────────────────────────
    cust_names = _unique_names(rng, 360, (2, 4), two_words=True)
    customers = [{"customer_id": f"CUS-{n:04d}", "name": cust_names[i], "city": rng.choice(cities),
                  "tier": TIERS[min(3, int(rng.random() ** 1.6 * 4))]}   # skewed toward bronze
                 for i, n in enumerate(rng.sample(range(1, 10000), 360))]
    customers.sort(key=lambda c: c["customer_id"])

    # ── orders (Jan–Dec), with line prices CHARGED (≠ current list) + QA sandbox orders ────────────
    by_sku = {p["sku"]: p for p in products}
    skus = [p["sku"] for p in products]
    status_pool = [s for s, w in STATUSES for _ in range(w)]
    days = _days(YEAR)
    orders: list[dict] = []
    seq = 0
    for month in range(1, 13):
        month_days = [d for d in days if d.month == month]
        for _ in range(rng.randrange(220, 281)):
            seq += 1
            lines = []
            for sku in rng.sample(skus, rng.randrange(1, 5)):
                factor = rng.choice([0.85, 0.9, 0.95, 1.0, 1.0, 1.05, 1.1])
                price = max(1, round(by_sku[sku]["list_price"] * factor))
                lines.append({"sku": sku, "qty": rng.randrange(1, 7), "unit_price": price})
            orders.append({"order_id": f"ORD-{seq:06d}", "customer_id": rng.choice(customers)["customer_id"],
                           "order_date": rng.choice(month_days).isoformat(), "status": rng.choice(status_pool),
                           "lines": lines})
    sandbox = []
    for i in range(1, 181):
        d = rng.choice(days)
        sandbox.append({"order_id": f"TST-{i:05d}", "customer_id": rng.choice(customers)["customer_id"],
                        "order_date": d.isoformat(), "status": "delivered",
                        "lines": [{"sku": rng.choice(skus), "qty": rng.randrange(1, 40),
                                   "unit_price": rng.randrange(1, 900)}]})

    # ── workforce: employees, former staff (distractor), projects, weekly timesheets ──────────────
    emp_names = _unique_names(rng, 190, (2, 4), two_words=True)
    employees = []
    for i, n in enumerate(rng.sample(range(1, 10000), 140)):
        employees.append({"employee_id": f"EMP-{n:04d}", "name": emp_names[i],
                          "department": DEPARTMENTS[i % len(DEPARTMENTS)], "office": rng.choice(cities),
                          "start_date": rng.choice(_days(YEAR - 1 - rng.randrange(0, 6))).isoformat()})
    employees.sort(key=lambda e: e["employee_id"])
    alumni = [{"employee_id": f"EMP-{n:04d}", "name": emp_names[140 + i],
               "department": rng.choice(DEPARTMENTS), "left_on": rng.choice(_days(YEAR - 1)).isoformat()}
              for i, n in enumerate(rng.sample(sorted(set(range(1, 10000)) -
                                                      {int(e["employee_id"][4:]) for e in employees}), 50))]
    alumni.sort(key=lambda e: e["employee_id"])
    codes = rng.sample(range(100, 1000), 30)
    projects = [{"project_code": f"PRJ-{c}", "name": f"{rng.choice(_PRODUCT_ADJ)} {_word(rng, 2)}",
                 "owner_department": DEPARTMENTS[i % len(DEPARTMENTS)],
                 "weekly_budget_hours": rng.randrange(110, 200, 5)}
                for i, c in enumerate(codes)]
    projects.sort(key=lambda p: p["project_code"])
    proj_by_dept: dict[str, list[str]] = {}
    for p in projects:
        proj_by_dept.setdefault(p["owner_department"], []).append(p["project_code"])
    all_codes = [p["project_code"] for p in projects]
    timesheets = []
    for week in range(1, 27):
        for e in employees:
            home = proj_by_dept.get(e["department"], all_codes)
            for _ in range(rng.randrange(1, 4)):
                code = rng.choice(home) if rng.random() < 0.8 else rng.choice(all_codes)
                timesheets.append({"week": f"{YEAR}-W{week:02d}", "employee_id": e["employee_id"],
                                   "project_code": code, "hours": rng.randrange(2, 31)})

    # ── fictional FX: 1 CR = rate units, a daily random walk ──────────────────────────────────────
    fx: dict[str, dict[str, float]] = {}
    for cur, start in CURRENCIES.items():
        rate, series = start, {}
        for d in days:
            rate = rate * (1 + rng.randrange(-150, 151) / 10000)
            series[d.isoformat()] = round(rate, 4)
        fx[cur] = series

    return {
        "meta": {"generator_version": GENERATOR_VERSION, "seed": seed, "year": YEAR, "base_currency": "CR"},
        "products": products, "archive_products": archive,
        "warehouses": warehouses, "stock": stock, "forecast": forecast,
        "customers": customers, "orders": orders, "sandbox_orders": sandbox,
        "departments": [{"department": d} for d in DEPARTMENTS],
        "employees": employees, "alumni": alumni, "projects": projects, "timesheets": timesheets,
        "fx": fx,
    }


def dumps(value: Any) -> str:
    """The ONE serialization for snapshots: sorted keys, fixed separators, trailing newline."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


EXT_VERSION = 1
REASON_CODES = {"RSN-101": "arrived damaged", "RSN-102": "wrong item shipped", "RSN-103": "not as described",
                "RSN-104": "arrived too late", "RSN-105": "changed mind", "RSN-106": "defective in use"}
PRIORITIES = ["low", "normal", "high", "urgent"]


def _clamp(d: _dt.date) -> _dt.date:
    return min(d, _dt.date(YEAR, 12, 31))


def generate_ext(seed: int, base: dict[str, Any]) -> dict[str, Any]:
    """The round-2 tables, from their OWN random stream, so the base tables (and every task built only on
    them) stay byte-identical. They join the base world by key: city, sku, order_id, customer_id,
    employee_id, and the fx currencies."""
    rng = random.Random(f"{seed}:ext:{EXT_VERSION}")
    days = _days(YEAR)

    # ── regions: 5 fictional regions of 2 cities each ─────────────────────────────────────────────
    cities = sorted({c["city"] for c in base["customers"]} | {w["city"] for w in base["warehouses"]})
    rng.shuffle(cities)
    names = _unique_names(rng, 5, (2, 4), two_words=False)
    regions = [{"region": f"{n}march" if i % 2 else f"{n} Vale", "cities": sorted(cities[2 * i:2 * i + 2])}
               for i, n in enumerate(names)]
    regions.sort(key=lambda r: r["region"])

    # ── suppliers + supply contracts (unit_cost in the supplier's currency) + prospects (distractor) ─
    currencies = sorted(base["fx"])
    countries = _unique_names(rng, 6, (2, 4), two_words=False)
    sup_ids = sorted(rng.sample(range(100, 1000), 24))
    sup_names = _unique_names(rng, 40, (2, 3), two_words=False)
    suppliers = [{"supplier_id": f"SUP-{n}", "name": f"{sup_names[i]} Supply", "country": rng.choice(countries),
                  "currency": "CR" if i % 6 == 0 else rng.choice(currencies),
                  "lead_time_days": rng.randrange(3, 46), "rating": rng.randrange(1, 6)}
                 for i, n in enumerate(sup_ids)]

    def rate_on(cur: str, day: str) -> float:
        return 1.0 if cur == "CR" else base["fx"][cur][day]

    contracts = []
    for p in base["products"]:
        for s in rng.sample(suppliers, rng.randrange(1, 4)):
            factor = rng.randrange(35, 76) / 100
            if rng.random() < 0.25:   # a mid-year renegotiation: two windows, two prices
                cut = rng.choice(days[60:300])
                windows = [(days[0], cut - _dt.timedelta(days=1)), (cut, days[-1])]
            else:
                windows = [(days[0], days[-1])]
            for start, end in windows:
                f = factor * rng.choice([0.9, 1.0, 1.1])
                cost = round(p["list_price"] * f * rate_on(s["currency"], start.isoformat()), 2)
                contracts.append({"sku": p["sku"], "supplier_id": s["supplier_id"], "unit_cost": cost,
                                  "currency": s["currency"], "min_order_qty": rng.choice([1, 5, 10, 25]),
                                  "valid_from": start.isoformat(), "valid_to": end.isoformat()})
    contracts.sort(key=lambda c: (c["supplier_id"], c["sku"], c["valid_from"]))
    prospects = [{"prospect_name": f"{sup_names[24 + i]} Trading", "sku": rng.choice(base["products"])["sku"],
                  "quoted_unit_cost": rng.randrange(2, 300), "currency": rng.choice(currencies + ["CR"]),
                  "status": "quote only, no contract"} for i in range(16)]

    # ── promotions + drafts (distractor) ───────────────────────────────────────────────────────────
    promotions = []
    for i, code in enumerate(sorted(rng.sample(range(200, 1000), 40))):
        start = rng.choice(days[:-25])
        promotions.append({"promo_code": f"PRM-{code}", "category": CATEGORIES[i % len(CATEGORIES)],
                           "start_date": start.isoformat(),
                           "end_date": (start + _dt.timedelta(days=rng.randrange(3, 21))).isoformat(),
                           "discount_pct": rng.choice([5, 10, 15, 20, 25])})
    drafts = [{"draft_id": f"DRF-{i:03d}", "category": rng.choice(CATEGORIES),
               "proposed_start": rng.choice(days).isoformat(), "discount_pct": rng.choice([10, 30, 40]),
               "status": "proposed, never run"} for i in range(1, 21)]

    # ── returns: full returns for `returned` orders, occasional partial ones on delivered orders ──────
    returns, seq = [], 0
    for o in base["orders"]:
        if o["status"] == "returned":
            lines = [(ln, ln["qty"]) for ln in o["lines"]]
        elif o["status"] == "delivered" and rng.random() < 0.06:
            ln = rng.choice(o["lines"])
            lines = [(ln, rng.randrange(1, ln["qty"] + 1))]
        else:
            continue
        processed = _clamp(_dt.date.fromisoformat(o["order_date"]) + _dt.timedelta(days=rng.randrange(3, 21)))
        for ln, qty in lines:
            seq += 1
            returns.append({"return_id": f"RET-{seq:05d}", "order_id": o["order_id"], "sku": ln["sku"],
                            "qty": qty, "refund_amount": qty * ln["unit_price"],
                            "reason_code": rng.choice(sorted(REASON_CODES)), "processed_date": processed.isoformat()})
    reason_codes = [{"reason_code": k, "description": v} for k, v in sorted(REASON_CODES.items())]

    # ── support tickets: mostly assigned to Customer Care staff ────────────────────────────────────
    care = [e["employee_id"] for e in base["employees"] if e["department"] == "Customer Care"]
    others = [e["employee_id"] for e in base["employees"] if e["department"] != "Customer Care"]
    tickets = []
    for n in sorted(rng.sample(range(10000, 99999), 1500)):
        opened = rng.choice(days)
        closed = opened + _dt.timedelta(days=rng.randrange(0, 31))
        tickets.append({"ticket_id": f"TCK-{n}", "customer_id": rng.choice(base["customers"])["customer_id"],
                        "opened_date": opened.isoformat(),
                        "closed_date": closed.isoformat() if rng.random() < 0.8 and closed.year == YEAR else None,
                        "priority": rng.choices(PRIORITIES, weights=[30, 40, 20, 10])[0],
                        "assigned_employee_id": rng.choice(care) if rng.random() < 0.85 else rng.choice(others)})

    return {"regions": regions, "suppliers": suppliers, "contracts": contracts, "supplier_prospects": prospects,
            "promotions": promotions, "promo_drafts": drafts, "returns": returns, "reason_codes": reason_codes,
            "tickets": tickets, "meta_ext": {"ext_version": EXT_VERSION, "seed": seed}}


def generate_full(seed: int = DEFAULT_SEED) -> dict[str, Any]:
    base = generate(seed)
    return {**base, **generate_ext(seed, base)}
