"""Gemma through Google's API: the key pool keeps a two-hour run inside the free
tier's per-minute token limit and inside each recording's answer window.

- a call goes to the key with room; a full key waits for room instead of failing;
- a refused call (429) rests that key for the delay asked and moves to another;
- transient server errors are retried; permanent ones are raised at once;
- nothing waits past the deadline (the recording's window would be gone).
"""

import asyncio
import time
from types import SimpleNamespace as NS

import pytest

from duet_voice import gemma_api


class ApiError(Exception):
    def __init__(self, code, text=""):
        super().__init__("%d %s" % (code, text))
        self.code = code


def fake_clients(monkeypatch, script):
    """script[key index] = list of results (an exception to raise, or tokens used)."""
    calls = []

    def client_for(i):
        async def generate_content(model, contents, config):
            calls.append(i)
            outcome = script[i].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return NS(usage_metadata=NS(prompt_token_count=outcome), key=i)
        return NS(aio=NS(models=NS(generate_content=generate_content)))
    monkeypatch.setattr(gemma_api, "client_for", client_for)
    return calls


def with_keys(monkeypatch, n, tpm=15000):
    monkeypatch.setenv("GOOGLE_API_KEYS", ",".join("key%d" % i for i in range(n)))
    monkeypatch.setenv("DUET_GEMMA_TPM", str(tpm))
    monkeypatch.setattr(gemma_api, "_pool", None)


def run(coro):
    return asyncio.run(coro)


def test_keys_come_from_either_variable_without_duplicates(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEYS", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "a")
    assert gemma_api.api_keys() == ["a"]
    monkeypatch.setenv("GOOGLE_API_KEYS", " a, b ,a,")
    assert gemma_api.api_keys() == ["a", "b"]


def test_calls_spread_over_keys_by_room_and_a_full_pool_waits():
    p = gemma_api.Pool(2, 5000)
    first, b1, _ = p.take("m", 3000)
    second, b2, _ = p.take("m", 3000)
    assert {first, second} == {0, 1}                  # the second call went to the emptier key
    none, _, wait = p.take("m", 3000)
    assert none is None and 0 < wait <= 60            # both full: wait, do not fail
    p.settle("m", b1, 1000)                           # it really used less
    assert p.take("m", 3000)[0] == first


def test_a_rate_limited_key_rests_and_the_call_moves_to_another(monkeypatch):
    with_keys(monkeypatch, 2)
    calls = fake_clients(monkeypatch, {0: [ApiError(429, "Please retry in 30s.")], 1: [2300]})
    resp = run(gemma_api.generate("gemma-4-26b-a4b-it", "hi", None, deadline_s=5))
    assert resp.key == 1 and calls == [0, 1]
    assert gemma_api.pool().windows[(0, "gemma-4-26b-a4b-it")].rest_until > time.monotonic() + 25


def test_transient_server_errors_are_retried(monkeypatch):
    with_keys(monkeypatch, 1)
    fake_clients(monkeypatch, {0: [ApiError(500, "INTERNAL"), ApiError(503), 2300]})
    assert run(gemma_api.generate("gemma-4-26b-a4b-it", "hi", None, deadline_s=10)).key == 0


def test_a_permanent_error_is_raised_at_once(monkeypatch):
    with_keys(monkeypatch, 2)
    calls = fake_clients(monkeypatch, {0: [ApiError(400, "INVALID_ARGUMENT")], 1: [2300]})
    with pytest.raises(ApiError):
        run(gemma_api.generate("gemma-4-26b-a4b-it", "hi", None, deadline_s=10))
    assert len(calls) == 1


def test_nothing_waits_past_the_deadline(monkeypatch):
    with_keys(monkeypatch, 1, tpm=3000)
    fake_clients(monkeypatch, {0: [2900, 2900]})
    run(gemma_api.generate("gemma-4-26b-a4b-it", "hi", None, deadline_s=5))   # the key is now full
    start = time.monotonic()
    with pytest.raises(RuntimeError):
        run(gemma_api.generate("gemma-4-26b-a4b-it", "hi", None, deadline_s=1.0))
    assert time.monotonic() - start < 1.5


def test_the_delay_a_429_asks_for_is_read():
    assert gemma_api.retry_delay(Exception("Please retry in 36.96s.")) == pytest.approx(36.96)
    assert gemma_api.retry_delay(Exception("{'retryDelay': '8s'}")) == 8.0
    assert gemma_api.retry_delay(Exception("quota")) == 20.0
