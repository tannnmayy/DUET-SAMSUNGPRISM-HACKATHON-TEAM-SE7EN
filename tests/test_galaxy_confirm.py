"""DUET for Galaxy: an action is confirmed the moment the phone says it is done.

- the words come only from the phone's reply, and only for a reply that says ok;
- readings, refusals and sensitive results are left to the slow mind;
- an action the ledger did not repeat is not confirmed again;
- the agent speaks the confirmation between the acknowledgement and the answer;
- the benchmark's agent confirms nothing (its answer comes from the thinker).
"""

import asyncio
import json

from livekit.agents import llm
from livekit.agents.types import FlushSentinel

from duet_voice import agent as agent_mod
from duet_voice.coordinator import Coordinator
from duet_voice.galaxy.confirm import confirmation, spoken_time
from duet_voice.galaxy.modes import MODES
from duet_voice.galaxy.tools import PhoneToolbox
from duet_voice.thinker import ThinkEvent


def test_times_are_said_the_way_people_say_them():
    assert spoken_time("07:00") == "7 in the morning"
    assert spoken_time("16:30") == "4:30 in the afternoon"
    assert spoken_time("19:05") == "7:05 in the evening"
    assert spoken_time("00:15") == "12:15 at night"
    assert spoken_time("04:30") == "4:30 in the morning"
    assert spoken_time("22:00") == "10 at night"


def test_a_done_action_is_confirmed_from_the_phones_reply():
    ok = {"status": "ok"}
    assert confirmation("set_alarm", {"time": "07:00"}, ok) == "Alarm set for 7 in the morning."
    assert confirmation("cancel_alarm", {"time": "03:30"}, {"status": "ok", "cancelled": "3:30 AM"}) == \
        "Cancelled the 3:30 in the morning alarm."
    assert confirmation("cancel_alarm", {"time": "03:30"}, {"status": "ok", "cancelled": False}) == \
        "There was no 3:30 in the morning alarm, so nothing to cancel."
    assert confirmation("set_timer", {"minutes": 0.5}, ok) == "Timer set for 30 seconds."
    assert confirmation("set_timer", {"minutes": 10}, ok) == "Timer set for 10 minutes."
    assert confirmation("set_brightness", {"percent": 40}, ok) == "Brightness set to 40 percent."
    assert confirmation("call_contact", {"name": "my daughter"},
                        {"status": "ok", "calling": "Priya (daughter)", "state": "ringing"}) == "Calling Priya (daughter)."
    assert confirmation("call_contact", {"name": "Priya"},
                        {"status": "ok", "calling": "Priya", "state": "the dialer is open; the user taps call"}) == \
        "The dialer is open to call Priya."
    assert confirmation("send_message", {"to": "Priya", "text": "Late"},
                        {"status": "ok", "to": "Priya", "how": "Messages is open with the text; the user taps send"}) == \
        "Your message to Priya is ready; tap send."
    assert confirmation("control_home_device", {"device": "AC", "action": "set", "value": "24"},
                        {"status": "ok", "device": "Air conditioner", "now": "on, 24°C cool"}) == \
        "Air conditioner: on, 24 degrees cool."
    assert confirmation("set_destination", {"place": "the airport"},
                        {"status": "ok", "destination": "the airport", "arrival": "8:12 PM"}) == \
        "Heading to the airport, arriving around 8:12 PM."


def test_readings_refusals_and_sensitive_results_are_left_to_the_slow_mind():
    assert confirmation("get_battery_report", {}, {"status": "ok", "level": 23}) is None
    assert confirmation("list_alarms", {}, {"status": "ok"}) is None
    assert confirmation("log_medication", {"medicine": "Amlodipine"},
                        {"status": "refused_double_dose", "taken_at": "9:02 AM"}) is None
    assert confirmation("alert_family", {"reason": "a fall"}, {"status": "ok"}) is None
    assert confirmation("set_alarm", {"time": "07:00"}, {"status": "error", "error": "no Clock"}) is None
    assert confirmation("set_alarm", {"time": "07:00"}, "not a dict") is None


def make_box(replies=None):
    async def send(tool, args, timeout):
        return json.dumps((replies or {}).get(tool, {"status": "ok"}))
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.turn_committed()
    return PhoneToolbox(coord, MODES["assistant"]["tools"], send)


def test_the_toolbox_keeps_the_reply_of_the_action_that_just_ran_and_nothing_else():
    box = make_box({"set_brightness": {"status": "needs_permission", "instruction": "allow it"}})
    out = json.loads(asyncio.run(box.call("set_alarm", {"time": "07:00"})))
    assert box.last_done == ("set_alarm", {"time": "07:00"}, {"status": "ok"}, "Alarm set for 7 in the morning.")
    assert out["already_said_to_user"] == "Alarm set for 7 in the morning."   # the thinker knows what was said
    asyncio.run(box.call("set_alarm", {"time": "07:00"}))       # answered from the ledger, not run again
    assert box.last_done is None
    asyncio.run(box.call("set_brightness", {"percent": 40}))    # the phone refused it
    assert box.last_done is None


def test_an_answer_that_only_repeats_the_confirmation_is_dropped_and_one_with_news_is_kept():
    from duet_voice.galaxy.confirm import adds_nothing
    said = ["Timer set for 15 minutes."]
    assert adds_nothing("OK. I've set a timer for 15 minutes.", said)
    assert adds_nothing("Done, fifteen minutes.", said)
    assert not adds_nothing("I've set it. Your other timer still has 3 minutes left.", said)
    assert not adds_nothing("Anything else?", [])                       # nothing confirmed: never dropped
    both = ["Cancelled the 3:30 in the morning alarm.", "Alarm set for 4:30 in the morning."]
    assert adds_nothing("OK. I've cancelled your 3:30 alarm and set a new one for 4:30 in the morning.", both)
    assert not adds_nothing("Your only alarm now is at 4:30, and it's 2 hours away.", both)


class FakeThinker:
    def __init__(self, events):
        self.events, self.history, self.model = events, [], "fake"

    async def run(self, text, toolbox, audio=None, note="", allow_listen=True):
        for ev in self.events:
            await asyncio.sleep(0.05)
            yield ev


class Confirming(agent_mod.DuetAgent):
    def confirm(self, ev):
        return "Alarm set for 7 in the morning." if ev.name == "set_alarm" else ""


def speak(a, text):
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="user", content=text)

    async def go():
        out = []
        async for chunk in a.llm_node(ctx, [], None):
            if not isinstance(chunk, FlushSentinel) and str(chunk).strip():
                out.append(str(chunk).strip())
        return out
    return asyncio.run(go())


def run_agent(cls):
    a = cls(agent_mod.Trace("test"), Coordinator(commit_hold_s=0.0), toolbox=None)
    a._thinker = FakeThinker([ThinkEvent("decided", tools=True),
                              ThinkEvent("tool_start", name="set_alarm", args={"time": "07:00"}),
                              ThinkEvent("tool_done", name="set_alarm", args={"time": "07:00"}, outcome="ok"),
                              ThinkEvent("say", text="<silent>")])

    async def no_talker(text, context="", usage=None, instructions=None):
        return None
    agent_mod.talker.acknowledgement = no_talker
    return speak(a, "set an alarm for 6:30, no, 7")


def test_the_confirmation_is_spoken_between_the_acknowledgement_and_the_answer():
    assert run_agent(Confirming) == ["One moment.", "Alarm set for 7 in the morning."]


def test_the_benchmark_agent_confirms_nothing():
    assert run_agent(agent_mod.DuetAgent) == ["One moment."]


class FailingAfterConfirm(Confirming):
    def error_line(self, confirmed):
        return "" if confirmed else "Sorry, I couldn't get that done just now. Could you say it again?"


def test_a_failure_after_the_action_was_confirmed_asks_for_nothing_again():
    a = FailingAfterConfirm(agent_mod.Trace("test"), Coordinator(commit_hold_s=0.0), toolbox=None)
    a._thinker = FakeThinker([ThinkEvent("decided", tools=True),
                              ThinkEvent("tool_done", name="set_alarm", args={"time": "07:00"}, outcome="ok"),
                              ThinkEvent("error", text="504 deadline")])

    async def no_talker(text, context="", usage=None, instructions=None):
        return None
    agent_mod.talker.acknowledgement = no_talker
    assert speak(a, "set an alarm for 7") == ["One moment.", "Alarm set for 7 in the morning."]
    assert a._pending_consumed == 1          # the request is answered: it will not be read again


def test_the_benchmark_still_apologises_when_the_thinker_fails():
    a = agent_mod.DuetAgent(agent_mod.Trace("test"), Coordinator(commit_hold_s=0.0), toolbox=None)
    a._thinker = FakeThinker([ThinkEvent("decided", tools=True), ThinkEvent("error", text="504")])

    async def no_talker(text, context="", usage=None, instructions=None):
        return None
    agent_mod.talker.acknowledgement = no_talker
    assert speak(a, "track my order")[-1] == "Sorry, something went wrong on my side. Could you say that once more?"
