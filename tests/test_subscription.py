"""_maybe_subscription_lm — the sentinel routes to rlm-kit's ClaudeAgentLM; non-sentinel is a no-op.

These monkeypatch `rlm_kit.ClaudeAgentLM` so they test OUR routing + error-wrapping DETERMINISTICALLY,
independent of whether the optional `claude-agent-sdk` extra happens to be installed (CI installs it via
`--all-extras`; a bare dev box does not — the test must pass in both).
"""

from __future__ import annotations

import pytest

import rlm_kit

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

    monkeypatch.setattr(rlm_kit, "ClaudeAgentLM", FakeLM, raising=False)
    lm = _maybe_subscription_lm(f"{SUBSCRIPTION_PREFIX}claude-sonnet-5")
    assert isinstance(lm, FakeLM) and seen["model"] == "claude-sonnet-5"


def test_sentinel_missing_sdk_wraps_error(monkeypatch):
    """A missing `claude-agent-sdk` surfaces as an ImportError at construction; we re-raise it as our
    uv-workflow-specific ModuleNotFoundError — never a bare ImportError. Forced deterministically."""
    def _boom(_model):
        raise ImportError("No module named 'claude_agent_sdk'")

    monkeypatch.setattr(rlm_kit, "ClaudeAgentLM", _boom, raising=False)
    with pytest.raises(ModuleNotFoundError, match="uv sync --extra subscription"):
        _maybe_subscription_lm(f"{SUBSCRIPTION_PREFIX}claude-sonnet-5")
