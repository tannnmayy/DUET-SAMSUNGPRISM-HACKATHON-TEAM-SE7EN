"""Tests for the coordination layer (M1 epochs, M2 commitment + idempotency).

This is the module the theme is actually about, and it needs no model, so it
is tested exhaustively and in isolation.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet import config, contract, telemetry  # noqa: E402
from duet.coordinator import (  # noqa: E402
    CANCELLED, COMPLETED, FAILED, LEDGER_COMMITTED, LEDGER_IN_FLIGHT,
    LEDGER_NONE, LEDGER_UNKNOWN, PENDING, Coordinator, idempotency_key,
)
from duet.emitter import Emitter  # noqa: E402
from duet.nlg import Phrasebook  # noqa: E402
from duet.state import ConversationState  # noqa: E402
from duet.tools import ToolRegistry  # noqa: E402
from harness.mock_env import TOOL_REGISTRY  # noqa: E402


def build():
    telemetry.reset()
    config.STRICT = False
    q: asyncio.Queue = asyncio.Queue()
    state = ConversationState()
    emitter = Emitter(q, state, Phrasebook())
    registry = ToolRegistry(TOOL_REGISTRY)
    coord = Coordinator(state, emitter, registry)
    return q, state, emitter, registry, coord


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.fixture(autouse=True)
def _restore():
    original = config.STRICT
    yield
    config.STRICT = original


def result_event(call_id, api_name, status="success", **result):
    payload = {"call_id": call_id, "api_name": api_name, "status": status,
               "result": {"status": status, **result}}
    return payload


# ------------------------------------------------------- idempotency key
def test_idempotency_key_matches_the_scorers_duplicate_key_exactly():
    """The scorer counts duplicate state-modifying completions with its own
    key. If ours differs, we would refuse a call it would have allowed, or
    allow one it penalises. Reproduce its construction and compare."""
    cases = [
        ("book_flight", {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"}),
        ("book_flight", {"passenger_name": "alice", "flight_id": "fl-den-8am"}),
        ("book_flight", {"flight_id": " FL-DEN-8AM ", "passenger_name": "ALICE"}),
        ("create_support_ticket", {"device": {"model": "QN90"},
                                   "issue": {"summary": "x", "severity": "low"}}),
        ("cancel_booking", {"booking_id": "BK-0001"}),
        ("t", {}),
        ("t", {"n": 3, "b": True, "none": None}),
    ]
    for api_name, args in cases:
        scorer_key = api_name + "|" + json.dumps(
            {k: contract.norm(v) if not isinstance(v, (dict, list)) else v
             for k, v in sorted(args.items())}, sort_keys=True, default=str)
        assert idempotency_key(api_name, args) == scorer_key, (api_name, args)


def test_key_is_insensitive_to_arg_order_and_case_like_the_scorer():
    a = idempotency_key("book_flight", {"flight_id": "FL-1", "passenger_name": "Alice"})
    b = idempotency_key("book_flight", {"passenger_name": "alice", "flight_id": "fl-1"})
    assert a == b


# ------------------------------------------------------- read-only freedom
def test_read_only_calls_bypass_the_commitment_gate():
    """Speculating with a read-only tool is free: it is cancellable and
    abandonable, which is where our latency advantage comes from."""
    _q, _st, _em, reg, coord = build()
    spec = reg.get("flight_search")
    ok, reason = coord.may_issue(spec, {"destination": "Denver"},
                                 now_ms=0, turn_ended=False, confidence=0.1)
    assert ok and reason == "read_only"


def test_read_only_may_fire_on_a_partial_turn():
    _q, _st, _em, reg, coord = build()
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Denver"},
                          now_ms=500, turn_ended=False)
    assert call_id is not None


# --------------------------------------------------- the commitment gate
def test_state_modifying_blocked_before_end_of_turn():
    _q, _st, _em, reg, coord = build()
    ok, reason = coord.may_issue(
        reg.get("book_flight"),
        {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"},
        now_ms=1000, turn_ended=False)
    assert not ok and reason == "turn_not_ended"


def test_state_modifying_blocked_on_low_confidence():
    _q, _st, _em, reg, coord = build()
    ok, reason = coord.may_issue(
        reg.get("book_flight"),
        {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"},
        now_ms=1000, confidence=0.4)
    assert not ok and reason.startswith("low_confidence")


def test_state_modifying_blocked_immediately_after_a_correction():
    """The user may still be mid-correction: 'New York... no, Newark'."""
    _q, state, _em, reg, coord = build()
    state.bump_epoch("interruption", at_ms=2000)
    ok, reason = coord.may_issue(
        reg.get("book_flight"),
        {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"},
        now_ms=2000 + config.COMMITMENT_QUIET_MS - 50)
    assert not ok and reason.startswith("too_soon_after_correction")

    ok, _ = coord.may_issue(
        reg.get("book_flight"),
        {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"},
        now_ms=2000 + config.COMMITMENT_QUIET_MS + 10)
    assert ok


def test_invalid_args_are_refused_before_they_cost_a_round_trip():
    _q, _st, _em, reg, coord = build()
    ok, reason = coord.may_issue(reg.get("book_flight"), {"flight_id": "FL-1"},
                                 now_ms=1000)
    assert not ok and reason.startswith("invalid_args")
    assert coord.issue(reg.get("book_flight"), {"flight_id": "FL-1"},
                       now_ms=1000) is None


# ------------------------------------------------------ idempotency ledger
def test_second_identical_booking_is_refused_while_in_flight():
    q, _st, _em, reg, coord = build()
    args = {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"}
    first = coord.issue(reg.get("book_flight"), args, now_ms=1000)
    assert first is not None
    assert coord.ledger_state("book_flight", args) == LEDGER_IN_FLIGHT
    second = coord.issue(reg.get("book_flight"), args, now_ms=1100)
    assert second is None
    calls = [a for a in drain(q) if a["action"] == "tool_call"]
    assert len(calls) == 1


def test_second_identical_booking_is_refused_after_it_committed():
    _q, _st, _em, reg, coord = build()
    args = {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"}
    call_id = coord.issue(reg.get("book_flight"), args, now_ms=1000)
    coord.on_result(result_event(call_id, "book_flight", booking_id="BK-0001"),
                    now_ms=2000)
    assert coord.ledger_state("book_flight", args) == LEDGER_COMMITTED
    assert coord.issue(reg.get("book_flight"), args, now_ms=2100) is None


def test_a_different_booking_is_still_allowed():
    """The ledger must block duplicates, not block progress."""
    _q, _st, _em, reg, coord = build()
    a = coord.issue(reg.get("book_flight"),
                    {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"},
                    now_ms=1000)
    b = coord.issue(reg.get("book_flight"),
                    {"flight_id": "FL-DEN-2PM", "passenger_name": "Alice"},
                    now_ms=1100)
    assert a is not None and b is not None


# ------------------------------------------------------- M1 invalidation
def test_invalidation_cancels_calls_that_depend_on_a_changed_slot():
    _q, state, _em, reg, coord = build()
    state.set_slot("destination", "Boston", at_ms=800)
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Boston"},
                          now_ms=810, depends_on=["destination"])

    state.bump_epoch("interruption", at_ms=1500)
    state.set_slot("destination", "New York", at_ms=1500)
    cancelled = coord.invalidate(now_ms=1520)

    assert cancelled == [call_id]
    assert coord.calls[call_id].status == CANCELLED


def test_invalidation_keeps_calls_that_are_provably_unaffected():
    """A refinement that adds a slot must not kill work depending on another.
    Cancelling a still-valid call costs the task points it would have earned."""
    _q, state, _em, reg, coord = build()
    state.set_slot("destination", "Boston", at_ms=800)
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Boston"},
                          now_ms=810, depends_on=["destination"])

    state.bump_epoch("refinement", at_ms=1500)
    state.set_slot("passenger_name", "Alice", at_ms=1500)
    cancelled = coord.invalidate(now_ms=1520)

    assert cancelled == []
    assert coord.calls[call_id].is_pending


def test_calls_with_unknown_dependence_are_cancelled():
    """Cancellation is free under the scorer (cancel_noop is logged, not
    penalised); a stale completion is not. The costs are asymmetric, so the
    policy is too."""
    _q, state, _em, reg, coord = build()
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Boston"},
                          now_ms=810)  # no depends_on declared
    state.bump_epoch("interruption", at_ms=1500)
    state.set_slot("destination", "New York", at_ms=1500)
    assert coord.invalidate(now_ms=1520) == [call_id]


def test_calls_issued_after_the_bump_survive():
    _q, state, _em, reg, coord = build()
    state.bump_epoch("interruption", at_ms=1500)
    state.set_slot("destination", "New York", at_ms=1500)
    call_id = coord.issue(reg.get("flight_search"), {"destination": "New York"},
                          now_ms=1520, depends_on=["destination"])
    assert coord.invalidate(now_ms=1540) == []
    assert coord.calls[call_id].is_pending


def test_cancelling_an_in_flight_booking_releases_the_ledger():
    """conf_04: the agent must cancel a booking it already issued and then
    issue a DIFFERENT one. A cancelled call never reaches a completion, so it
    can never count as a duplicate - the key must not stay locked."""
    _q, state, _em, reg, coord = build()
    old = {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"}
    call_id = coord.issue(reg.get("book_flight"), old, now_ms=2120,
                          depends_on=["flight_id"])
    assert coord.ledger_state("book_flight", old) == LEDGER_IN_FLIGHT

    state.bump_epoch("interruption", at_ms=2600)
    state.set_slot("flight_id", "FL-DEN-2PM", at_ms=2600)
    assert coord.invalidate(now_ms=2620) == [call_id]
    assert coord.ledger_state("book_flight", old) == LEDGER_NONE

    new_id = coord.issue(reg.get("book_flight"),
                         {"flight_id": "FL-DEN-2PM", "passenger_name": "Alice"},
                         now_ms=2900, depends_on=["flight_id"])
    assert new_id is not None


def test_cancel_all_abandons_everything():
    _q, _st, _em, reg, coord = build()
    coord.issue(reg.get("flight_search"), {"destination": "Miami"}, now_ms=710)
    coord.issue(reg.get("lookup_manual"), {"query": "x"}, now_ms=720)
    assert len(coord.cancel_all(now_ms=1600)) == 2
    assert coord.pending() == []


# ------------------------------------------------------------- results
def test_result_for_a_cancelled_call_is_never_returned():
    """'Never act on stale results' - PROTOCOL.md section 1.4."""
    _q, state, _em, reg, coord = build()
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Boston"},
                          now_ms=810, depends_on=["destination"])
    state.bump_epoch("interruption", at_ms=1500)
    state.set_slot("destination", "New York", at_ms=1500)
    coord.invalidate(now_ms=1520)

    out = coord.on_result(
        result_event(call_id, "flight_search", flights=[{"flight_id": "FL-BOS-8AM"}]),
        now_ms=2600)
    assert out is None


def test_result_from_a_superseded_epoch_is_discarded():
    """Even without an explicit cancel: if the epoch moved on, the answer is
    to a question the user has withdrawn."""
    _q, state, _em, reg, coord = build()
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Boston"},
                          now_ms=810, depends_on=["destination"])
    state.bump_epoch("interruption", at_ms=1500)
    out = coord.on_result(result_event(call_id, "flight_search", flights=[]),
                          now_ms=1600)
    assert out is None


def test_live_result_is_returned():
    _q, _st, _em, reg, coord = build()
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Denver"},
                          now_ms=810)
    out = coord.on_result(
        result_event(call_id, "flight_search", flights=[{"flight_id": "FL-DEN-8AM"}]),
        now_ms=2600)
    assert out is not None and out.status == COMPLETED


def test_a_committed_booking_is_recorded_even_if_we_no_longer_want_it():
    """The ledger tracks what happened in the world, not what we intended."""
    _q, state, _em, reg, coord = build()
    args = {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"}
    call_id = coord.issue(reg.get("book_flight"), args, now_ms=2120)
    state.bump_epoch("interruption", at_ms=2600)
    out = coord.on_result(result_event(call_id, "book_flight", booking_id="BK-0001"),
                          now_ms=2700)
    assert out is None                                        # not actionable
    assert coord.ledger_state("book_flight", args) == LEDGER_COMMITTED  # but recorded


def test_unknown_call_id_is_ignored():
    _q, _st, _em, _reg, coord = build()
    assert coord.on_result(result_event("nope", "flight_search"), now_ms=100) is None


# ------------------------------------------------------------ retry policy
def test_read_only_failure_is_retried_once():
    """pub_08 injects a timeout on the first flight_search and expects >= 2."""
    _q, _st, _em, reg, coord = build()
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Seattle"},
                          now_ms=110)
    rec = coord.on_result(
        result_event(call_id, "flight_search", status="error", error="timeout"),
        now_ms=2310)
    assert rec is not None and rec.status == FAILED
    allowed, _ = coord.may_retry(rec)
    assert allowed
    assert coord.retry(rec, now_ms=2320) is not None


def test_read_only_is_not_retried_forever():
    _q, _st, _em, reg, coord = build()
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Seattle"},
                          now_ms=110)
    rec = coord.on_result(
        result_event(call_id, "flight_search", status="error", error="timeout"),
        now_ms=2310)
    second = coord.retry(rec, now_ms=2320)
    rec2 = coord.on_result(
        result_event(second, "flight_search", status="error", error="timeout"),
        now_ms=4000)
    allowed, reason = coord.may_retry(rec2)
    assert not allowed and reason == "attempts_exhausted"


def test_state_modifying_timeout_is_never_blind_retried():
    """conf_08. A timeout does not prove the booking failed upstream - the
    user pays for the duplicate whether or not the trace shows it."""
    _q, _st, _em, reg, coord = build()
    args = {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"}
    call_id = coord.issue(reg.get("book_flight"), args, now_ms=1320)
    rec = coord.on_result(
        result_event(call_id, "book_flight", status="error", error="timeout"),
        now_ms=2320)
    allowed, reason = coord.may_retry(rec)
    assert not allowed and reason == "state_modifying_outcome_unknown"
    assert coord.ledger_state("book_flight", args) == LEDGER_UNKNOWN
    # and the gate refuses a fresh attempt with the same arguments too
    assert coord.issue(reg.get("book_flight"), args, now_ms=2400) is None


def test_state_modifying_invalid_args_may_be_corrected_and_retried():
    """invalid_args is proof the call never reached commit semantics."""
    _q, _st, _em, reg, coord = build()
    args = {"flight_id": "FL-DEN-8AM", "passenger_name": "Alice"}
    call_id = coord.issue(reg.get("book_flight"), args, now_ms=1320)
    rec = coord.on_result(
        result_event(call_id, "book_flight", status="error", error="invalid_args"),
        now_ms=1500)
    assert coord.ledger_state("book_flight", args) == LEDGER_NONE
    allowed, reason = coord.may_retry(rec)
    assert not allowed and reason == "invalid_args_needs_new_arguments"


# --------------------------------------------------------------- plumbing
def test_pending_state_modifying_count_is_reported_to_the_emitter():
    """The emitter's truthfulness guard tightens while irreversible work is
    outstanding."""
    _q, _st, emitter, reg, coord = build()
    assert emitter.pending_state_modifying == 0
    call_id = coord.issue(reg.get("book_flight"),
                          {"flight_id": "FL-1", "passenger_name": "Alice"},
                          now_ms=1000)
    assert emitter.pending_state_modifying == 1
    coord.on_result(result_event(call_id, "book_flight", booking_id="BK-1"),
                    now_ms=2000)
    assert emitter.pending_state_modifying == 0


def test_successful_tool_unlocks_truthful_claims():
    _q, _st, emitter, reg, coord = build()
    call_id = coord.issue(reg.get("book_flight"),
                          {"flight_id": "FL-1", "passenger_name": "Alice"},
                          now_ms=1000)
    coord.on_result(result_event(call_id, "book_flight", booking_id="BK-1"),
                    now_ms=2000)
    assert "book_flight" in emitter.succeeded_tools


def test_every_emitted_action_is_protocol_valid():
    q, state, _em, reg, coord = build()
    call_id = coord.issue(reg.get("flight_search"), {"destination": "Boston"},
                          now_ms=810, depends_on=["destination"])
    state.bump_epoch("interruption", at_ms=1500)
    state.set_slot("destination", "New York", at_ms=1500)
    coord.invalidate(now_ms=1520)
    coord.issue(reg.get("flight_search"), {"destination": "New York"}, now_ms=1530)
    for action in drain(q):
        assert contract.validate_action(action) == [], action
