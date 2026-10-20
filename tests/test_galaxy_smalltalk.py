"""The fast mind answers small talk itself, in context; everything else still goes where it went.

20 Oct 2026, the S24: "Good night", "Good morning", "How are you?" and "Thank you" each got "One moment." and
then silence. Now:
- small talk is answered by the fast mind at once, if a deterministic check agrees (no action named, no number,
  no health words, no question for information, DUET not waiting for an answer; a reply with no number, no claim,
  no tool name);
- anything that fails the check goes to the cloud mind, with what the fast mind would have said kept for later;
- the fast mind sees the conversation so far as chat turns; acting still depends on the current words alone;
- "One moment." waits while the fast mind decides, so it never comes before "Good morning!".
"""

import asyncio
import json
import time
from types import SimpleNamespace as NS

import pytest

from duet_voice import agent as base
from duet_voice import llm_local
from duet_voice.coordinator import Coordinator, Superseded
from duet_voice.galaxy import fast
from duet_voice.galaxy.modes import MODES
from duet_voice.galaxy.tools import PhoneToolbox, enum_value
from duet_voice.llm_local import LocalThinker
from duet_voice.thinker import ThinkEvent

ASSISTANT = fast.tool_names(fast.make_fast_thinker(MODES["assistant"]["tools"], "Assistant"))


# --- the rules --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("words", [
    "Good morning.", "Good night.", "Hi DUET, how are you doing today?", "How's it going?", "Okay.", "Thank you.",
    "Thanks a lot, that's really helpful.", "I am not good.", "I'm so tired today.", "What can you do?",
    "Who are you?", "Nothing much, I just want to rest today.", "Bye, talk to you later.",
    "I'm doing well, how about you?"])
def test_small_talk_may_be_answered_by_the_fast_mind(words):
    assert fast.not_small_talk(words, ASSISTANT) == ""


@pytest.mark.parametrize("words, why", [
    ("What do I do? I have a stomach pain.", "health"),
    ("Good morning. I have a headache today.", "health"),
    ("I fell down.", "health"),
    ("Can you check when is the next Apple event?", ""),
    ("When was the latest Samsung phone launched?", ""),
    ("Is it going to rain in Chennai today?", ""),
    ("How are you going to fix this?", "information"),
    ("Can you check the news and you tell me", ""),
    ("Thanks. Now turn on the torch.", "set_torch"),
    ("Good morning, set an alarm for 7.", ""),
    ("Yes.", "yes or no"),
    ("Sure, go ahead.", "yes or no"),
    ("4:30.", "number"),
    ("Turn on the", "mid-phrase"),
    ("Call my", ""),
])
def test_anything_else_goes_to_the_cloud_mind(words, why):
    reason = fast.not_small_talk(words, ASSISTANT)
    assert reason != "" and why in reason


def test_a_reply_is_spoken_only_if_it_says_no_number_claims_nothing_and_names_no_tool():
    ok = lambda reply, **kw: fast.chat_ok("Good morning.", reply, ASSISTANT, **kw)   # noqa: E731
    assert ok("Good morning, Tanmay! Any plans today?") == ""
    assert ok("Chat reply: Good morning! How are you?") == ""               # the label is not the reply
    assert "number" in ok("Good morning! It's 7 degrees outside.")
    assert "done" in ok("Good morning! I've set your alarm.")
    assert "tool" in ok("keep_listening")
    assert ok("<silent>") and ok("")
    assert "too long" in ok(" ".join(["word"] * 45))
    assert "answering" in ok("Good morning!", awaiting=True)
    assert fast.clean_reply('Chat reply: "Good night! Sleep well."') == "Good night! Sleep well."


def test_duet_waiting_for_an_answer_means_the_next_words_are_not_small_talk():
    assert fast.awaiting_answer("Should I go ahead and set the brightness to 40 percent?")
    assert fast.awaiting_answer("Would you like me to call your daughter?")
    assert fast.awaiting_answer("3:30 or 4:30?")
    assert not fast.awaiting_answer("I'm doing well, thanks! How about you?")
    assert not fast.awaiting_answer("Alarm set for 7 in the morning.")


def test_the_conversation_reaches_the_fast_mind_as_chat_turns():
    recent = [("assistant", "Hi, I'm DUET."), ("assistant", "What can I do?"), ("user", "How are you?"),
              ("assistant", "Great, thanks! And you?"), ("system", "ignored"), ("user", "  ")]
    assert fast.chat_turns(recent) == [
        {"role": "assistant", "content": "Hi, I'm DUET. What can I do?"},     # one speaker's lines joined
        {"role": "user", "content": "How are you?"},
        {"role": "assistant", "content": "Great, thanks! And you?"}]
    long = [("user" if i % 2 else "assistant", "line %d" % i) for i in range(40)]
    assert len(fast.chat_turns(long)) == fast.RECENT_TURNS


# --- the hybrid thinker -------------------------------------------------------------------------------------

class Script:
    def __init__(self, model, script):
        self.model, self.script, self.history, self.thinking = model, script, [], ""
        self.calls, self.seen_history = 0, []

    async def run(self, text, toolbox, audio=None, note="", allow_listen=True):
        self.calls += 1
        self.seen_history.append(list(self.history))
        for item in self.script:
            if callable(item):
                item()
                continue
            yield item


class Box:
    def __init__(self):
        self.coord = Coordinator()


def collect(hybrid, text, box=None):
    box = box or Box()

    async def go():
        return [ev async for ev in hybrid.run(text, box, note="Now: Tuesday 20 October 2026, 21:28.")]
    return asyncio.run(go())


CLOUD = [ThinkEvent("decided", tools=False), ThinkEvent("say", text="From the cloud.")]


def test_small_talk_is_answered_by_the_fast_mind_and_the_cloud_mind_hears_of_it():
    seen = []
    fast_mind = Script("e2b", [ThinkEvent("chat", text="Good morning, Tanmay! Any plans today?")])
    slow = Script("26b", CLOUD)
    hybrid = fast.HybridThinker(fast_mind, slow, on_route=lambda where, why: seen.append(where))
    events = collect(hybrid, "Good morning.")
    assert [(e.kind, e.text) for e in events] == [("route", "edge"), ("decided", ""),
                                                  ("say", "Good morning, Tanmay! Any plans today?")]
    assert slow.calls == 0 and seen == ["edge"]
    assert 'DUET said: "Good morning, Tanmay! Any plans today?"' in hybrid.edge_log[-1]


def test_a_plain_text_reply_is_small_talk_too_when_the_check_agrees():
    fast_mind = Script("e2b", [ThinkEvent("decided", tools=False), ThinkEvent("say", text="Good night! Sleep well.")])
    slow = Script("26b", CLOUD)
    events = collect(fast.HybridThinker(fast_mind, slow), "Good night.")
    assert [e.text for e in events if e.kind == "say"] == ["Good night! Sleep well."] and slow.calls == 0


def test_small_talk_the_check_refuses_goes_to_the_cloud_mind_with_the_line_kept():
    for words, reply in (("What do I do? I have a stomach pain.", "I'm sorry you're in pain. Rest and drink water."),
                         ("Good morning.", "Good morning! I've set your alarm.")):
        fast_mind = Script("e2b", [ThinkEvent("chat", text=reply)])
        slow = Script("26b", CLOUD)
        hybrid = fast.HybridThinker(fast_mind, slow)
        events = collect(hybrid, words)
        assert [e.text for e in events if e.kind == "say"] == ["From the cloud."] and slow.calls == 1
        assert hybrid.handover == {"say": reply}


def test_an_answer_to_duets_own_question_is_never_small_talk():
    fast_mind = Script("e2b", [ThinkEvent("chat", text="Great!")])
    slow = Script("26b", CLOUD)
    hybrid = fast.HybridThinker(fast_mind, slow)
    hybrid.recent = [("assistant", "Should I go ahead and set the brightness to 40 percent?")]
    events = collect(hybrid, "Okay.")
    assert slow.calls == 1 and "Great!" not in [e.text for e in events]


def test_the_fast_mind_sees_the_conversation_and_its_hand_over_is_kept():
    fast_mind = Script("e2b", [ThinkEvent("escalate", text="a web question", name="ask_expert",
                                          args={"reason": "a web question", "kind": "web",
                                                "say": "Let me check the latest iPhone launch.",
                                                "question": "When was the latest iPhone launched?"})])
    slow = Script("26b", CLOUD)
    hybrid = fast.HybridThinker(fast_mind, slow)
    hybrid.recent = [("user", "Good morning."), ("assistant", "Good morning! Any plans?")]
    collect(hybrid, "When was the latest Samsung phone launched? No, the latest iPhone.")
    assert fast_mind.seen_history[-1] == [{"role": "user", "content": "Good morning."},
                                          {"role": "assistant", "content": "Good morning! Any plans?"}]
    assert hybrid.handover["kind"] == "web" and hybrid.handover["question"] == "When was the latest iPhone launched?"
    assert slow.calls == 1


def test_small_talk_overtaken_by_new_words_is_never_spoken():
    box = Box()

    def more_words():
        box.coord.user_started_speaking()
        box.coord.heard("and set an alarm")
    fast_mind = Script("e2b", [more_words, ThinkEvent("chat", text="Good morning!")])
    hybrid = fast.HybridThinker(fast_mind, Script("26b", CLOUD))
    with pytest.raises(Superseded):
        collect(hybrid, "Good morning.", box)


# --- the local thinker --------------------------------------------------------------------------------------

def reply(content=None, calls=()):
    tool_calls = [NS(id="call_%d" % i, function=NS(name=name, arguments=json.dumps(args)))
                  for i, (name, args) in enumerate(calls)]
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls or None))],
              usage=NS(prompt_tokens=100, completion_tokens=10))


class LocalBox:
    def __init__(self):
        self.coord = Coordinator(commit_hold_s=0.0)
        self.coord.turn_committed()
        self.calls = []

    async def call(self, tool, args, epoch=None):
        self.calls.append((tool, args))
        return json.dumps({"status": "ok"})


def local_run(script, text="Good morning."):
    th = LocalThinker(model="e2b", escalate={"ask_expert"}, one_step=True, reply_tool="chat_reply")

    async def fake_create(messages, tools):
        return script.pop(0)
    th._create = fake_create
    box = LocalBox()

    async def go():
        return [ev async for ev in th.run(text, box)]
    return asyncio.run(go()), box


def test_the_reply_tool_alone_is_the_fast_minds_own_answer():
    evs, box = local_run([reply(calls=[("chat_reply", {"text": "Good morning! Any plans today?"})])])
    assert [(e.kind, e.text) for e in evs] == [("chat", "Good morning! Any plans today?")] and box.calls == []


def test_the_reply_tool_next_to_an_action_is_dropped_and_the_action_runs():
    evs, box = local_run([reply(calls=[("set_torch", {"on": True}), ("chat_reply", {"text": "Sure!"})])],
                         "Thanks. Turn on the torch.")
    assert box.calls == [("set_torch", {"on": True})] and "chat" not in [e.kind for e in evs]


def test_a_hand_over_carries_its_arguments():
    hand = {"reason": "the weather", "kind": "web", "say": "Let me check the weather."}
    evs, _ = local_run([reply(calls=[("ask_expert", hand)])], "Will it rain today?")
    assert [(e.kind, e.name, e.args) for e in evs] == [("escalate", "ask_expert", hand)]


def test_the_warm_up_sends_the_first_looks_instructions_and_tools(monkeypatch):
    sent = []

    async def create(**kw):
        sent.append(kw)
    monkeypatch.setattr(llm_local, "client", lambda base_url=None: NS(chat=NS(completions=NS(create=create))))
    th = fast.make_fast_thinker(MODES["assistant"]["tools"], "Assistant", "Tanmay")
    asyncio.run(fast.warm_up(th))
    assert len(sent) == 1 and sent[0]["max_tokens"] == 1
    assert sent[0]["messages"][0]["content"] == th.instructions and "Tanmay" in th.instructions
    assert {t["function"]["name"] for t in sent[0]["tools"]} >= {"chat_reply", "ask_expert", "keep_listening"}


def test_a_failed_warm_up_is_only_a_slower_first_answer(monkeypatch):
    async def create(**kw):
        raise ConnectionError("the server is not up yet")
    monkeypatch.setattr(llm_local, "client", lambda base_url=None: NS(chat=NS(completions=NS(create=create))))
    asyncio.run(fast.warm_up(fast.make_fast_thinker(MODES["assistant"]["tools"], "Assistant")))


# --- the toolbox's fixed choices ----------------------------------------------------------------------------

def test_a_plain_word_for_a_fixed_choice_is_that_choice():
    """20 Oct 2026: "Skip this song" came as control_media(action="skip"); the phone takes "next"."""
    prop = {"type": "string", "enum": ["play", "pause", "next", "previous"]}
    assert enum_value(prop, "skip") == "next" and enum_value(prop, "Resume") == "play"
    assert enum_value(prop, "pause") == "pause" and enum_value(prop, "rewind") == "rewind"
    assert enum_value({"enum": ["turn_on", "turn_off", "set"]}, "turn on") == "turn_on"
    sent = []

    async def send(tool, args, timeout):
        sent.append((tool, args))
        return json.dumps({"status": "ok"})
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.turn_committed()
    box = PhoneToolbox(coord, MODES["assistant"]["tools"], send)
    asyncio.run(box.call("control_media", {"action": "skip"}))
    assert sent == [("control_media", {"action": "next"})]
    specs = box.specs
    assert fast.vet_plan([("control_media", {"action": "skip"})], "Skip this song.", specs=specs) == ""
    assert "choices" in fast.vet_plan([("control_media", {"action": "rewind"})], "Skip this song.", specs=specs)


# --- the agent ----------------------------------------------------------------------------------------------

def galaxy_agent(monkeypatch):
    monkeypatch.setenv("DUET_FAST", "1")
    from duet_voice.galaxy import agent as gagent

    async def send(tool, args, timeout):
        return json.dumps({"status": "ok"})
    coord = Coordinator(commit_hold_s=0.0)
    box = PhoneToolbox(coord, MODES["assistant"]["tools"], send)
    return gagent, gagent.GalaxyAgent(base.Trace("test"), coord, box, gagent.get_mode("assistant"), "",
                                      user_name="Tanmay")


def test_the_agent_remembers_what_was_really_said_and_shares_it(monkeypatch):
    _, a = galaxy_agent(monkeypatch)
    assert isinstance(a._thinker, fast.HybridThinker) and a._thinker.recent is a.recent
    a.reply_delivered(said="Hi, I'm DUET. What can I do on your phone?")   # the greeting: no request closed
    a._request_text, a._pending_consumed = "Good morning.", 1
    a.reply_delivered(said="Good morning, Tanmay! Any plans today?")
    a.reply_delivered(said="One moment.")                                  # a fixed line says nothing
    a._request_text, a._pending_consumed = "What do I do? I have a stomach pain.", 2
    a.reply_delivered(said="One moment. I'm sorry you're in pain.")
    assert list(a.recent) == [("assistant", "Hi, I'm DUET. What can I do on your phone?"),
                              ("user", "Good morning."), ("assistant", "Good morning, Tanmay! Any plans today?"),
                              ("user", "What do I do? I have a stomach pain."),
                              ("assistant", "I'm sorry you're in pain.")]
    assert "Tanmay" in a._thinker.fast.instructions


def test_one_moment_waits_while_the_fast_mind_decides(monkeypatch):
    gagent, a = galaxy_agent(monkeypatch)
    a._route_now, a._asked_at = None, time.time()
    assert a.ack_line() == ""                       # it may answer by itself in a moment
    a._asked_at = time.time() - gagent.FAST_WAIT_S - 0.1
    assert a.ack_line() == "One moment."            # slow: the wait is covered after all
    a._route_now = "edge"
    assert a.ack_line() == "Sure."
    a._route_now, a._asked_at = "cloud", time.time()
    assert a.ack_line() == "One moment."


def test_a_spoken_answer_starts_with_a_capital_after_its_okay_is_dropped(monkeypatch):
    _, a = galaxy_agent(monkeypatch)
    assert a.polish("Okay, what can I do for you?") == "What can I do for you?"


# --- the cloud mind's instructions (Phase 2) ----------------------------------------------------------------

def test_no_mode_tells_the_cloud_mind_not_to_greet_and_small_talk_is_meant_for_it():
    """20 Oct 2026: told "do not greet them", Gemma 4 26B replied <silent> to "Good morning." 3 times of 3."""
    for key, m in MODES.items():
        text = m["thinker"].lower()
        assert "do not greet" not in text and "already acknowledged" not in text, key
        assert "greet them back" in text and "small talk are meant for you" in text, key
        assert "<silent> only to background talk or noise" in text, key
        assert "follow-up question" in text and "name now and then" in text, key


def test_the_assistant_answers_health_with_care_and_a_question():
    text = MODES["assistant"]["thinker"]
    assert "HEALTH" in text and "never diagnose" in text and "112" in text
    assert "Do not call yourself an AI" in text and "Where exactly is the pain" in text


def test_nothing_ran_so_nothing_was_cancelled(monkeypatch):
    """21 Oct 2026: "Set an alarm for 6 tomorrow morning. Actually, never mind, forget it." was answered "I've
    cancelled that." 3 times of 3, though no alarm was set."""
    from duet_voice.coordinator import CallRecord
    from duet_voice.galaxy.confirm import drop_false_undo
    _, a = galaxy_agent(monkeypatch)
    a._coord.heard("Set an alarm for 6 tomorrow morning. Actually, never mind, forget it.")
    assert a.polish("I've cancelled that.") == "Okay, I'll leave it."
    assert a.polish("Got it. The alarm was cancelled.") == "Got it."
    a._coord.history.append(CallRecord("cancel_alarm", {"time": "06:00"}, 0, 0.0, outcome="ok"))
    assert a.polish("I've cancelled that.") == "I've cancelled that."      # a real cancel: the words stand
    assert drop_false_undo("I couldn't cancel it, sorry.", True) == "I couldn't cancel it, sorry."
    assert drop_false_undo("I've removed it.", False) == ""


def test_the_greetings_use_the_name_and_read_well_without_one():
    for key in ("assistant", "drive"):
        g = MODES[key]["greeting"]
        assert g.format(name="Tanmay").startswith("Hi Tanmay,")
        assert g.format(name="there").startswith("Hi there,")
