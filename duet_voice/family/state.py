"""Deterministic Family Care session state.

The thinker may propose actions. This object is the source of truth for who is
selected, whether Knox health consent was granted, last Watch vitals, and any
outbound text or call. It is never inferred from LLM memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OutboundRecord:
    kind: str  # text | call
    record_id: str
    target_id: str
    target_name: str
    purpose: str
    status: str  # sent | placed | cancelled | unknown
    idempotency_key: str = ""


@dataclass
class FamilySessionState:
    workflow_id: int = 0
    epoch: int = 0
    intent: str = ""
    selected_member_id: Optional[str] = None
    members: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    consent: Dict[str, bool] = field(default_factory=dict)
    vitals: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    outbounds: List[OutboundRecord] = field(default_factory=list)
    user_observations: List[str] = field(default_factory=list)
    handoff: Optional[Dict[str, Any]] = None
    superseded: int = 0
    last_failure: str = ""

    def snapshot(self) -> Dict[str, Any]:
        selected = self.members.get(self.selected_member_id or "", {})
        last_text = next((o for o in reversed(self.outbounds) if o.kind == "text" and o.status in ("sent", "unknown")), None)
        last_call = next((o for o in reversed(self.outbounds) if o.kind == "call" and o.status in ("placed", "unknown")), None)
        return {
            "workflow_id": self.workflow_id,
            "epoch": self.epoch,
            "intent": self.intent,
            "member_id": self.selected_member_id,
            "name": selected.get("name"),
            "presence": selected.get("presence"),
            "inactivity_alert": selected.get("inactivity_alert"),
            "watch_paired": selected.get("watch_paired"),
            "consent_health": bool(self.consent.get(self.selected_member_id or "")),
            "vitals_band": (self.vitals.get(self.selected_member_id or "") or {}).get("band"),
            "last_text": None if last_text is None else last_text.__dict__,
            "last_call": None if last_call is None else last_call.__dict__,
            "handoff": self.handoff,
            "user_observations": list(self.user_observations),
            "last_failure": self.last_failure,
            "superseded": self.superseded,
        }

    def select_member(self, member_id: str, *, epoch: int) -> bool:
        changed = self.selected_member_id is not None and self.selected_member_id != member_id
        if changed:
            self.workflow_id += 1
            self.handoff = None
        self.selected_member_id = member_id
        self.epoch = epoch
        return changed

    def remember_member(self, member_id: str, facts: Dict[str, Any]) -> None:
        current = self.members.get(member_id, {})
        self.members[member_id] = {**current, **{k: v for k, v in facts.items() if v is not None}}

    def text_for(self, target_id: str, purpose: str) -> Optional[OutboundRecord]:
        for rec in reversed(self.outbounds):
            if rec.kind == "text" and rec.target_id == target_id and rec.purpose == purpose:
                if rec.status in ("sent", "unknown"):
                    return rec
        return None

    def call_for(self, target_id: str) -> Optional[OutboundRecord]:
        for rec in reversed(self.outbounds):
            if rec.kind == "call" and rec.target_id == target_id:
                if rec.status in ("placed", "unknown"):
                    return rec
        return None

    def session_note(self) -> str:
        s = self.snapshot()
        lines = [
            "DETERMINISTIC SESSION STATE (this is what is true; do not invent otherwise):",
            "epoch=%s workflow=%s intent=%s" % (s["epoch"], s["workflow_id"], s["intent"] or "none"),
        ]
        if not s["member_id"]:
            lines.append("No family member is selected yet.")
        else:
            lines.append(
                "Selected member: {name} id={member_id} presence={presence} "
                "inactivity_alert={inactivity_alert} watch_paired={watch_paired} "
                "knox_health_consent={consent_health} vitals_band={vitals_band}".format(**{
                    k: s.get(k) for k in (
                        "name", "member_id", "presence", "inactivity_alert",
                        "watch_paired", "consent_health", "vitals_band")
                })
            )
        if s.get("last_text"):
            t = s["last_text"]
            lines.append(
                "ACTIVE TEXT %s to %s purpose=%s status=%s. "
                "If the user asks whether it was sent, call get_outbound_status. "
                "Do not send another text for the same purpose." % (
                    t.get("record_id"), t.get("target_name"), t.get("purpose"), t.get("status"))
            )
        if s.get("last_call"):
            c = s["last_call"]
            lines.append(
                "ACTIVE CALL %s to %s status=%s. Do not place another call to this target." % (
                    c.get("record_id"), c.get("target_name"), c.get("status"))
            )
        if s.get("last_failure"):
            lines.append("Last failure: %s" % s["last_failure"])
        return "\n".join(lines)
