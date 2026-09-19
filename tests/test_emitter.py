"""Tests for Invariant 2: every action leaves through the emitter, correct.

These are the safety-category rules, tested in isolation so we never have to
rediscover them by reading a score report.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duet import config, contract, telemetry
from duet.emitter import Emitter
from duet.nlg import Phrasebook
from duet.state import ConversationState


def make(strict: bool = False):
    telemetry.reset()
    config.STRICT = strict
    q: asyncio.Queue = asyncio.Queue()
    st = ConversationState()
    pb = Phrasebook()
    return q, st, pb, Emitter(q, st, pb)


def drain(q: asyncio.Queue):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.fixture(autouse=True)
def _restore_strict():
    original = config.STRICT
    yield
    config.STRICT = original


# ---------------------------------------------------------------- snapshots
def test_final_response_always_carries_a_snapshot():
    q, st, _pb, em = make()
    st.set_intent("book_flight")
    st.set_slot("destination", "Denver")
    assert em.final("Found a flight to Denver.")
    action = drain(q)[0]
    assert action["state_snapshot"] == {"intent": "book_flight",
                                        "slots": {"destination": "Denver"}}
    assert contract.validate_action(action) == []


def test_every_spoken_action_carries_a_snapshot():
    """Recovery reads the LATEST snapshot after the interruption, on whatever
    action it rides. Attaching to the ack filler locks in the state half of
    the recovery score immediately."""
    q, st, _pb, em = make()
    st.set_slot("destination", "New York")
    em.filler("Got it - switching to New York.")
    em.clarify("Did you say Austin or Boston?")
    for action in drain(q):
        assert isinstance(action.get("state_snapshot"), dict)


def test_tool_actions_do_not_carry_snapshots():
    """The harness does not copy state_snapshot into the trace for tool_call
    or cancel_tool entries, so attaching one is dead weight."""
    q, _st, _pb, em = make()
    em.tool_call("c1", "flight_search", {"destination": "Denver"})
    em.cancel("c1")
    for action in drain(q):
        assert "state_snapshot" not in action


# ------------------------------------------------------------- truthfulness
def test_unbacked_completion_claim_is_replaced_not_sent():
    q, _st, _pb, em = make(strict=False)
    assert em.final("Booked! You are all set.")
    text = drain(q)[0]["payload"]["text"]
    assert "booked" not in contract.norm(text), text
    assert contract.is_substantive(text)


def test_unbacked_claim_raises_in_strict_mode():
    _q, _st, _pb, em = make(strict=True)
    with pytest.raises(AssertionError):
        em.final("Booked!")


def test_claim_allowed_once_tool_succeeded():
    q, _st, _pb, em = make(strict=True)
    em.note_tool_succeeded("book_flight")
    assert em.final("Booked - your confirmation is BK-0001.")
    assert "booked" in contract.norm(drain(q)[0]["payload"]["text"])


def test_promise_is_allowed_before_completion():
    q, _st, _pb, em = make(strict=True)
    assert em.filler("I'll get that booked now.")
    assert "booked" in contract.norm(drain(q)[0]["payload"]["text"])


# ------------------------------------------------------------ substantive
def test_non_substantive_speech_is_replaced():
    q, _st, _pb, em = make(strict=False)
    assert em.filler("...")
    text = drain(q)[0]["payload"]["text"]
    assert contract.is_substantive(text), text


def test_empty_speech_is_replaced_not_dropped():
    q, _st, _pb, em = make(strict=False)
    assert em.filler("")
    assert contract.is_substantive(drain(q)[0]["payload"]["text"])


# ---------------------------------------------------------------- repeats
def test_verbatim_filler_repeat_is_avoided():
    q, _st, _pb, em = make(strict=False)
    em.filler("One moment.")
    em.filler("One moment.")
    texts = [a["payload"]["text"] for a in drain(q)]
    assert len(set(contract.norm(t) for t in texts)) == len(texts), texts


def test_phrasebook_never_repeats_even_when_pools_are_exhausted():
    """Pools are finite. Past exhaustion the Phrasebook must still return
    unique, substantive text rather than repeating or returning None."""
    _q, _st, pb, _em = make()
    seen = set()
    producers = [
        lambda: pb.ack_lookup("flights to Denver"),
        lambda: pb.ack_lookup(None),
        lambda: pb.ack_correction("New York"),
        lambda: pb.ack_correction("Newark", old="New York"),
        lambda: pb.ack_retraction(),
        lambda: pb.ack_intent_change(),
        lambda: pb.ack_listen(),
        lambda: pb.ack_look(),
        lambda: pb.progress("the search"),
        lambda: pb.progress(None),
        lambda: pb.retrying(),
        lambda: pb.failed("the search"),
        lambda: pb.failed(None),
        lambda: pb.clarify_choice(["Austin", "Boston"]),
        lambda: pb.clarify_missing("destination"),
    ]
    for _ in range(12):
        for produce in producers:
            text = produce()
            key = contract.norm(text)
            assert contract.is_substantive(text), repr(text)
            assert key not in seen, "Phrasebook repeated: " + repr(text)
            seen.add(key)


# ------------------------------------------------------------ filler budget
def test_filler_budget_is_not_a_hard_stop():
    """Speaking over budget costs ~3 points; silence costs 15-23. We speak."""
    q, _st, _pb, em = make(strict=False)
    sent = sum(1 for i in range(config.FILLER_SOFT_BUDGET + 2)
               if em.filler("Checking item number " + str(i) + " for you."))
    assert sent == config.FILLER_SOFT_BUDGET + 2
    assert len(drain(q)) == config.FILLER_SOFT_BUDGET + 2


def test_runaway_fillers_are_capped():
    q, _st, _pb, em = make(strict=False)
    for i in range(config.FILLER_ABSOLUTE_CAP + 5):
        em.filler("Distinct filler number " + str(i) + " here.")
    assert len(drain(q)) == config.FILLER_ABSOLUTE_CAP
    assert em.filler_count == config.FILLER_ABSOLUTE_CAP


# ------------------------------------------------------------- tool actions
def test_tool_call_requires_our_own_call_id():
    _q, _st, _pb, em = make(strict=True)
    with pytest.raises(AssertionError):
        em.tool_call("", "flight_search", {})


def test_tool_call_shape_is_valid():
    q, _st, _pb, em = make()
    assert em.tool_call("c1", "flight_search", {"destination": "Denver"})
    action = drain(q)[0]
    assert action["payload"] == {"call_id": "c1", "api_name": "flight_search",
                                 "args": {"destination": "Denver"}}
    assert contract.validate_action(action) == []


def test_non_dict_args_rejected_in_strict_mode():
    _q, _st, _pb, em = make(strict=True)
    with pytest.raises(AssertionError):
        em.tool_call("c1", "flight_search", ["not", "a", "dict"])


def test_cancel_shape_is_valid():
    q, _st, _pb, em = make()
    assert em.cancel("c1")
    assert contract.validate_action(drain(q)[0]) == []


def test_every_emitted_action_validates():
    """Belt and braces: whatever we emit, the harness must accept."""
    q, st, _pb, em = make()
    st.set_slot("destination", "Denver")
    em.filler("Looking that up.")
    em.clarify("Which city did you want?")
    em.tool_call("c1", "flight_search", {"destination": "Denver"})
    em.cancel("c1")
    em.final("Here is what I found.")
    actions = drain(q)
    assert len(actions) == 5
    for a in actions:
        assert contract.validate_action(a) == [], a


def test_transcript_and_stats():
    _q, _st, _pb, em = make()
    em.filler("Looking that up.")
    em.final("Here is what I found.")
    assert em.transcript() == ["Looking that up.", "Here is what I found."]
    assert em.stats()["actions_sent"] == 2
    assert em.stats()["fillers"] == 1
