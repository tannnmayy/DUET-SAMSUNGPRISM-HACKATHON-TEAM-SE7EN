"""The phone's tools, through the DUET coordinator.

Every call the thinker makes is sent to the phone app over LiveKit RPC (method
`duet.tool`), and only after the coordinator lets it through: never while the
user is speaking, never before the commit hold, never twice. The phone answers
with a JSON result; a result with status `error` or `needs_permission` is a
failure (not remembered in the ledger, so it can run again once fixed).

Each step is also sent to the phone's screen (`emit`), so the DUET timeline
shows what was planned, what was held back because the user was still
correcting themselves, what ran, and what was refused as a repeat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from livekit import rtc

from ..coordinator import CallRecord, Coordinator, Superseded
from ..fdb_tools import coerce_props

log = logging.getLogger("duet.galaxy.tools")

TIMEOUTS = {"read": 8.0, "write": 10.0, "ui": 6.0}


class PhoneError(Exception):
    """The phone carried out the call and reported a failure (payload: its JSON)."""


def _norm(v: Any) -> Any:
    return " ".join(v.lower().split()) if isinstance(v, str) else v


class PhoneToolbox:
    """One per conversation. `send(tool, args, timeout)` delivers a call to the phone
    and returns its JSON reply (LiveKit RPC in the app, a fake in tests)."""

    def __init__(self, coord: Coordinator, tools: List[Dict[str, Any]],
                 send: Callable[[str, Dict[str, Any], float], Any],
                 emit: Callable[[Dict[str, Any]], None] = lambda msg: None,
                 consent: Optional[Callable[[str, Dict[str, Any]], Any]] = None) -> None:
        self.coord = coord
        self.specs = {t["name"]: t for t in tools}
        self.send = send
        self.emit = emit
        # consent(tool, args) -> bool: did the user ask for this action or agree to it?
        # Asked only for tools marked "consent" (settings, calls, texts, the home).
        self.consent = consent
        self.last_tool_start = 0.0
        self.last_tool_end = 0.0
        # calls of this request that the gate held back (the user kept talking): they never ran
        self.held_back: List[Dict[str, Any]] = []

    # -- the ledger's scope ------------------------------------------------------------
    def new_request(self) -> None:
        """The last request was answered. Repeatable actions ("call her again") and
        screens may run again; everything else stays exactly-once."""
        self.held_back = []
        for key, rec in list(self.coord.ledger.items()):
            spec = self.specs.get(rec.tool, {})
            if spec.get("repeatable") or spec.get("kind") == "ui":
                del self.coord.ledger[key]

    def _target(self, spec: Dict[str, Any], args: Dict[str, Any]) -> List[Any]:
        return [_norm(args.get(k)) for k in spec.get("target", [])]

    def _committed(self, record: CallRecord) -> None:
        """A write succeeded: earlier entries for the same thing are no longer the
        current state ("cancel 7:00" after "set 7:00" lets a later "set 7:00" run)."""
        spec = self.specs.get(record.tool, {})
        group = spec.get("group")
        if not group:
            return
        target = self._target(spec, record.args)
        for key, rec in list(self.coord.ledger.items()):
            other = self.specs.get(rec.tool, {})
            if rec is not record and other.get("group") == group and self._target(other, rec.args) == target:
                del self.coord.ledger[key]

    # -- one call ------------------------------------------------------------------------
    async def call(self, tool: str, raw_args: Dict[str, Any], epoch: Optional[int] = None) -> str:
        spec = self.specs.get(tool)
        if spec is None:
            log.warning("model called an unknown tool: %s", tool)
            return json.dumps({"status": "error", "error": "unknown tool '%s'" % tool,
                               "instruction": "use only the tools you were given"})
        args = coerce_props(spec["parameters"]["properties"], raw_args)
        kind = spec.get("kind", "read")
        timeout = TIMEOUTS.get(kind, 8.0)
        self.emit({"kind": "tool_planned", "tool": tool, "args": args, "hold_s": round(self.coord.hold(), 1)})
        if spec.get("consent") and self.consent is not None:
            try:
                allowed = await self.consent(tool, args)
            except Exception as exc:  # a failed check never blocks what the user asked for
                log.warning("consent check failed: %s", exc)
                allowed = True
            if not allowed:
                self.emit({"kind": "tool_asked", "tool": tool, "args": args})
                return json.dumps({"status": "not_executed", "reason":
                                   "not done yet: the user has not asked for this. Offer it in one short "
                                   "question; if they say yes, call this tool again"})

        async def run() -> Any:
            try:
                out = await self.send(tool, args, timeout)
            except rtc.RpcError as exc:
                if exc.code == rtc.RpcError.ErrorCode.RESPONSE_TIMEOUT:
                    raise asyncio.TimeoutError() from exc
                raise
            result = json.loads(out) if isinstance(out, str) and out else (out or {})
            if isinstance(result, dict) and result.get("status") in ("error", "needs_permission"):
                raise PhoneError(json.dumps(result))
            return result

        started = time.time()
        try:
            record = await self.coord.execute(tool, args, run, state_changing=kind == "write",
                                              timeout_s=timeout + 1.0, on_committed=self._committed,
                                              epoch=epoch)
        except Superseded:
            log.info("held back at the gate: %s %s", tool, args)
            self.held_back.append({"tool": tool, "args": args})
            self.emit({"kind": "tool_dropped", "tool": tool, "args": args,
                       "why": "you kept talking, so this plan was never carried out"})
            return json.dumps({"status": "not_executed", "reason":
                               "the user started speaking again; wait for their full request"})
        self.last_tool_start, self.last_tool_end = record.started, record.finished
        if record.outcome == "ok":
            self.emit({"kind": "tool_done", "tool": tool, "args": args, "result": record.result,
                       "after_s": round(time.time() - started, 2)})
            return json.dumps(record.result, default=str)
        if record.outcome == "cached":
            self.emit({"kind": "tool_cached", "tool": tool, "args": args})
            return json.dumps({"status": "already_done", "note":
                               "this exact action already ran for this request; not repeated",
                               "result": record.result}, default=str)
        if record.outcome == "unknown":
            self.emit({"kind": "tool_unknown", "tool": tool, "args": args, "error": record.error})
            return json.dumps({"status": "unknown_outcome", "error": record.error, "instruction":
                               "do not retry; tell the user it may not have gone through and to check "
                               "on the phone"})
        if record.error.startswith("PhoneError: "):
            reply = record.error[len("PhoneError: "):]
            self.emit({"kind": "tool_failed", "tool": tool, "args": args, "result": json.loads(reply)})
            return reply
        self.emit({"kind": "tool_failed", "tool": tool, "args": args, "error": record.error})
        return json.dumps({"status": "error", "error": record.error, "instruction":
                           "tell the user this step failed and what they can do next"})


def rpc_sender(room: rtc.Room, identity: Callable[[], str]):
    """send() over LiveKit RPC to the phone participant."""
    async def send(tool: str, args: Dict[str, Any], timeout: float) -> str:
        return await room.local_participant.perform_rpc(
            destination_identity=identity(), method="duet.tool",
            payload=json.dumps({"tool": tool, "args": args}), response_timeout=timeout)
    return send
