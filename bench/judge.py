"""Which LLM judge the official FDB-v3 evaluation scripts talk to.

The official scripts create `openai.OpenAI()` and ask for "gpt-4o". Samsung runs
them with their own pinned judge (gpt-4o), so that is the judge that counts. Our
own numbers use, in this order:

  1. gpt-4o, when OPENAI_API_KEY works (one tiny probe request decides: a key on
     an account with no credits would otherwise fail every call under an official
     label). Then nothing here changes anything.
  2. The local model (Qwen3-30B-A3B-Instruct-2507 on our vLLM server), when that
     server is running.
  3. Gemini through its OpenAI-compatible endpoint, when a Gemini key is set.

Reports from 2 or 3 are labelled "PROXY judge" and never presented as official.
DUET_JUDGE=openai|local|gemini|none forces a choice (default: auto).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any, Optional

GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"


class _Completions:
    """DUET_JUDGE_RPM paces the calls, for keys with a per-minute request limit."""

    def __init__(self, client: Any, model: str) -> None:
        self._client, self._model = client, model
        rpm = float(os.environ.get("DUET_JUDGE_RPM", "0") or 0)
        self._gap = 60.0 / rpm if rpm > 0 else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def create(self, *, model: str, **kwargs: Any) -> Any:
        kwargs.pop("max_tokens", None)  # thinking models count thoughts against it
        if self._gap:
            with self._lock:
                now = time.monotonic()
                start = max(now, self._next)
                self._next = start + self._gap
            time.sleep(start - now)
        return self._client.chat.completions.create(model=self._model, **kwargs)


class _Chat:
    def __init__(self, client: Any, model: str) -> None:
        self.completions = _Completions(client, model)


class ProxyJudge:
    """An OpenAI-compatible client that answers the official prompts with another model."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        from openai import OpenAI
        self.chat = _Chat(OpenAI(api_key=api_key, base_url=base_url, timeout=120, max_retries=2), model)
        self.model = model


class _Declines:
    def create(self, **kwargs: Any) -> Any:
        raise RuntimeError("no LLM judge configured")


class NoJudge:
    """Stands in for a client when no judge exists; every call fails politely."""

    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": _Declines()})()


def mode() -> str:
    return os.environ.get("DUET_JUDGE", "auto").strip().lower() or "auto"


def local_base() -> str:
    return os.environ.get("DUET_LLM_BASE_URL", "http://127.0.0.1:18000/v1").rstrip("/")


def local_model() -> str:
    return os.environ.get("DUET_LLM_MODEL", "qwen3-30b-a3b-instruct-2507")


def gemini_model() -> str:
    return os.environ.get("DUET_PROXY_JUDGE_MODEL", "gemini-3.5-flash")


_openai_ok: Optional[bool] = None
_choice: Optional[str] = None


def openai_usable() -> bool:
    """Whether the official judge (gpt-4o) can actually be called. Checked once."""
    global _openai_ok
    if _openai_ok is None:
        _openai_ok = False
        if os.environ.get("OPENAI_API_KEY") and mode() in ("auto", "openai"):
            try:
                from openai import OpenAI
                OpenAI().chat.completions.create(model="gpt-4o", max_tokens=1,
                                                 messages=[{"role": "user", "content": "ok"}])
                _openai_ok = True
            except Exception as exc:
                print("WARNING: OPENAI_API_KEY is set but gpt-4o cannot be called (%s); "
                      "using a PROXY judge instead." % str(exc)[:160], flush=True)
    return _openai_ok


def local_usable() -> bool:
    """Whether the local model server answers and serves our model."""
    try:
        with urllib.request.urlopen(local_base() + "/models", timeout=5) as r:
            served = [m.get("id") for m in json.loads(r.read().decode("utf-8")).get("data", [])]
        return local_model() in served
    except Exception:
        return False


def choice() -> str:
    """openai | local | gemini | none, decided once."""
    global _choice
    if _choice is None:
        m = mode()
        if m in ("auto", "openai") and openai_usable():
            _choice = "openai"
        elif m in ("auto", "local") and local_usable():
            _choice = "local"
        elif m in ("auto", "gemini") and (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
            _choice = "gemini"
        else:
            _choice = "none"
    return _choice


def judge_label() -> str:
    c = choice()
    if c == "openai":
        return "gpt-4o (official judge)"
    if c == "local":
        return "%s on the local model server (PROXY judge, not official)" % local_model()
    if c == "gemini":
        return "%s via Gemini's OpenAI-compatible endpoint (PROXY judge, not official)" % gemini_model()
    return "none (exact-match arguments, response quality skipped)"


def proxy_client() -> Optional[ProxyJudge]:
    c = choice()
    if c == "local":
        return ProxyJudge(local_base(), os.environ.get("DUET_LLM_API_KEY", "local"), local_model())
    if c == "gemini":
        return ProxyJudge(GEMINI_OPENAI_BASE, os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"),
                          gemini_model())
    return None


def install(*modules: Any) -> Optional[str]:
    """Point the official modules' client at the proxy judge when gpt-4o cannot be used.
    Returns the proxy model's name, or None (gpt-4o, or no judge at all)."""
    judge = proxy_client()
    if judge is None:
        return None
    for module in modules:
        if hasattr(module, "_get_openai_client"):
            module._openai_client = judge
            module._get_openai_client = lambda: judge
        if hasattr(module, "OpenAI"):
            module.OpenAI = lambda *a, **k: judge
    return judge.model
