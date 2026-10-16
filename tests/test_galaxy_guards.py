"""DUET for Galaxy's deterministic guards around the models.

- a request that stops mid-phrase is never acted on, by either mind;
- a clock time without AM or PM means the next one from now, whichever mind wrote it;
- the fast mind may act only on what the user's words plainly name, with values from those words.
"""

import asyncio
import datetime as dt
import json

from duet_voice.coordinator import Coordinator
from duet_voice.galaxy.fast import vet, vet_plan
from duet_voice.galaxy.modes import MODES
from duet_voice.galaxy.tools import PhoneToolbox, cut_off, next_occurrence


def test_a_request_cut_off_mid_phrase_is_recognised():
    assert cut_off("Call my") and cut_off("Set an alarm for") and cut_off("Turn on the.") and cut_off("Take me to")
    assert not cut_off("Turn the lights on.") and not cut_off("Call Priya.") and not cut_off("Set an alarm for 7.")


def test_times_without_am_or_pm_mean_the_next_one():
    late = dt.datetime(2026, 10, 16, 23, 50)
    assert next_occurrence("18:30", "Set an alarm for 6:30.", late) == "06:30"
    assert next_occurrence("06:30", "Set an alarm for 6:30.", late) == "06:30"
    afternoon = dt.datetime(2026, 10, 17, 14, 0)
    assert next_occurrence("07:00", "Set an alarm for 7.", afternoon) == "19:00"
    assert next_occurrence("18:00", "Wake me at 6 in the evening.", late) == "18:00"       # said which half
    assert next_occurrence("06:00", "Wake me at 6 am.", afternoon) == "06:00"
    assert next_occurrence("18:00", "Set an alarm for 18:00.", late) == "18:00"            # 24-hour, said as such
    assert next_occurrence("00:15", "Set an alarm for 12:15.", late) == "00:15"
    assert next_occurrence("23:50", "Set an alarm for 11:50.", late) == "11:50"            # exactly now: tomorrow's next


def make_box(said):
    sent = []

    async def send(tool, args, timeout):
        sent.append((tool, args))
        return json.dumps({"status": "ok"})
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.heard(said)
    coord.turn_committed()
    return PhoneToolbox(coord, MODES["assistant"]["tools"], send), sent


def test_the_toolbox_applies_both_rules():
    box, sent = make_box("Call my")
    assert json.loads(asyncio.run(box.call("call_contact", {"name": "my"})))["status"] == "not_executed"
    assert sent == []
    box, sent = make_box("Set an alarm for 6:30.")
    asyncio.run(box.call("set_alarm", {"time": "18:30"}))
    hour = int(sent[0][1]["time"][:2])
    assert sent[0][1]["time"] in ("06:30", "18:30") and (hour == 6) == (dt.datetime.now().hour >= 18 or dt.datetime.now().hour < 6)


def test_the_fast_mind_acts_only_on_what_the_words_name():
    assert vet("set_torch", {"on": True}, "turn on the torch") == ""
    assert vet("set_alarm", {"time": "07:00"}, "set an alarm for 6:30, no wait, make it 7") == ""
    assert vet("set_alarm", {"time": "06:30"}, "wake me up at half past six") == ""
    assert vet("set_timer", {"minutes": 15}, "set a timer for 10 minutes. i mean 15") == ""
    assert vet("call_contact", {"name": "Priya"}, "call priya") == ""
    assert vet("call_contact", {"name": "my daughter"}, "call my daughter") == ""
    # what Gemma 4 E2B did on 16 Oct 2026
    assert vet("open_app", {"name": "apps"}, "how many shopping apps do i have?")
    assert vet("set_alarm", {"time": "04:00"}, "add a dentist appointment on monday at 4")
    assert vet("set_alarm", {"time": "23:50"}, "turn on the torch")
    # values the user did not say
    assert vet("call_contact", {"name": "Rahul"}, "call priya")
    assert vet("set_brightness", {"percent": 50}, "make the screen brighter")
    assert vet("set_destination", {"place": "the airport"}, "take me to the office")
    assert vet("get_weather", {"place": "Chennai"}, "weather")                     # not a fast action at all


def test_the_last_value_the_user_said_must_be_in_the_fast_plan():
    live = "set an alarm for 3.30. no, no, cancel that. could you set it for 430 instead?"
    assert vet_plan([("cancel_alarm", {"time": "03:30"})], live)            # what the fast mind did, live
    assert vet_plan([("set_alarm", {"time": "04:30"})], live) == ""
    assert vet_plan([("set_alarm", {"time": "06:30"})], "wake me up at 630") == ""
    assert vet_plan([("send_message", {"to": "Rahul", "text": "I'll be home by eight."})],
                    "send a message to rahul saying i'll be home by eight") == ""
    assert vet_plan([("set_alarm", {"time": "06:30"}), ("set_timer", {"minutes": 20})],
                    "set an alarm for 6:30 and a timer for 20 minutes") == ""
    assert vet_plan([("set_torch", {"on": True})], "turn on the torch") == ""
    assert vet_plan([("set_alarm", {"time": "06:15"})], "wake me up at quarter past six") == ""
    assert vet_plan([("set_alarm", {"time": "07:45"})], "set an alarm for 7.15, no, 7.45") == ""
    assert vet_plan([("set_alarm", {"time": "07:15"})], "set an alarm for 7.15, no, 7.45")   # the stale value
    assert vet_plan([("set_car_climate", {"temperature": 22})], "set the ac to 22. no, 21.")


def test_a_question_is_never_a_fast_action():
    from duet_voice.galaxy.fast import asks_question
    assert vet_plan([("control_home_device", {"device": "AC", "action": "set", "value": "on"})], "Is the AC on at home?")
    assert asks_question("What alarms do I have now?") and asks_question("is the door locked")
    assert asks_question("Set an alarm for 7. Is that early enough?")
    assert not asks_question("Could you set it for 430 instead?") and not asks_question("Can you turn on the torch?")
    assert not asks_question("Please call Priya.") and not asks_question("Turn on the AC.")
    # a polite request is not a question, but it must still name its action ("alarm") to stay fast
    assert vet_plan([("set_alarm", {"time": "04:30"})], "Could you set an alarm for 430 instead?") == ""
