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
import time
from typing import Any, Awaitable, Dict, List, Optional, Set

from . import config, telemetry
from .coordinator import Coordinator
from .emitter import Emitter
from .fastpath import (
    REPAIR_CORRECTION, REPAIR_INTENT_CHANGE, REPAIR_REFINEMENT,
    REPAIR_RETRACTION, REPAIR_UNDO, classify_repair,
)
from .nlg import Phrasebook
from .planner import Plan, Planner
from .planner.rules import PLAN_CLARIFY, PLAN_SPEAK, PLAN_TOOL
from .state import ConversationState
from .tools import ToolRegistry


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
        self.registry = ToolRegistry()
        self.planner = Planner(self.state, self.registry)
        self.coord = Coordinator(self.state, self.emit, self.registry)

        self._buffer: List[str] = []
        self.last_frame: Optional[Dict[str, Any]] = None
        self._tasks: Set[asyncio.Task] = set()

        # Virtual clock, taken from event timestamps rather than wall time so
        # our reasoning matches what the scorer sees.
        self.now_ms: float = 0.0

        # Observed virtual-to-real ratio. --time-scale is not communicated to
        # the agent, but the tail flush has to sleep in real seconds, so we
        # measure it instead of assuming 1.0.
        self._wall0: float = time.monotonic()
        self._scale: float = 1.0

        self._ended = False
        self._final_sent = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def setup(self) -> None:
        """Runs before the virtual clock starts, under its own 300s cap.

        Model loading belongs here (Phase 2). Anything left inside run() is on
        the clock, including a lazy load on the first user turn.
        """
        telemetry.reset()
        telemetry.log("setup.begin")

    async def run(self) -> None:
        self._wall0 = time.monotonic()
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
        agent_crash and ends our participation, which is the most expensive
        possible outcome. A handler bug should cost one event, not the run."""
        etype = "?"
        try:
            if not isinstance(event, dict):
                telemetry.log("dispatch.bad_event", event=repr(event)[:120])
                return
            etype = str(event.get("event_type", "?"))
            self._observe_clock(event.get("timestamp_ms"))
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

    def _observe_clock(self, timestamp_ms: Any) -> None:
        try:
            self.now_ms = float(timestamp_ms)
        except (TypeError, ValueError):
            return
        wall_ms = (time.monotonic() - self._wall0) * 1000.0
        if wall_ms > 50.0 and self.now_ms > 0.0:
            self._scale = max(0.05, min(64.0, self.now_ms / wall_ms))

    def spawn(self, coro: "Awaitable[Any]", label: str = "task") -> asyncio.Task:
        task = asyncio.create_task(_guard(coro, label))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------------
    # handlers
    # ------------------------------------------------------------------
    def on_tool_manifest(self, payload: Dict[str, Any]) -> None:
        tools = payload.get("tools")
        self.registry = ToolRegistry(tools if isinstance(tools, dict) else {})
        self.planner.set_registry(self.registry)
        self.coord.set_registry(self.registry)
        telemetry.log("manifest", count=len(self.registry), names=self.registry.names())

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
        # A frame is context, not a question (M4): we stay silent and start
        # perceiving immediately, so the answer is in hand before it is asked.
        self.last_frame = dict(payload)
        telemetry.log("frame", ref=payload.get("image_ref"),
                      frame_id=payload.get("frame_id"))
        # Phase 2 attaches the vision job here.

    def on_interruption(self, payload: Dict[str, Any]) -> None:
        text = str(payload.get("text") or "")
        repair = classify_repair(text)
        self.state.utterance_id += 1
        self._final_sent = False

        telemetry.log("interruption", text=text, kind=repair.kind,
                      trigger=repair.trigger, at_ms=self.now_ms)

        if repair.kind == REPAIR_REFINEMENT:
            # Deliberately no epoch bump and no cancellation: the work in
            # flight is still the work the user wants.
            self.planner.harvest(text, now_ms=self.now_ms,
                                 has_frame=self.last_frame is not None)
            self.emit.filler(self.say.ack_correction(None))
            return

        self.state.bump_epoch("interruption:" + repair.kind, at_ms=self.now_ms)

        if repair.kind == REPAIR_UNDO:
            self.state.undo_last()
            self.coord.cancel_all(now_ms=self.now_ms, reason="undo")
            self.emit.filler(self.say.ack_correction(
                self.state.get("destination") or None))
            self._replan(self.planner.goal_text)
            return

        if repair.kind == REPAIR_RETRACTION:
            self.coord.cancel_all(now_ms=self.now_ms, reason="retraction")
            self.state.set_intent("cancelled")
            self.emit.filler(self.say.ack_retraction())
            self.planner.goal_text = ""
            return

        if repair.kind == REPAIR_INTENT_CHANGE:
            self.coord.cancel_all(now_ms=self.now_ms, reason="intent_change")
            self.state.reset_for_intent_change(None)
            self.emit.filler(self.say.ack_intent_change())
            self._on_turn(repair.remainder or text, announce=False)
            return

        # CORRECTION: harvest the new value, cancel what it invalidated, then
        # acknowledge by name so the user hears that it landed.
        before = {name: slot.value for name, slot in self.state.slots.items()}
        self.planner.harvest(repair.remainder or text, now_ms=self.now_ms,
                             has_frame=self.last_frame is not None)
        changed = self.state.changed_since(self.state.epoch)
        self.coord.invalidate(changed, now_ms=self.now_ms, reason="correction")

        new_value, old_value = self._describe_change(before, changed)
        self.emit.filler(self.say.ack_correction(new_value, old=old_value))
        self._replan(self.planner.goal_text or text)

    def _describe_change(self, before: Dict[str, Any],
                         changed: Set[str]) -> tuple:
        """The value to name in the acknowledgment, and what it replaced."""
        for name in sorted(changed):
            slot = self.state.get_slot(name)
            if slot is None or slot.value is None:
                continue
            return slot.value, before.get(name)
        return None, None

    def on_tool_result(self, payload: Dict[str, Any]) -> None:
        record = self.coord.on_result(payload, now_ms=self.now_ms)
        if record is None:
            return  # cancelled or superseded: never ground on it

        spec = self.registry.get(record.api_name)
        result = record.result or {}

        if record.status == "failed":
            retry_id = self.coord.retry(record, now_ms=self.now_ms)
            if retry_id is not None:
                self.emit.filler(self.say.retrying())
                return
            self._send_final(self.say.failed(record.purpose or None))
            return

        row = self.planner.adopt_result(spec, result, now_ms=self.now_ms)

        # Chaining: re-plan the original request now that new values exist. A
        # tool that was unsatisfiable before may be satisfiable now.
        follow_up = self.planner.plan_after_result(
            now_ms=self.now_ms, has_frame=self.last_frame is not None)
        if follow_up.is_tool and follow_up.tool.name != record.api_name:
            if self._issue(follow_up, turn_ended=True):
                return

        body = self.planner.describe(spec, result, row)
        if not body:
            # A successful call with nothing in it - an empty pages list is a
            # success, not an error (docs/TOOLS.md). Say so rather than
            # inventing a citation.
            self._send_final(self.say.failed(None))
            return
        self._send_final(self.say.report(body))

    def on_scenario_end(self, payload: Dict[str, Any]) -> None:
        self._ended = True
        telemetry.log("scenario_end", at_ms=self.now_ms)
        if self.coord.pending():
            # Work is still running and its result will produce the answer.
            # Guard against it never arriving with a scaled timer.
            self.spawn(self._tail_flush(), "tail_flush")
            return
        self._ensure_final()

    async def _tail_flush(self) -> None:
        delay_s = (config.TAIL_FLUSH_MS / max(self._scale, 0.05)) / 1000.0
        await asyncio.sleep(delay_s)
        if not self._final_sent:
            telemetry.log("tail_flush.forced", pending=len(self.coord.pending()))
            self._ensure_final()

    # ------------------------------------------------------------------
    # turn handling
    # ------------------------------------------------------------------
    def _on_turn(self, turn: str, *, announce: bool = True) -> None:
        if not turn:
            return
        self._final_sent = False
        plan = self.planner.plan_turn(turn, now_ms=self.now_ms,
                                      has_frame=self.last_frame is not None)
        self._execute(plan, announce=announce)

    def _replan(self, turn: str) -> None:
        """Re-plan after a correction.

        Uses planner.replan, NOT plan_turn: the corrected value is already in
        state, and re-parsing the superseded utterance would resurrect the
        abandoned one and re-issue the call we just cancelled.
        """
        if not turn:
            return
        plan = self.planner.replan(turn, now_ms=self.now_ms,
                                   has_frame=self.last_frame is not None)
        self._execute(plan, announce=False)

    def _execute(self, plan: Plan, *, announce: bool = True) -> None:
        if plan.intent:
            self.state.set_intent(plan.intent)

        if plan.kind == PLAN_SPEAK:
            self._send_final(self.say.capabilities(self.registry.descriptions()))
            return

        if plan.kind == PLAN_CLARIFY:
            self.emit.clarify(self.say.clarify_missing(
                plan.clarify_slot or "detail"))
            return

        if plan.is_tool:
            if announce:
                self.emit.filler(self.say.ack_lookup(plan.subject))
            self._issue(plan, turn_ended=True)

    def _issue(self, plan: Plan, *, turn_ended: bool) -> bool:
        if not plan.is_tool:
            return False
        call_id = self.coord.issue(
            plan.tool, plan.args, now_ms=self.now_ms,
            depends_on=plan.depends_on, turn_ended=turn_ended,
            confidence=plan.confidence, purpose=plan.subject)
        return call_id is not None

    def _send_final(self, text: str) -> None:
        if self.emit.final(text):
            self._final_sent = True

    def _ensure_final(self) -> None:
        """Never end a scenario silent.

        The no-participation gate scores a flat 0 for an agent that neither
        spoke nor called a tool, whatever the negative checkpoints say.
        """
        if self._final_sent:
            return
        if self.state.intent == "cancelled":
            self._send_final(self.say.ack_retraction())
            return
        self._send_final(self.say.capabilities(self.registry.descriptions()))

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
