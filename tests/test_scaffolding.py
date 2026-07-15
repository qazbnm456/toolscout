"""The scaffolding layer — signatures, length caps, arg coercion, informative errors, output natives."""

from __future__ import annotations

import pytest

from toolscout.catalog import Param, ServerInfo, ToolSpec
from toolscout.scaffolding import (
    ArgError,
    coerce_args,
    render_server_index,
    render_tool,
    signature,
    to_native,
    unknown_server_error,
    unknown_tool_error,
)


def _add_spec():
    return ToolSpec("math", "add", "Add two integers.", [Param("a", "int"), Param("b", "int")])


def test_signature_required_and_optional():
    spec = ToolSpec("memory", "set", "", [Param("key", "str"), Param("value", "Any", False, None)])
    assert signature(spec) == "set(key: str, value: Any = None)"


def test_render_tool_and_server_index_are_length_capped():
    spec = ToolSpec("s", "t", "x" * 5000, [Param("p", "str", True, None, "y" * 5000)])
    rendered = render_tool(spec, max_chars=50)
    assert "…" in rendered and len(rendered) < 5000
    idx = render_server_index([ServerInfo("srv", "z" * 5000)], max_chars=40)
    assert "…" in idx and len(idx) < 5000


def test_render_server_index_empty():
    assert render_server_index([]) == "(no servers configured)"


def test_coerce_args_coerces_types():
    out = coerce_args(_add_spec(), {"a": "6", "b": 7})
    assert out == {"a": 6, "b": 7} and isinstance(out["a"], int)


def test_coerce_args_missing_required_is_argerror():
    with pytest.raises(ArgError, match="needs `b`"):
        coerce_args(_add_spec(), {"a": 1})


def test_coerce_args_unknown_arg_is_argerror():
    with pytest.raises(ArgError, match="unknown argument"):
        coerce_args(_add_spec(), {"a": 1, "b": 2, "c": 3})


def test_coerce_args_bad_type_is_argerror():
    with pytest.raises(ArgError, match="must be int"):
        coerce_args(_add_spec(), {"a": "notanint", "b": 2})


def test_coerce_args_fills_optional_default():
    spec = ToolSpec("s", "t", "", [Param("x", "str", False, "d")])
    assert coerce_args(spec, {}) == {"x": "d"}


def test_unknown_errors_suggest_close_match():
    assert "Did you mean 'math'" in unknown_server_error("mth", ["math", "echo"])
    assert "Did you mean 'add'" in unknown_tool_error("math", "ad", ["add", "mul"])


def test_to_native_roundtrips_and_passes_through():
    assert to_native("13") == 13
    assert to_native("[1, 2]") == [1, 2]
    assert to_native("not-a-literal") == "not-a-literal"
    assert to_native(42) == 42
