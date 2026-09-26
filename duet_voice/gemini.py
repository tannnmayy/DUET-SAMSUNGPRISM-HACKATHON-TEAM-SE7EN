"""Small helpers shared by the talker and thinker for the Gemini API."""

from __future__ import annotations

from typing import Optional

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
