"""Local speech models: faster-whisper for the ears, Kokoro for the voice.

Both load once per worker process and run in a thread, so the event loop that
carries the conversation never waits on them.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .config import CONFIG

log = logging.getLogger("duet.speech")

_lock = threading.Lock()
_asr_lock = threading.Lock()   # one transcription at a time on the shared model
_tts_lock = threading.Lock()   # the Kokoro pipeline is not safe to share concurrently
_whisper = None
_kokoro = None


_cuda_ready = False


def _cuda_dll_paths() -> None:
    """Make pip-installed CUDA libraries visible to CTranslate2 (faster-whisper).

    CTranslate2 loads cuBLAS 12 and cuDNN 9 by name at run time. They arrive as
    pip packages (dependencies of torch) under site-packages, where the loader
    does not look. On Linux they are loaded here by absolute path with
    RTLD_GLOBAL, so CTranslate2's later lookup finds them resident; on Windows
    their folders join the DLL search path. Best effort: a trial transcription
    at load time is what proves the GPU works."""
    global _cuda_ready
    if _cuda_ready:
        return
    _cuda_ready = True
    import glob
    roots = [p for p in sys.path if p and os.path.isdir(os.path.join(p, "nvidia"))]
    if os.name == "nt":
        dirs = []
        try:
            import torch  # noqa: F401
            dirs.append(os.path.join(os.path.dirname(sys.modules["torch"].__file__), "lib"))
        except Exception:
            pass
        for root in roots:
            dirs.extend(glob.glob(os.path.join(root, "nvidia", "*", "bin")))
        for d in dirs:
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
        return
    import ctypes
    pending = []
    for root in roots:
        for name in ("cuda_runtime", "cublas", "cudnn", "cuda_nvrtc"):
            pending.extend(sorted(glob.glob(os.path.join(root, "nvidia", name, "lib", "*.so*"))))
    pending = list(dict.fromkeys(pending))
    for _ in range(4):  # cuDNN's sub-libraries do not declare a loadable order
        failed = []
        for path in pending:
            try:
                ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                failed.append(path)
        if len(failed) == len(pending):
            break
        pending = failed


def _pick_device() -> str:
    if CONFIG.asr_device != "auto":
        return CONFIG.asr_device
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


# The exact Hugging Face snapshots of the reported runs, so a re-run downloads the
# same weights even if a repository is updated later.
MODEL_REVISIONS = {
    "large-v3-turbo": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",  # mobiuslabsgmbh/faster-whisper-large-v3-turbo
}
KOKORO_REVISION = "f3ff3571791e39611d31c381e3a41a3af07b4987"        # hexgrad/Kokoro-82M


def whisper():
    global _whisper
    with _lock:
        if _whisper is None:
            _cuda_dll_paths()
            from faster_whisper import WhisperModel
            device = _pick_device()
            model = CONFIG.asr_model if device == "cuda" else os.environ.get("DUET_ASR_CPU_MODEL", "small.en")
            t = time.time()
            _whisper = WhisperModel(model, device=device, revision=MODEL_REVISIONS.get(model),
                                    compute_type="float16" if device == "cuda" else "int8")
            # one short warm-up so the first real turn is not slow
            _whisper.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1)
            log.info("whisper %s on %s ready in %.1f s", model, device, time.time() - t)
    return _whisper


def kokoro():
    global _kokoro
    with _lock:
        if _kokoro is None:
            import torch
            from huggingface_hub import hf_hub_download
            from kokoro import KModel, KPipeline
            t = time.time()
            repo = "hexgrad/Kokoro-82M"
            fetch = lambda name: hf_hub_download(repo_id=repo, filename=name, revision=KOKORO_REVISION)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = KModel(repo_id=repo, config=fetch("config.json"),
                           model=fetch(KModel.MODEL_NAMES[repo])).to(device).eval()
            _kokoro = KPipeline(lang_code="a", repo_id=repo, model=model)
            voice = CONFIG.tts_voice
            _kokoro.voices[voice] = torch.load(fetch("voices/%s.pt" % voice), weights_only=True)
            list(_kokoro("Ready.", voice=voice))  # warm-up
            log.info("kokoro ready in %.1f s", time.time() - t)
    return _kokoro


# The agent's own vocabulary: tool domains, not benchmark items. Whisper
# spells domain words better when it has seen them in the prompt.
ASR_PROMPT = ("Flight search and booking, passport and driver's license numbers, card "
              "benefits, currency exchange, autopay, apartments, commute, search filters, "
              "order tracking, product search, shopping cart.")


@dataclass
class Transcript:
    text: str
    confidence: float
    no_speech_prob: float
    dropped: bool


def transcribe(pcm16k: np.ndarray) -> Transcript:
    """One VAD segment in, one cleaned transcript out.

    Segments the model itself believes are not speech (high no-speech
    probability together with low log-probability) are dropped: in FDB-v3 the
    30 s of room noise after every request is exactly where Whisper invents
    sentences, and an invented sentence can trigger an extra tool call."""
    model = whisper()
    with _asr_lock:
        return _transcribe_locked(model, pcm16k)


def _transcribe_locked(model, pcm16k: np.ndarray) -> "Transcript":
    segments, _ = model.transcribe(
        pcm16k, language="en", beam_size=5, vad_filter=False,
        condition_on_previous_text=False, initial_prompt=ASR_PROMPT,
        temperature=0.0,  # greedy/beam only: sampled fallbacks would make re-runs differ
    )
    kept: List[str] = []
    logprobs: List[float] = []
    worst_no_speech = 0.0
    dropped_any = False
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if (seg.no_speech_prob > CONFIG.asr_max_no_speech_prob
                and seg.avg_logprob < CONFIG.asr_min_avg_logprob + 0.5) \
                or seg.avg_logprob < CONFIG.asr_min_avg_logprob - 0.5 \
                or seg.compression_ratio > 2.6:
            dropped_any = True
            continue
        kept.append(text)
        logprobs.append(seg.avg_logprob)
        worst_no_speech = max(worst_no_speech, seg.no_speech_prob)
    text = " ".join(kept).strip()
    confidence = math.exp(sum(logprobs) / len(logprobs)) if logprobs else 0.0
    return Transcript(text, confidence, worst_no_speech, dropped_any and not text)


def synthesize(text: str) -> np.ndarray:
    """Text to 24 kHz float32 audio."""
    pipe = kokoro()
    with _tts_lock:
        parts = [np.asarray(audio, dtype=np.float32) for _, _, audio in pipe(text, voice=CONFIG.tts_voice)]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
