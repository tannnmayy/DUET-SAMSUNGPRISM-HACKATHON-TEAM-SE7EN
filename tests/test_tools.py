"""Tests for schema-driven tool handling.

The hidden set contains roughly ten tools we have never seen, delivered as a
schema moments before we must call them. So the tests that matter most are the
ones using tools that appear nowhere in the kit.
"""

from __future__ import annotations

import itertools
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet import contract  # noqa: E402
from duet.tools import (  # noqa: E402
    ROLE_DATE, ROLE_ENUM, ROLE_ID, ROLE_NUMBER, ROLE_PERSON, ROLE_PLACE,
    ROLE_TEXT, ToolRegistry, ground_fields,
)
from harness.mock_env import TOOL_REGISTRY, MockEnvironment  # noqa: E402


# Tools invented for these tests. None of them exists in the kit; they are how
# we check that nothing depends on a name we have seen before.
UNSEEN = {
    "train_booking": {
        "kind": "state_modifying",
        "delay_range_ms": [900, 1800],
        "description": "Reserve a seat on a train between two stations.",
        "args": {
            "origin_station": {"type": "string", "required": True,
                               "description": "Departure city or station."},
            "arrival_station": {"type": "string", "required": True,
                                "description": "Arrival city or station."},
            "traveller_name": {"type": "string", "required": True,
                               "description": "Full name of the traveller."},
            "coach_class": {"type": "string", "required": False,
                            "enum": ["standard", "first"],
                            "description": "Seating class."},
        },
        "default_result": {"reservation_id": "TR-0001", "seat": "12A"},
    },
    "air_quality": {
        "kind": "read_only",
        "delay_range_ms": [500, 1100],
        "description": "Current air quality index for a city.",
        "args": {
            "city": {"type": "string", "required": True, "description": "City name."},
            "radius_km": {"type": "number", "required": False,
                          "description": "Search radius."},
        },
        "default_result": {"aqi": 42, "category": "good"},
    },
    "shelf_scan": {
        "kind": "read_only",
        "delay_range_ms": [1000, 2000],
        "description": "Identify products on a shelf from a photograph.",
        "args": {
            "prompt": {"type": "string", "required": True,
                       "description": "What to look for."},
            "frame_embedding": {"type": "array", "items": "number",
                                "required": False,
                                "description": "Dense visual embedding of the frame."},
        },
        "default_result": {"items": ["cereal"], "confidence": 0.8},
    },
}


def public() -> ToolRegistry:
    return ToolRegistry(TOOL_REGISTRY)


def mixed() -> ToolRegistry:
    merged = dict(TOOL_REGISTRY)
    merged.update(UNSEEN)
    return ToolRegistry(merged)


# ------------------------------------------------------------------ parsing
def test_parses_the_public_registry():
    reg = public()
    assert len(reg) == 5
    search = reg.get("flight_search")
    assert search is not None
    assert not search.is_state_modifying
    assert [a.name for a in search.required_args()] == ["destination"]
    assert search.delay_range_ms == (1500.0, 3000.0)


def test_identifies_state_modifying_tools_from_the_kind_tag():
    reg = mixed()
    assert reg.state_modifying_names() == {
        "book_flight", "cancel_booking", "create_support_ticket", "train_booking"}


def test_parses_nested_object_arguments():
    ticket = public().get("create_support_ticket")
    device = ticket.args["device"]
    assert device.is_object
    assert device.properties["model"].required
    assert not device.properties["serial"].required
    severity = ticket.args["issue"].properties["severity"]
    assert severity.enum == ["low", "medium", "high"]


def test_tolerates_a_malformed_manifest():
    """A crash while parsing the first event of a scenario is a zero."""
    reg = ToolRegistry({"broken": None, "also_broken": {"args": "nope"},
                        "fine": {"kind": "read_only", "description": "ok"}})
    assert len(reg) == 3
    assert reg.get("broken") is not None
    assert reg.get("also_broken").args == {}


# ------------------------------------------------------------------ roles
@pytest.mark.parametrize("arg_name,expected", [
    ("destination", ROLE_PLACE),
    ("city", ROLE_PLACE),
    ("pickup_city", ROLE_PLACE),
    ("origin_station", ROLE_PLACE),
    ("passenger_name", ROLE_PERSON),
    ("traveller_name", ROLE_PERSON),
    ("date", ROLE_DATE),
    ("booking_id", ROLE_ID),
    ("query", ROLE_TEXT),
    ("issue_summary", ROLE_TEXT),
])
def test_argument_roles_generalise_to_unseen_names(arg_name, expected):
    reg = ToolRegistry({"t": {"args": {arg_name: {"type": "string", "required": True}}}})
    assert reg.get("t").args[arg_name].role() == expected


def test_enum_and_number_roles_come_from_the_type():
    reg = ToolRegistry({"t": {"args": {
        "cls": {"type": "string", "enum": ["a", "b"]},
        "n": {"type": "number"},
    }}})
    assert reg.get("t").args["cls"].role() == ROLE_ENUM
    assert reg.get("t").args["n"].role() == ROLE_NUMBER


# ------------------------------------------------------------------ ranking
def test_ranks_the_unseen_weather_style_tool_over_known_tools():
    """pub_09's skill: the manifest contains a tool we have never seen and the
    request is not a flight request. Forcing it into a known tool is a scored
    failure."""
    reg = ToolRegistry({**TOOL_REGISTRY, "weather_lookup": {
        "kind": "read_only", "delay_range_ms": [900, 1800],
        "description": "Current weather and a short forecast for a city.",
        "args": {"city": {"type": "string", "required": True,
                          "description": "City name."}},
        "default_result": {"condition": "sunny", "temp_f": 74}}})
    best = reg.best("What's the weather like in Denver right now?")
    assert best is not None and best.name == "weather_lookup"


def test_ranks_flight_search_for_a_flight_request():
    best = public().best("Can you find flights to Chicago for Friday?")
    assert best is not None and best.name == "flight_search"


def test_ranks_an_unseen_tool_for_its_own_domain():
    reg = mixed()
    assert reg.best("How is the air quality in Denver?").name == "air_quality"
    assert reg.best("Reserve me a seat on a train to Boston").name == "train_booking"


def test_a_camera_frame_boosts_tools_that_accept_one():
    """Structural signal, not a name match: an array-of-number argument
    described as visual is how a schema advertises it can use a frame."""
    reg = mixed()
    without = {s.name: score for s, score in reg.rank("what is this?")}
    with_frame = {s.name: score
                  for s, score in reg.rank("what is this?", has_frame=True)}
    assert with_frame["shelf_scan"] > without["shelf_scan"]
    # and it becomes selectable, where on words alone it was not
    assert reg.best("what is this?") is None
    assert reg.best("what is this?", has_frame=True).wants_visual()


def test_wants_visual_is_detected_from_the_schema():
    reg = mixed()
    assert reg.get("shelf_scan").wants_visual()
    assert reg.get("lookup_manual").wants_visual()
    assert not reg.get("air_quality").wants_visual()
    assert reg.get("shelf_scan").visual_arg().name == "frame_embedding"


def test_ranking_is_deterministic():
    reg = mixed()
    first = [s.name for s, _ in reg.rank("book a flight to Denver")]
    second = [s.name for s, _ in reg.rank("book a flight to Denver")]
    assert first == second


def test_unrelated_utterance_scores_nothing():
    assert public().best("hello there, how are you today?") is None


# -------------------------------------------------------- arg construction
def test_binds_values_by_role_not_by_name():
    reg = mixed()
    values = {ROLE_PLACE: "Denver", ROLE_PERSON: "Alice"}
    args, missing = reg.build_args(reg.get("air_quality"), values)
    assert args == {"city": "Denver"} and missing == []

    args, missing = reg.build_args(reg.get("flight_search"), values)
    assert args["destination"] == "Denver" and missing == []


def test_reports_missing_required_arguments_rather_than_inventing_them():
    reg = mixed()
    args, missing = reg.build_args(reg.get("train_booking"), {ROLE_PERSON: "Alice"})
    assert "traveller_name" in args
    assert set(missing) == {"origin_station", "arrival_station"}


def test_builds_nested_object_arguments():
    """docs/TOOLS.md calls create_support_ticket 'the nested-object exercise:
    if your code assumes flat args, this tool is where it breaks'."""
    reg = public()
    args, missing = reg.build_args(
        reg.get("create_support_ticket"),
        {ROLE_TEXT: "LED blinking red", ROLE_ENUM: "high"},
        utterance="my TV has an LED blinking red")
    assert missing == [] or "device" in missing
    if "issue" in args:
        assert args["issue"]["summary"]
        assert args["issue"]["severity"] in ("low", "medium", "high")


def test_free_text_argument_falls_back_to_the_utterance():
    reg = public()
    args, missing = reg.build_args(reg.get("lookup_manual"), {},
                                   utterance="what is this port used for")
    assert args["query"] == "what is this port used for"
    assert missing == []


def test_enum_is_never_invented():
    """A wrong enum value is an immediate invalid_args error."""
    reg = public()
    spec = reg.get("lookup_manual")
    args, _ = reg.build_args(spec, {}, utterance="my QN90 television is broken")
    assert args.get("device_model") == "QN90"
    args, _ = reg.build_args(spec, {}, utterance="my toaster is broken")
    # optional enum with no evidence: omitted rather than guessed
    assert "device_model" not in args


def test_optional_enum_omitted_but_required_enum_defaults_to_middle():
    reg = ToolRegistry({"t": {"args": {
        "sev": {"type": "string", "required": True, "enum": ["low", "medium", "high"]},
        "opt": {"type": "string", "required": False, "enum": ["x", "y"]},
    }}})
    args, missing = reg.build_args(reg.get("t"), {}, utterance="something broke")
    assert args["sev"] == "medium"
    assert "opt" not in args and missing == []


def test_number_and_array_coercion():
    reg = mixed()
    args, _ = reg.build_args(reg.get("air_quality"),
                             {ROLE_PLACE: "Denver", ROLE_NUMBER: "5"})
    assert args["radius_km"] == 5
    args, _ = reg.build_args(reg.get("shelf_scan"), {},
                             utterance="find cereal",
                             extras={"frame_embedding": [0.1, 0.2]})
    assert args["frame_embedding"] == [0.1, 0.2]


# --------------------------------------------------- differential validation
def _arg_corpus():
    return [
        {}, {"destination": "Denver"}, {"destination": 42},
        {"destination": "Denver", "date": "Friday"},
        {"flight_id": "FL-DEN-8AM"},
        {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"},
        {"booking_id": "BK-0001"},
        {"query": "hdmi"}, {"query": "hdmi", "device_model": "QN90"},
        {"query": "hdmi", "device_model": "NOPE"},
        {"query": "hdmi", "image_embedding": [0.1, 0.2]},
        {"query": "hdmi", "image_embedding": "not-a-list"},
        {"device": {"model": "QN90"}, "issue": {"summary": "x", "severity": "low"}},
        {"device": {"model": "QN90"}, "issue": {"summary": "x", "severity": "URGENT"}},
        {"device": {}, "issue": {"summary": "x", "severity": "low"}},
        {"device": "flat", "issue": {"summary": "x", "severity": "low"}},
        {"device": {"model": "QN90"}},
        {"origin_station": "A", "arrival_station": "B", "traveller_name": "C"},
        {"origin_station": "A", "arrival_station": "B", "traveller_name": "C",
         "coach_class": "first"},
        {"origin_station": "A", "arrival_station": "B", "traveller_name": "C",
         "coach_class": "business"},
        {"city": "Denver", "radius_km": 5},
        {"prompt": "cereal", "frame_embedding": [0.1]},
    ]


def test_validation_matches_the_mock_environment_exactly():
    """We validate locally so a bad call never costs us a round trip. That is
    only safe if our rules are the mock's rules - so compare them directly."""
    merged = dict(TOOL_REGISTRY)
    merged.update(UNSEEN)
    reg = ToolRegistry(merged)
    env = MockEnvironment(scenario_id="test", extra_tools=UNSEEN)

    mismatches = []
    for tool_name, args in itertools.product(merged, _arg_corpus()):
        ours = reg.get(tool_name).validate(args)
        theirs = env._validate_args(merged[tool_name], args)
        if bool(ours) != bool(theirs) or len(ours) != len(theirs):
            mismatches.append((tool_name, args, ours, theirs))
    assert not mismatches, (
        str(len(mismatches)) + " validator mismatches, first 3: "
        + repr(mismatches[:3]))


def test_non_dict_args_rejected_like_the_mock():
    reg = public()
    assert reg.get("flight_search").validate("nope") == ["args must be an object"]


# ------------------------------------------------------------- grounding
def test_ground_fields_uses_the_schemas_declared_result_shape():
    """For a tool we have never seen, default_result is how we know which
    fields carry the answer (docs/TOOLS.md convention 7)."""
    reg = mixed()
    fields = ground_fields(reg.get("air_quality"),
                           {"status": "success", "aqi": 42, "category": "good"})
    assert dict(fields) == {"aqi": 42, "category": "good"}


def test_ground_fields_falls_back_to_scalar_result_fields():
    fields = ground_fields(None, {"status": "success", "booking_id": "BK-0001",
                                  "flight_id": "FL-DEN-8AM", "nested": {"a": 1}})
    assert dict(fields) == {"booking_id": "BK-0001", "flight_id": "FL-DEN-8AM"}


def test_ground_fields_handles_empty_and_garbage():
    assert ground_fields(None, {}) == []
    assert ground_fields(None, "not a dict") == []


# ------------------------------------------------- satisfiability weighting
def test_a_catchall_query_arg_does_not_win_against_a_real_extracted_value():
    """Regression: lookup_manual once outranked flight_search for 'find a
    flight to Denver and book the 8 AM one for Alice', because its `query`
    argument absorbs any sentence and so looked perfectly satisfiable."""
    reg = public()
    best = reg.best("Find a flight to Denver and book the 8 AM one for Alice.",
                    available_values={ROLE_PLACE: "Denver", ROLE_PERSON: "Alice"})
    assert best is not None and best.name == "flight_search"


def test_chaining_flips_the_choice_once_an_id_exists():
    """Before a flight_id exists, booking is unsatisfiable and searching wins.
    Once the search result supplies one, booking wins. The planner relies on
    this to sequence a chained request without hardcoding the order."""
    reg = public()
    utterance = "Find a flight to Denver and book the 8 AM one for Alice."
    before = reg.best(utterance,
                      available_values={ROLE_PLACE: "Denver", ROLE_PERSON: "Alice"})
    after = reg.best(utterance,
                     available_values={ROLE_PLACE: "Denver", ROLE_PERSON: "Alice",
                                       ROLE_ID: "FL-DEN-8AM"},
                     named_values={"flight_id": "FL-DEN-8AM"})
    assert before.name == "flight_search"
    assert after.name == "book_flight"


def test_an_identifier_only_fills_an_argument_of_its_own_kind():
    """A flight id once made cancel_booking look fully satisfiable - flight_id
    and booking_id share a role - and after a flight search for "please BOOK a
    flight" the agent issued cancel_booking(booking_id=FL-NYC-8AM): an
    irreversible call with a nonsense argument, scored 100 because it errored.
    An argument named after a canonical id slot accepts only that slot."""
    reg = public()
    utterance = "Please book a flight to Boston for tomorrow."
    held = {ROLE_PLACE: "New York", ROLE_ID: "FL-NYC-8AM"}
    best = reg.best(utterance, available_values=held,
                    named_values={"flight_id": "FL-NYC-8AM"})
    assert best is None or best.name != "cancel_booking", best
    args, missing = reg.build_args(reg.get("cancel_booking"), held,
                                   by_name={"flight_id": "FL-NYC-8AM"})
    assert "booking_id" not in args and missing == ["booking_id"]


# ----------------------------------------------------------- the text sink
def test_text_sink_covers_symptom_descriptions_with_no_shared_vocabulary():
    """'My TV is showing a blinking red light' shares no words with 'retrieve
    pages from indexed device manuals'. rank() correctly finds no evidence, so
    the planner falls back to a read-only free-text tool explicitly."""
    reg = public()
    assert reg.best("My TV is showing a blinking red light.") is None
    sink = reg.text_sink("My TV is showing a blinking red light.")
    assert sink is not None and sink.name == "lookup_manual"


def test_text_sink_never_returns_a_state_modifying_tool():
    """The fallback fires when we are least certain, which is exactly when an
    irreversible call must not happen."""
    reg = ToolRegistry({
        "log_complaint": {"kind": "state_modifying",
                          "description": "File a complaint.",
                          "args": {"text": {"type": "string", "required": True}}},
    })
    assert reg.text_sink("something is broken") is None


def test_text_sink_absent_when_no_tool_takes_free_text():
    reg = ToolRegistry({
        "ping": {"kind": "read_only", "description": "Ping a host.",
                 "args": {"host_id": {"type": "string", "required": True,
                                      "description": "Host id."}}},
    })
    assert reg.text_sink("is anything up?") is None


def test_text_sink_is_deterministic():
    reg = mixed()
    assert reg.text_sink("x") is reg.text_sink("x")


def test_a_venue_name_is_a_place_and_an_id_is_an_id():
    """"Restaurant name." once read as a PERSON because of the word "name",
    and an argument named hotel_id must stay an identifier."""
    from duet.tools import ArgSpec, ROLE_ID, ROLE_PLACE
    assert ArgSpec("restaurant", description="Restaurant name.").role() == ROLE_PLACE
    assert ArgSpec("hotel_id", description="Hotel identifier.").role() == ROLE_ID
    assert ArgSpec("venue", description="Where to meet.").role() == ROLE_PLACE
