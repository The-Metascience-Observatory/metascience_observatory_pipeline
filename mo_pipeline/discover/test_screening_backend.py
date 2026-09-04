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


# ── a misconfigured backend must stop the run, not be retried per item ──────

class _HTTPError(Exception):
    def __init__(self, code, body=b"{}"):
        self.code = code
        self._body = body
    def read(self): return self._body


def _openrouter(monkeypatch, raiser):
    monkeypatch.setattr(sb.urllib.error, "HTTPError", _HTTPError, raising=False)
    monkeypatch.setattr(sb.urllib.request, "urlopen", raiser)
    b = sb.OpenRouterBackend.__new__(sb.OpenRouterBackend)
    b.model, b.timeout, b.api_key, b.max_retries, b.max_tokens = "x/y", 1, "k", 1, 400
    return b


def test_a_retired_model_stops_the_run(monkeypatch):
    """The real failure: stage 4 booked all ~24,000 papers as retryable and exited
    0 having classified nothing, because a 404 looked like a per-item error."""
    def boom(*a, **k):
        raise _HTTPError(404, b'{"error":{"message":"no longer available"}}')
    b = _openrouter(monkeypatch, boom)
    r = b.complete("s", "u")
    assert r.fatal and "404" in r.error and "x/y" in r.error   # complete() still never raises
    with pytest.raises(sb.BackendUnavailable):
        b.screen(1, "s", "u")


def test_a_transient_code_is_not_fatal(monkeypatch):
    def boom(*a, **k):
        raise _HTTPError(503, b"upstream")
    b = _openrouter(monkeypatch, boom)
    r = b.complete("s", "u")
    assert r.error and not r.fatal
    assert b.screen(1, "s", "u") == (1, None)                  # retryable, so keep going


def test_an_empty_reply_is_reported_as_empty(monkeypatch, capsys):
    """A reasoning model can spend a 400-token budget before answering; that used
    to surface as "inner JSON parse error" with nothing to act on."""
    b = sb.OpenRouterBackend.__new__(sb.OpenRouterBackend)
    b.model, b.max_tokens = "x/y", 400
    monkeypatch.setattr(b, "complete", lambda *a, **k: sb.CompletionResult(text="   "))
    assert sb._screen(b, 7, "s", "u") == (7, None)
    out = capsys.readouterr().out
    assert "empty reply" in out and "max_tokens=400" in out


def test_cli_unrecognised_model_is_fatal(monkeypatch):
    class P:
        returncode, stdout, stderr = 1, "", "[claude-code:unrecognized_model] nope"
    monkeypatch.setattr(sb.subprocess, "run", lambda *a, **k: P())
    b = sb.ClaudeCLIBackend(model="not-a-model", timeout=1)
    assert b.complete("s", "u").fatal
    with pytest.raises(sb.BackendUnavailable):
        b.screen(1, "s", "u")


def test_a_truncated_reply_is_reported_as_truncation(monkeypatch, capsys):
    """finish_reason 'length' means the text is real but incomplete. Read as
    malformed JSON it looks like a model defect; read as truncation it is a
    one-line config fix."""
    payload = {"choices": [{"message": {"content": '{"replications": [{"resu'},
                            "finish_reason": "length"}], "usage": {}}
    b = sb.OpenRouterBackend.__new__(sb.OpenRouterBackend)
    b.model, b.timeout, b.api_key, b.max_retries, b.max_tokens = "x/y", 1, "k", 1, 400

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return __import__("json").dumps(payload).encode()
    monkeypatch.setattr(sb.urllib.request, "urlopen", lambda *a, **k: R())
    r = b.complete("s", "u")
    assert r.truncated and r.text and not r.error      # real text, just incomplete
    assert sb._screen(b, 3, "s", "u") == (3, None)
    out = capsys.readouterr().out
    assert "truncated at the output cap" in out and "max_tokens=400" in out
