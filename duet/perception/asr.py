"""Speech recognition with calibrated uncertainty (M5).

Built on faster-whisper (CTranslate2), chosen for three reasons: int8
inference is fast enough to be practical on CPU as well as GPU, it exposes
WORD-LEVEL probabilities rather than only a sentence score, and it bundles a
decoder that reads MP3 without a system ffmpeg.

The word probabilities are the point. Measured on the kit's own clips with
`base`:

    pub_05_turn1  conf 0.38  "And look up now to the question."   (no city)
    pub_05_turn2  conf 0.57  "I said Boston."            Boston 0.95
    pub_06_part1  conf 0.40  "Look up light to Boston."  Boston 0.31
    pub_06_part2  conf 0.52  "Actually make that New York."  New 0.77 York 1.00

A sentence-level threshold cannot separate these: pub_06 must ACT at 0.40
while pub_05_turn1 must ASK at 0.38. What distinguishes them is the slot
word - "New York" is heard at 0.77/1.00, while the ambiguous clip yields no
city at all. So confidence is reported per word and the gate is applied to
the extracted value, not to the utterance.

Two settings that are not optional:

  language="en"      Without it Whisper language-detects on every clip and
                     drifts on noisy input - `small` returned Devanagari for
                     pub_05_turn1. Pinning also skips the detection pass and
                     roughly halves latency.
  ASCII output       Transcripts can contain non-ASCII the harness then tries
                     to print to a cp1252 console. That raises inside the
                     HARNESS and is recorded against us, so text is sanitised
                     before it ever leaves this module.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from .. import telemetry
from .base import ASRBackend, Transcript

# Model preference. A GPU gets the accurate turbo model; CPU gets something
# that actually finishes, because a 5-second transcription would eat the
# scenario budget even though the acknowledgment has already gone out.
_GPU_MODEL = os.environ.get("DUET_ASR_MODEL", "large-v3-turbo")
_CPU_MODEL = os.environ.get("DUET_ASR_MODEL_CPU", "base")

_MODEL: Any = None
_MODEL_NAME: str = ""


def _to_ascii(text: str) -> str:
    """Fold a transcript to ASCII without losing the words.

    Accents are decomposed and stripped, typographic punctuation is mapped to
    its ASCII equivalent, and anything left is dropped. A non-ASCII character
    reaching the harness's print on a cp1252 console is a UnicodeEncodeError
    inside the harness, recorded as our failure.
    """
    if not text:
        return ""
    swaps = {"‘": "'", "’": "'", "“": '"', "”": '"',
             "–": "-", "—": "-", "…": "...", " ": " "}
    for bad, good in swaps.items():
        text = text.replace(bad, good)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip()


def _select_device() -> Tuple[str, str, str]:
    """(device, compute_type, model_name), preferring CUDA when present."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16", _GPU_MODEL
    except Exception:  # noqa: BLE001
        pass
    return "cpu", "int8", _CPU_MODEL


class FasterWhisperASR(ASRBackend):
    name = "faster-whisper"

    def __init__(self) -> None:
        self.model = None
        self.model_name = ""
        self.device = "cpu"

    def available(self) -> bool:
        return self.model is not None

    async def warm(self) -> bool:
        """Load the model once per process, off the scenario clock.

        PROTOCOL.md section 5.3: a lazy load on the first user turn is charged
        to our latency, so this is called from setup() where it has its own
        300s budget and the cost is paid by the first scenario only.
        """
        global _MODEL, _MODEL_NAME
        if _MODEL is not None:
            self.model, self.model_name = _MODEL, _MODEL_NAME
            return True

        device, compute_type, model_name = _select_device()
        self.device = device

        def _load():
            from faster_whisper import WhisperModel
            return WhisperModel(model_name, device=device,
                                compute_type=compute_type)

        try:
            model = await asyncio.to_thread(_load)
        except Exception as exc:  # noqa: BLE001
            telemetry.log("asr.load_failed", model=model_name, device=device,
                          error=type(exc).__name__ + ": " + str(exc))
            return False

        _MODEL, _MODEL_NAME = model, model_name
        self.model, self.model_name = model, model_name
        telemetry.log("asr.loaded", model=model_name, device=device,
                      compute_type=compute_type)
        return True

    async def transcribe(self, path: str, duration_ms: float = 0.0) -> Transcript:
        if self.model is None:
            return Transcript(duration_ms=duration_ms, backend=self.name,
                              error="not_loaded")
        try:
            return await asyncio.to_thread(self._transcribe_sync, path, duration_ms)
        except Exception as exc:  # noqa: BLE001
            telemetry.log("asr.error", path=path,
                          error=type(exc).__name__ + ": " + str(exc))
            return Transcript(duration_ms=duration_ms, backend=self.name,
                              error=type(exc).__name__)

    def _transcribe_sync(self, path: str, duration_ms: float) -> Transcript:
        # to_thread keeps this off the event loop: CTranslate2 releases the
        # GIL, so the harness keeps delivering events while we decode.
        segments, _info = self.model.transcribe(
            path,
            language="en",
            beam_size=5,
            temperature=0.0,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        segments = list(segments)
        if not segments:
            return Transcript(duration_ms=duration_ms, backend=self.name,
                              error="no_speech")

        text = _to_ascii(" ".join(s.text.strip() for s in segments))
        avg_logprob = sum(s.avg_logprob for s in segments) / len(segments)
        confidence = float(min(1.0, max(0.0, math.exp(avg_logprob))))

        words: List[Tuple[str, float]] = []
        for segment in segments:
            for word in (segment.words or []):
                token = _to_ascii(word.word).strip(" .,!?;:")
                if token:
                    words.append((token, float(word.probability)))

        return Transcript(text=text, confidence=confidence, words=words,
                          duration_ms=duration_ms, backend=self.name)


# ---------------------------------------------------------------------------
# Slot-level confidence (M5)
# ---------------------------------------------------------------------------
def value_confidence(transcript: Transcript, value: str) -> Optional[float]:
    """How confident the model was about the words forming `value`.

    Returns None when the value cannot be located in the word list, which is
    itself informative - a value we extracted but cannot tie back to any
    recognised word is not one to act on.

    This is the number M5 gates on. The utterance score is too coarse:
    pub_06 must be acted on at sentence confidence 0.40 because "New York"
    was heard at 0.77/1.00, while pub_05_turn1 must be questioned at 0.38
    because no city survives at all.
    """
    if not value or not transcript.words:
        return None
    wanted = [w for w in re.findall(r"[a-z0-9']+", value.lower()) if w]
    if not wanted:
        return None

    probs: List[float] = []
    for token in wanted:
        best: Optional[float] = None
        for word, probability in transcript.words:
            if word.lower().strip(" .,!?;:") == token:
                best = probability if best is None else max(best, probability)
        if best is not None:
            probs.append(best)
    if not probs:
        return None
    return min(probs)


def merge(parts: List[Transcript]) -> Transcript:
    """Fold the chunks of one turn into a single transcript."""
    texts = [p.text.strip() for p in parts if p.text.strip()]
    words: List[Tuple[str, float]] = []
    for part in parts:
        words.extend(part.words)
    confidences = [p.confidence for p in parts if p.ok]
    return Transcript(
        text=" ".join(texts).strip(),
        confidence=min(confidences) if confidences else 0.0,
        words=words,
        duration_ms=sum(p.duration_ms for p in parts),
        backend=parts[0].backend if parts else "none",
        error=None if texts else "no_speech",
    )
