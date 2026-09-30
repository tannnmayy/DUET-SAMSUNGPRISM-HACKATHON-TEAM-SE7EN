"""The local-model backend (the phone app's laptop mode: an OpenAI-compatible server),
with the server replaced by scripted replies."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from duet_voice import llm_local
from duet_voice.coordinator import Coordinator, Superseded
from duet_voice.llm_local import LocalThinker, salvage_calls



def reply(content=None, calls=()):
    tool_calls = [NS(id="call_%d" % i, function=NS(name=name, arguments=json.dumps(args) if isinstance(args, dict) else args))
                  for i, (name, args) in enumerate(calls)]
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls or None))],
              usage=NS(prompt_tokens=100, completion_tokens=10))


class Box:
    def __init__(self):
        self.coord = Coordinator(commit_hold_s=0.0)
        self.coord.turn_committed()
        self.calls = []
        self.on_call = None

    async def call(self, tool, args, epoch=None):
        self.calls.append((tool, args))
        if self.on_call:
            self.on_call()
        return json.dumps({"status": "success", "tool": tool})


def run(script, box=None, **kw):
    th = LocalThinker(model="qwen-test")
    sent = []

    async def fake_create(messages, tools):
        sent.append(([dict(m) for m in messages], [t["function"]["name"] for t in tools]))
        return script.pop(0)
    th._create = fake_create
    box = box or Box()

    async def go():
        return [ev async for ev in th.run("track order XK42Q7", box, **kw)]
    return asyncio.run(go()), box, sent, th


def test_a_tool_call_then_an_answer_keeps_a_well_formed_history():
    evs, box, sent, th = run([reply(calls=[("track_order", {"order_id": "XK42Q7"})]),
                              reply("It is out for delivery.")])
    assert box.calls == [("track_order", {"order_id": "XK42Q7"})]
    assert [e.kind for e in evs] == ["decided", "tool_start", "tool_done", "say"]
    assert evs[-1].text == "It is out for delivery." and evs[-1].usage["calls"] == 2
    roles = [m["role"] for m in th.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert th.history[1]["tool_calls"][0]["function"]["name"] == "track_order"
    assert th.history[2]["tool_call_id"] == th.history[1]["tool_calls"][0]["id"]
    # the first request offered keep_listening; the second did not
    assert "keep_listening" in sent[0][1] and "keep_listening" not in sent[1][1]


def test_keep_listening_alone_acts_on_nothing_and_remembers_nothing():
    evs, box, sent, th = run([reply(calls=[("keep_listening", {"reason": "the id is still coming"})])])
    assert [e.kind for e in evs] == ["listen"] and evs[0].text == "the id is still coming"
    assert box.calls == [] and th.history == []


def test_keep_listening_next_to_real_work_is_answered_too():
    evs, box, sent, th = run([reply(calls=[("keep_listening", {"reason": "x"}), ("track_order", {"order_id": "A1"})]),
                              reply("Done.")])
    assert box.calls == [("track_order", {"order_id": "A1"})]
    tool_msgs = [m for m in th.history if m["role"] == "tool"]
    assert len(tool_msgs) == 2  # every call gets a response


def test_a_tool_call_left_in_the_text_is_recovered():
    raw = 'Sure.<tool_call>\n{"name": "track_order", "arguments": {"order_id": "B7"}}\n</tool_call>'
    evs, box, sent, th = run([reply(raw), reply("Out for delivery.")])
    assert box.calls == [("track_order", {"order_id": "B7"})]
    calls, rest = salvage_calls(raw)
    assert calls[0]["name"] == "track_order" and rest == "Sure."


def test_arguments_that_are_not_json_are_refused_not_executed():
    evs, box, sent, th = run([reply(calls=[("track_order", "{order_id: B7")]),
                              reply(calls=[("track_order", {"order_id": "B7"})]),
                              reply("Out for delivery.")])
    assert box.calls == [("track_order", {"order_id": "B7"})]
    assert "not a valid JSON object" in [m for m in th.history if m["role"] == "tool"][0]["content"]


def test_an_empty_reply_is_asked_again_once():
    evs, box, sent, th = run([reply(""), reply("Hello!")])
    assert evs[-1].text == "Hello!" and len(sent) == 2


def test_a_plan_overtaken_during_a_tool_call_stops_at_once():
    box = Box()
    def says_more():                                # the user speaks while the call runs
        box.coord.user_started_speaking()
        box.coord.heard("wait, not that one")
    box.on_call = says_more
    with pytest.raises(Superseded):
        run([reply(calls=[("track_order", {"order_id": "A1"})]), reply("never asked")], box=box)


def test_the_talker_on_the_local_model(monkeypatch):
    class Completions:
        async def create(self, **kw):
            assert kw["messages"][0]["role"] == "system" and kw["max_tokens"] <= 64
            return reply("Sure, checking that now.")
    monkeypatch.setattr(llm_local, "client", lambda: NS(chat=NS(completions=Completions())))
    usage = {}
    assert asyncio.run(llm_local.acknowledge("where is my order", usage)) == "Sure, checking that now."
    assert usage["calls"] == 1


def test_sampling_follows_the_model_card_with_a_fixed_seed(monkeypatch):
    import dataclasses
    from duet_voice import config
    s = llm_local.sampling()
    assert (s["temperature"], s["top_p"], s["extra_body"]["top_k"], s["seed"]) == (0.7, 0.8, 20, 7)
    monkeypatch.setattr(llm_local, "CONFIG", dataclasses.replace(config.CONFIG, temperature="0"))
    assert llm_local.sampling()["temperature"] == 0.0


# --- the model-server launcher ------------------------------------------------------------------

def run_reviewed(script):
    """The phone app's thinker: a second look before its answer is spoken."""
    th = LocalThinker(model="qwen-test", review="Second look: anything missing? Else reply OK")
    th._create = lambda messages, tools: asyncio.sleep(0, result=script.pop(0))
    box = Box()

    async def go():
        return [ev async for ev in th.run("set an alarm for 4:30", box)]
    return asyncio.run(go()), box, th


def test_a_second_look_that_finds_nothing_keeps_the_draft_and_a_clean_history():
    evs, box, th = run_reviewed([reply("Set for 4:30."), reply("OK")])
    assert [e.kind for e in evs] == ["decided", "review", "say"] and evs[-1].text == "Set for 4:30."
    assert [m["role"] for m in th.history] == ["user", "assistant"]


def test_a_second_look_calls_the_action_the_draft_only_claimed():
    evs, box, th = run_reviewed([reply("Set for 4:30."), reply(calls=[("set_alarm", {"time": "04:30"})]),
                                 reply("Set for 4:30 in the morning.\n\nOK")])
    assert box.calls == [("set_alarm", {"time": "04:30"})]
    assert evs[-1].kind == "say" and evs[-1].text == "Set for 4:30 in the morning."


def test_the_benchmark_thinker_takes_no_second_look():
    evs, box, sent, th = run([reply("Hello!")])
    assert [e.kind for e in evs] == ["decided", "say"] and th.review is None


def test_a_second_look_whose_action_was_held_back_keeps_the_draft():
    th = LocalThinker(model="qwen-test", review="Second look")
    script = [reply("It's the screen. Want me to dim it?"), reply(calls=[("set_brightness", {"percent": 50})]),
              reply("I'll dim it to 50%, okay?")]
    th._create = lambda messages, tools: asyncio.sleep(0, result=script.pop(0))

    class Held(Box):
        async def call(self, tool, args, epoch=None):
            self.calls.append((tool, args))
            return json.dumps({"status": "not_executed", "reason": "the user has not asked for this"})
    box = Held()

    async def go():
        return [ev async for ev in th.run("why does my battery drain", box)]
    evs = asyncio.run(go())
    assert evs[-1].text == "It's the screen. Want me to dim it?" and box.calls == [("set_brightness", {"percent": 50})]
