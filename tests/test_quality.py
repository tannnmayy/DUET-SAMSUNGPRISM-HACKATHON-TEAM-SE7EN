"""Tests for what the agent SAYS, as the quality judge will read it.

The automated scorer is blind to most of this: the phantom capabilities
answer these tests exist for appeared in six of seventeen scenarios that all
scored 100. So the rules are checked two ways - on the phrasing helpers in
isolation, and on real transcripts through tools/quality.py's lint, whose own
rules are first proven to fire on a known-bad transcript.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from duet import config, contract  # noqa: E402
from duet.nlg import Phrasebook, _summarise_description  # noqa: E402
from duet.planner.rules import (  # noqa: E402
    _is_id_key, _is_label, _phrase, commit_body, speakable_subject,
)
from duet.tools import ToolRegistry  # noqa: E402
from harness.mock_env import TOOL_REGISTRY  # noqa: E402
from harness.runner import EvaluationHarness  # noqa: E402
from harness.scenario_gen import UNSEEN_TOOLS  # noqa: E402

import quality  # noqa: E402  (tools/quality.py)

REGISTRY = ToolRegistry({**TOOL_REGISTRY,
                         **{t["name"]: t["schema"] for t in UNSEEN_TOOLS}})


# ---------------------------------------------------------------- capabilities
@pytest.mark.parametrize("description, expected", [
    ("Search flights to a destination city on a given date.", "search flights"),
    ("Book a specific flight returned by flight_search.", "book a flight"),
    ("Cancel an existing booking.", "cancel a booking"),
    ("Retrieve pages from indexed device manuals. Supports hybrid search.",
     "retrieve pages from device manuals"),
    ("Open a support ticket for an unresolved device issue.", "open a support ticket"),
    ("Current weather and a short forecast for a city.",
     "get current weather and a short forecast"),
    ("Search hotels in a city.", "search hotels"),
    ("Quote a rental car at a city's airport.", "quote a rental car"),
    ("Set the target temperature of a room thermostat.", "set the target temperature"),
])
def test_capability_phrases_are_complete_clauses(description, expected):
    assert _summarise_description(description) == expected


def test_capabilities_answer_has_no_dangling_words():
    text = Phrasebook().capabilities(REGISTRY.descriptions())
    assert not quality._DANGLING.search(text), text
    assert "help" in contract.norm(text)          # pub_04's grounding needle


# ---------------------------------------------------------------- subjects
@pytest.mark.parametrize("tool, args, expected", [
    ("flight_search", {"destination": "Chicago", "date": "Friday"},
     "flights to Chicago for Friday"),
    ("flight_search", {"destination": "Boston"}, "flights to Boston"),
    ("weather_lookup", {"city": "Denver"}, "the weather in Denver"),
    ("hotel_search", {"city": "Miami"}, "hotels in Miami"),
    ("lookup_manual", {"query": "what is this port"}, "the manual"),
    ("book_flight", {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"},
     "booking flight FL-DEN-8AM for Alice"),
    ("cancel_booking", {"booking_id": "BK-0001"}, "cancelling booking BK-0001"),
])
def test_subjects_never_read_the_tool_name_aloud(tool, args, expected):
    subject = speakable_subject(REGISTRY.get(tool), args)
    assert subject == expected
    assert tool not in subject and tool.replace("_", " ") not in subject


def test_commit_body_reports_the_new_reference():
    body = commit_body(REGISTRY.get("book_flight"),
                       {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"},
                       {"status": "success", "booking_id": "BK-0001",
                        "flight_id": "FL-DEN-8AM"})
    assert body == "flight FL-DEN-8AM is booked for Alice, booking reference BK-0001"


# ---------------------------------------------------------------- result fields
def test_identifier_fields_are_recognised_by_their_last_word():
    for key in ("flight_id", "id", "ticket_id", "confirmation_code", "booking_reference"):
        assert _is_id_key(key), key
    # Substring matching on "id" once put a weather humidity into the
    # snapshot as a flight id.
    for key in ("humidity", "provider", "valid_until", "width"):
        assert not _is_id_key(key), key


def test_label_fields_are_recognised_by_word_not_substring():
    assert _is_label("title") and _is_label("name") and _is_label("hotel_name")
    assert not _is_label("depart")     # contains "part"


@pytest.mark.parametrize("key, value, expected", [
    ("temp_f", 74, "74 degrees"),
    ("daily_usd", 58, "for $58 a day"),
    ("price_usd", 129, "for $129"),
    ("quote_id", "RC-7781", "quote RC-7781"),
    ("depart", "08:00", "departing at 08:00"),
    ("name", "Harbor Inn", "Harbor Inn"),
])
def test_fields_are_spoken_with_their_connective(key, value, expected):
    assert _phrase(key, value) == expected


# ---------------------------------------------------------------- truthfulness
def test_a_timed_out_commit_is_reported_as_unknown_never_as_done():
    pb = Phrasebook()
    for _ in range(5):
        text = pb.failed_commit("booking flight FL-DEN-8AM for Alice", "timeout")
        low = contract.norm(text)
        # Every variant, including the fallback once the specific lines are
        # spent, must say the outcome is UNKNOWN - never that it failed.
        assert any(p in low for p in ("timed out", "cannot tell", "unclear",
                                      "no clear answer")), text
        for word in ("booked", "reserved", "confirmed", "all set"):
            assert word not in low, text
        assert not contract.find_completion_claims(text), text


def test_commit_acknowledgment_is_a_promise_not_a_claim():
    pb = Phrasebook()
    for _ in range(4):
        text = pb.ack_commit("booking flight FL-DEN-8AM for Alice")
        assert not contract.find_completion_claims(text), text


# ---------------------------------------------------------------- the lint
def _entry(t, action, text):
    return {"kind": "action", "t_ms": t, "action": action, "payload": {"text": text}}


def test_lint_catches_the_defects_it_exists_for():
    """Testing the test: every rule must fire on the transcript that
    motivated it, or a clean result means nothing."""
    trace = [
        {"kind": "event", "t_ms": 0, "event_type": "tool_manifest",
         "payload": {"tools": ["flight_search"]}},
        {"kind": "event", "t_ms": 100, "event_type": "interruption",
         "payload": {"text": "Wait, actually make it New York."}},
        _entry(100, "final_response", "I can help you search flights to, book a specific."),
        _entry(130, "filler_speech", "Looking up flight search for New York now."),
        _entry(900, "final_response", "I found FL-NYC-8AM."),
        _entry(950, "filler_speech", "Looking up flight search for New York now."),
    ]
    problems = " || ".join(quality.lint({}, trace))
    for symptom in ("tool name spoken", "final answers after", "stacked speech",
                    "dangling clause", "repeat"):
        assert symptom in problems, symptom + " not detected: " + problems


TEXT_SCENARIOS = [os.path.join(ROOT, p) for p in (
    "scenarios/pub_01_text_simple.json",
    "scenarios/pub_02_text_interrupt.json",
    "scenarios/pub_03_text_chained_booking.json",
    "scenarios/pub_04_text_no_tool.json",
    "scenarios/pub_08_text_tool_failure.json",
    "scenarios/pub_09_text_unseen_tool.json",
    "tests/conformance/conf_01_double_interrupt.json",
    "tests/conformance/conf_02_retraction.json",
    "tests/conformance/conf_03_intent_change.json",
    "tests/conformance/conf_04_interrupt_during_booking.json",
    "tests/conformance/conf_06_paraphrase_cheapest.json",
    "tests/conformance/conf_07_empty_result.json",
    "tests/conformance/conf_08_state_mod_timeout.json",
)]


@pytest.mark.parametrize("path", TEXT_SCENARIOS, ids=lambda p: os.path.basename(p)[:7])
def test_real_transcripts_pass_the_lint(path):
    """End to end: including the scenario_end race that produced a phantom
    final answer whenever the last event triggered a re-plan."""
    from agent.agent import ParticipantAgent

    with open(path, "r", encoding="utf-8") as fh:
        scenario = json.load(fh)
    original = config.STRICT
    config.STRICT = False
    try:
        async def go():
            h = EvaluationHarness(scenario, lambda a, b: ParticipantAgent(a, b),
                                  time_scale=4.0, verbose=False)
            await h.prepare()
            return await h.run()
        trace = asyncio.run(go())
    finally:
        config.STRICT = original
    problems = quality.lint(scenario, trace)
    assert not problems, "\n".join(problems + quality.transcript_lines(trace))
