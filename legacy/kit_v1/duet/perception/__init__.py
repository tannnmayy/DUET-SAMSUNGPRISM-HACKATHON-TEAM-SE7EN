"""Perception: turning raw media into values the planner can use.

Audio turns arrive as MP3 with no transcript and visual turns as PNG with no
caption. These are ~50% of the hidden set by count and 60% by weight.

The orchestration matters more than the models:

  * acknowledge first, perceive behind the acknowledgment - transcription
    takes hundreds of milliseconds and the latency clock does not wait;
  * start on a frame the moment it ARRIVES, not when it is asked about (M4);
  * carry uncertainty forward and ask rather than guess (M5);
  * every backend is optional, and missing one degrades to a clarification.
"""

from .base import (  # noqa: F401
    ASRBackend, EmbedBackend, FrameReading, NullASR, NullEmbed, NullVision,
    Transcript, VisionBackend, install, load_asr, load_embed, load_vision,
    reset, resolve_media,
)
