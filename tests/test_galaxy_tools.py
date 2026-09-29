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
    box.coord.user_started_speaking()       # "no no, cancel that ..."
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
