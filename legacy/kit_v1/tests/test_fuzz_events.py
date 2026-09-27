"""H9 event fuzzer (notes/SPRINT_PLAN.md).

Malformed and adversarial event streams, run through the real harness
in-process (see tests/fuzz_common.py / tests/test_invariants.py's pattern).
Nothing here needs a model: DUET_NO_ASR=1 DUET_NO_EMBED=1 DUET_NO_VISION=1 are
set in fuzz_common.py before duet/ is ever imported.

Properties, checked on every case:
  - no agent_crash, no protocol_error;
  - the agent participates (speaks or calls a tool) - the no-participation
    gate in duet/runtime.py::_ensure_final (harness/scorer.py mirrors it)
    guarantees this by construction once scenario_end is delivered, so this
    also catches a regression that removed that guarantee;
  - nothing non-ASCII is ever emitted in spoken text (the harness prints
    trace entries to a console that may be cp1252 on Windows - PROJECT.md
    F11 - and duet/perception/asr.py::_to_ascii / vision.py::_ascii fold
    MODEL output, but raw user_speech_chunk text is never folded).
"""

from __future__ import annotations

import os
import random
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fuzz_common as fc  # noqa: E402

# ---------------------------------------------------------------------------
# garbage media fixtures - real files, invalid content
# ---------------------------------------------------------------------------
_TMPDIR = tempfile.mkdtemp(prefix="duet_fuzz_media_")
_GARBAGE_MP3 = os.path.join(_TMPDIR, "garbage.mp3")
_GARBAGE_PNG = os.path.join(_TMPDIR, "garbage.png")
with open(_GARBAGE_MP3, "wb") as _fh:
    _fh.write(os.urandom(256))
with open(_GARBAGE_PNG, "wb") as _fh:
    _fh.write(b"\x89PNG\r\n\x1a\n" + os.urandom(128))  # PNG magic, garbage body

_NONEXISTENT_MP3 = os.path.join(_TMPDIR, "does_not_exist.mp3")
_NONEXISTENT_PNG = os.path.join(_TMPDIR, "does_not_exist.png")

NON_ASCII_SAMPLES = [
    "Can you find something for me? \U0001f389\U0001f680\U0001f600",  # emoji
    "Je voudrais reserver une table a Montreal, s'il vous plait, tres bientot.",
    "मुझे कुछ जानकारी चाहिए, कृपया मदद करें।",  # Devanagari
]
# the French sample above is plain ASCII on purpose (no accents) so we add a
# second, genuinely accented variant here rather than relying on comments:
NON_ASCII_SAMPLES[1] = "Je voudrais réserver une table à Montréal, s'il vous plaît."


def _manifest(seed: int):
    rng = random.Random("events:%d" % seed)
    verb_nouns = rng.sample(fc.VERB_NOUN_POOL, k=rng.randint(1, 2))
    return {v + "_" + n: fc.make_decoy_tool(rng, v, n) for v, n in verb_nouns}


def _scenario(case_id: str, events, seed: int = 0):
    return {
        "scenario_id": "fuzz_events_%s" % case_id,
        "tool_manifest": _manifest(seed),
        "events": events,
    }


def _speech(t_ms, payload):
    return {"timestamp_ms": t_ms, "event_type": "user_speech_chunk", "payload": payload}


# ---------------------------------------------------------------------------
# the adversarial cases
# ---------------------------------------------------------------------------
def _case_payload_not_dict_string(seed):
    return _scenario("payload_not_dict_string", [
        {"timestamp_ms": 100, "event_type": "user_speech_chunk", "payload": "oops, not a dict"},
        _speech(900, {"text": "Please help me with a real request now.", "end_of_turn": True}),
    ], seed)


def _case_payload_not_dict_number(seed):
    return _scenario("payload_not_dict_number", [
        {"timestamp_ms": 100, "event_type": "user_speech_chunk", "payload": 12345},
    ], seed)


def _case_payload_none(seed):
    return _scenario("payload_none", [
        {"timestamp_ms": 100, "event_type": "user_speech_chunk", "payload": None},
    ], seed)


def _case_missing_text_field(seed):
    return _scenario("missing_text_field", [
        _speech(100, {"end_of_turn": True}),
    ], seed)


def _case_missing_end_of_turn_field(seed):
    return _scenario("missing_end_of_turn_field", [
        _speech(100, {"text": "Please schedule something for me tomorrow."}),
    ], seed)


def _case_empty_text(seed):
    return _scenario("empty_text", [
        _speech(100, {"text": "", "end_of_turn": True}),
    ], seed)


def _case_audio_ref_missing_key(seed):
    return _scenario("audio_ref_missing_key", [
        {"timestamp_ms": 100, "event_type": "user_audio_chunk",
         "payload": {"duration_ms": 1200, "end_of_turn": True}},
    ], seed)


def _case_audio_ref_nonexistent(seed):
    return _scenario("audio_ref_nonexistent", [
        {"timestamp_ms": 100, "event_type": "user_audio_chunk",
         "payload": {"audio_ref": _NONEXISTENT_MP3, "duration_ms": 1200, "end_of_turn": True}},
    ], seed)


def _case_audio_ref_corrupt(seed):
    return _scenario("audio_ref_corrupt", [
        {"timestamp_ms": 100, "event_type": "user_audio_chunk",
         "payload": {"audio_ref": _GARBAGE_MP3, "duration_ms": 1200, "end_of_turn": True}},
    ], seed)


def _case_image_ref_missing_key(seed):
    return _scenario("image_ref_missing_key", [
        {"timestamp_ms": 100, "event_type": "video_frame",
         "payload": {"frame_id": "f1", "device_hint": "GENERIC"}},
        _speech(600, {"text": "What is this part called?", "end_of_turn": True}),
    ], seed)


def _case_image_ref_nonexistent(seed):
    return _scenario("image_ref_nonexistent", [
        {"timestamp_ms": 100, "event_type": "video_frame",
         "payload": {"frame_id": "f1", "image_ref": _NONEXISTENT_PNG}},
        _speech(600, {"text": "What is this part called?", "end_of_turn": True}),
    ], seed)


def _case_image_ref_corrupt(seed):
    return _scenario("image_ref_corrupt", [
        {"timestamp_ms": 100, "event_type": "video_frame",
         "payload": {"frame_id": "f1", "image_ref": _GARBAGE_PNG}},
        _speech(600, {"text": "What is this part called?", "end_of_turn": True}),
    ], seed)


def _case_frame_no_question(seed):
    return _scenario("frame_no_question", [
        {"timestamp_ms": 100, "event_type": "video_frame",
         "payload": {"frame_id": "f1", "image_ref": _GARBAGE_PNG}},
    ], seed)


def _case_duplicate_events(seed):
    ev = _speech(100, {"text": "Please schedule an appointment for me.", "end_of_turn": True})
    return _scenario("duplicate_events", [dict(ev), dict(ev)], seed)


def _case_duplicate_tool_manifest(seed):
    scenario = _scenario("duplicate_tool_manifest", [
        _speech(100, {"text": "Please schedule an appointment for me.", "end_of_turn": True}),
    ], seed)
    return scenario


def _case_tool_result_unknown_call_id(seed):
    return _scenario("tool_result_unknown_call_id", [
        {"timestamp_ms": 100, "event_type": "tool_result",
         "payload": {"call_id": "ghost_1", "api_name": "nonexistent_tool",
                     "status": "success", "result": {"status": "success"}}},
        _speech(300, {"text": "Please help me with a real request now.", "end_of_turn": True}),
    ], seed)


def _case_tool_result_twice(seed):
    result_ev = {"timestamp_ms": 100, "event_type": "tool_result",
                "payload": {"call_id": "ghost_2", "api_name": "nonexistent_tool",
                            "status": "success", "result": {"status": "success"}}}
    return _scenario("tool_result_twice", [dict(result_ev), dict(result_ev)], seed)


def _case_interruption_empty_text(seed):
    return _scenario("interruption_empty_text", [
        {"timestamp_ms": 100, "event_type": "interruption", "payload": {"text": ""}},
    ], seed)


def _case_interruption_no_prior_turn(seed):
    return _scenario("interruption_no_prior_turn", [
        {"timestamp_ms": 100, "event_type": "interruption",
         "payload": {"text": "Wait, actually make it something else."}},
    ], seed)


def _case_scenario_end_before_any_user_event(seed):
    return _scenario("scenario_end_before_any_user_event", [], seed)


def _case_very_long_utterance(seed):
    long_text = ("Please help me with a rather long and repetitive request. " * 40).strip()
    assert len(long_text) >= 2000
    return _scenario("very_long_utterance", [
        _speech(100, {"text": long_text, "end_of_turn": True}),
    ], seed)


def _case_non_ascii(seed, sample):
    return _scenario("non_ascii_%d" % seed, [
        _speech(100, {"text": sample, "end_of_turn": True}),
    ], seed)


# A non-ASCII VALUE that gets bound to a slot and echoed back through NLG,
# rather than merely absorbed whole into a free-text tool argument. Uses the
# public flight_search vocabulary ("flight"/"to <place>") so the routing is
# guaranteed regardless of which decoy tools the manifest seed adds - the
# point of this case is the echo, not the routing.
NON_ASCII_TASK_SAMPLES = [
    "Please book a flight to Zürich for tomorrow.",
    "Please book a flight for André Dupont to Denver.",
]


def _case_non_ascii_task(seed, sample):
    return _scenario("non_ascii_task", [
        _speech(100, {"text": sample, "end_of_turn": True}),
    ], seed)


CASES = [
    ("payload_not_dict_string", _case_payload_not_dict_string),
    ("payload_not_dict_number", _case_payload_not_dict_number),
    ("payload_none", _case_payload_none),
    ("missing_text_field", _case_missing_text_field),
    ("missing_end_of_turn_field", _case_missing_end_of_turn_field),
    ("empty_text", _case_empty_text),
    ("audio_ref_missing_key", _case_audio_ref_missing_key),
    ("audio_ref_nonexistent", _case_audio_ref_nonexistent),
    ("audio_ref_corrupt", _case_audio_ref_corrupt),
    ("image_ref_missing_key", _case_image_ref_missing_key),
    ("image_ref_nonexistent", _case_image_ref_nonexistent),
    ("image_ref_corrupt", _case_image_ref_corrupt),
    ("frame_no_question", _case_frame_no_question),
    ("duplicate_events", _case_duplicate_events),
    ("tool_result_unknown_call_id", _case_tool_result_unknown_call_id),
    ("tool_result_twice", _case_tool_result_twice),
    ("interruption_empty_text", _case_interruption_empty_text),
    ("interruption_no_prior_turn", _case_interruption_no_prior_turn),
    ("scenario_end_before_any_user_event", _case_scenario_end_before_any_user_event),
    ("very_long_utterance", _case_very_long_utterance),
]
for _i, _sample in enumerate(NON_ASCII_SAMPLES):
    CASES.append(("non_ascii_%d" % _i,
                 (lambda seed, s=_sample: _case_non_ascii(seed, s))))
for _i, _sample in enumerate(NON_ASCII_TASK_SAMPLES):
    CASES.append(("non_ascii_task_%d" % _i,
                 (lambda seed, s=_sample: _case_non_ascii_task(seed, s))))

# a handful of extra seeds per case for real fuzzing variety (different decoy
# manifests underneath each malformed event stream), kept small so the whole
# file stays fast.
SEEDS = range(3)

ALL_RUNS = [(case_id, seed) for case_id, _builder in CASES for seed in SEEDS]

# Found by this fuzzer, not previously in notes/SPRINT_PLAN.md's H1-H9 list:
# a non-ASCII VALUE bound to a slot (a place or person extracted by
# duet/fastpath.py) is echoed verbatim by duet/nlg.py into a filler or
# final_response. duet/perception/asr.py::_to_ascii and
# duet/perception/vision.py::_ascii fold MODEL output to ASCII (PROJECT.md
# F11), but nothing folds a value that arrived as plain text and was only
# ever extracted, never transcribed - so a hidden scenario naming a place or
# person with an accented/non-Latin character (plausible: "Zurich",
# "Montreal", a name like "Andre") reaches spoken output unfolded. Confirmed
# reproducible: "Please book a flight to Zürich for tomorrow." ->
# filler_speech "Looking up flights to Zürich for tomorrow now." on this
# branch. Not one of H1-H5's catalogued items; closest owner by file is H-B
# (duet/fastpath.py extraction feeds duet/nlg.py phrasing) or the
# orchestrator directly - the fix is one line in duet/emitter.py::_speak (or
# duet/nlg.py), ASCII-folding the composed text the same way asr.py already
# does for transcript output.
ASCII_LEAK_CASE_IDS = {"non_ascii_task_0"}
# non_ascii_task_1 (an accented PERSON name feeding passenger_name) is kept as
# a passing contrast: that value is not echoed until a later booking-turn
# NLG line this scenario never reaches within the tail window, so it stays a
# clean baseline confirming the property is meaningful rather than vacuous.
ASCII_LEAK_REASON = (
    "duet/nlg.py echoes an extracted slot value (place/person) verbatim; "
    "duet/perception/asr.py::_to_ascii and vision.py::_ascii only fold "
    "MODEL output, not plain text that was merely extracted (PROJECT.md "
    "F11 covers ASR/vision, not this path) - reported to the orchestrator, "
    "not fixed here (H9 owns tests/ only)."
)


def _ascii_param(case_id, seed):
    if case_id in ASCII_LEAK_CASE_IDS:
        return pytest.param(case_id, seed, marks=pytest.mark.xfail(
            strict=False, reason=ASCII_LEAK_REASON))
    return pytest.param(case_id, seed)


ASCII_RUNS = [_ascii_param(case_id, seed) for case_id, seed in ALL_RUNS]


def _run(case_id: str, seed: int):
    builder = dict(CASES)[case_id]
    scenario = builder(seed)
    trace = fc.run_inprocess(scenario, time_scale=8.0, tail_ms=1200.0)
    return scenario, trace


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_id,seed", ALL_RUNS, ids=[c for c, s in ALL_RUNS])
def test_no_crash_no_protocol_error(case_id, seed):
    _scenario, trace = _run(case_id, seed)
    assert not fc.crashes(trace), (case_id, seed, fc.crashes(trace))
    assert not fc.protocol_errors(trace), (case_id, seed, fc.protocol_errors(trace))


@pytest.mark.parametrize("case_id,seed", ALL_RUNS, ids=[c for c, s in ALL_RUNS])
def test_agent_always_participates(case_id, seed):
    """The no-participation gate (duet/runtime.py::_ensure_final) always
    speaks the capabilities line if nothing else was said or called by the
    time scenario_end's tail window closes - so every one of these streams,
    however malformed, must end with at least one spoken action or tool
    call. A regression here would zero the WHOLE scenario under
    harness/scorer.py's no-participation gate."""
    _scenario, trace = _run(case_id, seed)
    assert fc.agent_participated(trace), (case_id, seed, trace[-5:])


@pytest.mark.parametrize("case_id,seed", ASCII_RUNS, ids=[c for c, s in ALL_RUNS])
def test_nothing_non_ascii_in_spoken_text(case_id, seed):
    _scenario, trace = _run(case_id, seed)
    bad = fc.find_non_ascii_speech(trace)
    assert not bad, (case_id, seed, bad)
