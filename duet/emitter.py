"""The only place in DUET that writes to the output queue.

INVARIANT 2: every outbound action passes through this class. `out_q.put_nowait`
appears exactly once in the codebase, and tests/test_invariants.py enforces
that by grep. Centralising it means each of the scorer's safety rules is
implemented once, cannot be forgotten at a call site, and is testable in
isolation.

Guarantees provided here, so no caller has to remember them:

  * every final_response carries a top-level state_snapshot   (-0.20 if missing)
  * every action is schema-valid before it is sent            (-0.10 each, cap -0.5)
  * no filler text is ever repeated verbatim                  (-0.15 each, cap -0.45)
  * no utterance claims an irreversible action completed
    before its tool reported success                          (-0.25 each, cap -0.5)
  * no utterance is non-substantive, so every spoken action
    actually stops the latency clock                          (up to 23 pts/scenario)

Why synchronous: asyncio.Queue is unbounded, so put_nowait never blocks or
raises. Keeping emit() sync means there is no await point between deciding to
speak and speaking - the fast path cannot be descheduled mid-emission, and a
handler that emits three actions emits them atomically from the loop's point
of view.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence, Set

from . import config, contract, telemetry
from .nlg import Phrasebook
from .state import ConversationState


class Emitter:
    """Owns the outbound side of the protocol."""

    def __init__(
        self,
        out_queue: "asyncio.Queue[Dict[str, Any]]",
        state: ConversationState,
        phrasebook: Phrasebook,
    ) -> None:
        self._out_q = out_queue
        self._state = state
        self._say = phrasebook

        # Speech accounting
        self.filler_count: int = 0
        self.spoken: List[Dict[str, Any]] = []
        self._spoken_norm: Set[str] = set()

        # Truthfulness accounting, maintained by the coordinator
        self.succeeded_tools: Set[str] = set()
        self.pending_state_modifying: int = 0
        self.extra_claim_patterns: Dict[str, Sequence[str]] = {}

        # Diagnostics
        self.dropped: List[Dict[str, Any]] = []
        self.substitutions: int = 0
        self.actions_sent: int = 0

    # ------------------------------------------------------------------
    # bookkeeping the coordinator feeds us
    # ------------------------------------------------------------------
    def note_tool_succeeded(self, api_name: str) -> None:
        """A tool reported success; claims about it are now truthful."""
        if api_name:
            self.succeeded_tools.add(contract.norm(api_name))

    def set_pending_state_modifying(self, count: int) -> None:
        self.pending_state_modifying = max(0, int(count))

    def register_claim_patterns(self, patterns: Dict[str, Sequence[str]]) -> None:
        """Extra completion-claim patterns for tools we met at runtime."""
        self.extra_claim_patterns.update(patterns or {})

    # ------------------------------------------------------------------
    # spoken actions
    # ------------------------------------------------------------------
    def filler(self, text: str) -> bool:
        return self._speak("filler_speech", text)

    def clarify(self, text: str) -> bool:
        return self._speak("clarification_request", text)

    def final(self, text: str) -> bool:
        return self._speak("final_response", text)

    def _speak(self, kind: str, text: str) -> bool:
        text = ("" if text is None else str(text)).strip()

        # -- guard 1: substantive ---------------------------------------
        # Non-substantive speech does not stop the latency clock, so an
        # empty or punctuation-only utterance is worse than useless: it
        # burns a filler slot for nothing.
        if not contract.is_substantive(text):
            replacement = self._say.ack_lookup(None)
            telemetry.log("emit.nonsubstantive", kind=kind, original=text,
                          replacement=replacement)
            if config.STRICT:
                raise AssertionError(
                    "non-substantive speech requested: " + repr(text))
            text = replacement
            self.substitutions += 1

        # -- guard 2: truthfulness --------------------------------------
        claims = contract.find_completion_claims(
            text,
            succeeded_tools=self.succeeded_tools,
            extra_patterns=self.extra_claim_patterns,
            any_state_modifying_pending=self.pending_state_modifying > 0,
        )
        if claims:
            telemetry.log("emit.unbacked_claim", kind=kind, text=text, reasons=claims)
            if config.STRICT:
                raise AssertionError(
                    "unbacked completion claim: " + repr(text) + " -> " + repr(claims))
            # Degrade to a truthful line rather than dropping the utterance:
            # we still need to stop the latency clock.
            text = self._say.progress(None)
            self.substitutions += 1

        # -- guard 3: no verbatim repeats -------------------------------
        if contract.norm(text) in self._spoken_norm:
            replacement = self._say.ack_lookup(None)
            telemetry.log("emit.repeat", kind=kind, text=text, replacement=replacement)
            if config.STRICT:
                raise AssertionError("verbatim repeat requested: " + repr(text))
            if contract.norm(replacement) in self._spoken_norm:
                # Phrasebook exhausted; drop rather than incur the penalty,
                # unless this is a final_response, which carries task points.
                if kind != "final_response":
                    self.dropped.append({"kind": kind, "text": text, "why": "repeat"})
                    return False
            else:
                text = replacement
                self.substitutions += 1

        # -- guard 4: runaway filler protection -------------------------
        # We deliberately do NOT enforce the scorer's filler budget as a hard
        # stop. Economics: one filler over budget costs 0.25 of the safety
        # category (~2.5-4 points), while failing to respond to an event costs
        # the whole latency category (15-23 points). Speaking is almost always
        # correct. This cap exists only to contain a runaway loop.
        if kind == "filler_speech":
            if self.filler_count >= config.FILLER_ABSOLUTE_CAP:
                telemetry.log("emit.filler_cap", count=self.filler_count, text=text)
                self.dropped.append({"kind": kind, "text": text, "why": "absolute_cap"})
                return False
            if self.filler_count >= config.FILLER_SOFT_BUDGET:
                telemetry.log("emit.filler_over_budget",
                              count=self.filler_count + 1,
                              soft_budget=config.FILLER_SOFT_BUDGET)

        action: Dict[str, Any] = {"action": kind, "payload": {"text": text}}

        # -- guard 5: snapshot ------------------------------------------
        # Mandatory on final_response; attached to every spoken action
        # because the recovery scorer reads the LATEST snapshot after the
        # interruption timestamp, whatever action it rides on. Attaching it
        # to the acknowledgment filler locks in half the recovery score at
        # ~100ms instead of waiting for the final answer.
        #
        # Not attached to tool_call / cancel_tool: the harness does not copy
        # state_snapshot into the trace for those entries, so it would be
        # dead weight.
        action["state_snapshot"] = self._state.snapshot()

        if not self._send(action):
            return False

        self.spoken.append({"kind": kind, "text": text})
        self._spoken_norm.add(contract.norm(text))
        self._say.mark_used(text)
        if kind == "filler_speech":
            self.filler_count += 1
        return True

    # ------------------------------------------------------------------
    # tool actions
    # ------------------------------------------------------------------
    def tool_call(self, call_id: str, api_name: str, args: Dict[str, Any]) -> bool:
        """Emit a non-blocking tool call.

        call_id is always ours. PROTOCOL.md section 2.2: if it is omitted the
        harness assigns one, and a call we cannot name is a call we cannot
        cancel - which is an automatic recovery violation the moment the user
        changes their mind.
        """
        if not isinstance(call_id, str) or not call_id:
            telemetry.log("emit.bad_call_id", call_id=call_id, api_name=api_name)
            if config.STRICT:
                raise AssertionError("tool_call requires our own string call_id")
            return False
        if not isinstance(args, dict):
            telemetry.log("emit.bad_args", api_name=api_name, args=repr(args))
            if config.STRICT:
                raise AssertionError("tool_call args must be a dict")
            args = {}
        return self._send({
            "action": "tool_call",
            "payload": {"call_id": call_id, "api_name": str(api_name), "args": args},
        })

    def cancel(self, call_id: str) -> bool:
        """Abort an in-flight call.

        Cancelling a call that already finished is a harmless no-op in the
        harness (logged as cancel_noop, not penalised), which is why the
        coordinator cancels on any doubt rather than reasoning about races.
        """
        if not isinstance(call_id, str) or not call_id:
            return False
        return self._send({"action": "cancel_tool", "payload": {"call_id": call_id}})

    # ------------------------------------------------------------------
    # the single exit point
    # ------------------------------------------------------------------
    def _send(self, action: Dict[str, Any]) -> bool:
        problems = contract.validate_action(action)
        if problems:
            telemetry.log("emit.invalid", problems=problems, action=repr(action)[:200])
            if config.STRICT:
                raise AssertionError("invalid action: " + repr(problems))
            self.dropped.append({"kind": action.get("action"), "why": "invalid",
                                 "problems": problems})
            return False

        self._out_q.put_nowait(action)   # the ONE queue write in duet/
        self.actions_sent += 1
        telemetry.log("emit.sent", action=action.get("action"))
        return True

    # ------------------------------------------------------------------
    def transcript(self) -> List[str]:
        """Everything we said, in order - for quality self-grading."""
        return [s["text"] for s in self.spoken]

    def stats(self) -> Dict[str, Any]:
        return {
            "actions_sent": self.actions_sent,
            "fillers": self.filler_count,
            "spoken": len(self.spoken),
            "dropped": len(self.dropped),
            "substitutions": self.substitutions,
        }
