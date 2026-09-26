"""Every model DUET loads, pinned to an exact revision.

Scoring happens AFTER the submission deadline, on a machine that downloads
our checkpoints from Hugging Face at that time (organizer clarification 3:
huggingface.co is reachable and download time is not charged to setup()). A
model repository can change in between - and one of ours already has: the
repository faster-whisper's built-in "large-v3-turbo" alias points at has
been renamed and now only answers with a redirect. Pinning a repository id
AND a commit hash means the weights we tested are the weights we are graded
with.

Each entry can still be overridden from the environment for experiments:
DUET_<ROLE>_MODEL replaces the repository (its revision then defaults to the
branch head unless DUET_<ROLE>_REVISION is also set).

tools/prefetch.py downloads exactly this table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Checkpoint:
    repo: str
    revision: Optional[str]
    note: str = ""


_PINNED: Dict[str, Checkpoint] = {
    # Speech on GPU: the distilled large-v3 - near large-v3 accuracy at a
    # fraction of the decode cost. Canonical repository after the rename.
    "ASR_GPU": Checkpoint("dropbox-dash/faster-whisper-large-v3-turbo",
                          "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
                          "CTranslate2 float16, ~1.6 GB"),
    # Speech on CPU: small enough to finish inside the conversation. The
    # M5 thresholds in config.py were measured with this model.
    "ASR_CPU": Checkpoint("Systran/faster-whisper-base",
                          "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
                          "CTranslate2 int8, ~150 MB"),
    # Frame embeddings (hybrid-search argument) and visual re-ranking.
    "CLIP": Checkpoint("sentence-transformers/clip-ViT-B-32",
                       "327ab6726d33c0e22f920c83f2ff9e4bd38ca37f",
                       "~600 MB"),
    # Vision-language model for reading what is in a frame. GPU only.
    "VLM": Checkpoint("Qwen/Qwen2.5-VL-3B-Instruct",
                      "66285546d2b821cf421d4f5eb2576359d3770cd3",
                      "float16, ~7.5 GB"),
}

# Older environment variable names, kept so existing commands still work.
_LEGACY_ENV = {"ASR_GPU": "DUET_ASR_MODEL", "ASR_CPU": "DUET_ASR_MODEL_CPU",
               "CLIP": "DUET_CLIP_MODEL", "VLM": "DUET_VLM_MODEL"}


def get(role: str) -> Checkpoint:
    """The checkpoint for a role, honouring environment overrides."""
    pinned = _PINNED[role]
    override = (os.environ.get("DUET_" + role + "_MODEL")
                or os.environ.get(_LEGACY_ENV.get(role, ""), ""))
    if override and override != pinned.repo:
        return Checkpoint(override, os.environ.get("DUET_" + role + "_REVISION") or None,
                          "environment override")
    return pinned


def all_pinned() -> Dict[str, Checkpoint]:
    return dict(_PINNED)
