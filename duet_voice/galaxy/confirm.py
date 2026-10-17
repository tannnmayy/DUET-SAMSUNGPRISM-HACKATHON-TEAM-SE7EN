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
    return ("Message sent to %s." % who) if str(r.get("how", "")).startswith("sent") \
        else ("Your message to %s is ready; tap send." % who)


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
    return {"play": "Playing.", "pause": "Paused.", "next": "Next track.", "previous": "Previous track."}.get(
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
           "one"}
_SPELLING = {"canceled": "cancelled", "cancel": "cancelled", "minute": "minutes", "second": "seconds"}


def _content(text: str) -> set:
    words = re.findall(r"[a-z0-9']+", (text or "").lower().replace(":", " "))
    return {_SPELLING.get(w, _NUMBER_WORDS.get(w, w)) for w in words}


def adds_nothing(answer: str, confirmations: List[str]) -> bool:
    """True when the slow mind's answer only restates actions already confirmed aloud
    ("OK. I've set a timer for 15 minutes." after "Timer set for 15 minutes.")."""
    if not confirmations or not (answer or "").strip():
        return False
    said = set().union(*(_content(c) for c in confirmations))
    return not (_content(answer) - said - _FILLER)


def confirmation(tool: str, args: Dict[str, Any], result: Any) -> Optional[str]:
    """A few words saying the action is done, or None when the slow mind should say it.
    Only for a result the phone reported as done (status ok)."""
    template = TEMPLATES.get(tool)
    if template is None or not isinstance(result, dict) or result.get("status", "ok") != "ok":
        return None
    try:
        text = template(args or {}, result).strip()
    except Exception:  # a template must never break the conversation
        return None
    return text or None
