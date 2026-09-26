"""The thinker's own tool loop, with Gemini replaced by scripted responses (no key needed)."""

import asyncio
import json

from google.genai import types

from duet_voice.coordinator import Coordinator
from duet_voice.thinker import Thinker


def reply(*parts, finish="STOP"):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=list(parts)) if parts else None,
        finish_reason=finish)])


def call(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


class FakeToolbox:
    def __init__(self):
        self.coord = Coordinator(commit_hold_s=0.0)
        self.coord.turn_committed()
        self.calls = []

    async def call(self, tool, args, epoch=None):
        self.calls.append((tool, args))
        return json.dumps({"status": "success", "tool": tool})


def run(script, **kw):
    th = Thinker(model="gemini-2.5-flash", thinking="low")
    sent = []

    async def fake_generate(contents, config):
        sent.append(config)
        return script.pop(0)
    th._generate = fake_generate
    box = FakeToolbox()

    async def go():
        return [ev async for ev in th.run("track order XK42Q7", box, **kw)]
    return asyncio.run(go()), box, sent, th


def test_an_empty_reply_is_asked_again_once():
    evs, box, sent, _ = run([reply(finish="MALFORMED_FUNCTION_CALL"),
                             reply(call("track_order", order_id="XK42Q7")),
                             reply(types.Part(text="It is out for delivery."))])
    assert box.calls == [("track_order", {"order_id": "XK42Q7"})]
    assert evs[-1].kind == "say" and evs[-1].text == "It is out for delivery."
    assert evs[-1].usage["calls"] == 3


def test_a_second_empty_reply_is_not_retried_forever():
    evs, box, sent, _ = run([reply(), reply()])
    assert box.calls == [] and len(sent) == 2
    assert evs[-1].kind == "say" and evs[-1].text == ""


def test_keep_listening_acts_on_nothing_and_remembers_nothing():
    evs, box, sent, th = run([reply(call("keep_listening", reason="the order id is still coming"))])
    assert [e.kind for e in evs] == ["listen"] and box.calls == [] and th.history == []


def test_the_second_look_cannot_keep_listening():
    evs, box, sent, _ = run([reply(types.Part(text="What is the order number?"))], allow_listen=False)
    names = [d.name for t in sent[0].tools for d in t.function_declarations]
    assert "keep_listening" not in names and "track_order" in names
    assert evs[-1].text == "What is the order number?"


def test_a_completed_exchange_is_kept_as_text_only():
    evs, box, sent, th = run([reply(call("track_order", order_id="XK42Q7")),
                              reply(types.Part(text="Out for delivery."))])
    # user turn, model call, tool result, model answer
    assert len(th.history) == 4 and th.history[0].parts[0].text == "track order XK42Q7"


def test_a_keep_listening_next_to_real_work_is_answered_not_deleted():
    """Gemini 3 signs function-call parts; the model's turn must go back unchanged,
    and every call in it (keep_listening included) gets a response."""
    signed = types.Part(function_call=types.FunctionCall(name="keep_listening", args={"reason": "x"}),
                        thought_signature=b"sig")
    evs, box, sent, th = run([reply(signed, call("track_order", order_id="XK42Q7")),
                              reply(types.Part(text="Out for delivery."))])
    assert box.calls == [("track_order", {"order_id": "XK42Q7"})]
    model_turn, tool_turn = th.history[1], th.history[2]
    assert model_turn.parts[0].thought_signature == b"sig" and len(model_turn.parts) == 2
    assert [p.function_response.name for p in tool_turn.parts] == ["keep_listening", "track_order"]


def test_sampling_follows_the_model_family(monkeypatch):
    import dataclasses
    from duet_voice import config, gemini
    assert gemini.sampling("gemini-2.5-flash") == {"temperature": 0.0, "seed": 7}
    assert gemini.sampling("gemini-3.5-flash") == {"temperature": 1.0, "seed": 7}
    monkeypatch.setattr(config, "CONFIG", dataclasses.replace(config.CONFIG, temperature="0.3"))
    assert gemini.sampling("gemini-3.5-flash")["temperature"] == 0.3


def test_thinking_is_a_budget_on_2_5_and_a_level_on_3():
    from duet_voice.gemini import thinking_config
    assert thinking_config("gemini-2.5-flash", "low").thinking_budget == 512
    assert thinking_config("gemini-2.5-flash", "minimal").thinking_budget == 0
    assert str(thinking_config("gemini-3.5-flash", "low").thinking_level).lower().endswith("low")
    assert str(thinking_config("gemini-3.5-flash-lite", "minimal").thinking_level).lower().endswith("minimal")
