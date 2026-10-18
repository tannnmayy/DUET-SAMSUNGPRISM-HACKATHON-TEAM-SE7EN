"""DUET speaks first: offers made from what the car, the phone and the home report.

"You'll be home in about 10 minutes, and it's 31 degrees outside. Shall I start the living room AC at
24 degrees?" The user can accept it ("yes"), change it ("yes, make it 22", "just the AC"), decline it
("no thanks"), or talk over it; silence never acts, and an offer lapses after a minute.

An offer is built here from facts (the home's devices as SmartThings or the app reports them, the
outside temperature, the time), with no model call, so it is quick and says only what is true. It is
spoken by the agent when nobody is talking. Nothing runs from the offer itself: an offered action runs
only after the user's own words, through the coordinator like any other (the gate's hold, the ledger,
the read-back), and only as offered, changed only by values the user says (`covers`).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .smartthings import ALIASES

OFFER_TTL_S = 60.0       # an offer nobody answers lapses: a late "yes" to something else must not run it
AC_SETPOINT = "24"       # the comfortable default an arrival offer proposes (the user can change it)
# outside temperature from which arriving home to a cooled room helps (DUET_HOT_C: a demo on a cool day)
HOT_C = float(os.environ.get("DUET_HOT_C", "26"))

# "yes", "sure, make it 22", "okay just the AC": an answer that takes the offer up
_YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok(ay)?|alright|all right|please|go ahead|do it|do that|"
                  r"sounds good|why not|of course|definitely|perfect|great)\b", re.I)
# "no", "no thanks", "not now": an answer that turns it down (but "no, make it 22" changes it)
_NO = re.compile(r"^\W*(no|nope|nah|not now|not yet|no thanks|no thank you|don'?t|leave it|skip it|"
                 r"never ?mind|it'?s fine|i'?m (fine|good|okay)|not needed)\b", re.I)
_CHANGE = re.compile(r"\b(make it|set it|but|just|only|instead|change|at \d|to \d)\b|\d", re.I)


def declines(words: str) -> bool:
    """A plain no: "no thanks", "not now". "No, make it 22" is a change, not a no."""
    w = (words or "").strip()
    return bool(_NO.search(w)) and not _CHANGE.search(w) and len(w.split()) <= 6


def accepts(words: str) -> bool:
    w = (words or "").strip()
    return bool(_YES.search(w)) and not declines(w)


def _numbers(text: str) -> set:
    return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text or "")}


def _kinds(name: str) -> set:
    words = set(re.findall(r"[a-z]+", (name or "").lower()))
    return {kind for kind, aliases in ALIASES.items() if words & set(aliases.split())}


def same_device(a: str, b: str) -> bool:
    """"AC", "air conditioner" and "Air conditioner" are one device; "bedroom light" is not the
    living room light."""
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ka, kb = _kinds(a), _kinds(b)
    if not (ka & kb):
        return False
    rooms = {"living", "bedroom", "kitchen", "hall", "office", "front", "back", "porch", "dining", "garage"}
    ra, rb = set(re.findall(r"[a-z]+", a)) & rooms, set(re.findall(r"[a-z]+", b)) & rooms
    return not ra or not rb or bool(ra & rb)


@dataclass
class OfferItem:
    tool: str
    args: Dict[str, Any]
    label: str                     # as said: "start the living room AC at 24 degrees"

    def matches(self, tool: str, args: Dict[str, Any]) -> bool:
        if tool != self.tool:
            return False
        if tool == "control_home_device":
            return same_device(str(args.get("device", "")), str(self.args.get("device", "")))
        return True


@dataclass
class Offer:
    text: str                      # what DUET says
    why: str                       # the facts it rests on, for the timeline
    items: List[OfferItem]
    source: str = ""               # "arrival", "washer"
    made_at: float = field(default_factory=time.monotonic)
    # open; accepted (its actions may still run for the answer being made); closed once that answer is
    # delivered; declined; expired
    status: str = "open"
    done: List[str] = field(default_factory=list)

    def is_open(self, now: Optional[float] = None) -> bool:
        if self.status == "open" and (now or time.monotonic()) - self.made_at > OFFER_TTL_S:
            self.status = "expired"
        return self.status in ("open", "accepted") and bool(self.items)

    def match(self, tool: str, args: Dict[str, Any]) -> Optional[OfferItem]:
        return next((i for i in self.items if i.matches(tool, args)), None)

    def note(self) -> str:
        """What the thinker is told while the offer is open."""
        age = int(time.monotonic() - self.made_at)
        calls = "; ".join("%s(%s)" % (i.tool, _args(i.args)) for i in self.items)
        return ('DUET\'s open offer, said by DUET itself %d s ago without being asked: "%s" Offered actions: %s. '
                "If the user accepts (yes, sure, go ahead), call exactly these, changed only as the user says "
                "(\"make it 22\"); if they accept only some (\"just the AC\"), call only those; if they decline or "
                "talk about something else, call none of them." % (age, self.text, calls))

    def event(self) -> Dict[str, Any]:
        return {"kind": "offer", "source": self.source, "text": self.text, "why": self.why,
                "items": [{"tool": i.tool, "args": i.args, "label": i.label} for i in self.items]}


def _args(args: Dict[str, Any]) -> str:
    return ", ".join("%s=%s" % (k, v) for k, v in args.items())


def covers(offer: Optional[Offer], calls: List[tuple], words: str) -> bool:
    """Does the user's answer take up the open offer, with a plan that stays inside it? Every call
    must be an offered one (same action, same device), and every number in it either offered or
    said by the user ("yes, make it 22"). A question or a plain no never does."""
    if offer is None or not offer.is_open() or not calls or declines(words):
        return False
    w = (words or "").strip()
    if w.endswith("?"):
        return False
    said = _numbers(w)
    for tool, args in calls:
        item = offer.match(tool, args)
        if item is None:
            return False
        offered = _numbers(" ".join(str(v) for v in item.args.values()))
        for v in args.values():
            for n in _numbers(str(v)):
                if n not in offered and n not in said:
                    return False
    return True


# --- the offers ------------------------------------------------------------------------------------

def _ac_label(d: Dict[str, Any]) -> str:
    room = (d.get("room") or "").strip().lower()
    return "the %sAC" % (room + " " if room and room not in (d.get("name") or "").lower() else "")


def arrival_offer(minutes: int, devices: List[Dict[str, Any]], outside_c: Optional[float],
                  hour: int, place: str = "home") -> Offer:
    """Nearly home: get the home ready. At most two things, each one only when it helps: the AC when it
    is off and the day is hot (or the temperature is unknown), a living-room light when it is dark and
    the light is off. With nothing to offer, DUET only says how far home is."""
    items: List[OfferItem] = []
    facts = ["%d min from %s" % (minutes, place)]
    if outside_c is not None:
        facts.append("%d °C outside" % round(outside_c))
    ac = next((d for d in devices if d.get("kind") == "ac" and not d.get("on")), None)
    if ac and (outside_c is None or outside_c >= HOT_C):
        items.append(OfferItem("control_home_device",
                               {"device": ac["name"], "action": "turn_on", "value": AC_SETPOINT},
                               "start %s at %s degrees" % (_ac_label(ac), AC_SETPOINT)))
        facts.append("%s is off" % ac["name"])
    if hour >= 18 or hour < 6:
        lights = [d for d in devices if d.get("kind") == "light" and not d.get("on")]
        light = next((d for d in lights if "living" in ((d.get("room") or "") + " " + d["name"]).lower()), None) \
            or (lights[0] if lights else None)
        if light:
            items.append(OfferItem("control_home_device", {"device": light["name"], "action": "turn_on"},
                                   "switch on the %s" % light["name"].lower()))
            facts.append("dark, %s is off" % light["name"])
    head = "You'll be %s in about %d minutes" % (place if place != "home" else "home", minutes)
    if outside_c is not None and items and items[0].args.get("device") == (ac or {}).get("name"):
        head += ", and it's %d degrees outside" % round(outside_c)
    text = head + "." if not items else head + ". Shall I " + " and ".join(i.label for i in items) + "?"
    return Offer(text=text, why="; ".join(facts), items=items, source="arrival")


def washer_offer() -> Offer:
    return Offer(text="The washer has finished. Shall I set a 10-minute timer to remind you to hang the laundry?",
                 why="the washer reported finished",
                 items=[OfferItem("set_timer", {"minutes": 10, "label": "Hang the laundry"},
                                  "set a 10-minute laundry timer")],
                 source="washer")


def devices_from_phone(lines: List[str]) -> List[Dict[str, Any]]:
    """The app's simulated home ("Air conditioner: off (set to 24°C)") as SmartThings-like devices."""
    out = []
    for line in lines or []:
        name, _, state = str(line).partition(":")
        name, state = name.strip(), state.strip().lower()
        kinds = _kinds(name)
        kind = next(iter(kinds), "other") if len(kinds) == 1 else ("ac" if "ac" in kinds else next(iter(kinds), "other"))
        on = state.startswith("on") or state.startswith("running") or state == "unlocked" or state == "cleaning"
        out.append({"name": name, "room": "", "kind": kind, "state": state, "on": on})
    return out


class Offers:
    """The one open offer of a conversation (a newer one replaces it)."""

    def __init__(self, on_status=None) -> None:
        self.current: Optional[Offer] = None
        self.on_status = on_status          # told when the user declines an offer (for the timeline)

    def active(self) -> Optional[Offer]:
        if self.current is not None and not self.current.is_open():
            return None
        return self.current

    def make(self, offer: Offer) -> Offer:
        self.current = offer
        return offer

    def resolve(self, status: str) -> Optional[Offer]:
        offer = self.active()
        if offer is not None:
            offer.status = status
            if self.on_status is not None:
                try:
                    self.on_status(offer)
                except Exception:  # the timeline is a view
                    pass
        return offer

    def consent(self, tool: str, args: Dict[str, Any], words: str) -> bool:
        """The consent gate's answer for an offered action: the user's answer takes the offer up."""
        offer = self.active()
        return offer is not None and covers(offer, [(tool, args)], words)

    def done(self, tool: str, args: Dict[str, Any]) -> Optional[Offer]:
        """An offered action ran: the offer is accepted. Its other actions may still run in the same
        answer ("yes, both"); it closes when that answer has been delivered (`answered`)."""
        offer = self.active()
        if offer is None or offer.match(tool, args) is None:
            return None
        offer.done.append(tool)
        first = offer.status == "open"
        offer.status = "accepted"
        return offer if first else None

    def answered(self) -> None:
        """A reply was delivered: an accepted offer is finished."""
        if self.current is not None and self.current.status == "accepted":
            self.current.status = "closed"
