"""The committed data dir: reproducible, self-consistent, and split by template."""

import json
import os
import re

import pytest
from toolscout_synth import DISTRACTOR_SERVERS, HELDOUT_TEMPLATES, TEMPLATES, build_catalog, default_data_dir
from toolscout_synth.cli import main
from toolscout_synth.templates import fetch_all


def _manifest():
    with open(os.path.join(default_data_dir(), "MANIFEST.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_committed_data_regenerates_byte_for_byte(capsys):
    """Seed → snapshots → tasks → gold is fully deterministic; any drift (code or Python) goes red here."""
    assert main(["verify"]) == 0, capsys.readouterr().err


def test_manifest_matches_the_taskset(taskset):
    m = _manifest()
    assert m["n_tasks"] == len(taskset) >= 100
    assert m["heldout_templates"] == HELDOUT_TEMPLATES == [
        "fx_avg_order", "project_overrun", "region_customer_pressure", "renegotiation_impact", "shortfall_cost",
        "supplier_concentration", "ticket_resolution", "ticket_sla_miss", "tier_share_shift"]
    assert {t["snapshot_sha256"] for t in taskset} == {m["snapshot_sha256"]}
    assert len({t["id"] for t in taskset}) == len(taskset)
    assert len({t["task"] for t in taskset}) == len(taskset)


def test_split_is_by_template(taskset):
    for t in taskset:
        assert t["split"] == TEMPLATES[t["template"]].split
    train = {t["template"] for t in taskset if t["split"] == "train"}
    assert train.isdisjoint(HELDOUT_TEMPLATES)


def test_every_template_has_tasks_and_only_known_gold_kinds(taskset):
    assert {t["template"] for t in taskset} == set(TEMPLATES)
    assert {t["gold_kind"] for t in taskset} <= {"int", "number", "id", "id_set", "label", "label_set",
                                                 "cannot_complete"}
    for t in taskset:
        if t["gold_kind"] in ("label", "label_set"):
            assert set([t["gold"]] if t["gold_kind"] == "label" else t["gold"]) <= set(t["choices"]), t["id"]


def test_no_security_domain_content():
    """The held-out cve-reverser task must not overlap: no CVE/vulnerability vocabulary anywhere."""
    pat = re.compile(r"CVE-|vulnerab|exploit|malware|breach", re.IGNORECASE)
    root = default_data_dir()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                assert not pat.search(fh.read()), f


def test_tool_names_are_globally_unique_and_distractors_toggle(catalog):
    """StaticCatalog.describe() looks tools up by bare name, so a name may exist on one server only."""
    names = [n for s in catalog.servers() for n in catalog.tool_names(s.name)]
    assert len(names) == len(set(names))
    servers = {s.name for s in catalog.servers()}
    assert set(DISTRACTOR_SERVERS) <= servers and len(servers) == 19
    lean = {s.name for s in build_catalog(distractors=False).servers()}
    assert lean == servers - set(DISTRACTOR_SERVERS)


def test_pagination_covers_every_row_and_validates(catalog, world):
    got = fetch_all(catalog.call, "order_ledger", "list_orders", {"month": "2031-05"})
    want = [o for o in world["orders"] if o["order_date"].startswith("2031-05")]
    assert got == want and len(want) > 200
    first = catalog.call("order_ledger", "list_orders", {"month": "2031-05", "page": 1, "page_size": 50})
    assert first["total_pages"] == -(-len(want) // 50) and len(first["items"]) == 50
    with pytest.raises(ValueError, match="page_size"):
        catalog.call("order_ledger", "list_orders", {"month": "2031-05", "page_size": 500})
    with pytest.raises(ValueError, match="month"):
        catalog.call("order_ledger", "list_orders", {"month": "May"})


def test_charged_price_differs_from_list_price_somewhere(world):
    """The trap the revenue/spend templates rely on actually exists in the data."""
    price = {p["sku"]: p["list_price"] for p in world["products"]}
    lines = [ln for o in world["orders"] for ln in o["lines"]]
    assert sum(ln["unit_price"] != price[ln["sku"]] for ln in lines) > len(lines) // 3


def test_round_one_tasks_survive_round_two(taskset):
    """Round 2 added tables from their own random stream: every round-1 task is still there, unchanged."""
    import hashlib

    v1 = [t for t in taskset if t["template"] in ("revenue_by_category", "restock_shortlist", "top_customer",
                                                  "unsupported_lookup", "project_overrun", "fx_avg_order")
          and t["level"] <= 2]
    assert len(v1) == 110
    body = "".join(json.dumps({k: v for k, v in t.items() if k != "snapshot_sha256"}, sort_keys=True) for t in v1)
    assert hashlib.sha256(body.encode()).hexdigest()[:16] == "a3ce6d8dd4f98975"   # sha256 of round 1's committed records


def test_every_contract_currency_has_a_rate(catalog, world):
    """A value a task needs must be reachable through a tool, never an implicit constant. Contracts priced in
    CR are compared "at that date's rates"; when fx_rates had no CR, agents skipped those contracts."""
    quoted = set(catalog.call("fx_rates", "list_currencies", {}))
    assert {c["currency"] for c in world["contracts"]} <= quoted
    cr = catalog.call("fx_rates", "rate_history", {"currency": "CR", "month": "2031-02"})["rates"]
    assert len(cr) == 28 and set(cr.values()) == {1.0}


_R1 = ("revenue_by_category", "restock_shortlist", "top_customer", "unsupported_lookup", "project_overrun",
       "fx_avg_order")
_R2 = ("margin_by_supplier", "promo_window_revenue", "region_top_category", "return_rate", "ticket_assignee_leader",
       "restock_slow_supplier", "city_refunds", "cross_dept_hours", "shortfall_cost", "repeat_buyers", "price_drift",
       "forecast_gap", "ticket_resolution", "unsupported_partial", "order_cheapest_suppliers", "tier_share_shift",
       "project_breadth", "top_return_reason", "region_customer_pressure")


def _shipped_before_round3(t):
    """Records that existed before round 3: rounds 1-2 templates, levels <= 3, instance index <= 009."""
    return t["template"] in _R1 + _R2 and t["level"] <= 3 and int(t["id"].rsplit("-", 1)[1]) <= 9


def test_round_two_tasks_survive_round_three(taskset):
    """Round 3 appended levels, instances and templates: every earlier record is byte-identical, and the
    world snapshots are untouched."""
    import hashlib

    from toolscout_synth import default_data_dir

    before = [t for t in taskset if _shipped_before_round3(t)]
    assert len(before) == 627
    r2 = [t for t in before if not (t["template"] in _R1 and t["level"] <= 2)]
    body = "".join(json.dumps({k: v for k, v in t.items() if k != "snapshot_sha256"}, sort_keys=True) for t in r2)
    assert len(r2) == 517 and hashlib.sha256(body.encode()).hexdigest()[:16] == "af0d556787f67a9d"
    with open(os.path.join(default_data_dir(), "MANIFEST.json"), encoding="utf-8") as fh:
        assert json.load(fh)["snapshot_sha256"].startswith("c11a03d48c7f")


def test_round_three_golds_are_spread(taskset):
    """Per template and level, round-3 golds must not collapse: >= 6 distinct int/number golds (>= 5 on a
    10-instance level), >= 40% distinct ids or id sets."""
    from collections import defaultdict

    groups = defaultdict(list)
    for t in taskset:
        if not _shipped_before_round3(t) and t["gold_kind"] not in ("label", "label_set", "cannot_complete"):
            groups[(t["template"], t["level"], t["gold_kind"])].append(json.dumps(t["gold"], sort_keys=True))
    for (tid, level, kind), golds in groups.items():
        distinct = len(set(golds))
        if kind in ("int", "number"):
            assert distinct >= (6 if len(golds) >= 20 else 5), (tid, level, distinct)
        else:
            assert distinct >= 0.4 * len(golds), (tid, level, distinct)
