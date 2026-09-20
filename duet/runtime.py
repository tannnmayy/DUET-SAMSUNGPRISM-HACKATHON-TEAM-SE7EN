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
from .perception import (
    FrameReading, Transcript, load_asr, load_embed, load_vision, resolve_media,
)
from .perception.asr import merge as asr_merge
from .perception.asr import value_confidence as asr_value_confidence
from .planner import Plan, Planner
from .planner.rules import (
    PLAN_CLARIFY, PLAN_SPEAK, PLAN_TOOL, has_empty_collection, result_rows,
)
from .state import ConversationState, SRC_AUDIO
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

        # Perception backends, loaded once per process in setup().
        self.asr = None
        self.vision = None
        self.embed = None

        # Audio turn assembly. Chunks are transcribed concurrently and may
        # finish out of order, so parts are keyed by arrival index and the
        # turn is only assembled once every part up to the end marker is in.
        self._audio_parts: Dict[int, Transcript] = {}
        self._audio_next: int = 0
        self._audio_end: Optional[int] = None

        # Frame-ahead cache (M4): what we saw, keyed by frame id, computed
        # when the frame arrived rather than when it was asked about.
        self._frame_readings: Dict[str, FrameReading] = {}
        self._frame_embeddings: Dict[str, List[float]] = {}
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
        # True between asking a question and getting an answer. The reply
        # to "which city?" is a fragment - it carries the missing value
        # but none of the context - so it is planned together with the
        # request that prompted the question.
        self._awaiting_answer = False
        # State-modifying calls held behind the post-correction quiet
        # window. They are not yet in the coordinator's pending list, but
        # they are outstanding work, so scenario_end must wait for them.
        self._deferred = 0

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
        # Loaded here, before the virtual clock starts and under the separate
        # 300s setup budget. A lazy load on the first user turn would be
        # charged to our latency instead (PROTOCOL.md section 5.3).
        self.asr = await load_asr()
        self.vision = await load_vision()
        self.embed = await load_embed()
        telemetry.log("setup.done",
                      asr=getattr(self.asr, "name", "none"),
                      vision=getattr(self.vision, "name", "none"),
                      embed=getattr(self.embed, "name", "none"))

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
        # takes hundreds of milliseconds; the latency clock does not wait, and
        # an acknowledgment that does not pretend to have understood anything
        # is both honest and enough to hold the floor.
        self._speak_floored(lambda: self.emit.filler(self.say.ack_listen()))

        index = self._audio_next
        self._audio_next += 1
        if payload.get("end_of_turn"):
            self._audio_end = index

        telemetry.log("audio", ref=payload.get("audio_ref"), index=index,
                      end_of_turn=payload.get("end_of_turn"))
        self.spawn(self._transcribe(index, payload, self.state.epoch),
                   "asr")

    async def _transcribe(self, index: int, payload: Dict[str, Any],
                          epoch: int) -> None:
        path = resolve_media(payload.get("audio_ref"))
        duration = float(payload.get("duration_ms") or 0.0)
        if path is None or self.asr is None:
            result = Transcript(duration_ms=duration, error="missing_media")
        else:
            result = await self.asr.transcribe(path, duration)

        if self.state.epoch != epoch:
            telemetry.log("asr.discarded", index=index, why="stale_epoch")
            return

        self._audio_parts[index] = result
        telemetry.log("asr.part", index=index, text=result.text,
                      confidence=round(result.confidence, 3),
                      backend=result.backend)
        self._maybe_close_audio_turn(epoch)

    def _maybe_close_audio_turn(self, epoch: int) -> None:
        """Assemble the turn once every chunk up to the end marker is in.

        Chunks are transcribed concurrently and can finish out of order, so
        the turn is only complete when no index below the end marker is
        missing. Acting on a partial turn would mean acting on the abandoned
        half of a self-repair.
        """
        if self._audio_end is None:
            return
        needed = range(self._audio_end + 1)
        if any(i not in self._audio_parts for i in needed):
            return

        parts = [self._audio_parts[i] for i in needed]
        self._audio_parts = {}
        self._audio_next = 0
        self._audio_end = None

        text = " ".join(p.text.strip() for p in parts if p.text.strip()).strip()
        confidence = min((p.confidence for p in parts), default=0.0)
        self.state.utterance_id += 1
        telemetry.log("asr.turn", text=text, confidence=round(confidence, 3))
        self._on_audio_turn(text, confidence, parts, epoch)

    def _on_audio_turn(self, text: str, confidence: float,
                       parts: List[Transcript], epoch: int) -> None:
        """Decide whether we heard well enough to act (M5).

        Acting on a shaky slot is worse than asking. docs/SCORING.md makes
        that explicit, and it is also the accessibility case the theme names:
        a user with a stutter or an unusual accent is failed by an agent that
        confidently does the wrong thing.
        """
        if self.state.epoch != epoch:
            return

        options = self._ambiguous_values(parts)
        if options:
            question = self.say.clarify_choice(options)
            self._awaiting_answer = True
            self._speak_floored(lambda: self.emit.clarify(question))
            telemetry.log("asr.clarify", options=options, why="competing_values")
            return

        merged = asr_merge(parts)
        values = self._effective_values(text)

        if values:
            # Gate on the PRIMARY slot, not the sentence and not every role
            # that happened to be extracted.
            #
            # The sentence score is too coarse: pub_06 must be acted on at
            # utterance confidence 0.40 because "New York" was heard at
            # 0.77/1.00. But taking the minimum across all roles is too harsh
            # in the other direction - a capitalised span incidentally read as
            # a passenger name scored 0.16 there and blocked a search whose
            # destination was perfectly clear.
            #
            # The question this gate answers is "did I understand the
            # request?", which turns on the content value. A shaky passenger
            # name is M2's problem at commit time, where the commitment gate
            # already refuses to book on low-confidence slots.
            primary_role, primary_value = self._primary_value(values)
            slot_confidence = (asr_value_confidence(merged, str(primary_value))
                               if primary_value is not None else None)

            # A backend that does not expose word-level probabilities leaves
            # slot_confidence unknown. Falling through would mean acting on
            # an utterance we barely trust, so the sentence score is the
            # fallback judge rather than no judge at all.
            too_weak = (slot_confidence < config.ASR_SLOT_CONFIDENCE
                        if slot_confidence is not None
                        else confidence < config.ASR_UTTERANCE_CONFIDENCE)
            if too_weak:
                telemetry.log("asr.clarify", why="low_slot_confidence",
                              slot=primary_role,
                              slot_confidence=slot_confidence,
                              utterance_confidence=round(confidence, 3))
                self._awaiting_answer = True
                self._speak_floored(lambda: self.emit.clarify(
                    self.say.clarify_missing(primary_role)))
                return

            effective = slot_confidence if slot_confidence is not None else confidence
            self._on_turn(text, source=SRC_AUDIO, confidence=effective)
            return

        if not text or confidence < config.ASR_UTTERANCE_CONFIDENCE:
            # Keep whatever we did make out as the pending request. It may be
            # mostly noise, but a partially-heard "book a flight to ..." is
            # exactly the context the user's next answer needs - without it,
            # a reply of "I said Boston" names a city and no domain at all.
            if text:
                self.planner.goal_text = text
            self._awaiting_answer = True
            self._speak_floored(lambda: self.emit.clarify(
                self.say.clarify_unheard()))
            telemetry.log("asr.clarify", why="nothing_understood",
                          confidence=round(confidence, 3))
            return

        self._on_turn(text, source=SRC_AUDIO, confidence=confidence)

    def _primary_value(self, values: Dict[str, Any]):
        """The extracted value the request actually turns on.

        Ordered by how much a mishearing would change what we do: a wrong
        place or identifier sends the whole request somewhere else, while a
        wrong date or free-text fragment is recoverable.
        """
        from .tools import ROLE_DATE, ROLE_ID, ROLE_PERSON, ROLE_PLACE
        for role in (ROLE_PLACE, ROLE_ID, ROLE_PERSON, ROLE_DATE):
            if values.get(role) is not None:
                return role, values[role]
        for role, value in values.items():
            if value is not None:
                return role, value
        return "detail", None

    def _effective_values(self, text: str) -> Dict[str, Any]:
        """The values the planner will end up with for this turn.

        Mirrors the planner's two-pass harvest, including the intra-turn
        self-repair, so the confidence gate judges the value we will actually
        act on rather than the one the repair abandoned.
        """
        from .fastpath import REPAIR_CORRECTION, classify_repair, extract_values

        wanted = sorted(self.planner.wanted_roles(
            text, has_frame=self.last_frame is not None))
        found = extract_values(text, expect=wanted)
        repair = classify_repair(text)
        if (repair.kind == REPAIR_CORRECTION and repair.trigger
                and repair.remainder.strip()):
            found.update(extract_values(repair.remainder, expect=wanted))
        return {role: cand.value for role, cand in found.items()}

    def _ambiguous_values(self, parts: List[Transcript]) -> List[str]:
        """Distinct slot values the competing hypotheses disagree about.

        Whole-utterance alternatives are not useful to a person. What they
        need named is the word we could not settle: "did you say Austin or
        Boston?" - so the hypotheses are parsed and the DIFFERING extracted
        values are what we ask about.
        """
        from .fastpath import extract_values
        from .tools import ROLE_PLACE, ROLE_PERSON, ROLE_ID

        for part in parts:
            rivals = part.competing(config.ASR_AMBIGUITY_MARGIN)
            if len(rivals) < 2:
                continue
            for role in (ROLE_PLACE, ROLE_PERSON, ROLE_ID):
                values = []
                for hypothesis in rivals:
                    found = extract_values(hypothesis, expect=[role])
                    candidate = found.get(role)
                    if candidate is not None:
                        label = str(candidate.value).strip()
                        if label and label.lower() not in [v.lower() for v in values]:
                            values.append(label)
                if len(values) >= 2:
                    return values[:3]
        return []

    def _expected_slot(self) -> str:
        """Which value we were most likely missing, for a natural question."""
        ranked = self.registry.rank(self.planner.goal_text or "")
        for spec, score in ranked:
            if score <= 0:
                continue
            for arg in spec.required_args():
                return arg.name
        return "detail"

    def on_video_frame(self, payload: Dict[str, Any]) -> None:
        # M4, perception-ahead scheduling. A frame is context, not a question:
        # the user's NEXT utterance refers to it. So we stay silent and start
        # looking immediately. In pub_07 the frame lands 500ms before the
        # question, which is 500ms of vision we get for free - the alternative
        # is stacking model latency on top of reasoning latency after the
        # question arrives.
        self.last_frame = dict(payload)
        frame_id = str(payload.get("frame_id")
                       or payload.get("image_ref") or "frame")
        telemetry.log("frame", ref=payload.get("image_ref"), frame_id=frame_id)
        self.spawn(self._read_frame(frame_id, dict(payload)), "vision")

    async def _read_frame(self, frame_id: str, payload: Dict[str, Any]) -> None:
        """Describe the frame and embed it, concurrently.

        Deliberately NOT epoch-guarded. A frame is an observation about the
        world, not a plan derived from an intent: if the user changes their
        mind, what is in front of the camera has not changed, and throwing the
        reading away would only mean paying for it again.
        """
        path = resolve_media(payload.get("image_ref"))
        if path is None:
            self._frame_readings[frame_id] = FrameReading(error="missing_media")
            return

        hint = payload.get("device_hint")
        reading = FrameReading(device_hint=hint, error="no_vision_backend")
        embedding: List[float] = []
        try:
            jobs = []
            if self.vision is not None:
                jobs.append(self.vision.read_frame(path, hint))
            if self.embed is not None:
                jobs.append(self.embed.embed_image(path))
            results = await asyncio.gather(*jobs, return_exceptions=True)
            for item in results:
                if isinstance(item, FrameReading):
                    reading = item
                elif isinstance(item, list):
                    embedding = item
        except Exception as exc:  # noqa: BLE001
            telemetry.log("vision.error",
                          error=type(exc).__name__ + ": " + str(exc))

        self._frame_readings[frame_id] = reading
        if embedding:
            self._frame_embeddings[frame_id] = embedding
        telemetry.log("vision.done", frame_id=frame_id, focus=reading.focus,
                      query=reading.query, confidence=round(reading.confidence, 3),
                      embedding_dims=len(embedding))

    async def _ground_visually(self, spec, result: Dict[str, Any],
                              epoch: int) -> None:
        """Choose which returned row the frame actually depicts."""
        from .perception.embed import describe_row

        rows = result_rows(result)
        path = resolve_media((self.last_frame or {}).get("image_ref"))
        chosen = None
        reranked = False
        if path and rows:
            texts = [describe_row(r) for r in rows]
            ranked = await self.embed.rank_texts(path, texts)
            if self.state.epoch != epoch:
                telemetry.log("visual_rerank.discarded", why="stale_epoch")
                return
            if ranked:
                index, score = ranked[0]
                runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
                margin = score - runner_up
                if margin >= config.CLIP_RERANK_MARGIN:
                    chosen = rows[index]
                    reranked = True
                    telemetry.log("visual_rerank.chosen", score=round(score, 4),
                                  margin=round(margin, 4), text=texts[index])
                else:
                    telemetry.log("visual_rerank.declined",
                                  margin=round(margin, 4),
                                  why="image_does_not_discriminate")

        if chosen is None:
            chosen = self.planner.adopt_result(spec, result, now_ms=self.now_ms)

        # Only assert a label we can actually stand behind. Without a
        # confident visual identification we know the manual has a relevant
        # page but not which connector the user means, so we cite the page
        # and stay silent about its title - naming the wrong component is
        # worse than naming none.
        # `chosen` is non-None either way once the fallback runs, so it is
        # not evidence of anything. We have identified the subject only if a
        # vision model read it or the image discriminated decisively.
        identified = bool(self._frame_reading()) or reranked
        body = self.planner.describe(spec, result, chosen,
                                     labels=identified)
        if not body:
            self._send_final(self.say.no_results())
            return
        self._send_final(self.say.report(body))

    def _current_frame_id(self) -> Optional[str]:
        if not self.last_frame:
            return None
        return str(self.last_frame.get("frame_id")
                   or self.last_frame.get("image_ref") or "frame")

    def _frame_reading(self) -> Optional[FrameReading]:
        frame_id = self._current_frame_id()
        if frame_id is None:
            return None
        reading = self._frame_readings.get(frame_id)
        return reading if reading is not None and reading.ok else None

    def _frame_embedding(self) -> Optional[List[float]]:
        frame_id = self._current_frame_id()
        if frame_id is None:
            return None
        return self._frame_embeddings.get(frame_id) or None

    def _ground_in_frame(self, turn: str) -> str:
        """Fold what we saw into the words we plan with.

        "What is this port used for?" names nothing searchable - the answer is
        in the pixels. Appending the vision query both steers tool selection
        and gives a free-text argument something worth searching for, without
        either layer needing to know the tool is a manual lookup.
        """
        reading = self._frame_reading()
        if reading is None or not reading.query.strip():
            return turn
        return (turn + " " + reading.query).strip()

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
            text = self.say.ack_correction(None)
            self._speak_floored(lambda: self.emit.filler(text))
            return

        self.state.bump_epoch("interruption:" + repair.kind, at_ms=self.now_ms)

        if repair.kind == REPAIR_UNDO:
            self.state.undo_last()
            self.coord.cancel_all(now_ms=self.now_ms, reason="undo")
            self.emit.filler(self.say.ack_correction(
                self.state.get("destination") or None))
            self._after_floor(
                lambda: self._replan(self.planner.goal_text), "floored_replan")
            return

        if repair.kind == REPAIR_RETRACTION:
            self.coord.cancel_all(now_ms=self.now_ms, reason="retraction")
            self.state.set_intent("cancelled")
            text = self.say.ack_retraction()
            self._speak_floored(lambda: self.emit.filler(text))
            self.planner.goal_text = ""
            return

        if repair.kind == REPAIR_INTENT_CHANGE:
            self.coord.cancel_all(now_ms=self.now_ms, reason="intent_change")
            self.state.reset_for_intent_change(None)
            text = self.say.ack_intent_change()
            self._speak_floored(lambda: self.emit.filler(text))
            remainder = repair.remainder or text
            self._after_floor(
                lambda: self._on_turn(remainder, announce=False),
                "floored_replan")
            return

        # CORRECTION: harvest the new value, cancel what it invalidated, then
        # acknowledge by name so the user hears that it landed.
        #
        # A correction has two forms. It can re-value a slot ("make it New
        # York"), or it can re-select a row of results we already hold ("make
        # it the 2 PM flight"). The second changes no slot by extraction -
        # times and superlatives are selectors, not values - so without
        # reselect() it produces no state change, nothing is invalidated, and
        # the in-flight booking survives.
        before = {name: slot.value for name, slot in self.state.slots.items()}
        self.planner.harvest(repair.remainder or text, now_ms=self.now_ms,
                             has_frame=self.last_frame is not None)
        self.planner.reselect(repair.remainder or text, now_ms=self.now_ms)
        changed = self.state.changed_since(self.state.epoch)
        self.coord.invalidate(changed, now_ms=self.now_ms, reason="correction")

        new_value, old_value = self._describe_change(before, changed)
        ack = self.say.ack_correction(new_value, old=old_value)
        self._speak_floored(lambda: self.emit.filler(ack))
        goal = self.planner.goal_text or text
        self._after_floor(lambda: self._replan(goal), "floored_replan")

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

        # Visual re-ranking. A question about a frame ("what is THIS port?")
        # produces a text query that matches every comparable row equally, so
        # the tool's own ordering is close to arbitrary and answering from the
        # first row names the wrong thing. The frame is what disambiguates,
        # and the tool has already proposed the candidates - so we let the
        # image choose between them rather than inventing a label vocabulary.
        #
        # Gated on the schema: only tools that advertise they can consume a
        # frame get re-ranked.
        if (spec is not None and spec.wants_visual() and self.last_frame
                and self.embed is not None and result_rows(result)):
            self.spawn(self._ground_visually(spec, result, self.state.epoch),
                       "visual_rerank")
            return

        # Chaining: re-plan the original request now that new values exist. A
        # tool that was unsatisfiable before may be satisfiable now.
        follow_up = self.planner.plan_after_result(
            now_ms=self.now_ms, has_frame=self.last_frame is not None,
            visual_embedding=self._frame_embedding())
        if follow_up.is_tool and follow_up.tool.name != record.api_name:
            if self._issue(follow_up, turn_ended=True):
                return

        if row is None and has_empty_collection(result):
            self._send_final(self.say.no_results())
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
        if self.coord.pending() or self._deferred:
            # Work is still running (or held behind the commitment gate)
            # and its result will produce the answer.
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
    def _on_turn(self, turn: str, *, announce: bool = True,
                 source: str = "text", confidence: float = 1.0) -> None:
        if not turn:
            return
        self._final_sent = False

        # "I said Boston." answers a question we asked; on its own it names no
        # domain at all and would route to whatever tool absorbs free text.
        # Planned together with the request that prompted the question, it is
        # the missing slot of that request.
        if self._awaiting_answer and self.planner.goal_text:
            turn = (self.planner.goal_text + " " + turn).strip()
            telemetry.log("turn.merged_with_pending_question", text=turn)
        self._awaiting_answer = False

        plan = self.planner.plan_turn(
            self._ground_in_frame(turn), now_ms=self.now_ms,
            has_frame=self.last_frame is not None,
            source=source, confidence_scale=confidence,
            visual_embedding=self._frame_embedding())
        self._execute(plan, announce=announce)

    def _replan(self, turn: str) -> None:
        """Re-plan after a correction.

        Uses planner.replan, NOT plan_turn: the corrected value is already in
        state, and re-parsing the superseded utterance would resurrect the
        abandoned one and re-issue the call we just cancelled.
        """
        if not turn:
            return
        plan = self.planner.replan(
            self._ground_in_frame(turn), now_ms=self.now_ms,
            has_frame=self.last_frame is not None,
            visual_embedding=self._frame_embedding())
        self._execute(plan, announce=False)

    def _execute(self, plan: Plan, *, announce: bool = True) -> None:
        if plan.intent:
            self.state.set_intent(plan.intent)

        if plan.kind == PLAN_SPEAK:
            self._speak_floored(
                lambda: self._send_final(
                    self.say.capabilities(self.registry.descriptions())))
            return

        if plan.kind == PLAN_CLARIFY:
            text = self.say.clarify_missing(plan.clarify_slot or "detail")
            self._awaiting_answer = True
            self._speak_floored(lambda: self.emit.clarify(text))
            return

        if plan.is_tool:
            if announce:
                text = self.say.ack_lookup(plan.subject)
                self._speak_floored(lambda: self.emit.filler(text))
            # The tool call itself goes out immediately: it does not stop
            # the latency clock, and earlier is strictly better.
            self._issue(plan, turn_ended=True)

    def _issue(self, plan: Plan, *, turn_ended: bool) -> bool:
        if not plan.is_tool:
            return False

        allowed, reason = self.coord.may_issue(
            plan.tool, plan.args, now_ms=self.now_ms,
            turn_ended=turn_ended, confidence=plan.confidence)
        if not allowed and reason.startswith("too_soon_after_correction"):
            # The gate holds irreversible work for a moment after a correction,
            # because the user may still be mid-correction ("the 2 PM... no,
            # the 6 PM"). Deferring is right; dropping is not - nothing else
            # would ever re-trigger this plan.
            self._deferred += 1
            self.spawn(self._deferred_commit(plan, self.state.epoch),
                       "deferred_commit")
            return True

        call_id = self.coord.issue(
            plan.tool, plan.args, now_ms=self.now_ms,
            depends_on=plan.depends_on, turn_ended=turn_ended,
            confidence=plan.confidence, purpose=plan.subject)
        return call_id is not None

    async def _deferred_commit(self, plan: Plan, epoch: int) -> None:
        """Issue a state-modifying call once the post-correction quiet window
        has passed, unless a further correction superseded it."""
        elapsed = self.now_ms - self.state.last_bump_at_ms
        remaining_ms = max(config.COMMITMENT_QUIET_MS - elapsed, 0.0) + 20.0
        try:
            await asyncio.sleep(remaining_ms / max(self._scale, 0.05) / 1000.0)
            if self.state.epoch != epoch:
                telemetry.log("deferred_commit.superseded", tool=plan.tool.name,
                              born_epoch=epoch, now_epoch=self.state.epoch)
                return
            settled_ms = self.state.last_bump_at_ms + config.COMMITMENT_QUIET_MS + 1.0
            self.coord.issue(plan.tool, plan.args,
                             now_ms=max(settled_ms, self.now_ms),
                             depends_on=plan.depends_on, turn_ended=True,
                             confidence=plan.confidence, purpose=plan.subject)
        finally:
            self._deferred = max(0, self._deferred - 1)

    def _after_floor(self, produce, label: str = "floored") -> None:
        """Run `produce` once the speech floor has elapsed.

        Used for BOTH utterances and the tool calls that answer an
        interruption. Task checkpoints carry `after_ms` windows anchored to
        the event's DECLARED timestamp, so a call emitted a millisecond after
        an early-delivered interruption lands before its own window opens and
        scores "0/1 emitted" - measured at 1 run in 6 on gen_interrupt_024.

        `produce` runs from a task, so the handler stays synchronous and
        Invariant 1 still holds. If a further correction lands inside the
        window the epoch moves and we drop this entirely, rather than acting
        on an intent the user has already replaced.
        """
        epoch = self.state.epoch
        self.spawn(self._floored(produce, epoch), label)

    def _speak_floored(self, produce) -> None:
        self._after_floor(produce, "floored_speech")

    async def _floored(self, produce, epoch: int) -> None:
        await asyncio.sleep(
            config.SPEECH_FLOOR_MS / max(self._scale, 0.05) / 1000.0)
        if self.state.epoch != epoch:
            telemetry.log("floored_speech.superseded", epoch=epoch,
                          now_epoch=self.state.epoch)
            return
        produce()

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
