"""Family Care tools, run through the existing DUET coordinator.

The thinker decides *what* to call. This module decides what is true and
whether the action is allowed: commit gate, idempotency ledger, Knox consent
for Watch vitals, and exactly-once texts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

from ..coordinator import CallRecord, Coordinator, Superseded
from .household import FamilyHousehold, HouseholdError, MockFamilyHousehold
from .knox import KnoxError, KnoxVault, MockKnoxVault, RealKnoxVault
from .messaging import CareMessenger, MessagingError, MockCareMessenger, RealCareMessenger
from .privacy import redact
from .state import FamilySessionState, OutboundRecord
from .watch import GalaxyWatchAdapter, MockGalaxyWatchAdapter, WatchError

log = logging.getLogger("duet.family")

S = {"type": "string"}
B = {"type": "boolean"}


def _obj(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


TOOL_SPECS: List[Dict[str, Any]] = [
    {"name": "list_household", "kind": "read",
     "description": "List SmartThings Family Care household members. Optional query filters by name (Mum, Dad).",
     "parameters": _obj({
         "query": {**S, "description": "Optional name the user said, e.g. 'Mum' or 'Dad'."},
     }, [])},
    {"name": "get_member_status", "kind": "read",
     "description": "Read live presence and Family Care inactivity for one member. Never invent a medical diagnosis.",
     "parameters": _obj({
         "member_id": {**S, "description": "Member id from list_household."},
     }, ["member_id"])},
    {"name": "request_health_consent", "kind": "write",
     "description": "Ask Knox to allow a Watch health read for this member in this session. Call only when the user asked to check heart rate or vitals.",
     "parameters": _obj({
         "member_id": {**S, "description": "Member whose Watch vitals will be read."},
     }, [])},
    {"name": "get_watch_vitals", "kind": "read",
     "description": "Read Galaxy Watch heart rate after Knox consent. Returns consent_required if consent is missing. Never invent a heart rate.",
     "parameters": _obj({
         "member_id": {**S, "description": "Member whose Watch to read."},
     }, [])},
    {"name": "send_care_text", "kind": "write",
     "description": "Send one caregiver text via DUET. Exactly-once for the same contact and purpose. Use when the user asks to text someone.",
     "parameters": _obj({
         "contact_id": {**S, "description": "Contact id (contact-priya, contact-me) or a name the user said (Priya)."},
         "purpose": {**S, "description": "Short purpose, e.g. vitals_alert or inactivity_alert."},
         "body": {**S, "description": "Message body in the user's words plus facts the tools returned."},
     }, ["contact_id"])},
    {"name": "place_care_call", "kind": "write",
     "description": "Place a call to the user, a caregiver, or emergency services. Emergency/ambulance requires explicit_emergency true. Exactly-once per target.",
     "parameters": _obj({
         "target_id": {**S, "description": "contact-me, contact-priya, ambulance, or a name."},
         "reason": {**S, "description": "Why the user asked to call."},
         "explicit_emergency": {**B, "description": "True only if the user clearly asked for ambulance or emergency services."},
     }, ["target_id"])},
    {"name": "get_outbound_status", "kind": "read",
     "description": "Look up an existing text or call. Use when the user asks if you already sent it.",
     "parameters": _obj({
         "record_id": {**S, "description": "MSG- or CALL- id if known."},
         "contact_id": {**S, "description": "Contact to look up when the id is not given."},
         "purpose": {**S, "description": "Purpose of the text, if known."},
     }, [])},
    {"name": "prepare_care_handoff", "kind": "write",
     "description": "Build a caregiver handoff packet from session state so they do not have to repeat the situation.",
     "parameters": _obj({
         "member_id": {**S, "description": "Member the handoff is for."},
         "extra_note": {**S, "description": "Optional extra observation from the user."},
     }, [])},
]

SPEC_BY_NAME = {s["name"]: s for s in TOOL_SPECS}
WRITE_TOOLS = {s["name"] for s in TOOL_SPECS if s["kind"] == "write"}

_EMERGENCY = re.compile(r"\b(ambulance|emergency|999|911|112)\b", re.I)


def coerce(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    props = SPEC_BY_NAME[tool]["parameters"]["properties"]
    out: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        want = props.get(key, {}).get("type")
        if want == "boolean":
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes")
            else:
                value = bool(value)
        elif want == "string" and not isinstance(value, str):
            value = str(value)
        if isinstance(value, str):
            value = value.strip()
        out[key] = value
    return out


def error_result(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    out = {"status": "error", "error": code, "instruction": message}
    out.update(extra)
    return out


def _match_name(record: Dict[str, Any], query: str) -> bool:
    if not query:
        return True
    blob = " ".join(str(record.get(k) or "") for k in ("name", "member_id", "contact_id", "room", "kind")).lower()
    q = query.lower().strip()
    aliases = {"mom": "mum", "mother": "mum", "mum": "mum", "dad": "dad", "father": "dad",
               "me": "me", "priya": "priya"}
    want = aliases.get(q, q)
    return bool(re.search(r"\b" + re.escape(want) + r"\b", blob, re.I))


def canonicalize_target(raw: str) -> str:
    q = (raw or "").strip().lower()
    if _EMERGENCY.search(q) or q in ("ambulance", "emergency"):
        return "ambulance"
    if q in ("me", "myself", "us", "contact-me", "user"):
        return "contact-me"
    if "priya" in q or q == "contact-priya":
        return "contact-priya"
    return raw.strip()


def canonicalize_purpose(raw: str) -> str:
    q = " ".join((raw or "").lower().split())
    if "inactiv" in q:
        return "inactivity_alert"
    if "vital" in q or "heart" in q or "hr" == q:
        return "vitals_alert"
    if not q:
        return "care_update"
    return q.replace(" ", "_")[:40]


class FamilyToolbox:
    """One per conversation. Same call(tool, args, epoch) contract as FdbToolbox."""

    def __init__(
        self,
        room_name: str,
        coordinator: Coordinator,
        *,
        household: Optional[FamilyHousehold] = None,
        knox: Optional[KnoxVault] = None,
        watch: Optional[GalaxyWatchAdapter] = None,
        messenger: Optional[CareMessenger] = None,
        on_trace: Optional[Callable[..., None]] = None,
    ) -> None:
        self.room_name = room_name
        self.coord = coordinator
        self.state = FamilySessionState()
        self.household = household or default_household()
        self.knox = knox or default_knox()
        self.watch = watch or default_watch()
        self.messenger = messenger or default_messenger()
        self._trace = on_trace or (lambda *a, **k: None)
        self.last_tool_start = 0.0
        self.last_tool_end = 0.0
        self.timeout_read = 8.0
        self.timeout_write = 12.0

    @property
    def specs(self) -> List[Dict[str, Any]]:
        return TOOL_SPECS

    def session_note(self) -> str:
        self.state.epoch = self.coord.epoch
        return self.state.session_note()

    def _emit(self, kind: str, **data: Any) -> None:
        try:
            self._trace(kind, epoch=self.coord.epoch, member=self.state.selected_member_id,
                        intent=self.state.intent, **redact(data))
        except Exception:
            log.debug("family trace failed", exc_info=True)

    def _member_id(self, args: Dict[str, Any]) -> str:
        raw = args.get("member_id") or self.state.selected_member_id or ""
        if raw in self.state.members:
            return raw
        for mid, rec in self.state.members.items():
            if _match_name(rec, raw):
                return mid
        return raw

    def _contact_id(self, raw: str) -> str:
        target = canonicalize_target(raw)
        if target in ("contact-me", "contact-priya", "ambulance"):
            return target
        contacts = []
        try:
            contacts = self.household.list_contacts()
        except HouseholdError:
            pass
        for c in contacts:
            if _match_name(c, raw) or c.get("contact_id") == raw:
                return str(c["contact_id"])
        return target or raw

    def _hh(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HouseholdError as exc:
            self.state.last_failure = "household:%s" % exc.code
            if exc.code == "auth":
                return error_result(
                    "authentication_error",
                    "SmartThings Family Care authentication failed. Do not invent members or status.",
                )
            if exc.code == "timeout":
                return error_result("timeout", "SmartThings Family Care timed out. Do not invent status.")
            if exc.code == "not_found":
                return error_result("not_found", str(exc), retry=False)
            return error_result(exc.code, str(exc))

    def _list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self.state.intent = "check"
        members = self._hh(self.household.list_members)
        if isinstance(members, dict) and members.get("status") == "error":
            return members
        query = args.get("query") or ""
        matched = [m for m in members if _match_name(m, query)]
        for m in matched:
            self.state.remember_member(m["member_id"], m)
        return {"status": "ok", "members": matched, "query": query or None, "count": len(matched),
                "simulated": True}

    def _status(self, args: Dict[str, Any], *, epoch: int) -> Dict[str, Any]:
        self.state.intent = "check"
        member_id = self._member_id(args)
        if not member_id:
            return error_result("missing_member", "List the household and pick a member before reading status.")
        payload = self._hh(self.household.get_member, member_id)
        if not isinstance(payload, dict):
            return error_result("error", "SmartThings Family Care returned an unexpected result.")
        if payload.get("status") == "ok":
            self.state.remember_member(member_id, payload)
            if epoch == self.coord.epoch:
                changed = self.state.select_member(member_id, epoch=epoch)
                if changed:
                    self._emit("superseded_workflow", member=member_id, workflow_id=self.state.workflow_id)
        return payload

    def _consent(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self.state.intent = "health"
        member_id = self._member_id(args)
        if not member_id:
            return error_result("missing_member", "Name the family member before requesting Knox health consent.")
        try:
            payload = self.knox.grant_health_read(member_id)
        except KnoxError as exc:
            self.state.last_failure = "knox:%s" % exc.code
            if exc.code == "auth":
                return error_result("authentication_error", "Knox authentication failed. Do not invent vitals.")
            return error_result(exc.code, str(exc))
        if payload.get("status") == "ok":
            self.state.consent[member_id] = True
        return payload

    def _vitals(self, args: Dict[str, Any], *, epoch: int) -> Dict[str, Any]:
        self.state.intent = "health"
        member_id = self._member_id(args)
        if not member_id:
            return error_result("missing_member", "Name the family member before reading Watch vitals.")
        member = self.state.members.get(member_id) or {}
        if not member:
            status = self._status({"member_id": member_id}, epoch=epoch)
            if status.get("status") != "ok":
                return status
            member = self.state.members.get(member_id) or {}
        try:
            allowed = self.knox.has_health_read(member_id) or self.state.consent.get(member_id)
        except KnoxError as exc:
            self.state.last_failure = "knox:%s" % exc.code
            return error_result(exc.code, "Knox could not confirm consent. Do not invent a heart rate.")
        if not allowed:
            return {
                "status": "consent_required",
                "member_id": member_id,
                "knox": "health_read",
                "instruction": (
                    "Do not invent heart rate. Ask the user to allow a Knox-protected health read "
                    "for this family member, then call request_health_consent."
                ),
            }
        watch_id = member.get("watch_id")
        if not watch_id:
            return {
                "status": "unavailable",
                "reason": "no_watch",
                "member_id": member_id,
                "instruction": "No Galaxy Watch is paired. Do not invent vitals.",
            }
        try:
            payload = self.watch.get_vitals(watch_id)
        except WatchError as exc:
            self.state.last_failure = "watch:%s" % exc.code
            if exc.code == "offline":
                return error_result("offline", "The Galaxy Watch is offline. Do not invent vitals.")
            if exc.code == "timeout":
                return error_result("timeout", "The Galaxy Watch timed out. Do not invent vitals.")
            return error_result(exc.code, str(exc))
        if epoch == self.coord.epoch:
            self.state.vitals[member_id] = payload
        return payload

    def _existing_text_reply(self, rec: OutboundRecord) -> Dict[str, Any]:
        return {
            "status": "already_done",
            "note": "A text with this purpose was already sent to this contact; it was not sent again.",
            "record_id": rec.record_id,
            "target_id": rec.target_id,
            "target_name": rec.target_name,
            "purpose": rec.purpose,
            "outbound_status": rec.status,
        }

    def _purpose(self, args: Dict[str, Any]) -> str:
        # Purpose is the idempotency key. Never derive it from the free-text body
        # or a rephrased "did you text her?" would send a second message.
        return canonicalize_purpose(args.get("purpose") or "")

    def _unknown_text_reply(self, rec: OutboundRecord) -> Dict[str, Any]:
        return {
            "status": "unknown_outcome",
            "record_id": rec.record_id,
            "target_id": rec.target_id,
            "purpose": rec.purpose,
            "instruction": (
                "A text may already have gone through. Do not send again. "
                "Tell the user it is uncertain."
            ),
        }

    def _remember_unknown_text(self, contact_id: str, purpose: str, name: str) -> OutboundRecord:
        existing = self.state.text_for(contact_id, purpose)
        if existing is not None:
            return existing
        rec = OutboundRecord(
            kind="text", record_id="", target_id=contact_id,
            target_name=name, purpose=purpose, status="unknown",
        )
        self.state.outbounds.append(rec)
        return rec

    def _text(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self.state.intent = "text"
        contact_id = self._contact_id(args.get("contact_id") or "")
        purpose = self._purpose(args)
        if not contact_id:
            return error_result("missing_contact", "Name who to text (for example Priya).")
        if contact_id == "ambulance":
            return error_result(
                "refused",
                "Do not text emergency services. If the user asked for an ambulance, use place_care_call with explicit_emergency true.",
            )
        name = "Priya" if contact_id == "contact-priya" else ("Me" if contact_id == "contact-me" else contact_id)
        existing = self.state.text_for(contact_id, purpose)
        if existing and existing.status == "unknown":
            return self._unknown_text_reply(existing)
        if existing and existing.status == "sent":
            return self._existing_text_reply(existing)
        member = self.state.members.get(self.state.selected_member_id or "") or {}
        vitals = self.state.vitals.get(self.state.selected_member_id or "") or {}
        body = args.get("body") or (
            "Family Care update for %s. Inactivity=%s. Watch band=%s."
            % (member.get("name") or "the family member",
               member.get("inactivity_alert"),
               vitals.get("band") or "not read")
        )
        try:
            payload = self.messenger.send_text(contact_id=contact_id, body=body, purpose=purpose)
        except MessagingError as exc:
            self.state.last_failure = "sms:%s" % exc.code
            if exc.code == "timeout":
                rec = self._remember_unknown_text(contact_id, purpose, name)
                return self._unknown_text_reply(rec)
            return error_result(exc.code, "Messaging failed. Do not claim the text was sent.")
        rec = OutboundRecord(
            kind="text", record_id=str(payload.get("record_id") or ""),
            target_id=contact_id, target_name=name, purpose=purpose,
            status="sent" if payload.get("status") == "ok" else "unknown",
        )
        self.state.outbounds.append(rec)
        payload["target_name"] = name
        payload["purpose"] = purpose
        payload["instruction"] = "The text was sent once. If asked whether you sent it, call get_outbound_status."
        return payload

    def _call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        self.state.intent = "call"
        target = self._contact_id(args.get("target_id") or "")
        reason = args.get("reason") or ""
        explicit = bool(args.get("explicit_emergency"))
        if target == "ambulance" and not explicit:
            return {
                "status": "refused",
                "target_id": "ambulance",
                "instruction": (
                    "Do not call emergency services unless the user clearly asked for an ambulance "
                    "or emergency. Offer to text a caregiver or call the user instead."
                ),
            }
        existing = self.state.call_for(target)
        if existing and existing.status in ("placed", "unknown"):
            return {
                "status": "already_done",
                "note": "A call to this target was already placed; it was not placed again.",
                "record_id": existing.record_id,
                "target_id": existing.target_id,
                "outbound_status": existing.status,
            }
        try:
            payload = self.messenger.place_call(target_id=target, reason=reason)
        except MessagingError as exc:
            self.state.last_failure = "call:%s" % exc.code
            if exc.code == "timeout":
                rec = OutboundRecord(
                    kind="call", record_id="", target_id=target, target_name=target,
                    purpose=canonicalize_purpose(reason), status="unknown",
                )
                self.state.outbounds.append(rec)
                return {
                    "status": "unknown_outcome",
                    "record_id": rec.record_id,
                    "target_id": target,
                    "instruction": "A call may already have been placed. Do not place it again.",
                }
            return error_result(exc.code, "The call failed. Do not claim it connected.")
        name = target
        rec = OutboundRecord(
            kind="call", record_id=str(payload.get("record_id") or ""),
            target_id=target, target_name=name, purpose=canonicalize_purpose(reason),
            status="placed" if payload.get("status") == "ok" else "unknown",
        )
        self.state.outbounds.append(rec)
        payload["instruction"] = "The call was placed once. Do not place it again."
        return payload

    def _outbound(self, args: Dict[str, Any]) -> Dict[str, Any]:
        record_id = args.get("record_id") or ""
        if record_id:
            for rec in self.state.outbounds:
                if rec.record_id == record_id:
                    return {"status": "ok", "record": rec.__dict__, "simulated": True}
        contact_id = self._contact_id(args.get("contact_id") or "")
        purpose = self._purpose(args) if args.get("purpose") else ""
        if contact_id:
            rec = self.state.text_for(contact_id, purpose) if purpose else None
            if rec is None:
                rec = next(
                    (o for o in reversed(self.state.outbounds)
                     if o.kind == "text" and o.target_id == contact_id
                     and o.status in ("sent", "unknown")),
                    None,
                )
            if rec:
                return {"status": "ok", "record": rec.__dict__, "simulated": True}
            call = self.state.call_for(contact_id)
            if call:
                return {"status": "ok", "record": call.__dict__, "simulated": True}
        if self.state.outbounds:
            rec = self.state.outbounds[-1]
            return {"status": "ok", "record": rec.__dict__, "simulated": True}
        return {"status": "not_found", "instruction": "No text or call is on record. Do not invent one."}

    def _handoff(self, args: Dict[str, Any], *, epoch: int) -> Dict[str, Any]:
        member_id = self._member_id(args)
        member = dict(self.state.members.get(member_id or "", {}))
        vitals = dict(self.state.vitals.get(member_id or "", {}))
        note = args.get("extra_note") or ""
        if note:
            self.state.user_observations.append(note)
        packet = {
            "member": {k: member.get(k) for k in ("member_id", "name", "room", "presence", "inactivity_alert", "watch_id")},
            "knox_health_consent": bool(self.state.consent.get(member_id or "")),
            "vitals": {
                "band": vitals.get("band"),
                "heart_rate_bpm": vitals.get("heart_rate_bpm"),
                "source": vitals.get("source"),
                "simulated": vitals.get("simulated"),
            } if vitals else None,
            "outbounds": [o.__dict__ for o in self.state.outbounds],
            "user_observations": list(self.state.user_observations),
            "simulated": True,
            "instruction": "Give this packet to the caregiver. Do not add medical claims the tools did not return.",
        }
        self.state.handoff = packet
        return {"status": "ok", "handoff": packet}

    def _dispatch(self, tool: str, args: Dict[str, Any], epoch: int) -> Dict[str, Any]:
        if tool == "list_household":
            return self._list(args)
        if tool == "get_member_status":
            return self._status(args, epoch=epoch)
        if tool == "request_health_consent":
            return self._consent(args)
        if tool == "get_watch_vitals":
            return self._vitals(args, epoch=epoch)
        if tool == "send_care_text":
            return self._text(args)
        if tool == "place_care_call":
            return self._call(args)
        if tool == "get_outbound_status":
            return self._outbound(args)
        if tool == "prepare_care_handoff":
            return self._handoff(args, epoch=epoch)
        return error_result("unknown_tool", "use only the tools you were given")

    def _precheck(self, tool: str, args: Dict[str, Any]) -> Optional[str]:
        if tool == "send_care_text":
            contact_id = self._contact_id(args.get("contact_id") or "")
            purpose = self._purpose(args)
            existing = self.state.text_for(contact_id, purpose) if contact_id else None
            if existing and existing.status == "unknown":
                return json.dumps(self._unknown_text_reply(existing))
            if existing and existing.status == "sent":
                return json.dumps(self._existing_text_reply(existing))
        if tool == "place_care_call":
            target = self._contact_id(args.get("target_id") or "")
            explicit = bool(args.get("explicit_emergency"))
            if target == "ambulance" and not explicit:
                return json.dumps({
                    "status": "refused",
                    "target_id": "ambulance",
                    "instruction": (
                        "Do not call emergency services unless the user clearly asked for an ambulance "
                        "or emergency. Offer to text a caregiver or call the user instead."
                    ),
                })
            existing = self.state.call_for(target) if target else None
            if existing and existing.status in ("placed", "unknown"):
                return json.dumps({
                    "status": "already_done",
                    "record_id": existing.record_id,
                    "note": "A call to this target was already placed; it was not placed again.",
                })
        return None

    def _ledger_args(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool == "send_care_text":
            return {
                "contact_id": self._contact_id(args.get("contact_id") or ""),
                "purpose": self._purpose(args),
            }
        if tool == "place_care_call":
            return {"target_id": self._contact_id(args.get("target_id") or "")}
        if tool == "request_health_consent":
            return {"member_id": self._member_id(args), "scope": "health_read"}
        if tool == "prepare_care_handoff":
            return {"member_id": self._member_id(args)}
        return args

    async def call(self, tool: str, raw_args: Dict[str, Any], epoch: Optional[int] = None) -> str:
        if tool not in SPEC_BY_NAME:
            log.warning("model called an unknown family tool: %s", tool)
            return json.dumps(error_result("unknown_tool", "use only the tools you were given"))
        args = coerce(tool, raw_args)
        plan_epoch = self.coord.epoch if epoch is None else epoch
        guarded = self._precheck(tool, args)
        if guarded is not None:
            self._emit("tool_guard", name=tool, args=args, result=json.loads(guarded).get("status"))
            return guarded

        ledger_args = self._ledger_args(tool, args)

        async def run() -> Any:
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
            if tool == "get_member_status" and isinstance(result, dict):
                if record.epoch != self.coord.epoch:
                    self._emit("stale_result", name=tool, args=args, planned_epoch=record.epoch)
            return json.dumps(result, default=str)
        if record.outcome == "cached":
            return json.dumps({"status": "already_done", "note":
                               "this exact call already ran in this conversation; not repeated",
                               "result": record.result}, default=str)
        if record.outcome == "unknown":
            if tool == "send_care_text":
                contact_id = self._contact_id(args.get("contact_id") or "")
                purpose = self._purpose(args)
                name = "Priya" if contact_id == "contact-priya" else (
                    "Me" if contact_id == "contact-me" else contact_id)
                if contact_id:
                    self._remember_unknown_text(contact_id, purpose, name)
            elif tool == "place_care_call":
                target = self._contact_id(args.get("target_id") or "")
                if target and self.state.call_for(target) is None:
                    self.state.outbounds.append(OutboundRecord(
                        kind="call", record_id="", target_id=target,
                        target_name=target, purpose=canonicalize_purpose(args.get("reason") or ""),
                        status="unknown"))
            return json.dumps({"status": "unknown_outcome", "error": record.error, "instruction":
                               "do not retry this action; tell the user it may not have gone through"})
        return json.dumps({"status": "error", "error": record.error, "instruction":
                           "tell the user this step failed and what they can do next"})

    def _on_committed(self, record: CallRecord) -> None:
        self._emit("tool_committed", name=record.tool, args=record.args, outcome=record.outcome)


def default_household() -> FamilyHousehold:
    return MockFamilyHousehold()


def default_knox() -> KnoxVault:
    token = os.environ.get("DUET_KNOX_TOKEN") or ""
    url = os.environ.get("DUET_KNOX_URL") or ""
    if token:
        try:
            return RealKnoxVault(token, url)
        except KnoxError as exc:
            log.warning("DUET_KNOX_TOKEN rejected (%s); using the mock Knox vault", exc)
    return MockKnoxVault()


def default_watch() -> GalaxyWatchAdapter:
    return MockGalaxyWatchAdapter()


def default_messenger() -> CareMessenger:
    url = os.environ.get("DUET_FAMILY_SMS_URL") or ""
    if url:
        try:
            return RealCareMessenger(url, os.environ.get("DUET_FAMILY_SMS_TOKEN") or "")
        except MessagingError as exc:
            log.warning("DUET_FAMILY_SMS_URL rejected (%s); using the mock messenger", exc)
    return MockCareMessenger()
