"""Plain readings answered from the reading itself (duet_voice/galaxy/readings.py), with no model call.

- a plain lookup goes to its reading; judgement, an action, or the weather goes to the cloud mind;
- the answer states only what the reading says, and a reading it cannot answer plainly is handed on;
- in the two-speed router: read, said, and the cloud mind never called.
"""

import asyncio
import json

from duet_voice.galaxy import readings as R
from duet_voice.galaxy.fast import HybridThinker


def test_plain_lookups_go_to_their_reading_and_the_rest_to_the_cloud_mind():
    plain = {"How much battery do I have left?": "get_battery_report", "Is my phone charging?": "get_battery_report",
             "What alarms do I have?": "list_alarms", "When is my next alarm?": "list_alarms",
             "When will I get there?": "get_trip_status", "Is the front door locked?": "get_home_status",
             "Are the lights on?": "get_home_status"}
    for q, tool in plain.items():
        assert R.which(q) == tool, q
    for q in ("Why is my battery draining so fast?", "How long will my battery last?", "Is my battery okay?",
              "Should I charge my phone now?", "Turn on the AC.", "What's the weather in Chennai?",
              "Is it hot outside?", "Set an alarm for 7."):
        assert R.which(q) is None, q


def test_the_answer_says_only_what_the_reading_says():
    bat = {"status": "ok", "level": 64, "charging": True, "temperature_c": 33.5}
    assert R.answer("get_battery_report", bat, "how much battery do i have left?") == "Your battery is at 64 percent, and charging."
    assert R.answer("get_battery_report", dict(bat, charging=False), "is my phone charging?") == "No, it isn't charging, at 64 percent."
    assert R.answer("list_alarms", {"status": "ok", "set_by_duet": [], "phone_next_alarm": "Sun 6:45 AM"},
                    "when is my next alarm?") == "Your next alarm is Sunday at 6:45 AM."
    assert R.answer("list_alarms", {"status": "ok", "set_by_duet": [], "phone_next_alarm": None},
                    "what alarms do i have?") == "You have no alarms set."
    assert R.answer("get_trip_status", {"status": "ok", "trip": {"destination": "home", "minutes_left": 12, "arrival": "3:54 PM"}},
                    "when will i get there?") == "You'll be home at about 3:54 PM, in 12 minutes."
    home = {"status": "ok", "devices": ["Living room light (Living room): on, 70%", "Bedroom light (Bedroom): off",
                                        "Air conditioner (Living room): on, 22°C cool, room 29°C", "Front door lock (Entrance): locked"]}
    assert R.answer("get_home_status", home, "is the ac on?") == "The air conditioner is on at 22 degrees, cool mode."
    assert R.answer("get_home_status", home, "is the bedroom light on?") == "The bedroom light is off."
    assert R.answer("get_home_status", home, "is the tv on?") is None              # no such device: the cloud mind
    assert R.answer("get_battery_report", {"status": "error", "error": "x"}, "how much battery?") is None


class Box:
    def __init__(self, result):
        from duet_voice.coordinator import Coordinator
        self.coord, self.specs, self.result, self.calls = Coordinator(), {"get_battery_report": {}}, result, []

    async def call(self, tool, args, epoch=None):
        self.calls.append(tool)
        return json.dumps(self.result)


class Mind:
    def __init__(self):
        self.model, self.history, self.thinking, self.calls = "m", [], "", 0

    async def run(self, *a, **k):
        self.calls += 1
        if False:
            yield None


def test_in_the_router_a_plain_reading_is_read_and_said_without_any_model():
    fast, slow = Mind(), Mind()
    box = Box({"status": "ok", "level": 41, "charging": False})

    async def go():
        return [ev async for ev in HybridThinker(fast, slow).run("How much battery do I have left?", box, note="")]
    events = asyncio.run(go())
    assert [e.text for e in events if e.kind == "say"] == ["Your battery is at 41 percent."]
    assert box.calls == ["get_battery_report"] and fast.calls == 0 and slow.calls == 0
