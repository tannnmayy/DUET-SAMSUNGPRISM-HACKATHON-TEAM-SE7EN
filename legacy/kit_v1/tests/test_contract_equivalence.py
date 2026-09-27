"""Differential test: our vendored contract must agree with the real harness.

duet/contract.py deliberately duplicates harness/protocol.py and parts of
harness/scorer.py so that duet/ is standalone. Duplication rots. This test is
the tripwire: if the organizers ship a changed harness, or we fat-finger the
copy, it fails here rather than silently costing points on the hidden set.
"""

from __future__ import annotations

import itertools
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duet import contract
from harness import protocol as kit_protocol
from harness import scorer as kit_scorer


def _action_corpus():
    """Valid, malformed and adversarial actions, including non-dict garbage."""
    texts = ["Looking up flights.", "", "   ", "...", "ab", "ok!", "12345", None, 42]
    payloads = [
        {"text": t} for t in texts
    ] + [
        {"call_id": "c1", "api_name": "flight_search", "args": {"destination": "Denver"}},
        {"call_id": "c1", "api_name": "flight_search"},              # missing args
        {"call_id": "c1", "args": {}},                                # missing api_name
        {"api_name": "flight_search", "args": {}},                    # no call_id (legal)
        {"call_id": "c1"},
        {"call_id": 7},                                               # wrong type
        {"api_name": 3, "args": "nope"},
        {},
        None,
        "not-a-dict",
        [],
    ]
    kinds = list(contract.OUTGOING_ACTIONS) + ["speak", None, 5, ""]

    corpus = []
    for kind, payload in itertools.product(kinds, payloads):
        corpus.append({"action": kind, "payload": payload})
        corpus.append({"action": kind, "payload": payload, "state_snapshot": {"intent": "x", "slots": {}}})
        corpus.append({"action": kind, "payload": payload, "state_snapshot": None})
    # non-dict actions
    corpus += [None, "x", 3, [], {"no_action_key": 1}]
    return corpus


def test_validate_action_matches_harness():
    mismatches = []
    for action in _action_corpus():
        ours = contract.validate_action(action)
        theirs = kit_protocol.validate_action(action)
        # Compare as sets of problem-count + emptiness: the harness only cares
        # whether the list is empty, but we compare length too to catch drift.
        if bool(ours) != bool(theirs) or len(ours) != len(theirs):
            mismatches.append((action, ours, theirs))
    assert not mismatches, (
        str(len(mismatches)) + " validator mismatches, first 3: " + repr(mismatches[:3])
    )


def test_is_substantive_matches_scorer():
    samples = [
        "", " ", "...", "..", "a", "ab", "abc", "ok!", "o.k", "123", "1234a",
        "Got it.", "Switching to New York.", "?!?!", "a1b2c3", "   hi   ",
        None, 0, 3.5, "———", "e.g.", "n/a", "--", "a-b",
    ]
    for s in samples:
        assert contract.is_substantive(s) == kit_scorer._is_substantive(s), (
            "is_substantive drift on " + repr(s)
        )


def test_norm_matches_harness():
    for v in ["  Boston ", "NEW YORK", 42, None, "", " x "]:
        assert contract.norm(v) == kit_protocol.norm(v)


def test_get_path_matches_harness():
    obj = {"slots": {"destination": "Denver", "nested": {"a": 1}}, "intent": "book"}
    for p in ["slots.destination", "intent", "slots.nested.a", "missing", "slots.missing", ""]:
        assert contract.get_path(obj, p) == kit_protocol.get_path(obj, p)


def test_claim_patterns_have_not_drifted():
    """Our copy of the scorer's published claim patterns must be exact."""
    ours = {k: tuple(v) for k, v in contract.CLAIM_PATTERNS_PUBLISHED.items()}
    theirs = {k: tuple(v) for k, v in kit_scorer.CLAIM_PATTERNS.items()}
    assert ours == theirs, "CLAIM_PATTERNS drift: ours=" + repr(ours) + " theirs=" + repr(theirs)


def test_future_guards_have_not_drifted():
    assert tuple(contract.FUTURE_GUARDS) == tuple(kit_scorer.FUTURE_GUARDS)


def test_spoken_actions_have_not_drifted():
    assert set(contract.SPOKEN_ACTIONS) == set(kit_protocol.SPOKEN_ACTIONS)
    assert set(contract.OUTGOING_ACTIONS) == set(kit_protocol.OUTGOING_ACTIONS)
    assert set(contract.INCOMING_EVENT_TYPES) == set(kit_protocol.INCOMING_EVENT_TYPES)


def test_generic_claim_guard_is_a_superset_of_published():
    """Every published claim phrase must also trip our generic guard when we
    have no confirmed completions - otherwise a hidden tool's phrasing slips
    through."""
    probes = [
        ("Booked!", "book_flight"),
        ("Your flight is reserved.", "book_flight"),
        ("Ticket created.", "create_support_ticket"),
        ("Your booking is cancelled.", "cancel_booking"),
    ]
    for text, _tool in probes:
        reasons = contract.find_completion_claims(text, succeeded_tools=())
        assert reasons, "generic guard missed: " + text


def test_claim_guard_allows_promises_and_grounded_claims():
    assert not contract.find_completion_claims(
        "I'll get that booked now.", succeeded_tools=("book_flight",))
    assert not contract.find_completion_claims(
        "Booked - your confirmation is BK-0001.", succeeded_tools=("book_flight",))
    assert not contract.find_completion_claims(
        "Looking up flights to Denver.", succeeded_tools=())


# --------------------------------------------------------------------------
# Promise-vs-claim: our guard is stricter than the scorer, never looser.
# --------------------------------------------------------------------------

_PROMISES = [
    "I'll get that booked now.",
    "Let me get that booked for you.",
    "I'm about to book that.",
    "Getting that booked right away.",
    "I will book the 8 AM one.",
]

_CLAIMS = [
    "Booked!",
    "That's booked.",
    "Your flight is reserved.",
    "Ticket created.",
    "Your booking is cancelled.",
    "Booked! I'll email you the details.",   # scorer misses this one; we must not
    "Done - reserved for you.",
]


def test_promises_are_allowed_before_completion():
    for text in _PROMISES:
        assert not contract.find_completion_claims(text, succeeded_tools=()), (
            "promise wrongly flagged: " + text)


def test_claims_are_blocked_before_completion():
    for text in _CLAIMS:
        assert contract.find_completion_claims(text, succeeded_tools=()), (
            "unbacked claim not caught: " + text)


def test_claims_allowed_once_the_tool_succeeded():
    assert not contract.find_completion_claims(
        "Booked - your confirmation is BK-0001.", succeeded_tools=("book_flight",))
    assert not contract.find_completion_claims(
        "Ticket created: TK-0001.", succeeded_tools=("create_support_ticket",))


def test_we_are_never_more_permissive_than_the_scorer():
    """THE load-bearing invariant.

    If our guard permits an utterance that contains a completion verb, the
    scorer's own check must also skip it - otherwise we would emit something
    that costs -0.25 safety. Verified behaviourally rather than by inspecting
    the regex, so it survives edits to either side.
    """
    verbs = ["booked", "reserved", "cancelled", "created", "confirmed", "filed"]
    prefixes = [
        "", "i'll ", "let me ", "i will ", "we'll ", "about to ", "getting ",
        "right away ", "one moment ", "please ", "that is ", "your flight is ",
        "now ", "already ", "just ",
    ]
    suffixes = ["", " for you", " now", " shortly", "!", " - BK-0001"]

    offenders = []
    for v, p, s in itertools.product(verbs, prefixes, suffixes):
        text = (p + v + s).strip()
        we_allow = not contract.find_completion_claims(text, succeeded_tools=())
        scorer_skips = any(g in contract.norm(text) for g in kit_scorer.FUTURE_GUARDS)
        if we_allow and not scorer_skips:
            offenders.append(text)
    assert not offenders, (
        "we permit utterances the scorer penalises: " + repr(offenders[:10]))
