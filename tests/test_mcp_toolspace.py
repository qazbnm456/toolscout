"""McpCatalog — the external-MCP toolspace backing, tested OFFLINE with a fake bridge (no real server).

The async→sync transport itself needs a live server, but the pure helpers (`_params_from_schema`,
`_result_text`), the spec validation, the ISL connect/describe/call flow, and — critically — the
partial-eager-connect CLEANUP are all exercised here by injecting a fake `_ServerBridge`.
"""

from __future__ import annotations

import pytest

from toolscout import mcp_toolspace as mt
from toolscout.mcp_toolspace import McpCatalog, _params_from_schema, _result_text


# ---- pure helpers ------------------------------------------------------------

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


class _Block:
    def __init__(self, text):
        self.text = text


class _Result:
    def __init__(self, content=(), structured=None, is_error=False):
        self.content = list(content)
        self.structuredContent = structured
        self.isError = is_error


def test_result_text_joins_blocks_and_flags_error():
    assert _result_text(_Result([_Block("hello"), _Block("world")])) == "hello\nworld"
    assert _result_text(_Result(structured={"k": 1})) == '{"k": 1}'
    assert _result_text(_Result([_Block("bad")], is_error=True)).startswith("[tool reported an error]")


# ---- the catalog, with a fake bridge -----------------------------------------

class _FakeTool:
    def __init__(self, name, description, schema):
        self.name = name
        self.description = description
        self.inputSchema = schema


class _FakeBridge:
    made: list = []

    def __init__(self, server, *, timeout):
        self.server = server
        self.timeout = timeout
        self.started = False
        self.closed = False
        self.tools = [_FakeTool("greet", "Greet someone.",
                                {"type": "object", "properties": {"who": {"type": "string"}},
                                 "required": ["who"]})]
        _FakeBridge.made.append(self)

    def start(self):
        if self.server.get("fail"):
            raise RuntimeError("cannot connect")
        self.started = True

    def call(self, name, args):
        return _Result([_Block(f"hi {args.get('who')} via {name}")])

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_bridge(monkeypatch):
    _FakeBridge.made = []
    monkeypatch.setattr(mt, "_ServerBridge", _FakeBridge)


def _specs(*names, fail=None):
    return [{"name": n, "description": f"{n} server", "command": "x",
             **({"fail": True} if n == fail else {})} for n in names]


def test_rejects_spec_without_name():
    with pytest.raises(ValueError, match="'name'"):
        McpCatalog([{"description": "no name"}])


def test_eager_connect_describe_and_call():
    cat = McpCatalog(_specs("alpha", "beta"), connect="eager", timeout=5.0)
    assert {s.name for s in cat.servers()} == {"alpha", "beta"}
    assert cat.has_server("alpha") and not cat.has_server("ghost")
    assert cat.tool_names("alpha") == ["greet"]
    specs = cat.describe(["greet"])
    assert {s.server for s in specs} == {"alpha", "beta"}  # both connected → both describable
    greet = next(s for s in specs if s.server == "alpha")
    assert [p.name for p in greet.params] == ["who"] and greet.params[0].required
    assert cat.call("alpha", "greet", {"who": "sam"}) == "hi sam via greet"
    cat.close()
    assert all(b.closed for b in _FakeBridge.made)


def test_lazy_connect_defers_until_load():
    cat = McpCatalog(_specs("alpha", "beta"), connect="lazy", timeout=5.0)
    assert _FakeBridge.made == []          # nothing connected yet
    assert cat.describe(["greet"]) == []   # ISL: unconnected servers disclose nothing
    cat.load("alpha")
    assert cat.tool_names("alpha") == ["greet"]
    assert [s.server for s in cat.describe(["greet"])] == ["alpha"]  # only the loaded one
    cat.close()


def test_partial_eager_connect_does_not_leak():
    """The second server fails to start → __init__ must close the first (a live thread/subprocess) and
    re-raise, never leaking it."""
    with pytest.raises(RuntimeError, match="cannot connect"):
        McpCatalog(_specs("alpha", "boom", fail="boom"), connect="eager", timeout=5.0)
    # the 'alpha' bridge connected before 'boom' failed — it must have been closed on the way out
    started = [b for b in _FakeBridge.made if b.started]
    assert started and all(b.closed for b in started)


def test_call_on_unconnected_server_raises():
    cat = McpCatalog(_specs("alpha"), connect="lazy")
    with pytest.raises(RuntimeError, match="not connected"):
        cat.call("alpha", "greet", {"who": "x"})
