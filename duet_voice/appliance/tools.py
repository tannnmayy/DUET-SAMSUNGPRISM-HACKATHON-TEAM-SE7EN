"""Appliance tools, run through the existing DUET coordinator.

The thinker decides *what* to call. This module decides what is true and
whether the action is allowed: commit gate, idempotency ledger, session state,
grounded error meanings, and exactly-once booking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

from ..coordinator import CallRecord, Coordinator, Superseded, canonical
from .handoff import build_handoff
from .redaction import redact
from .safety import PROFESSIONAL_REQUIRED, may_instruct_user
from .service import MockSamsungServiceAdapter, RealSamsungServiceAdapter, SamsungServiceAdapter, ServiceError
from .smartthings import (
    ACCEPTED,
    MockSmartThingsAdapter,
    RealSmartThingsAdapter,
    SmartThingsAdapter,
    SmartThingsError,
)
from .state import ApplianceSessionState, BookingRecord, CommandRecord, StepAttempt
from .troubleshooting import lookup_guide, step_by_id, steps_payload

log = logging.getLogger("duet.appliance")

S = {"type": "string"}


def _obj(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


TOOL_SPECS: List[Dict[str, Any]] = [
    {"name": "list_appliances", "kind": "read",
     "description": "List Samsung appliances on the customer's SmartThings account. Optional query filters by type or name (washer, dryer, fridge).",
     "parameters": _obj({
         "query": {**S, "description": "Optional type or name the user said, e.g. 'washer' or 'dryer'."},
     }, [])},
    {"name": "get_appliance_status", "kind": "read",
     "description": "Read live SmartThings status and health for one device. Never invent a status.",
     "parameters": _obj({
         "device_id": {**S, "description": "SmartThings device id from list_appliances."},
     }, ["device_id"])},
    {"name": "get_appliance_diagnostics", "kind": "read",
     "description": "Read SmartThings diagnostics for one device. Returns unavailable when the device is offline or has no diagnostic attributes.",
     "parameters": _obj({
         "device_id": {**S, "description": "SmartThings device id."},
     }, ["device_id"])},
    {"name": "get_troubleshooting_steps", "kind": "read",
     "description": "Look up grounded troubleshooting steps for a documented error code and model. Unknown codes return unknown_error_code; do not invent a meaning.",
     "parameters": _obj({
         "model": {**S, "description": "Appliance model, e.g. from status."},
         "error_code": {**S, "description": "Error code reported by the appliance or by the user."},
         "appliance_type": {**S, "description": "washer, dryer, refrigerator, or dishwasher when known."},
     }, ["error_code"])},
    {"name": "record_troubleshooting_step", "kind": "write",
     "description": "Record that a troubleshooting step was completed, skipped, already done, or failed. Use when the user says they already did a step.",
     "parameters": _obj({
         "device_id": {**S, "description": "Device the step applies to."},
         "step_id": {**S, "description": "Step id from get_troubleshooting_steps."},
         "outcome": {**S, "description": "completed, skipped, already_done, or failed."},
         "note": {**S, "description": "Optional user observation, in their words."},
     }, ["step_id", "outcome"])},
    {"name": "verify_appliance_state", "kind": "read",
     "description": "Re-read SmartThings and decide whether the problem is actually gone. Never claim the appliance is fixed without this.",
     "parameters": _obj({
         "device_id": {**S, "description": "Device to verify."},
     }, ["device_id"])},
    {"name": "find_service_slots", "kind": "read",
     "description": "Find Samsung technician appointment slots. Pass the user's preferred window when they gave one.",
     "parameters": _obj({
         "device_id": {**S, "description": "Device that needs service."},
         "preferred_window": {**S, "description": "When the user wants service, e.g. 'Friday morning'."},
         "postal_code": {**S, "description": "Postal code if the user gave one."},
     }, [])},
    {"name": "book_samsung_service", "kind": "write",
     "description": "Book a technician visit. Exactly-once: if a booking already exists for this device, returns it instead of creating another. Requires a slot from find_service_slots.",
     "parameters": _obj({
         "device_id": {**S, "description": "Device to book service for."},
         "slot_id": {**S, "description": "Slot id from find_service_slots."},
         "preferred_window": {**S, "description": "Window the user asked for, if they did not give a slot id."},
     }, [])},
    {"name": "get_service_request", "kind": "read",
     "description": "Look up an existing Samsung service request. Use when the user asks if service is booked.",
     "parameters": _obj({
         "request_id": {**S, "description": "Service request id, if known."},
         "device_id": {**S, "description": "Device to look up when the id is not given."},
     }, [])},
    {"name": "prepare_human_handoff", "kind": "write",
     "description": "Build a technician summary from session state so the customer does not have to repeat the problem.",
     "parameters": _obj({
         "device_id": {**S, "description": "Device the handoff is for."},
         "extra_note": {**S, "description": "Optional extra observation from the user."},
     }, [])},
]

SPEC_BY_NAME = {s["name"]: s for s in TOOL_SPECS}
WRITE_TOOLS = {s["name"] for s in TOOL_SPECS if s["kind"] == "write"}

_WINDOW_PATTERNS = (
    (re.compile(r"tomorrow.*after|tomorrow.*\bpm\b|tomorrow_afternoon", re.I), "tomorrow_afternoon"),
    (re.compile(r"tomorrow.*morn|tomorrow.*\bam\b|tomorrow_morning", re.I), "tomorrow_morning"),
    (re.compile(r"friday.*morn|friday.*\bam\b|friday_morning", re.I), "friday_morning"),
    (re.compile(r"friday.*after|friday.*\bpm\b|friday_afternoon", re.I), "friday_afternoon"),
    (re.compile(r"wednesday.*morn|wednesday_morning", re.I), "wednesday_morning"),
)


def canonicalize_window(text: str) -> str:
    raw = " ".join((text or "").lower().split())
    if not raw:
        return ""
    for pattern, name in _WINDOW_PATTERNS:
        if pattern.search(raw):
            return name
    return raw.replace(" ", "_")


def coerce(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    props = SPEC_BY_NAME[tool]["parameters"]["properties"]
    out: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        want = props.get(key, {}).get("type")
        if want == "string" and not isinstance(value, str):
            value = str(value)
        if isinstance(value, str):
            value = value.strip()
        out[key] = value
    return out


def error_result(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    out = {"status": "error", "error": code, "instruction": message}
    out.update(extra)
    return out


def _match_query(device: Dict[str, Any], query: str) -> bool:
    if not query:
        return True
    blob = " ".join(str(device.get(k) or "") for k in
                    ("name", "label", "appliance_type", "model", "room")).lower()
    q = query.lower().strip()
    aliases = {
        "washing machine": "washer", "washers": "washer", "washer": "washer",
        "dryers": "dryer", "tumble dryer": "dryer", "dryer": "dryer",
        "fridge": "refrigerator", "fridge freezer": "refrigerator",
        "dishwasher": "dishwasher",
    }
    want = aliases.get(q, q)
    # Word boundaries so "washer" does not match "dishwasher".
    if re.search(r"\b" + re.escape(want) + r"\b", blob, re.I):
        return True
    if want != q and re.search(r"\b" + re.escape(q) + r"\b", blob, re.I):
        return True
    return False


class ApplianceToolbox:
    """One per conversation. Same call(tool, args, epoch) contract as FdbToolbox."""

    def __init__(
        self,
        room_name: str,
        coordinator: Coordinator,
        *,
        smartthings: Optional[SmartThingsAdapter] = None,
        service: Optional[SamsungServiceAdapter] = None,
        on_trace: Optional[Callable[..., None]] = None,
    ) -> None:
        self.room_name = room_name
        self.coord = coordinator
        self.state = ApplianceSessionState()
        self.smartthings = smartthings or default_smartthings()
        self.service = service or default_service()
        self._trace = on_trace or (lambda *a, **k: None)
        self.last_tool_start = 0.0
        self.last_tool_end = 0.0
        self.timeout_read = 8.0
        self.timeout_write = 12.0
        if hasattr(self.smartthings, "subscribe"):
            self.smartthings.subscribe(self._on_device_event)

    @property
    def specs(self) -> List[Dict[str, Any]]:
        return TOOL_SPECS

    def session_note(self) -> str:
        self.state.epoch = self.coord.epoch
        return self.state.session_note()

    def _on_device_event(self, event: Dict[str, Any]) -> None:
        device_id = event.get("device_id")
        payload = (event.get("event") or {})
        if device_id:
            self.state.remember_device(device_id, {
                k: payload[k] for k in ("error_code", "operating_state", "job_state", "online", "health")
                if k in payload
            })
            if "diagnostics" in payload:
                self.state.remember_device(device_id, {"diagnostics": payload["diagnostics"]})
        self._emit("webhook", device=device_id, event=redact(payload))

    def _emit(self, kind: str, **data: Any) -> None:
        try:
            self._trace(kind, epoch=self.coord.epoch, appliance=self.state.selected_device_id,
                        intent=self.state.intent, **redact(data))
        except Exception:
            log.debug("appliance trace failed", exc_info=True)

    def _st_call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SmartThingsError as exc:
            self.state.last_failure = "smartthings:%s" % exc.code
            if exc.code == "auth":
                return error_result(
                    "authentication_error",
                    "SmartThings authentication failed. Do not invent devices or status. "
                    "Ask the user to reconnect SmartThings, or continue from what they can see on the appliance.",
                    retry=False)
            if exc.code == "timeout":
                return error_result("timeout", "SmartThings timed out. Do not invent a status. Offer to try again.",
                                    retry=True)
            if exc.code == "rate_limit":
                return error_result("rate_limited", "SmartThings is rate-limiting requests. Wait, then retry the read.",
                                    retry=True, retry_after_s=exc.retry_after_s)
            if exc.code == "not_found":
                return error_result("not_found", "That device is not on this SmartThings account.", retry=False)
            return error_result(exc.code, str(exc))

    def _svc_call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ServiceError as exc:
            self.state.last_failure = "service:%s" % exc.code
            if exc.code == "timeout":
                return error_result("timeout", "Samsung service timed out. Do not claim a booking. Offer a human agent.",
                                    retry=False)
            if exc.code == "unavailable":
                return error_result("service_unavailable",
                                    "Samsung service is unavailable. Do not invent a booking. Offer a human agent.",
                                    retry=False)
            if exc.code == "not_found":
                return error_result("not_found", str(exc), retry=False)
            return error_result(exc.code, str(exc))

    def _device_id(self, args: Dict[str, Any]) -> str:
        return args.get("device_id") or self.state.selected_device_id or ""

    def _apply_status(self, device_id: str, payload: Dict[str, Any], *, epoch: int, select: bool) -> None:
        if not device_id or not isinstance(payload, dict):
            return
        if payload.get("status") == "error":
            return
        facts = {k: payload.get(k) for k in (
            "name", "label", "model", "serial", "appliance_type", "room", "manufacturer",
            "online", "health", "error_code", "operating_state", "job_state", "diagnostics")
            if payload.get(k) is not None}
        if payload.get("error_code"):
            kind = facts.get("appliance_type") or (self.state.devices.get(device_id) or {}).get("appliance_type") or ""
            guide = lookup_guide(facts.get("model") or "", payload["error_code"], kind)
            facts["error_meaning"] = None if guide is None else guide.problem
            facts["service_required"] = False if guide is None else guide.service_if_unresolved
        self.state.remember_device(device_id, facts)
        if select and epoch == self.coord.epoch:
            changed = self.state.select_device(device_id, epoch=epoch)
            if changed:
                self._emit("superseded_workflow", previous=True, device=device_id,
                           workflow_id=self.state.workflow_id)

    def _list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self.state.intent = "diagnose"
        devices = self._st_call(self.smartthings.list_devices)
        if isinstance(devices, dict) and devices.get("status") == "error":
            return devices
        query = args.get("query") or ""
        matched = [d for d in devices if _match_query(d, query)]
        for d in matched:
            self.state.remember_device(d["device_id"], d)
        return {"status": "ok", "appliances": matched, "query": query or None,
                "count": len(matched)}

    def _status(self, args: Dict[str, Any], *, epoch: int) -> Dict[str, Any]:
        self.state.intent = "diagnose"
        device_id = self._device_id(args)
        if not device_id:
            return error_result("missing_device", "List appliances and pick one before reading status.")
        payload = self._st_call(self.smartthings.get_status, device_id)
        if payload.get("status") != "error":
            self._apply_status(device_id, payload, epoch=epoch, select=True)
        return payload

    def _diagnostics(self, args: Dict[str, Any], *, epoch: int) -> Dict[str, Any]:
        self.state.intent = "diagnose"
        device_id = self._device_id(args)
        if not device_id:
            return error_result("missing_device", "List appliances and pick one before reading diagnostics.")
        payload = self._st_call(self.smartthings.get_diagnostics, device_id)
        if payload.get("status") == "ok":
            self._apply_status(device_id, payload, epoch=epoch, select=True)
        return payload

    def _steps(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self.state.intent = "troubleshoot"
        device_id = self.state.selected_device_id
        device = self.state.devices.get(device_id or "", {})
        model = args.get("model") or device.get("model") or ""
        kind = args.get("appliance_type") or device.get("appliance_type") or ""
        code = args.get("error_code") or device.get("error_code") or ""
        payload = steps_payload(model, code, kind)
        if payload.get("status") == "ok" and device_id:
            self.state.remember_device(device_id, {
                "error_code": payload.get("error_code"),
                "error_meaning": payload.get("problem"),
                "service_required": payload.get("service_if_unresolved"),
                "model": model or device.get("model"),
            })
        return payload

    def _record_step(self, args: Dict[str, Any], *, epoch: int) -> Dict[str, Any]:
        self.state.intent = "troubleshoot"
        device_id = self._device_id(args)
        if not device_id:
            return error_result("missing_device", "Select an appliance before recording a step.")
        step_id = args.get("step_id") or ""
        outcome = (args.get("outcome") or "").lower().replace(" ", "_")
        if outcome in ("already done", "i already did that", "already_did"):
            outcome = "already_done"
        if outcome not in ("completed", "skipped", "already_done", "failed"):
            return error_result("invalid_outcome",
                                "outcome must be completed, skipped, already_done, or failed.")
        step = step_by_id(step_id)
        if step is None:
            return error_result("unknown_step",
                                "That step is not in the knowledge base. Do not invent one.",
                                step_id=step_id)
        if outcome == "completed" and not may_instruct_user(step.safety):
            outcome = "refused"
            note = "Professional repair; not recorded as completed by the customer."
            self.state.record_step(device_id, StepAttempt(
                step_id, step.title, outcome, note, epoch,
                error_code=(self.state.devices.get(device_id) or {}).get("error_code") or ""))
            return {
                "status": "ok",
                "recorded": False,
                "outcome": "refused",
                "safety": step.safety,
                "instruction": "This step is professional-only. Offer Samsung service. Do not give repair instructions.",
            }
        command_result = None
        if outcome == "completed" and step.may_send_command:
            command_result = self._st_call(
                self.smartthings.execute_command, device_id, step.may_send_command)
            if isinstance(command_result, dict) and command_result.get("status") == ACCEPTED:
                self.state.commands.append(CommandRecord(
                    command_id=str(command_result.get("command_id") or ""),
                    device_id=device_id, command=step.may_send_command,
                    acceptance=ACCEPTED, applied=False))
        note = args.get("note") or ""
        self.state.record_step(device_id, StepAttempt(
            step_id, step.title, outcome, note, epoch,
            error_code=(self.state.devices.get(device_id) or {}).get("error_code") or ""))
        next_steps = []
        device = self.state.devices.get(device_id, {})
        guide_payload = steps_payload(device.get("model") or "", device.get("error_code") or "",
                                      device.get("appliance_type") or "")
        done_ids = {s.step_id for s in self.state.steps_for(device_id)}
        if guide_payload.get("status") == "ok":
            next_steps = [s for s in guide_payload["steps"] if s["step_id"] not in done_ids]
        return {
            "status": "ok",
            "recorded": True,
            "step_id": step_id,
            "outcome": outcome,
            "command": command_result,
            "command_note": None if not command_result else (
                "A SmartThings command was queued (ACCEPTED). That is not proof the appliance finished it. "
                "Call verify_appliance_state before saying it worked."
            ),
            "remaining_steps": next_steps,
        }

    def _verify(self, args: Dict[str, Any], *, epoch: int) -> Dict[str, Any]:
        self.state.intent = "verify"
        device_id = self._device_id(args)
        if not device_id:
            return error_result("missing_device", "Select an appliance before verifying.")
        status = self._st_call(self.smartthings.get_status, device_id)
        if status.get("status") == "error":
            return status
        self._apply_status(device_id, status, epoch=epoch, select=False)
        error_code = status.get("error_code")
        online = bool(status.get("online"))
        health = status.get("health")
        resolved = bool(online and health != "OFFLINE" and not error_code)
        pending = [c for c in self.state.commands if c.device_id == device_id and not c.applied]
        result = {
            "status": "ok",
            "device_id": device_id,
            "resolved": resolved,
            "online": online,
            "health": health,
            "error_code": error_code,
            "operating_state": status.get("operating_state"),
            "queued_commands_unconfirmed": len(pending),
            "instruction": (
                "You may tell the user the appliance looks clear."
                if resolved else
                "The problem is not confirmed fixed. Do not say it is fixed. "
                "Continue troubleshooting or offer Samsung service."
            ),
        }
        if pending:
            result["command_note"] = (
                "One or more SmartThings commands were only ACCEPTED. "
                "Do not say SmartThings completed them."
            )
        self.state.verification[device_id] = {**result, "epoch": epoch}
        if not resolved:
            self.state.remember_device(device_id, {"service_required": True})
        return result

    def _find_slots(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self.state.intent = "service"
        device_id = self._device_id(args)
        window = canonicalize_window(args.get("preferred_window") or "")
        payload = self._svc_call(self.service.find_slots, device_id=device_id or "",
                                 window=window, postal_code=args.get("postal_code") or "")
        if isinstance(payload, dict) and payload.get("status") == "error":
            return payload
        slots = list(payload)
        self.state.last_offered_slots = slots
        return {"status": "ok", "slots": slots, "window": window or None,
                "simulated": bool(getattr(self.service, "simulated", False))}

    def _existing_booking_reply(self, rec: BookingRecord) -> Dict[str, Any]:
        return {
            "status": "already_done",
            "note": "A service request already exists for this appliance; it was not booked again.",
            "request_id": rec.request_id,
            "slot_id": rec.slot_id,
            "window": rec.window,
            "when_label": rec.when_label,
            "booking_status": rec.status,
        }

    def _resolve_slot(self, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        slot_id = args.get("slot_id") or ""
        window = canonicalize_window(args.get("preferred_window") or "")
        offered = list(self.state.last_offered_slots)
        if slot_id:
            for s in offered:
                if s.get("slot_id") == slot_id:
                    return s
            found = self._svc_call(self.service.find_slots, device_id=self._device_id(args), window="")
            if isinstance(found, list):
                for s in found:
                    if s.get("slot_id") == slot_id:
                        return s
            return None
        if window:
            matches = [s for s in offered if s.get("window") == window]
            if len(matches) == 1:
                return matches[0]
            found = self._svc_call(self.service.find_slots, device_id=self._device_id(args), window=window)
            if isinstance(found, list) and len(found) == 1:
                return found[0]
            if isinstance(found, list) and found:
                return found[0]
        return None

    def _book(self, args: Dict[str, Any], *, epoch: int) -> Dict[str, Any]:
        self.state.intent = "service"
        device_id = self._device_id(args)
        if not device_id:
            return error_result("missing_device", "Select an appliance before booking service.")
        existing = self.state.booking_for(device_id)
        slot = self._resolve_slot(args)
        window = canonicalize_window(args.get("preferred_window") or "") or (slot or {}).get("window") or ""
        if existing and existing.status == "unknown":
            return {
                "status": "unknown_outcome",
                "request_id": existing.request_id,
                "instruction": (
                    "A booking was already attempted and may have gone through. "
                    "Do not book again. Tell the user it is uncertain and offer a human agent."
                ),
            }
        if existing and existing.status == "confirmed":
            same = (slot and existing.slot_id == slot.get("slot_id")) or (
                window and existing.window == window)
            if same or not slot:
                return self._existing_booking_reply(existing)
            cancelled = self._svc_call(self.service.cancel_request, existing.request_id)
            if isinstance(cancelled, dict) and cancelled.get("status") == "error":
                return cancelled
            existing.status = "cancelled"
        if not slot:
            return error_result(
                "missing_slot",
                "Find service slots and pick one the user agreed to. Do not invent a slot or a booking.",
            )
        device = self.state.devices.get(device_id, {})
        payload = {
            "device_id": device_id,
            "slot_id": slot["slot_id"],
            "preferred_window": slot.get("window") or window,
            "model": device.get("model"),
            "issue_summary": device.get("error_meaning") or device.get("error_code") or "appliance fault",
            "error_code": device.get("error_code"),
        }
        created = self._svc_call(self.service.create_request, payload)
        if created.get("status") == "error":
            return created
        rec = BookingRecord(
            request_id=str(created.get("request_id")),
            device_id=device_id,
            slot_id=str(created.get("slot_id") or slot["slot_id"]),
            window=str(created.get("window") or slot.get("window") or window),
            when_label=str(created.get("when_label") or slot.get("when_label") or ""),
            status="confirmed" if created.get("state") in (None, "confirmed", "ok") else str(created.get("state")),
            idempotency_key=canonical("book_samsung_service", {
                "device_id": device_id, "slot_id": slot["slot_id"],
                "preferred_window": slot.get("window") or window,
            }),
        )
        self.state.bookings[device_id] = rec
        self.state.remember_device(device_id, {"service_required": True})
        return {
            "status": "ok",
            "request_id": rec.request_id,
            "slot_id": rec.slot_id,
            "window": rec.window,
            "when_label": rec.when_label,
            "booking_status": rec.status,
            "simulated": bool(created.get("simulated", getattr(self.service, "simulated", False))),
        }

    def _get_request(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self.state.intent = "service"
        request_id = args.get("request_id") or ""
        device_id = self._device_id(args)
        if not request_id and device_id:
            rec = self.state.booking_for(device_id)
            if rec:
                request_id = rec.request_id
        if not request_id:
            # any active booking in the session
            for rec in self.state.bookings.values():
                if rec.status in ("confirmed", "unknown"):
                    request_id = rec.request_id
                    break
        if not request_id:
            return {
                "status": "ok",
                "booked": False,
                "instruction": "No service request is on file. Do not claim one was booked.",
            }
        payload = self._svc_call(self.service.get_request, request_id)
        if payload.get("status") == "error":
            return payload
        return {"status": "ok", "booked": payload.get("state") not in ("cancelled", None),
                "request": payload}

    def _handoff(self, args: Dict[str, Any], *, epoch: int) -> Dict[str, Any]:
        self.state.intent = "handoff"
        device_id = self._device_id(args)
        if device_id and self.coord.epoch == epoch:
            self.state.select_device(device_id, epoch=epoch)
        extra = [args["extra_note"]] if args.get("extra_note") else None
        packet = build_handoff(self.state, extra_observations=extra)
        self.state.handoff = packet
        booking = self.state.booking_for(device_id) if device_id else None
        if booking and booking.request_id:
            attached = self._svc_call(self.service.attach_handoff, booking.request_id, packet)
            if isinstance(attached, dict) and attached.get("status") == "error":
                packet = {**packet, "attach_warning": attached}
        return {"status": "ok", "handoff": packet,
                "instruction": "Summarise that a technician now has the full context. Do not make the customer repeat it."}

    def _dispatch(self, tool: str, args: Dict[str, Any], epoch: int) -> Dict[str, Any]:
        if tool == "list_appliances":
            return self._list(args)
        if tool == "get_appliance_status":
            return self._status(args, epoch=epoch)
        if tool == "get_appliance_diagnostics":
            return self._diagnostics(args, epoch=epoch)
        if tool == "get_troubleshooting_steps":
            return self._steps(args)
        if tool == "record_troubleshooting_step":
            return self._record_step(args, epoch=epoch)
        if tool == "verify_appliance_state":
            return self._verify(args, epoch=epoch)
        if tool == "find_service_slots":
            return self._find_slots(args)
        if tool == "book_samsung_service":
            return self._book(args, epoch=epoch)
        if tool == "get_service_request":
            return self._get_request(args)
        if tool == "prepare_human_handoff":
            return self._handoff(args, epoch=epoch)
        return error_result("unknown_tool", "use only the tools you were given")

    def _precheck(self, tool: str, args: Dict[str, Any]) -> Optional[str]:
        """Deterministic guards that run before the commit gate for already-known truth."""
        if tool == "book_samsung_service":
            device_id = self._device_id(args)
            existing = self.state.booking_for(device_id) if device_id else None
            slot = self._resolve_slot(args)
            window = canonicalize_window(args.get("preferred_window") or "")
            if existing and existing.status == "unknown":
                return json.dumps({
                    "status": "unknown_outcome",
                    "request_id": existing.request_id,
                    "instruction": (
                        "A booking was already attempted and may have gone through. "
                        "Do not book again. Tell the user it is uncertain and offer a human agent."
                    ),
                })
            if existing and existing.status == "confirmed":
                same = (slot and existing.slot_id == slot.get("slot_id")) or (
                    window and existing.window == window) or slot is None
                if same:
                    return json.dumps(self._existing_booking_reply(existing))
        if tool == "get_service_request":
            # cheap session-state answer when the backend is not needed
            pass
        return None

    def _ledger_args(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Canonical identity for the idempotency ledger.

        Booking keys on device + slot/window, not on free-text issue summaries,
        so a rephrased 'did you book it?' cannot create a second request.
        """
        if tool == "book_samsung_service":
            slot = self._resolve_slot(args) or {}
            return {
                "device_id": self._device_id(args),
                "slot_id": args.get("slot_id") or slot.get("slot_id") or "",
                "preferred_window": canonicalize_window(args.get("preferred_window") or "")
                or slot.get("window") or "",
            }
        if tool == "prepare_human_handoff":
            return {"device_id": self._device_id(args)}
        if tool == "record_troubleshooting_step":
            return {
                "device_id": self._device_id(args),
                "step_id": args.get("step_id"),
                "outcome": (args.get("outcome") or "").lower().replace(" ", "_"),
            }
        return args

    async def call(self, tool: str, raw_args: Dict[str, Any], epoch: Optional[int] = None) -> str:
        if tool not in SPEC_BY_NAME:
            log.warning("model called an unknown appliance tool: %s", tool)
            return json.dumps(error_result("unknown_tool", "use only the tools you were given"))
        args = coerce(tool, raw_args)
        plan_epoch = self.coord.epoch if epoch is None else epoch
        guarded = self._precheck(tool, args)
        if guarded is not None:
            self._emit("tool_guard", name=tool, args=args, result=json.loads(guarded).get("status"))
            return guarded

        ledger_args = self._ledger_args(tool, args)

        async def run() -> Any:
            # Worker thread: a blocking mock delay or HTTP call must not freeze the audio loop.
            return await asyncio.to_thread(self._dispatch, tool, args, plan_epoch)

        try:
            record = await self.coord.execute(
                tool, ledger_args, run,
                state_changing=tool in WRITE_TOOLS,
                timeout_s=self.timeout_write if tool in WRITE_TOOLS else self.timeout_read,
                on_committed=self._on_committed,
                epoch=epoch,
                use_ledger=tool in WRITE_TOOLS,
            )
        except Superseded:
            self.state.superseded += 1
            self._emit("superseded", name=tool, args=args)
            log.info("superseded at the gate: %s %s", tool, redact(args))
            return json.dumps({"status": "not_executed", "reason":
                               "the user started speaking again; wait for their full request"})
        self.last_tool_start, self.last_tool_end = record.started, record.finished
        self.state.epoch = self.coord.epoch
        if record.outcome == "ok":
            result = record.result
            if tool in ("get_appliance_status", "get_appliance_diagnostics") and isinstance(result, dict):
                # Stale reads may store facts on that device but must not steal the selection.
                if record.epoch != self.coord.epoch:
                    self._emit("stale_result", name=tool, args=args, planned_epoch=record.epoch)
            return json.dumps(result, default=str)
        if record.outcome == "cached":
            return json.dumps({"status": "already_done", "note":
                               "this exact call already ran in this conversation; not repeated",
                               "result": record.result}, default=str)
        if record.outcome == "unknown":
            if tool == "book_samsung_service":
                device_id = self._device_id(args)
                if device_id and device_id not in self.state.bookings:
                    slot = self._resolve_slot(args) or {}
                    self.state.bookings[device_id] = BookingRecord(
                        request_id="", device_id=device_id,
                        slot_id=str(slot.get("slot_id") or args.get("slot_id") or ""),
                        window=canonicalize_window(args.get("preferred_window") or "") or str(slot.get("window") or ""),
                        when_label=str(slot.get("when_label") or ""),
                        status="unknown")
            return json.dumps({"status": "unknown_outcome", "error": record.error, "instruction":
                               "do not retry this action; tell the user it may not have gone "
                               "through and offer to connect them with a human agent"})
        return json.dumps({"status": "error", "error": record.error, "instruction":
                           "tell the user this step failed and what they can do next"})

    def _on_committed(self, record: CallRecord) -> None:
        booking = self.state.booking_for(self.state.selected_device_id or "")
        self._emit("tool_committed", name=record.tool, args=record.args,
                   outcome=record.outcome,
                   service_request=None if booking is None else booking.request_id)


def default_smartthings() -> SmartThingsAdapter:
    mode = os.environ.get("DUET_SMARTTHINGS", "mock").strip().lower()
    token = os.environ.get("SMARTTHINGS_TOKEN") or os.environ.get("SMARTTHINGS_ACCESS_TOKEN") or ""
    if mode in ("real", "live", "api") and token:
        return RealSmartThingsAdapter(token)
    if mode in ("real", "live", "api") and not token:
        log.warning("DUET_SMARTTHINGS=%s but no SMARTTHINGS_TOKEN; using the mock adapter", mode)
    return MockSmartThingsAdapter()


def default_service() -> SamsungServiceAdapter:
    url = os.environ.get("SAMSUNG_SERVICE_API_URL") or ""
    if url:
        try:
            return RealSamsungServiceAdapter(url, os.environ.get("SAMSUNG_SERVICE_TOKEN") or "")
        except ServiceError as exc:
            log.warning("SAMSUNG_SERVICE_API_URL rejected (%s); using the mock service adapter", exc)
    return MockSamsungServiceAdapter()
