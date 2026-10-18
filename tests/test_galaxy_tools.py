"""DUET for Galaxy: the phone bridge keeps the coordinator's promises.

- an action runs once per request; a repeatable one may run again in a later request;
- a correction that cancels something lets the same thing be done again later;
- a failure the phone reports (a missing permission) is passed on and not remembered;
- a plan the user's words overtook never reaches the phone;
- the benchmark's tools and instructions are untouched by the phone app's modes.
"""

import asyncio
import json

from duet_voice.coordinator import Coordinator
from duet_voice.galaxy.modes import MODES, llm_specs
from duet_voice.galaxy.tools import PhoneToolbox


def make(replies=None):
    sent = []

    async def send(tool, args, timeout):
        sent.append((tool, args))
        reply = (replies or {}).get(tool, {"status": "ok"})
        return json.dumps(reply)
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.turn_committed()
    events = []
    box = PhoneToolbox(coord, MODES["assistant"]["tools"] + MODES["care"]["tools"], send, emit=events.append)
    return box, sent, events


def call(box, tool, args):
    return json.loads(asyncio.run(box.call(tool, args)))


def test_the_same_alarm_is_set_once_even_if_the_plan_is_made_again():
    box, sent, events = make()
    assert call(box, "set_alarm", {"time": "16:30"})["status"] == "ok"
    assert call(box, "set_alarm", {"time": "16:30"})["status"] == "already_done"
    assert sent == [("set_alarm", {"time": "16:30"})]
    assert [e["kind"] for e in events] == ["tool_planned", "tool_done", "tool_planned", "tool_cached"]


def test_cancelling_an_alarm_lets_it_be_set_again():
    box, sent, _ = make()
    call(box, "set_alarm", {"time": "07:00"})
    call(box, "cancel_alarm", {"time": "07:00"})
    assert call(box, "set_alarm", {"time": "07:00"})["status"] == "ok"
    assert [t for t, _ in sent] == ["set_alarm", "cancel_alarm", "set_alarm"]


def test_repeatable_actions_run_again_in_a_new_request_but_not_within_one():
    box, sent, _ = make()
    call(box, "call_contact", {"name": "Priya"})
    assert call(box, "call_contact", {"name": "Priya"})["status"] == "already_done"
    box.new_request()                       # DUET answered; "call her again" is a new request
    assert call(box, "call_contact", {"name": "Priya"})["status"] == "ok"
    call(box, "log_medication", {"medicine": "Amlodipine"})
    box.new_request()                       # a dose stays logged once for the whole conversation
    assert call(box, "log_medication", {"medicine": "Amlodipine"})["status"] == "already_done"
    assert [t for t, _ in sent] == ["call_contact", "call_contact", "log_medication"]


def test_a_missing_permission_is_passed_on_and_not_remembered():
    need = {"status": "needs_permission", "permission": "modify_system_settings", "instruction": "allow it"}
    box, sent, events = make({"set_brightness": need})
    assert call(box, "set_brightness", {"percent": 40}) == need
    assert call(box, "set_brightness", {"percent": 40}) == need     # tried again, not "already done"
    assert len(sent) == 2 and events[1]["kind"] == "tool_failed"


def test_a_plan_the_user_overtook_never_reaches_the_phone():
    box, sent, events = make()
    stale = box.coord.epoch
    box.coord.user_started_speaking()
    box.coord.heard("no no, cancel that")
    out = json.loads(asyncio.run(box.call("set_alarm", {"time": "15:30"}, epoch=stale)))
    assert out["status"] == "not_executed" and sent == []
    assert events[-1]["kind"] == "tool_dropped"


def test_arguments_are_coerced_to_the_schema():
    box, sent, _ = make()
    call(box, "set_brightness", {"percent": "40", "adaptive": None})
    assert sent == [("set_brightness", {"percent": 40})]


def test_every_mode_has_valid_tools_and_the_benchmark_is_untouched():
    from duet_voice import fdb_tools, prompts
    for m in MODES.values():
        names = [t["name"] for t in m["tools"]]
        assert len(names) == len(set(names))
        for spec in llm_specs(m["tools"]):
            assert spec["parameters"]["type"] == "object" and spec["description"]
            assert set(spec["parameters"]["required"]) <= set(spec["parameters"]["properties"])
        assert "benchmark" not in m["thinker"].lower()
    assert len(fdb_tools.TOOL_SPECS) == 12
    assert "flights and identity documents" in prompts.THINKER_INSTRUCTIONS
    assert not {t["name"] for m in MODES.values() for t in m["tools"]} & {s["name"] for s in fdb_tools.TOOL_SPECS}


def test_the_consent_gate_holds_back_a_change_nobody_asked_for():
    box, sent, events = make()
    asked = []

    async def consent(tool, args):
        asked.append(tool)
        return tool != "set_brightness"      # the user only asked what drains the battery
    box.consent = consent
    out = call(box, "set_brightness", {"percent": 50})
    assert out["status"] == "not_executed" and sent == []
    assert events[-1]["kind"] == "tool_asked"
    call(box, "set_alarm", {"time": "07:00"})  # an alarm is what they asked for: no check
    assert asked == ["set_brightness"] and sent == [("set_alarm", {"time": "07:00"})]


def test_a_failed_consent_check_does_not_block_the_user():
    box, sent, _ = make()

    async def broken(tool, args):
        raise RuntimeError("model server down")
    box.consent = broken
    assert call(box, "send_message", {"to": "Priya", "text": "running late"})["status"] == "ok"


def test_a_held_back_call_is_remembered_for_the_request_only():
    box, sent, _ = make()
    stale = box.coord.epoch
    box.coord.user_started_speaking()
    box.coord.heard("actually, no")
    asyncio.run(box.call("set_alarm", {"time": "15:30"}, epoch=stale))
    assert box.held_back == [{"tool": "set_alarm", "args": {"time": "15:30"}}]
    box.new_request()
    assert box.held_back == []


def test_an_action_the_phone_never_confirmed_stays_unconfirmed_when_tried_again():
    """17 Oct 2026: the phone did not answer a timer in time; the cloud mind tried it again, was told
    already_done, and said the timer was set. A second try now hears unknown_outcome again."""
    sent = []

    async def silent_phone(tool, args, timeout):
        sent.append(tool)
        await asyncio.sleep(5)
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.turn_committed()
    events = []
    box = PhoneToolbox(coord, MODES["assistant"]["tools"], silent_phone, emit=events.append)
    box.specs["set_timer"] = dict(box.specs["set_timer"], timeout=0.2)
    first = json.loads(asyncio.run(box.call("set_timer", {"minutes": 3})))
    again = json.loads(asyncio.run(box.call("set_timer", {"minutes": 3})))
    assert first["status"] == again["status"] == "unknown_outcome" and sent == ["set_timer"]
    assert "do not retry" in again["instruction"] and box.last_done is None
    assert [e["kind"] for e in events if e["kind"] != "tool_planned"] == ["tool_unknown", "tool_unknown"]


def test_a_call_refused_by_the_consent_check_is_not_retried_in_a_loop():
    asked = []

    async def consent(tool, args):
        asked.append(tool)
        return False
    box, sent, events = make()
    box.consent = consent
    first = call(box, "call_contact", {"name": "Priya"})
    again = call(box, "call_contact", {"name": "Priya"})
    assert first["status"] == again["status"] == "not_executed" and sent == [] and asked == ["call_contact"]
    assert "STOP calling tools" in again["reason"]
    box.new_request()                            # a new request may ask again
    call(box, "call_contact", {"name": "Priya"})
    assert asked == ["call_contact", "call_contact"]


def test_a_time_has_one_spelling_so_cancel_and_set_again_work():
    """17 Oct 2026: cancel_alarm("7:00") did not clear set_alarm("07:00") in the ledger."""
    box, sent, _ = make()
    call(box, "set_alarm", {"time": "07:00"})
    call(box, "cancel_alarm", {"time": "7:00"})
    assert call(box, "set_alarm", {"time": "7.00"})["status"] == "ok"           # set again, really
    assert sent == [("set_alarm", {"time": "07:00"}), ("cancel_alarm", {"time": "07:00"}), ("set_alarm", {"time": "07:00"})]


def test_a_call_the_guards_held_back_is_remembered_until_the_request_is_answered():
    """If the model then says nothing, the agent asks for the missing part itself (18 Oct 2026)."""
    box, sent, _ = make()
    box.coord.heard("Call my")
    box.coord.turn_committed()
    assert call(box, "call_contact", {"name": "my"})["status"] == "not_executed" and sent == []
    assert box.unfinished == ("call_contact", "cut_off")
    box.new_request()
    assert box.unfinished is None


def test_times_in_a_text_message_are_sent_in_digits():
    """18 Oct 2026, Drive mode: the arrival time went out as "about half past three"."""
    from duet_voice.galaxy.tools import message_times
    assert message_times("I'll be there at about half past three.") == "I'll be there at about 3:30."
    assert message_times("See you at quarter to five") == "See you at 4:45"
    assert message_times("quarter to one") == "12:45"
    assert message_times("Meet at six o'clock") == "Meet at 6:00"
    assert message_times("Quarter past 12 works") == "12:15 works"
    for same in ("half past caring", "around 3:25 AM", "I'll be 10 minutes late", "half past 13"):
        assert message_times(same) == same
    box, sent, _ = make()
    call(box, "send_message", {"to": "Priya", "text": "On my way, there at half past three."})
    assert sent == [("send_message", {"to": "Priya", "text": "On my way, there at 3:30."})]


def test_a_reading_is_fresh_in_every_request():
    """18 Oct 2026: a battery question asked a minute after another got the first one's reading."""
    box, sent, events = make({"get_battery_report": {"status": "ok", "level": 80}})
    call(box, "get_battery_report", {})
    assert call(box, "get_battery_report", {})["status"] == "already_done"      # within one request: read once
    box.new_request()
    assert call(box, "get_battery_report", {})["status"] == "ok"                # a new request: read again
    assert sent == [("get_battery_report", {}), ("get_battery_report", {})]
