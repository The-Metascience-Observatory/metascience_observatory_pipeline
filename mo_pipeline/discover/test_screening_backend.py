"""Backend selection regression tests. Run:
    cd mo_pipeline && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest mo_pipeline/discover -q
"""
from __future__ import annotations

import pytest

from mo_pipeline import config
from mo_pipeline.discover import screening_backend as sb


@pytest.fixture
def cfg(monkeypatch):
    """Config naming OpenRouter, as production does, with no env override."""
    monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
    monkeypatch.delenv("SCREENING_MODEL", raising=False)
    monkeypatch.setattr(config, "SCREENING_PROVIDER", "openrouter", raising=False)
    monkeypatch.setattr(config, "SCREENING_MODEL", "inclusionai/ling-2.6-flash", raising=False)
    monkeypatch.setattr(sb.OpenRouterBackend, "__init__",
                        lambda self, model=None, **kw: setattr(self, "model", model or "openai/gpt-5-nano"))


def test_configured_model_is_not_handed_to_another_provider(cfg):
    """The bug: SCREENING_MODEL names a model for the CONFIGURED provider, but was
    read whichever provider the caller asked for. Once the default flipped to
    OpenRouter, asking for the Claude CLI handed it an OpenRouter slug; the CLI
    exited 1, screen() returned None, and stage 4 classified nothing yet exited 0."""
    assert sb.get_backend(provider="claude_cli").model == config.LLM_MODEL
    # the configured provider still gets the configured model
    assert sb.get_backend().model == "inclusionai/ling-2.6-flash"
    assert sb.get_backend(provider="openrouter").model == "inclusionai/ling-2.6-flash"
    # an explicit model always wins
    assert sb.get_backend(provider="claude_cli", model="sonnet").model == "sonnet"


def test_the_symmetric_case(monkeypatch):
    """A claude_cli-shaped config must not send a bare alias to OpenRouter."""
    monkeypatch.delenv("SCREENING_PROVIDER", raising=False)
    monkeypatch.delenv("SCREENING_MODEL", raising=False)
    monkeypatch.setattr(config, "SCREENING_PROVIDER", "claude_cli", raising=False)
    monkeypatch.setattr(config, "SCREENING_MODEL", "haiku", raising=False)
    monkeypatch.setattr(sb.OpenRouterBackend, "__init__",
                        lambda self, model=None, **kw: setattr(self, "model", model or "openai/gpt-5-nano"))
    assert sb.get_backend(provider="openrouter").model == "openai/gpt-5-nano"
    assert sb.get_backend(provider="claude_cli").model == "haiku"


def test_a_cross_provider_model_is_rejected_not_silently_attempted():
    with pytest.raises(ValueError, match="OpenRouter slug"):
        sb.check_model("claude_cli", "inclusionai/ling-2.6-flash")
    with pytest.raises(ValueError, match="vendor/model"):
        sb.check_model("openrouter", "haiku")
    assert sb.check_model("claude_cli", "claude-sonnet-5") == "claude-sonnet-5"
    assert sb.check_model("openrouter", "anthropic/claude-sonnet-4.6") == "anthropic/claude-sonnet-4.6"


def test_unknown_provider_still_raises(cfg):
    with pytest.raises(ValueError, match="unknown SCREENING_PROVIDER"):
        sb.get_backend(provider="nope")
