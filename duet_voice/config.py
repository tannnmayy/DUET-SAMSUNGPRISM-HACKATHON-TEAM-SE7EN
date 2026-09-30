"""Runtime configuration, read once from the environment.

Every knob that changes behaviour is here, with the value used for the reported
benchmark run as its default, so a re-run with no environment set reproduces it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields


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
    # --- the language model behind both minds --------------------------------------
    # "local" (default): Qwen3-30B-A3B-Instruct-2507, open weights (Apache-2.0), served
    #   by vLLM on the same GPU as everything else (bench/llm_server.py). No API key,
    #   nothing billed, and it fits Samsung's single 48 GB GPU.
    # "gemini": the Gemini API (GOOGLE_API_KEY), kept as an alternative.
    llm_backend: str = field(default_factory=lambda: _env("DUET_LLM_BACKEND", "local"))
    # the local server's OpenAI-compatible endpoint, and the name it serves the model as
    llm_base_url: str = field(default_factory=lambda: _env("DUET_LLM_BASE_URL", "http://127.0.0.1:18000/v1"))
    llm_model: str = field(default_factory=lambda: _env("DUET_LLM_MODEL", "qwen3-30b-a3b-instruct-2507"))

    # --- thinker (slow mind): plans and runs tool chains ---------------------
    # empty: the backend's default (the local model, or gemini-3.7-flash; Gemini 2.5
    # Flash-Lite and 2.5 Pro are "no longer available to new users" since Sep 2026)
    thinker_model: str = field(default_factory=lambda: _env("DUET_THINKER_MODEL", ""))
    # Gemini only: minimal | low | medium | high (a token budget on Gemini 2.5).
    # Qwen3-30B-A3B-Instruct-2507 is a non-thinking model.
    thinker_thinking: str = field(default_factory=lambda: _env("DUET_THINKER_THINKING", "low"))
    max_tool_steps: int = field(default_factory=lambda: _env_int("DUET_MAX_TOOL_STEPS", 8))
    # fixed sampling seed for every model call (the guide: "pin seeds and versions")
    seed: int = field(default_factory=lambda: _env_int("DUET_SEED", 7))
    # empty: the model authors' recommendation (Qwen3-2507: 0.7 with top-p 0.8 and
    # top-k 20; Gemini 2.x: 0; Gemini 3: 1.0, where Google advises against lowering it)
    temperature: str = field(default_factory=lambda: _env("DUET_TEMPERATURE", ""))

    # --- talker (fast mind): acknowledgements, progress, never tools -----------
    talker_enabled: bool = field(default_factory=lambda: _env_bool("DUET_TALKER", True))
    # empty: the backend's default (the same local model, or gemini-3.5-flash-lite)
    talker_model: str = field(default_factory=lambda: _env("DUET_TALKER_MODEL", ""))
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
    # Preemptive generation lets the thinker start during a pause, before the turn
    # closes. LiveKit's defaults stop it 10 s into a turn and after 3 attempts, so on
    # FDB-v3's long, pause-filled requests (15-40 s, 5-8 pauses) the thinker would
    # otherwise start only after the last pause. Safe: a preemptive plan cannot act
    # before the turn closes, and it is void if the words change.
    preempt_max_speech_s: float = field(default_factory=lambda: _env_float("DUET_PREEMPT_MAX_SPEECH", 120.0))
    preempt_max_retries: int = field(default_factory=lambda: _env_int("DUET_PREEMPT_RETRIES", 20))

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

    # --- use-case extension ---------------------------------------------------------
    # benchmark (default): FDB-v3 tools.
    # appliance: DUET Smart Appliance Care.
    # family: DUET SmartThings Family Care (Watch + Knox + texts).
    use_case: str = field(default_factory=lambda: _env("DUET_USE_CASE", "benchmark").strip().lower())
    # mock (default) uses the in-process household; real needs SMARTTHINGS_TOKEN
    smartthings: str = field(default_factory=lambda: _env("DUET_SMARTTHINGS", "mock").strip().lower())
    smartthings_token: str = field(default_factory=lambda: _env("SMARTTHINGS_TOKEN", "") or _env("SMARTTHINGS_ACCESS_TOKEN", ""))

    def __post_init__(self) -> None:
        local = self.llm_backend == "local"
        if not self.thinker_model:
            object.__setattr__(self, "thinker_model", self.llm_model if local else "gemini-3.7-flash")
        if not self.talker_model:
            object.__setattr__(self, "talker_model", self.llm_model if local else "gemini-3.5-flash-lite")
        if self.use_case not in (
            "benchmark", "appliance", "appliances", "samsung", "care",
            "family", "family_care", "familycare",
        ):
            object.__setattr__(self, "use_case", "benchmark")
        if self.use_case in (
            "appliance", "appliances", "samsung", "care",
            "family", "family_care", "familycare",
        ) and os.environ.get("DUET_MAX_TOOL_STEPS") in (None, ""):
            object.__setattr__(self, "max_tool_steps", 12)


CONFIG = Config()


def reload() -> Config:
    """Re-read the environment into the process-wide CONFIG singleton.

    `from duet_voice.config import CONFIG` keeps the same object, so callers
    that already imported it see the new values. Used by the text chat when
    `--use-case` is passed after import.
    """
    fresh = Config()
    for item in fields(fresh):
        object.__setattr__(CONFIG, item.name, getattr(fresh, item.name))
    return CONFIG
