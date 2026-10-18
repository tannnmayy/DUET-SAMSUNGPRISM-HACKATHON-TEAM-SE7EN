"""Two speeds for the phone app: a small Gemma on our own GPU looks first, Gemma 4 26B in the cloud for the rest.

The fast mind (Gemma 4 E2B on llama.cpp, DUET_FAST_BASE_URL) sees each request first, with only
the simple phone actions and one more tool, ask_expert. It may:
  - act: carry out simple, clear phone actions with the final values the user settled on
    ("6:30, no, 7" sets 7), about a second after the user stops;
  - keep listening, when the user has clearly not finished;
  - hand the request to the cloud mind (Gemma 4 26B through Google's API): questions,
    explanations, health, feelings, the weather, facts, anything unclear or wavering.
It may not answer anything by itself: a reply without a tool goes to the cloud mind. If the fast
mind is slow (no decision within DUET_FAST_DEADLINE_S) or its server is down, the request goes to
the cloud mind too. Both minds act through the same coordinator and toolbox, so a plan the user's
words overtook never runs and no action runs twice, whichever mind made it.

With no fast mind configured (DUET_FAST unset), the phone app uses the cloud mind alone, as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from ..thinker import ThinkEvent

log = logging.getLogger("duet.galaxy.fast")

ASK_EXPERT = "ask_expert"
ASK_EXPERT_SPEC = {
    "name": ASK_EXPERT,
    "description": "Hand this request to DUET's expert mind instead of acting or answering. Call it for any "
                   "question, explanation or advice; anything about health, medicines, feelings, safety or an "
                   "emergency; the weather, news or facts; counting, comparing or reading the phone's data; any "
                   "request where the user wavers between options or a value is unclear; and whenever you are not "
                   "sure. Nothing is done; the expert takes over.",
    "parameters": {"type": "object", "properties": {
        "reason": {"type": "string", "description": "In a few words, why the expert should handle it."}},
        "required": ["reason"]},
}

# Simple, clear phone actions the fast mind may carry out itself. Everything else (readings that
# need explaining, medicines, family alerts, the trip, the weather, the web, calendar dates) is
# the cloud mind's.
# Readings (the alarms, the home's state) are not among them: the fast mind acts in one step and says
# nothing but the confirmations, so a reading would never be spoken (measured 16 Oct 2026).
FAST_TOOLS = {"set_alarm", "cancel_alarm", "set_timer", "set_reminder", "set_brightness",
              "set_screen_timeout", "open_settings", "open_app", "navigate_to", "call_contact", "send_message",
              "control_home_device", "set_destination", "add_stop", "set_car_climate",
              "open_navigation", "set_torch", "control_media", "set_volume"}

FAST_INSTRUCTIONS = """\
You are the quick mind of DUET, a voice assistant on the user's Samsung Galaxy phone. The user message \
is an automatic transcript of natural speech. Do exactly one of these:
1. If the user has clearly not finished speaking (cut off mid-sentence, "and...", "the number is..."), \
call keep_listening.
2. If they asked for a simple, clear phone action that your tools can do, do it: one tool call per \
action, with the FINAL values the user settled on. A self-correction replaces what came before \
("6:30, no, make it 7" means 7; "text her, actually call her" means call). A dropped request \
("never mind") is not done. Then reply with at most five words, or exactly <silent>.
3. Otherwise call ask_expert: questions, explanations, advice, health, medicines, feelings, safety, \
the weather, news, facts, counting or comparing, reading the phone's data, a value that is unclear, \
a user who wavers between options ("6:30 or maybe 7"), or anything you are not completely sure about.
Never answer a question yourself, never guess a value the user did not say, and never reply without \
calling a tool.

Examples of when to call ask_expert: "how much storage is left?" (reading the phone), "put lunch \
with Sam on Friday in my calendar" (the calendar), "set a timer for 5, or maybe 10" (wavering), "is it \
cold outside?" (the weather), "why is my phone so hot?" (an explanation). When the words stop \
mid-phrase ("text my", "turn on the"), call keep_listening.
"""

# The fast mind may act only on what the user's own words plainly name. A plan that does not pass
# goes to the cloud mind (which can still do it): a small model's misreadings cost a second, never a
# wrong action. Measured 16 Oct 2026: without this check Gemma 4 E2B opened an app called "apps" for
# "how many shopping apps do I have?" and set a 4 AM alarm for "a dentist appointment on Monday at 4".
NAMED = {
    "set_alarm": r"alarm|wake|get me up",
    "cancel_alarm": r"alarm",
    "list_alarms": r"alarm",
    "set_timer": r"timer|count ?down",
    "set_reminder": r"remind",
    "set_brightness": r"bright|dim",
    "set_screen_timeout": r"time ?out|screen",
    "open_settings": r"setting",
    "open_app": r"\b(open|launch|start)\b",
    "navigate_to": r"navigat|direction|take me|drive (me )?to|\bgo to\b|route|get me to",
    "call_contact": r"\b(call|ring|phone|dial)\b",
    "send_message": r"\b(text|message|tell|send|whatsapp)\b",
    "control_home_device": r"light|lamp|\bac\b|air ?con|lock|door|\btv\b|television|vacuum|washer|fan|heater|geyser",
    "get_home_status": r"light|lamp|\bac\b|air ?con|lock|door|\btv\b|vacuum|washer|home|house",
    "set_destination": r"take me|navigat|\bgo to\b|drive (me )?to|destination|head (to|for)|route|instead",
    "add_stop": r"\bstop\b|\bvia\b|on the way",
    "set_car_climate": r"\bac\b|air ?con|cool|warm|\bhot\b|\bcold\b|temperature|degree|climate|\bfan\b|heat",
    "open_navigation": r"\bmaps?\b|navigat|direction",
    "set_torch": r"torch|flash ?light",
    "control_media": r"music|song|track|\bplay|pause|resume|skip|\bnext\b|previous|video|podcast",
    "set_volume": r"volume|louder|quieter|\bmute\b",
}
NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
                "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
                "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100}
# argument values that must come from the user's own words (not the model's guess)
WORD_ARGS = {"call_contact": "name", "send_message": "to", "navigate_to": "destination", "set_destination": "place",
             "add_stop": "place", "open_app": "name", "control_home_device": "device"}
NUMBER_ARGS = {"set_timer": "minutes", "set_brightness": "percent", "set_volume": "percent",
               "set_screen_timeout": "seconds", "set_car_climate": "temperature"}
RELATIONS = {"mom", "mum", "mother", "dad", "father", "daughter", "son", "wife", "husband", "brother", "sister",
             "home", "office", "work", "ac"}


def _numbers(words: str) -> set:
    """Every number the words contain, with the parts of a spoken clock time: the recogniser
    writes 4:30 as "4:30", "4.30" or "430"."""
    out = set()
    for tok in re.findall(r"\d{1,2}[:.]\d{2}|\d+", words):
        if re.match(r"\d{1,2}[:.]\d{2}$", tok):
            h, m = re.split(r"[:.]", tok)
            out |= {int(h), int(m)}
        else:
            out.add(int(tok))
            if len(tok) in (3, 4) and int(tok[-2:]) < 60:      # "430" is 4:30, "1015" is 10:15
                out |= {int(tok[:-2]), int(tok[-2:])}
    out |= {v for w, v in NUMBER_WORDS.items() if re.search(r"\b%s\b" % w, words)}
    return out


_VALUE = r"\d{1,2}[:.]\d{2}|\d+|\b(?:%s)\b" % "|".join(NUMBER_WORDS)


def _keys(token: str) -> set:
    """What one spoken number can stand for. A clock time with its minutes ("4:30", "4.30", "430")
    is that time, hour and minute together; a plain number (20, "six") is that number, or that
    hour of the clock ("quarter past six")."""
    if re.match(r"\d{1,2}[:.]\d{2}$", token):
        h, m = (int(x) for x in re.split(r"[:.]", token))
        return {("t", h % 12, m)}
    v = int(token) if token.isdigit() else NUMBER_WORDS[token]
    keys = {("n", v), ("h", v % 12)}
    if token.isdigit() and len(token) in (3, 4) and int(token[-2:]) < 60:
        keys.add(("t", int(token[:-2]) % 12, int(token[-2:])))       # "430"
    return keys


def _last_value(words: str) -> Optional[set]:
    """The last number the user said, or None. After a correction it is the value they settled on."""
    tokens = re.findall(_VALUE, words)
    return _keys(tokens[-1]) if tokens else None


def _plan_values(calls: List[tuple]) -> set:
    out = set()
    for tool, args in calls:
        for v in args.values():
            m = re.match(r"\s*(\d{1,2}):(\d{2})\s*$", str(v))
            if m:
                h, mi = int(m.group(1)), int(m.group(2))
                out |= {("t", h % 12, mi), ("h", h % 12)}
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out |= {("n", int(v))}
            elif isinstance(v, str):
                for token in re.findall(_VALUE, v.lower()):   # "I'll be home by eight" in a message
                    out |= _keys(token)
    return out


# A question is answered, never acted on by the fast mind ("Is the AC on at home?" switched the AC
# on, 16 Oct 2026). A polite request phrased as a question ("Could you set an alarm for 7?") is a request.
_POLITE = re.compile(r"^\W*(can|could|would|will) (you|u)\b|^\W*please\b|^\W*(can|could) i (get|have)\b", re.I)
_QUESTION = re.compile(r"^\W*(is|are|was|were|do|does|did|what|what's|whats|which|how|why|when|where|who|"
                       r"should|shall|am i|have i|has|had|isn't|aren't|don't|doesn't)\b", re.I)


# The user drops something ("Set an alarm for 6. Actually, never mind, forget it."): whatever is left to do
# is the cloud mind's to judge, never the fast mind's (17 Oct 2026: the fast mind's second look, told to
# "respond now", set the dropped 6:00 alarm).
# "leave the light on" keeps a device as it is: a part of the request dropped, not a new action (18 Oct
# 2026: the fast mind turned it into control_home_device(light, on) after "switch off the living room light")
_DROPPED = re.compile(r"\b(never ?mind|forget (it|that|about it)|don'?t bother|skip (it|that)|leave it|no need|"
                      r"leave (that|them|the [a-z ]{1,30}?) (on|off|alone|be|as (it|they) (is|are)))\b", re.I)


# a message whose words point at a fact to look up rather than state it
_NEEDS_LOOKUP = re.compile(r"arrival time|\beta\b|when (i|we)('?ll| will)? (get|be|reach|arrive)|how long|"
                           r"my location|where i am|the (latest|current) |the time i", re.I)


def _named(tool: str) -> Optional[str]:
    """The words that name an action: the built-in ones, or a skill's `named` (app/www/skills.json)."""
    if tool in NAMED:
        return NAMED[tool]
    from .skills import named as skill_names
    return skill_names().get(tool)


def user_named(tool: str, words: str) -> bool:
    """Do the user's own words name this action ("set the AC to 22", "call Priya")? Then it was asked for."""
    pattern = _named(tool)
    return bool(pattern and re.search(pattern, (words or "").lower()))


def drops_something(words: str) -> bool:
    return bool(_DROPPED.search(words or ""))


def asks_question(words: str) -> bool:
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", (words or "").strip()) if s.strip()]
    if not sentences:
        return False
    last = sentences[-1]
    if _POLITE.search(last):
        return False
    return last.rstrip().endswith("?") or bool(_QUESTION.search(last))


def vet_plan(calls: List[tuple], words: str, offer=None) -> str:
    """The fast mind's whole first step: every call must pass `vet`, and when the user said a
    number, the last one they said (the value a correction settled on) must be in the plan.
    "3:30, no, cancel that, set it for 430 instead" planned as cancel_alarm(03:30) alone is
    handed on (measured 16 Oct 2026, live).
    An answer to DUET's own open offer ("yes", "make it 22", "just the AC") names no action, yet asks
    for one: a plan that stays inside the offer, with only values offered or said, may run."""
    from .proactive import covers
    if offer is not None and covers(offer, calls, words):
        return ""
    w = (words or "").lower()
    if calls and asks_question(words):
        return "a question: the cloud mind answers it"
    if calls and drops_something(words):
        return "the user dropped something: the cloud mind decides what is left"
    for tool, args in calls:
        why = vet(tool, args, w)
        if why:
            return why
    last = _last_value(w)
    if last and calls and not (last & _plan_values(calls)):
        return "the last value the user said is not in the plan"
    return ""


def vet(tool: str, args: Dict[str, Any], words: str) -> str:
    """'' when the plan plainly matches the user's words, else why it goes to the cloud mind."""
    w = (words or "").lower()
    pattern = _named(tool)
    if pattern is None:
        return "not a fast action: %s" % tool
    if not re.search(pattern, w):
        return "the words do not name %s" % tool
    key = WORD_ARGS.get(tool)
    if key:
        value = str(args.get(key) or "").lower()
        parts = [p for p in re.findall(r"[a-z0-9]+", value) if len(p) >= 2 and p not in ("the", "my", "to", "a")]
        if parts and not any(re.search(r"\b%s" % re.escape(p), w) or p in RELATIONS for p in parts):
            return "%s '%s' is not in the user's words" % (key, value)
    if tool == "send_message" and (_NEEDS_LOOKUP.search(str(args.get("text") or "")) or _NEEDS_LOOKUP.search(w)):
        # "tell Priya I'll be there by the arrival time": the time must be looked up (the trip), and
        # the fast mind would send the words as they are (measured 17 Oct 2026)
        return "the message needs a fact looked up first: the cloud mind"
    num_key = NUMBER_ARGS.get(tool)
    if num_key and args.get(num_key) is not None:
        try:
            n = float(args[num_key])
        except (TypeError, ValueError):
            return "%s is not a number" % num_key
        if n not in _numbers(w) and not (num_key == "minutes" and n == 0.5 and "half" in w):
            return "%s %s is not in the user's words" % (num_key, args[num_key])
    if tool in ("set_alarm", "cancel_alarm", "set_reminder"):
        m = re.match(r"\s*(\d{1,2}):(\d{2})", str(args.get("time") or ""))
        if not m:
            return "no clock time"
        hour, minute = int(m.group(1)), int(m.group(2))
        nums = _numbers(w)
        if not ({hour, hour % 12 or 12} & nums):
            return "the hour is not in the user's words"
        if minute and minute not in nums and not (minute == 30 and "half" in w) \
                and not (minute in (15, 45) and "quarter" in w):
            return "the minutes are not in the user's words"
    return ""


AFTER_ACTION_S = 12.0   # the gate's hold, the phone's reply and a short last word fit easily
CLOUD_DOWN_S = 20.0     # after failing to reach the cloud mind, requests skip it for this long

REFUSED_MAX = 3         # tries of an action the toolbox refused, in one request, before DUET asks the user

# the question DUET asks when the cloud mind keeps trying an action it may not take yet
_ASK = {
    "set_brightness": lambda a: ("set the brightness to %s percent" % a["percent"]) if a.get("percent") is not None
    else "change the brightness",
    "set_screen_timeout": lambda a: "set the screen timeout to %s seconds" % a.get("seconds"),
    "call_contact": lambda a: "call %s" % a.get("name"),
    "send_message": lambda a: "text %s" % a.get("to"),
    "control_home_device": lambda a: "%s the %s" % (str(a.get("action") or "change").replace("_", " "), a.get("device")),
    "set_car_climate": lambda a: ("set the car's AC to %s degrees" % a["temperature"]) if a.get("temperature") is not None
    else "switch the car's AC %s" % ("off" if a.get("on") is False else "on"),
}


def ask_first(tool: str, args: Dict[str, Any]) -> str:
    try:
        what = _ASK[tool](args or {})
    except Exception:
        what = "do that"
    return "Should I go ahead and %s?" % what


CLOUD_OFF = "the cloud mind is switched off (offline drill)"
CLOUD_DOWN = "cannot reach the cloud mind (it failed moments ago)"


def cloud_unreachable(why: str) -> bool:
    """Does this failure mean the cloud mind cannot be reached (rather than a bad answer)?"""
    w = (why or "").lower()
    return "cannot reach" in w or "offline drill" in w


def enabled() -> bool:
    return os.environ.get("DUET_FAST", "0") == "1"


def fast_specs(mode_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What the fast mind sees: the mode's simple actions and ask_expert."""
    from .skills import named as skill_names
    fast_skills = skill_names()
    return [{"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
            for t in mode_tools if t["name"] in FAST_TOOLS or t["name"] in fast_skills] + [ASK_EXPERT_SPEC]


def make_fast_thinker(mode_tools: List[Dict[str, Any]], mode_title: str):
    from ..llm_local import LocalThinker
    return LocalThinker(
        model=os.environ.get("DUET_FAST_MODEL", "gemma-4-e2b"),
        specs=fast_specs(mode_tools),
        instructions=FAST_INSTRUCTIONS + "\nYou are in %s mode." % mode_title,
        base_url=os.environ.get("DUET_FAST_BASE_URL", "http://127.0.0.1:18002/v1"),
        # Gemma's own recommendation is temperature 1.0; a router that must pick the same action
        # for the same words every time is better near-greedy
        sampler={"temperature": float(os.environ.get("DUET_FAST_TEMPERATURE", "0.2")), "top_p": 0.95,
                 "seed": int(os.environ.get("DUET_SEED", "7")), "extra_body": {"top_k": 64}},
        escalate={ASK_EXPERT}, note_role="system", one_step=True)


class HybridThinker:
    """The thinker interface (run, history, model) over a fast mind and a cloud mind."""

    def __init__(self, fast, slow, on_route: Optional[Callable[[str, str], None]] = None,
                 deadline_s: Optional[float] = None) -> None:
        self.fast, self.slow = fast, slow
        self.on_route = on_route or (lambda where, why: None)
        self.deadline_s = deadline_s if deadline_s is not None else float(os.environ.get("DUET_FAST_DEADLINE_S", "3.0"))
        self.thinking = getattr(slow, "thinking", "")
        self.model = "%s (fast) + %s (cloud)" % (fast.model, slow.model)
        # requests the fast mind handled since the cloud mind last spoke, so the cloud mind knows them
        self.edge_log: List[str] = []
        self.cloud_off = False          # the offline drill (the app's switch): the cloud mind is cut
        self.cloud_down_until = 0.0     # it could not be reached moments ago: do not wait for it again
        self.offers = None              # DUET's open offer, if any (proactive.Offers, set by the agent)
        self.cloud_delay_s = 0.0        # the slow-cloud drill: every cloud answer comes this much later

    @property
    def history(self):
        return self.slow.history

    def _route(self, where: str, why: str) -> None:
        self.model = self.fast.model if where == "edge" else self.slow.model
        try:
            self.on_route(where, why)
        except Exception:  # the timeline is a view; never let it break the request
            pass

    async def run(self, text: str, toolbox, audio=None, note: str = "",
                  allow_listen: bool = True) -> AsyncIterator[ThinkEvent]:
        coord = toolbox.coord
        done_before = len(coord.history)
        if not allow_listen:
            # the second look, after waiting for a user who had not finished: these are the unclear
            # requests by nature, so the cloud mind takes them (17 Oct 2026: the fast mind, told to respond
            # now, acted on a request the user had just dropped)
            async for ev in self._cloud(text, toolbox, audio, note, allow_listen, done_before,
                                        "a second look after the user finished"):
                yield ev
            return
        offer = self.offers.active() if self.offers is not None else None
        if offer is not None:
            from .proactive import declines
            if declines(text):
                # "no thanks" to DUET's own offer: nothing to do and nothing to look up, so no model either
                self.offers.resolve("declined")
                self._route("edge", "the user declined DUET's offer")
                yield ThinkEvent("route", text="edge")
                yield ThinkEvent("decided", tools=False)
                yield ThinkEvent("say", text="Okay, I'll leave it.")
                return
        from . import readings
        reading = readings.which(text) if os.environ.get("DUET_FAST_READINGS", "1") == "1" else None
        if reading and reading in getattr(toolbox, "specs", {}):
            # a plain reading ("how much battery do I have?"): read it and say it, with no model call (about a
            # second, against two cloud calls); a template that cannot answer it hands it on as usual
            self._route("edge", "a plain reading, answered from the reading itself")
            yield ThinkEvent("route", text="edge")
            yield ThinkEvent("decided", tools=False)
            yield ThinkEvent("tool_start", name=reading, args={})
            result = json.loads(await toolbox.call(reading, {}, epoch=coord.epoch))
            yield ThinkEvent("tool_done", name=reading, args={},
                             outcome=str(result.get("status", "ok")) if isinstance(result, dict) else "ok")
            said = readings.answer(reading, result, text)
            if said:
                self.edge_log.append('User: "%s" -> answered from %s: %s' % (text[-160:], reading, said))
                yield ThinkEvent("say", text=said)
                return
        self.fast.history = []          # stateless: each request is judged on its own words
        self.fast.vet = lambda calls: vet_plan(calls, text, offer)
        # The fast mind does not need the clock: it acts only on values the user said, and the toolbox
        # settles a time without AM or PM itself. Without the minute-by-minute "Now:" line its instructions
        # stay the same from request to request, so the server reuses its processed copy of them.
        fast_note = "\n".join(l for l in (note or "").splitlines()
                              if not l.startswith(("Now: ", "Earlier in this conversation you read")))
        agen = self.fast.run(text, toolbox, note=fast_note, allow_listen=allow_listen)
        escalate_why = ""
        acted_at: Optional[float] = None
        started = time.time()
        try:
            while True:
                # before it acts: a short deadline, then the cloud mind takes over; after it acted
                # (the action is done and confirmed aloud): a longer one, then the reply just ends
                limit = (self.deadline_s - (time.time() - started)) if acted_at is None \
                    else (AFTER_ACTION_S - (time.time() - acted_at))
                try:
                    if limit <= 0:
                        raise asyncio.TimeoutError()
                    ev = await asyncio.wait_for(agen.__anext__(), limit)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    if acted_at is not None:
                        log.warning("fast mind: no reply %.0f s after acting; ending the reply", AFTER_ACTION_S)
                        yield ThinkEvent("say", text="<silent>")
                        return
                    escalate_why = "the fast mind took longer than %.1f s" % self.deadline_s
                    break
                if ev.kind == "escalate":
                    escalate_why = ev.text or "the fast mind handed it over"
                    break
                if ev.kind == "error":
                    escalate_why = "the fast mind failed: %s" % (ev.text or "")[:80]
                    break
                if ev.kind == "listen":
                    self._route("edge", "the user has not finished")
                    yield ev
                    return
                if ev.kind == "decided":
                    if not ev.tools:
                        escalate_why = "nothing for the fast mind to do"
                        break
                    acted_at = time.time()
                    self._route("edge", "a simple phone action")
                    yield ThinkEvent("route", text="edge")
                yield ev
                if ev.kind == "say":
                    self.edge_log.append('User: "%s" -> done on the phone: %s' % (
                        text[-160:], "; ".join("%s(%s)" % (r.tool, json.dumps(r.args))
                                               for r in coord.history[done_before:] if r.outcome == "ok") or "nothing"))
                    return
        finally:
            await agen.aclose()
        async for ev in self._cloud(text, toolbox, audio, note, allow_listen, done_before, escalate_why):
            yield ev

    async def _cloud(self, text: str, toolbox, audio, note: str, allow_listen: bool, done_before: int,
                     why: str) -> AsyncIterator[ThinkEvent]:
        coord = toolbox.coord
        if self.cloud_off or time.monotonic() < self.cloud_down_until:
            # no cloud mind: say so at once (the agent's offline line), never wait for it
            yield ThinkEvent("error", text=CLOUD_OFF if self.cloud_off else CLOUD_DOWN)
            return
        # the cloud mind takes over: it is told what the fast mind already did, for this request
        # and for earlier ones it handled alone
        self._route("cloud", why)
        yield ThinkEvent("route", text="cloud")   # the turn loop covers the longer wait at once
        if self.cloud_delay_s > 0:
            # the slow-cloud drill: variable latency, live ("One moment.", then "Still working on it.")
            await asyncio.sleep(self.cloud_delay_s)
        extra = []
        if self.edge_log:
            extra.append("Handled quickly by the phone since you last answered (already done; do not repeat): "
                         + " | ".join(self.edge_log[-6:]))
        ran = ["%s(%s)" % (r.tool, json.dumps(r.args)) for r in coord.history[done_before:] if r.outcome == "ok"]
        if ran:
            extra.append("Already carried out for this request (do not repeat): " + "; ".join(ran))
        unsure = ["%s(%s)" % (r.tool, json.dumps(r.args)) for r in coord.history[done_before:] if r.outcome == "unknown"]
        if unsure:
            extra.append("Tried for this request, but the phone never confirmed it, so it may or may not have "
                         "happened (do not retry it; tell the user it may not have gone through and to check "
                         "the phone): " + "; ".join(unsure))
        slow_note = "\n".join([x for x in [note] + extra if x])
        refused: Dict[str, int] = {}
        agen = self.slow.run(text, toolbox, audio=audio, note=slow_note, allow_listen=allow_listen)
        try:
            async for ev in agen:
                if ev.kind == "say":
                    self.edge_log = []
                    self.cloud_down_until = 0.0
                if ev.kind == "error" and cloud_unreachable(ev.text):
                    self.cloud_down_until = time.monotonic() + CLOUD_DOWN_S
                yield ev
                if ev.kind == "tool_done" and ev.outcome == "not_executed":
                    refused[ev.name] = refused.get(ev.name, 0) + 1
                    if refused[ev.name] >= REFUSED_MAX:
                        # the cloud mind keeps calling an action it was told not to (17 Oct 2026: seven
                        # times, 15 s of silence): stop it, and ask the user plainly instead
                        log.warning("cloud mind: %s refused %d times; asking the user", ev.name, refused[ev.name])
                        yield ThinkEvent("say", text=ask_first(ev.name, ev.args))
                        return
        finally:
            await agen.aclose()
