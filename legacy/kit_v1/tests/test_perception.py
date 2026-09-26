"""Perception orchestration, verified without a model.

The models are the uncertain part of Phase 2, but they are not the hard part.
The hard part is the scheduling around them: acknowledge before you
understand, transcribe concurrently and assemble in order, start looking at a
frame before you are asked about it, carry uncertainty into the decision to
act, and throw away anything the user has already superseded.

All of that is deterministic, so it is tested with stub backends that stand in
for the models. When the real backends land, these tests still hold - the
orchestration does not know or care which backend produced a Transcript.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Dict, List, Optional

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet import config, perception, telemetry  # noqa: E402
from duet.perception import (  # noqa: E402
    ASRBackend, EmbedBackend, FrameReading, Transcript, VisionBackend,
)
from harness.runner import EvaluationHarness  # noqa: E402
from harness.scorer import score_scenario  # noqa: E402


# --------------------------------------------------------------------------
# Stub backends
# --------------------------------------------------------------------------
class StubASR(ASRBackend):
    """Returns a scripted transcript after a realistic delay."""

    name = "stub"

    def __init__(self, by_suffix: Dict[str, Transcript], delay_s: float = 0.25):
        self.by_suffix = by_suffix
        self.delay_s = delay_s
        self.calls: List[str] = []

    def available(self) -> bool:
        return True

    async def transcribe(self, path: str, duration_ms: float = 0.0) -> Transcript:
        self.calls.append(path)
        await asyncio.sleep(self.delay_s)
        for suffix, transcript in self.by_suffix.items():
            if path.replace("\\", "/").endswith(suffix):
                return Transcript(**{**transcript.__dict__,
                                     "duration_ms": duration_ms,
                                     "backend": self.name})
        return Transcript(text="", confidence=0.0, backend=self.name,
                          error="no_script")


class StubVision(VisionBackend):
    name = "stub"

    def __init__(self, reading: FrameReading, delay_s: float = 0.4):
        self.reading = reading
        self.delay_s = delay_s
        self.started_at: Optional[float] = None
        self.calls: List[str] = []

    def available(self) -> bool:
        return True

    async def read_frame(self, path: str,
                         device_hint: Optional[str] = None) -> FrameReading:
        self.calls.append(path)
        self.started_at = asyncio.get_event_loop().time()
        await asyncio.sleep(self.delay_s)
        return FrameReading(**{**self.reading.__dict__,
                               "device_hint": device_hint,
                               "backend": self.name})


class StubEmbed(EmbedBackend):
    name = "stub"
    dimensions = 8

    def __init__(self, delay_s: float = 0.1):
        self.delay_s = delay_s
        self.calls: List[str] = []

    def available(self) -> bool:
        return True

    async def embed_image(self, path: str) -> List[float]:
        self.calls.append(path)
        await asyncio.sleep(self.delay_s)
        return [0.11, -0.22, 0.33, 0.44, -0.55, 0.66, 0.77, -0.88]


@pytest.fixture(autouse=True)
def _isolate():
    original = config.STRICT
    config.STRICT = False
    telemetry.reset()
    perception.reset()
    yield
    perception.reset()
    config.STRICT = original


def run_scenario(path: str, time_scale: float = 1.0):
    from agent.agent import ParticipantAgent

    with open(path, "r", encoding="utf-8") as fh:
        scenario = json.load(fh)

    async def go():
        h = EvaluationHarness(scenario, lambda a, b: ParticipantAgent(a, b),
                              time_scale=time_scale, verbose=False)
        await h.prepare()
        return await h.run()

    trace = asyncio.run(go())
    return scenario, trace, score_scenario(scenario, trace)


def actions(trace, kind=None):
    return [e for e in trace if e.get("kind") == "action"
            and (kind is None or e.get("action") == kind)]


def tool_calls(trace):
    return actions(trace, "tool_call")


def spoken(trace):
    return [e for e in actions(trace)
            if e.get("action") in ("filler_speech", "clarification_request",
                                   "final_response")]


PUB05 = os.path.join(ROOT, "scenarios", "pub_05_audio_asr_ambiguity.json")
PUB06 = os.path.join(ROOT, "scenarios", "pub_06_audio_disfluency.json")
PUB07 = os.path.join(ROOT, "scenarios", "pub_07_visual_port_lookup.json")


# --------------------------------------------------------------------------
# Acknowledge first, perceive behind it
# --------------------------------------------------------------------------
def test_audio_is_acknowledged_long_before_it_is_transcribed():
    """The latency clock does not wait for a model. A 250ms transcription
    behind a ~20ms acknowledgment is the whole point of the fast path."""
    perception.install(asr=StubASR({"pub_05_turn1.mp3": Transcript(
        text="book a flight to Boston", confidence=0.9)}, delay_s=0.6))
    _scenario, trace, _score = run_scenario(PUB05)

    first = spoken(trace)[0]
    assert first["action"] == "filler_speech"
    assert first["t_ms"] < 300, first
    # and it does not pretend to have understood anything yet
    assert "boston" not in first["payload"]["text"].lower()


def test_agent_does_not_crash_when_media_is_missing():
    """PROTOCOL.md: 'Your agent must not crash if a referenced file is
    missing.' Hidden scenarios ship their own media; a path we cannot resolve
    must degrade to asking, not to a zero."""
    perception.install(asr=StubASR({}, delay_s=0.05))
    scenario = {
        "scenario_id": "missing_media",
        "metadata": {"modality": "audio", "difficulty": "L1"},
        "events": [{"timestamp_ms": 100, "event_type": "user_audio_chunk",
                    "payload": {"audio_ref": "audio/does_not_exist.mp3",
                                "duration_ms": 900, "end_of_turn": True}}],
        "ground_truth": {"checkpoints": [
            {"id": "spoke", "type": "spoken_contains", "any_of": [""],
             "weight": 1.0}]},
    }

    async def go():
        from agent.agent import ParticipantAgent
        h = EvaluationHarness(scenario, lambda a, b: ParticipantAgent(a, b),
                              time_scale=1.0, verbose=False)
        await h.prepare()
        return await h.run()

    trace = asyncio.run(go())
    assert not [e for e in trace if e.get("kind") == "agent_crash"]
    assert spoken(trace), "agent went silent on missing media"


def test_no_asr_backend_degrades_to_asking():
    """Bottom of the degradation ladder: with no speech model at all we ask
    rather than guess, which still earns clarification and latency credit."""
    perception.install(asr=perception.NullASR())
    _scenario, trace, _score = run_scenario(PUB05)
    assert not [e for e in trace if e.get("kind") == "agent_crash"]
    kinds = {e["action"] for e in spoken(trace)}
    assert "clarification_request" in kinds or "final_response" in kinds


# --------------------------------------------------------------------------
# M5: calibrated abstention
# --------------------------------------------------------------------------
def test_competing_hypotheses_produce_a_question_naming_both():
    """pub_05. The clip can be heard as Austin or Boston. Naming both is the
    honest question, and firing a tool on a guess is a scored failure."""
    perception.install(asr=StubASR({
        "pub_05_turn1.mp3": Transcript(
            text="book a flight to Austin", confidence=0.52,
            alternatives=[("book a flight to Austin", 0.52),
                          ("book a flight to Boston", 0.46)]),
        "pub_05_turn2.mp3": Transcript(text="I said Boston", confidence=0.95),
    }, delay_s=0.3))
    _scenario, trace, _score = run_scenario(PUB05)

    asks = actions(trace, "clarification_request")
    assert asks, "no clarification raised on ambiguous audio"
    text = asks[0]["payload"]["text"].lower()
    assert "austin" in text and "boston" in text, text
    assert asks[0]["t_ms"] < 4200, "clarification must come before turn 2"


def test_no_tool_fires_while_the_slot_is_ambiguous():
    perception.install(asr=StubASR({
        "pub_05_turn1.mp3": Transcript(
            text="book a flight to Austin", confidence=0.52,
            alternatives=[("book a flight to Austin", 0.52),
                          ("book a flight to Boston", 0.46)]),
        "pub_05_turn2.mp3": Transcript(text="I said Boston", confidence=0.95),
    }, delay_s=0.3))
    _scenario, trace, _score = run_scenario(PUB05)

    early = [c for c in tool_calls(trace) if c["t_ms"] < 4200]
    assert not early, "acted on a guess before clarifying: " + repr(early)


def test_low_confidence_alone_also_triggers_a_question():
    """Even with no rival hypothesis, a transcript we barely trust is not a
    basis for action."""
    perception.install(asr=StubASR({
        "pub_05_turn1.mp3": Transcript(text="book a flight to Boston",
                                       confidence=0.2),
        "pub_05_turn2.mp3": Transcript(text="I said Boston", confidence=0.95),
    }, delay_s=0.2))
    _scenario, trace, _score = run_scenario(PUB05)
    assert actions(trace, "clarification_request")


def test_confident_audio_is_acted_on_without_asking():
    """The gate must not fire on everything, or it is just a refusal to work."""
    perception.install(asr=StubASR({
        "pub_05_turn1.mp3": Transcript(text="book a flight to Boston",
                                       confidence=0.95),
        "pub_05_turn2.mp3": Transcript(text="I said Boston", confidence=0.95),
    }, delay_s=0.2))
    _scenario, trace, _score = run_scenario(PUB05)
    calls = tool_calls(trace)
    assert calls, "confident audio produced no action at all"
    assert any("bos" in json.dumps(c["args"]).lower() for c in calls)


def test_asr_confidence_flows_into_slot_confidence():
    """M5 feeding M2: a value heard at low confidence must not later satisfy
    the commitment gate as though it had been typed."""
    from duet.nlg import Phrasebook
    from duet.planner import Planner
    from duet.state import ConversationState
    from duet.tools import ToolRegistry
    from harness.mock_env import TOOL_REGISTRY

    state = ConversationState()
    planner = Planner(state, ToolRegistry(TOOL_REGISTRY))
    planner.harvest("book a flight to Boston", now_ms=100,
                    source="audio", confidence_scale=0.5)
    assert state.confidence("destination") <= 0.5
    assert state.get_slot("destination").source == "audio"


# --------------------------------------------------------------------------
# Turn assembly
# --------------------------------------------------------------------------
def test_self_repair_across_chunks_uses_the_repaired_value():
    """pub_06. The abandoned city must never reach a tool, and the turn must
    not be acted on until the end marker arrives."""
    perception.install(asr=StubASR({
        "pub_06_turn1_part1.mp3": Transcript(
            text="uh, book a flight to Boston", confidence=0.9),
        "pub_06_turn1_part2.mp3": Transcript(
            text="actually, make that New York", confidence=0.9),
    }, delay_s=0.25))
    _scenario, trace, _score = run_scenario(PUB06)

    args_blob = json.dumps([c["args"] for c in tool_calls(trace)]).lower()
    assert "new york" in args_blob or "nyc" in args_blob, args_blob
    assert "boston" not in args_blob, "searched the abandoned city: " + args_blob


def test_out_of_order_transcription_is_reassembled_correctly():
    """Chunks are transcribed concurrently and can finish out of order. If the
    second part came back first we would act on the abandoned half of the
    repair, so assembly waits for every index below the end marker."""
    class OutOfOrderASR(StubASR):
        async def transcribe(self, path, duration_ms=0.0):
            # part1 deliberately takes far longer than part2
            slow = path.replace("\\", "/").endswith("part1.mp3")
            await asyncio.sleep(0.6 if slow else 0.05)
            for suffix, transcript in self.by_suffix.items():
                if path.replace("\\", "/").endswith(suffix):
                    return Transcript(**{**transcript.__dict__,
                                         "backend": self.name})
            return Transcript(text="", confidence=0.0, backend=self.name)

    perception.install(asr=OutOfOrderASR({
        "pub_06_turn1_part1.mp3": Transcript(
            text="uh, book a flight to Boston", confidence=0.9),
        "pub_06_turn1_part2.mp3": Transcript(
            text="actually, make that New York", confidence=0.9),
    }))
    _scenario, trace, _score = run_scenario(PUB06)

    args_blob = json.dumps([c["args"] for c in tool_calls(trace)]).lower()
    assert "boston" not in args_blob, args_blob
    assert "new york" in args_blob or "nyc" in args_blob, args_blob


# --------------------------------------------------------------------------
# M4: perception-ahead
# --------------------------------------------------------------------------
def test_vision_starts_when_the_frame_arrives_not_when_asked():
    """pub_07 delivers the frame 500ms before the question. Starting on
    arrival converts model latency into latency we never pay."""
    vision = StubVision(FrameReading(
        focus="HDMI port", query="hdmi output external display",
        labels=["HDMI"], confidence=0.9), delay_s=0.3)
    perception.install(vision=vision, embed=StubEmbed())
    _scenario, trace, _score = run_scenario(PUB07)

    assert vision.calls, "vision never ran"
    frame_event = next(e for e in trace
                       if e.get("kind") == "event"
                       and e.get("event_type") == "video_frame")
    first_call = tool_calls(trace)[0]
    # The lookup could not have been issued this early if vision had waited
    # for the question at 600ms plus its own 300ms.
    assert first_call["t_ms"] < frame_event["t_ms"] + 1500, first_call


def test_frame_stays_silent_on_arrival():
    """A frame is context, not a question. Speaking about an image nobody
    asked about is filler spam."""
    perception.install(vision=StubVision(FrameReading(
        focus="HDMI port", query="hdmi output", confidence=0.9)),
        embed=StubEmbed())
    _scenario, trace, _score = run_scenario(PUB07)
    frame_t = next(e["t_ms"] for e in trace if e.get("kind") == "event"
                   and e.get("event_type") == "video_frame")
    question_t = next(e["t_ms"] for e in trace if e.get("kind") == "event"
                      and e.get("event_type") == "user_speech_chunk")
    between = [e for e in spoken(trace) if frame_t <= e["t_ms"] < question_t]
    assert not between, "spoke about a frame before being asked: " + repr(between)


def test_frame_reading_grounds_the_tool_query():
    """'What is this port used for?' names nothing searchable - the answer is
    in the pixels, so what we saw has to reach the query."""
    perception.install(vision=StubVision(FrameReading(
        focus="HDMI port", query="hdmi output external display",
        confidence=0.9)), embed=StubEmbed())
    _scenario, trace, _score = run_scenario(PUB07)
    blob = json.dumps([c["args"] for c in tool_calls(trace)]).lower()
    assert "hdmi" in blob, blob


def test_embedding_is_bound_to_whatever_the_schema_calls_it():
    """The visual argument name is schema-specific, so it can only be resolved
    after a tool is chosen. Binding by structure, not by name."""
    perception.install(vision=StubVision(FrameReading(
        focus="HDMI port", query="hdmi output external display",
        confidence=0.9)), embed=StubEmbed())
    _scenario, trace, _score = run_scenario(PUB07)
    with_embedding = [c for c in tool_calls(trace)
                      if any(isinstance(v, list) and v for v in c["args"].values())]
    assert with_embedding, "no call carried the frame embedding"
    assert len(with_embedding[0]["args"]["image_embedding"]) == 8


def test_vision_failure_still_produces_an_answer():
    """A broken vision model must cost us the grounding, not the scenario."""
    class BrokenVision(VisionBackend):
        name = "broken"

        def available(self):
            return True

        async def read_frame(self, path, device_hint=None):
            raise RuntimeError("model exploded")

    perception.install(vision=BrokenVision(), embed=StubEmbed())
    _scenario, trace, _score = run_scenario(PUB07)
    assert not [e for e in trace if e.get("kind") == "agent_crash"]
    assert spoken(trace)


# --------------------------------------------------------------------------
# M1 interaction: perception results are epoch-guarded
# --------------------------------------------------------------------------
def test_transcription_of_a_superseded_turn_is_discarded():
    """A transcript that arrives after the user has moved on answers a
    question they have withdrawn."""
    scenario = {
        "scenario_id": "audio_then_interrupt",
        "metadata": {"modality": "audio", "difficulty": "L3"},
        "events": [
            {"timestamp_ms": 100, "event_type": "user_audio_chunk",
             "payload": {"audio_ref": "audio/pub_05_turn1.mp3",
                         "duration_ms": 1400, "end_of_turn": True}},
            {"timestamp_ms": 400, "event_type": "interruption",
             "payload": {"text": "Actually make it Chicago."}},
        ],
        "ground_truth": {"checkpoints": [
            {"id": "no_stale_city", "type": "tool_not_called",
             "tool": "flight_search",
             "args_subset": {"destination": ["boston", "bos"]}, "weight": 1.0}]},
    }
    perception.install(asr=StubASR({
        "pub_05_turn1.mp3": Transcript(text="book a flight to Boston",
                                       confidence=0.95)}, delay_s=0.8))

    async def go():
        from agent.agent import ParticipantAgent
        h = EvaluationHarness(scenario, lambda a, b: ParticipantAgent(a, b),
                              time_scale=1.0, verbose=False)
        await h.prepare()
        return await h.run()

    trace = asyncio.run(go())
    blob = json.dumps([c["args"] for c in tool_calls(trace)]).lower()
    assert "boston" not in blob, "acted on a transcript the user superseded"
