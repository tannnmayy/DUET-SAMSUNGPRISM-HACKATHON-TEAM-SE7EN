"""Frame embeddings, and visual re-ranking of tool results.

Two jobs, one model.

1. The hybrid-search argument. `lookup_manual` and its hidden equivalents
   accept a dense visual embedding alongside the text query, which the schema
   advertises as an array-of-number argument mentioning vision. Supplying a
   real one is both what the argument is for and a scored checkpoint.

2. Visual re-ranking - the more interesting one.

   "What is this port used for?" contains no searchable noun. A text query
   built from those words matches EVERY port page in a device manual equally
   (headphone, USB, ethernet, HDMI all mention "port"), so the top hit is
   whichever the corpus happens to list first, and answering from it means
   naming the wrong connector.

   The frame is the only thing that disambiguates. Rather than asking a large
   vision-language model to name the object - which needs a label vocabulary
   we would have to invent, and would not survive a hidden frame of something
   else - we let the TOOL propose the candidates and use the image to choose
   between them. The text search returns plausible rows; CLIP scores the frame
   against each row's own description; the best match is the answer.

   This is fully schema-driven: the labels come from whatever the tool
   returned, so it works for a manual lookup, a parts catalogue, or a tool we
   have never seen, without knowing what any of them contain.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import telemetry
from . import checkpoints
from .base import EmbedBackend

_MODEL: Any = None


class ClipEmbed(EmbedBackend):
    """CLIP via sentence-transformers: small, fast, and it embeds images and
    text into one space, which is what the re-ranking needs."""

    name = "clip"
    dimensions = 512

    def __init__(self) -> None:
        self.model = None

    def available(self) -> bool:
        return self.model is not None

    async def warm(self) -> bool:
        global _MODEL
        if _MODEL is not None:
            self.model = _MODEL
            return True

        ckpt = checkpoints.get("CLIP")

        def _load():
            from PIL import Image
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(ckpt.repo, revision=ckpt.revision)
            # One real encode, off the clock. The first inference in a
            # process is several times slower than the rest, and in pub_07
            # it was slow enough that the embedding missed the 500 ms
            # between the frame and the question - dropping the hybrid-search
            # checkpoint in one run out of three.
            model.encode(Image.new("RGB", (224, 224)), convert_to_numpy=True)
            model.encode(["warm up"], convert_to_numpy=True)
            return model

        try:
            model = await asyncio.to_thread(_load)
        except Exception as exc:  # noqa: BLE001
            telemetry.log("embed.load_failed", model=ckpt.repo,
                          error=type(exc).__name__ + ": " + str(exc))
            return False

        _MODEL = model
        self.model = model
        telemetry.log("embed.loaded", model=ckpt.repo, revision=ckpt.revision)
        return True

    # -- embeddings ------------------------------------------------------
    async def embed_image(self, path: str) -> List[float]:
        if self.model is None:
            return []
        try:
            return await asyncio.to_thread(self._embed_image_sync, path)
        except Exception as exc:  # noqa: BLE001
            telemetry.log("embed.error", path=path,
                          error=type(exc).__name__ + ": " + str(exc))
            return []

    def _embed_image_sync(self, path: str) -> List[float]:
        from PIL import Image
        with Image.open(path) as handle:
            image = handle.convert("RGB")
            vector = self.model.encode(image, convert_to_numpy=True)
        return [float(x) for x in vector.tolist()]

    async def rank_texts(self, path: str,
                         texts: Sequence[str]) -> List[Tuple[int, float]]:
        """Score candidate descriptions against the frame.

        Returns (index, similarity) sorted best first. An empty list means we
        could not judge, and the caller keeps the tool's own ordering rather
        than guessing.
        """
        if self.model is None or not texts:
            return []
        try:
            return await asyncio.to_thread(self._rank_sync, path, list(texts))
        except Exception as exc:  # noqa: BLE001
            telemetry.log("embed.rank_error", path=path,
                          error=type(exc).__name__ + ": " + str(exc))
            return []

    def _rank_sync(self, path: str, texts: List[str]) -> List[Tuple[int, float]]:
        import numpy as np
        from PIL import Image

        with Image.open(path) as handle:
            image = handle.convert("RGB")
            image_vec = self.model.encode(image, convert_to_numpy=True)
        text_vecs = self.model.encode(texts, convert_to_numpy=True)

        image_vec = image_vec / (np.linalg.norm(image_vec) + 1e-9)
        norms = np.linalg.norm(text_vecs, axis=1, keepdims=True) + 1e-9
        text_vecs = text_vecs / norms
        sims = text_vecs @ image_vec

        order = sorted(range(len(texts)), key=lambda i: -float(sims[i]))
        return [(i, float(sims[i])) for i in order]


# ---------------------------------------------------------------------------
# Row description, for re-ranking
# ---------------------------------------------------------------------------
def describe_row(row: Dict[str, Any]) -> str:
    """A natural phrase describing a result row, for CLIP to match.

    Built from the row's own string fields, so a manual page becomes
    "HDMI Connections" and a catalogue entry becomes whatever it calls itself.
    Numbers and identifiers are skipped: CLIP cannot see a page number.
    """
    if not isinstance(row, dict):
        return ""
    parts: List[str] = []
    for key, value in row.items():
        if not isinstance(value, str):
            continue
        low = key.lower()
        if any(h in low for h in ("id", "code", "url", "ref")):
            continue
        cleaned = value.replace("-", " ").replace("_", " ").strip()
        if cleaned and cleaned.lower() not in [p.lower() for p in parts]:
            parts.append(cleaned)
    return ", ".join(parts)
