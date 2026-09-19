"""Tests for repair classification and gazetteer-free value extraction.

Two properties matter more than anything else here.

  * Cities that appear in no list. The kit's baseline knows seven and dies on
    the eighth; hidden scenarios are explicitly re-skinned. Every place in
    this file is deliberately absent from the kit.

  * Lowercase, unpunctuated text. Audio turns arrive as raw speech, and a
    transcript may have no capitals at all. Anything that only works on
    "Boston" and not "boston" will fail 30% of the hidden set.
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet import contract  # noqa: E402
from duet.fastpath import (  # noqa: E402
    REPAIR_CORRECTION, REPAIR_INTENT_CHANGE, REPAIR_NONE, REPAIR_REFINEMENT,
    REPAIR_RETRACTION, REPAIR_UNDO, classify_repair, extract_selector,
    extract_values, looks_like_capability_question, looks_like_greeting,
)
from duet.tools import (  # noqa: E402
    ROLE_DATE, ROLE_ID, ROLE_PERSON, ROLE_PLACE,
)

# Places that appear nowhere in the kit, the scenario generator, or our code.
UNSEEN_PLACES = ["Ljubljana", "Bengaluru", "Trondheim", "Fukuoka", "Recife"]


# --------------------------------------------------------------- classify
@pytest.mark.parametrize("text,expected", [
    ("Wait, actually make it New York.", REPAIR_CORRECTION),
    ("No sorry, Chicago. Chicago is right.", REPAIR_CORRECTION),
    ("change that to Trondheim", REPAIR_CORRECTION),
    ("I meant Fukuoka", REPAIR_CORRECTION),
    ("no, Bengaluru", REPAIR_CORRECTION),
    ("switch to the afternoon one", REPAIR_CORRECTION),
    ("Actually, never mind. Forget it.", REPAIR_RETRACTION),
    ("never mind", REPAIR_RETRACTION),
    ("cancel that, don't bother", REPAIR_RETRACTION),
    ("Forget the flight. My TV is showing a blinking red light.", REPAIR_INTENT_CHANGE),
    ("never mind the flight, what is the weather in Recife", REPAIR_INTENT_CHANGE),
    ("also add a window seat", REPAIR_REFINEMENT),
    ("and make it a window seat as well", REPAIR_REFINEMENT),
    ("go back to what I said before", REPAIR_UNDO),
    ("undo that", REPAIR_UNDO),
    ("", REPAIR_NONE),
])
def test_repair_classification(text, expected):
    assert classify_repair(text).kind == expected


def test_refinement_does_not_invalidate_in_flight_work():
    """The distinction that costs real points: cancelling a still-valid search
    throws away the task credit it would have earned."""
    assert not classify_repair("also add a window seat").invalidates
    assert classify_repair("wait, make it Ljubljana").invalidates
    assert classify_repair("never mind").invalidates
    assert classify_repair("forget the flight, my TV is broken").invalidates


def test_bare_interruption_with_no_trigger_is_still_a_correction():
    """The user barged in for a reason, even without a cue word."""
    repair = classify_repair("Trondheim please")
    assert repair.kind == REPAIR_CORRECTION


def test_retraction_with_a_new_request_is_an_intent_change():
    repair = classify_repair("forget it, how is the air quality in Fukuoka")
    assert repair.kind == REPAIR_INTENT_CHANGE
    assert "air quality" in contract.norm(repair.remainder)


# --------------------------------------------------------------- places
@pytest.mark.parametrize("place", UNSEEN_PLACES)
def test_extracts_places_never_seen_in_any_list(place):
    values = extract_values("book a flight to " + place + " for tomorrow",
                            expect=[ROLE_PLACE])
    assert ROLE_PLACE in values
    assert contract.norm(values[ROLE_PLACE].value) == contract.norm(place)


@pytest.mark.parametrize("place", UNSEEN_PLACES)
def test_extracts_places_from_lowercase_asr_output(place):
    """No capitals, no punctuation - what a raw transcript often looks like."""
    text = "book a flight to " + place.lower() + " for tomorrow"
    values = extract_values(text, expect=[ROLE_PLACE])
    assert ROLE_PLACE in values
    assert contract.norm(values[ROLE_PLACE].value) == contract.norm(place)


def test_place_is_title_cased_for_speech_when_asr_gives_lowercase():
    values = extract_values("i need a flight to bengaluru", expect=[ROLE_PLACE])
    assert values[ROLE_PLACE].value == "Bengaluru"


def test_last_mentioned_place_wins_on_a_self_repair():
    """pub_06: 'uh, book a flight to Boston - actually, make that New York.'
    The repaired value wins and the abandoned one must never reach a tool."""
    repair = classify_repair("uh book a flight to Trondheim actually make that Fukuoka")
    assert repair.kind == REPAIR_CORRECTION
    values = extract_values(repair.remainder, expect=[ROLE_PLACE])
    assert contract.norm(values[ROLE_PLACE].value) == "fukuoka"


# --------------------------------------------------------------- people
def test_extracts_a_person_from_a_benefactive_phrase():
    values = extract_values("book the 8 AM one for Alice", expect=[ROLE_PERSON])
    assert values[ROLE_PERSON].value == "Alice"


def test_extracts_a_person_from_an_appositive():
    values = extract_values("put me on the cheapest one, name's Priya",
                            expect=[ROLE_PERSON])
    assert contract.norm(values[ROLE_PERSON].value) == "priya"


def test_place_versus_person_is_resolved_by_what_the_schema_wants():
    """'for Recife' and 'for Alice' have identical grammar. Only the tool
    schema can break the tie, which is why extraction takes `expect`."""
    as_place = extract_values("find something for Recife", expect=[ROLE_PLACE])
    assert ROLE_PLACE in as_place
    as_person = extract_values("book it for Alice", expect=[ROLE_PERSON])
    assert ROLE_PERSON in as_person


# ------------------------------------------------------------------ ids
def test_extracts_identifier_shapes():
    values = extract_values("cancel booking BK-0001 please", expect=[ROLE_ID])
    assert values[ROLE_ID].value == "BK-0001"


def test_extracts_flight_identifier():
    values = extract_values("book FL-DEN-8AM for Alice", expect=[ROLE_ID])
    assert values[ROLE_ID].value == "FL-DEN-8AM"


def test_extracts_lowercase_identifier_from_asr():
    values = extract_values("cancel booking bk-0001", expect=[ROLE_ID])
    assert values[ROLE_ID].value == "BK-0001"


def test_unseen_identifier_prefix_still_recognised():
    """Hidden tools return their own id shapes (TR-0001, RC-7781, HT-0001)."""
    for ident in ("TR-0001", "RC-7781", "HT-0002", "XYZ-99-A"):
        values = extract_values("use " + ident + " please", expect=[ROLE_ID])
        assert values[ROLE_ID].value == ident


# ----------------------------------------------------------------- dates
@pytest.mark.parametrize("phrase", ["tomorrow", "Friday", "next week",
                                    "2026-09-12", "this weekend"])
def test_extracts_date_expressions(phrase):
    values = extract_values("a flight to Recife " + phrase, expect=[ROLE_DATE])
    assert ROLE_DATE in values


# -------------------------------------------------------------- selectors
def test_selector_reads_a_clock_time():
    assert extract_selector("book the 8 AM one for Alice").time == "8AM"
    assert extract_selector("make it the 2 PM flight instead").time == "2PM"
    assert extract_selector("the 14:00 departure").time == "14:00"


def test_selector_reads_a_superlative():
    """conf_06: 'the cheapest' is the $99 flight, not flights[0]."""
    assert extract_selector("put me on the cheapest one").superlative == "min_price"
    assert extract_selector("the earliest departure").superlative == "min_time"
    assert extract_selector("whatever is least expensive").superlative == "min_price"


def test_selector_is_empty_when_nothing_was_specified():
    assert extract_selector("book a flight to Recife").is_empty


# ------------------------------------------------------- negative routing
@pytest.mark.parametrize("text", [
    "Hey, what can you help me with?",
    "what do you do",
    "What else can you do?",
    "who are you",
])
def test_capability_questions_are_recognised(text):
    assert looks_like_capability_question(text)


@pytest.mark.parametrize("text", [
    "Can you find flights to Recife for Friday?",
    "book the 8 AM one for Alice",
    "what is this port used for",
    "my TV is showing a blinking red light",
    "what is the weather in Fukuoka",
])
def test_real_requests_are_not_mistaken_for_capability_questions(text):
    """The routing test must be narrow: swallowing a real request costs the
    whole task score for that scenario."""
    assert not looks_like_capability_question(text)


def test_greetings_are_recognised():
    assert looks_like_greeting("hello")
    assert looks_like_greeting("Hi!")
    assert not looks_like_greeting("hi, book me a flight to Recife")


# ------------------------------------------------------------ robustness
@pytest.mark.parametrize("text", ["", "   ", "...", "uh", "um er ah"])
def test_degenerate_input_never_raises(text):
    classify_repair(text)
    extract_values(text, expect=[ROLE_PLACE, ROLE_PERSON])
    extract_selector(text)


def test_extraction_is_deterministic():
    text = "wait, actually make it Ljubljana for Alice on Friday"
    first = extract_values(text, expect=[ROLE_PLACE, ROLE_PERSON, ROLE_DATE])
    second = extract_values(text, expect=[ROLE_PLACE, ROLE_PERSON, ROLE_DATE])
    assert {k: v.value for k, v in first.items()} == {k: v.value for k, v in second.items()}
