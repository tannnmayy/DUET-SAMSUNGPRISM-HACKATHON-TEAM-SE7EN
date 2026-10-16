"""DUET for Galaxy: when the user has not decided, nothing is done; DUET asks.

"Call Rahul, or maybe Priya, I'm not sure" must never call anyone, whatever the model decides
(on 16 Oct 2026 the cloud model, with minimal thinking, called both). Only the user's latest
sentence counts, so an answer ("Priya.") is acted on; questions about why or how are not
undecided requests; readings are always allowed.
"""

import asyncio
import json

from duet_voice.coordinator import Coordinator
from duet_voice.galaxy.modes import MODES
from duet_voice.galaxy.tools import PhoneToolbox, undecided


def test_open_choices_are_recognised_and_decided_ones_are_not():
    assert undecided("Call Rahul or maybe Priya, I'm not sure.")
    assert undecided("Wake me up at 6, 30 tomorrow or maybe 7 home.")      # as the recogniser heard "hmm"
    assert undecided("Set it for either six or seven.")
    assert undecided("I can't decide, the office or the airport")
    assert undecided("Turn on the AC, hmm")
    assert not undecided("Call Rahul or maybe Priya, I'm not sure. Priya.")   # answered in the last sentence
    assert not undecided("Seven.")
    assert not undecided("I'm not sure why my battery dies so fast. What's going on?")
    assert not undecided("Set an alarm for 7.")
    assert not undecided("")


def make_box(said):
    sent = []

    async def send(tool, args, timeout):
        sent.append(tool)
        return json.dumps({"status": "ok"})
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.heard(said)
    coord.turn_committed()
    events = []
    return PhoneToolbox(coord, MODES["assistant"]["tools"], send, emit=events.append), sent, events


def test_an_undecided_user_gets_a_question_not_an_action():
    box, sent, events = make_box("Call Rahul or maybe Priya, I'm not sure.")
    out = json.loads(asyncio.run(box.call("call_contact", {"name": "Rahul"})))
    assert out["status"] == "not_executed" and sent == []
    assert events[-1]["kind"] == "tool_asked"
    # reading something is harmless and still allowed
    assert json.loads(asyncio.run(box.call("list_alarms", {})))["status"] == "ok" and sent == ["list_alarms"]


def test_the_answer_is_acted_on():
    box, sent, _ = make_box("Call Rahul or maybe Priya, I'm not sure. Priya.")
    assert json.loads(asyncio.run(box.call("call_contact", {"name": "Priya"})))["status"] == "ok"
    assert sent == ["call_contact"]
