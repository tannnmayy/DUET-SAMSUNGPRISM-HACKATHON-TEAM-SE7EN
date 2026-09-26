"""The fast mind: one short, truthful acknowledgement the moment the user is done.

It runs alongside the thinker, never calls a tool, and never states a result.
If it is slow or fails, the agent simply says nothing extra; the thinker's answer
still comes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Optional

from .config import CONFIG
from .prompts import TALKER_INSTRUCTIONS

log = logging.getLogger("duet.talker")

SILENT = "<silent>"


def _gemini():
    from .gemini import client  # one client per process, API key or Vertex AI
    return client()


async def acknowledgement(user_text: str, context: str = "", usage: Optional[dict] = None) -> Optional[str]:
    """One sentence, or None when the talker has nothing useful to say in time.
    `usage`, if given, receives the call's token counts (for the cost analysis)."""
    if not CONFIG.talker_enabled or not user_text.strip():
        return None
    from google.genai import types

    prompt = (("Earlier in this call: " + context + "\n") if context else "") + "User: " + user_text
    from .gemini import sampling, thinking_config
    config = types.GenerateContentConfig(
        system_instruction=TALKER_INSTRUCTIONS,
        **sampling(CONFIG.talker_model),
        max_output_tokens=48,
        thinking_config=thinking_config(CONFIG.talker_model, "minimal"),
    )
    try:
        resp = await asyncio.wait_for(
            _gemini().aio.models.generate_content(model=CONFIG.talker_model, contents=prompt, config=config),
            timeout=CONFIG.talker_timeout_s,
        )
    except asyncio.TimeoutError:
        log.info("talker timed out after %.1f s", CONFIG.talker_timeout_s)
        return None
    except Exception as exc:
        log.warning("talker failed: %s", exc)
        return None
    text = (resp.text or "").strip().strip('"')
    u = getattr(resp, "usage_metadata", None)
    if u is not None and usage is not None:
        usage.update(input=int(u.prompt_token_count or 0), output=int(u.candidates_token_count or 0),
                     thinking=int(u.thoughts_token_count or 0), calls=1)
    if not text or SILENT in text:
        return None
    return text


async def as_stream(text_future: "asyncio.Future[Optional[str]]") -> AsyncIterator[str]:
    """Adapt a pending acknowledgement to the text stream `session.say()` accepts."""
    text = await text_future
    if text:
        yield text
