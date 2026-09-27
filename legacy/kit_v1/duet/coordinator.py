"""The coordination layer: what makes the agent safe to interrupt.

This is where the theme's "concurrency and state-consistency challenge" lives,
and it is the only part of DUET that needs no model at all. It owns four
mechanisms:

M1  Epoch invalidation. Every call records the epoch it was issued under. When
    an interruption bumps the epoch, calls conceived under the old one are
    invalidated in O(pending) without enumerating special cases.

M2  Reversibility-gated commitment. Read-only calls fire freely, including on
    partial turns: they are free to cancel and free to abandon. State-modifying
    calls pass a gate first, because every safety penalty in the rubric and
    every real-world harm lives on that side of the line.

    The idempotency ledger keys on the scorer's OWN duplicate-detection key, so
    what we refuse to do is exactly what it would penalise - not something
    approximately similar.

    Retry policy follows docs/TOOLS.md: a read-only tool is safe to retry, a
    state-modifying one only when we have evidence the first attempt did not
    commit. `invalid_args` is such evidence. `timeout` is NOT - the booking may
    have gone through upstream, and the user pays for the duplicate whether or
    not the trace shows it.

Cancellation policy is deliberately asymmetric: when in doubt, cancel. The
harness logs a cancel for an already-finished call as `cancel_noop` and does
not penalise it, while a stale completion more than 800ms after the
interruption is a recovery violation. The costs are not symmetric, so the
policy should not be either.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from . import config, contract, telemetry
from .state import ConversationState
from .tools import ToolRegistry, ToolSpec

# Call lifecycle
PENDING = "pending"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

# Idempotency ledger states for state-modifying work
LEDGER_NONE = "none"            # never attempted, or attempt provably did not commit
LEDGER_IN_FLIGHT = "in_flight"  # attempted, outcome not yet known
LEDGER_COMMITTED = "committed"  # confirmed success - must never be repeated
LEDGER_UNKNOWN = "unknown"      # attempted, outcome genuinely unknown (timeout)


@dataclass
class CallRecord:
    """One tool call and everything needed to reason about it later."""

    call_id: str
    api_name: str
    args: Dict[str, Any]
    kind: str
    epoch: int
    depends_on: Set[str] = field(default_factory=set)
    issued_at_ms: float = 0.0
    status: str = PENDING
    settled_at_ms: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    attempt: int = 1
    idem_key: Optional[str] = None
    purpose: Optional[str] = None

    @property
    def is_state_modifying(self) -> bool:
        return self.kind == contract.STATE_MODIFYING

    @property
    def is_pending(self) -> bool:
        return self.status == PENDING


def idempotency_key(api_name: str, args: Dict[str, Any]) -> str:
    """Mirror of the scorer's duplicate-detection key.

    harness/scorer.py builds exactly this to count duplicate state-modifying
    completions. Keying our ledger the same way means our refusal and its
    penalty agree by construction; any other normalisation would leave a gap
    between what we prevent and what it punishes.
    """
    normalised = {
        k: contract.norm(v) if not isinstance(v, (dict, list)) else v
        for k, v in sorted((args or {}).items())
    }
    return api_name + "|" + json.dumps(normalised, sort_keys=True, default=str)


class Coordinator:
    """Owns in-flight work, cancellation, and commitment safety."""

    def __init__(self, state: ConversationState, emitter: Any,
                 registry: Optional[ToolRegistry] = None) -> None:
        self.state = state
        self.emit = emitter
        self.registry = registry or ToolRegistry()

        self.calls: Dict[str, CallRecord] = {}
        self.ledger: Dict[str, str] = {}
        self._seq = 0

        # Diagnostics for our own gates, not for the scorer.
        self.cancel_latencies_ms: List[float] = []
        self.refused_commits: List[Tuple[str, str]] = []

    def set_registry(self, registry: ToolRegistry) -> None:
        self.registry = registry

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def pending(self) -> List[CallRecord]:
        return [r for r in self.calls.values() if r.is_pending]

    def pending_state_modifying(self) -> List[CallRecord]:
        return [r for r in self.pending() if r.is_state_modifying]

    def completed_for(self, api_name: str) -> List[CallRecord]:
        return [r for r in self.calls.values()
                if r.api_name == api_name and r.status == COMPLETED]

    def last_success(self, api_name: str) -> Optional[CallRecord]:
        found = self.completed_for(api_name)
        return found[-1] if found else None

    def ledger_state(self, api_name: str, args: Dict[str, Any]) -> str:
        return self.ledger.get(idempotency_key(api_name, args), LEDGER_NONE)

    def _sync_emitter(self) -> None:
        """Keep the emitter's truthfulness guard in step with reality."""
        if hasattr(self.emit, "set_pending_state_modifying"):
            self.emit.set_pending_state_modifying(len(self.pending_state_modifying()))

    # ------------------------------------------------------------------
    # M2: the commitment gate
    # ------------------------------------------------------------------
    def may_issue(
        self,
        spec: ToolSpec,
        args: Dict[str, Any],
        *,
        now_ms: float,
        turn_ended: bool = True,
        confidence: float = 1.0,
    ) -> Tuple[bool, str]:
        """Decide whether this call may be emitted now.

        Read-only work is unconditionally allowed - that asymmetry is where our
        latency comes from, because a speculative search costs nothing to throw
        away. State-modifying work must clear four conditions.
        """
        problems = spec.validate(args)
        if problems:
            return False, "invalid_args:" + "; ".join(problems)

        if not spec.is_state_modifying:
            return True, "read_only"

        if not turn_ended:
            return False, "turn_not_ended"

        if confidence < config.COMMIT_CONFIDENCE:
            return False, "low_confidence:" + str(round(confidence, 2))

        # The user may still be mid-correction: "New York... no, Newark".
        since_bump = now_ms - self.state.last_bump_at_ms
        if self.state.epoch > 0 and since_bump < config.COMMITMENT_QUIET_MS:
            return False, "too_soon_after_correction:" + str(int(since_bump)) + "ms"

        status = self.ledger_state(spec.name, args)
        if status == LEDGER_COMMITTED:
            return False, "already_committed"
        if status == LEDGER_IN_FLIGHT:
            return False, "already_in_flight"
        if status == LEDGER_UNKNOWN:
            return False, "previous_outcome_unknown"

        return True, "ok"

    # ------------------------------------------------------------------
    # issuing
    # ------------------------------------------------------------------
    def issue(
        self,
        spec: ToolSpec,
        args: Dict[str, Any],
        *,
        now_ms: float,
        depends_on: Optional[Sequence[str]] = None,
        turn_ended: bool = True,
        confidence: float = 1.0,
        purpose: Optional[str] = None,
    ) -> Optional[str]:
        """Emit a tool call if the gate allows. Returns the call_id or None."""
        allowed, reason = self.may_issue(
            spec, args, now_ms=now_ms, turn_ended=turn_ended, confidence=confidence)
        if not allowed:
            self.refused_commits.append((spec.name, reason))
            telemetry.log("coord.refused", tool=spec.name, reason=reason, args=args)
            return None

        self._seq += 1
        call_id = "c" + str(self._seq)
        key = idempotency_key(spec.name, args)
        record = CallRecord(
            call_id=call_id,
            api_name=spec.name,
            args=dict(args),
            kind=spec.kind,
            epoch=self.state.epoch,
            depends_on=set(depends_on or ()),
            issued_at_ms=now_ms,
            idem_key=key,
            purpose=purpose,
        )

        if not self.emit.tool_call(call_id, spec.name, args):
            telemetry.log("coord.emit_failed", tool=spec.name, call_id=call_id)
            return None

        self.calls[call_id] = record
        if spec.is_state_modifying:
            self.ledger[key] = LEDGER_IN_FLIGHT
        self._sync_emitter()
        telemetry.log("coord.issued", call_id=call_id, tool=spec.name,
                      epoch=record.epoch, depends_on=sorted(record.depends_on),
                      at_ms=now_ms)
        return call_id

    # ------------------------------------------------------------------
    # M1: invalidation
    # ------------------------------------------------------------------
    def should_invalidate(self, record: CallRecord, changed_slots: Set[str]) -> bool:
        """Is this in-flight call made obsolete by what just changed?

        Cancelling a call that is still valid costs us the task points it would
        have earned. Failing to cancel one that is stale costs half the recovery
        category. So we cancel when the call depends on something that moved,
        and also when we cannot tell - but not when it is provably unaffected.
        """
        if record.epoch >= self.state.epoch:
            return False            # issued after the bump: already current
        if not record.depends_on:
            return True             # unknown dependence: cancellation is free
        return bool(record.depends_on & changed_slots)

    def invalidate(self, changed_slots: Optional[Set[str]] = None,
                   *, now_ms: float = 0.0, reason: str = "interruption") -> List[str]:
        """Cancel every in-flight call the latest change invalidated."""
        if changed_slots is None:
            changed_slots = self.state.changed_since(self.state.epoch)
        cancelled: List[str] = []
        for record in self.pending():
            if self.should_invalidate(record, changed_slots):
                if self._cancel(record, now_ms=now_ms, reason=reason):
                    cancelled.append(record.call_id)
        self._sync_emitter()
        telemetry.log("coord.invalidate", reason=reason, cancelled=cancelled,
                      changed=sorted(changed_slots), at_ms=now_ms)
        return cancelled

    def cancel_all(self, *, now_ms: float = 0.0,
                   reason: str = "retraction") -> List[str]:
        """Abandon everything in flight - retractions and shutdown."""
        cancelled = []
        for record in self.pending():
            if self._cancel(record, now_ms=now_ms, reason=reason):
                cancelled.append(record.call_id)
        self._sync_emitter()
        return cancelled

    def _cancel(self, record: CallRecord, *, now_ms: float, reason: str) -> bool:
        if not record.is_pending:
            return False
        if not self.emit.cancel(record.call_id):
            return False
        record.status = CANCELLED
        record.settled_at_ms = now_ms
        latency = max(0.0, now_ms - self.state.last_bump_at_ms)
        self.cancel_latencies_ms.append(latency)

        # A cancelled state-modifying call never reaches a completion, so it
        # never counts as a duplicate. Release the key so a corrected call can
        # be issued for the same tool.
        if record.is_state_modifying and record.idem_key:
            if self.ledger.get(record.idem_key) == LEDGER_IN_FLIGHT:
                self.ledger[record.idem_key] = LEDGER_NONE

        telemetry.log("coord.cancelled", call_id=record.call_id,
                      tool=record.api_name, reason=reason,
                      latency_ms=round(latency, 1))
        return True

    # ------------------------------------------------------------------
    # results
    # ------------------------------------------------------------------
    def on_result(self, payload: Dict[str, Any], *,
                  now_ms: float) -> Optional[CallRecord]:
        """Record a tool result and decide whether we may act on it.

        Returns the record when the result is live and usable, or None when it
        belongs to work we cancelled or that a later interruption superseded.
        The ledger is updated either way: a booking that succeeded is committed
        whether or not we still want it.
        """
        call_id = str(payload.get("call_id") or "")
        record = self.calls.get(call_id)
        if record is None:
            telemetry.log("coord.unknown_result", call_id=call_id)
            return None

        status = payload.get("status")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        record.result = result
        record.settled_at_ms = now_ms

        if status == "success":
            if record.status != CANCELLED:
                record.status = COMPLETED
            if record.is_state_modifying and record.idem_key:
                self.ledger[record.idem_key] = LEDGER_COMMITTED
            self.emit.note_tool_succeeded(record.api_name)
        else:
            if record.status != CANCELLED:
                record.status = FAILED
            record.error = str(result.get("error") or "error")
            if record.is_state_modifying and record.idem_key:
                # Only a pre-commit failure proves nothing happened upstream.
                self.ledger[record.idem_key] = (
                    LEDGER_NONE if record.error in contract.PRE_COMMIT_ERRORS
                    else LEDGER_UNKNOWN)

        self._sync_emitter()

        if record.status == CANCELLED:
            telemetry.log("coord.result_discarded", call_id=call_id,
                          why="cancelled", tool=record.api_name)
            return None

        if record.epoch < self.state.epoch:
            telemetry.log("coord.result_discarded", call_id=call_id, why="stale_epoch",
                          tool=record.api_name, call_epoch=record.epoch,
                          now_epoch=self.state.epoch)
            return None

        telemetry.log("coord.result", call_id=call_id, tool=record.api_name,
                      status=record.status, at_ms=now_ms)
        return record

    # ------------------------------------------------------------------
    # retry policy
    # ------------------------------------------------------------------
    def may_retry(self, record: CallRecord) -> Tuple[bool, str]:
        """Should a failed call be attempted again?

        docs/TOOLS.md: read-only tools are safe to retry; state-modifying tools
        only with evidence the first attempt did not commit. A timeout is not
        such evidence, and retrying on one is the single most expensive mistake
        available in this rubric.
        """
        if record.status != FAILED:
            return False, "not_failed"
        if record.attempt >= config.MAX_TOOL_ATTEMPTS:
            return False, "attempts_exhausted"
        if record.error == "invalid_args":
            return False, "invalid_args_needs_new_arguments"
        if record.error == "unknown_tool":
            return False, "unknown_tool"
        if not record.is_state_modifying:
            return True, "read_only_retry"
        if record.error in contract.PRE_COMMIT_ERRORS:
            return True, "provably_uncommitted"
        return False, "state_modifying_outcome_unknown"

    def retry(self, record: CallRecord, *, now_ms: float) -> Optional[str]:
        """Re-issue a failed call under the current epoch."""
        allowed, reason = self.may_retry(record)
        if not allowed:
            telemetry.log("coord.retry_refused", call_id=record.call_id,
                          tool=record.api_name, reason=reason)
            return None
        spec = self.registry.get(record.api_name)
        if spec is None:
            return None
        self._seq += 1
        call_id = "c" + str(self._seq)
        clone = CallRecord(
            call_id=call_id,
            api_name=record.api_name,
            args=dict(record.args),
            kind=record.kind,
            epoch=self.state.epoch,
            depends_on=set(record.depends_on),
            issued_at_ms=now_ms,
            attempt=record.attempt + 1,
            idem_key=record.idem_key,
            purpose=record.purpose,
        )
        if not self.emit.tool_call(call_id, record.api_name, clone.args):
            return None
        self.calls[call_id] = clone
        if clone.is_state_modifying and clone.idem_key:
            self.ledger[clone.idem_key] = LEDGER_IN_FLIGHT
        self._sync_emitter()
        telemetry.log("coord.retry", call_id=call_id, tool=record.api_name,
                      attempt=clone.attempt, at_ms=now_ms)
        return call_id

    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        return {
            "issued": len(self.calls),
            "pending": len(self.pending()),
            "cancelled": sum(1 for r in self.calls.values() if r.status == CANCELLED),
            "completed": sum(1 for r in self.calls.values() if r.status == COMPLETED),
            "failed": sum(1 for r in self.calls.values() if r.status == FAILED),
            "refused": len(self.refused_commits),
            "max_cancel_latency_ms": round(max(self.cancel_latencies_ms), 1)
            if self.cancel_latencies_ms else 0.0,
        }
