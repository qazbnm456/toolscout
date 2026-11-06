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
