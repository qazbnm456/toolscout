"""_maybe_subscription_lm — the sentinel routes to rlm-harness's ClaudeAgentLM; non-sentinel is a no-op.

These monkeypatch `rlm_harness.ClaudeAgentLM` so they test OUR routing + error-wrapping DETERMINISTICALLY,
independent of whether the optional `claude-agent-sdk` extra happens to be installed (CI installs it via
`--all-extras`; a bare dev box does not — the test must pass in both).
"""

from __future__ import annotations

import pytest
import rlm_harness

from toolscout.agent import _maybe_subscription_lm
from toolscout.config import SUBSCRIPTION_PREFIX


def test_non_sentinel_returns_none():
    assert _maybe_subscription_lm("openai/gpt-4o-mini") is None
    assert _maybe_subscription_lm("claude-sonnet-5") is None  # a plain id is NOT the sentinel


def test_sentinel_strips_prefix_and_builds(monkeypatch):
    """The sentinel is stripped and the remainder handed to ClaudeAgentLM (which we stub)."""
    seen = {}

    class FakeLM:
        def __init__(self, model):
            seen["model"] = model

    monkeypatch.setattr(rlm_harness, "ClaudeAgentLM", FakeLM, raising=False)
    lm = _maybe_subscription_lm(f"{SUBSCRIPTION_PREFIX}claude-sonnet-5")
    assert isinstance(lm, FakeLM) and seen["model"] == "claude-sonnet-5"


def test_sentinel_missing_sdk_wraps_error(monkeypatch):
    """A missing `claude-agent-sdk` surfaces as an ImportError at construction; we re-raise it as our
    uv-workflow-specific ModuleNotFoundError — never a bare ImportError. Forced deterministically."""
    def _boom(_model):
        raise ImportError("No module named 'claude_agent_sdk'")

    monkeypatch.setattr(rlm_harness, "ClaudeAgentLM", _boom, raising=False)
    with pytest.raises(ModuleNotFoundError, match="uv sync --extra subscription"):
        _maybe_subscription_lm(f"{SUBSCRIPTION_PREFIX}claude-sonnet-5")


def test_setup_refuses_host_side_sub_agents(monkeypatch):
    """rlm-harness >= 1.16 lets model code `import dspy` and build host-side sub-agents unless
    `sandbox_sub_agents=False`; their inner turns never reach the trace. setup() must turn it off."""
    import rlm_harness

    from toolscout.agent import setup
    from toolscout.config import ToolscoutConfig

    seen = {}
    monkeypatch.setattr(rlm_harness, "configure", lambda cfg, **kw: seen.setdefault("cfg", cfg))
    setup(ToolscoutConfig(main_model="x", sub_model="y"))
    assert seen["cfg"].sandbox_sub_agents is False and seen["cfg"].max_retries == 1


def test_setup_forwards_lm_kwargs_and_request_timeout(monkeypatch):
    import rlm_harness

    from toolscout.agent import setup
    from toolscout.config import ToolscoutConfig

    seen = {}
    monkeypatch.setattr(rlm_harness, "configure", lambda cfg, **kw: seen.setdefault("cfg", cfg))
    setup(ToolscoutConfig(main_model="x", sub_model="y", main_lm_kwargs={"temperature": 0.7},
                          sub_lm_kwargs={"temperature": 0.2}, request_timeout_s=1800.0))
    c = seen["cfg"]
    assert c.main_lm_kwargs == {"temperature": 0.7} and c.sub_lm_kwargs == {"temperature": 0.2}
    assert c.request_timeout_s == 1800.0


def test_lm_kwargs_reach_the_built_dspy_lm():
    """End to end through the REAL configure(): the kwargs land on the planner/specialist dspy.LM."""
    import dspy

    from toolscout.agent import setup
    from toolscout.config import ToolscoutConfig

    setup(ToolscoutConfig(main_model="p", sub_model="s", base_url="http://127.0.0.1:9/v1", api_key="k",
                          main_lm_kwargs={"temperature": 0.7}, sub_lm_kwargs={"temperature": 0.3},
                          request_timeout_s=1800.0))
    from rlm_harness.runtime import _STATE

    main = dspy.settings.lm
    assert main.kwargs["temperature"] == 0.7 and main.kwargs["timeout"] == 1800.0
    assert _STATE.sub_lm.kwargs["temperature"] == 0.3 and _STATE.sub_lm.kwargs["timeout"] == 1800.0
