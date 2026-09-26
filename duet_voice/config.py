"""Runtime configuration, read once from the environment.

Every knob that changes behaviour is here, with the value used for the reported
benchmark run as its default, so a re-run with no environment set reproduces it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # --- thinker (slow mind): plans and runs tool chains ---------------------
    # Gemini 2.5 Flash-Lite and 2.5 Pro are "no longer available to new users" (the
    # API's own words, Sep 2026), and a re-run uses a new key: DUET declares the
    # current generation (3.5 and later), which any key can reach.
    thinker_model: str = field(default_factory=lambda: _env("DUET_THINKER_MODEL", "gemini-3.7-flash"))
    # minimal | low | medium | high: thinking level (a token budget on Gemini 2.5)
    thinker_thinking: str = field(default_factory=lambda: _env("DUET_THINKER_THINKING", "low"))
    max_tool_steps: int = field(default_factory=lambda: _env_int("DUET_MAX_TOOL_STEPS", 8))
    # fixed sampling seed for every model call (the guide: "pin seeds and versions")
    seed: int = field(default_factory=lambda: _env_int("DUET_SEED", 7))
    # empty: the model family's recommended value (0 on Gemini 2.x; 1.0 on Gemini 3,
    # where Google advises against lowering it: it can cause looping)
    temperature: str = field(default_factory=lambda: _env("DUET_TEMPERATURE", ""))

    # --- talker (fast mind): acknowledgements, progress, never tools -----------
    talker_enabled: bool = field(default_factory=lambda: _env_bool("DUET_TALKER", True))
    talker_model: str = field(default_factory=lambda: _env("DUET_TALKER_MODEL", "gemini-3.5-flash-lite"))
    talker_timeout_s: float = field(default_factory=lambda: _env_float("DUET_TALKER_TIMEOUT", 1.2))

    # --- perception ------------------------------------------------------------
    asr_backend: str = field(default_factory=lambda: _env("DUET_ASR", "whisper"))
    asr_model: str = field(default_factory=lambda: _env("DUET_ASR_MODEL", "large-v3-turbo"))
    asr_device: str = field(default_factory=lambda: _env("DUET_ASR_DEVICE", "auto"))
    # a segment is dropped as non-speech when either test fails
    asr_max_no_speech_prob: float = field(default_factory=lambda: _env_float("DUET_ASR_MAX_NO_SPEECH", 0.6))
    asr_min_avg_logprob: float = field(default_factory=lambda: _env_float("DUET_ASR_MIN_LOGPROB", -1.0))
    vad_activation: float = field(default_factory=lambda: _env_float("DUET_VAD_ACTIVATION", 0.5))
    vad_min_silence_s: float = field(default_factory=lambda: _env_float("DUET_VAD_MIN_SILENCE", 0.35))

    # --- turn taking -------------------------------------------------------------
    # silence after the last word before the turn may end, when the words say done
    endpoint_min_s: float = field(default_factory=lambda: _env_float("DUET_ENDPOINT_MIN", 0.8))
    # longest wait when the words say the user is mid-thought
    endpoint_max_s: float = field(default_factory=lambda: _env_float("DUET_ENDPOINT_MAX", 2.5))
    # quiet time required before a tool may run (the commit hold), and the longer
    # holds used while the user is revising or has left a sentence open
    commit_hold_s: float = field(default_factory=lambda: _env_float("DUET_COMMIT_HOLD", 1.1))
    revising_hold_s: float = field(default_factory=lambda: _env_float("DUET_REVISING_HOLD", 1.8))
    dangling_hold_s: float = field(default_factory=lambda: _env_float("DUET_DANGLING_HOLD", 2.2))

    # --- speech out ---------------------------------------------------------------
    tts_backend: str = field(default_factory=lambda: _env("DUET_TTS", "kokoro"))
    tts_voice: str = field(default_factory=lambda: _env("DUET_TTS_VOICE", "af_heart"))

    # --- benchmark plumbing ---------------------------------------------------------
    # where reproduce.sh clones the benchmark, unless FDB_V3_DIR says otherwise
    fdb_dir: str = field(default_factory=lambda: _env("FDB_V3_DIR", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "third_party", "Full-Duplex-Bench", "v3")))
    tool_log: str = field(default_factory=lambda: _env("FDB_TOOL_LOG", "/tmp/agent_tool_calls.log"))
    heartbeat_log: str = field(default_factory=lambda: _env("FDB_HEARTBEAT_LOG", "/tmp/agent_heartbeat.log"))
    latency_profile: str = field(default_factory=lambda: _env("FDB_LATENCY_PROFILE", "instant"))
    trace_dir: str = field(default_factory=lambda: _env("DUET_TRACE_DIR", ""))


CONFIG = Config()
