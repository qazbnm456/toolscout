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
    assert m["heldout_templates"] == HELDOUT_TEMPLATES == ["fx_avg_order", "project_overrun"]
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
    assert {t["gold_kind"] for t in taskset} <= {"int", "number", "id", "id_set", "cannot_complete"}


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
    assert set(DISTRACTOR_SERVERS) <= servers and len(servers) == 12
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
