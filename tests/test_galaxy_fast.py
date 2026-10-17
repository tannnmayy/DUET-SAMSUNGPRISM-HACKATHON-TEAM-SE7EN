"""Two speeds: the fast mind acts on simple phone actions, the cloud mind takes everything else.

- a simple action: the fast mind's events pass through, the cloud mind is never called;
- a hand-off, a reply without a tool, a failure or a slow fast mind: the cloud mind answers,
  told what the fast mind already did;
- an unfinished sentence: keep_listening passes through;
- a fast mind that hangs after it acted: the reply ends quietly (the action was confirmed).
"""

import asyncio
import time

from duet_voice.coordinator import CallRecord, Coordinator
from duet_voice.galaxy import fast as fast_mod
from duet_voice.galaxy.fast import HybridThinker
from duet_voice.thinker import ThinkEvent


class Script:
    """A thinker that yields scripted events; a number in the script is a pause in seconds."""

    def __init__(self, model, script, record_action=None):
        self.model, self.script, self.history, self.thinking = model, script, [], ""
        self.notes, self.calls = [], 0
        self.record_action = record_action

    async def run(self, text, toolbox, audio=None, note="", allow_listen=True):
        self.calls += 1
        self.notes.append(note)
        for item in self.script:
            if isinstance(item, (int, float)):
                await asyncio.sleep(item)
                continue
            if item.kind == "tool_done" and self.record_action:
                toolbox.coord.history.append(CallRecord(item.name, item.args, 0, 0.0, 0.0, "ok"))
            yield item


class Box:
    def __init__(self):
        self.coord = Coordinator()


def collect(thinker, text="set an alarm for 7", box=None):
    box = box or Box()

    async def go():
        return [ev async for ev in thinker.run(text, box, note="Now: Friday 23:50.")]
    return asyncio.run(go()), box


SAY = ThinkEvent("say", text="<silent>")
ACT = [ThinkEvent("decided", tools=True), ThinkEvent("tool_start", name="set_alarm", args={"time": "07:00"}),
       ThinkEvent("tool_done", name="set_alarm", args={"time": "07:00"}, outcome="ok"), SAY]
CLOUD = [ThinkEvent("decided", tools=False), ThinkEvent("say", text="7 or 6:30?")]


def routes():
    seen = []
    return seen, (lambda where, why: seen.append(where))


def test_a_simple_action_stays_on_the_fast_mind():
    seen, on_route = routes()
    fast, slow = Script("e2b", ACT, record_action=True), Script("26b", CLOUD)
    events, _ = collect(HybridThinker(fast, slow, on_route=on_route))
    # the turn loop hears the route first ("Sure." rather than "One moment.")
    assert [e.kind for e in events] == ["route", "decided", "tool_start", "tool_done", "say"]
    assert events[0].text == "edge" and slow.calls == 0 and seen == ["edge"]


def test_a_hand_off_goes_to_the_cloud_mind():
    seen, on_route = routes()
    fast, slow = Script("e2b", [ThinkEvent("escalate", text="the user wavers")]), Script("26b", CLOUD)
    events, _ = collect(HybridThinker(fast, slow, on_route=on_route))
    assert [e.text for e in events if e.kind == "say"] == ["7 or 6:30?"]
    assert slow.calls == 1 and seen == ["cloud"]
    assert [e.text for e in events if e.kind == "route"] == ["cloud"]   # the wait is covered at once


def test_a_fast_reply_without_a_tool_goes_to_the_cloud_mind():
    fast = Script("e2b", [ThinkEvent("decided", tools=False), ThinkEvent("say", text="Sure!")])
    slow = Script("26b", CLOUD)
    events, _ = collect(HybridThinker(fast, slow))
    assert slow.calls == 1 and "Sure!" not in [e.text for e in events]


def test_a_failed_or_slow_fast_mind_hands_over_in_time():
    for script in ([ThinkEvent("error", text="connection refused")], [5.0] + ACT):
        fast, slow = Script("e2b", script), Script("26b", CLOUD)
        start = time.monotonic()
        events, _ = collect(HybridThinker(fast, slow, deadline_s=0.4))
        assert slow.calls == 1 and [e.text for e in events if e.kind == "say"] == ["7 or 6:30?"]
        assert time.monotonic() - start < 2.0


def test_without_the_cloud_mind_simple_actions_still_run_and_the_rest_is_said_at_once():
    # the offline drill: the fast mind still acts; a request for the cloud mind fails at once, honestly
    fast, slow = Script("e2b", ACT, record_action=True), Script("26b", CLOUD)
    hybrid = HybridThinker(fast, slow)
    hybrid.cloud_off = True
    events, _ = collect(hybrid)
    assert [e.kind for e in events][-1] == "say" and slow.calls == 0
    hybrid.fast = Script("e2b", [ThinkEvent("escalate", text="a question")])
    start = time.monotonic()
    events, _ = collect(hybrid, "what's the weather?")
    assert [(e.kind, e.text) for e in events] == [("error", fast_mod.CLOUD_OFF)] and slow.calls == 0
    assert time.monotonic() - start < 0.5


def test_after_the_cloud_mind_could_not_be_reached_it_is_skipped_for_a_while():
    fast = Script("e2b", [ThinkEvent("escalate", text="a question")])
    slow = Script("26b", [ThinkEvent("error", text="cannot reach Google's API (getaddrinfo failed)")])
    hybrid = HybridThinker(fast, slow)
    collect(hybrid, "what's the weather?")
    assert slow.calls == 1
    hybrid.fast = Script("e2b", [ThinkEvent("escalate", text="a question")])
    events, _ = collect(hybrid, "and tomorrow?")
    assert slow.calls == 1 and [(e.kind, e.text) for e in events] == [("error", fast_mod.CLOUD_DOWN)]
    assert fast_mod.cloud_unreachable(events[0].text) and not fast_mod.cloud_unreachable("quota exceeded")


def test_keep_listening_passes_through():
    fast, slow = Script("e2b", [ThinkEvent("listen", text="the order number is...")]), Script("26b", CLOUD)
    events, _ = collect(HybridThinker(fast, slow))
    assert [e.kind for e in events] == ["listen"] and slow.calls == 0


def test_the_cloud_mind_hears_what_the_fast_mind_did():
    fast, slow = Script("e2b", ACT, record_action=True), Script("26b", CLOUD)
    hybrid = HybridThinker(fast, slow)
    collect(hybrid)                                     # handled on the edge
    hybrid.fast = Script("e2b", [ThinkEvent("escalate", text="a question")])
    collect(hybrid, "what alarms do I have?")
    assert "Handled quickly by the phone" in slow.notes[-1] and "set_alarm" in slow.notes[-1]
    assert "Now: Friday 23:50." in slow.notes[-1]      # the agent's own note is kept
    # acting, then handing over within the same request
    box = Box()
    hybrid.fast = Script("e2b", [ThinkEvent("decided", tools=True),
                                 ThinkEvent("tool_done", name="set_alarm", args={"time": "07:00"}, outcome="ok"),
                                 ThinkEvent("escalate", text="the rest is a question")], record_action=True)
    collect(hybrid, "set an alarm for 7 and tell me the weather", box)
    assert "Already carried out for this request" in slow.notes[-1]


def test_a_fast_mind_that_hangs_after_acting_ends_the_reply_quietly(monkeypatch):
    monkeypatch.setattr(fast_mod, "AFTER_ACTION_S", 0.5)
    fast = Script("e2b", ACT[:3] + [30.0, SAY], record_action=True)
    slow = Script("26b", CLOUD)
    start = time.monotonic()
    events, _ = collect(HybridThinker(fast, slow))
    assert events[-1].kind == "say" and events[-1].text == "<silent>" and slow.calls == 0
    assert time.monotonic() - start < 2.0


def test_the_fast_mind_sees_only_simple_actions_and_the_hand_off():
    from duet_voice.galaxy.modes import MODES
    names = [s["name"] for s in fast_mod.fast_specs(MODES["care"]["tools"])]
    assert "ask_expert" in names and "call_contact" in names
    assert not {"log_medication", "get_medication_schedule", "alert_family", "log_checkin"} & set(names)
