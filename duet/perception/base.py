"""Perception interfaces, and what the rest of DUET is allowed to assume.

Audio and visual scenarios are ~50% of the hidden set by count and 60% of it
by weight, and both arrive as raw media with no transcript and no caption. The
model that reads them is the least certain part of this system, so the
interface is built around that uncertainty rather than hiding it.

Three principles shape this module.

1. Perception returns a posterior, not a string. A Transcript carries the
   competing hypotheses and a confidence, because "I think they said Boston,
   but it could be Austin" is the single most useful thing an ASR can tell an
   agent that is about to spend money on a booking (M5).

2. Every backend is optional. `available()` is part of the contract, and a
   missing model degrades to asking the user rather than crashing or
   guessing. A scenario where we clarify because we could not hear properly
   still scores; a scenario where we crash scores zero.

3. Heavy resources load once per PROCESS, not once per scenario. The harness
   constructs a fresh agent for every scenario and every repetition, so the
   caches here are module level and `setup()` is where they are warmed -
   before the virtual clock starts, under its own 300s budget.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import config, telemetry


@dataclass
class Transcript:
    """What the ASR heard, including what it was unsure about."""

    text: str = ""
    confidence: float = 0.0
    alternatives: List[Tuple[str, float]] = field(default_factory=list)
    words: List[Tuple[str, float]] = field(default_factory=list)
    duration_ms: float = 0.0
    backend: str = "none"
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and self.error is None

    def competing(self, margin: float) -> List[str]:
        """Alternative readings close enough to be genuine rivals.

        These become the options in "did you say Austin or Boston?". An
        alternative that is far behind is not ambiguity, it is noise.
        """
        if not self.alternatives:
            return []
        best = max(score for _, score in self.alternatives)
        return [text for text, score in self.alternatives
                if best - score <= margin and text.strip()]


@dataclass
class FrameReading:
    """What the vision model saw in a camera frame."""

    focus: str = ""          # the thing the user is pointing at, e.g. "HDMI port"
    summary: str = ""        # a short description of the whole frame
    query: str = ""          # a search phrase suitable for a manual-style tool
    labels: List[str] = field(default_factory=list)   # text visible in the frame
    device_hint: Optional[str] = None
    confidence: float = 0.0
    backend: str = "none"
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.focus.strip() or self.query.strip()) and self.error is None


# ---------------------------------------------------------------------------
# Backend protocols
# ---------------------------------------------------------------------------
class ASRBackend:
    name = "base"

    def available(self) -> bool:
        return False

    async def transcribe(self, path: str, duration_ms: float = 0.0) -> Transcript:
        raise NotImplementedError


class VisionBackend:
    name = "base"

    def available(self) -> bool:
        return False

    async def read_frame(self, path: str,
                         device_hint: Optional[str] = None) -> FrameReading:
        raise NotImplementedError


class EmbedBackend:
    name = "base"
    dimensions = 0

    def available(self) -> bool:
        return False

    async def embed_image(self, path: str) -> List[float]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Null backends - the bottom of the degradation ladder
# ---------------------------------------------------------------------------
class NullASR(ASRBackend):
    """Used when no speech model is present.

    Returns an empty transcript with zero confidence, which routes the agent
    to a clarification. That is the honest response to "I cannot hear you",
    and it still earns the clarification and latency checkpoints instead of
    scoring nothing.
    """

    name = "null"

    def available(self) -> bool:
        return False

    async def transcribe(self, path: str, duration_ms: float = 0.0) -> Transcript:
        return Transcript(text="", confidence=0.0, duration_ms=duration_ms,
                          backend=self.name, error="no_asr_backend")


class NullVision(VisionBackend):
    name = "null"

    def available(self) -> bool:
        return False

    async def read_frame(self, path: str,
                         device_hint: Optional[str] = None) -> FrameReading:
        return FrameReading(device_hint=device_hint, backend=self.name,
                            error="no_vision_backend")


class NullEmbed(EmbedBackend):
    name = "null"

    def available(self) -> bool:
        return False

    async def embed_image(self, path: str) -> List[float]:
        return []


# ---------------------------------------------------------------------------
# Media helpers
# ---------------------------------------------------------------------------
def resolve_media(ref: Any) -> Optional[str]:
    """Turn a scenario media reference into a path that exists.

    PROTOCOL.md: paths are relative to the kit root, and "your agent must not
    crash if a referenced file is missing". Hidden scenarios ship their media
    the same way, but we do not control the working directory the evaluator
    runs from, so a few plausible roots are tried before giving up.
    """
    if not isinstance(ref, str) or not ref.strip():
        return None
    candidates = [ref]
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates.append(os.path.join(here, ref))
    candidates.append(os.path.join(os.getcwd(), ref))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    telemetry.log("perception.media_missing", ref=ref)
    return None


# ---------------------------------------------------------------------------
# Backend selection, cached per process
# ---------------------------------------------------------------------------
_ASR: Optional[ASRBackend] = None
_VISION: Optional[VisionBackend] = None
_EMBED: Optional[EmbedBackend] = None


def _env_disabled(name: str) -> bool:
    return os.environ.get(name, "") not in ("", "0", "false", "False")


async def load_asr() -> ASRBackend:
    """Load the best available speech backend, once per process."""
    global _ASR
    if _ASR is not None:
        return _ASR
    if _env_disabled("DUET_NO_ASR"):
        _ASR = NullASR()
        return _ASR
    try:
        from .asr import FasterWhisperASR
        backend = FasterWhisperASR()
        if await backend.warm():
            _ASR = backend
            telemetry.log("perception.asr_ready", backend=backend.name)
            return _ASR
    except Exception as exc:  # noqa: BLE001
        telemetry.log("perception.asr_unavailable",
                      error=type(exc).__name__ + ": " + str(exc))
    _ASR = NullASR()
    return _ASR


async def load_vision() -> VisionBackend:
    global _VISION
    if _VISION is not None:
        return _VISION
    if _env_disabled("DUET_NO_VISION"):
        _VISION = NullVision()
        return _VISION
    try:
        from .vision import VlmVision
        backend = VlmVision()
        if await backend.warm():
            _VISION = backend
            telemetry.log("perception.vision_ready", backend=backend.name)
            return _VISION
    except Exception as exc:  # noqa: BLE001
        telemetry.log("perception.vision_unavailable",
                      error=type(exc).__name__ + ": " + str(exc))
    _VISION = NullVision()
    return _VISION


async def load_embed() -> EmbedBackend:
    global _EMBED
    if _EMBED is not None:
        return _EMBED
    if _env_disabled("DUET_NO_EMBED"):
        _EMBED = NullEmbed()
        return _EMBED
    try:
        from .embed import ClipEmbed
        backend = ClipEmbed()
        if await backend.warm():
            _EMBED = backend
            telemetry.log("perception.embed_ready", backend=backend.name)
            return _EMBED
    except Exception as exc:  # noqa: BLE001
        telemetry.log("perception.embed_unavailable",
                      error=type(exc).__name__ + ": " + str(exc))
    _EMBED = NullEmbed()
    return _EMBED


def install(asr: Optional[ASRBackend] = None,
            vision: Optional[VisionBackend] = None,
            embed: Optional[EmbedBackend] = None) -> None:
    """Override the cached backends. Tests use this to run the orchestration
    deterministically without any model at all."""
    global _ASR, _VISION, _EMBED
    if asr is not None:
        _ASR = asr
    if vision is not None:
        _VISION = vision
    if embed is not None:
        _EMBED = embed


def reset() -> None:
    """Drop cached backends. Tests only - the agent never calls this, because
    reloading a model per scenario would blow the setup budget."""
    global _ASR, _VISION, _EMBED
    _ASR = _VISION = _EMBED = None
