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
import datetime as dt
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from livekit import rtc

from ..coordinator import CallRecord, Coordinator, Superseded
from ..fdb_tools import coerce_props
from .confirm import confirmation

log = logging.getLogger("duet.galaxy.tools")

TIMEOUTS = {"read": 8.0, "write": 10.0, "ui": 6.0}


class PhoneError(Exception):
    """The phone carried out the call and reported a failure (payload: its JSON)."""


# The user's latest words leave a choice open: two options ("6:30 or maybe 7"), or they say they
# have not decided. Not "I'm not sure why...", which is a question, not an undecided request.
_UNSURE = re.compile(
    r"\bor maybe\b"
    r"|\beither\b.{0,40}\bor\b"
    r"|\b(not sure|unsure|don'?t know|can'?t decide|haven'?t decided)\b(?!\s+(why|how|what|where|when))"
    r"|\b(hmm+|umm*)\W*$", re.I)


def undecided(text: str) -> bool:
    """Does the last sentence of what the user has said leave the choice open? Only the last one:
    "...or maybe 7, hmm. Seven." has been decided."""
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if s.strip()]
    return bool(sentences) and bool(_UNSURE.search(sentences[-1]))


# The user's words stop mid-phrase ("Call my", "Set an alarm for"): whatever the model guessed,
# nothing is done; DUET asks for the rest. ("on", "in", "off" are left out: "turn the lights on".)
_CUT_OFF = re.compile(r"\b(my|the|a|an|to|for|and|or|with|his|her|their|our|your|some|from|about|at)\W*$", re.I)


def cut_off(text: str) -> bool:
    return bool(_CUT_OFF.search((text or "").strip()))


# A clock time said without the half of the day ("set an alarm for 6:30") means the next one
# from now, whichever mind wrote the time down. Measured 16 Oct 2026, 23:50: the fast mind wrote
# 18:30 for "6:30"; the next 6:30 was 06:30.
_DAYPART = re.compile(r"\b(a\.?\s?m\.?|p\.?\s?m\.?|morning|evening|night|tonight|afternoon|noon|midnight)\b", re.I)
_HOUR_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
               "ten": 10, "eleven": 11, "twelve": 12}


def next_occurrence(hhmm: str, words: str, now) -> str:
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*$", str(hhmm or ""))
    if not m:
        return hhmm
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (words or "").strip() or hour > 23 or minute > 59 or _DAYPART.search(words):
        return hhmm                    # no words to go by, or the user said which half of the day
    said = {int(n) for n in re.findall(r"\d+", words)} | {
        v for w, v in _HOUR_WORDS.items() if re.search(r"\b%s\b" % w, words, re.I)}
    if hour >= 13 and hour in said:
        return hhmm                    # a 24-hour time, said as such
    if not ({hour % 12, hour % 12 + 12, 12 if hour % 12 == 0 else -1} & said):
        return hhmm                    # this time is not from these words: leave it as it is
    now_min = now.hour * 60 + now.minute
    best = min(((h * 60 + minute - now_min - 1) % (24 * 60), h) for h in (hour % 12, hour % 12 + 12))
    return "%02d:%02d" % (best[1], minute)


def _norm(v: Any) -> Any:
    return " ".join(v.lower().split()) if isinstance(v, str) else v


class PhoneToolbox:
    """One per conversation. `send(tool, args, timeout)` delivers a call to the phone
    and returns its JSON reply (LiveKit RPC in the app, a fake in tests)."""

    def __init__(self, coord: Coordinator, tools: List[Dict[str, Any]],
                 send: Callable[[str, Dict[str, Any], float], Any],
                 emit: Callable[[Dict[str, Any]], None] = lambda msg: None,
                 consent: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
                 server: Optional[Dict[str, Callable[[Dict[str, Any]], Any]]] = None) -> None:
        self.coord = coord
        self.specs = {t["name"]: t for t in tools}
        self.send = send
        # tools DUET runs itself rather than on the phone (the weather, web answers)
        self.server = server or {}
        self.emit = emit
        # consent(tool, args) -> bool: did the user ask for this action or agree to it?
        # Asked only for tools marked "consent" (settings, calls, texts, the home).
        self.consent = consent
        self.last_tool_start = 0.0
        self.last_tool_end = 0.0
        # calls of this request that the gate held back (the user kept talking): they never ran
        self.held_back: List[Dict[str, Any]] = []
        # calls of this request the consent check refused (the user is asked first, once)
        self.asked: set = set()
        # the last call that ran: (tool, args, the phone's reply, its spoken confirmation or None)
        self.last_done: Optional[tuple] = None

    # -- the ledger's scope ------------------------------------------------------------
    def new_request(self) -> None:
        """The last request was answered. Repeatable actions ("call her again") and
        screens may run again; everything else stays exactly-once."""
        self.held_back = []
        self.asked = set()
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
        self.last_done = None
        if tool in ("set_alarm", "set_reminder") and args.get("time"):
            args["time"] = next_occurrence(args["time"], self.coord.open_utterance, dt.datetime.now())
        timeout = float(spec.get("timeout", TIMEOUTS.get(kind, 8.0)))
        self.emit({"kind": "tool_planned", "tool": tool, "args": args, "hold_s": round(self.coord.hold(), 1)})
        if kind in ("write", "ui") and cut_off(self.coord.open_utterance):
            self.emit({"kind": "tool_asked", "tool": tool, "args": args})
            return json.dumps({"status": "not_executed", "reason":
                               "the user's words stop mid-phrase; ask them, in a few words, for the rest"})
        if kind in ("write", "ui") and undecided(self.coord.open_utterance):
            # "Call Rahul, or maybe Priya, I'm not sure": the user has not chosen, so nothing is
            # done whatever the model decided; it asks instead (measured 16 Oct: the cloud model
            # with minimal thinking called both)
            self.emit({"kind": "tool_asked", "tool": tool, "args": args})
            return json.dumps({"status": "not_executed", "reason":
                               "the user has not decided yet. Ask which one, in one short question that names "
                               "the options; act only on their answer"})
        if spec.get("consent") and self.consent is not None:
            key = (tool, json.dumps(args, sort_keys=True))
            if key in self.asked:
                # asked about already in this request: the model must stop and ask the user (17 Oct 2026:
                # the cloud mind called a refused call eight times in a row, 14 s of silence)
                return json.dumps({"status": "not_executed", "reason":
                                   "refused already: STOP calling tools now. Reply to the user with one short "
                                   "question offering this; act only on their yes"})
            try:
                allowed = await self.consent(tool, args)
            except Exception as exc:  # a failed check never blocks what the user asked for
                log.warning("consent check failed: %s", exc)
                allowed = True
            if not allowed:
                self.asked.add(key)
                self.emit({"kind": "tool_asked", "tool": tool, "args": args})
                return json.dumps({"status": "not_executed", "reason":
                                   "not done yet: the user has not asked for this. Offer it in one short "
                                   "question; if they say yes, call this tool again"})

        async def run() -> Any:
            if tool in self.server:
                out = await self.server[tool](args)
            else:
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
            said = confirmation(tool, args, record.result)
            self.last_done = (tool, args, record.result, said)
            self.emit({"kind": "tool_done", "tool": tool, "args": args, "result": record.result,
                       "after_s": round(time.time() - started, 2)})
            if said and isinstance(record.result, dict):
                # the thinker sees exactly what the user has already been told
                return json.dumps({**record.result, "already_said_to_user": said}, default=str)
            return json.dumps(record.result, default=str)
        if record.outcome == "cached" and record.error:
            # tried before and its outcome is unknown (the phone did not answer): still unknown, never
            # "already done" (17 Oct 2026: the cloud mind retried a timer the phone had not answered,
            # read already_done, and said the timer was set)
            self.emit({"kind": "tool_unknown", "tool": tool, "args": args, "error": record.error})
            return json.dumps({"status": "unknown_outcome", "error": record.error, "instruction":
                               "this was already tried and the phone did not confirm it: do not retry; tell "
                               "the user it may not have gone through and to check on the phone"})
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
