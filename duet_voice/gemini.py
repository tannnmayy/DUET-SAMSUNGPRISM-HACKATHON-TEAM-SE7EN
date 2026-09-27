"""Small helpers shared by the talker and thinker for the Gemini API."""

from __future__ import annotations

import os
import threading
from typing import Optional

_client = None
_client_lock = threading.Lock()


def uses_vertex() -> bool:
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes")


def client():
    """One Gemini client per process, for either way of reaching Gemini:

    - Gemini API key (GOOGLE_API_KEY): what Samsung's re-run uses, and the default.
    - Vertex AI on Google Cloud (GOOGLE_GENAI_USE_VERTEXAI=true, GOOGLE_CLOUD_PROJECT,
      GOOGLE_CLOUD_LOCATION, and application-default credentials)."""
    global _client
    with _client_lock:
        if _client is None:
            from google import genai
            http = http_options()
            if uses_vertex():
                _client = genai.Client(vertexai=True,
                                       project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                                       location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
                                       http_options=http)
            else:
                _client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"),
                                       http_options=http)
    return _client


def http_options():
    """Retry transient failures instead of failing the conversation.

    A rate limit (429) or a brief server error (5xx) on the re-run machine would
    otherwise end the turn with an apology and fail the item. Three retries with
    backoff (about 1, 2 and 4 s) fit well inside the benchmark's ~30 s answer
    window; a request that hangs is cut off after DUET_GEMINI_TIMEOUT_MS."""
    from google.genai import types
    return types.HttpOptions(
        timeout=int(os.environ.get("DUET_GEMINI_TIMEOUT_MS", "20000")),
        retry_options=types.HttpRetryOptions(
            attempts=int(os.environ.get("DUET_GEMINI_ATTEMPTS", "4")),
            initial_delay=1.0, max_delay=8.0, exp_base=2.0, jitter=0.3,
            http_status_codes=[408, 429, 500, 502, 503, 504]))


def sampling(model: str) -> dict:
    """Temperature and seed for a model call.

    The seed is always fixed. Temperature is DUET_TEMPERATURE when set; otherwise
    0 on Gemini 2.x and the default 1.0 on Gemini 3, for which Google recommends
    against lowering it (it can cause looping or degraded reasoning)."""
    from .config import CONFIG
    if CONFIG.temperature != "":
        temperature = float(CONFIG.temperature)
    else:
        temperature = 0.0 if model.startswith("gemini-2") else 1.0
    return {"temperature": temperature, "seed": CONFIG.seed}


# Gemini 2.5 models take a token budget; Gemini 3 models take a named level.
_BUDGETS_25 = {"minimal": 0, "none": 0, "low": 512, "medium": 2048, "high": 8192}


def thinking_config(model: str, level: str):
    """The right ThinkingConfig for a model family, or None for the model default.

    2.5 Pro cannot switch thinking off (its minimum budget is 128)."""
    from google.genai import types
    level = (level or "").lower()
    if not level:
        return None
    if model.startswith("gemini-2.5"):
        budget = _BUDGETS_25.get(level, 512)
        if "pro" in model:
            budget = max(budget, 128)
        return types.ThinkingConfig(thinking_budget=budget)
    if level == "none":
        level = "minimal"
    return types.ThinkingConfig(thinking_level=level)


# --- which model actually serves each role -------------------------------------------------
# Google withdraws models from new keys without notice: in September 2026
# gemini-2.5-flash-lite and gemini-2.5-pro began answering "no longer available to
# new users". A re-run on someone else's key must not turn every reply into an
# apology because of that, so each role falls back down a list of current models.
FALLBACKS = {
    "thinker": ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-2.5-flash"],
    "talker": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-3.5-flash"],
}
_resolved: dict = {}
_notes: list = []


def probe(model: str) -> tuple:
    """One tiny request. Returns (verdict, detail): ok | unavailable | rate_limited
    | free_tier | no_credit | bad_key | error."""
    from google.genai import types
    try:
        client().models.generate_content(model=model, contents="Reply with the word ok.",
                                         config=types.GenerateContentConfig(max_output_tokens=32))
        return "ok", ""
    except Exception as exc:
        text = str(exc)
        if "free_tier" in text:
            return "free_tier", text[:300]
        if "prepayment credits" in text or "402" in text[:12]:
            return "no_credit", text[:300]
        if "RESOURCE_EXHAUSTED" in text or " 429" in text[:12]:
            return "rate_limited", text[:300]
        if "NOT_FOUND" in text or "no longer available" in text or "not found" in text.lower():
            return "unavailable", text[:300]
        if "API key" in text or "API_KEY" in text or "PERMISSION_DENIED" in text or "UNAUTHENTICATED" in text:
            return "bad_key", text[:300]
        return "error", text[:300]


def resolve(role: str, preferred: str) -> str:
    """The model this key can use for a role: the preferred one, or the first
    fallback the API serves. Only a model the API refuses is skipped; a rate limit
    or a network error keeps the preferred model (the retries handle those)."""
    if role in _resolved:
        return _resolved[role]
    chosen = preferred
    for model in [preferred] + [m for m in FALLBACKS.get(role, []) if m != preferred]:
        verdict, detail = probe(model)
        if verdict == "unavailable":
            _notes.append("%s %s unavailable to this key; trying the next model" % (role, model))
            continue
        if verdict in ("free_tier", "rate_limited"):
            _notes.append("%s %s: %s (the key may be on the free tier: 5 requests/min, "
                          "20/day per model, not enough for a benchmark run)" % (role, model, verdict))
        elif verdict == "bad_key":
            _notes.append("%s %s: the API key was refused (%s)" % (role, model, detail[:120]))
        elif verdict == "no_credit":
            _notes.append("%s %s: the key's prepaid credit is used up; add credits in AI Studio "
                          "(Billing, Buy credits) before running" % (role, model))
        elif verdict == "error":
            _notes.append("%s %s: probe failed (%s); keeping it" % (role, model, detail[:120]))
        chosen = model
        break
    _resolved[role] = chosen
    return chosen


def resolved(role: str, preferred: str) -> str:
    """The model resolve() chose for a role, or `preferred` if nothing was resolved
    (tests, offline runs with an explicit model)."""
    return _resolved.get(role, preferred)


def main() -> int:
    """python -m duet_voice.gemini: which models this key can use; JSON on stdout."""
    import json
    import sys
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"))
    from .config import CONFIG
    out = {"thinker": resolve("thinker", CONFIG.thinker_model),
           "talker": resolve("talker", CONFIG.talker_model), "notes": _notes}
    print(json.dumps(out))
    return 1 if any("refused" in n or "prepaid credit" in n for n in _notes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
