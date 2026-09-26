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
