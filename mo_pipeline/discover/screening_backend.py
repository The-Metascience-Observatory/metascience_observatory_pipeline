"""
Pluggable LLM backends: stage-4 screening and the single-shot core extractor.

Stage 4 shells out to the `claude -p` CLI with Haiku. That is free on the Max
plan but rate-budgeted (~10k calls/week) and it shares the `claude_cli` mutex
group with extraction, so a large screening run starves the rest of the
pipeline. This module puts a second provider behind the same interface so a
sweep can be moved off the Claude rate-limit pool when volume demands it.

Cost per 1M screens at ~600 in / 40 out tokens, measured from OpenRouter's
live model list:

    claude_cli (Max plan)          $0 marginal, but rate-limited
    anthropic/claude-haiku-4.5     ~$800   (~$400 batched)
    openai/gpt-5.6-luna            ~$84
    openai/gpt-5-nano              ~$46
    inclusionai/ling-2.6-flash     ~$7

Cost is not the constraint at any plausible scale; throughput is.

Two entry points per backend:

    complete(system_prompt, user_prompt) -> CompletionResult
        One no-tools call. Returns the reply text, a usage dict in extract.py's
        shape, and -- on failure -- an error string plus the raw exit/stderr so
        the caller can classify it (extract.is_usage_limit_error). Used by
        extract/extract_core.py.
    screen(idx, system_prompt, user_prompt) -> (idx, dict | None)
        Stage 4's historical interface, a thin wrapper over complete() whose
        prints and return values are unchanged.

The CLI backend is the DEFAULT for screening and its behaviour is byte-for-byte
the logic that previously lived in `classify_candidates.classify_one`, so
switching providers is opt-in and reversible.

The two providers are NOT interchangeable for a controlled comparison: the
OpenRouter path pins temperature 0 and a max_tokens cap and retries 429/5xx four
times, while the CLI exposes neither knob and does not retry. Treat a
provider switch as a change of condition, not as a free substitution.

Usage:
    from mo_pipeline.discover.screening_backend import get_backend
    backend = get_backend()                      # honours config
    verdict = backend.screen(idx, system_prompt, user_prompt)

Selection (config.py, overridable by env):
    SCREENING_PROVIDER = "claude_cli" | "openrouter"
    SCREENING_MODEL    = provider-specific model id
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from mo_pipeline.config import LLM_MODEL, LLM_TIMEOUT_SEC

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: Each provider's own default model. `SCREENING_MODEL` names a model for the
#: CONFIGURED provider only: it is a provider-specific value, so it must not be
#: handed to a provider the caller asked for explicitly.
PROVIDER_DEFAULT_MODEL = {"claude_cli": LLM_MODEL, "openrouter": "openai/gpt-5-nano",
                          "codex_cli": "gpt-5.6-luna"}


def check_model(provider: str, model: str) -> str:
    """Reject a model that belongs to the other provider, before any call.

    Worth failing loudly for, because the alternative is near-invisible: the
    Claude CLI exits 1 with `[claude-code:unrecognized_model]`, `screen()`
    returns None, classify books it as a retryable failure, and stage 4 finishes
    with exit 0 having classified nothing at all. OpenRouter model ids are
    `vendor/model`; every Claude CLI alias and full model id has no slash.
    """
    if provider == "claude_cli" and "/" in model:
        raise ValueError(
            f"model {model!r} looks like an OpenRouter slug but the provider is claude_cli. "
            f"SCREENING_MODEL names a model for the configured provider "
            f"({_cfg('SCREENING_PROVIDER', 'claude_cli')}); pass an explicit model to use another.")
    if provider == "openrouter" and "/" not in model:
        raise ValueError(
            f"model {model!r} is not an OpenRouter id (expected `vendor/model`). "
            f"Pass an explicit --model, e.g. anthropic/claude-sonnet-4.6.")
    if provider == "codex_cli" and "/" in model:
        raise ValueError(f"model {model!r} is an OpenRouter slug; the Codex CLI wants e.g. gpt-5.6-luna.")
    return model


# ── shared JSON salvage (identical to the pre-existing CLI behaviour) ────────

def strip_code_fences(text):
    """Remove markdown code fences like ```json ... ``` if present."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("\n", 1)
        text = parts[1] if len(parts) > 1 else ""
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    return text.strip()


def extract_json(text):
    """Best-effort JSON extraction from LLM output."""
    text = strip_code_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def parse_json_reply(text):
    """Parse an extraction reply into a dict, or None.

    More forgiving than extract_json for long multi-entry replies: after the
    fence strip and a plain json.loads, it decodes the first complete object
    starting at the first `{` (so trailing prose containing braces cannot break
    it), and only then falls back to the greedy regex. A bare list is wrapped
    into the {"contains_replications", "replications"} envelope.
    """
    if not text:
        return None
    body = strip_code_fences(text)
    parsed = None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        if start != -1:
            try:
                parsed, _ = json.JSONDecoder().raw_decode(body[start:])
            except json.JSONDecodeError:
                parsed = None
        if parsed is None:
            match = re.search(r"\{.*\}", body, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    parsed = None
    if isinstance(parsed, list):
        parsed = {"contains_replications": bool(parsed), "replications": parsed}
    return parsed if isinstance(parsed, dict) else None


# ── result of one completion ────────────────────────────────────────────────

class BackendUnavailable(RuntimeError):
    """The backend cannot serve ANY request: a retired model, a bad key, no credit.

    Distinct from a per-call failure. Retrying is pointless and expensive: stage 4
    would book each of ~24,000 papers as "FAILED, will retry on next run" and exit
    0 having classified nothing, which reads as a successful run. Raised out of
    screen() so the run stops on the first call instead.
    """


@dataclass
class CompletionResult:
    """What one no-tools call produced.

    `error` is a short human string (None on success). `returncode`/`stdout`/
    `stderr` are the raw subprocess facts for the CLI backend so a caller can
    reproduce extract.py's "{cli} CLI failed for {paper}:\\n{stderr+stdout}"
    message, whose empty body is how is_usage_limit_error spots a silent
    session-limit exit. `usage` follows extract.py's per-paper usage dict.
    """
    text: str = ""
    usage: dict = field(default_factory=dict)
    error: str | None = None
    #: True when `error` describes the backend itself rather than this request,
    #: so every subsequent call would fail identically.
    fatal: bool = False
    #: True when the model hit its output cap mid-answer. The text is real but
    #: incomplete, which otherwise reads downstream as malformed JSON.
    truncated: bool = False
    raw: dict | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None


def primary_model(model_usage: dict | None, fallback: str) -> str:
    """The model that did the work, from a CLI envelope's `modelUsage`.

    The CLI also runs a small helper model (Haiku) for internal chores and
    lists it first, so "first key" reports the wrong model. Pick the entry
    with the most output tokens; fall back to the first key, then `fallback`.
    """
    if not model_usage:
        return fallback
    def out_tokens(v):
        return v.get("outputTokens", 0) if isinstance(v, dict) else 0
    return max(model_usage, key=lambda k: out_tokens(model_usage[k]))


def _cli_usage(outer: dict, fallback_model: str) -> dict:
    """extract.py:usage shape from a `claude --output-format json` envelope."""
    model_usage = outer.get("modelUsage", {}) or {}
    u = outer.get("usage", {}) or {}
    return {
        "model": primary_model(model_usage, fallback_model),
        "input_tokens": u.get("input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "cache_creation_tokens": u.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": u.get("cache_read_input_tokens", 0),
        "cost_usd": outer.get("total_cost_usd", 0),
        "duration_ms": outer.get("duration_ms", 0),
        "num_turns": outer.get("num_turns", 0),
    }


# ── backends ────────────────────────────────────────────────────────────────

class ClaudeCLIBackend:
    """`claude -p` subprocess. Free on the Max plan, rate-limited, holds the
    `claude_cli` mutex. This is the default and the historical behaviour."""

    name = "claude_cli"

    def __init__(self, model=None, timeout=None, cwd=None):
        self.model = model or LLM_MODEL
        self.timeout = timeout or LLM_TIMEOUT_SEC
        # Working directory for the CLI. The CLI injects a CLAUDE.md found in
        # its cwd into the conversation, so a caller that wants a deterministic
        # prompt passes a directory without one (e.g. the paper folder).
        self.cwd = cwd

    def complete(self, system_prompt, user_prompt, cwd=None) -> CompletionResult:
        """One single-turn, no-tools call. Never raises."""
        cmd = [
            "claude", "-p",
            "--model", self.model,
            "--output-format", "json",
            "--tools", "",
            "--system-prompt", system_prompt,
            "--no-session-persistence",
        ]
        try:
            result = subprocess.run(
                cmd, input=user_prompt, capture_output=True,
                text=True, timeout=self.timeout, cwd=cwd or self.cwd,
            )
        except subprocess.TimeoutExpired:
            return CompletionResult(error=f"TIMEOUT after {self.timeout}s")
        except Exception as e:
            return CompletionResult(error=f"subprocess error: {e}")

        base = CompletionResult(returncode=result.returncode,
                                stdout=result.stdout or "", stderr=result.stderr or "")
        if result.returncode != 0:
            combined = ((result.stderr or "") + (result.stdout or ""))[:400]
            base.error = f"CLI exit {result.returncode}: {combined}"
            # A model the CLI does not know fails identically on every call.
            base.fatal = "unrecognized_model" in combined or "unknown model" in combined.lower()
            return base
        try:
            outer = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            base.error = f"outer JSON parse error: {e}"
            return base
        base.raw = outer
        base.text = outer.get("result", "") or ""
        base.usage = _cli_usage(outer, self.model)
        if outer.get("is_error"):
            base.error = f"CLI reported error: {base.text[:200]}"
        return base

    def screen(self, idx, system_prompt, user_prompt):
        """Return (idx, parsed_dict_or_None), or raise BackendUnavailable when the
        backend itself is misconfigured. Prints are otherwise unchanged from the
        pre-backend classify_one."""
        return _screen(self, idx, system_prompt, user_prompt)


class OpenRouterBackend:
    """OpenRouter chat-completions. Costs money but does not touch the Claude
    rate limit, so screening and extraction can run concurrently."""

    name = "openrouter"

    def __init__(self, model=None, timeout=None, api_key=None, max_retries=4,
                 max_tokens=400):
        # Its own default, never SCREENING_MODEL: that names a model for the
        # configured provider, which may not be this one.
        self.model = model or PROVIDER_DEFAULT_MODEL["openrouter"]
        self.timeout = timeout or LLM_TIMEOUT_SEC
        self.api_key = api_key or _openrouter_key()
        self.max_retries = max_retries
        # Screening verdicts are small; the 400 default caps a runaway
        # generation so it cannot silently multiply cost. Extraction passes
        # config.EXTRACT_CORE_MAX_OUTPUT_TOKENS.
        self.max_tokens = max_tokens
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter backend selected but no API key found. Set "
                "OPENROUTER_API_KEY in the environment or in a .env.local file."
            )

    def complete(self, system_prompt, user_prompt, cwd=None) -> CompletionResult:
        """One chat-completions call with backoff on 429/5xx. Never raises."""
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }).encode()

        payload = None
        for attempt in range(self.max_retries):
            req = urllib.request.Request(
                OPENROUTER_URL, data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    # OpenRouter asks for attribution headers; harmless if unset.
                    "HTTP-Referer": "https://github.com/metascience-observatory",
                    "X-Title": "mo_pipeline screening",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                # 429/5xx are transient; back off and retry. 4xx otherwise is fatal.
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    wait = 2 ** attempt
                    print(f"  OpenRouter {e.code}, retry in {wait}s")
                    time.sleep(wait)
                    continue
                body = e.read()[:300].decode(errors="replace")
                # 401/402/403/404 concern the key, the credit or the model id:
                # identical for every request, so stop rather than repeat them.
                hint = (" Set SCREENING_MODEL to a live model id "
                        "(https://openrouter.ai/api/v1/models lists them) or pass --model."
                        if e.code == 404 else "")
                return CompletionResult(
                    error=f"OpenRouter HTTP {e.code} for model {self.model!r}: {body}{hint}",
                    fatal=e.code in (401, 402, 403, 404))
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return CompletionResult(error=f"OpenRouter error: {e}")
        if payload is None:
            return CompletionResult(error="OpenRouter: no response after retries")

        if payload.get("error"):
            return CompletionResult(error=f"OpenRouter error: {str(payload['error'])[:200]}", raw=payload)
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return CompletionResult(error=f"unexpected OpenRouter payload: {str(payload)[:200]}", raw=payload)
        u = payload.get("usage", {}) or {}
        usage = {
            "model": payload.get("model", self.model),
            "input_tokens": u.get("prompt_tokens", 0),
            "output_tokens": u.get("completion_tokens", 0),
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost_usd": u.get("cost", 0) or 0,
            "duration_ms": 0,
            "num_turns": 1,
        }
        # A reasoning model can spend most of its budget before emitting, so a
        # length stop is common and must not be reported as malformed output.
        finish = (payload.get("choices") or [{}])[0].get("finish_reason")
        return CompletionResult(text=content or "", usage=usage, raw=payload,
                                truncated=finish == "length")

    def screen(self, idx, system_prompt, user_prompt):
        """Return (idx, parsed_dict_or_None), or raise BackendUnavailable when the
        backend itself is misconfigured. Prints are otherwise unchanged from the
        pre-backend classify_one."""
        return _screen(self, idx, system_prompt, user_prompt)


class CodexCLIBackend:
    """`codex exec` subprocess (OpenAI models on the ChatGPT plan's Codex usage).

    Does not touch the Claude rate limit. Codex has no system-prompt flag, so the
    system prompt is prepended to the task. Read-only sandbox, ephemeral session,
    user config ignored, run from an empty temp dir so no AGENTS.md is injected.
    """

    name = "codex_cli"

    def __init__(self, model=None, timeout=None, cwd=None):
        self.model = model or PROVIDER_DEFAULT_MODEL["codex_cli"]
        self.timeout = timeout or LLM_TIMEOUT_SEC
        self.cwd = cwd

    def complete(self, system_prompt, user_prompt, cwd=None) -> CompletionResult:
        """One single-turn call. Never raises."""
        import tempfile
        from mo_pipeline.extract.codex_backend import parse_events
        prompt = (f"System instructions:\n{system_prompt}\n\nTask:\n{user_prompt}\n\n"
                  "Answer directly from the text above. Do not run commands or read files.")
        cmd = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
               "--sandbox", "read-only", "--model", self.model, "--json", prompt]
        try:
            with tempfile.TemporaryDirectory(prefix="mo_codex_") as tmp:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout,
                                        cwd=cwd or self.cwd or tmp, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return CompletionResult(error=f"TIMEOUT after {self.timeout}s")
        except Exception as e:
            return CompletionResult(error=f"subprocess error: {e}")
        base = CompletionResult(returncode=result.returncode,
                                stdout=result.stdout or "", stderr=result.stderr or "")
        combined = ((result.stderr or "") + (result.stdout or ""))
        if result.returncode != 0:
            base.error = f"codex exit {result.returncode}: {combined[-400:]}"
            low = combined.lower()
            base.fatal = ("model" in low and ("not found" in low or "not supported" in low
                                              or "does not exist" in low)) or "not logged in" in low
            return base
        try:
            parsed = parse_events(result.stdout)
        except Exception as e:
            base.error = f"codex stream error: {str(e)[:300]}"
            return base
        u = parsed.get("usage") or {}
        base.text = parsed.get("result", "") or ""
        base.usage = {"model": self.model, "input_tokens": u.get("input_tokens", 0),
                      "output_tokens": u.get("output_tokens", 0),
                      "cache_read_tokens": u.get("cached_input_tokens", 0)}
        return base

    def screen(self, idx, system_prompt, user_prompt):
        return _screen(self, idx, system_prompt, user_prompt)


def _screen(backend, idx, system_prompt, user_prompt):
    """One screening verdict, or None for a failure worth retrying.

    Three outcomes are kept apart that used to collapse into one unhelpful
    "inner JSON parse error":
      fatal        the backend can serve nothing -> raise, never retry
      empty reply  the model spent its whole token budget before answering
      unparseable  it answered, but not with JSON
    """
    r = backend.complete(system_prompt, user_prompt)
    if r.fatal:
        raise BackendUnavailable(r.error)
    if r.error:
        print(f"  [{idx}] {r.error}")
        return idx, None
    cap = getattr(backend, "max_tokens", None)
    if r.truncated or not (r.text or "").strip():
        what = "truncated at the output cap" if r.truncated else "empty reply"
        print(f"  [{idx}] {what} from {backend.model}"
              + (f" (max_tokens={cap}; a reasoning model can spend most of the budget "
                 f"before it emits anything)" if cap else ""))
        return idx, None
    inner = extract_json(r.text)
    if inner is None:
        print(f"  [{idx}] inner JSON parse error. Raw: {r.text[:200]}")
        return idx, None
    return idx, inner


# ── selection ───────────────────────────────────────────────────────────────

def _cfg(name, default):
    """config attribute, overridable by env var of the same name.

    Note the `or default` rather than getattr's default arg: config declares
    SCREENING_MODEL = None to mean "use the provider's default", and getattr
    would return that explicit None instead of falling through.
    """
    from mo_pipeline import config
    return os.environ.get(name) or getattr(config, name, None) or default


def _openrouter_key():
    """OPENROUTER_API_KEY from env, the repo's .env.local, or the observatory root's."""
    from mo_pipeline import config
    from mo_pipeline.shared.env import env_key
    return env_key("OPENROUTER_API_KEY", config.OBSERVATORY_ROOT / ".env.local")


BACKENDS = {b.name: b for b in (ClaudeCLIBackend, OpenRouterBackend, CodexCLIBackend)}


def get_backend(provider=None, model=None, **kwargs):
    """Instantiate the configured screening backend.

    Extra keyword arguments (timeout, cwd, max_tokens, ...) go to the backend
    constructor.

    `SCREENING_MODEL` is applied only when the caller wants the provider that
    config names. It is a provider-specific value, and reading it regardless of
    provider meant `get_backend(provider="claude_cli")` handed the Claude CLI
    "inclusionai/ling-2.6-flash" as soon as the configured default became
    OpenRouter -- so stage 4 classified nothing while still exiting 0.
    """
    configured = _cfg("SCREENING_PROVIDER", "claude_cli")
    provider = provider or configured
    if provider not in BACKENDS:
        raise ValueError(
            f"unknown SCREENING_PROVIDER {provider!r}; expected one of {sorted(BACKENDS)}"
        )
    if model is None and provider == configured:
        model = _cfg("SCREENING_MODEL", None) or None
    model = check_model(provider, model or PROVIDER_DEFAULT_MODEL[provider])
    return BACKENDS[provider](model=model, **kwargs)
