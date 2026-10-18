"""DUET speaks first (duet_voice/galaxy/proactive.py): offers built from facts, and answers to them.

- nearly home: the AC when it is off and the day is hot, a light when it is dark; nothing else;
- the user's answer: yes, a change ("make it 22"), a part ("just the AC"), or no; silence lapses;
- only what was offered can ride on a yes, changed only by values the user says;
- the fast mind may act on an answer that takes the offer up; a plain no is answered at once.
"""

import asyncio
import time

from duet_voice.galaxy import proactive
from duet_voice.galaxy.fast import HybridThinker, vet_plan
from duet_voice.galaxy.proactive import Offers, accepts, arrival_offer, covers, declines, same_device, washer_offer

HOME = [
    {"name": "Air conditioner", "room": "Living room", "kind": "ac", "state": "off (set to 24°C)", "on": False},
    {"name": "Living room light", "room": "Living room", "kind": "light", "state": "off", "on": False},
    {"name": "Bedroom light", "room": "Bedroom", "kind": "light", "state": "off", "on": False},
    {"name": "Front door lock", "room": "Entrance", "kind": "lock", "state": "locked", "on": False},
]


def test_nearly_home_on_a_hot_evening_offers_the_ac_and_the_living_room_light():
    o = arrival_offer(10, HOME, 31, hour=19)
    assert o.text == ("You'll be home in about 10 minutes, and it's 31 degrees outside. Shall I start the living room "
                      "AC at 24 degrees and switch on the living room light?")
    assert [i.args for i in o.items] == [{"device": "Air conditioner", "action": "turn_on", "value": "24"},
                                         {"device": "Living room light", "action": "turn_on"}]
    assert "31 °C outside" in o.why and "Air conditioner is off" in o.why


def test_only_what_helps_is_offered():
    assert [i.args["device"] for i in arrival_offer(10, HOME, 31, hour=14).items] == ["Air conditioner"]   # daytime
    assert [i.args["device"] for i in arrival_offer(10, HOME, 19, hour=20).items] == ["Living room light"]  # cool
    on = [dict(d, on=True, state="on") for d in HOME]
    o = arrival_offer(8, on, 33, hour=21)
    assert o.items == [] and o.text == "You'll be home in about 8 minutes."      # nothing to do: no question
    assert len(arrival_offer(10, HOME, None, hour=12).items) == 1                # unknown weather: the AC is offered
    # the front door is never offered (unlocking is not something DUET suggests)
    assert all(i.args["device"] != "Front door lock" for i in arrival_offer(10, HOME, 35, hour=23).items)


def test_answers_to_an_offer():
    for yes in ("Yes.", "Yeah, please.", "Sure, make it 22.", "Okay, just the AC.", "Go ahead"):
        assert accepts(yes) and not declines(yes), yes
    for no in ("No thanks.", "No.", "Not now", "Nah, leave it.", "I'm fine, thanks."):
        assert declines(no) and not accepts(no), no
    # a change of value is not a no
    assert not declines("No, make it 22.") and not declines("No, just the AC.")


def test_only_what_was_offered_rides_on_a_yes():
    offer = arrival_offer(10, HOME, 31, hour=19)
    ac = ("control_home_device", {"device": "AC", "action": "turn_on", "value": "24"})
    assert covers(offer, [ac], "Yes.")
    assert covers(offer, [("control_home_device", {"device": "air conditioner", "action": "set", "value": "22"})],
                  "Yes, make it 22.")                                                 # changed by the user
    assert not covers(offer, [("control_home_device", {"device": "AC", "action": "set", "value": "18"})], "Yes.")
    assert not covers(offer, [("control_home_device", {"device": "Front door lock", "action": "unlock"})], "Yes.")
    assert not covers(offer, [("control_home_device", {"device": "Bedroom light", "action": "turn_on"})], "Yes.")
    assert covers(offer, [("control_home_device", {"device": "Living room light", "action": "turn_on"})], "Just the light.")
    assert not covers(offer, [ac], "No thanks.")
    assert not covers(offer, [ac], "Is it hot at home?")                               # a question is answered
    assert not covers(offer, [("set_alarm", {"time": "07:00"})], "Yes.")


def test_devices_are_one_by_kind_and_room():
    assert same_device("AC", "Air conditioner") and same_device("the living room light", "Living room light")
    assert not same_device("bedroom light", "Living room light") and not same_device("TV", "Air conditioner")


def test_an_offer_lapses_and_closes_after_its_answer():
    offers = Offers()
    o = offers.make(washer_offer())
    assert offers.active() is o and offers.consent("set_timer", {"minutes": 10, "label": "x"}, "Yes please.")
    assert offers.done("set_timer", {"minutes": 10}) is o and offers.active() is o     # still usable in this answer
    offers.answered()
    assert offers.active() is None and o.status == "closed"
    late = offers.make(washer_offer())
    late.made_at = time.monotonic() - proactive.OFFER_TTL_S - 1
    assert offers.active() is None and late.status == "expired"                        # silence never acts


def test_the_simulated_home_is_read_like_smartthings():
    devices = proactive.devices_from_phone(["Living room light: on, 70%", "Air conditioner: off (set to 24°C)",
                                            "Front door: locked", "Washer: running, 25 min left"])
    assert [(d["kind"], d["on"]) for d in devices] == [("light", True), ("ac", False), ("lock", False), ("washer", True)]


# --- the fast mind and the cloud mind -----------------------------------------------------------------

def test_the_fast_mind_may_act_on_an_answer_that_takes_the_offer_up():
    offer = arrival_offer(10, HOME, 31, hour=19)
    plan = [("control_home_device", {"device": "Air conditioner", "action": "turn_on", "value": "22"})]
    assert vet_plan(plan, "Yes, make it 22.", offer) == ""
    assert vet_plan(plan, "Yes, make it 22.") != ""                     # without the offer, "yes" names nothing
    assert vet_plan([("control_home_device", {"device": "TV", "action": "turn_on"})], "Yes.", offer) != ""


class Script:
    def __init__(self, model, events):
        self.model, self.events, self.history, self.thinking, self.calls = model, events, [], "", 0

    async def run(self, text, toolbox, audio=None, note="", allow_listen=True):
        self.calls += 1
        for ev in self.events:
            yield ev


class Box:
    def __init__(self):
        from duet_voice.coordinator import Coordinator
        self.coord = Coordinator()


def test_a_plain_no_to_an_offer_is_answered_at_once_and_nothing_runs():
    from duet_voice.thinker import ThinkEvent
    fast, slow = Script("e2b", []), Script("26b", [ThinkEvent("say", text="Sure?")])
    hybrid = HybridThinker(fast, slow)
    seen = []
    hybrid.offers = Offers(on_status=lambda o: seen.append(o.status))
    hybrid.offers.make(arrival_offer(10, HOME, 31, hour=19))

    async def go():
        return [ev async for ev in hybrid.run("No thanks.", Box(), note="")]
    events = asyncio.run(go())
    assert [e.text for e in events if e.kind == "say"] == ["Okay, I'll leave it."]
    assert fast.calls == 0 and slow.calls == 0 and seen == ["declined"] and hybrid.offers.active() is None
