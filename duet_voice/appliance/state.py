"""Deterministic appliance session state.

The thinker may propose actions. This object is the source of truth for what
device is selected, what SmartThings last reported, which steps ran, whether
anything is booked, and the current epoch. It is never inferred from LLM memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StepAttempt:
    step_id: str
    title: str
    outcome: str  # completed | skipped | already_done | failed | refused
    user_note: str
    epoch: int
    error_code: str = ""


@dataclass
class BookingRecord:
    request_id: str
    device_id: str
    slot_id: str
    window: str
    when_label: str
    status: str  # confirmed | cancelled | unknown
    idempotency_key: str = ""


@dataclass
class CommandRecord:
    command_id: str
    device_id: str
    command: str
    acceptance: str  # ACCEPTED | REJECTED | error
    applied: bool = False


@dataclass
class ApplianceSessionState:
    workflow_id: int = 0
    epoch: int = 0
    intent: str = ""
    selected_device_id: Optional[str] = None
    # last observed facts keyed by device_id (never mixed across appliances)
    devices: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    troubleshooting: Dict[str, List[StepAttempt]] = field(default_factory=dict)
    verification: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    bookings: Dict[str, BookingRecord] = field(default_factory=dict)
    commands: List[CommandRecord] = field(default_factory=list)
    last_offered_slots: List[Dict[str, Any]] = field(default_factory=list)
    user_observations: List[str] = field(default_factory=list)
    handoff: Optional[Dict[str, Any]] = None
    superseded: int = 0
    last_failure: str = ""

    def snapshot(self) -> Dict[str, Any]:
        selected = self.devices.get(self.selected_device_id or "", {})
        booking = self.bookings.get(self.selected_device_id or "")
        steps = self.troubleshooting.get(self.selected_device_id or "", [])
        return {
            "workflow_id": self.workflow_id,
            "epoch": self.epoch,
            "intent": self.intent,
            "device_id": self.selected_device_id,
            "appliance_type": selected.get("appliance_type"),
            "name": selected.get("name"),
            "model": selected.get("model"),
            "serial": selected.get("serial"),
            "online": selected.get("online"),
            "health": selected.get("health"),
            "error_code": selected.get("error_code"),
            "error_meaning": selected.get("error_meaning"),
            "diagnostics": selected.get("diagnostics"),
            "steps_attempted": [s.__dict__ for s in steps if s.outcome in ("completed", "failed")],
            "steps_skipped": [s.__dict__ for s in steps if s.outcome in ("skipped", "already_done")],
            "verification": self.verification.get(self.selected_device_id or ""),
            "service_required": bool(selected.get("service_required")),
            "booking": None if booking is None else booking.__dict__,
            "handoff": self.handoff,
            "user_observations": list(self.user_observations),
            "last_failure": self.last_failure,
            "superseded": self.superseded,
        }

    def select_device(self, device_id: str, *, epoch: int) -> bool:
        """Select a device. Returns True when this starts a new appliance workflow."""
        changed = self.selected_device_id is not None and self.selected_device_id != device_id
        if changed:
            self.workflow_id += 1
            self.last_offered_slots = []
            self.handoff = None
        self.selected_device_id = device_id
        self.epoch = epoch
        return changed

    def remember_device(self, device_id: str, facts: Dict[str, Any]) -> None:
        current = self.devices.get(device_id, {})
        merged = {**current, **{k: v for k, v in facts.items() if v is not None}}
        self.devices[device_id] = merged

    def booking_for(self, device_id: str) -> Optional[BookingRecord]:
        rec = self.bookings.get(device_id)
        if rec is None or rec.status == "cancelled":
            return None
        return rec

    def steps_for(self, device_id: str) -> List[StepAttempt]:
        return list(self.troubleshooting.get(device_id, []))

    def record_step(self, device_id: str, attempt: StepAttempt) -> None:
        self.troubleshooting.setdefault(device_id, []).append(attempt)
        if attempt.user_note:
            self.user_observations.append(attempt.user_note)

    def session_note(self) -> str:
        """Grounding text for the thinker. Trust this over conversational memory."""
        s = self.snapshot()
        lines = [
            "DETERMINISTIC SESSION STATE (this is what is true; do not invent otherwise):",
            "epoch=%s workflow=%s intent=%s" % (s["epoch"], s["workflow_id"], s["intent"] or "none"),
        ]
        if not s["device_id"]:
            lines.append("No appliance is selected yet.")
        else:
            lines.append(
                "Selected appliance: {name} type={appliance_type} id={device_id} model={model} "
                "online={online} health={health} error={error_code} meaning={error_meaning}".format(**{
                    k: s.get(k) for k in (
                        "name", "appliance_type", "device_id", "model",
                        "online", "health", "error_code", "error_meaning")
                })
            )
        if s.get("steps_attempted") or s.get("steps_skipped"):
            done = ", ".join(x["step_id"] + "=" + x["outcome"] for x in (s["steps_attempted"] or []))
            skipped = ", ".join(x["step_id"] + "=" + x["outcome"] for x in (s["steps_skipped"] or []))
            lines.append("Troubleshooting attempted: %s. Skipped: %s." % (done or "none", skipped or "none"))
        ver = s.get("verification") or {}
        if ver:
            lines.append("Verification: resolved=%s remaining_error=%s" % (
                ver.get("resolved"), ver.get("error_code")))
        booking = s.get("booking")
        if booking:
            lines.append(
                "ACTIVE SERVICE REQUEST %s status=%s slot=%s window=%s. "
                "If the user asks whether it is booked, call get_service_request. "
                "Do not create another booking." % (
                    booking.get("request_id"), booking.get("status"),
                    booking.get("slot_id"), booking.get("window"))
            )
        if s.get("last_failure"):
            lines.append("Last failure: %s" % s["last_failure"])
        return "\n".join(lines)
