"""The DUET coordinator: when is the agent allowed to act?

The FDB-v3 paper names the failure that costs every published system the most:
"models commit intermediate parameters before the correction arrives". This
module is the answer, carried over from DUET's first version:

* **Epochs (M1).** Every time the user starts speaking, the epoch advances. Work
  planned under an older epoch was planned on words the user may be changing.
* **Commit gate (M2).** No tool runs while the user is speaking, and none runs
  until the user has been quiet for a short hold after their last word. A call
  that meets new speech at the gate is superseded: it is never executed and never
  logged, so a stale, pre-correction value can never reach a backend.
* **Idempotency ledger (M2).** An identical call already made in this
  conversation is answered from the ledger instead of being sent again. For a
  state-changing tool this is the guarantee "never perform the same action
  twice"; for a read-only tool it saves a round trip.
* **Failure policy.** A read-only call that fails is retried once. A
  state-changing call is never blindly re-sent: if its outcome is unknown, the
  ledger remembers that, and the thinker is told to say so and offer a human.

Everything here is per conversation. Nothing is shared between rooms.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


class Superseded(Exception):
    """The user spoke again before a planned call reached the gate."""


@dataclass
class CallRecord:
    tool: str
    args: Dict[str, Any]
    epoch: int
    started: float
    finished: float = 0.0
    outcome: str = "pending"  # ok | error | unknown | superseded | cached
    result: Any = None
    error: str = ""


def canonical(tool: str, args: Dict[str, Any]) -> str:
    """Idempotency key: the tool plus its arguments, case- and order-insensitive."""
    def norm(v: Any) -> Any:
        if isinstance(v, str):
            return " ".join(v.lower().split())
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, dict):
            return {k: norm(x) for k, x in sorted(v.items())}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v
    clean = {k: norm(v) for k, v in sorted(args.items()) if v is not None}
    return tool + ":" + json.dumps(clean, sort_keys=True, default=str)


# Words that say the speaker is revising (a later value may replace an earlier
# one) or has not finished (the sentence dangles). Generic English, no domain.
_REVISING = re.compile(r"\b(no,? wait|wait,? no|actually|i mean|scratch that|changed? my mind|"
                       r"on second thought|instead|make (?:it|that)|rather|correction|sorry,? i meant)", re.I)
_DANGLING = re.compile(r"(\b(and|or|but|so|then|also|because|with|to|for|of|the|a|an|um+|uh+|er+|"
                       r"like|let me (?:think|see|check)|i mean)|,|\.\.\.|-)\s*$", re.I)


@dataclass
class Coordinator:
    commit_hold_s: float = 1.1          # quiet time before any tool may run
    revising_hold_s: float = 1.8        # when the user has been correcting themselves
    dangling_hold_s: float = 2.2        # when the last words leave the sentence open
    clock: Callable[[], float] = time.monotonic

    epoch: int = 0
    committed_epoch: int = -1           # the epoch whose user turn the listener has closed
    user_speaking: bool = False
    last_speech_end: float = 0.0
    open_utterance: str = ""            # what the user said since the agent last acted
    ledger: Dict[str, CallRecord] = field(default_factory=dict)
    history: List[CallRecord] = field(default_factory=list)

    # -- perception events -------------------------------------------------------
    def user_started_speaking(self) -> None:
        self.epoch += 1
        self.user_speaking = True

    def user_stopped_speaking(self) -> None:
        self.user_speaking = False
        self.last_speech_end = self.clock()

    def heard(self, text: str) -> None:
        """A final transcript segment from the ears.

        Words that arrive after the turn was closed are the end of a segment that
        was still being transcribed when the end-of-turn detector fired (in our
        dry run, a third of all turns closed that way, often right before a
        correction: "...from checking. Wait, no, make it savings"). A plan made
        without them is stale, so they advance the epoch exactly as new speech
        does; the listener then closes a new turn that includes them."""
        text = (text or "").strip()
        if not text:
            return
        if self.committed_epoch == self.epoch and not self.user_speaking:
            self.epoch += 1
        self.open_utterance = (self.open_utterance + " " + text).strip()

    def agent_acted(self) -> None:
        self.open_utterance = ""

    def turn_committed(self) -> None:
        """The listener decided the user's turn is over (end of turn detected)."""
        self.committed_epoch = self.epoch

    async def wait_committed(self, epoch: Optional[int] = None) -> int:
        """Planning may start early, during a pause; acting may not. Wait until the
        turn the plan was made in is closed. Raises Superseded if the user resumes."""
        epoch = self.epoch if epoch is None else epoch
        while self.committed_epoch != epoch:
            if self.epoch != epoch:
                raise Superseded()
            await asyncio.sleep(0.03)
        return epoch

    def hold(self) -> float:
        """How long the user must have been quiet before a call may commit."""
        text = self.open_utterance
        if _DANGLING.search(text):
            return self.dangling_hold_s
        if _REVISING.search(text):
            return self.revising_hold_s
        return self.commit_hold_s

    # -- the gate -------------------------------------------------------------------
    async def gate(self, epoch: Optional[int] = None) -> int:
        """Wait until the user has been quiet for the commit hold.

        `epoch` is the epoch the plan was made in (default: now). A call from a
        plan made before the words changed (the user spoke again, or late words
        arrived) raises Superseded, even if the new turn has since closed: it is
        never executed under the newer turn. Returns the epoch it commits under."""
        epoch = await self.wait_committed(epoch)
        while True:
            if self.user_speaking or self.epoch != epoch:
                raise Superseded()
            hold = self.hold()
            quiet_for = self.clock() - self.last_speech_end
            if quiet_for >= hold:
                return epoch
            await asyncio.sleep(min(0.05, hold - quiet_for))

    # -- execution with the ledger and the failure policy -----------------------------
    async def execute(
        self,
        tool: str,
        args: Dict[str, Any],
        run: Callable[[], Awaitable[Any]],
        *,
        state_changing: bool,
        timeout_s: float,
        on_committed: Optional[Callable[[CallRecord], None]] = None,
        epoch: Optional[int] = None,
        use_ledger: bool = True,
    ) -> CallRecord:
        key = canonical(tool, args)
        previous = self.ledger.get(key) if use_ledger else None
        if previous is not None and previous.outcome in ("ok", "unknown"):
            cached = CallRecord(tool, args, self.epoch, self.clock(), self.clock(),
                                "cached", previous.result, previous.error)
            self.history.append(cached)
            return cached

        epoch = await self.gate(epoch)
        record = CallRecord(tool, args, epoch, time.time())
        self.history.append(record)
        # Past the gate the call is committed. If the conversation is interrupted
        # now (the user barges in, the reply is cancelled), the call still runs to
        # completion and is recorded in the ledger and the log: dropping it would
        # leave an action that happened unaccounted for, and a re-plan could then
        # perform it a second time.
        committed = asyncio.ensure_future(
            self._run_committed(key, record, run, state_changing, timeout_s, on_committed, use_ledger))
        return await asyncio.shield(committed)

    async def _run_committed(self, key, record, run, state_changing, timeout_s, on_committed, use_ledger=True) -> CallRecord:
        attempts = 1 if state_changing else 2
        for attempt in range(attempts):
            try:
                record.result = await asyncio.wait_for(run(), timeout=timeout_s)
                record.outcome = "ok"
                break
            except asyncio.TimeoutError:
                record.error = "timed out after %.0f s" % timeout_s
                # A state-changing call that timed out may still have happened.
                record.outcome = "unknown" if state_changing else "error"
            except Exception as exc:  # the backend refused or crashed
                record.error = "%s: %s" % (type(exc).__name__, exc)
                record.outcome = "error"
            if attempt + 1 < attempts:
                await asyncio.sleep(0.3)
        record.finished = time.time()
        if use_ledger and record.outcome in ("ok", "unknown"):
            self.ledger[key] = record
        if on_committed is not None and record.outcome == "ok":
            on_committed(record)
        return record
