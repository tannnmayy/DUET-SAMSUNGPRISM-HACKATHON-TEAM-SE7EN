"""The dual-mind speaking rules, with the models replaced by fakes (no key needed).

- work to do: the talker's acknowledgement is spoken first, then the answer;
- no tools (a greeting, a question): one answer, no acknowledgement;
- a noise-only turn: nothing is spoken;
- the thinker fails: the user still hears something.
"""

import asyncio
import os

os.environ.setdefault("DUET_ACK_GRACE", "0.3")

from livekit.agents import llm  # noqa: E402
from livekit.agents.types import FlushSentinel  # noqa: E402

from duet_voice import agent as agent_mod  # noqa: E402
from duet_voice.coordinator import Coordinator  # noqa: E402
from duet_voice.thinker import ThinkEvent  # noqa: E402


class FakeThinker:
    def __init__(self, events, delay=0.0):
        self.events, self.delay, self.history, self.model = events, delay, [], "fake"

    async def run(self, text, toolbox, audio=None, note=""):
        for ev in self.events:
            await asyncio.sleep(self.delay)
            if isinstance(ev, Exception):
                raise ev
            yield ev


def make_agent(events, ack="Sure, checking that now.", delay=0.0):
    coord = Coordinator(commit_hold_s=0.0)
    a = agent_mod.DuetAgent(agent_mod.Trace("test"), coord, toolbox=None)
    a._thinker = FakeThinker(events, delay)

    async def fake_ack(text, context=""):
        await asyncio.sleep(0.05)
        return ack
    agent_mod.talker.acknowledgement = fake_ack
    return a


def speak(a, user_text):
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="user", content=user_text)

    async def go():
        out = []
        async for chunk in a.llm_node(ctx, [], None):
            if not isinstance(chunk, FlushSentinel):
                out.append(chunk.strip() if isinstance(chunk, str) else chunk)
        return [c for c in out if c]
    return asyncio.run(go())


def test_work_gets_an_acknowledgement_then_the_answer():
    a = make_agent([ThinkEvent("decided", tools=True),
                    ThinkEvent("tool_start", name="track_order", args={"order_id": "X1"}),
                    ThinkEvent("tool_done", name="track_order", outcome="success"),
                    ThinkEvent("say", text="Order X1 is out for delivery.")], delay=0.05)
    said = speak(a, "where is my order X1")
    assert said == ["Sure, checking that now.", "Order X1 is out for delivery."]


def test_no_tools_means_one_answer_and_no_acknowledgement():
    a = make_agent([ThinkEvent("decided", tools=False), ThinkEvent("say", text="Hi! How can I help?")])
    assert speak(a, "hello there") == ["Hi! How can I help?"]


def test_noise_turn_is_silent():
    a = make_agent([ThinkEvent("decided", tools=False), ThinkEvent("say", text="<silent>")], ack=None)
    assert speak(a, "mm") == []


def test_a_thinker_failure_is_never_silent():
    a = make_agent([ThinkEvent("error", text="quota exceeded")], ack=None)
    said = speak(a, "book it")
    assert said and "wrong" in said[-1].lower()


def test_slow_thinker_gets_the_acknowledgement_after_the_grace_period():
    a = make_agent([ThinkEvent("decided", tools=True), ThinkEvent("say", text="Done: it's booked.")],
                   delay=0.6)
    said = speak(a, "book the flight")
    assert said[0] == "Sure, checking that now." and said[-1] == "Done: it's booked."
