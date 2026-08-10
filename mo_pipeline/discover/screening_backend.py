"""
Pluggable screening backend for stage-4 classification.

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

The CLI backend is the DEFAULT and its behaviour is byte-for-byte the logic
that previously lived in `classify_candidates.classify_one`, so switching
providers is opt-in and reversible.

Usage:
    from mo_pipeline.discover.screening_backend import get_backend
    backend = get_backend()                      # honours config
    verdict = backend.screen(idx, title, abstract, system_prompt, user_prompt)

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

from mo_pipeline.config import LLM_MODEL, LLM_TIMEOUT_SEC

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


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


# ── backends ────────────────────────────────────────────────────────────────

class ClaudeCLIBackend:
    """`claude -p` subprocess. Free on the Max plan, rate-limited, holds the
    `claude_cli` mutex. This is the default and the historical behaviour."""

    name = "claude_cli"

    def __init__(self, model=None, timeout=None):
        self.model = model or LLM_MODEL
        self.timeout = timeout or LLM_TIMEOUT_SEC

    def screen(self, idx, system_prompt, user_prompt):
        """Return (idx, parsed_dict_or_None)."""
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
                text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"  [{idx}] TIMEOUT after {self.timeout}s")
            return idx, None
        except Exception as e:
            print(f"  [{idx}] subprocess error: {e}")
            return idx, None

        if result.returncode != 0:
            print(f"  [{idx}] CLI exit {result.returncode}: {(result.stderr or '')[:300]}")
            return idx, None

        try:
            outer = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            print(f"  [{idx}] outer JSON parse error: {e}")
            return idx, None

        if outer.get("is_error"):
            print(f"  [{idx}] CLI reported error: {outer.get('result', '')[:200]}")
            return idx, None

        inner = extract_json(outer.get("result", ""))
        if inner is None:
            print(f"  [{idx}] inner JSON parse error. Raw: {outer.get('result', '')[:200]}")
            return idx, None
        return idx, inner


class OpenRouterBackend:
    """OpenRouter chat-completions. Costs money but does not touch the Claude
    rate limit, so screening and extraction can run concurrently."""

    name = "openrouter"

    def __init__(self, model=None, timeout=None, api_key=None, max_retries=4):
        self.model = model or _cfg("SCREENING_MODEL", "openai/gpt-5-nano")
        self.timeout = timeout or LLM_TIMEOUT_SEC
        self.api_key = api_key or _openrouter_key()
        self.max_retries = max_retries
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter backend selected but no API key found. Set "
                "OPENROUTER_API_KEY in the environment or in a .env.local file."
            )

    def screen(self, idx, system_prompt, user_prompt):
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # Screening verdicts are small; cap output so a runaway generation
            # cannot silently multiply cost.
            "max_tokens": 400,
            "temperature": 0,
        }).encode()

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
                    print(f"  [{idx}] OpenRouter {e.code}, retry in {wait}s")
                    time.sleep(wait)
                    continue
                print(f"  [{idx}] OpenRouter HTTP {e.code}: {e.read()[:200]!r}")
                return idx, None
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                print(f"  [{idx}] OpenRouter error: {e}")
                return idx, None
        else:
            return idx, None

        if payload.get("error"):
            print(f"  [{idx}] OpenRouter error: {str(payload['error'])[:200]}")
            return idx, None
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            print(f"  [{idx}] unexpected OpenRouter payload: {str(payload)[:200]}")
            return idx, None

        inner = extract_json(content)
        if inner is None:
            print(f"  [{idx}] inner JSON parse error. Raw: {content[:200]}")
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
    """OPENROUTER_API_KEY from env, else from a .env.local beside the repo.

    Mirrors the `_get_env_key` pattern in shared/fetch_metadata_from_doi.py
    rather than introducing a second convention.
    """
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    from mo_pipeline import config
    for base in (config.REPO_ROOT, config.REPO_ROOT.parent):
        env = base / ".env.local"
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            if line.strip().startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return None


BACKENDS = {b.name: b for b in (ClaudeCLIBackend, OpenRouterBackend)}


def get_backend(provider=None, model=None):
    """Instantiate the configured screening backend.

    Defaults to claude_cli so existing behaviour is unchanged unless a caller
    or config explicitly opts into another provider.
    """
    provider = provider or _cfg("SCREENING_PROVIDER", "claude_cli")
    if provider not in BACKENDS:
        raise ValueError(
            f"unknown SCREENING_PROVIDER {provider!r}; expected one of {sorted(BACKENDS)}"
        )
    cls = BACKENDS[provider]
    if provider == "claude_cli":
        return cls(model=model or _cfg("SCREENING_MODEL", None) or LLM_MODEL)
    return cls(model=model)
