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
    assert cfg.max_repeat_calls == 3
    monkeypatch.setenv("TS_MAX_REPEAT_CALLS", "7")
    assert ToolscoutConfig.from_env().max_repeat_calls == 7


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


def test_lm_kwargs_and_request_timeout_from_env(monkeypatch):
    import pytest

    from toolscout.config import ToolscoutConfig

    monkeypatch.setenv("TS_ROOT_LM", "p")
    monkeypatch.setenv("TS_SUB_LM", "s")
    cfg = ToolscoutConfig.from_env()
    assert cfg.main_lm_kwargs is None and cfg.sub_lm_kwargs is None and cfg.request_timeout_s is None
    hash(cfg)   # still hashable with the dict-typed fields

    monkeypatch.setenv("TS_ROOT_LM_KWARGS", '{"temperature": 0.7, "extra_body": {"top_k": 20}}')
    monkeypatch.setenv("TS_SUB_LM_KWARGS", '{"temperature": 0.7}')
    monkeypatch.setenv("TS_REQUEST_TIMEOUT", "1800")
    cfg = ToolscoutConfig.from_env()
    assert cfg.main_lm_kwargs == {"temperature": 0.7, "extra_body": {"top_k": 20}}
    assert cfg.sub_lm_kwargs == {"temperature": 0.7} and cfg.request_timeout_s == 1800.0
    hash(cfg)

    for bad, match in (('{"timeout": 5}', "TS_ROOT_LM_KWARGS.*timeout"),
                       ('{"base_url": "http://x"}', "TS_ROOT_LM_KWARGS.*base_url"),
                       ("[1]", "JSON object"), ("{temperature: 0.7}", "not valid JSON")):
        monkeypatch.setenv("TS_ROOT_LM_KWARGS", bad)
        with pytest.raises(ValueError, match=match):
            ToolscoutConfig.from_env()
    monkeypatch.delenv("TS_ROOT_LM_KWARGS")
    for bad in ("0", "-5", "nan", "inf", "abc"):
        monkeypatch.setenv("TS_REQUEST_TIMEOUT", bad)
        with pytest.raises(ValueError, match="TS_REQUEST_TIMEOUT"):
            ToolscoutConfig.from_env()


def test_lm_kwargs_refuse_what_the_kit_refuses(monkeypatch):
    """The refusal is the kit's own rule, applied at config time: exact keys AND the aws_/azure_/vertex_
    prefixes. An endpoint alias like `api_base` or an injected header must never reach the wire."""
    import pytest

    from toolscout.config import ToolscoutConfig

    monkeypatch.setenv("TS_ROOT_LM", "p")
    monkeypatch.setenv("TS_SUB_LM", "s")
    for key in ("api_base", "extra_headers", "headers", "mock_response", "aws_region_name", "azure_ad_token",
                "vertex_project", "timeout", "model"):
        monkeypatch.setenv("TS_SUB_LM_KWARGS", f'{{"{key}": "x"}}')
        with pytest.raises(ValueError, match=f"TS_SUB_LM_KWARGS.*{key}"):   # never the kit's wording
            ToolscoutConfig.from_env()
    monkeypatch.setenv("TS_SUB_LM_KWARGS", '{"temperature": 0.7, "top_p": 0.9, "extra_body": {"top_k": 20}}')
    assert ToolscoutConfig.from_env().sub_lm_kwargs["top_p"] == 0.9
