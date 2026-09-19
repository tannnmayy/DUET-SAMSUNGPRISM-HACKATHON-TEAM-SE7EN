"""The dispatcher: DUET's event loop.

INVARIANT 1: no event handler may occupy the asyncio event loop for longer
than config.DISPATCHER_BUDGET_MS. The agent shares its loop with the harness
that delivers events and executes tools (PROTOCOL.md section 5), so a slow
handler does not merely delay us - it delays event delivery, freezes in-flight
tools, and makes the trace attribute phantom violations to us. The documented
example is a 2s synchronous call turning a 100-point run into 61, recorded as
"re-issued a stale call after the interruption" because the interruption was
still sitting in the queue.

The discipline that follows is absolute:

    handle an event  ->  update state, speak, spawn.  Never await slow work.

Every handler is synchronous. Anything that can take longer than a few
milliseconds - ASR, vision, an LLM plan - is launched with create_task and
writes its result back through the emitter when it finishes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Dict, List, Optional, Set

from . import config, telemetry
from .emitter import Emitter
from .nlg import Phrasebook
from .state import ConversationState, SRC_TEXT


class DuetAgent:
    """The agent the harness constructs, and the same object the live-mic and
    sandbox adapters drive. It knows only the two queues."""

    def __init__(self, in_queue: "asyncio.Queue", out_queue: "asyncio.Queue") -> None:
        # PROTOCOL.md section 5.3: keep __init__ trivial. A fresh instance is
        # constructed per scenario; expensive resources belong in setup() and
        # are cached at module level.
        self.in_q = in_queue
        self.out_q = out_queue

        self.state = ConversationState()
        self.say = Phrasebook()
        self.emit = Emitter(out_queue, self.state, self.say)

        # Tools available in THIS scenario, learned from the manifest event.
        self.tools: Dict[str, Any] = {}

        # Partial-turn text buffer.
        self._buffer: List[str] = []

        # Most recent frame, kept as context: a frame is not a question, the
        # user's next utterance refers to it.
        self.last_frame: Optional[Dict[str, Any]] = None

        # Background work we own and must clean up at shutdown.
        self._tasks: Set[asyncio.Task] = set()

        # Virtual clock, taken from event timestamps rather than wall time so
        # that our reasoning matches what the scorer sees.
        self.now_ms: float = 0.0

        self._ended = False
        self._final_sent = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def setup(self) -> None:
        """Runs before the virtual clock starts, under its own 300s cap.

        Model loading belongs here (Phase 2). Anything left inside run() is
        on the clock, including a lazy load on the first user turn.
        """
        telemetry.reset()
        telemetry.log("setup.begin")

    async def run(self) -> None:
        try:
            while True:
                event = await self.in_q.get()
                self._handle_guarded(event)
        except asyncio.CancelledError:
            raise
        finally:
            # PROTOCOL.md section 5.4: cancel our own tasks so nothing leaks.
            for task in list(self._tasks):
                if not task.done():
                    task.cancel()
            self._tasks.clear()

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------
    def _handle_guarded(self, event: Any) -> None:
        """Route one event. Never raises: a crash inside run() is recorded as
        agent_crash and ends our participation in the scenario, which is the
        most expensive possible outcome. A handler bug should cost one event,
        not the run."""
        etype = "?"
        try:
            if not isinstance(event, dict):
                telemetry.log("dispatch.bad_event", event=repr(event)[:120])
                return
            etype = str(event.get("event_type", "?"))
            ts = event.get("timestamp_ms", self.now_ms)
            try:
                self.now_ms = float(ts)
            except (TypeError, ValueError):
                pass
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            with telemetry.Watchdog(etype):
                handler = self._HANDLERS.get(etype)
                if handler is None:
                    telemetry.log("dispatch.unknown_event", event_type=etype)
                    return
                handler(self, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberate catch-all
            telemetry.log("dispatch.error", event_type=etype,
                          error=type(exc).__name__ + ": " + str(exc))
            if config.STRICT:
                raise

    def spawn(self, coro: "Awaitable[Any]", label: str = "task") -> asyncio.Task:
        """Launch slow work off the dispatcher.

        The returned task is tracked so run()'s finally block can cancel it.
        """
        task = asyncio.create_task(_guard(coro, label))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------------
    # handlers (all synchronous, all under the dispatcher budget)
    # ------------------------------------------------------------------
    def on_tool_manifest(self, payload: Dict[str, Any]) -> None:
        tools = payload.get("tools")
        self.tools = dict(tools) if isinstance(tools, dict) else {}
        telemetry.log("manifest", count=len(self.tools), names=sorted(self.tools))

    def on_user_speech_chunk(self, payload: Dict[str, Any]) -> None:
        text = str(payload.get("text") or "")
        if text:
            self._buffer.append(text)
        if not payload.get("end_of_turn"):
            return
        turn = " ".join(self._buffer).strip()
        self._buffer = []
        self.state.utterance_id += 1
        telemetry.log("turn", text=turn, at_ms=self.now_ms)
        self._on_turn(turn)

    def on_user_audio_chunk(self, payload: Dict[str, Any]) -> None:
        # Acknowledge first, perceive behind the acknowledgment. Transcription
        # takes hundreds of milliseconds; the latency clock does not wait.
        self.emit.filler(self.say.ack_listen())
        telemetry.log("audio", ref=payload.get("audio_ref"),
                      end_of_turn=payload.get("end_of_turn"))
        # Phase 2 attaches the ASR job here.

    def on_video_frame(self, payload: Dict[str, Any]) -> None:
        # A frame is context, not a question. We stay silent and start
        # perceiving immediately (M4): by the time the user asks about it we
        # want the answer already in hand.
        self.last_frame = dict(payload)
        telemetry.log("frame", ref=payload.get("image_ref"),
                      frame_id=payload.get("frame_id"))
        # Phase 2 attaches the vision job here.

    def on_interruption(self, payload: Dict[str, Any]) -> None:
        text = str(payload.get("text") or "")
        self.state.utterance_id += 1
        self.state.bump_epoch("interruption", at_ms=self.now_ms)
        telemetry.log("interruption", text=text, epoch=self.state.epoch,
                      at_ms=self.now_ms)
        # Phase 1 attaches cancellation and re-planning here.
        self.emit.filler(self.say.ack_correction(None))

    def on_tool_result(self, payload: Dict[str, Any]) -> None:
        status = payload.get("status")
        api = str(payload.get("api_name") or "")
        if status == "success":
            self.emit.note_tool_succeeded(api)
        telemetry.log("tool_result", call_id=payload.get("call_id"),
                      api_name=api, status=status)
        # Phase 1 attaches grounding here.

    def on_scenario_end(self, payload: Dict[str, Any]) -> None:
        self._ended = True
        telemetry.log("scenario_end", at_ms=self.now_ms)
        self._ensure_final()

    # ------------------------------------------------------------------
    # turn handling (replaced by the planner in Phase 1)
    # ------------------------------------------------------------------
    def _on_turn(self, turn: str) -> None:
        if not turn:
            return
        self.emit.filler(self.say.ack_lookup(None))

    def _ensure_final(self) -> None:
        """Never end a scenario silent.

        The no-participation gate scores a flat 0 for an agent that neither
        spoke nor called a tool, regardless of how many negative checkpoints
        it vacuously satisfies.
        """
        if self._final_sent:
            return
        descriptions = [
            spec.get("description", "")
            for spec in self.tools.values()
            if isinstance(spec, dict)
        ]
        self._final_sent = self.emit.final(self.say.capabilities(descriptions))

    _HANDLERS = {
        "tool_manifest": on_tool_manifest,
        "user_speech_chunk": on_user_speech_chunk,
        "user_audio_chunk": on_user_audio_chunk,
        "video_frame": on_video_frame,
        "interruption": on_interruption,
        "tool_result": on_tool_result,
        "scenario_end": on_scenario_end,
    }


async def _guard(coro: "Awaitable[Any]", label: str) -> Any:
    """Wrap background work so a failure is logged, never silently swallowed
    and never propagated into the harness."""
    try:
        return await coro
    except asyncio.CancelledError:
        telemetry.log("task.cancelled", label=label)
        raise
    except Exception as exc:  # noqa: BLE001
        telemetry.log("task.error", label=label,
                      error=type(exc).__name__ + ": " + str(exc))
        if config.STRICT:
            raise
        return None
