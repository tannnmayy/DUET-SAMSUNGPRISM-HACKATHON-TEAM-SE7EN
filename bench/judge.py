"""Which LLM judge the official FDB-v3 evaluation scripts talk to.

The official scripts create `openai.OpenAI()` and ask for "gpt-4o". Samsung runs
them with their own pinned judge (gpt-4o), so that is the judge that counts. Our
own numbers use, in this order:
  1. gpt-4o, when OPENAI_API_KEY works (one tiny probe request decides: a key on
     an account with no credits would otherwise fail every call under an official
     label). Then nothing here changes anything.
  2. Gemma 4 31B through Google's API (the same keys as the agent; the 31B has its
     own per-minute quota, so judging never slows the 26B thinker), answering the
     official prompts unchanged.
Reports from 2 are labelled "PROXY judge" and never presented as official.
DUET_JUDGE=openai|gemma|none forces a choice (default: auto).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def gemma_model() -> str:
    return os.environ.get("DUET_PROXY_JUDGE_MODEL", "gemma-4-31b-it")


class _GemmaCompletions:
    """chat.completions.create(), answered by Gemma through duet_voice.gemma_api (key
    pool, token budget, retries). DUET_JUDGE_RPM paces the calls further if needed."""

    def __init__(self, model: str) -> None:
        self._model = model
        rpm = float(os.environ.get("DUET_JUDGE_RPM", "0") or 0)
        self._gap = 60.0 / rpm if rpm > 0 else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def create(self, *, model: str = "", messages: List[dict], **kwargs: Any) -> Any:
        from google.genai import types
        from duet_voice import gemma_api
        if self._gap:
            with self._lock:
                now = time.monotonic()
                start = max(now, self._next)
                self._next = start + self._gap
            time.sleep(start - now)
        system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        turns = [types.Content(role="model" if m.get("role") == "assistant" else "user",
                               parts=[types.Part(text=str(m.get("content", "")))])
                 for m in messages if m.get("role") != "system"]
        config = types.GenerateContentConfig(
            system_instruction=system or None, seed=7,
            temperature=float(kwargs.get("temperature", 0) or 0))
        resp = gemma_api.generate_sync(self._model, turns, config, deadline_s=180.0)
        parts = (resp.candidates[0].content.parts or []) if resp.candidates else []
        text = "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class ProxyJudge:
    """An OpenAI-compatible client that answers the official prompts with Gemma."""

    def __init__(self, model: str) -> None:
        self.chat = SimpleNamespace(completions=_GemmaCompletions(model))
        self.model = model


class _Declines:
    def create(self, **kwargs: Any) -> Any:
        raise RuntimeError("no LLM judge configured")


class NoJudge:
    """Stands in for a client when no judge exists; every call fails politely."""

    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": _Declines()})()


def mode() -> str:
    m = os.environ.get("DUET_JUDGE", "auto").strip().lower() or "auto"
    if m not in ("auto", "openai", "gemma", "none"):
        # an unknown value (such as an old "gemini") must not quietly mean "no judge"
        print("WARNING: DUET_JUDGE=%s is not one of openai, gemma, none; choosing automatically." % m,
              flush=True)
        m = "auto"
    return m


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


def choice() -> str:
    """openai | gemma | none, decided once."""
    global _choice
    if _choice is None:
        m = mode()
        if m in ("auto", "openai") and openai_usable():
            _choice = "openai"
        elif m in ("auto", "gemma") and (os.environ.get("GOOGLE_API_KEYS") or os.environ.get("GOOGLE_API_KEY")):
            _choice = "gemma"
        else:
            _choice = "none"
    return _choice


def judge_label() -> str:
    c = choice()
    if c == "openai":
        return "gpt-4o (official judge)"
    if c == "gemma":
        return "%s through Google's API (PROXY judge, not official)" % gemma_model()
    return "none (exact-match arguments, response quality skipped)"


def proxy_client() -> Optional[ProxyJudge]:
    return ProxyJudge(gemma_model()) if choice() == "gemma" else None


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
