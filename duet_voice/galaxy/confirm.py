"""Spoken confirmations, the moment a phone action is done.

When the phone reports that an action ran, DUET says so at once in a few words ("Alarm set
for 7 in the morning."), built only from what the phone reported back. It does not wait for
the slow mind to phrase it, which is a second model call of several seconds. The slow mind's
answer then adds only what is still worth saying.

Only actions that changed something get a confirmation. Readings (battery, alarms, trip)
are explained by the slow mind. So are results that need care: a refused second dose, a
family alert, a check-in.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty"}


def _words(n: int) -> str:
    """0-59 in words: 5 -> 'five', 45 -> 'forty-five'."""
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + ("-" + _ONES[ones] if ones else "")


_CLOCK_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b(\s*(?:a\.?\s?m\b\.?|p\.?\s?m\b\.?))?", re.I)
_GLUED_DAYPART = re.compile(r"(\d)\s*(a|p)\.?\s?m\b\.?", re.I)


def speakable(text: str) -> str:
    """Times in words, as people say them, for the voice only (the screen keeps the digits). Kokoro reads "11:05" as
    "eleven-zero five" and "10:00" as "ten zero zero" (22 Oct 2026, the S24: "ELEVENFIVE"), and "8am" as "eight am"
    the word. '11:05' -> 'eleven oh five', '4:30' -> 'four thirty', '7:00' -> "seven o'clock", '8am' -> '8 AM'."""
    def clock(m: "re.Match") -> str:
        hour, minute, half = int(m.group(1)), int(m.group(2)), (m.group(3) or "")
        mark = ""
        if half:
            mark = " PM" if half.strip().lower().startswith("p") else " AM"
        if minute == 0:
            return _words(hour) + (mark or " o'clock")
        return _words(hour) + (" oh " + _ONES[minute] if minute < 10 else " " + _words(minute)) + mark
    text = _CLOCK_TIME.sub(clock, text or "")
    return _GLUED_DAYPART.sub(lambda m: m.group(1) + (" PM" if m.group(2).lower() == "p" else " AM"), text)


def spoken_time(value: Any) -> str:
    """'16:30' -> '4:30 in the afternoon', '07:00' -> '7 in the morning'."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})", str(value or ""))
    if not m:
        return str(value or "")
    hour, minute = int(m.group(1)) % 24, int(m.group(2))
    h12 = (hour + 11) % 12 + 1
    clock = "%d" % h12 if minute == 0 else "%d:%02d" % (h12, minute)
    if hour == 0:
        part = "at night"                      # 12:15 at night
    elif hour < 12:
        part = "in the morning"                # 4:30 in the morning, as people say for an alarm
    elif hour < 17:
        part = "in the afternoon"
    elif hour < 21:
        part = "in the evening"
    else:
        part = "at night"
    return "%s %s" % (clock, part)


def _degrees(text: Any) -> str:
    return re.sub(r"\s*°\s*C\b", " degrees", str(text or ""))


def _minutes(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if n < 1:
        return "%d seconds" % round(n * 60)
    n = round(n, 1)
    whole = int(n) if n == int(n) else n
    return "%s minute%s" % (whole, "" if whole == 1 else "s")


def _alarm(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    return "Alarm set for %s." % spoken_time(a.get("time"))


def _cancel(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    if r.get("cancelled") is False:
        return "There was no %s alarm, so nothing to cancel." % spoken_time(a.get("time"))
    if r.get("verified") is False:   # the Clock app was asked; nobody can check it did it
        return "I asked the Clock app to cancel the %s alarm; you can check it there." % spoken_time(a.get("time"))
    return "Cancelled the %s alarm." % spoken_time(a.get("time"))


def _brightness(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    if a.get("percent") is not None:
        return "Brightness set to %s percent." % a["percent"]
    return "Adaptive brightness %s." % ("on" if a.get("adaptive") else "off")


def _call(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    who = r.get("calling") or a.get("name")
    return ("Calling %s." % who) if "ring" in str(r.get("state", "")) else ("The dialer is open to call %s." % who)


def _message(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    who = r.get("to") or a.get("to")
    how = str(r.get("how", ""))
    if how.startswith("sent"):
        return "Message sent to %s." % who
    if how.startswith("handed"):       # a direct text the radio has not confirmed yet
        return "Your message to %s is with the phone, but it hasn't confirmed it went out." % who
    return "Your message to %s is ready; tap send." % who


def home_state(now: Any) -> str:
    """A device's state as the phone or SmartThings reported it, as a person says it:
    'on, 24°C cool, room 29°C' -> 'on at 24 degrees, cool mode'; 'on, 40%' -> 'on at 40 percent'."""
    s = re.sub(r",?\s*room -?[\d.]+\s*°C", "", str(now or ""))          # the room's reading is not news here
    s = re.sub(r"\s*\(set to [^)]*\)", "", s).strip()
    s = re.sub(r"^on, (\d+)%$", r"on at \1 percent", s)
    s = re.sub(r"^on, ([\d.]+)\s*°C\s*(\w+)?$", lambda m: "on at %s degrees%s" % (
        m.group(1), (", %s mode" % m.group(2)) if m.group(2) else ""), s)
    return _degrees(s.replace("%", " percent")).strip()


def _home(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    device, now = str(r.get("device") or a.get("device") or ""), home_state(r.get("now"))
    if not (device and now):
        return ""
    name = device if device[:2].isupper() else device[0].lower() + device[1:]   # "TV" stays "TV"
    return "The %s is now %s." % (name, now)


def _destination(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    arrival = r.get("arrival")
    return "Heading to %s%s." % (r.get("destination") or a.get("place"),
                                 (", arriving around %s" % arrival) if arrival else "")


def _event(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    when = spoken_time(a.get("time"))
    day = str(a.get("date") or "")
    try:
        import datetime as _dt
        d = _dt.date.fromisoformat(day)
        today = _dt.date.today()
        day = "today" if d == today else "tomorrow" if (d - today).days == 1 else d.strftime("%A %d %B").replace(" 0", " ")
    except ValueError:
        pass
    how = str(r.get("how", ""))
    if how.startswith("open"):
        return "The calendar is open with %s; tap save." % a.get("title")
    return "Added %s to your calendar, %s at %s." % (a.get("title"), day, when)


def _media(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    # play is a key press: nothing tells DUET that a music app took it (none may be open), so it says what it did
    return {"play": "Pressed play on your music app.", "pause": "Paused.", "next": "Next track.",
            "previous": "Previous track."}.get(
        str(a.get("action")), "")


def _climate(a: Dict[str, Any], r: Dict[str, Any]) -> str:
    if r.get("ac_on") is False:
        return "Car AC off."
    temp = r.get("cabin_temp_c")
    return "Car AC on%s." % ((" at %s degrees" % temp) if temp is not None else "")


TEMPLATES: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], str]] = {
    "set_alarm": _alarm,
    "cancel_alarm": _cancel,
    "set_timer": lambda a, r: "Timer set for %s." % _minutes(a.get("minutes")),
    "set_reminder": lambda a, r: "Reminder set for %s." % spoken_time(a.get("time")),
    "set_brightness": _brightness,
    "set_screen_timeout": lambda a, r: "Screen timeout set to %s seconds." % a.get("seconds"),
    "open_settings": lambda a, r: "Opening %s settings." % str(a.get("page", "")).replace("_", " "),
    "open_app": lambda a, r: "Opening %s." % (r.get("opened") or a.get("name")),
    "navigate_to": lambda a, r: "Starting directions to %s." % a.get("destination"),
    "call_contact": _call,
    "send_message": _message,
    "control_home_device": _home,
    "set_destination": _destination,
    "add_stop": lambda a, r: "Added a stop at %s." % a.get("place"),
    "set_car_climate": _climate,
    "open_navigation": lambda a, r: "Handing the route to Google Maps.",
    "set_torch": lambda a, r: "Torch on." if a.get("on") else "Torch off.",
    "control_media": _media,
    "set_volume": lambda a, r: "Volume set to %s percent." % a.get("percent"),
    "create_event": _event,
}


# ("one" is left out: "a new one" is a pronoun far more often than a number in these answers)
_NUMBER_WORDS = {"two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                 "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15",
                 "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50"}
# words that carry no fact of their own: an answer made only of these and of words already
# spoken in the confirmations adds nothing
_FILLER = {"ok", "okay", "alright", "sure", "done", "great", "all", "right", "now", "just", "i", "i've", "ive",
           "i'm", "im", "have", "has", "been", "is", "it", "it's", "its", "the", "a", "an", "your", "you", "for",
           "to", "at", "in", "on", "of", "and", "that", "this", "there", "so", "as", "with", "set", "new",
           "again", "instead", "up", "o'clock", "oclock", "am", "pm", "successfully", "already", "requested",
           "one", "you'll", "youll", "be", "will", "arriving", "arrive", "around", "about", "heading"}
_SPELLING = {"canceled": "cancelled", "cancel": "cancelled", "minute": "minutes", "second": "seconds",
             # 21 Oct 2026, the emulator: "Camera's open." then "I've opened the camera."
             "opened": "open", "closed": "close", "paused": "pause", "locked": "lock", "unlocked": "unlock"}


def _content(text: str) -> set:
    # "half past four" is the 4:30 already confirmed (21 Oct 2026: "Alarm set for 4:30 in the morning. I've set your
    # alarm for half past four in the morning.")
    # ...and "7:00" the 7 already confirmed ("Alarm set for 7 in the morning. I've changed that to 7:00 in the morning.")
    text = re.sub(r"\b(\d{1,2}):00\b", r"\1", spoken_digits(text))
    words = re.findall(r"[a-z0-9']+", text.lower().replace(":", " "))
    words = [w[:-2] if w.endswith("'s") and len(w) > 4 else w for w in words]     # "camera's" is the camera
    return {_SPELLING.get(w, _NUMBER_WORDS.get(w, w)) for w in words}


def adds_nothing(answer: str, confirmations: List[str]) -> bool:
    """True when the slow mind's answer only restates actions already confirmed aloud
    ("OK. I've set a timer for 15 minutes." after "Timer set for 15 minutes.")."""
    if not confirmations or not (answer or "").strip():
        return False
    said = set().union(*(_content(c) for c in confirmations))
    return not (_content(answer) - said - _FILLER)


# the verbs an answer restates an action with ("I've turned on the AC" after "The air conditioner is now on.")
_RESTATING = {"turned", "switched", "changed", "updated", "moved", "started", "made", "put", "got", "them", "they",
              "back"}
# the same thing in other words: the short form is covered when the long one was said, and back
_SAME = [("ac", ("air", "conditioner")), ("tv", ("television",)), ("aircon", ("air", "conditioner"))]


def _restates(sentence: str, said: set) -> bool:
    rest = _content(sentence) - said - _FILLER - _RESTATING
    for short, long in _SAME:
        if short in rest and all(w in said for w in long):
            rest.discard(short)
        if short in said and all(w in rest for w in long):
            rest -= set(long)
    return not rest


def drop_restated(answer: str, confirmations: List[str]) -> str:
    """The answer without its sentences that only restate an action already confirmed aloud in this reply.
    21 Oct 2026: "The air conditioner is now on." was followed by the cloud mind's "I've turned on the AC.", and
    "Alarm set for 4:30 in the morning." by "I've changed that to 4:30 in the morning." A sentence with anything
    new in it stays."""
    if not confirmations or not (answer or "").strip():
        return answer
    said = set().union(*(_content(c) for c in confirmations))
    parts = [p for p in re.split(r"(?<=[.!?])\s+", answer.strip()) if p]
    return " ".join(p for p in parts if not _restates(p, said))


# What a confirmation says was NOT (yet) done, and the claims in the slow mind's answer that would
# contradict it. The slow mind sometimes words a result its own way (18 Oct 2026, Drive mode: after
# "Your message to Priya is ready; tap send." it added "I've sent that to Priya.").
_NOT_DONE = [
    (re.compile(r"is ready; tap send|hasn't confirmed it went out", re.I),
     re.compile(r"\b(sent|texted|messaged|delivered)\b", re.I)),
    (re.compile(r"^The dialer is open", re.I),
     re.compile(r"\b(calling|called|ringing|rang|connected|on the line)\b", re.I)),
    (re.compile(r"^Pressed play", re.I),
     re.compile(r"\b(is playing|now playing|started playing|playing now|music is on)\b", re.I)),
]


_HOURS = {w: i + 1 for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split())}
_SPOKEN_TIME = re.compile(r"\b(half past|quarter past|quarter to)\s+(\d{1,2}|%s)\b" % "|".join(_HOURS), re.I)


def spoken_digits(text: str) -> str:
    """Times said in words, as digits: "half past four" -> "4:30", "quarter to seven" -> "6:45"."""
    def digits(m: "re.Match") -> str:
        h = int(m.group(2)) if m.group(2).isdigit() else _HOURS[m.group(2).lower()]
        how = m.group(1).lower()
        return "%d:%02d" % (((h + 10) % 12 + 1, 45) if how == "quarter to" else (h, 30 if how == "half past" else 15))
    return _SPOKEN_TIME.sub(digits, text or "")


def clock_times(text: str) -> set:
    """The clock times a sentence states, as (hour on a 12-hour dial, minute): "3:54 PM" and "15:54" are
    (3, 54); "half past three" is (3, 30); "7 in the morning" is (7, 0)."""
    t = spoken_digits(text)
    found = {(int(h) % 12, int(mm)) for h, mm in re.findall(r"\b(\d{1,2}):(\d{2})\b", t)}
    found |= {(int(h) % 12, 0) for h in re.findall(
        r"(?<![:\d])\b(\d{1,2})\s*(?:am|pm|a\.m\.|p\.m\.|o'?clock|in the (?:morning|afternoon|evening)|at night)\b",
        t, re.I)}
    return found


# a clause that says an action happened ("The AC is on", "I've set the alarm", "turned it off")
_SAYS_DONE = re.compile(
    r"\b(is|are|'s|was|were)\s+(now\s+)?(on|off|set|locked|unlocked|running|started|playing|sent|cooling)\b|"
    r"\b(i'?ve|i have|i)\s+(set|turned|switched|started|sent|locked|unlocked|played|called|changed|put|opened)\b|"
    r"\b(turned|switched)\s+(it\s+|them\s+)?(on|off)\b|\bset (to|at|for) \d", re.I)
_NEGATED = re.compile(r"\b(not|cannot|unable|failed|no longer|may not|might not)\b|n't\b", re.I)
# the words that name what a failed action was about, beyond its device (a tool's own noun)
_TOOL_WORDS = {"set_alarm": "alarm", "cancel_alarm": "alarm", "set_timer": "timer", "send_message": "message text",
               "call_contact": "call calling", "set_torch": "torch flashlight", "set_car_climate": "car",
               "set_volume": "volume", "set_brightness": "brightness", "control_media": "music song"}


def _about(tool: str, args: Dict[str, Any]) -> set:
    if tool == "control_home_device":
        from .smartthings import ALIASES
        name = set(re.findall(r"[a-z]+", str((args or {}).get("device", "")).lower()))
        kinds = [a.split() for a in ALIASES.values() if name & set(a.split())]
        return set().union(*kinds) if kinds else (name - {"the", "room", "living", "bedroom", "kitchen"})
    return set(_TOOL_WORDS.get(tool, "").split())


def drop_failed_claims(answer: str, failed: List[tuple], confirmations: List[str]) -> str:
    """The answer without a clause that says a failed action happened. 18 Oct 2026: SmartThings refused
    the AC command (too many requests) and the cloud mind said "The AC is on, but I can't reach your smart
    home devices right now"; what is spoken is "I can't reach your smart home devices right now." A
    clause about something that was confirmed in this reply after all (a retry that worked) is kept."""
    if not failed or not (answer or "").strip():
        return answer
    said = " ".join(confirmations or []).lower()
    words = set()
    for tool, args in failed:
        about = _about(tool, args)
        if about and not any(re.search(r"\b%s\b" % re.escape(w), said) for w in about):
            words |= about
    if not words:
        return answer
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", answer.strip()):
        clauses = re.split(r",?\s+but\s+|;\s*", sentence)
        kept = [c for c in clauses if not (_SAYS_DONE.search(c) and not _NEGATED.search(c)
                                           and set(re.findall(r"[a-z]+", c.lower())) & words)]
        if len(kept) == len(clauses):
            out.append(sentence)
        elif kept:
            text = " ".join(kept)
            text = text[0].upper() + text[1:]
            out.append(text if re.search(r"[.!?]$", text) else text + ".")
    return " ".join(out)


def drop_overclaims(answer: str, confirmations: List[str]) -> str:
    """The answer without the sentences that claim more than the phone reported, or contradict it:
    - a message "sent" that only waits for the user's tap, a call "ringing" in a dialer nobody tapped;
    - a different clock time from the one just confirmed (18 Oct 2026, Drive mode: after "Heading to
      home, arriving around 3:54 PM." the cloud mind added "you'll be there around half past three")."""
    claims = [claim for done, claim in _NOT_DONE if any(done.search(c) for c in confirmations or [])]
    confirmed_times = set().union(*(clock_times(c) for c in confirmations or [])) if confirmations else set()
    if not (claims or confirmed_times) or not (answer or "").strip():
        return answer
    parts = re.split(r"(?<=[.!?])\s+", answer.strip())

    def contradicts(sentence: str) -> bool:
        if any(c.search(sentence) for c in claims):
            return True
        times = clock_times(sentence)
        return bool(confirmed_times and times and not times & confirmed_times)
    return " ".join(p for p in parts if not contradicts(p))


# a sentence saying something was cancelled or undone
_UNDONE = re.compile(r"\b(i'?ve|i have|i)\s+(cancel+ed|undone|undid|removed|deleted|called off)\b|"
                     r"\b(it|that|this|the \w+)\s+(is|was|has been)\s+(cancel+ed|removed|deleted|undone)\b", re.I)
LEFT_ALONE = "Okay, I'll leave it."


def drop_false_undo(answer: str, dropped: bool) -> str:
    """For a request in which nothing ran: the answer without a sentence that says something was cancelled or
    undone, since nothing was. 21 Oct 2026: "Set an alarm for 6 tomorrow morning. Actually, never mind, forget it."
    was answered "I've cancelled that." 3 times out of 3, though no alarm was ever set. If that was the whole answer
    and the user dropped their request (`dropped`), it is said plainly instead."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", (answer or "").strip()) if p]
    kept = [p for p in parts if not _UNDONE.search(p)]
    if len(kept) == len(parts):
        return answer
    if not kept and dropped:
        return LEFT_ALONE
    return " ".join(kept)


def confirmation(tool: str, args: Dict[str, Any], result: Any) -> Optional[str]:
    """A few words saying the action is done, or None when the slow mind should say it.
    Only for a result the phone reported as done (status ok)."""
    template = TEMPLATES.get(tool)
    if template is None:
        from .skills import confirmation as skill_confirmation, get as skill
        if skill(tool) is not None:   # a skill (app/www/skills.json) says its own confirmation
            template = lambda a, r: skill_confirmation(tool, a) or ""  # noqa: E731
    if template is None or not isinstance(result, dict) or result.get("status", "ok") != "ok":
        return None
    try:
        text = template(args or {}, result).strip()
    except Exception:  # a template must never break the conversation
        return None
    return text or None
