"""Plain readings, answered from the reading itself in about a second.

"How much battery do I have?", "Is the front door locked?", "When will I get there?", "What alarms do I
have?": a plain lookup. The phone (or the home) reads it, and the answer is said from that reading,
with no model call, as the confirmations of actions are (confirm.py). Without this, a question costs two
cloud calls (about 4 s at the median): the cloud mind first asks for the reading, then words the answer.

Only plain readings: a question with any judgement in it ("why", "should I", "is that enough", "how long
will it last") or any action in it goes to the cloud mind, which explains. So does a reading the
template cannot answer (a device it cannot pick out). Every answer states only what the reading says.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .confirm import home_state
from .proactive import _kinds

# words that ask for judgement or explanation: the cloud mind answers those
JUDGEMENT = re.compile(r"\b(why|should|enough|ok(ay)?|fine|normal|good|bad|help|best|better|worse|recommend|explain|"
                       r"caus(e|es|ing)|because|worth|safe|healthy|problem|wrong|fix|improve|save|last|lasts|drain(s|ing)?|"
                       r"tips?|advice|suggest|weather|rain|temperature outside)\b", re.I)
# words that ask for an action: the usual path
ACTION = re.compile(r"\b(turn|switch|set|call|text|send|message|open|start|stop|play|pause|navigate|take me|remind|"
                    r"lock|unlock|make|put|cancel|add|change|dim|raise|lower)\b", re.I)
QUESTION = re.compile(r"^\W*(is|are|do|does|did|what|what's|whats|which|when|how|how's|tell me|check)\b", re.I)

READINGS = [
    ("get_battery_report", re.compile(
        r"\bbattery\b.*\b(much|level|percent|percentage|left|at|charge)\b|\b(how much|what'?s|what is)\b.*\bbattery\b|"
        r"\bis (my |the )?(phone|battery) charging\b|\bhow (hot|warm) is (my |the )?(phone|battery)\b", re.I)),
    ("list_alarms", re.compile(r"\b(what|which|when|any|do i have)\b.*\balarms?\b|\bnext alarm\b", re.I)),
    ("get_trip_status", re.compile(
        r"\b(when|what time) (will|do|would|shall) (i|we) (get|be|arrive|reach)\b|\bhow long (until|till|before|to|is left)\b|"
        r"\b(eta|arrival time)\b|\bhow (far|much longer)\b", re.I)),
    ("get_home_status", re.compile(
        r"^\W*(is|are)\b.*\b(on|off|locked|unlocked|running|open|closed|docked|charging|finished|done)\b", re.I)),
    # the time and date, here or in a named place, from the clock and the place's time zone, said from a template
    # (22 Oct 2026, the S24: "isn't it afternoon now?" went to a web search, which answered "5:48 AM", the time in
    # UTC; "what time is it in London?" was read right by get_time, 7:25, but the cloud mind said "half past seven",
    # and "the exact time in London" then went to a web search, which said 6:25)
    ("get_time", re.compile(
        r"\bwhat('?s| is) the (exact |current |local )?time\b|\bwhat time is it\b|\bthe time (now|right now)\b|"
        r"\b(exact|current|local) time\b|\bwhat('?s| is) (the |today'?s )?(date|day)\b|\bwhat day is (it|today)\b|"
        r"\b(is|isn'?t) it (morning|afternoon|evening|night)\b", re.I)),
    # where the user is, from the phone's GPS (22 Oct 2026: the cloud mind put "I can help you with navigation if you
    # tell me where you want to go" before the answer)
    ("get_location", re.compile(r"\bwhere am i\b|\bwhere i am\b|\bmy (current |exact |live )?location\b", re.I)),
]
# the place of a clock question: "what time is it in New York right now?" -> "New York"
_TIME_IN = re.compile(r"\b(?:time|date|day)\b[^?.!,]*?\b(?:in|at)\s+(?!the moment\b|the minute\b|the morning\b|"
                      r"the evening\b|the afternoon\b|night\b)([a-z][a-z .'-]{1,40}?)\s*"
                      r"(?:\b(?:right now|now|currently|today|at the moment|please)\b\s*)?(?:[?.!,]|$)", re.I)


def args_for(tool: str, words: str) -> Dict[str, Any]:
    """What a reading needs from the words: the place of a clock question."""
    if tool == "get_time":
        m = _TIME_IN.search(words or "")
        if m:
            return {"place": m.group(1).strip()}
    return {}

_DAYS = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday",
         "Sun": "Sunday"}
_ROOMS = ("living", "bedroom", "kitchen", "hall", "office", "front", "back", "porch", "dining", "garage", "entrance",
          "utility", "kids", "guest")


def which(words: str) -> Optional[str]:
    """The reading a plain question asks for, or None (judgement, an action, or not a plain question)."""
    w = (words or "").strip()
    if not w or len(w.split()) > 14 or not (w.endswith("?") or QUESTION.search(w)):
        return None
    if JUDGEMENT.search(w) or ACTION.search(w):
        return None
    return next((tool for tool, pattern in READINGS if pattern.search(w)), None)


def _num(v: Any) -> str:
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else "%.1f" % f
    except (TypeError, ValueError):
        return str(v)


def _battery(r: Dict[str, Any], w: str) -> Optional[str]:
    level, charging = r.get("level"), r.get("charging")
    if level is None:
        return None
    if re.search(r"\b(hot|warm|heat)\b", w) and r.get("temperature_c") is not None:
        return "Your battery is at %s degrees." % _num(r["temperature_c"])
    if re.search(r"\bcharging\b", w) and not re.search(r"how much|level|percent|left", w):
        return "%s, at %s percent." % ("Yes, it's charging" if charging else "No, it isn't charging", _num(level))
    return "Your battery is at %s percent%s." % (_num(level), ", and charging" if charging else "")


def _when(next_alarm: str) -> str:
    """'Sun 6:45 AM' -> 'Sunday at 6:45 AM'."""
    m = re.match(r"\s*(\w{3})\s+(.+)", next_alarm or "")
    return "%s at %s" % (_DAYS[m.group(1)], m.group(2)) if m and m.group(1) in _DAYS else (next_alarm or "")


def _clock(alarm: str) -> str:
    """A time as the phone wrote it, as it is said: "04:30" -> "4:30 in the morning" (the app already sends
    "4:30 AM"; any other phone, and the test phone, send the 24-hour time)."""
    from .confirm import spoken_time
    m = re.match(r"\s*(\d{1,2}:\d{2})\s*(\(.*\))?\s*$", str(alarm))
    return (spoken_time(m.group(1)) + (" " + m.group(2) if m.group(2) else "")) if m else str(alarm)


def _alarms(r: Dict[str, Any], w: str) -> Optional[str]:
    duet = [_clock(a) for a in (r.get("set_by_duet") or []) if a]
    nxt = r.get("phone_next_alarm")
    if "next" in w and nxt:
        return "Your next alarm is %s." % _when(nxt)
    if duet:
        said = duet[0] if len(duet) == 1 else ", ".join(duet[:-1]) + " and " + duet[-1]
        head = "Your alarm is set for %s." % said if len(duet) == 1 else "Your alarms are set for %s." % said
        return head
    if nxt:
        return "Your next alarm is %s." % _when(nxt)
    return "You have no alarms set."


def _trip(r: Dict[str, Any], w: str) -> Optional[str]:
    t = r.get("trip")
    if not isinstance(t, dict):
        return "There's no trip on right now."
    if t.get("minutes_left") is None or not t.get("arrival"):
        return None
    home = re.search(r"\bhome\b", str(t.get("destination", "")), re.I)
    return "You'll %s at about %s, in %s minutes." % ("be home" if home else "arrive", t["arrival"], _num(t["minutes_left"]))


def _devices(lines: List[str]) -> List[Dict[str, str]]:
    out = []
    for line in lines or []:
        head, _, state = str(line).partition(":")
        m = re.match(r"\s*(.+?)\s*(?:\(([^)]*)\))?\s*$", head)
        out.append({"name": m.group(1) if m else head.strip(), "room": (m.group(2) or "") if m else "",
                    "state": state.strip()})
    return out


def _home(r: Dict[str, Any], w: str) -> Optional[str]:
    asked_kinds = _kinds(w)
    if not asked_kinds:
        return None
    rooms = {x for x in _ROOMS if re.search(r"\b%s" % x, w, re.I)}
    picked = []
    for d in _devices(r.get("devices") or []):
        if not (_kinds(d["name"]) & asked_kinds):
            continue
        place = (d["name"] + " " + d["room"]).lower()
        if rooms and not any(x in place for x in rooms):
            continue
        picked.append(d)
    if not picked or len(picked) > 3:
        return None
    parts = ["the %s is %s" % (d["name"].lower(), home_state(d["state"])) for d in picked]
    text = " and ".join(parts)
    return text[0].upper() + text[1:] + "."


def _time(r: Dict[str, Any], w: str) -> Optional[str]:
    """'It's 11:20 in the morning.' (the voice says the time in words), or the date when only the date was asked."""
    there = "" if str(r.get("where") or "").startswith("where the user") else str(r.get("where") or "").split(",")[0]
    if re.search(r"\b(date|day)\b", w) and not re.search(r"\btime\b", w):
        return ("It's %s%s." % (r["date"], " in " + there if there else "")) if r.get("date") else None
    said = r.get("said") or r.get("time")
    return ("It's %s%s." % (said, " in " + there if there else "")) if said else None


def _location(r: Dict[str, Any], w: str) -> Optional[str]:
    """'You're in Potheri, Chennai, Tamil Nadu.'; no place name: the cloud mind words it."""
    return "You're in %s." % r["place"] if r.get("place") else None


ANSWERS = {"get_battery_report": _battery, "list_alarms": _alarms, "get_trip_status": _trip, "get_home_status": _home,
           "get_time": _time, "get_location": _location}


def answer(tool: str, result: Any, words: str) -> Optional[str]:
    """The spoken answer from the reading, or None when the template cannot answer it plainly."""
    if isinstance(result, dict) and result.get("status") == "already_done" and isinstance(result.get("result"), dict):
        result = result["result"]          # read once already in this request: the same reading
    if not isinstance(result, dict) or result.get("status", "ok") != "ok" or tool not in ANSWERS:
        return None
    try:
        return ANSWERS[tool](result, (words or "").lower())
    except Exception:  # a template must never break the conversation: the cloud mind answers instead
        return None
