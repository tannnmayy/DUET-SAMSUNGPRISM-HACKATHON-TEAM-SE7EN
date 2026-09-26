"""DUET voice agent: the LiveKit entrypoint.

    python -m duet_voice.agent start      # serve jobs from a LiveKit server
    python -m duet_voice.agent dev        # same, with auto-reload
    python -m duet_voice.agent console    # talk to it with your own mic and speakers

LiveKit is the ears and the mouth: audio transport, Silero VAD, speech
recognition, end-of-turn detection, text-to-speech, and barge-in. The brain is
DUET's own, and it has two minds:

  talker   a fast model that says one short, truthful acknowledgement while the
           thinker works (only when there is work to cover, so a greeting or a
           question gets one answer, not two)
  thinker  a reasoning model with the tools, run through the DUET coordinator:
           no tool fires while the user is speaking or before the commit hold
           has passed, no action is ever repeated, failures are handled

One conversation = one LiveKit room = one session with its own coordinator,
toolbox, thinker state and trace. Nothing is shared between rooms.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
from typing import Any, AsyncIterable, Deque, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"))

import numpy as np  # noqa: E402
from livekit import agents, rtc  # noqa: E402
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobExecutorType, JobProcess, llm  # noqa: E402
from livekit.agents.types import FlushSentinel  # noqa: E402
from livekit.agents.voice import ModelSettings  # noqa: E402
# LiveKit plugins must be registered on the main thread, so import them here and
# not inside the per-process warm-up.
from livekit.plugins import google as lk_google  # noqa: E402
from livekit.plugins import silero  # noqa: E402

from . import speech_models, talker  # noqa: E402
from .config import CONFIG  # noqa: E402
from .coordinator import Coordinator, Superseded  # noqa: E402
from .fdb_tools import FdbToolbox  # noqa: E402
from .plugins import KokoroTTS, WhisperSTT  # noqa: E402
from .prompts import THINKER_INSTRUCTIONS  # noqa: E402
from .thinker import Thinker, encode_audio  # noqa: E402

log = logging.getLogger("duet.agent")
logging.getLogger("duet").setLevel(logging.INFO)

# how long the talker waits for the thinker's first decision before speaking anyway
ACK_GRACE_S = float(os.environ.get("DUET_ACK_GRACE", "0.9"))
# speak a progress line when tools keep the user waiting this long
PROGRESS_AFTER_S = float(os.environ.get("DUET_PROGRESS_AFTER", "3.5"))
THINKER_AUDIO = os.environ.get("DUET_THINKER_AUDIO", "0") == "1"


# --- models ----------------------------------------------------------------------------

def build_placeholder_llm():
    """AgentSession needs an LLM to run its STT-LLM-TTS pipeline; DuetAgent.llm_node
    replaces the call itself, so this instance only satisfies the framework."""
    return lk_google.LLM(model=CONFIG.thinker_model, api_key=os.environ.get("GOOGLE_API_KEY") or "unused")


def build_vad():
    return silero.VAD.load(
        min_speech_duration=0.08,
        min_silence_duration=CONFIG.vad_min_silence_s,
        activation_threshold=CONFIG.vad_activation,
    )


def build_turn_detection():
    mode = os.environ.get("DUET_TURN", "eou")
    if mode == "vad":
        return "vad"
    from livekit.agents import inference
    return inference.TurnDetector(version="v1-mini")


# --- trace (our own run log, richer than the benchmark's) -------------------------------

class Trace:
    def __init__(self, room: str) -> None:
        self.path = ""
        if CONFIG.trace_dir:
            os.makedirs(CONFIG.trace_dir, exist_ok=True)
            self.path = os.path.join(CONFIG.trace_dir, room + ".jsonl")

    def __call__(self, kind: str, **data: Any) -> None:
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.time(), "kind": kind, **data}, default=str) + "\n")


# --- the ears' memory: recent input audio, for a thinker that listens --------------------

class AudioTape:
    """The last minute of user audio with wall-clock times, downmixed to 16 kHz."""

    def __init__(self, seconds: float = 60.0) -> None:
        self.frames: Deque[Tuple[float, np.ndarray]] = collections.deque()
        self.seconds = seconds

    def add(self, frame: rtc.AudioFrame) -> None:
        pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        if frame.num_channels > 1:
            pcm = pcm.reshape(-1, frame.num_channels).mean(axis=1)
        if frame.sample_rate != 16000:
            n = max(1, int(round(len(pcm) * 16000 / frame.sample_rate)))
            pcm = np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.float32)
        now = time.time()
        self.frames.append((now, pcm))
        while self.frames and now - self.frames[0][0] > self.seconds:
            self.frames.popleft()

    def since(self, t0: float) -> Optional[np.ndarray]:
        chunks = [pcm for t, pcm in self.frames if t >= t0]
        return np.concatenate(chunks) if chunks else None


# --- the agent ---------------------------------------------------------------------------

class DuetAgent(Agent):
    def __init__(self, trace: Trace, coord: Coordinator, toolbox: FdbToolbox) -> None:
        super().__init__(instructions=THINKER_INSTRUCTIONS)
        self._trace = trace
        self._coord = coord
        self._toolbox = toolbox
        self._thinker = Thinker()
        self._tape = AudioTape()
        self._utterance_started: Optional[float] = None
        self._consumed = 0  # user messages already answered by a completed thinker run

    # ears: keep a copy of the audio for the thinker, pass it on to STT unchanged
    async def stt_node(self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings):
        async def tee():
            async for frame in audio:
                self._tape.add(frame)
                yield frame
        async for event in Agent.default.stt_node(self, tee(), model_settings):
            yield event

    def mark_speech_start(self) -> None:
        if self._utterance_started is None:
            self._utterance_started = time.time() - 0.4

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        self._coord.turn_committed()
        self._trace("user_turn", text=(new_message.text_content or "").strip())

    def _user_messages(self, chat_ctx: llm.ChatContext) -> List[str]:
        return [item.text_content.strip() for item in chat_ctx.items
                if isinstance(item, llm.ChatMessage) and item.role == "user" and item.text_content]

    def _open_request(self, chat_ctx: llm.ChatContext) -> str:
        """Everything the user said since the thinker last finished an answer.

        A turn that was cut short (the user kept talking, or barged in) is re-read
        together with what followed, so a correction always sees what it corrects."""
        return " ".join(self._user_messages(chat_ctx)[self._consumed:]).strip()

    async def llm_node(self, chat_ctx: llm.ChatContext, tools: List[llm.Tool],
                       model_settings: ModelSettings) -> AsyncIterable[Any]:
        text = self._open_request(chat_ctx)
        if not text:
            return
        started = time.time()
        audio = None
        if THINKER_AUDIO and self._utterance_started is not None:
            pcm = self._tape.since(self._utterance_started)
            if pcm is not None and len(pcm) > 1600:
                audio = await asyncio.to_thread(encode_audio, pcm)
        done_before = [r for r in self._coord.history if r.outcome in ("ok", "cached")]
        note = ""
        if done_before and self._thinker.history == []:
            note = "Already done in this conversation (do not repeat): " + "; ".join(
                "%s(%s)" % (r.tool, json.dumps(r.args)) for r in done_before)

        ack_task = asyncio.ensure_future(talker.acknowledgement(text))
        events: asyncio.Queue = asyncio.Queue()

        async def pump() -> None:
            try:
                async for ev in self._thinker.run(text, self._toolbox, audio=audio, note=note):
                    await events.put(ev)
            except Superseded:
                self._trace("superseded")  # the user kept talking; this plan is void
            except Exception as exc:  # never leave the user in silence
                log.exception("thinker crashed")
                await events.put(type("E", (), {"kind": "error", "text": str(exc)})())
            finally:
                await events.put(None)

        worker = asyncio.ensure_future(pump())
        spoke = False
        decided_tools: Optional[bool] = None
        last_speech = started

        async def speak_ack(wait_s: float) -> Optional[str]:
            try:
                ack = await asyncio.wait_for(asyncio.shield(ack_task), timeout=wait_s)
            except asyncio.TimeoutError:
                return None
            except Exception:
                return None
            return ack

        self._trace("think_start", text=text, audio=bool(audio))
        try:
            while True:
                timeout = None
                if not spoke and decided_tools is None:
                    timeout = max(0.0, ACK_GRACE_S - (time.time() - started))
                elif decided_tools and not spoke:
                    timeout = 0.0
                else:
                    timeout = max(0.1, PROGRESS_AFTER_S - (time.time() - last_speech))
                try:
                    ev = await asyncio.wait_for(events.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    ev = "tick"
                if ev == "tick":
                    if not spoke and (decided_tools is None or decided_tools):
                        ack = await speak_ack(0.4 if decided_tools is None else 0.6)
                        self._trace("talker", text=ack, reason="grace" if decided_tools is None else "tools")
                        if ack:
                            yield ack + " "
                            yield FlushSentinel()
                            spoke, last_speech = True, time.time()
                        elif decided_tools:
                            spoke = True  # nothing to say in time; do not try again
                    elif spoke and decided_tools and time.time() - last_speech >= PROGRESS_AFTER_S:
                        yield "Still working on it. "
                        yield FlushSentinel()
                        last_speech = time.time()
                    continue
                if ev is None:
                    break
                kind = ev.kind
                if kind == "decided":
                    decided_tools = ev.tools
                    self._trace("thinker_decided", tools=ev.tools, after_s=round(time.time() - started, 2))
                elif kind == "tool_start":
                    self._trace("tool_start", name=ev.name, args=ev.args)
                elif kind == "tool_done":
                    self._trace("tool_done", name=ev.name, args=ev.args, outcome=ev.outcome)
                elif kind == "error":
                    self._trace("thinker_error", text=ev.text)
                    yield "Sorry, something went wrong on my side. Could you say that once more?"
                    spoke = True
                elif kind == "say":
                    words = (ev.text or "").strip()
                    self._trace("thinker_say", text=words, after_s=round(time.time() - started, 2),
                                usage=getattr(ev, "usage", {}), model=self._thinker.model)
                    if words and "<silent>" not in words:
                        yield words
                        spoke = True
            # this request is answered: the next one starts fresh
            self._consumed = len(self._user_messages(chat_ctx))
            self._utterance_started = None
        finally:
            worker.cancel()
            if not ack_task.done():
                ack_task.cancel()


# --- worker ------------------------------------------------------------------------------

def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = build_vad()
    speech_models.whisper()
    if CONFIG.tts_backend == "kokoro":
        speech_models.kokoro()


# Jobs run as threads of one process, so the speech models are loaded once and
# shared (one copy in GPU memory however many rooms come and go); model calls
# release the GIL and run off the event loop.
server = AgentServer(
    setup_fnc=prewarm,
    job_executor_type=JobExecutorType.THREAD,
    num_idle_processes=int(os.environ.get("DUET_IDLE_PROCESSES", "1")),
    initialize_process_timeout=240.0,
)


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    room = ctx.room.name
    trace = Trace(room)
    coord = Coordinator(commit_hold_s=CONFIG.commit_hold_s, revising_hold_s=CONFIG.revising_hold_s,
                        dangling_hold_s=CONFIG.dangling_hold_s)
    toolbox = FdbToolbox(room, coord)
    agent = DuetAgent(trace, coord, toolbox)

    session = AgentSession(
        vad=ctx.proc.userdata.get("vad") or build_vad(),
        stt=WhisperSTT(),
        llm=build_placeholder_llm(),
        tts=KokoroTTS(),
        turn_handling={
            "turn_detection": build_turn_detection(),
            "endpointing": {"min_delay": CONFIG.endpoint_min_s, "max_delay": CONFIG.endpoint_max_s},
            "preemptive_generation": {"enabled": True},
        },
        user_away_timeout=None,
    )

    @session.on("user_state_changed")
    def _user_state(ev) -> None:
        if ev.new_state == "speaking":
            coord.user_started_speaking()
            agent.mark_speech_start()
        elif ev.old_state == "speaking":
            coord.user_stopped_speaking()
        trace("user_state", state=ev.new_state)

    @session.on("user_input_transcribed")
    def _heard(ev) -> None:
        if ev.is_final:
            coord.heard(ev.transcript)
            trace("heard", text=ev.transcript)

    @session.on("agent_state_changed")
    def _agent_state(ev) -> None:
        trace("agent_state", state=ev.new_state)
        if ev.new_state == "speaking":
            coord.agent_acted()

    @session.on("metrics_collected")
    def _metrics(ev) -> None:
        m = ev.metrics
        kind = getattr(m, "type", "")
        if kind in ("eou_metrics", "eot_inference_metrics", "tts_metrics", "stt_metrics"):
            fields = {k: v for k, v in m.model_dump().items()
                      if isinstance(v, (int, float, str)) and k not in ("timestamp", "request_id", "label")}
            trace("metrics", **fields)

    @session.on("conversation_item_added")
    def _item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) == "assistant":
            trace("agent_said", text=item.text_content)

    trace("session_start", room=room, thinker=CONFIG.thinker_model, thinking=CONFIG.thinker_thinking,
          talker=CONFIG.talker_model, asr=CONFIG.asr_model, tts=CONFIG.tts_backend, thinker_audio=THINKER_AUDIO)
    await session.start(room=ctx.room, agent=agent)


if __name__ == "__main__":
    agents.cli.run_app(server)
