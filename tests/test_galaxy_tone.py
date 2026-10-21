"""DUET hears how the user said it (duet_voice/galaxy/tone.py), and every part that reads the words reads it too.

- Measuring: brightness, pitch and level of a segment, against the speaker's own earlier segments.
- Reading a request: urgent, distressed and frustrated each need a cue from the voice and one from the words; unsure
  needs a hedge next to the value, or a long pause before it.
- Using it: the coordinator's hold grows (never shrinks); the phone asks back an unsure value before it runs, and a
  plain "yes" then runs it from DUET's offer; a raised "okay!" or a shouted "no!" over DUET stops it; an upset user
  is answered by the cloud mind, told how they sounded; DUET's voice slows for distress.
- The benchmark is untouched: its coordinator has no extra hold and its agent never imports the tone channel.
"""

import asyncio
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from duet_voice import agent as base
from duet_voice.coordinator import Coordinator
from duet_voice.galaxy import fast, proactive, tone
from duet_voice.galaxy.modes import MODES
from duet_voice.galaxy.tools import PhoneToolbox
from duet_voice.thinker import ThinkEvent

SR = 16000


def voice(f0=140.0, bright=False, seconds=1.2, level=0.1):
    """A voiced, syllable-like test signal: harmonics of f0, darker (1/k^2) or brighter (1/k)."""
    t = np.arange(int(seconds * SR)) / SR
    x = sum((1.0 / k if bright else 1.0 / k ** 2) * np.sin(2 * np.pi * f0 * k * t) for k in range(1, 40) if f0 * k < 7000)
    x = x * (0.5 - 0.5 * np.cos(2 * np.pi * 3 * t))
    return (x / np.max(np.abs(x)) * level).astype(np.float32)


def seg(words="", on=0.0, off=1.0, delta=None, emotion=None, pause=0.0):
    f = tone.Features(on=on, off=off, speech_s=off - on, delta=delta or {}, emotion=emotion, longest_pause_s=pause)
    f.words, f.worded = words, bool(words)
    return f


RAISED = {"tilt": 12.0, "pitch": 7.0, "loud": 1.0}
CALM = {"tilt": 0.5, "pitch": -0.3, "loud": 0.2}


# --- measuring ------------------------------------------------------------------------------------------------

def test_a_segment_is_measured_for_brightness_pitch_and_level():
    f = tone.analyse(voice(150.0), t0=100.0, with_emotion=False)
    assert abs(12 * np.log2(f.f0_hz / 150.0)) < 0.5
    assert f.on >= 100.0 and f.off <= 101.25 and f.speech_s > 0.5
    brighter = tone.analyse(voice(150.0, bright=True), with_emotion=False)
    assert brighter.tilt_db - f.tilt_db > 6
    assert tone.analyse(np.zeros(SR // 8, dtype=np.float32)) is None          # too short to read


def test_a_raised_voice_is_brighter_and_higher_than_the_speakers_usual_even_at_the_same_level():
    """The phone's gain control flattens loudness, so brightness and pitch carry it (CREMA-D, 21 Oct 2026)."""
    listener = tone.Listener()
    for f0 in (138.0, 142.0, 140.0):
        listener.add(tone.analyse(voice(f0), with_emotion=False))
    plain = listener.add(tone.analyse(voice(141.0), with_emotion=False))
    shout = listener.add(tone.analyse(voice(200.0, bright=True), with_emotion=False))
    assert plain.raised() == []
    assert len(shout.raised()) >= 2 and shout.delta["loud"] < 3          # not louder
    # the shout does not become part of the speaker's usual voice
    assert shout not in listener.baseline.items and plain in listener.baseline.items


def test_nothing_is_raised_before_the_speaker_has_a_baseline():
    listener = tone.Listener()
    first = listener.add(tone.analyse(voice(200.0, bright=True), with_emotion=False))
    assert first.delta == {} and first.raised() == []


def test_loud_enough_alone_counts_and_one_cue_alone_does_not():
    assert tone.raised({"loud": 9.0})
    assert tone.raised({"tilt": 7.0}) == [] and tone.raised({"pitch": 6.0}) == []
    assert len(tone.raised({"tilt": 7.0, "pitch": 5.0})) == 2


def test_the_emotion_model_gives_class_probabilities_when_it_is_there():
    if tone._session() is None:
        pytest.skip("the emotion model is not downloaded here")
    probs, pose = tone.emotion(voice(160.0, seconds=2.0))
    assert set(probs) == set(tone.CLASSES) and abs(sum(probs.values()) - 1.0) < 1e-3 and pose in tone.POSES


# --- reading a request ------------------------------------------------------------------------------------------

def test_urgent_needs_a_raised_voice_and_words_that_stop():
    assert tone.read([seg("No, stop!", delta=RAISED)], "No, stop!").label == "urgent"
    assert tone.read([seg("No, stop.", delta=CALM)], "No, stop.").label == ""             # said calmly
    r = tone.read([seg("Good morning!", delta=RAISED)], "Good morning!")                  # excited, not urgent
    assert r.label == "raised voice" and not r.urgent and r.note() == ""


def test_distress_needs_the_voice_and_the_words():
    sad = {"sad": 0.8, "neutral": 0.2}
    assert tone.read([seg("I have a stomach pain.", emotion=sad)], "I have a stomach pain.").label == "distressed"
    assert tone.read([seg("Good night.", emotion=sad)], "Good night.").label == ""        # the model alone
    assert tone.read([seg("I have a stomach pain.", emotion={"neutral": 1.0})],
                     "I have a stomach pain.").label == ""                                # the words alone
    # a raised voice lowers the bar for the model
    assert tone.read([seg("I'm scared.", delta=RAISED, emotion={"fearful": 0.45, "neutral": 0.55})],
                     "I'm scared.").label == "distressed"


def test_frustration_is_a_raised_or_negative_voice_with_words_of_trouble():
    r = tone.read([seg("It's still not working!", delta=RAISED)], "It's still not working!")
    assert r.label == "frustrated" and "Acknowledge the trouble" in r.note()
    assert tone.read([seg("It's still not working.", delta=CALM)], "It's still not working.").label == ""


@pytest.mark.parametrize("words,unsure", [
    ("Set an alarm for 4:30, I think.", True), ("Set an alarm for maybe 4:30.", True),
    ("Set a timer for 15 minutes or so.", True), ("Set the AC to around 22.", True), ("Set it for 7-ish.", True),
    ("Set the brightness to 40 percent, probably.", True),
    ("Set an alarm for 4:30.", False), ("I think I want an alarm at 7.", False),
    ("Remind me about the meeting at 5.", False), ("Maybe set an alarm for 7.", False),
    ("I think so.", False)])
def test_a_hedge_counts_only_next_to_the_value(words, unsure):
    assert tone.read([], words).unsure is unsure


def test_a_hedge_makes_only_its_own_value_unsure():
    r = tone.read([], "Set an alarm for 4:30, I think, and a timer for 10 minutes.")
    assert r.about("set_alarm", {"time": "16:30"}) and not r.about("set_timer", {"minutes": 10})
    assert tone.read([], "Set it for 7-ish.").about("set_alarm", {"time": "19:00"})
    # a value in words, or a pause before it, cannot be placed: it covers the request's values
    assert tone.read([], "Set a timer for maybe fifteen minutes.").about("set_timer", {"minutes": 15})
    assert not tone.read([], "Set a timer for 15 minutes.").about("set_timer", {"minutes": 15})


def test_a_long_pause_before_the_value_mid_phrase_or_after_a_filler_is_hesitation():
    mid = [seg("Set an alarm for", 0.0, 1.2), seg("4:30.", 2.4, 3.0)]
    assert tone.read(mid, "Set an alarm for 4:30.").unsure                       # 1.2 s, mid-phrase
    short = [seg("Set an alarm for", 0.0, 1.2), seg("4:30.", 1.6, 2.2)]
    assert not tone.read(short, "Set an alarm for 4:30.").unsure                 # 0.4 s: just speaking
    done = [seg("Set an alarm.", 0.0, 1.2), seg("4:30.", 2.4, 3.0)]
    assert not tone.read(done, "Set an alarm. 4:30.").unsure                     # a pause between sentences
    filler = [seg("Set an alarm for, um,", 0.0, 1.4), seg("4:30.", 2.1, 2.7)]
    r = tone.read(filler, "Set an alarm for, um, 4:30.")
    assert r.unsure and r.hesitant and any("paused" in c for c in r.cues)


def test_words_find_their_segment_in_order_and_skip_a_noise_burst():
    listener = tone.Listener()
    noise = listener.expect(ended=10.0)
    listener.add(None, into=noise)                         # too little speech to measure, and no words ever come
    first = listener.expect(ended=11.0)
    second = listener.expect(ended=11.4)                   # the user went on at once
    listener.heard("Set an alarm for", at=11.35)           # the first one's words (the second has only just ended)
    listener.heard("4:30.", at=11.8)
    assert (noise.words, first.words, second.words) == ("", "Set an alarm for", "4:30.")
    listener.add(tone.analyse(voice(150.0), t0=10.2, with_emotion=False), into=first)    # measured after its words
    assert first.measured and first.words == "Set an alarm for" and abs(first.f0_hz - 150) < 5
    assert second not in [s for s in listener.segments if s.measured]   # not read until it is measured
    listener.close(before=11.2)                            # the request was answered
    assert listener.segments == [second]


def test_a_raised_voice_waits_for_its_words():
    listener = tone.Listener()
    shout = seg("", 0.0, 0.8, delta=RAISED)
    shout.worded = False
    listener.add(shout)
    shout.delta = RAISED                                      # (add compares it with an empty baseline)
    assert listener.awaiting_words()
    listener.heard("No!", 1.0)
    assert not listener.awaiting_words()


@pytest.mark.parametrize("tool,args,question", [
    ("set_alarm", {"time": "16:30"}, "4:30 in the afternoon, right?"),
    ("set_reminder", {"time": "07:00", "text": "tablets"}, "7 in the morning, right?"),
    ("set_timer", {"minutes": 15}, "15 minutes, right?"),
    ("set_brightness", {"percent": 40}, "40 percent, right?"),
    ("control_home_device", {"device": "AC", "action": "set", "value": "22"}, "The AC at 22, right?"),
    ("set_car_climate", {"on": True, "temperature": 21}, "21 degrees, right?")])
def test_the_question_names_the_value(tool, args, question):
    assert tone.ask_back(tool, args)[0] == question


def test_actions_with_no_value_are_never_asked_back():
    assert tone.ask_back("call_contact", {"name": "Priya"}) is None
    assert tone.ask_back("set_torch", {"on": True}) is None


# --- the coordinator ------------------------------------------------------------------------------------------

def test_the_voice_only_ever_lengthens_the_hold():
    coord = Coordinator(commit_hold_s=1.4, revising_hold_s=1.8)
    assert coord.extra_hold is None and coord.hold() == 1.4             # the benchmark: the words alone
    coord.extra_hold = lambda: 4.0
    assert coord.hold() == 4.0
    coord.extra_hold = lambda: 0.0
    assert coord.hold() == 1.4

    def broken():
        raise RuntimeError("no reading")
    coord.extra_hold = broken
    assert coord.hold() == 1.4


# --- the phone's actions ------------------------------------------------------------------------------------------

def toolbox(reading):
    sent, events, asked = [], [], []

    async def send(tool, args, timeout):
        sent.append((tool, args))
        return json.dumps({"status": "ok", "alarm": args.get("time")})
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.turn_committed()
    box = PhoneToolbox(coord, MODES["assistant"]["tools"], send, emit=events.append)
    box.tone = lambda: reading
    box.on_unsure = lambda *a: asked.append(a)
    return box, sent, events, asked


def test_an_unsure_value_is_asked_back_and_never_sent():
    # (the half of the day said: a bare "4:30" is the next 4:30, which after 16:30 is the morning one)
    box, sent, events, asked = toolbox(tone.read([], "Set an alarm for 4:30 in the afternoon, I think."))
    box.coord.open_utterance = "Set an alarm for 4:30 in the afternoon, I think."
    out = json.loads(asyncio.run(box.call("set_alarm", {"time": "16:30"})))
    assert out["status"] == "not_executed" and "4:30 in the afternoon, right?" in out["reason"]
    assert sent == [] and box.confirming == ("set_alarm", {"time": "16:30"}, "4:30 in the afternoon, right?")
    assert asked and asked[0][2] == "4:30 in the afternoon, right?"
    assert [e for e in events if e["kind"] == "tool_asked"][0]["why"].startswith("you sounded unsure")
    box.new_request()
    assert box.confirming is None


def test_a_certain_value_and_a_value_free_action_run_as_before():
    box, sent, _, _ = toolbox(tone.read([], "Set an alarm for 4:30."))
    asyncio.run(box.call("set_alarm", {"time": "16:30"}))
    assert sent == [("set_alarm", {"time": "16:30"})]
    box, sent, _, _ = toolbox(tone.read([], "Call Priya, I think, at 5."))
    box.consent = None
    asyncio.run(box.call("call_contact", {"name": "Priya"}))
    assert sent == [("call_contact", {"name": "Priya"})]
    box, sent, _, _ = toolbox(None)                           # no tone channel
    asyncio.run(box.call("set_timer", {"minutes": 15}))
    assert sent == [("set_timer", {"minutes": 15})]


def test_the_reading_is_taken_after_the_gates_hold():
    """A hedge said after a short pause belongs to the request: the value is checked once the user has finished."""
    coord = Coordinator(commit_hold_s=0.3, revising_hold_s=0.3, dangling_hold_s=0.3)
    coord.turn_committed()
    seen = []

    async def send(tool, args, timeout):
        return json.dumps({"status": "ok"})
    box = PhoneToolbox(coord, MODES["assistant"]["tools"], send)
    box.tone = lambda: seen.append(coord.clock() - coord.last_speech_end) or None

    async def go():
        coord.last_speech_end = coord.clock()
        await box.call("set_timer", {"minutes": 5})
    asyncio.run(go())
    assert seen and seen[0] >= 0.3


def test_a_yes_to_the_question_runs_it_from_the_offer():
    offer = proactive.Offer(text="4:30 in the afternoon, right?", why="you sounded unsure: \"I think\"",
                            items=[proactive.OfferItem("set_alarm", {"time": "16:30"}, "set an alarm for 4:30")],
                            source="tone")
    assert proactive.answer(offer, "Yes.") == [("set_alarm", {"time": "16:30"})]
    assert proactive.answer(offer, "Yeah, that's right.") is None or proactive.answer(offer, "Yes, please.")
    assert proactive.declines("No.") and not proactive.declines("No, 5:30.")
    assert "sounded unsure" in offer.note() and "without being asked" not in offer.note()


# --- the two minds ----------------------------------------------------------------------------------------------

class Script:
    def __init__(self, model, script):
        self.model, self.script, self.history, self.thinking = model, script, [], ""

    async def run(self, text, toolbox, audio=None, note="", allow_listen=True):
        for item in self.script:
            if callable(item):
                item()
                continue
            yield item


CLOUD = [ThinkEvent("decided", tools=False), ThinkEvent("say", text="From the cloud.")]


def says(hybrid, words, box=None):
    box = box or NS(coord=Coordinator(), confirming=None)

    async def go():
        return [e async for e in hybrid.run(words, box, note="")]
    return [e.text for e in asyncio.run(go()) if e.kind == "say"]


def test_a_raised_okay_over_duet_stops_it_and_a_calm_one_lets_it_go_on():
    hybrid = fast.HybridThinker(Script("e2b", [ThinkEvent("chat", text="Okay!")]), Script("26b", CLOUD))
    hybrid.resume_text = "The phone is warm too."
    hybrid.tone = tone.read([seg("Okay, okay!", delta=RAISED)], "Okay, okay!")
    assert says(hybrid, "Okay, okay!") == ["<silent>"]
    hybrid.tone = tone.read([seg("Okay.", delta=CALM)], "Okay.")
    assert says(hybrid, "Okay.") == ["The phone is warm too."]


def test_a_shouted_no_is_a_stop_and_a_calm_no_is_not():
    hybrid = fast.HybridThinker(Script("e2b", [ThinkEvent("chat", text="Alright.")]), Script("26b", CLOUD))
    hybrid.tone = tone.read([seg("No, no, no!", delta=RAISED)], "No, no, no!")
    assert says(hybrid, "No, no, no!") == ["<silent>"]
    hybrid.tone = tone.read([seg("No.", delta=CALM)], "No.")
    assert says(hybrid, "No.") != ["<silent>"]
    hybrid.tone = tone.read([seg("No, set it for 5!", delta=RAISED)], "No, set it for 5!")
    assert says(hybrid, "No, set it for 5!") != ["<silent>"]       # a correction, however loud


def test_an_upset_user_is_answered_by_the_cloud_mind():
    hybrid = fast.HybridThinker(Script("e2b", [ThinkEvent("chat", text="Good night!")]), Script("26b", CLOUD))
    hybrid.tone = tone.Reading(upset="distressed", cues=["voice: sad 0.80", '"not okay"'])
    assert says(hybrid, "I'm not okay.") == ["From the cloud."]
    hybrid.tone = None
    assert says(hybrid, "Good night.") == ["Good night!"]


def test_the_question_ends_the_reply_whichever_mind_planned_the_action():
    box = NS(coord=Coordinator(), confirming=None)

    def held():
        box.confirming = ("set_alarm", {"time": "16:30"}, "4:30 in the afternoon, right?")
    fast_script = [ThinkEvent("decided", tools=True), ThinkEvent("tool_start", name="set_alarm", args={"time": "16:30"}),
                   held, ThinkEvent("tool_done", name="set_alarm", args={"time": "16:30"}, outcome="not_executed"),
                   ThinkEvent("say", text="I've set it for 4:30.")]
    hybrid = fast.HybridThinker(Script("e2b", fast_script), Script("26b", CLOUD))
    assert says(hybrid, "Set an alarm for 4:30, I think.", box) == ["4:30 in the afternoon, right?"]
    assert "nothing was done with it yet" in hybrid.edge_log[-1]
    box.confirming = None
    cloud_script = [ThinkEvent("decided", tools=True), held,
                    ThinkEvent("tool_done", name="set_alarm", args={"time": "16:30"}, outcome="not_executed"),
                    ThinkEvent("say", text="Done.")]
    hybrid = fast.HybridThinker(Script("e2b", [ThinkEvent("escalate", text="cloud")]), Script("26b", cloud_script))
    assert says(hybrid, "Set an alarm for 4:30, I think.", box)[-1] == "4:30 in the afternoon, right?"


def test_the_other_actions_of_the_plan_still_run_before_the_question():
    """"Set an alarm for 4:30, I think, and a timer for 10 minutes": the timer is set, the alarm is asked about."""
    box = NS(coord=Coordinator(), confirming=None)
    events = []

    def held():
        box.confirming = ("set_alarm", {"time": "16:30"}, "4:30 in the afternoon, right?")
    plan = [ThinkEvent("decided", tools=True), held,
            ThinkEvent("tool_done", name="set_alarm", args={"time": "16:30"}, outcome="not_executed"),
            ThinkEvent("tool_start", name="set_timer", args={"minutes": 10}),
            ThinkEvent("tool_done", name="set_timer", args={"minutes": 10}, outcome="ok"),
            ThinkEvent("say", text="I set the timer and the alarm.")]
    hybrid = fast.HybridThinker(Script("e2b", plan), Script("26b", CLOUD))

    async def go():
        return [e async for e in hybrid.run("Set an alarm for 4:30, I think, and a timer for 10 minutes.", box)]
    for e in asyncio.run(go()):
        events.append((e.kind, e.text if e.kind == "say" else getattr(e, "name", "")))
    assert ("tool_done", "set_timer") in events
    assert [t for k, t in events if k == "say"] == ["4:30 in the afternoon, right?"]


def test_a_slow_reply_after_the_held_action_still_asks(monkeypatch):
    """The fast mind's reply after acting can time out; the reply then ends with the question, not in silence."""
    monkeypatch.setattr(fast, "AFTER_ACTION_S", 0.05)
    box = NS(coord=Coordinator(), confirming=None)

    class Slow(Script):
        async def run(self, text, toolbox, audio=None, note="", allow_listen=True):
            yield ThinkEvent("decided", tools=True)
            box.confirming = ("set_timer", {"minutes": 15}, "15 minutes, right?")
            yield ThinkEvent("tool_done", name="set_timer", args={"minutes": 15}, outcome="not_executed")
            await asyncio.sleep(1.0)
            yield ThinkEvent("say", text="too late")
    hybrid = fast.HybridThinker(Slow("e2b", []), Script("26b", CLOUD))
    assert says(hybrid, "Set a timer for 15 minutes, maybe.", box) == ["15 minutes, right?"]


def test_a_hand_over_for_the_held_value_alone_is_answered_with_the_question_at_once():
    """21 Oct 2026: the fast mind held the unsure alarm, set the timer, then handed over ("not simply done"), and the
    cloud mind said the same question 5.8 s later."""
    box = NS(coord=Coordinator(), confirming=None, failed=[])

    def held():
        box.confirming = ("set_alarm", {"time": "16:30"}, "4:30 in the afternoon, right?")
    plan = [ThinkEvent("decided", tools=True), held,
            ThinkEvent("tool_done", name="set_alarm", args={"time": "16:30"}, outcome="not_executed"),
            ThinkEvent("tool_done", name="set_timer", args={"minutes": 10}, outcome="ok"),
            ThinkEvent("escalate", text="the phone did not simply do it (not_executed, ok)")]
    cloud = Script("26b", [ThinkEvent("say", text="From the cloud.")])
    hybrid = fast.HybridThinker(Script("e2b", plan), cloud)
    assert says(hybrid, "Set an alarm for 4:30, I think, and a timer for 10 minutes.", box) == [
        "4:30 in the afternoon, right?"]
    box.confirming, box.failed = None, [("set_timer", "error")]       # something else failed too: the cloud explains
    hybrid = fast.HybridThinker(Script("e2b", plan), cloud)
    assert says(hybrid, "Set an alarm for 4:30, I think, and a timer for 10 minutes.", box)[-1] == \
        "4:30 in the afternoon, right?"


def test_a_question_from_a_plan_the_user_overtook_is_not_asked():
    box = NS(coord=Coordinator(), confirming=("set_alarm", {"time": "16:30"}, "4:30 in the afternoon, right?"))
    hybrid = fast.HybridThinker(Script("e2b", [ThinkEvent("chat", text="Good morning!")]), Script("26b", CLOUD))
    assert says(hybrid, "Good morning.", box) == ["Good morning!"]


def test_a_mind_that_calls_the_held_action_again_is_told_the_same_quietly():
    # (the half of the day said: a bare "4:30" is the next 4:30, which after 16:30 is the morning one)
    box, sent, events, asked = toolbox(tone.read([], "Set an alarm for 4:30 in the afternoon, I think."))
    box.coord.open_utterance = "Set an alarm for 4:30 in the afternoon, I think."
    for _ in range(2):
        out = json.loads(asyncio.run(box.call("set_alarm", {"time": "16:30"})))
        assert out["status"] == "not_executed"
    assert sent == [] and len(asked) == 1 and len([e for e in events if e["kind"] == "tool_asked"]) == 1


# --- the agent ----------------------------------------------------------------------------------------------

def galaxy_agent(monkeypatch):
    monkeypatch.setenv("DUET_FAST", "1")
    from duet_voice.galaxy import agent as gagent

    async def send(tool, args, timeout):
        return json.dumps({"status": "ok"})
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=1.8)
    box = PhoneToolbox(coord, MODES["assistant"]["tools"], send)
    return gagent, gagent.GalaxyAgent(base.Trace("test"), coord, box, gagent.get_mode("assistant"), "",
                                      user_name="Tanmay")


def test_the_agent_measures_each_segment_from_the_tape(monkeypatch):
    from livekit import rtc
    gagent, agent = galaxy_agent(monkeypatch)
    monkeypatch.setenv("DUET_TONE_EMOTION", "0")
    monkeypatch.setitem(tone._model, "tried", True)
    pcm = (voice(150.0, seconds=1.0) * 32767).astype(np.int16)
    pcm48 = np.repeat(pcm, 3)                                 # 48 kHz, as the room sends it

    async def go():
        agent.speech_started()
        for i in range(0, len(pcm48), 480):
            agent._tape.add(rtc.AudioFrame(pcm48[i:i + 480].tobytes(), 48000, 1, 480))
        agent.speech_ended()
        await asyncio.gather(*list(agent._tone_jobs))
    asyncio.run(go())
    assert len(agent.listener.segments) == 1
    f = agent.listener.segments[0]
    assert 0.6 < f.speech_s < 1.1 and abs(12 * np.log2(f.f0_hz / 150.0)) < 0.7
    agent.heard_words("Set a timer for ten minutes.")
    assert f.words == "Set a timer for ten minutes."


def test_the_agent_holds_longer_asks_once_and_tells_the_minds(monkeypatch):
    gagent, agent = galaxy_agent(monkeypatch)
    assert agent._coord.extra_hold == agent.tone_hold and agent._toolbox.tone is not None
    shout = seg("", 0.0, 0.8)
    shout.worded = False
    agent.listener.segments.append(shout)
    shout.delta = RAISED
    assert agent.tone_hold() == gagent.RAISED_WAIT_S
    agent.listener.heard("Wait!", 1.0)
    agent._coord.open_utterance = "Wait! Set an alarm for maybe 4:30."
    assert agent.tone_hold() == 1.8                           # hesitant: the time to correct it
    agent._coord.open_utterance = "Set an alarm for 4:30."
    agent.listener.segments = []
    assert agent.tone_hold() == 0.0
    reading = tone.read([], "Set an alarm for 4:30, I think.")
    agent._unsure_offer("set_alarm", {"time": "16:30"}, "4:30 in the afternoon, right?", "set an alarm", reading)
    offer = agent.offers.active()
    assert offer.source == "tone" and offer.items[0].args == {"time": "16:30"}
    agent._tone = tone.Reading(upset="frustrated", cues=["raised voice", '"still not working"'])
    assert "sounded frustrated" in agent.turn_note()
    agent._tone = reading
    assert agent.ack_line() == ""                             # no "Sure." before "4:30, right?"


def test_the_voice_slows_for_distress_and_its_fixed_lines_stay_cached(monkeypatch):
    gagent, agent = galaxy_agent(monkeypatch)

    async def go():
        voice_ = gagent.GalaxyTTS()
        calm = voice_.synthesize("Sure.").speed
        voice_.speed = gagent.DISTRESSED_SPEED
        return calm, voice_.synthesize("I'm here.").speed
    calm, slow = asyncio.run(go())
    assert calm == 1.0 and slow == pytest.approx(0.9)


def test_the_health_check_says_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("DUET_TONE", "0")
    assert tone.status()["ok"] is None
    monkeypatch.setenv("DUET_TONE", "1")
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **k: None)
    out = tone.status()
    assert out["ok"] is False and "emotion model" in out["why"]
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **k: str(tmp_path / "model.onnx"))
    assert tone.status() == {"ok": True, "why": ""}


def test_the_benchmark_never_hears_the_tone_channel():
    import inspect
    src = inspect.getsource(base)
    assert "tone" not in {n for n in dir(base)} and "galaxy.tone" not in src and "extra_hold" not in src
    assert Coordinator().extra_hold is None
