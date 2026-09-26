"""The DUET coordinator: the commit gate, the ledger and the failure policy.

These are the mechanisms that keep a stale, pre-correction value from ever
reaching a backend, and an action from ever running twice.
"""

import asyncio

import pytest

from duet_voice.coordinator import Coordinator, Superseded, canonical


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def run(coro):
    return asyncio.run(coro)


def make(**kw):
    clock = Clock()
    c = Coordinator(clock=clock, **kw)
    c.user_started_speaking()
    c.user_stopped_speaking()
    return c, clock


def test_gate_waits_for_the_turn_to_close():
    c, clock = make(commit_hold_s=0.0)

    async def go():
        task = asyncio.ensure_future(c.gate())
        await asyncio.sleep(0.1)
        assert not task.done()  # planned during a pause: not allowed to act yet
        c.turn_committed()
        return await asyncio.wait_for(task, 1)

    assert run(go()) == c.epoch


def test_gate_supersedes_when_the_user_resumes():
    c, clock = make(commit_hold_s=5.0)
    c.turn_committed()

    async def go():
        task = asyncio.ensure_future(c.gate())
        await asyncio.sleep(0.1)
        c.user_started_speaking()  # "...no wait, Milan"
        with pytest.raises(Superseded):
            await asyncio.wait_for(task, 1)

    run(go())


def test_hold_is_longer_while_the_user_is_revising_or_mid_sentence():
    c, _ = make(commit_hold_s=1.0, revising_hold_s=2.0, dangling_hold_s=3.0)
    c.heard("book a flight to Rome, no wait, Milan.")
    assert c.hold() == 2.0
    c.agent_acted()
    c.heard("search for apartments and")
    assert c.hold() == 3.0
    c.agent_acted()
    c.heard("track order ABC123.")
    assert c.hold() == 1.0


def test_an_identical_call_is_answered_from_the_ledger_not_repeated():
    c, clock = make(commit_hold_s=0.0)
    c.turn_committed()
    runs = []

    async def backend():
        runs.append(1)
        return {"status": "success"}

    async def go():
        first = await c.execute("add_to_cart", {"product_id": "P1", "quantity": 2}, backend,
                                state_changing=True, timeout_s=1)
        second = await c.execute("add_to_cart", {"quantity": 2.0, "product_id": "p1 "}, backend,
                                 state_changing=True, timeout_s=1)
        return first, second

    first, second = run(go())
    assert first.outcome == "ok" and second.outcome == "cached"
    assert len(runs) == 1


def test_a_failed_read_is_retried_once_a_failed_write_is_not():
    c, _ = make(commit_hold_s=0.0)
    c.turn_committed()
    calls = {"read": 0, "write": 0}

    async def flaky_read():
        calls["read"] += 1
        if calls["read"] == 1:
            raise ConnectionError("backend hiccup")
        return {"status": "success"}

    async def failing_write():
        calls["write"] += 1
        raise ConnectionError("backend down")

    async def go():
        r = await c.execute("track_order", {"order_id": "X"}, flaky_read, state_changing=False, timeout_s=1)
        w = await c.execute("book_flight", {"passenger_name": "A"}, failing_write, state_changing=True, timeout_s=1)
        return r, w

    r, w = run(go())
    assert r.outcome == "ok" and calls["read"] == 2
    assert w.outcome == "error" and calls["write"] == 1


def test_a_write_that_times_out_is_remembered_as_unknown_and_never_resent():
    c, _ = make(commit_hold_s=0.0)
    c.turn_committed()
    sent = []

    async def slow_write():
        sent.append(1)
        await asyncio.sleep(1)

    async def go():
        first = await c.execute("modify_autopay", {"bill_type": "phone", "source_account": "savings"},
                                slow_write, state_changing=True, timeout_s=0.05)
        again = await c.execute("modify_autopay", {"bill_type": "phone", "source_account": "savings"},
                                slow_write, state_changing=True, timeout_s=0.05)
        return first, again

    first, again = run(go())
    assert first.outcome == "unknown"
    assert again.outcome == "cached" and len(sent) == 1


def test_a_committed_call_survives_a_barge_in_and_is_recorded():
    """The user interrupts while a booking is executing: the reply is cancelled,
    but the booking that already started must finish and be recorded, so a
    re-plan answers it from the ledger instead of booking twice."""
    c, _ = make(commit_hold_s=0.0)
    c.turn_committed()
    logged, sent = [], []

    async def slow_booking():
        sent.append(1)
        await asyncio.sleep(0.2)
        return {"status": "success", "booking_ref": "B1"}

    async def go():
        task = asyncio.ensure_future(c.execute("book_flight", {"passenger_name": "A"}, slow_booking,
                                               state_changing=True, timeout_s=2,
                                               on_committed=logged.append))
        await asyncio.sleep(0.05)
        task.cancel()  # the reply that owned the call is gone
        await asyncio.sleep(0.3)
        again = await c.execute("book_flight", {"passenger_name": "A"}, slow_booking,
                                state_changing=True, timeout_s=2)
        return again

    again = run(go())
    assert len(sent) == 1 and len(logged) == 1
    assert again.outcome == "cached"


def test_canonical_key_ignores_case_order_spacing_and_nulls():
    assert canonical("t", {"a": "New  York", "b": 2.0, "c": None}) == canonical("t", {"b": 2, "a": "new york"})
