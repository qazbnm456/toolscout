"""ToolscoutConfig.from_env — model ROLES, the subscription/judge guard, and the RLM knobs."""

from __future__ import annotations

import pytest

from toolscout.config import SUBSCRIPTION_PREFIX, ToolscoutConfig


def _clear_ts_env(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("TS_"):
            monkeypatch.delenv(k, raising=False)


def test_from_env_requires_both_roles(monkeypatch):
    _clear_ts_env(monkeypatch)
    with pytest.raises(ValueError, match="TS_ROOT_LM"):
        ToolscoutConfig.from_env()
    monkeypatch.setenv("TS_ROOT_LM", "openai/gpt-4o-mini")
    with pytest.raises(ValueError):
        ToolscoutConfig.from_env()  # specialist still missing


def test_from_env_roles_and_defaults(monkeypatch):
    _clear_ts_env(monkeypatch)
    monkeypatch.setenv("TS_ROOT_LM", "openai/planner")
    monkeypatch.setenv("TS_SUB_LM", "openai/specialist")
    monkeypatch.setenv("TS_BASE_URL", "https://proxy/v1")
    monkeypatch.setenv("TS_API_KEY", "sk-abc")
    cfg = ToolscoutConfig.from_env()
    assert cfg.main_model == "openai/planner"
    assert cfg.sub_model == "openai/specialist"
    assert cfg.base_url == "https://proxy/v1"
    # judge defaults to the specialist and inherits proxy creds
    assert cfg.judge_model == "openai/specialist"
    assert cfg.judge_base_url == "https://proxy/v1"
    assert cfg.enable_judge is False
    assert cfg.interpreter == "pyodide"
    assert cfg.connect == "eager"
    assert cfg.max_iterations == 45 and cfg.max_desc_chars == 1200


def test_enabled_judge_may_not_inherit_a_subscription_specialist(monkeypatch):
    _clear_ts_env(monkeypatch)
    monkeypatch.setenv("TS_ROOT_LM", f"{SUBSCRIPTION_PREFIX}claude-sonnet-5")
    monkeypatch.setenv("TS_SUB_LM", f"{SUBSCRIPTION_PREFIX}claude-fable-5")
    monkeypatch.setenv("TS_ENABLE_JUDGE", "1")  # judge ON + inherits the subscription specialist → LOUD
    with pytest.raises(ValueError, match="subscription"):
        ToolscoutConfig.from_env()


def test_subscription_planner_with_judge_off_is_allowed(monkeypatch):
    """With the judge OFF (the default), its model is inert — a subscription planner+specialist must NOT
    be blocked by the judge default inheriting the sentinel (the common live-run config)."""
    _clear_ts_env(monkeypatch)
    monkeypatch.setenv("TS_ROOT_LM", f"{SUBSCRIPTION_PREFIX}claude-sonnet-5")
    monkeypatch.setenv("TS_SUB_LM", f"{SUBSCRIPTION_PREFIX}claude-sonnet-5")
    cfg = ToolscoutConfig.from_env()  # no raise
    assert cfg.enable_judge is False and cfg.main_model.startswith(SUBSCRIPTION_PREFIX)


def test_explicit_enabled_judge_sentinel_is_rejected(monkeypatch):
    _clear_ts_env(monkeypatch)
    monkeypatch.setenv("TS_ROOT_LM", "openai/planner")
    monkeypatch.setenv("TS_SUB_LM", "openai/specialist")
    monkeypatch.setenv("TS_JUDGE_LM", f"{SUBSCRIPTION_PREFIX}claude-sonnet-5")
    monkeypatch.setenv("TS_ENABLE_JUDGE", "1")
    with pytest.raises(ValueError, match="sentinel"):
        ToolscoutConfig.from_env()


def test_from_env_enable_judge_and_toolspace(monkeypatch):
    _clear_ts_env(monkeypatch)
    monkeypatch.setenv("TS_ROOT_LM", "openai/planner")
    monkeypatch.setenv("TS_SUB_LM", "openai/specialist")
    monkeypatch.setenv("TS_ENABLE_JUDGE", "1")
    monkeypatch.setenv("TS_JUDGE_LM", "qwen/judge")
    monkeypatch.setenv("TS_TOOLSPACE", "./toolspace.json")
    monkeypatch.setenv("TS_MAX_ITERATIONS", "12")
    cfg = ToolscoutConfig.from_env()
    assert cfg.enable_judge is True
    assert cfg.judge_model == "qwen/judge"
    assert cfg.toolspace_path == "./toolspace.json"
    assert cfg.max_iterations == 12
