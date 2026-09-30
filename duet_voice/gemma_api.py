"""Gemma 4 through Google's API: the language model behind both of DUET's minds.

DUET's thinker is Gemma 4 26B-A4B-it (open weights, Apache 2.0), reached through
Google's Gemini API with the google-genai SDK. Google serves Gemma free of charge
on free-tier projects, with a limit of 16,000 input tokens per minute per model
per project. A benchmark recording gives DUET about 30 seconds after the request
ends before the room closes, so a call that waits out a rate limit, or dies on a
transient server error, is a failed recording. Four things here prevent that:

1. Several keys. GOOGLE_API_KEYS=key1,key2,... (or one GOOGLE_API_KEY); keys from
   different projects add up their limits. Each call goes to the key with the most
   room for that model.
2. A token budget per key and model (DUET_GEMMA_TPM, default 15000 of the 16000):
   a call is booked before it is sent and waits for room rather than being refused.
   If the API still refuses (429), that key rests for the delay the API asks for,
   and the call moves to another key at once.
3. Quick retries of transient failures (server errors), frequent on the free tier,
   inside one deadline per call (DUET_GEMMA_DEADLINE_S): past it, the recording's
   answer window has closed anyway, so the error is reported.
4. Backup requests. Now and then a request hangs with no answer at all (26 of about
   350 in one night's run, while answers took 3.1 s at the median and 7.1 s at the
   95th percentile). A request unanswered after DUET_GEMMA_HEDGE_S (6 s) is not
   abandoned: a second one goes out to the key with the most room, and whichever
   answers first is used. At most four are in flight; the rest are cancelled.

Errors that no retry can fix (a bad key, a model this key cannot use, a malformed
request) are raised at once.

    python -m duet_voice.gemma_api    # preflight: which keys and models work, does tool calling work?
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("duet.gemma")

# The models DUET uses, best first. The 31B is the fallback if Google stops serving
# the 26B-A4B to a key; it is slower (dense) but has its own per-model quota.
THINKER_MODEL = "gemma-4-26b-a4b-it"
MODELS = ["gemma-4-26b-a4b-it", "gemma-4-31b-it"]

TRANSIENT = {408, 500, 502, 503, 504}


def uses_vertex() -> bool:
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes")


def api_keys() -> List[str]:
    """GOOGLE_API_KEYS (comma-separated), else GOOGLE_API_KEY; duplicates removed."""
    raw = os.environ.get("GOOGLE_API_KEYS") or os.environ.get("GOOGLE_API_KEY") or ""
    keys: List[str] = []
    for k in raw.split(","):
        k = k.strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def _tpm() -> float:
    return float(os.environ.get("DUET_GEMMA_TPM", "15000"))


def _deadline_s() -> float:
    return float(os.environ.get("DUET_GEMMA_DEADLINE_S", "28"))


def _hedge_s() -> float:
    """How long a request may go unanswered before a backup request is sent."""
    return float(os.environ.get("DUET_GEMMA_HEDGE_S", "6"))


MAX_IN_FLIGHT = 4  # requests for one call at once: the first and up to three backups


# --- the token budget ---------------------------------------------------------------------

class _Window:
    """One key's input tokens for one model over the last 60 seconds."""

    def __init__(self) -> None:
        self.events: collections.deque = collections.deque()   # [time, tokens]
        self.rest_until = 0.0

    def used(self, now: float) -> float:
        while self.events and now - self.events[0][0] >= 60.0:
            self.events.popleft()
        return sum(e[1] for e in self.events)

    def free_in(self, need: float, now: float, tpm: float) -> float:
        """Seconds until `need` more tokens fit."""
        over = self.used(now) + need - tpm
        if over <= 0:
            return 0.05
        for t, tokens in self.events:
            over -= tokens
            if over <= 0:
                return max(0.05, t + 60.0 - now)
        return 60.0


class Pool:
    """The keys, each with a budget per model. Thread-safe: LiveKit runs each
    conversation in its own thread and event loop."""

    def __init__(self, n_keys: int, tpm: float) -> None:
        self.n = n_keys
        self.tpm = tpm
        self.lock = threading.Lock()
        self.windows: Dict[Tuple[int, str], _Window] = {}
        self.average: Dict[str, float] = {}

    def estimate(self, model: str) -> float:
        """Input tokens the next call to `model` will likely use: the recent average,
        or the size of one DUET thinker call (the instructions and 13 tools)."""
        return self.average.get(model, 2600.0)

    def _window(self, i: int, model: str) -> _Window:
        return self.windows.setdefault((i, model), _Window())

    def take(self, model: str, need: float) -> Tuple[Optional[int], Optional[list], float]:
        """(key index, booking, 0) when a key has room now; else (None, None, seconds to wait)."""
        with self.lock:
            now = time.monotonic()
            best, best_room, wait = None, -1.0, 60.0
            for i in range(self.n):
                w = self._window(i, model)
                if w.rest_until > now:
                    wait = min(wait, w.rest_until - now)
                    continue
                room = self.tpm - w.used(now)
                if room >= need:
                    if room > best_room:
                        best, best_room = i, room
                else:
                    wait = min(wait, w.free_in(need, now, self.tpm))
            if best is None:
                return None, None, max(0.05, wait)
            booking = [now, need]
            self._window(best, model).events.append(booking)
            return best, booking, 0.0

    def settle(self, model: str, booking: list, actual: Optional[float]) -> None:
        """Replace a booking with what the call really used (0: the API refused it)."""
        with self.lock:
            if actual is not None:
                booking[1] = actual
                if actual > 0:
                    old = self.average.get(model, actual)
                    self.average[model] = 0.8 * old + 0.2 * actual

    def rest(self, i: int, model: str, seconds: float) -> None:
        with self.lock:
            w = self._window(i, model)
            w.rest_until = max(w.rest_until, time.monotonic() + seconds)


_pool: Optional[Pool] = None
_pool_lock = threading.Lock()


def pool() -> Pool:
    global _pool
    with _pool_lock:
        n = 1 if uses_vertex() else max(1, len(api_keys()))
        if _pool is None or _pool.n != n:
            _pool = Pool(n, _tpm())
    return _pool


# --- clients --------------------------------------------------------------------------------

_clients: Dict[Tuple[int, int], Any] = {}
_clients_lock = threading.Lock()


def http_options():
    """No retries inside the SDK: generate() decides, because on a 429 the right
    move is another key, not the same key again."""
    from google.genai import types
    return types.HttpOptions(
        timeout=int(float(os.environ.get("DUET_GEMMA_REQUEST_TIMEOUT_S", "25")) * 1000),
        retry_options=types.HttpRetryOptions(attempts=1))


def client_for(i: int):
    """The client for key i, one per thread (each conversation has its own event loop)."""
    from google import genai
    slot = (i, threading.get_ident())
    with _clients_lock:
        if slot not in _clients:
            if uses_vertex():
                _clients[slot] = genai.Client(vertexai=True, project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                                              location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
                                              http_options=http_options())
            else:
                keys = api_keys()
                if not keys:
                    raise RuntimeError("no Google API key: set GOOGLE_API_KEY (or GOOGLE_API_KEYS=key1,key2)")
                _clients[slot] = genai.Client(api_key=keys[i % len(keys)], http_options=http_options())
        return _clients[slot]


def client():
    """A client on the first key (for simple calls such as listing models)."""
    return client_for(0)


def error_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    m = re.match(r"\s*(\d{3})\b", str(exc))
    return int(m.group(1)) if m else None


def retry_delay(exc: BaseException) -> float:
    """The delay a 429 asks for ("Please retry in 36.9s" / 'retryDelay': '36s')."""
    text = str(exc)
    m = re.search(r"retry in ([\d.]+)s", text) or re.search(r"retryDelay\W+([\d.]+)s", text)
    return min(60.0, float(m.group(1))) if m else 20.0


def _transient(exc: BaseException) -> bool:
    code = error_code(exc)
    if code in TRANSIENT:
        return True
    if code is not None:
        return False
    name = type(exc).__name__.lower()
    return any(w in name for w in ("timeout", "connect", "remoteprotocol", "readerror", "network"))


async def generate(model: str, contents: Any, config: Any, deadline_s: Optional[float] = None):
    """One generate_content call through the key pool (see the module notes)."""
    p = pool()
    start = time.monotonic()
    end = start + (deadline_s if deadline_s is not None else _deadline_s())
    need = p.estimate(model)
    last: Optional[BaseException] = None
    flights: Dict[Any, Tuple[int, list, float]] = {}   # request -> (key index, booking, time sent)
    pause_until = 0.0                                    # a short pause after a server error
    answered = False

    def send(i: int, booking: list) -> None:
        task = asyncio.ensure_future(
            client_for(i).aio.models.generate_content(model=model, contents=contents, config=config))
        flights[task] = (i, booking, time.monotonic())

    try:
        while True:
            now = time.monotonic()
            if now >= end:
                break
            newest = max((f[2] for f in flights.values()), default=0.0)
            if not flights:
                if now < pause_until:
                    await asyncio.sleep(min(pause_until, end) - now)
                    continue
                i, booking, wait = p.take(model, need)
                if i is None:
                    if now + wait > end:
                        why = ("every key is resting after Google refused it (rate limit; another program "
                               "using the same keys?)" if last is not None and error_code(last) == 429
                               else "this minute's input-token budget is spent")
                        raise RuntimeError("no key has room for %s within the deadline: %s%s" % (
                            model, why, " (last error: %s)" % str(last)[:160] if last else ""))
                    await asyncio.sleep(wait)
                    continue
                send(i, booking)
                continue
            hedge_at = newest + _hedge_s()
            if len(flights) < MAX_IN_FLIGHT and now >= hedge_at and end - now >= 3.0:
                # the newest request is unanswered for longer than answers take: send a backup
                # (only if a key has room right now; the requests in flight keep running)
                i, booking, _ = p.take(model, need)
                if i is not None:
                    log.info("gemma: %s unanswered after %.1f s; asking key %d as well", model, now - newest, i + 1)
                    send(i, booking)
                    continue
                hedge_at = now + 1.0  # no room for a backup yet: look again in a second
            timeout = end - now
            if len(flights) < MAX_IN_FLIGHT and hedge_at > now:
                timeout = min(timeout, hedge_at - now)
            done, _ = await asyncio.wait(list(flights), timeout=max(0.01, timeout),
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                i, booking, sent = flights.pop(task)
                if task.cancelled():  # not by us (we cancel only on the way out): treat it as a failure
                    last = RuntimeError("a request to %s on key %d was cancelled" % (model, i + 1))
                    pause_until = time.monotonic() + 0.3
                    continue
                exc = task.exception()
                if exc is None:
                    resp = task.result()
                    u = getattr(resp, "usage_metadata", None)
                    tokens = float(getattr(u, "prompt_token_count", 0) or 0) or None
                    p.settle(model, booking, tokens)
                    log.info("gemma: %s answered on key %d in %.1f s (%d input tokens)", model, i + 1,
                             time.monotonic() - sent, tokens or 0)
                    answered = True
                    return resp
                code = error_code(exc)
                if code == 429:
                    last = exc
                    p.settle(model, booking, 0)
                    p.rest(i, model, retry_delay(exc))
                    log.info("gemma: key %d refused %s (rate limit); resting it %.0f s", i + 1, model,
                             retry_delay(exc))
                elif _transient(exc):
                    last = exc
                    log.info("gemma: %s on key %d failed (%s); asking again", model, i + 1, str(exc)[:80])
                    pause_until = time.monotonic() + 0.3 + random.random() * 0.4
                else:
                    raise exc
        waiting = len(flights)
        raise RuntimeError("no answer from %s within %.0f s%s%s" % (
            model, end - start, " (%d request(s) unanswered)" % waiting if waiting else "",
            "; last error: %s" % str(last)[:160] if last else ""))
    finally:
        # Requests still in flight: backups after an answer, or all of them when the
        # plan was overtaken (the user spoke again). They still count against their keys.
        if flights:
            oldest = min(f[2] for f in flights.values())
            log.info("gemma: %s: %d request(s) %s after %.1f s", model, len(flights),
                     "no longer needed" if answered else "cancelled", time.monotonic() - oldest)
        for task in flights:
            task.cancel()


def generate_sync(model: str, contents: Any, config: Any = None, deadline_s: float = 60.0,
                  key: Optional[int] = None):
    """A blocking call (preflight, probes). `key`: that key only, no rotation."""
    end = time.monotonic() + deadline_s
    last: Optional[BaseException] = None
    while time.monotonic() < end:
        i = key if key is not None else (pool().take(model, 50)[0] or 0)
        try:
            return client_for(i).models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            last = exc
            if error_code(exc) == 429 and key is None:
                pool().rest(i, model, retry_delay(exc))
                time.sleep(1.0)
                continue
            if _transient(exc):
                time.sleep(0.5)
                continue
            raise
    raise last or RuntimeError("no answer within %.0f s" % deadline_s)


# --- sampling -------------------------------------------------------------------------------

def sampling(model: str) -> dict:
    """Google's recommended sampling for Gemma 4 (temperature 1.0, top-p 0.95,
    top-k 64) with a fixed seed; DUET_TEMPERATURE overrides the temperature."""
    from .config import CONFIG
    temperature = float(CONFIG.temperature) if CONFIG.temperature != "" else 1.0
    return {"temperature": temperature, "top_p": 0.95, "top_k": 64, "seed": CONFIG.seed}


def thinking_config(model: str, level: str):
    """Gemma 4 thinks on its own; the API refuses a thinking budget or level for it."""
    return None


# --- which model serves each role -------------------------------------------------------

_resolved: dict = {}
_notes: list = []


def probe(model: str, key: Optional[int] = None) -> tuple:
    """One tiny request. (verdict, detail): ok | unavailable | rate_limited | no_access | bad_key | error."""
    try:
        generate_sync(model, "Reply with the word ok.", deadline_s=45.0, key=key)
        return "ok", ""
    except Exception as exc:
        text, code = str(exc), error_code(exc)
        if code == 429 or "RESOURCE_EXHAUSTED" in text[:80]:
            return "rate_limited", text[:300]
        if code == 402 or "prepayment" in text or "billing" in text.lower():
            return "no_access", text[:300]
        if code == 404 or "not found" in text.lower() or "no longer available" in text:
            return "unavailable", text[:300]
        if code in (401, 403) or "API key" in text or "API_KEY" in text:
            return "bad_key", text[:300]
        return "error", text[:300]


def resolve(role: str, preferred: str) -> str:
    """The Gemma model this key pool can use: the preferred one, or the other one."""
    if role in _resolved:
        return _resolved[role]
    chosen = preferred
    for model in [preferred] + [m for m in MODELS if m != preferred]:
        verdict, detail = probe(model)
        if verdict == "unavailable":
            _notes.append("%s %s is not served to this key; trying the next model" % (role, model))
            continue
        if verdict == "no_access":
            _notes.append("%s %s: this key's project cannot use Gemma (%s). Google serves Gemma free "
                          "on free-tier projects: create a key in a project without billing at "
                          "aistudio.google.com" % (role, model, detail[:120]))
        elif verdict == "bad_key":
            _notes.append("%s %s: the API key was refused (%s)" % (role, model, detail[:120]))
        elif verdict in ("rate_limited", "error"):
            _notes.append("%s %s: probe %s (%s); keeping it" % (role, model, verdict, detail[:120]))
        chosen = model
        break
    _resolved[role] = chosen
    return chosen


def resolved(role: str, preferred: str) -> str:
    return _resolved.get(role, preferred)


# --- preflight --------------------------------------------------------------------------------

def check() -> Dict[str, Any]:
    """Every key can reach Gemma, and tool calling works (a correction included)."""
    from google.genai import types
    from .config import CONFIG
    out: Dict[str, Any] = {"backend": "gemma", "thinker": CONFIG.thinker_model, "keys": len(api_keys()),
                           "keys_ok": 0, "notes": []}
    if not api_keys() and not uses_vertex():
        out["error"] = ("no Google API key: set GOOGLE_API_KEY (one key) or GOOGLE_API_KEYS=key1,key2 "
                        "(keys from different projects add up their limits)")
        return out
    for i in range(pool().n):
        verdict, detail = probe(CONFIG.thinker_model, key=i)
        if verdict in ("ok", "rate_limited"):
            out["keys_ok"] += 1
        else:
            out["notes"].append("key %d: %s (%s)" % (i + 1, verdict, detail[:160]))
    if out["keys_ok"] == 0:
        out["error"] = ("no key can use %s. Google serves Gemma 4 free of charge on free-tier projects: "
                        "create an API key in a project without billing at aistudio.google.com"
                        % CONFIG.thinker_model)
        return out
    tool = types.Tool(function_declarations=[types.FunctionDeclaration(
        name="track_order", description="Get the shipping status of an order.",
        parameters_json_schema={"type": "object", "properties": {"order_id": {"type": "string"}},
                                "required": ["order_id"]})])
    try:
        resp = generate_sync(CONFIG.thinker_model, "Could you track my order? It's K 7, no wait, K 4 Q 2.",
                             types.GenerateContentConfig(
                                 tools=[tool], **sampling(CONFIG.thinker_model),
                                 automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
                             deadline_s=90.0)
        calls = [(p.function_call.name, dict(p.function_call.args or {}))
                 for p in (resp.candidates[0].content.parts or []) if p.function_call]
        out["tool_calling"] = "ok" if calls and calls[0][0] == "track_order" else "unexpected: %s" % calls
        if out["tool_calling"] != "ok":
            out["notes"].append("the tool-calling test did not call track_order")
    except Exception as exc:
        out["error"] = "tool-calling test failed (%s)" % str(exc)[:200]
    return out


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"))
    result = check()
    print(json.dumps(result))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
