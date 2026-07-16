"""McpCatalog — the external-MCP toolspace ADAPTER, tested OFFLINE with a fake kit catalog.

toolscout's `McpCatalog` is now a thin adapter over rlm-kit's `rlm_kit.mcp.McpCatalog` (the transport,
which the kit tests against a REAL server). What's toolscout's OWN here is the scaffolding MAPPING —
`_params_from_schema`, and raw MCP `Tool` → `ToolSpec` in `describe` / tuple → `ServerInfo` in
`servers` — plus faithful delegation of the ISL surface. Those are exercised by injecting a fake kit
catalog; the connect lifecycle, partial-eager teardown, and per-call hang-safety live in the kit and
are tested there (`rlm-kit`'s `tests/test_mcp.py`).
"""

from __future__ import annotations

import pytest

from toolscout import mcp_toolspace as mt
from toolscout.catalog import ServerInfo, ToolSpec
from toolscout.mcp_toolspace import McpCatalog, _params_from_schema, _returns_from_schema


# ---- pure helper: the scaffolding mapping ------------------------------------

def test_params_from_schema_maps_types_required_defaults():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "integer", "description": "count"},
            "b": {"type": "string", "default": "x"},
            "c": {"type": "array"},
            "d": {"type": "wat"},  # unknown JSON type → Any
        },
        "required": ["a"],
    }
    params = {p.name: p for p in _params_from_schema(schema)}
    assert params["a"].type == "int" and params["a"].required and params["a"].description == "count"
    assert params["b"].type == "str" and not params["b"].required and params["b"].default == "x"
    assert params["c"].type == "list"
    assert params["d"].type == "Any"


def test_params_from_schema_handles_absent_schema():
    assert _params_from_schema(None) == []
    assert _params_from_schema({"type": "object"}) == []


# ---- the adapter, over a fake kit catalog ------------------------------------

class _FakeTool:
    def __init__(self, name, description, schema, output_schema=None):
        self.name = name
        self.description = description
        self.inputSchema = schema
        if output_schema is not None:   # a tool without one models an old SDK (no outputSchema attr)
            self.outputSchema = output_schema


def _greet_tool():
    return _FakeTool("greet", "Greet someone.",
                     {"type": "object", "properties": {"who": {"type": "string"}}, "required": ["who"]})


class _FakeKitCatalog:
    """Stands in for `rlm_kit.mcp.McpCatalog` (the transport), so the adapter is tested OFFLINE — no
    real MCP server. Mirrors the kit's contract closely enough to drive the adapter: name/connect
    validation, eager connect + partial teardown, and the servers/tools/call surface it maps."""

    made: list = []

    def __init__(self, specs, *, connect="eager", timeout=60.0):
        if connect not in ("eager", "lazy"):
            raise ValueError(f"connect must be 'eager' or 'lazy', got {connect!r}")
        self._specs: dict = {}
        for s in specs:
            if not isinstance(s, dict) or not s.get("name"):
                raise ValueError("each MCP catalog spec must be a dict with a 'name'")
            self._specs[str(s["name"])] = s
        self._connected: dict[str, list] = {}
        self.closed = False
        _FakeKitCatalog.made.append(self)
        if connect == "eager":
            try:
                for name in self._specs:
                    self._connect(name)
            except Exception:
                self.close()
                raise

    def _connect(self, name):
        if name in self._connected:
            return
        if self._specs[name].get("fail"):
            raise RuntimeError("cannot connect")
        self._connected[name] = [_greet_tool()]

    def servers(self):
        return [(n, str(s.get("description", ""))) for n, s in self._specs.items()]

    def has_server(self, name):
        return name in self._specs

    def load(self, name):
        self._connect(name)

    def tools(self, name):
        return list(self._connected.get(name, []))

    def tool_names(self, name):
        return [t.name for t in self.tools(name)]

    def call(self, server, tool, args=None):
        if server not in self._connected:
            raise RuntimeError(f"MCP server {server!r} is not connected (load it first)")
        return f"hi {(args or {}).get('who')} via {tool}"

    def close(self):
        self.closed = True
        self._connected.clear()


@pytest.fixture(autouse=True)
def _fake_kit_catalog(monkeypatch):
    _FakeKitCatalog.made = []
    monkeypatch.setattr(mt, "_KitMcpCatalog", _FakeKitCatalog)


def _specs(*names, fail=None):
    return [{"name": n, "description": f"{n} server", **({"fail": True} if n == fail else {})}
            for n in names]


def test_rejects_spec_without_name():
    # delegated to the kit catalog, surfaced through the adapter unchanged.
    with pytest.raises(ValueError, match="'name'"):
        McpCatalog([{"description": "no name"}])


def test_eager_connect_maps_servers_describe_and_call():
    cat = McpCatalog(_specs("alpha", "beta"), connect="eager", timeout=5.0)
    infos = cat.servers()
    assert {s.name for s in infos} == {"alpha", "beta"} and isinstance(infos[0], ServerInfo)  # tuple → ServerInfo
    assert cat.has_server("alpha") and not cat.has_server("ghost")
    assert cat.tool_names("alpha") == ["greet"]
    specs = cat.describe(["greet"])
    assert {s.server for s in specs} == {"alpha", "beta"}  # both connected → both describable
    greet = next(s for s in specs if s.server == "alpha")
    assert isinstance(greet, ToolSpec) and greet.description == "Greet someone."  # raw Tool → ToolSpec
    assert [p.name for p in greet.params] == ["who"] and greet.params[0].required  # inputSchema → Param
    assert cat.call("alpha", "greet", {"who": "sam"}) == "hi sam via greet"  # flattened result TEXT
    cat.close()
    assert cat._mcp.closed  # teardown delegated to the kit


def test_lazy_connect_defers_until_load():
    cat = McpCatalog(_specs("alpha", "beta"), connect="lazy", timeout=5.0)
    assert cat.describe(["greet"]) == []  # ISL: unconnected servers disclose nothing
    cat.load("alpha")
    assert cat.tool_names("alpha") == ["greet"]
    assert [s.server for s in cat.describe(["greet"])] == ["alpha"]  # only the loaded one
    cat.close()


def test_partial_eager_failure_propagates_from_kit():
    """The kit tears down a partial eager connect and re-raises; the adapter just propagates."""
    with pytest.raises(RuntimeError, match="cannot connect"):
        McpCatalog(_specs("alpha", "boom", fail="boom"), connect="eager", timeout=5.0)
    assert _FakeKitCatalog.made and _FakeKitCatalog.made[0].closed  # kit closed itself on the way out


def test_call_on_unconnected_server_raises():
    cat = McpCatalog(_specs("alpha"), connect="lazy")
    with pytest.raises(RuntimeError, match="not connected"):
        cat.call("alpha", "greet", {"who": "x"})


# ---- (iv) outputSchema → a compact return-type hint --------------------------

def test_returns_from_schema_maps_output_types():
    obj = {"type": "object", "properties": {"ok": {"type": "boolean"}, "n": {"type": "integer"}}}
    assert _returns_from_schema(obj) == "{ok: bool, n: int}"
    assert _returns_from_schema({"type": "string"}) == "str"
    assert _returns_from_schema({"type": "object"}) == "dict"   # object, no properties
    assert _returns_from_schema(None) == ""
    assert _returns_from_schema({"no": "type"}) == ""


def test_describe_populates_returns_from_output_schema(monkeypatch):
    typed = _FakeTool("typed", "T", {"type": "object", "properties": {"x": {"type": "integer"}}},
                      output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}})

    class _TypedCatalog(_FakeKitCatalog):
        def _connect(self, name):
            if name in self._connected:
                return
            self._connected[name] = [typed]

    monkeypatch.setattr(mt, "_KitMcpCatalog", _TypedCatalog)
    cat = McpCatalog(_specs("srv"))
    try:
        assert cat.describe(["typed"])[0].returns == "{ok: bool}"   # outputSchema → hint
    finally:
        cat.close()
    assert not hasattr(_greet_tool(), "outputSchema")   # a tool WITHOUT one (old SDK) → returns ""
