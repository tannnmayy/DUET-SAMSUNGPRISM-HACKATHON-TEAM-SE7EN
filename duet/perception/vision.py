"""Reading a camera frame.

Two complementary capabilities, because visual scenarios ask two different
kinds of question.

  "What is THIS port used for?"     -> which of several candidates is depicted
  "What does this error code say?"  -> read what is written in the frame

The first is answered by visual re-ranking in embed.py, which needs only CLIP
and, crucially, takes its candidate labels from the tool's own results rather
than from a vocabulary we invent. That generalises to frames of things we have
never seen.

The second needs a model that can actually describe and read, which is what
this module provides. It is optional by design: `available()` is part of the
contract, and with no VLM the agent still calls the manual tool, still passes
a frame embedding, and still re-ranks the results visually - it simply cannot
volunteer detail that was only in the pixels.

Sized for the evaluation machine (one 48GB A6000). The default is a small
vision-language model rather than the largest that would fit: the scenarios
are short, the frames are single images, and a 7B model's extra accuracy is
not worth the download risk against the 300s setup budget. DUET_VLM_MODEL
overrides it.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, List, Optional

from .. import telemetry
from .base import FrameReading, VisionBackend

_MODEL_ID = os.environ.get("DUET_VLM_MODEL", "Qwen/Qwen2.5-VL-3B-Instruct")
_MAX_NEW_TOKENS = int(os.environ.get("DUET_VLM_TOKENS", "64"))

_MODEL: Any = None
_PROCESSOR: Any = None

# One prompt, answered in a fixed shape so the reply can be parsed without a
# second model. Asking for the search phrase directly is deliberate: what the
# planner needs is not prose but something a manual-style tool can search for.
_PROMPT = (
    "Look at this photograph. The user is pointing at the most prominent "
    "object in the centre. Answer in exactly three lines:\n"
    "OBJECT: <what the central object is, two or three words>\n"
    "TEXT: <any text or label printed on or beside it, or NONE>\n"
    "SEARCH: <a short phrase to look this up in a device manual>"
)


def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


class VlmVision(VisionBackend):
    name = "vlm"

    def __init__(self) -> None:
        self.model = None
        self.processor = None

    def available(self) -> bool:
        return self.model is not None

    async def warm(self) -> bool:
        """Load the model once per process, off the scenario clock.

        Refuses to load on CPU: a vision-language model there takes tens of
        seconds per frame, which would consume the scenario budget without
        producing an answer in time. Degrading to CLIP re-ranking is strictly
        better than being slow, so this returns False and the caller installs
        the null backend.
        """
        global _MODEL, _PROCESSOR
        if _MODEL is not None:
            self.model, self.processor = _MODEL, _PROCESSOR
            return True

        if not _cuda_available() and not os.environ.get("DUET_VLM_ON_CPU"):
            telemetry.log("vision.skipped", why="no_cuda", model=_MODEL_ID)
            return False

        def _load():
            import torch
            from transformers import AutoModelForImageTextToText, AutoProcessor
            processor = AutoProcessor.from_pretrained(_MODEL_ID)
            model = AutoModelForImageTextToText.from_pretrained(
                _MODEL_ID,
                torch_dtype=torch.float16 if _cuda_available() else torch.float32,
                device_map="auto" if _cuda_available() else None,
            )
            model.eval()
            return model, processor

        try:
            model, processor = await asyncio.to_thread(_load)
        except Exception as exc:  # noqa: BLE001
            telemetry.log("vision.load_failed", model=_MODEL_ID,
                          error=type(exc).__name__ + ": " + str(exc))
            return False

        _MODEL, _PROCESSOR = model, processor
        self.model, self.processor = model, processor
        telemetry.log("vision.loaded", model=_MODEL_ID)
        return True

    async def read_frame(self, path: str,
                         device_hint: Optional[str] = None) -> FrameReading:
        if self.model is None:
            return FrameReading(device_hint=device_hint, backend=self.name,
                                error="not_loaded")
        try:
            return await asyncio.to_thread(self._read_sync, path, device_hint)
        except Exception as exc:  # noqa: BLE001
            telemetry.log("vision.error", path=path,
                          error=type(exc).__name__ + ": " + str(exc))
            return FrameReading(device_hint=device_hint, backend=self.name,
                                error=type(exc).__name__)

    def _read_sync(self, path: str, device_hint: Optional[str]) -> FrameReading:
        import torch
        from PIL import Image

        with Image.open(path) as handle:
            image = handle.convert("RGB")

            messages = [{"role": "user", "content": [
                {"type": "image"},
                {"type": "text", "text": _PROMPT},
            ]}]
            prompt = self.processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True)
            inputs = self.processor(images=image, text=prompt,
                                    return_tensors="pt")

        inputs = {k: v.to(self.model.device) if hasattr(v, "to") else v
                  for k, v in inputs.items()}
        with torch.inference_mode():
            generated = self.model.generate(**inputs,
                                            max_new_tokens=_MAX_NEW_TOKENS,
                                            do_sample=False)
        text = self.processor.batch_decode(generated,
                                           skip_special_tokens=True)[0]
        return _parse(text, device_hint, self.name)


def _field(text: str, label: str) -> str:
    match = re.search(label + r"\s*:\s*(.+)", text, re.I)
    if not match:
        return ""
    return _ascii(match.group(1).splitlines()[0].strip().strip('"'))


def _ascii(text: str) -> str:
    """Model output can contain anything; the harness prints it to a console
    that may be cp1252, and a UnicodeEncodeError there is recorded as ours."""
    return re.sub(r"\s+", " ",
                  (text or "").encode("ascii", "ignore").decode("ascii")).strip()


def _parse(raw: str, device_hint: Optional[str], backend: str) -> FrameReading:
    focus = _field(raw, "OBJECT")
    labels_line = _field(raw, "TEXT")
    query = _field(raw, "SEARCH")

    labels: List[str] = []
    if labels_line and labels_line.upper() != "NONE":
        labels = [part.strip() for part in re.split(r"[,;/]", labels_line)
                  if part.strip()]

    # A label printed on the object is the strongest evidence there is - a
    # port stamped "HDMI" is an HDMI port - so it outranks the model's own
    # guess when the two disagree.
    if labels and focus and labels[0].lower() not in focus.lower():
        focus = (labels[0] + " " + focus).strip()
    if not query:
        query = focus

    confidence = 0.0
    if focus:
        confidence = 0.6
    if labels:
        confidence = 0.85

    return FrameReading(focus=focus, summary=_ascii(raw)[:240], query=query,
                        labels=labels, device_hint=device_hint,
                        confidence=confidence, backend=backend,
                        error=None if (focus or query) else "unparsed")
