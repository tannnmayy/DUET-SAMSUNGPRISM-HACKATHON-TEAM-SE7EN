"""Technician handoff packet.

Built from deterministic session state so the customer does not have to repeat
the problem. Serials are included for the technician; logs still redact them.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .state import ApplianceSessionState, StepAttempt


def _steps(attempts: List[StepAttempt], outcomes) -> List[Dict[str, str]]:
    return [{"step_id": s.step_id, "title": s.title, "outcome": s.outcome,
             "note": s.user_note, "error_code": s.error_code}
            for s in attempts if s.outcome in outcomes]


def build_handoff(state: ApplianceSessionState, *, extra_observations: List[str] = None) -> Dict[str, Any]:
    device_id = state.selected_device_id or ""
    device = state.devices.get(device_id, {})
    booking = state.booking_for(device_id)
    attempts = state.steps_for(device_id)
    verification = state.verification.get(device_id) or {}
    observations = list(state.user_observations)
    if extra_observations:
        observations.extend(extra_observations)
    last_command = next((c.__dict__ for c in reversed(state.commands) if c.device_id == device_id), None)
    return {
        "appliance": {
            "device_id": device_id or None,
            "type": device.get("appliance_type"),
            "name": device.get("name"),
            "model": device.get("model"),
            "serial": device.get("serial"),
            "room": device.get("room"),
            "manufacturer": device.get("manufacturer") or "Samsung",
        },
        "problem": device.get("problem") or device.get("error_meaning") or device.get("user_problem"),
        "error_code": device.get("error_code"),
        "error_meaning": device.get("error_meaning"),
        "smartthings": {
            "online": device.get("online"),
            "health": device.get("health"),
            "operating_state": device.get("operating_state"),
            "job_state": device.get("job_state"),
            "last_command": last_command,
            "last_command_note": (
                "SmartThings command acceptance is queued, not proof of completion"
                if last_command else None
            ),
        },
        "diagnostics": device.get("diagnostics"),
        "troubleshooting_attempted": _steps(attempts, ("completed", "failed")),
        "troubleshooting_skipped": _steps(attempts, ("skipped", "already_done")),
        "verification": {
            "resolved": verification.get("resolved"),
            "error_code": verification.get("error_code"),
            "checked_at_epoch": verification.get("epoch"),
        },
        "service_request_id": None if booking is None else booking.request_id,
        "appointment": None if booking is None else {
            "slot_id": booking.slot_id,
            "window": booking.window,
            "when": booking.when_label,
            "status": booking.status,
        },
        "user_observations": observations,
        "workflow_id": state.workflow_id,
        "epoch": state.epoch,
    }
