"""The synth toolspace: fictional MCP-like servers over the committed world snapshots, as a `StaticCatalog`.

Eight servers answer real questions; four DISTRACTORS (`product_archive`, `demand_forecast`,
`order_sandbox`, `staff_alumni`) look like them but serve a different question, so picking the server
(ISL) is part of the task. Their descriptions say honestly what they are. A planner that reads the index
can avoid them; one that pattern-matches on names will not.

Every list endpoint PAGINATES (`page_size` ≤ 50) and returns `{items, page, page_size, total_items,
total_pages}`, so a question over a month of orders or a warehouse's stock needs a fetch loop plus
filter/join/aggregate in REPL code: printing the data cannot fit the REPL output cap. Bad arguments raise
`ValueError` with a fixable message, which toolscout's `call_tool` returns to the planner as text.

All tools are pure functions of the snapshot: no clock, no randomness, no network.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

from toolscout.catalog import Param, StaticCatalog, ToolSpec

from .world import CATEGORIES, TIERS

MAX_PAGE_SIZE = 50
DISTRACTOR_SERVERS = ("product_archive", "demand_forecast", "order_sandbox", "staff_alumni")
TABLES = ("meta", "products", "archive_products", "warehouses", "stock", "forecast", "customers", "orders",
          "sandbox_orders", "departments", "employees", "alumni", "projects", "timesheets", "fx")

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_WEEK = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")


def default_data_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "data")


def load_world(data_dir: str | None = None) -> dict[str, Any]:
    """Read the committed snapshot tables (one JSON file per table under `<data_dir>/snapshots/`)."""
    snap = os.path.join(data_dir or default_data_dir(), "snapshots")
    world = {}
    for t in TABLES:
        with open(os.path.join(snap, f"{t}.json"), encoding="utf-8") as fh:
            world[t] = json.load(fh)
    return world


def _page(items: list, page: int, page_size: int) -> dict:
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}, got {page_size}")
    if page < 1:
        raise ValueError(f"page starts at 1, got {page}")
    total_pages = max(1, -(-len(items) // page_size))
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "page": page, "page_size": page_size,
            "total_items": len(items), "total_pages": total_pages}


def _pager() -> list[Param]:
    return [Param("page", "int", False, 1, "1-based page number"),
            Param("page_size", "int", False, 25, f"items per page, at most {MAX_PAGE_SIZE}")]


def _choice(name: str, value: Any, allowed: list[str]) -> str:
    if value not in allowed:
        raise ValueError(f"unknown {name} {value!r}; expected one of {allowed}")
    return value


def _month(value: str) -> str:
    if not _MONTH.match(str(value)):
        raise ValueError(f"month must look like '2031-04', got {value!r}")
    return str(value)


def _lookup(rows: list[dict], key: str, value: Any, what: str) -> dict:
    for r in rows:
        if r[key] == value:
            return r
    raise ValueError(f"no {what} with {key} {value!r}")


def catalog_from_world(world: dict[str, Any], *, distractors: bool = True) -> StaticCatalog:
    """Build the toolspace over an in-memory world (the snapshot tables)."""
    w = world
    servers: dict[str, tuple[str, list[ToolSpec]]] = {}

    def tool(server: str, name: str, desc: str, params: list[Param], fn: Callable[[dict], Any],
             returns: str = "") -> ToolSpec:
        return ToolSpec(server, name, desc, params, fn, returns=returns)

    page_ret = "{items: [...], page, page_size, total_items, total_pages}"

    # ── product_catalog ───────────────────────────────────────────────────────────────────────────
    def list_products(a):
        rows = w["products"]
        if a.get("category") is not None:
            rows = [p for p in rows if p["category"] == _choice("category", a["category"], CATEGORIES)]
        return _page(rows, a.get("page", 1), a.get("page_size", 25))

    servers["product_catalog"] = (
        ("The CURRENT product catalog: every SKU on sale today, its name, category and current list price "
        "in credits (CR)."), [
            tool("product_catalog", "list_categories", "List the product categories.", [],
                 lambda a: list(CATEGORIES), "list[str]"),
            tool("product_catalog", "list_products",
                 "List current products (sku, name, category, list_price), optionally filtered by category.",
                 [Param("category", "str", False, None, "a category from list_categories"), *_pager()],
                 list_products, page_ret),
            tool("product_catalog", "get_product", "Fetch one current product by SKU.",
                 [Param("sku", "str", True, None, "e.g. 'SKU-1234'")],
                 lambda a: _lookup(w["products"], "sku", a["sku"], "product"), "dict"),
        ])

    # ── warehouse_inventory ───────────────────────────────────────────────────────────────────────
    wh_ids = [x["warehouse_id"] for x in w["warehouses"]]

    def stock_report(a):
        wid = _choice("warehouse_id", a["warehouse_id"], wh_ids)
        rows = [{k: s[k] for k in ("sku", "qty_on_hand", "reorder_point")}
                for s in w["stock"] if s["warehouse_id"] == wid]
        return _page(rows, a.get("page", 1), a.get("page_size", 25))

    servers["warehouse_inventory"] = (
        "LIVE on-hand stock: per warehouse, each stocked SKU's quantity on hand and its reorder point.", [
            tool("warehouse_inventory", "list_warehouses", "List warehouses (warehouse_id, name, city).", [],
                 lambda a: list(w["warehouses"]), "list[dict]"),
            tool("warehouse_inventory", "stock_report",
                 "Current stock lines for one warehouse: sku, qty_on_hand, reorder_point. Does not include "
                 "product category; join with the product catalog by sku.",
                 [Param("warehouse_id", "str", True, None, "from list_warehouses"), *_pager()],
                 stock_report, page_ret),
        ])

    # ── order_ledger ──────────────────────────────────────────────────────────────────────────────
    statuses = sorted({o["status"] for o in w["orders"]})

    def list_orders(a):
        month = _month(a["month"])
        rows = [o for o in w["orders"] if o["order_date"].startswith(month)]
        if a.get("status") is not None:
            rows = [o for o in rows if o["status"] == _choice("status", a["status"], statuses)]
        return _page(rows, a.get("page", 1), a.get("page_size", 25))

    servers["order_ledger"] = (
        ("Customer orders placed in 2031 (real sales). Each order has customer_id, order_date, status and "
        "line items with the unit_price actually CHARGED (credits), which can differ from the current list "
        "price."), [
            tool("order_ledger", "list_orders",
                 f"List orders placed in a month, optionally filtered by status (one of {statuses}).",
                 [Param("month", "str", True, None, "'YYYY-MM', e.g. '2031-04'"),
                  Param("status", "str", False, None, "order status filter"), *_pager()],
                 list_orders, page_ret),
            tool("order_ledger", "get_order", "Fetch one order by order_id.",
                 [Param("order_id", "str", True, None, "e.g. 'ORD-000123'")],
                 lambda a: _lookup(w["orders"], "order_id", a["order_id"], "order"), "dict"),
        ])

    # ── customer_directory ────────────────────────────────────────────────────────────────────────
    cities = sorted({c["city"] for c in w["customers"]})

    def search_customers(a):
        rows = w["customers"]
        if a.get("city") is not None:
            rows = [c for c in rows if c["city"] == _choice("city", a["city"], cities)]
        if a.get("tier") is not None:
            rows = [c for c in rows if c["tier"] == _choice("tier", a["tier"], TIERS)]
        return _page(rows, a.get("page", 1), a.get("page_size", 25))

    servers["customer_directory"] = (
        "Customer accounts: customer_id, name, home city and loyalty tier.", [
            tool("customer_directory", "search_customers",
                 f"Search customers by city and/or loyalty tier (one of {TIERS}).",
                 [Param("city", "str", False, None, "home city"), Param("tier", "str", False, None, "tier"),
                  *_pager()], search_customers, page_ret),
            tool("customer_directory", "get_customer", "Fetch one customer by customer_id.",
                 [Param("customer_id", "str", True, None, "e.g. 'CUS-0042'")],
                 lambda a: _lookup(w["customers"], "customer_id", a["customer_id"], "customer"), "dict"),
        ])

    # ── staff_directory ───────────────────────────────────────────────────────────────────────────
    depts = [d["department"] for d in w["departments"]]

    def list_employees(a):
        rows = w["employees"]
        if a.get("department") is not None:
            rows = [e for e in rows if e["department"] == _choice("department", a["department"], depts)]
        return _page(rows, a.get("page", 1), a.get("page_size", 25))

    servers["staff_directory"] = (
        "CURRENT employees: employee_id, name, department, office city and start date.", [
            tool("staff_directory", "list_departments", "List department names.", [],
                 lambda a: list(depts), "list[str]"),
            tool("staff_directory", "list_employees", "List current employees, optionally by department.",
                 [Param("department", "str", False, None, "a department name"), *_pager()],
                 list_employees, page_ret),
            tool("staff_directory", "get_employee", "Fetch one current employee by employee_id.",
                 [Param("employee_id", "str", True, None, "e.g. 'EMP-0007'")],
                 lambda a: _lookup(w["employees"], "employee_id", a["employee_id"], "employee"), "dict"),
        ])

    # ── project_registry ──────────────────────────────────────────────────────────────────────────
    def list_projects(a):
        rows = w["projects"]
        if a.get("owner_department") is not None:
            d = _choice("owner_department", a["owner_department"], depts)
            rows = [p for p in rows if p["owner_department"] == d]
        return list(rows)

    servers["project_registry"] = (
        "Internal projects: project_code, name, owning department and weekly budget in hours.", [
            tool("project_registry", "list_projects", "List projects, optionally by owning department.",
                 [Param("owner_department", "str", False, None, "a department name")],
                 list_projects, "list[dict]"),
            tool("project_registry", "get_project", "Fetch one project by project_code.",
                 [Param("project_code", "str", True, None, "e.g. 'PRJ-512'")],
                 lambda a: _lookup(w["projects"], "project_code", a["project_code"], "project"), "dict"),
        ])

    # ── timesheets ────────────────────────────────────────────────────────────────────────────────
    def list_entries(a):
        week = str(a["week"])
        if not _WEEK.match(week):
            raise ValueError(f"week must look like '2031-W07', got {week!r}")
        rows = [{k: t[k] for k in ("employee_id", "project_code", "hours")}
                for t in w["timesheets"] if t["week"] == week]
        return _page(rows, a.get("page", 1), a.get("page_size", 25))

    servers["timesheets"] = (
        "Hours logged per employee per project, by ISO week (2031-W01 .. 2031-W26).", [
            tool("timesheets", "list_entries", "Timesheet entries for one ISO week.",
                 [Param("week", "str", True, None, "'YYYY-Www', e.g. '2031-W07'"), *_pager()],
                 list_entries, page_ret),
        ])

    # ── fx_rates ──────────────────────────────────────────────────────────────────────────────────
    currencies = sorted(w["fx"])

    def rate_history(a):
        cur = _choice("currency", a["currency"], currencies)
        month = _month(a["month"])
        return {"currency": cur, "base": "CR", "month": month,
                "rates": {d: r for d, r in w["fx"][cur].items() if d.startswith(month)}}

    servers["fx_rates"] = (
        "Daily exchange rates from credits (CR) to other currencies: 1 CR = rate units.", [
            tool("fx_rates", "list_currencies", "List the quoted currencies.", [],
                 lambda a: list(currencies), "list[str]"),
            tool("fx_rates", "rate_history", "All daily rates for one currency in one month.",
                 [Param("currency", "str", True, None, "from list_currencies"),
                  Param("month", "str", True, None, "'YYYY-MM'")],
                 rate_history, "{currency, base, month, rates: {date: rate}}"),
        ])

    if distractors:
        def list_archived(a):
            return _page(w["archive_products"], a.get("page", 1), a.get("page_size", 25))

        servers["product_archive"] = (
            ("ARCHIVED 2029 price list, including discontinued SKUs. Historical reference only; prices here "
            "are not current."), [
                tool("product_archive", "list_archived_products", "List the 2029 archived price list.",
                     _pager(), list_archived, page_ret),
            ])

        def forecast_report(a):
            wid = _choice("warehouse_id", a["warehouse_id"], wh_ids)
            rows = [{k: s[k] for k in ("sku", "projected_qty", "reorder_point", "horizon")}
                    for s in w["forecast"] if s["warehouse_id"] == wid]
            return _page(rows, a.get("page", 1), a.get("page_size", 25))

        servers["demand_forecast"] = (
            "MODEL FORECAST of stock levels for next quarter per warehouse (projected, not actual).", [
                tool("demand_forecast", "forecast_report", "Projected stock lines for one warehouse.",
                     [Param("warehouse_id", "str", True, None, "warehouse id"), *_pager()],
                     forecast_report, page_ret),
            ])

        def list_test_orders(a):
            month = _month(a["month"])
            return _page([o for o in w["sandbox_orders"] if o["order_date"].startswith(month)],
                         a.get("page", 1), a.get("page_size", 25))

        servers["order_sandbox"] = (
            "Synthetic TEST orders from checkout QA. Not real sales; never counts toward revenue.", [
                tool("order_sandbox", "list_test_orders", "List QA test orders for a month.",
                     [Param("month", "str", True, None, "'YYYY-MM'"), *_pager()], list_test_orders, page_ret),
            ])

        def list_alumni(a):
            return _page(w["alumni"], a.get("page", 1), a.get("page_size", 25))

        servers["staff_alumni"] = (
            "FORMER employees who have left, with their last department and leaving date.", [
                tool("staff_alumni", "list_alumni", "List former employees.", _pager(), list_alumni,
                     page_ret),
            ])

    return StaticCatalog(dict(sorted(servers.items())))


def build_catalog(data_dir: str | None = None, *, distractors: bool = True) -> StaticCatalog:
    """THE entry: the synth toolspace over the committed snapshots (default: the packaged data dir).

    Pass it to `toolscout.cli.run(task, catalog=build_catalog(...))`. `distractors=False` drops the four
    look-alike servers, a coarse difficulty knob."""
    return catalog_from_world(load_world(data_dir), distractors=distractors)
