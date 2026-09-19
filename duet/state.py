"""Conversation state: slots with provenance, epoch versioning, and undo.

Two of DUET's named mechanisms live here.

M1 - Epoch-versioned state. Every mutation is stamped with the epoch that
produced it. An interruption bumps the epoch, which is what lets the
coordinator decide, in O(1) and without enumerating special cases, that a
computation conceived under a previous epoch is now orphaned.

M3 - Slot provenance. A slot is not a string. It records which utterance
produced it, at what timestamp, from which modality, and with what
confidence. That is what makes a correction *local*: when the user says
"no, New York", we rewrite one slot and leave the rest of the plan intact,
and we can say what changed. Theme objective 3 calls this "localized slot
corrections".

M6 - Conversational undo falls out of M1 for free: epoch checkpoints are
retained, so "go back to what I said before" is a state restore rather than
a re-conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

# Modality a slot value came from. Confidence semantics differ per source:
# TEXT is delivered already transcribed and is trusted; AUDIO and VISION
# carry a model posterior and may fall below the clarification threshold.
SRC_TEXT = "text"
SRC_AUDIO = "audio"
SRC_VISION = "vision"
SRC_TOOL = "tool"
SRC_INFERRED = "inferred"


@dataclass(frozen=True)
class Slot:
    """One extracted value, with everything needed to revise it safely."""

    name: str
    value: Any
    confidence: float = 1.0
    source: str = SRC_TEXT
    utterance_id: int = 0
    span: Optional[Tuple[int, int]] = None
    set_at_ms: float = 0.0
    epoch: int = 0

    def is_confident(self, threshold: float) -> bool:
        return self.confidence >= threshold


@dataclass
class EpochCheckpoint:
    """A restorable point in the conversation (M6)."""

    epoch: int
    intent: Optional[str]
    slots: Dict[str, Slot]
    reason: str
    at_ms: float


class ConversationState:
    """Session-scoped state. One instance per scenario, never shared.

    The theme restricts us to session-scoped memory with no cross-session
    caching, and the harness constructs a fresh agent per scenario, so this
    object's lifetime is exactly one conversation.
    """

    def __init__(self) -> None:
        self.epoch: int = 0
        self.intent: Optional[str] = None
        self.slots: Dict[str, Slot] = {}

        # Provenance trail: every value a slot has ever held, in order.
        self.superseded: Dict[str, List[Slot]] = {}

        # Slot names dropped, with the epoch at which they were dropped, so
        # changed_since() reports removals as changes too.
        self._dropped: List[Tuple[str, int]] = []

        # Epoch checkpoints for undo / retraction (M6).
        self._checkpoints: List[EpochCheckpoint] = []

        # Monotonic id for each user utterance, used as slot provenance.
        self.utterance_id: int = 0

        # Why the most recent epoch bump happened - used for explainability
        # ("I dropped the Boston search") and by the NLG.
        self.last_bump_reason: Optional[str] = None
        self.last_bump_at_ms: float = 0.0

    # -- slots ------------------------------------------------------------
    def set_slot(
        self,
        name: str,
        value: Any,
        *,
        confidence: float = 1.0,
        source: str = SRC_TEXT,
        at_ms: float = 0.0,
        span: Optional[Tuple[int, int]] = None,
    ) -> Slot:
        """Write a slot, retaining the previous value as provenance."""
        old = self.slots.get(name)
        if old is not None:
            self.superseded.setdefault(name, []).append(old)
        slot = Slot(
            name=name,
            value=value,
            confidence=float(confidence),
            source=source,
            utterance_id=self.utterance_id,
            span=span,
            set_at_ms=float(at_ms),
            epoch=self.epoch,
        )
        self.slots[name] = slot
        return slot

    def get(self, name: str, default: Any = None) -> Any:
        slot = self.slots.get(name)
        return default if slot is None else slot.value

    def get_slot(self, name: str) -> Optional[Slot]:
        return self.slots.get(name)

    def has(self, name: str) -> bool:
        return name in self.slots and self.slots[name].value is not None

    def drop_slot(self, name: str) -> Optional[Slot]:
        slot = self.slots.pop(name, None)
        if slot is not None:
            self.superseded.setdefault(name, []).append(slot)
            self._dropped.append((name, self.epoch))
        return slot

    def previous_value(self, name: str) -> Any:
        """The value this slot held before its current one, if any.

        Used by the NLG to name what was abandoned ("switching from Boston
        to New York") and by the coordinator to build the invalidation probe.
        """
        history = self.superseded.get(name) or []
        return history[-1].value if history else None

    def confidence(self, name: str) -> float:
        slot = self.slots.get(name)
        return 0.0 if slot is None else slot.confidence

    def low_confidence_slots(self, threshold: float) -> List[Slot]:
        return [s for s in self.slots.values() if s.confidence < threshold]

    # -- epochs (M1) ------------------------------------------------------
    def bump_epoch(self, reason: str, at_ms: float = 0.0) -> int:
        """Invalidate everything conceived under the current epoch.

        Checkpoints the pre-bump state first so it can be restored (M6).
        Returns the new epoch.
        """
        self._checkpoints.append(
            EpochCheckpoint(
                epoch=self.epoch,
                intent=self.intent,
                slots=dict(self.slots),  # Slot is frozen; shallow copy is safe
                reason=reason,
                at_ms=at_ms,
            )
        )
        self.epoch += 1
        self.last_bump_reason = reason
        self.last_bump_at_ms = float(at_ms)
        return self.epoch

    def changed_since(self, epoch: int) -> Set[str]:
        """Slot names written or dropped at or after `epoch`.

        This is the invalidation probe: an in-flight tool call whose
        arguments depend on any of these slots is stale.
        """
        changed = {name for name, slot in self.slots.items() if slot.epoch >= epoch}
        changed |= {name for name, ep in self._dropped if ep >= epoch}
        return changed

    def checkpoints(self) -> List[EpochCheckpoint]:
        return list(self._checkpoints)

    def restore_epoch(self, epoch: int) -> bool:
        """M6: restore the state as it was at the given epoch.

        Returns False if no such checkpoint exists. Note this does NOT roll
        the epoch counter back - restoring is itself a new epoch, so any work
        in flight under the current epoch is still correctly invalidated.
        """
        for cp in reversed(self._checkpoints):
            if cp.epoch == epoch:
                self.bump_epoch("undo:restore_epoch_" + str(epoch), self.last_bump_at_ms)
                self.intent = cp.intent
                self.slots = dict(cp.slots)
                return True
        return False

    def undo_last(self) -> bool:
        """M6: revert the most recent correction ('no, go back')."""
        if not self._checkpoints:
            return False
        return self.restore_epoch(self._checkpoints[-1].epoch)

    # -- intent -----------------------------------------------------------
    def set_intent(self, intent: Optional[str]) -> None:
        self.intent = intent

    def reset_for_intent_change(self, new_intent: Optional[str]) -> None:
        """A full intent change ('forget the flight, my TV is broken').

        Slots are dropped rather than carried over: keeping `destination`
        around while the user asks about a television is how an agent ends
        up sending nonsense arguments to the next tool.
        """
        for name in list(self.slots):
            self.drop_slot(name)
        self.intent = new_intent

    # -- snapshot ---------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """The scored view of our state.

        Convention fixed by PROTOCOL.md section 2.5 and used by every piece
        of ground truth: {"intent": <string>, "slots": {<name>: <value>}}.
        The scorer reads it with a dotted path and compares with
        norm(actual) == norm(expected), so values must be plain scalars.
        """
        return {
            "intent": self.intent,
            "slots": {
                name: slot.value
                for name, slot in self.slots.items()
                if slot.value is not None
            },
        }

    def explain_last_change(self) -> Optional[str]:
        """Human-readable description of the most recent correction.

        Feeds the NLG so an acknowledgment can name what changed, which the
        scorer rewards (content-aware fillers) and users need in order to
        trust that the correction landed.
        """
        if not self._checkpoints:
            return None
        cp = self._checkpoints[-1]
        for name, slot in self.slots.items():
            old = cp.slots.get(name)
            if old is None or old.value != slot.value:
                if old is not None:
                    return name + ": " + str(old.value) + " -> " + str(slot.value)
                return name + ": " + str(slot.value)
        return None

    def __repr__(self) -> str:
        return (
            "ConversationState(epoch="
            + str(self.epoch)
            + ", intent="
            + repr(self.intent)
            + ", slots="
            + repr({k: v.value for k, v in self.slots.items()})
            + ")"
        )
