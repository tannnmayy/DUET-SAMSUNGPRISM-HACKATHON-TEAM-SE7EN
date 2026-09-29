"""Shared helpers for the benchmark tools: data discovery and audio loading."""

from __future__ import annotations

import json
import os
import re
import socket
import struct
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env.local")
FDB_DIR = Path(os.environ.get("FDB_V3_DIR", REPO / "third_party" / "Full-Duplex-Bench" / "v3"))
DATA_DIR = Path(os.environ.get("FDB_DATA_DIR", FDB_DIR / "fdb_v3_data_released"))
_FOLDER_RE = re.compile(r"^(.+)_([0-9a-f]{24})$")


PLACEMENT_VARS = ("CUDA_VISIBLE_DEVICES", "DUET_LLM_GPUS", "DUET_LLM_TP", "DUET_AGENT_GPUS", "DUET_SCORING_GPUS")


def provenance() -> Dict:
    """Which code, machine and GPUs produced a result, so every number can be traced
    back: the git commit (and any uncommitted edits), the host, its GPUs and driver,
    and where each part of the stack was placed."""
    def run(cmd):
        try:
            # rstrip only: `git status --porcelain` lines start with a status column
            return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=20).stdout.rstrip()
        except Exception:
            return ""
    return {
        "git_commit": run(["git", "rev-parse", "HEAD"]),
        "git_branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "git_uncommitted": [l[3:] for l in run(["git", "status", "--porcelain", "--untracked-files=no"]).splitlines()],
        "host": socket.gethostname(),
        "time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "gpus": run(["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,driver_version",
                     "--format=csv,noheader"]).splitlines(),
        "placement": {k: os.environ[k] for k in PLACEMENT_VARS if k in os.environ},
    }


def discover(data_dir: Path = DATA_DIR) -> List[Tuple[str, str, Path, Dict]]:
    """(example_id, speaker_id, folder, metadata) for every example, sorted."""
    out = []
    for folder in sorted(Path(data_dir).iterdir()):
        m = _FOLDER_RE.match(folder.name)
        if not m or not (folder / "input.wav").exists():
            continue
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        out.append((m.group(1), m.group(2), folder, meta))
    return out


def read_wav(path: Path) -> Tuple[np.ndarray, int]:
    """Mono float32 samples and the sample rate, for PCM16/24/32 and float WAVs."""
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file: %s" % path)
    pos, fmt, pcm = 12, None, None
    while pos < len(data) - 8:
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, ch, sr, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, ch, sr, bits)
        elif cid == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    tag, ch, sr, bits = fmt
    if tag == 3:
        x = np.frombuffer(pcm, dtype="<f4" if bits == 32 else "<f8").astype(np.float32)
    elif bits == 16:
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    elif bits == 24:
        b = np.frombuffer(pcm[: len(pcm) // 3 * 3], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v & 0x800000, v - 0x1000000, v).astype(np.float32) / 8388608.0
    else:
        x = np.frombuffer(pcm, dtype="<i4").astype(np.float32) / 2147483648.0
    x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    return x, sr


def resample(x: np.ndarray, sr: int, target: int = 16000) -> np.ndarray:
    """The agent's own band-limited resampler, so offline hearing matches live."""
    assert target == 16000
    from duet_voice.speech_models import to_16k
    return to_16k(x, sr)
