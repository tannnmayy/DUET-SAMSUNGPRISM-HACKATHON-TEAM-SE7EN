"""LiveKit STT and TTS adapters for the local speech models."""

from __future__ import annotations

import asyncio
import logging

import numpy as np
from livekit import rtc
from livekit.agents import APIConnectOptions, stt, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

from . import speech_models
from .config import CONFIG

log = logging.getLogger("duet.plugins")


def _to_16k_mono(buffer: utils.AudioBuffer) -> np.ndarray:
    frame = rtc.combine_audio_frames(buffer)
    pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
    if frame.num_channels > 1:
        pcm = pcm.reshape(-1, frame.num_channels).mean(axis=1)
    if frame.sample_rate != 16000 and len(pcm):
        # polyphase-free linear resampling is enough for 48k/24k -> 16k speech
        n_out = int(round(len(pcm) * 16000 / frame.sample_rate))
        pcm = np.interp(np.linspace(0, len(pcm) - 1, n_out), np.arange(len(pcm)), pcm).astype(np.float32)
    return pcm


class WhisperSTT(stt.STT):
    """faster-whisper, one VAD segment at a time, with the non-speech filter."""

    def __init__(self) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))

    @property
    def model(self) -> str:
        return CONFIG.asr_model

    @property
    def provider(self) -> str:
        return "faster-whisper"

    def prewarm(self) -> None:
        speech_models.whisper()

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        pcm = _to_16k_mono(buffer)
        result = await asyncio.to_thread(speech_models.transcribe, pcm)
        if result.dropped:
            log.info("dropped a non-speech segment (%.1f s)", len(pcm) / 16000)
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(text=result.text, language="en",
                                         confidence=result.confidence)],
        )


class KokoroTTS(tts.TTS):
    """Kokoro-82M on the local GPU (or CPU): about 0.1 s per sentence on a GPU."""

    SAMPLE_RATE = 24000

    def __init__(self) -> None:
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                         sample_rate=self.SAMPLE_RATE, num_channels=1)

    @property
    def model(self) -> str:
        return "kokoro-82m"

    @property
    def provider(self) -> str:
        return "kokoro"

    def prewarm(self) -> None:
        speech_models.kokoro()

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> "KokoroStream":
        return KokoroStream(tts=self, input_text=text, conn_options=conn_options)


class KokoroStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        audio = await asyncio.to_thread(speech_models.synthesize, self.input_text)
        pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        output_emitter.initialize(request_id=utils.shortuuid(), sample_rate=KokoroTTS.SAMPLE_RATE,
                                  num_channels=1, mime_type="audio/pcm")
        output_emitter.push(pcm16)
        output_emitter.flush()
