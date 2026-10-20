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

from ..thinker import ThinkEvent, said_of

log = logging.getLogger("duet.galaxy.fast")

ASK_EXPERT = "ask_expert"
ASK_EXPERT_SPEC = {
    "name": ASK_EXPERT,
    "description": "Hand this request to DUET's expert mind instead of acting or answering. Call it for any "
                   "question, explanation or advice; anything about health, medicines, feeling unwell, safety or "
                   "an emergency; the weather, news or facts; counting, comparing or reading the phone's data; any "
                   "request where the user wavers between options or a value is unclear; and whenever you are not "
                   "sure. Nothing is done; the expert takes over.",
    "parameters": {"type": "object", "properties": {
        "reason": {"type": "string", "description": "In a few words, why the expert should handle it."},
        "say": {"type": "string", "description": "One short, natural sentence to say to the user right now, while "
                                                 "the expert works, showing you understood (\"Let me check when the "
                                                 "next Apple event is.\"). Never a question. No times, numbers or "
                                                 "values of an action."},
        "kind": {"type": "string", "enum": ["web", "phone", "health", "talk", "other"],
                 "description": "web: facts, news, dates, places from the internet; phone: about this phone, its "
                                "data or the home; health: the body, medicines, feeling unwell; talk: advice or a "
                                "longer chat; other: anything else."},
        "question": {"type": "string", "description": "For web only: the user's final question, complete and "
                                                      "self-contained."}},
        "required": ["reason", "say", "kind"]},
}

# The fast mind answers small talk itself: a greeting, thanks, "how are you", "I'm not good". Its words are
# spoken only after small_talk() and chat_ok() agree (20 Oct 2026, the S24: "Good night", "Good morning",
# "How are you?" and "Thank you" got "One moment." and then silence, because nothing but the cloud mind
# could answer, and the cloud mind replied <silent> to greetings).
CHAT_REPLY = "chat_reply"
CHAT_REPLY_SPEC = {
    "name": CHAT_REPLY,
    "description": "Answer the user yourself, for small talk only: greetings, goodbyes, thanks, 'how are you', "
                   "'okay', 'what can you do', the user saying how they feel. Nothing is done on the phone.",
    "parameters": {"type": "object", "properties": {
        "text": {"type": "string", "description": "What you say back: warm and natural, one or two short sentences, "
                                                  "at most 25 words, like a friend on a phone call."}},
        "required": ["text"]},
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
You are the quick mind of DUET, a voice assistant on the user's Samsung Galaxy phone. The last user \
message is an automatic transcript of natural speech; the messages before it are the conversation so \
far, already handled (never act on them again). Do exactly one of these:
1. If the user has clearly not finished speaking (cut off mid-sentence, "and...", "the number is..."), \
call keep_listening.
2. If they asked for a simple, clear phone action that your tools can do, do it: one tool call per \
action, with the FINAL values the user settled on. A self-correction replaces what came before \
("6:30, no, make it 7" means 7; "text her, actually call her" means call). A dropped request \
("never mind") is not done. Then reply with at most five words, or exactly <silent>.
3. If they are only making small talk (a greeting, goodbye, thanks, "how are you", "okay", "what can \
you do", or telling you how they feel, such as "I'm not good" or "I'm tired"), call chat_reply with what \
you say back: warm and natural, like a friend on a call. When they feel bad, show you care and ask what \
is wrong; otherwise, when it keeps the conversation going, end with a short question. Never state facts \
about the world, never give health advice, never say you did something.
4. Otherwise call ask_expert: questions, explanations, advice, health, medicines, feeling unwell, \
safety, the weather, news, facts, counting or comparing, reading the phone's data, a value that is \
unclear, a user who wavers between options ("6:30 or maybe 7"), or anything you are not completely sure \
about. Give `say`: one short sentence to say right away that shows you understood; it is never a \
question. Questions about this phone (battery, heat, apps, storage) are kind "phone".
Never answer a question about the world yourself, never guess a value the user did not say, and never \
reply without calling a tool.

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


# --- small talk: what the fast mind may answer itself ----------------------------------------------------

# The body, medicines, safety and fear: never small talk, whatever the model thinks; the cloud mind answers
# (Care mode's medicines need the schedule, and every mode's health answer needs care)
_HEALTH = re.compile(
    r"\b(pain|pains|hurts?|hurting|ache|aches|aching|headache|stomach|sick|ill|fever|dizzy|faint|fell|fall|fallen|"
    r"bleed\w*|blood|breath\w*|chest|heart|medicines?|tablets?|pills?|doses?|doctor|hospit\w*|emergency|ambulance|"
    r"injur\w*|vomit\w*|nause\w*|cough\w*|pressure|sugar|scared|afraid|frightened|panic\w*|suicid\w*|die|dying|"
    r"kill|unsafe|danger\w*)\b", re.I)
# questions that are themselves small talk ("how are you?", "what can you do?")
_SMALL_TALK_Q = re.compile(
    r"\b(how (are|r) (you|u)|how('?s| is) it going|how (have|'ve) you been|how was your|how do you do|what'?s up|"
    r"what'?s new|what can you do|what do you do|who are you|what'?s your name|are you there|can you hear me|"
    r"how about you|and you|what about you)\b", re.I)
# any other sentence asking for something to be found out or explained is the cloud mind's
_WANTS_INFO = re.compile(r"\b(know|find|look (it |that |this )?up|search|google|check|latest|news|explain|tell me|"
                         r"why|when|where|which|who|whom|whose|what|how|help|fix|can you|could you|would you|"
                         r"will you)\b", re.I)
# a bare yes or no answers something: the cloud mind (or DUET's offer) decides what it agrees to
_ANSWER = re.compile(r"^\W*(yes|yeah|yep|yup|no|nope|nah|sure|please|go ahead|do it|of course|correct|right)\b", re.I)
# DUET's last line asked about an action or a choice: what the user says now answers it, it is not small talk
_DUET_ASKED = re.compile(r"\b(should i|shall i|do you want me to|would you like me to|want me to|go ahead|which one|"
                         r"who should|what time|how long|where to|did you mean|is that right|ok(ay)? to)\b"
                         r"|\bor [^.?!]{1,40}\?\s*$", re.I)
# a reply that says something was done (or will be): only a tool's own result may say that
_CLAIMS = re.compile(r"\b(i'?ve|i have|i'?ll|i will|i just|i)\s+(set|turned|switched|started|sent|called|changed|opened|"
                     r"booked|ordered|added|cancel+ed|scheduled|reminded|texted|messaged|locked|unlocked|played|"
                     r"paused)\b|\b(done|all set)\b", re.I)
SMALL_TALK_MAX_WORDS = 16
CHAT_MAX_WORDS = 40


def not_small_talk(words: str, tools: List[str]) -> str:
    """'' when the user's words are only small talk the fast mind may answer itself, else why not. Precision
    first: anything that names one of the mode's actions, says a number, asks for information, or touches on
    health goes to the cloud mind, which answers it a second or two later."""
    from .tools import cut_off
    w = " ".join((words or "").split())
    if not w:
        return "no words"
    if len(w.split()) > SMALL_TALK_MAX_WORDS:
        return "too long for small talk"
    if cut_off(w):
        return "the words stop mid-phrase"     # "Turn on the": the user has not finished
    if _HEALTH.search(w):
        return "about health or safety"
    if re.search(r"\d", w) or any(re.search(r"\b%s\b" % n, w, re.I) for n in NUMBER_WORDS):
        return "a number"
    if _ANSWER.search(w):
        return "a yes or no answers something"
    named = [t for t in tools if t not in (ASK_EXPERT, CHAT_REPLY) and user_named(t, w)]
    if named:
        return "the words name %s" % named[0]
    for sentence in [s for s in re.split(r"(?<=[.?!])\s+", w) if s.strip()]:
        # a small-talk question excuses only itself, not the rest of its sentence
        rest = _SMALL_TALK_Q.sub(" ", sentence)
        if _WANTS_INFO.search(rest) or (sentence.rstrip().endswith("?") and rest == sentence):
            return "asks for information"
    return ""


_REPLY_LABEL = re.compile(r"^\s*chat[\s_]*reply\s*[:\-]\s*", re.I)


def clean_reply(text: str) -> str:
    """The words to speak. A reply written as text sometimes names the tool it meant ("Chat reply: What else can
    I help you with?", 20 Oct 2026): that label is dropped."""
    return " ".join(re.sub(r"[*#`_\"]+", "", _REPLY_LABEL.sub("", text or "")).split()).strip()


def chat_ok(words: str, reply: str, tools: List[str], awaiting: bool = False) -> str:
    """'' when the fast mind's own reply to small talk may be spoken, else why it goes to the cloud mind."""
    if awaiting:
        return "the user may be answering DUET's question"
    why = not_small_talk(words, tools)
    if why:
        return why
    if re.search(r"\b[a-z]+_[a-z_]+\b", _REPLY_LABEL.sub("", reply or ""), re.I):
        # a tool's name written out as the reply ("keep_listening" for "Turn on the", 20 Oct 2026)
        return "the reply names a tool"
    said = clean_reply(reply)
    if not said or "<silent>" in said:
        return "no words to say"
    if re.search(r"\d", said):
        return "a number in the reply"
    if _CLAIMS.search(said):
        return "the reply says something was done"
    if len(said.split()) > CHAT_MAX_WORDS:
        return "too long for small talk"
    return ""


def awaiting_answer(last_said: str) -> bool:
    """Did DUET's last line ask the user about an action or a choice?"""
    return bool(_DUET_ASKED.search(last_said or ""))


# --- the bridge: what DUET says the moment a request goes to the cloud mind -------------------------------

# fixed lines by kind, for when the fast mind gave no line DUET may say (synthesized once at start-up)
BRIDGE_LINES = {"web": "Let me look that up.", "health": "Let me think about that.", "talk": "Let me think about that.",
                "phone": "Let me check.", "other": "Let me check."}
# a line that promises an action: the action's own confirmation speaks once it is done (the fast voice never
# claims a result, nor names a value a correction could still change)
_PROMISES = re.compile(r"\b(let me|i'?ll|i will|i'?m going to|going to)\s+(set|turn|switch|call|text|send|open|cancel|"
                       r"change|book|add|start|stop|lock|unlock|play|pause|remind|navigate|dim|lower|raise)\b", re.I)
BRIDGE_MAX_WORDS = 16
# how a line that only covers the wait begins; anything else could be a statement of fact the small model made up
_BRIDGE_OPENERS = re.compile(r"^(let me|let's|i'?ll (check|look|find|see|get)|i can (check|look|help|find|see)|"
                             r"i'?m (so |very |really )?sorry|sorry to hear|oh no|sure|okay|ok|got it|good question|"
                             r"hmm+|checking|looking|give me a (sec|second|moment))\b", re.I)


def bridge_line(handover: Dict[str, Any], words: str, tools: List[str] = ()) -> str:
    """What DUET says when a request goes to the cloud mind, in place of the fixed "One moment.":
    - the first sentence of the fast mind's own line that only covers the wait ("Let me...", "I'm sorry...") and
      promises nothing (no question, no number, no claim, no action promised, no tool name): "Let me check when
      the next Apple event is.";
    - else "Sure." for an action the user named (one of `tools`), and not a question: "No, cancel that, set it
      for 4:30" is acknowledged as the fast mind's own actions are;
    - else a fixed line by kind ("Let me look that up.");
    - nothing for small talk or words cut off mid-phrase: the cloud mind answers those in a second or two, and
      "Let me check." before "Good night, Tanmay." or "Who should I call?" would be wrong."""
    kind = str((handover or {}).get("kind") or "other").lower()
    for sentence in re.split(r"(?<=[.!?])\s+", _REPLY_LABEL.sub("", str((handover or {}).get("say") or ""))):
        if re.search(r"\b[a-z]+_[a-z_]+\b", sentence, re.I):
            continue                    # a tool's name written out ("keep_listening")
        s = clean_reply(sentence)
        if not s or s.endswith("?") or re.search(r"\d", s) or _CLAIMS.search(s) or _PROMISES.search(s) \
                or "<silent>" in s or len(s.split()) > BRIDGE_MAX_WORDS or not _BRIDGE_OPENERS.search(s):
            continue
        return s if s[-1] in ".!" else s + "."
    from .tools import cut_off
    if not (words or "").strip() or cut_off(words) or not_small_talk(words, []) == "":
        return ""
    from .readings import ACTION
    if not asks_question(words) and (ACTION.search(words) or any(
            t not in (ASK_EXPERT, CHAT_REPLY) and user_named(t, words) for t in tools)):
        return "Sure."
    return BRIDGE_LINES.get(kind, BRIDGE_LINES["other"])


_SORRY = re.compile(r"^\W*(i'?m|i am)?\s*(so |very |really )?sorry\b|^\W*sorry to hear\b|^\W*oh no\b", re.I)


def drop_second_apology(answer: str, bridge: str) -> str:
    """The cloud mind's answer without its opening apology when the bridge already apologised (21 Oct 2026: "I'm
    sorry to hear you have a stomach pain." and then "I'm sorry to hear that you're in pain. For mild...")."""
    if not bridge or not _SORRY.search(bridge):
        return answer
    parts = [p for p in re.split(r"(?<=[.!?])\s+", (answer or "").strip()) if p]
    if len(parts) > 1 and _SORRY.search(parts[0]):
        parts = parts[1:]
    return " ".join(parts)


# --- conversation controls: stop, go on, say it again ------------------------------------------------------

# the whole utterance asks DUET to stop or wait: it says nothing (talking over it has already stopped its voice)
_STOP = re.compile(r"^\W*(ok(ay)?\W+)?(stop|stop it|stop talking|wait|wait a (sec|second|minute|moment)|hold on|"
                   r"hang on|one (sec|second|moment)|just a (sec|second|minute|moment)|sh+|quiet|be quiet|shut up|"
                   r"enough|that'?s enough)\W*$", re.I)
# ...asks it to carry on with the answer it was cut off in ("go ahead" is left out: it says yes to an offer)
_RESUME = re.compile(r"^\W*(sorry\W+)?(go on|continue|carry on|keep going|please continue|you were saying|"
                     r"what were you saying)\W*$", re.I)
# ...asks it to say its last answer again
_REPEAT = re.compile(r"^\W*((what|sorry|huh|pardon)\s*\?|pardon( me)?|come again|say (that|it) again|repeat (that|it)|"
                     r"can you repeat (that|it)|could you repeat (that|it)|what did you (just )?say|"
                     r"i didn'?t (get|catch) that)\W*$", re.I)


# ...is only a backchannel: said over an answer, it means "I'm listening", so the answer goes on from the sentence it
# was cut in. 21 Oct 2026: "Mm-hm, okay." said over an answer stopped DUET, in LiveKit's VAD and adaptive modes alike,
# and the answer was lost. ("Yes" and "sure" are left out: they can say yes to an offer.)
_BACKCHANNEL = re.compile(r"^\W*((m+-?h?m+|mhm+|hmm+|uh-?huh|okay|ok|right|yeah|yep|i see|got it|alright|cool)\W*){1,3}$",
                          re.I)


def control(words: str) -> Optional[str]:
    """"stop", "resume", "repeat" or "backchannel" when the user's whole utterance is one of those, else None."""
    w = (words or "").strip()
    if _STOP.match(w):
        return "stop"
    if _RESUME.match(w):
        return "resume"
    if _REPEAT.match(w):
        return "repeat"
    if _BACKCHANNEL.match(w):
        return "backchannel"
    return None


def remainder(full: str, heard: str) -> str:
    """What the user did not hear of an answer they cut off, from the start of the sentence they stopped in.
    `full`: the whole answer; `heard`: the part played before they spoke."""
    full, heard = " ".join((full or "").split()), " ".join((heard or "").split())
    if not full or not heard:
        return ""                       # nothing is known
    # what was played can start with a line that is not part of the answer ("Let me check."): the cut is where
    # the last words heard are found in the answer
    tail = " ".join(heard.split()[-4:])
    at = full.find(tail)
    cut = at + len(tail) if at >= 0 else 0      # none of the answer was heard (only "Let me check." and the like)
    if cut >= len(full):
        return ""                       # they heard all of it
    start = max([m.end() for m in re.finditer(r"[.!?]\s+", full[:cut])] or [0])
    return full[start:].strip()


# --- one cloud call where one is enough ---------------------------------------------------------------------

# the weather has its own tool (Open-Meteo, exact): those questions stay with the full cloud mind
_WEATHER = re.compile(r"\b(weather|rain\w*|forecast|temperature|humid\w*|sunny|cloudy|storm\w*|snow\w*|hot|cold|"
                      r"degrees|umbrella)\b", re.I)
# this phone, its readings and the home: the full cloud mind, which has their tools ("the latest Samsung phone"
# is a web question, "my phone" is not)
_PHONE_WORDS = re.compile(r"\b((my|this|the) (phone|mobile)|battery|charg\w*|apps?|screen|brightness|storage|alarms?|"
                          r"timers?|reminders?|calendar|home|lights?|ac|air ?con\w*|door|lock|tv|washer|vacuum|trip|"
                          r"route|car|settings?|wi-?fi|bluetooth|volume|music|torch|medicines?|tablets?|pills?|"
                          r"doses?)\b", re.I)


def web_question(handover: Dict[str, Any], words: str, specs: Optional[Dict[str, Any]]) -> str:
    """The question to search the web with directly, in one grounded call, or '' for the full cloud mind (which
    decides on a search, makes it, then words the answer: three calls, 10-11 s on 20 Oct 2026). Only a web
    question the fast mind wrote out whole, with no phone action, no phone data and no weather in it."""
    if str((handover or {}).get("kind") or "") != "web" or "web_search" not in (specs or {}):
        return ""
    q = " ".join(str((handover or {}).get("question") or "").split())
    if len(q.split()) < 3:
        return ""
    from .readings import ACTION
    if ACTION.search(words or "") or _WEATHER.search(words or "") or _WEATHER.search(q) or _PHONE_WORDS.search(words or ""):
        return ""
    return q


def light_request(handover: Dict[str, Any], words: str) -> bool:
    """Health or talk with nothing on the phone in it: one cloud call with no tools (a few hundred input tokens,
    against about four thousand with the tools) is enough."""
    if str((handover or {}).get("kind") or "") not in ("health", "talk"):
        return False
    from .readings import ACTION
    return not (ACTION.search(words or "") or _PHONE_WORDS.search(words or ""))


RECENT_TURNS = 12      # what the fast mind sees of the conversation: the last six exchanges


def chat_turns(recent) -> List[Dict[str, str]]:
    """The recent conversation, [(role, text), ...] with role "user" or "assistant", as chat messages: one
    message per turn, the same speaker's lines joined. As real turns (not inside the system message) the
    server keeps the instructions and tools it has already processed (20 Oct 2026: 0.24-0.44 s a reply,
    against 0.84-0.96 s with the conversation in the system message)."""
    out: List[Dict[str, str]] = []
    for role, text in list(recent or [])[-RECENT_TURNS:]:
        text = " ".join((text or "").split())
        if not text or role not in ("user", "assistant"):
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += " " + text
        else:
            out.append({"role": role, "content": text})
    return out


def vet_plan(calls: List[tuple], words: str, offer=None, specs: Optional[Dict[str, Any]] = None) -> str:
    """The fast mind's whole first step: every call must pass `vet`, and when the user said a
    number, the last one they said (the value a correction settled on) must be in the plan.
    "3:30, no, cancel that, set it for 430 instead" planned as cancel_alarm(03:30) alone is
    handed on (measured 16 Oct 2026, live).
    An answer to DUET's own open offer ("yes", "make it 22", "just the AC") names no action, yet asks
    for one: a plan that stays inside the offer, with only values offered or said, may run.
    `specs` (tool name -> spec, the toolbox's): a value a tool does not take, even as a plain synonym,
    is handed on too."""
    from .proactive import covers
    if offer is not None and covers(offer, calls, words):
        return ""
    w = (words or "").lower()
    if calls and asks_question(words):
        return "a question: the cloud mind answers it"
    if calls and drops_something(words):
        return "the user dropped something: the cloud mind decides what is left"
    for tool, args in calls:
        why = vet(tool, args, w) or off_choice(tool, args, specs)
        if why:
            return why
    last = _last_value(w)
    if last and calls and not (last & _plan_values(calls)):
        return "the last value the user said is not in the plan"
    return ""


def off_choice(tool: str, args: Dict[str, Any], specs: Optional[Dict[str, Any]]) -> str:
    """'' unless an argument with fixed choices has a value that is none of them (nor a plain synonym of one)."""
    if not specs or tool not in specs:
        return ""
    from .tools import enum_value
    props = (specs[tool].get("parameters") or {}).get("properties") or {}
    for k, v in (args or {}).items():
        allowed = (props.get(k) or {}).get("enum")
        if allowed and enum_value(props.get(k), v) not in allowed:
            return "%s '%s' is not one of the choices %s takes" % (k, v, tool)
    return ""


# the words that name a mode the fast mind may switch on
MODE_WORDS = {"adaptive": r"adaptive|auto"}


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
    for k, v in args.items():
        # a mode switched on must be one the user named (18 Oct 2026: "Would lowering my brightness help
        # my battery?", heard as "With lowering, my brightness helped my battery.", turned on adaptive
        # brightness); a plain on/off is the action itself, named above
        if k != "on" and (v is True or str(v).lower() == "true") and not re.search(MODE_WORDS.get(k, re.escape(k)), w):
            return "%s is not in the user's words" % k
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
            for t in mode_tools if t["name"] in FAST_TOOLS or t["name"] in fast_skills] + [ASK_EXPERT_SPEC,
                                                                                          CHAT_REPLY_SPEC]


def make_fast_thinker(mode_tools: List[Dict[str, Any]], mode_title: str, user_name: str = ""):
    from ..llm_local import LocalThinker
    # the same for the whole conversation, so the server keeps its processed copy of these instructions
    who = (" The user's name is %s: use it now and then in small talk, not every time." % user_name
           if user_name else "")
    return LocalThinker(
        model=os.environ.get("DUET_FAST_MODEL", "gemma-4-e2b"),
        specs=fast_specs(mode_tools),
        instructions=FAST_INSTRUCTIONS + "\nYou are in %s mode.%s" % (mode_title, who),
        base_url=os.environ.get("DUET_FAST_BASE_URL", "http://127.0.0.1:18002/v1"),
        # Gemma's own recommendation is temperature 1.0; a router that must pick the same action
        # for the same words every time is better near-greedy
        sampler={"temperature": float(os.environ.get("DUET_FAST_TEMPERATURE", "0.2")), "top_p": 0.95,
                 "seed": int(os.environ.get("DUET_SEED", "7")), "extra_body": {"top_k": 64}},
        escalate={ASK_EXPERT}, note_role="system", one_step=True, reply_tool=CHAT_REPLY)


def tool_names(thinker) -> List[str]:
    """The names of the tools a local thinker was given."""
    return [t["function"]["name"] for t in getattr(thinker, "tools", []) or [] if isinstance(t, dict)]


async def warm_up(thinker) -> None:
    """The fast mind's instructions and tools processed once, while DUET greets the user, so the first answer
    is as quick as the rest."""
    started = time.time()
    try:
        await asyncio.wait_for(thinker.warm_up(), 20.0)
        log.info("fast mind warmed up in %.1f s", time.time() - started)
    except Exception as exc:  # a cold first answer is slower, never broken
        log.warning("fast mind warm-up failed: %s", exc)


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
        # the conversation so far, [(role, text), ...], kept by the agent from what was really said
        self.recent: List[tuple] = []
        # the fast mind's hand-over of the current request: reason, say, kind, question (ask_expert's arguments)
        self.handover: Dict[str, Any] = {}
        # set by the agent before each request: DUET's last answer in full, and what the user did not hear of
        # it if they cut it off (for "say that again" and "go on")
        self.last_answer = ""
        self.resume_text = ""
        self.bridge_said = ""           # the line said when this request went to the cloud mind
        # the short instructions of a cloud call with no tools, for talk and health (modes.light); None: never
        self.light_instructions: Optional[str] = None

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
        self.bridge_said = ""
        if not allow_listen:
            # the second look, after waiting for a user who had not finished: these are the unclear
            # requests by nature, so the cloud mind takes them (17 Oct 2026: the fast mind, told to respond
            # now, acted on a request the user had just dropped)
            async for ev in self._cloud(text, toolbox, audio, note, allow_listen, done_before,
                                        "a second look after the user finished"):
                yield ev
            return
        asked = control(text)
        said = {"stop": "<silent>", "resume": self.resume_text, "repeat": self.last_answer,
                "backchannel": self.resume_text}.get(asked or "")
        if said:
            # "wait" / "stop": nothing more is said (talking over DUET already stopped its voice); "go on", or only
            # "mm-hm" over an answer: the rest of the answer they cut off, from the sentence they stopped it in;
            # "what?": the last answer again. (A backchannel with nothing cut off is ordinary small talk.)
            self._route("edge", {"stop": "the user asked DUET to stop or wait", "resume": "the user asked DUET to go on",
                                 "repeat": "the user asked DUET to say it again",
                                 "backchannel": "the user was only listening: the answer goes on"}[asked])
            yield ThinkEvent("route", text="edge")
            yield ThinkEvent("decided", tools=False)
            yield ThinkEvent("say", text=said)
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
            from .proactive import answer
            plan = answer(offer, text)
            if plan:
                # "yes", "yes, make it 22", "just the AC": the answer is settled from the offer itself, with
                # no model, and runs through the toolbox like any other action (consent, gate, ledger, read-back)
                self._route("edge", "an answer to DUET's offer, settled from the offer")
                yield ThinkEvent("route", text="edge")
                yield ThinkEvent("decided", tools=True)
                epoch = coord.epoch
                for tool, args in plan:
                    if coord.overtaken(epoch):
                        from ..coordinator import Superseded
                        raise Superseded()      # the user's words changed: as for any plan, it is void
                    yield ThinkEvent("tool_start", name=tool, args=args)
                    out = json.loads(await toolbox.call(tool, dict(args), epoch=epoch))
                    yield ThinkEvent("tool_done", name=tool, args=args, text=said_of(out),
                                     outcome=str(out.get("status", "ok")) if isinstance(out, dict) else "ok")
                self.edge_log.append('User: "%s" -> answered DUET\'s offer: %s' % (
                    text[-160:], "; ".join("%s(%s)" % (r.tool, json.dumps(r.args))
                                           for r in coord.history[done_before:] if r.outcome == "ok") or "nothing"))
                yield ThinkEvent("say", text="<silent>")
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
            yield ThinkEvent("tool_done", name=reading, args={}, text=said_of(result),
                             outcome=str(result.get("status", "ok")) if isinstance(result, dict) else "ok")
            said = readings.answer(reading, result, text)
            if said:
                self.edge_log.append('User: "%s" -> answered from %s: %s' % (text[-160:], reading, said))
                yield ThinkEvent("say", text=said)
                return
        # The conversation so far, as chat turns, so small talk is answered in context. Acting stays on the
        # current words alone: vet_plan checks them, and the ledger never runs an action twice.
        self.fast.history = chat_turns(self.recent)
        self.fast.vet = lambda calls: vet_plan(calls, text, offer, getattr(toolbox, "specs", None))
        self.handover = {}
        epoch = coord.epoch
        # The fast mind does not need the clock: it acts only on values the user said, and the toolbox
        # settles a time without AM or PM itself. Without the minute-by-minute "Now:" line its instructions
        # stay the same from request to request, so the server reuses its processed copy of them.
        fast_note = "\n".join(l for l in (note or "").splitlines()
                              if not l.startswith(("Now: ", "Earlier in this conversation you read")))
        agen = self.fast.run(text, toolbox, note=fast_note, allow_listen=allow_listen)
        escalate_why = ""
        acted_at: Optional[float] = None
        plain = False                   # the fast mind replied without a tool: small talk, if the check agrees
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
                    self.handover = dict(ev.args or {})
                    break
                if ev.kind == "error":
                    escalate_why = "the fast mind failed: %s" % (ev.text or "")[:80]
                    break
                if ev.kind == "listen":
                    self._route("edge", "the user has not finished")
                    yield ev
                    return
                if ev.kind == "chat" or (plain and ev.kind == "say"):
                    why = self._chat_check(text, ev.text, offer)
                    if why:
                        escalate_why = "small talk the fast mind may not answer itself: %s" % why
                        self.handover = {"say": ev.text or ""}
                        break
                    if coord.overtaken(epoch):
                        from ..coordinator import Superseded
                        raise Superseded()      # the user said more while it was answering
                    said = clean_reply(ev.text)
                    self._route("edge", "small talk, answered by the fast mind")
                    yield ThinkEvent("route", text="edge")
                    yield ThinkEvent("decided", tools=False)
                    yield ThinkEvent("say", text=said, usage=ev.usage)
                    self.edge_log.append('User: "%s" -> DUET said: "%s"' % (text[-160:], said))
                    return
                if ev.kind == "decided":
                    if not ev.tools:
                        plain = True
                        continue
                    acted_at = time.time()
                    self._route("edge", "a simple phone action")
                    yield ThinkEvent("route", text="edge")
                if ev.kind == "say":
                    async for extra in self._offer_rest(text, toolbox, done_before):
                        yield extra
                yield ev
                if ev.kind == "say":
                    self.edge_log.append('User: "%s" -> done on the phone: %s' % (
                        text[-160:], "; ".join("%s(%s)" % (r.tool, json.dumps(r.args))
                                               for r in coord.history[done_before:] if r.outcome == "ok") or "nothing"))
                    return
        finally:
            await agen.aclose()
        async for ev in self._cloud(text, toolbox, audio, note, allow_listen, done_before,
                                    escalate_why or "nothing for the fast mind to do"):
            yield ev

    async def _web(self, question: str, text: str, toolbox) -> AsyncIterator[ThinkEvent]:
        """A web question in one grounded search, through the toolbox (the coordinator's gate and ledger, the
        timeline), its answer spoken as it comes (the search is told to answer in two spoken sentences). No
        answer: nothing is yielded but the search's own events, and the full cloud mind takes over."""
        coord = toolbox.coord
        epoch = coord.epoch
        args = {"question": question}
        yield ThinkEvent("decided", tools=True)
        yield ThinkEvent("tool_start", name="web_search", args=args)
        out = json.loads(await toolbox.call("web_search", dict(args), epoch=epoch))
        if coord.overtaken(epoch):
            from ..coordinator import Superseded
            raise Superseded()
        status = str(out.get("status", "ok")) if isinstance(out, dict) else "ok"
        yield ThinkEvent("tool_done", name="web_search", args=args, outcome=status)
        answer = " ".join(str(out.get("answer") or "").split()) if isinstance(out, dict) else ""
        if status != "ok" or not answer:
            log.info("web search gave no answer (%s); the full cloud mind takes over", status)
            return
        self.edge_log.append('User: "%s" -> answered from a web search: %s' % (text[-160:], answer[:240]))
        self.cloud_down_until = 0.0
        yield ThinkEvent("say", text=answer)

    async def _light(self, text: str, note: str, coord) -> AsyncIterator[ThinkEvent]:
        """Talk or health with nothing on the phone in it: one cloud call with no tools and short instructions,
        with the recent conversation. No answer: nothing is yielded and the full cloud mind takes over; Google
        not reachable: the error, as the full cloud mind would give it."""
        from google.genai import types
        from .. import gemma_api
        from ..config import CONFIG
        epoch = coord.epoch
        model = gemma_api.resolved("thinker", CONFIG.thinker_model)
        said_before = "\n".join("%s: %s" % ("User" if t["role"] == "user" else "DUET", t["content"])
                                for t in chat_turns(self.recent))
        prompt = (("The conversation so far:\n%s\n\n" % said_before) if said_before else "") + \
            "The user now says: " + text + ("\n\n" + note if note else "") + \
            ('\n\nThe fast voice has just said to the user: "%s" Carry on from it; do not say it again.'
             % self.bridge_said if self.bridge_said else "")
        config = types.GenerateContentConfig(system_instruction=self.light_instructions,
                                             thinking_config=gemma_api.thinking_config(model, "minimal"),
                                             **gemma_api.sampling(model))
        try:
            resp = await gemma_api.generate(model, prompt, config)
        except gemma_api.CloudUnreachable as exc:
            self.cloud_down_until = time.monotonic() + CLOUD_DOWN_S
            yield ThinkEvent("error", text=str(exc))
            return
        except Exception as exc:  # a light call that fails leaves it to the full cloud mind
            log.warning("light cloud call failed (%s); the full cloud mind takes over", str(exc)[:120])
            return
        if coord.overtaken(epoch):
            from ..coordinator import Superseded
            raise Superseded()
        parts = (resp.candidates[0].content.parts or []) if resp.candidates and resp.candidates[0].content else []
        answer = " ".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)).strip()
        if not answer or "<silent>" in answer:
            return
        self.edge_log.append('User: "%s" -> DUET answered: "%s"' % (text[-160:], answer[:240]))
        self.cloud_down_until = 0.0
        from ..thinker import usage_of
        yield ThinkEvent("decided", tools=False)
        yield ThinkEvent("say", text=answer, usage=usage_of(resp))

    def _chat_check(self, words: str, reply: str, offer) -> str:
        """'' when the fast mind's own reply may be spoken (see chat_ok)."""
        last = next((t for role, t in reversed(list(self.recent or [])) if role == "assistant"), "")
        return chat_ok(words, reply, tool_names(self.fast), awaiting=offer is not None or awaiting_answer(last))

    async def _offer_rest(self, text: str, toolbox, done_before: int) -> AsyncIterator[ThinkEvent]:
        """A yes to DUET's whole offer that changed one part ("yes, make it 22") takes up its other parts
        too: the offered actions the plan left out run as offered, through the toolbox like any other
        (the consent check, the gate, the ledger, the read-back), before the reply ends."""
        offer = self.offers.active() if self.offers is not None else None
        if offer is None:
            return
        from .proactive import rest
        coord = toolbox.coord
        epoch = coord.epoch
        done = [(r.tool, r.args) for r in coord.history[done_before:] if r.outcome == "ok"]
        for item in rest(offer, done, text):
            if coord.overtaken(epoch):
                from ..coordinator import Superseded
                raise Superseded()
            log.info("offer: the user's yes takes up %s too", item.label)
            yield ThinkEvent("tool_start", name=item.tool, args=dict(item.args))
            out = json.loads(await toolbox.call(item.tool, dict(item.args), epoch=epoch))
            yield ThinkEvent("tool_done", name=item.tool, args=dict(item.args), text=said_of(out),
                             outcome=str(out.get("status", "ok")) if isinstance(out, dict) else "ok")

    async def _cloud(self, text: str, toolbox, audio, note: str, allow_listen: bool, done_before: int,
                     why: str) -> AsyncIterator[ThinkEvent]:
        coord = toolbox.coord
        if self.cloud_off or time.monotonic() < self.cloud_down_until:
            # no cloud mind: say so at once (the agent's offline line), never wait for it
            yield ThinkEvent("error", text=CLOUD_OFF if self.cloud_off else CLOUD_DOWN)
            return
        # DUET says what it understood, at once, in place of "One moment." (an empty line: nothing to say, as for
        # small talk the cloud mind answers in a second). Before the route: the turn loop covers a cloud route at once.
        self.bridge_said = bridge_line(self.handover, text, tool_names(self.fast))
        yield ThinkEvent("bridge", text=self.bridge_said)
        # the cloud mind takes over: it is told what the fast mind already did, for this request
        # and for earlier ones it handled alone
        self._route("cloud", why)
        yield ThinkEvent("route", text="cloud")   # the turn loop covers the longer wait at once
        if self.cloud_delay_s > 0:
            # the slow-cloud drill: variable latency, live ("One moment.", then "Still working on it.")
            await asyncio.sleep(self.cloud_delay_s)
        if not any(r.outcome == "ok" for r in coord.history[done_before:]):
            # one cloud call where one is enough (nothing ran yet in this request; else the full cloud mind,
            # which hears what ran): a web question goes straight to the search, talk and health to one call
            # with no tools. Either falls back to the full cloud mind if it gets no answer.
            q = web_question(self.handover, text, getattr(toolbox, "specs", None))
            if q:
                answered = False
                async for ev in self._web(q, text, toolbox):
                    answered = answered or ev.kind in ("say", "error")
                    yield ev
                if answered:
                    return
            elif self.light_instructions and light_request(self.handover, text):
                answered = False
                async for ev in self._light(text, note, coord):
                    answered = True
                    yield ev
                if answered:
                    return
        extra = []
        if self.bridge_said:
            extra.append('The fast voice has just said to the user: "%s" Carry on from it; do not say it again.'
                         % self.bridge_said)
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
                    async for extra in self._offer_rest(text, toolbox, done_before):
                        yield extra
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
