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

_HOURS = {w: i + 1 for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split())}
_CLOCK = re.compile(r"\b(half past|quarter past|quarter to)\s+(\d{1,2}|%s)\b|\b(\d{1,2}|%s) o'?clock\b"
                    % ("|".join(_HOURS), "|".join(_HOURS)), re.I)


def message_times(text: str) -> str:
    """Times in a text message in digits, whatever the model wrote: "half past three" -> "3:30",
    "quarter to five" -> "4:45", "six o'clock" -> "6:00" (18 Oct 2026: a Drive text gave the arrival
    time in words although told to use digits; a reader glancing at a phone wants the number)."""
    def hour(word: str) -> int:
        return int(word) if word.isdigit() else _HOURS[word.lower()]

    def digits(m: "re.Match") -> str:
        if m.group(3):
            h = hour(m.group(3))
            return "%d:00" % h if 1 <= h <= 12 else m.group(0)
        h, how = hour(m.group(2)), m.group(1).lower()
        if not 1 <= h <= 12:
            return m.group(0)
        if how == "half past":
            return "%d:30" % h
        if how == "quarter past":
            return "%d:15" % h
        return "%d:45" % ((h + 10) % 12 + 1)
    return _CLOCK.sub(digits, text)


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
    numbers = [int(n) for n in re.findall(r"\d+", words)]
    # "430" is 4:30 as the recogniser sometimes writes it (21 Oct 2026: "set it for 430 instead" kept 04:30 at 9 am)
    said = set(numbers) | {n // 100 for n in numbers if 100 <= n <= 1259 and n % 100 < 60} | {
        v for w, v in _HOUR_WORDS.items() if re.search(r"\b%s\b" % w, words, re.I)}
    if hour >= 13 and hour in said:
        return hhmm                    # a 24-hour time, said as such
    if not ({hour % 12, hour % 12 + 12, 12 if hour % 12 == 0 else -1} & said):
        return hhmm                    # this time is not from these words: leave it as it is
    now_min = now.hour * 60 + now.minute
    best = min(((h * 60 + minute - now_min - 1) % (24 * 60), h) for h in (hour % 12, hour % 12 + 12))
    return "%02d:%02d" % (best[1], minute)


def same_alarm(a: Any, b: Any, words: str = "") -> bool:
    """Two alarm times that mean one alarm: the same, or the same clock time half a day apart when the user's words
    do not say which half (21 Oct 2026: "Set an alarm for 3:30. No, no, cancel that." planned 15:30 at 9 am, then a
    cancel of 03:30, which went to the phone although nothing had been set)."""
    ma, mb = (re.match(r"\s*(\d{1,2}):(\d{2})\s*$", str(v or "")) for v in (a, b))
    if not ma or not mb:
        return bool(a) and a == b
    ha, hb = int(ma.group(1)), int(mb.group(1))
    if ma.group(2) != mb.group(2):
        return False
    return ha == hb or (ha % 12 == hb % 12 and not _DAYPART.search(words or ""))


def _norm(v: Any) -> Any:
    return " ".join(v.lower().split()) if isinstance(v, str) else v


# The words people use for a tool's fixed choices, when a model writes them as they were said (20 Oct 2026:
# "Skip this song" came as control_media(action="skip"); the phone takes "next")
ENUM_SYNONYMS = {"skip": "next", "forward": "next", "back": "previous", "prev": "previous", "resume": "play",
                 "unpause": "play", "stop": "pause", "on": "turn_on", "off": "turn_off"}


def enum_value(prop: Optional[Dict[str, Any]], value: Any) -> Any:
    """A value for a property with fixed choices: itself if it is one, else its plain synonym if that is one,
    else unchanged (the caller decides what to do with it)."""
    allowed = (prop or {}).get("enum")
    if not allowed or not isinstance(value, str) or value in allowed:
        return value
    v = "_".join(value.strip().lower().split())
    if v in allowed:
        return v
    return ENUM_SYNONYMS[v] if ENUM_SYNONYMS.get(v) in allowed else value


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
        # every call of this request that was planned, run or not
        self.planned: List[Dict[str, Any]] = []
        # calls of this request the consent check refused (the user is asked first, once)
        self.asked: set = set()
        # the last call that ran: (tool, args, the phone's reply, its spoken confirmation or None)
        self.last_done: Optional[tuple] = None
        # a call of this request the guards held back, as (tool, "cut_off" or "undecided"): if the model
        # then says nothing, the agent asks for the missing part itself
        self.unfinished: Optional[tuple] = None
        # the actions of this request the phone refused or failed, as (tool, "needs_permission" or "error"):
        # if the model then says nothing, the agent says so itself
        self.failed: List[tuple] = []
        # the same, as (tool, args): what the answer must not say happened
        self.failed_calls: List[tuple] = []
        # the tone channel (tone.py), set by the agent: tone() -> how the open request was said (a tone.Reading);
        # on_unsure(tool, args, question, action, reading) is told when a value is asked back
        self.tone: Optional[Callable[[], Any]] = None
        self.on_unsure: Optional[Callable[..., None]] = None
        # a value of this request the user sounded unsure of, asked back before it runs: (tool, args, question)
        self.confirming: Optional[tuple] = None

    # -- the ledger's scope ------------------------------------------------------------
    def new_request(self) -> None:
        """The last request was answered. Repeatable actions ("call her again"), screens and readings
        may run again; everything else stays exactly-once. A reading is never answered from an earlier
        request's copy (18 Oct 2026: "how much battery?" asked a minute later got the old reading, from
        the ledger, and was answered with it)."""
        self.held_back = []
        self.planned = []
        self.asked = set()
        self.unfinished = None
        self.failed = []
        self.failed_calls = []
        self.confirming = None
        for key, rec in list(self.coord.ledger.items()):
            spec = self.specs.get(rec.tool, {})
            if spec.get("repeatable") or spec.get("kind") in ("ui", "read"):
                del self.coord.ledger[key]

    def _never_set(self, time: Any) -> bool:
        """An alarm at this time was planned in this request and held back at the gate, and no alarm at this time
        was ever set in this conversation."""
        setting = ("set_alarm", "set_reminder")
        words = self.coord.open_utterance
        # held back at the gate, or planned in a reply the user cut off (LiveKit cancels that reply, gate and all)
        held = any(c["tool"] in setting and same_alarm((c.get("args") or {}).get("time"), time, words)
                   for c in self.held_back + self.planned)
        ran = any(r.tool in setting and r.outcome == "ok" and same_alarm(r.args.get("time"), time, words)
                  for r in self.coord.history)
        return bool(time) and held and not ran

    def _alarm_that_ran(self, time: Any) -> Any:
        """The alarm this conversation really set that a cancel means: "cancel the 3:30" after 15:30 was set is a
        cancel of 15:30 when the user did not say which half of the day. The time as given otherwise."""
        exact = [r for r in self.coord.history if r.tool in ("set_alarm", "set_reminder") and r.outcome == "ok"
                 and r.args.get("time") == time]
        if exact:
            return time
        near = [r.args.get("time") for r in self.coord.history if r.tool in ("set_alarm", "set_reminder")
                and r.outcome == "ok" and same_alarm(r.args.get("time"), time, self.coord.open_utterance)]
        return near[-1] if near else time

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
        props = spec["parameters"]["properties"]
        args = {k: enum_value(props.get(k), v) for k, v in coerce_props(props, raw_args).items()}
        if isinstance(args.get("time"), str):
            # one spelling of a time: "7:00" and "07:00" are the same alarm to the ledger (17 Oct 2026: a
            # cancel of "7:00" did not clear the "07:00" it cancelled, so setting 07:00 again read "already done")
            m = re.match(r"\s*(\d{1,2})[:.](\d{2})\s*$", args["time"])
            if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
                args["time"] = "%02d:%s" % (int(m.group(1)), m.group(2))
        if tool == "send_message" and isinstance(args.get("text"), str):
            args["text"] = message_times(args["text"])
        kind = spec.get("kind", "read")
        self.last_done = None
        if tool in ("set_alarm", "set_reminder") and args.get("time"):
            args["time"] = next_occurrence(args["time"], self.coord.open_utterance, dt.datetime.now())
        timeout = float(spec.get("timeout", TIMEOUTS.get(kind, 8.0)))
        if tool == "cancel_alarm" and self._never_set(args.get("time")):
            # The alarm the user takes back was only planned in this request, and the gate held it back: it never
            # ran, so there is nothing to cancel and nothing goes to the phone. 21 Oct 2026: "Set an alarm for 3:30.
            # No, no, cancel that. Could you set it for 4:30 instead?" sent a cancel of 3:30 to the phone, which
            # said there was no such alarm.
            self.emit({"kind": "tool_dropped", "tool": tool, "args": args,
                       "why": "that alarm was never set, so there is nothing to cancel"})
            return json.dumps({"status": "ok", "note": "that alarm was never set (the user's correction came before "
                                                       "it ran): nothing to cancel, and nothing was done"})
        if tool == "cancel_alarm" and args.get("time"):
            args["time"] = self._alarm_that_ran(args["time"])
        self.planned.append({"tool": tool, "args": dict(args)})
        self.emit({"kind": "tool_planned", "tool": tool, "args": args, "hold_s": round(self.coord.hold(), 1)})
        if kind in ("write", "ui") and cut_off(self.coord.open_utterance):
            self.unfinished = (tool, "cut_off")
            self.emit({"kind": "tool_asked", "tool": tool, "args": args})
            return json.dumps({"status": "not_executed", "reason":
                               "the user's words stop mid-phrase; ask them, in a few words, for the rest"})
        if kind in ("write", "ui") and undecided(self.coord.open_utterance):
            # "Call Rahul, or maybe Priya, I'm not sure": the user has not chosen, so nothing is
            # done whatever the model decided; it asks instead (measured 16 Oct: the cloud model
            # with minimal thinking called both)
            self.unfinished = (tool, "undecided")
            self.emit({"kind": "tool_asked", "tool": tool, "args": args})
            return json.dumps({"status": "not_executed", "reason":
                               "the user has not decided yet. Ask which one, in one short question that names "
                               "the options; act only on their answer"})
        if kind == "write" and self.tone is not None:
            try:
                asked = await self._unsure(tool, args, epoch)
            except Superseded:
                return self._held(tool, args)
            if asked:
                return asked
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
            except Exception as exc:  # a broken check never lets an unasked call or change through: ask first
                log.warning("consent check failed: %s", exc)
                allowed = False
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
            return self._held(tool, args)
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
            result = json.loads(reply)
            self.failed.append((tool, "needs_permission" if isinstance(result, dict) and
                                result.get("status") == "needs_permission" else "error"))
            self.failed_calls.append((tool, args))
            self.emit({"kind": "tool_failed", "tool": tool, "args": args, "result": result})
            return reply
        self.failed.append((tool, "error"))
        self.failed_calls.append((tool, args))
        self.emit({"kind": "tool_failed", "tool": tool, "args": args, "error": record.error})
        return json.dumps({"status": "error", "error": record.error, "instruction":
                           "tell the user this step failed and what they can do next"})


    def _held(self, tool: str, args: Dict[str, Any]) -> str:
        log.info("held back at the gate: %s %s", tool, args)
        self.held_back.append({"tool": tool, "args": args})
        self.emit({"kind": "tool_dropped", "tool": tool, "args": args,
                   "why": "you kept talking, so this plan was never carried out"})
        return json.dumps({"status": "not_executed", "reason":
                           "the user started speaking again; wait for their full request"})

    async def _unsure(self, tool: str, args: Dict[str, Any], epoch: Optional[int]) -> str:
        """A value the user sounded unsure of ("4:30, I think", or a long pause before it) is asked back before it
        runs: "4:30 in the afternoon, right?". Read once the gate's hold has passed, so words said after a pause
        count too. The question becomes DUET's offer, so a plain "yes" runs it as asked (proactive.py). '' when the
        value stands (or the action has none)."""
        from .tone import ask_back
        asked = ask_back(tool, args)
        if asked is None:
            return ""
        await self.coord.gate(epoch)       # Superseded: the user said more, as at the gate itself
        try:
            reading = self.tone()
        except Exception as exc:  # the voice never blocks an action
            log.warning("tone reading failed: %s", exc)
            return ""
        if reading is None or not reading.about(tool, args):
            return ""
        question, action = asked
        again = self.confirming is not None and self.confirming[:2] == (tool, dict(args))
        self.unfinished = (tool, "unsure")
        self.confirming = (tool, dict(args), question)
        if not again:   # a mind that calls it once more is told the same, with nothing new on the timeline
            self.emit({"kind": "tool_asked", "tool": tool, "args": args,
                       "why": "you sounded unsure (%s), so DUET checks first" % "; ".join(reading.cues)})
        if self.on_unsure is not None and not again:
            try:
                self.on_unsure(tool, dict(args), question, action, reading)
            except Exception as exc:  # the offer is a convenience; the question is still asked
                log.warning("could not record the question as an offer: %s", exc)
        return json.dumps({"status": "not_executed", "reason":
                           "the user sounded unsure of the value (%s). Ask exactly this and nothing else: \"%s\" "
                           "Act only on their yes." % ("; ".join(reading.cues), question)})


def rpc_sender(room: rtc.Room, identity: Callable[[], str]):
    """send() over LiveKit RPC to the phone participant."""
    async def send(tool: str, args: Dict[str, Any], timeout: float) -> str:
        return await room.local_participant.perform_rpc(
            destination_identity=identity(), method="duet.tool",
            payload=json.dumps({"tool": tool, "args": args}), response_timeout=timeout)
    return send
