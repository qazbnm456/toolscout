"""The catalog abstraction — demo_catalog, StaticCatalog dispatch, load_catalog default."""

from __future__ import annotations

import pytest

from toolscout.catalog import Catalog, Param, ServerInfo, StaticCatalog, ToolSpec, demo_catalog, load_catalog
from toolscout.config import ToolscoutConfig


def test_demo_catalog_servers_and_tools():
    cat = demo_catalog()
    names = {s.name for s in cat.servers()}
    assert names == {"echo", "math", "memory", "text"}
    assert set(cat.tool_names("math")) == {"add", "mul"}
    assert cat.has_server("memory") and not cat.has_server("nope")


def test_demo_catalog_dispatch_and_stateful_memory():
    cat = demo_catalog()
    assert cat.call("math", "add", {"a": 6, "b": 7}) == 13
    assert cat.call("text", "upper", {"text": "ok"}) == "OK"
    # memory persists across calls within one catalog (demonstrates PTC persistence)
    assert cat.call("memory", "set", {"key": "k", "value": 99}) == "ok"
    assert cat.call("memory", "get", {"key": "k"}) == 99
    assert cat.call("memory", "get", {"key": "absent"}) is None


def test_demo_catalog_two_instances_are_independent():
    a, b = demo_catalog(), demo_catalog()
    a.call("memory", "set", {"key": "k", "value": 1})
    assert b.call("memory", "get", {"key": "k"}) is None  # fresh state per catalog


def test_describe_returns_specs():
    specs = demo_catalog().describe(["add", "upper"])
    by_name = {s.name: s for s in specs}
    assert by_name["add"].server == "math"
    assert [p.name for p in by_name["add"].params] == ["a", "b"]


def test_static_catalog_unknown_raises():
    cat = demo_catalog()
    with pytest.raises(KeyError):
        cat.call("math", "nope", {})
    with pytest.raises(KeyError):
        cat.tool_names("ghost")


def test_load_catalog_defaults_to_demo():
    cfg = ToolscoutConfig(main_model="x", sub_model="y", toolspace_path="")
    cat = load_catalog(cfg)
    assert isinstance(cat, StaticCatalog)
    assert {s.name for s in cat.servers()} == {"echo", "math", "memory", "text"}


def test_load_catalog_missing_toolspace_file_raises():
    cfg = ToolscoutConfig(main_model="x", sub_model="y", toolspace_path="/nonexistent/toolspace.json")
    with pytest.raises(FileNotFoundError):
        load_catalog(cfg)


def test_base_catalog_load_is_a_noop():
    class Bare(Catalog):
        def servers(self):
            return [ServerInfo("s", "d")]

    assert Bare().load("s") is None and Bare().close() is None


def test_toolspec_and_param_dataclasses():
    p = Param("x", "int")
    assert p.required is True and p.default is None
    t = ToolSpec("srv", "t", "desc", [p])
    assert t.server == "srv" and t.params[0].name == "x"
