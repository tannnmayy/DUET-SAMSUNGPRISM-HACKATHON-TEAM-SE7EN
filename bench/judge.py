"""Which LLM judge the official FDB-v3 evaluation scripts talk to.

The official scripts create `openai.OpenAI()` and ask for "gpt-4o". Samsung runs
them with their own pinned judge, so our reported numbers must come from gpt-4o
too whenever an OPENAI_API_KEY is available; then nothing here changes anything.

Without a usable OpenAI key, `install()` points the same prompts at a Gemini
model through Gemini's OpenAI-compatible endpoint. Reports produced that way are
labelled as a proxy judge and are never presented as official numbers. A key is
"usable" only if one tiny gpt-4o request succeeds: a key on an account with no
credits would otherwise make every judge call fail under an official label.
DUET_JUDGE=gemini forces the proxy.
"""

from __future__ import annotations

import os
from typing import Any, Optional

GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"


class _Completions:
    def __init__(self, client: Any, model: str) -> None:
        self._client, self._model = client, model

    def create(self, *, model: str, **kwargs: Any) -> Any:
        kwargs.pop("max_tokens", None)  # thinking models count thoughts against it
        return self._client.chat.completions.create(model=self._model, **kwargs)


class _Chat:
    def __init__(self, client: Any, model: str) -> None:
        self.completions = _Completions(client, model)


class GeminiJudge:
    def __init__(self, model: str) -> None:
        from openai import OpenAI
        key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        self.chat = _Chat(OpenAI(api_key=key, base_url=GEMINI_OPENAI_BASE), model)
        self.model = model


class _Declines:
    def create(self, **kwargs: Any) -> Any:
        raise RuntimeError("no LLM judge configured")


class NoJudge:
    """Stands in for a client when no judge key exists; every call fails politely."""

    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": _Declines()})()


_openai_ok: Optional[bool] = None


def openai_usable() -> bool:
    """Whether the official judge (gpt-4o) can actually be called. Checked once."""
    global _openai_ok
    if _openai_ok is None:
        _openai_ok = False
        if os.environ.get("OPENAI_API_KEY") and os.environ.get("DUET_JUDGE", "").lower() != "gemini":
            try:
                from openai import OpenAI
                OpenAI().chat.completions.create(model="gpt-4o", max_tokens=1,
                                                 messages=[{"role": "user", "content": "ok"}])
                _openai_ok = True
            except Exception as exc:
                print("WARNING: OPENAI_API_KEY is set but gpt-4o cannot be called (%s); "
                      "using the Gemini PROXY judge instead." % str(exc)[:160], flush=True)
    return _openai_ok


def judge_label() -> str:
    if openai_usable():
        return "gpt-4o (official judge)"
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return "%s via OpenAI-compatible endpoint (PROXY judge, not official)" % proxy_model()
    return "none (exact-match arguments, response quality skipped)"


def proxy_model() -> str:
    return os.environ.get("DUET_PROXY_JUDGE_MODEL", "gemini-3.5-flash")


def install(*modules: Any) -> Optional[str]:
    """Patch the official modules' client getter when gpt-4o cannot be used."""
    if openai_usable():
        return None
    if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        return None
    judge = GeminiJudge(proxy_model())
    for module in modules:
        if hasattr(module, "_get_openai_client"):
            module._openai_client = judge
            module._get_openai_client = lambda: judge
        if hasattr(module, "OpenAI"):
            module.OpenAI = lambda *a, **k: judge
    return judge.model
