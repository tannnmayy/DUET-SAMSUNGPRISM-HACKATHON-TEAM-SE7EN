"""Reading a camera frame.

Two complementary capabilities, because visual scenarios ask two different
kinds of question.

  "What is THIS port used for?"     -> which of several candidates is depicted
  "What does this error code say?"  -> read what is written in the frame

The first is where the points are. The manual tool ranks pages by the words in
its query, and "what is this port used for" names nothing: every connector
page matches "port" equally and the right one is not even returned. Only the
pixels can supply the missing word, so the reading must exist BEFORE the tool
is called - hence frame-ahead scheduling and the barrier in runtime._on_turn.

GPU only. A vision-language model on CPU takes tens of seconds per frame,
which would consume the conversation without producing an answer in time;
with no GPU the agent degrades to CLIP re-ranking and an honest citation.
The checkpoint is pinned in checkpoints.py; DUET_VLM_MODEL overrides it.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, List, Optional

from .. import telemetry
from . import checkpoints
from .base import FrameReading, VisionBackend

_MAX_NEW_TOKENS = int(os.environ.get("DUET_VLM_TOKENS", "48"))

_MODEL: Any = None
_PROCESSOR: Any = None
_MODEL_NAME: str = ""

# One prompt, answered in a fixed shape so the reply can be parsed without a
# second model. Asking for the search phrase directly is deliberate: what the
# planner needs is not prose but something a manual-style tool can search for.
#
# "Most prominent", not "centre": a user pointing a phone at a row of
# connectors rarely centres the one they mean, but it is the largest and
# sharpest thing in the shot. The examples in the prompt are deliberately
# NOT drawn from any scenario we test on - an example that matches the test
# frame would score by suggestion rather than by sight.
_PROMPT = (
    "A user pointed their camera at something and is about to ask about it. "
    "Identify the single item they most likely mean: the most prominent, "
    "largest or sharpest one in the photo. Use its specific technical name "
    "(for example 'power button', 'SIM card tray' or 'error code display').\n"
    "Answer in exactly three lines:\n"
    "OBJECT: <the item, two to four words>\n"
    "TEXT: <any text or symbol printed on or right next to it, or NONE>\n"
    "SEARCH: <a short phrase to look it up in a device manual>"
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
        self.model_name = ""

    def available(self) -> bool:
        return self.model is not None

    async def warm(self) -> bool:
        """Load the model once per process, off the scenario clock, and prove
        it can generate.

        Refuses to load on CPU (see module docstring) unless DUET_VLM_ON_CPU
        is set for testing. A trial generation runs here because a model that
        loads is not a model that works: a missing kernel or an out-of-memory
        on the first real frame would otherwise surface mid-conversation.
        """
        global _MODEL, _PROCESSOR, _MODEL_NAME
        if _MODEL is not None:
            self.model, self.processor, self.model_name = _MODEL, _PROCESSOR, _MODEL_NAME
            return True

        ckpt = checkpoints.get("VLM")
        on_gpu = _cuda_available()
        if not on_gpu and not os.environ.get("DUET_VLM_ON_CPU"):
            telemetry.log("vision.skipped", why="no_cuda", model=ckpt.repo)
            return False

        def _load():
            import torch
            from PIL import Image
            from transformers import AutoModelForImageTextToText, AutoProcessor
            processor = AutoProcessor.from_pretrained(ckpt.repo, revision=ckpt.revision)
            model = AutoModelForImageTextToText.from_pretrained(
                ckpt.repo, revision=ckpt.revision,
                dtype=torch.float16 if on_gpu else torch.float32,
                device_map="auto" if on_gpu else None,
            )
            model.eval()
            _generate(model, processor, Image.new("RGB", (64, 64), "gray"), 4)
            return model, processor

        try:
            model, processor = await asyncio.to_thread(_load)
        except Exception as exc:  # noqa: BLE001
            telemetry.log("vision.load_failed", model=ckpt.repo,
                          error=type(exc).__name__ + ": " + str(exc)[:300])
            return False

        _MODEL, _PROCESSOR, _MODEL_NAME = model, processor, ckpt.repo
        self.model, self.processor, self.model_name = model, processor, ckpt.repo
        telemetry.log("vision.loaded", model=ckpt.repo, revision=ckpt.revision,
                      device="cuda" if on_gpu else "cpu")
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
        from PIL import Image
        with Image.open(path) as handle:
            image = handle.convert("RGB")
        text = _generate(self.model, self.processor, image, _MAX_NEW_TOKENS)
        return parse_reading(text, device_hint, self.name)


def _generate(model: Any, processor: Any, image: Any, max_new_tokens: int) -> str:
    """Greedy, deterministic generation; returns only the new text."""
    import torch
    messages = [{"role": "user", "content": [
        {"type": "image"},
        {"type": "text", "text": _PROMPT},
    ]}]
    prompt = processor.apply_chat_template(messages, tokenize=False,
                                           add_generation_prompt=True)
    inputs = processor(images=[image], text=prompt, return_tensors="pt")
    inputs = {k: v.to(model.device) if hasattr(v, "to") else v
              for k, v in inputs.items()}
    with torch.inference_mode():
        generated = model.generate(**inputs, max_new_tokens=max_new_tokens,
                                   do_sample=False)
    new_tokens = generated[:, inputs["input_ids"].shape[1]:]
    return processor.batch_decode(new_tokens, skip_special_tokens=True)[0]


def _field(text: str, label: str) -> str:
    match = re.search(label + r"\s*:\s*(.+)", text, re.I)
    if not match:
        return ""
    return _ascii(match.group(1).splitlines()[0].strip().strip('"<>'))


def _ascii(text: str) -> str:
    """Model output can contain anything; the harness prints it to a console
    that may be cp1252, and a UnicodeEncodeError there is recorded as ours."""
    return re.sub(r"\s+", " ",
                  (text or "").encode("ascii", "ignore").decode("ascii")).strip()


def parse_reading(raw: str, device_hint: Optional[str], backend: str) -> FrameReading:
    focus = _field(raw, "OBJECT")
    labels_line = _field(raw, "TEXT")
    query = _field(raw, "SEARCH")

    labels: List[str] = []
    if labels_line and labels_line.upper() not in ("NONE", "N/A", "NO TEXT"):
        labels = [part.strip() for part in re.split(r"[,;/]", labels_line)
                  if part.strip()]

    # A printed label is the strongest evidence there is - a port stamped
    # "HDMI" is an HDMI port - so when the model's own name for the object
    # does not include it, the label is put in front of it.
    if labels and focus and labels[0].lower() not in focus.lower() \
            and len(labels[0]) <= 24:
        focus = (labels[0] + " " + focus).strip()
    if not query:
        query = focus
    # The object name is the most specific search term there is, so it is
    # always part of the query even when the model phrased SEARCH loosely.
    if focus and focus.lower() not in query.lower():
        query = (focus + " " + query).strip()

    confidence = 0.0
    if focus:
        confidence = 0.6
    if labels and focus and any(l.lower() in focus.lower() or focus.lower() in l.lower()
                                for l in labels):
        # A printed label that agrees with the identification: the port is
        # stamped "HDMI" AND looks like one.
        confidence = 0.85

    return FrameReading(focus=focus, summary=_ascii(raw)[:240], query=query,
                        labels=labels, device_hint=device_hint,
                        confidence=confidence, backend=backend,
                        error=None if (focus or query) else "unparsed")
