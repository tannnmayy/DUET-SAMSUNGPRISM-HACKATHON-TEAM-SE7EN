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
            if uses_vertex():
                _client = genai.Client(vertexai=True,
                                       project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                                       location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"))
            else:
                _client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))
    return _client

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
