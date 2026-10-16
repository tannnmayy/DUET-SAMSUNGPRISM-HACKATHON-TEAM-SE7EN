"""How well does the fast mind judge? Its decisions on 42 requests, text only, in about a minute.

    python scripts/galaxy_fast_eval.py                 # the fast mind at DUET_FAST_BASE_URL (llama.cpp)
    python scripts/galaxy_fast_eval.py --repeat 3

For each request the fast mind (duet_voice/galaxy/fast.py) must either:
  - act: carry out exactly the expected simple phone actions, with the final values;
  - hand over: call ask_expert (or reply without a tool, which the phone app also hands over);
  - keep listening: when the sentence is cut off.
The two failures that matter most are counted apart:
  wrong action: it acted with a wrong or stale value, or acted on something it should have handed over;
  missed: it handed over a simple action it should have done (slower, but not wrong).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet_voice.coordinator import Coordinator  # noqa: E402
from duet_voice.galaxy import fast  # noqa: E402
from duet_voice.galaxy.modes import MODES  # noqa: E402

ACT, HAND, LISTEN, NOTHING = "act", "hand", "listen", "nothing"   # NOTHING: hand over, listen, or a quiet no-op

CASES = [
    # (mode, words, expected, [(tool, {arg: pattern})])
    ("assistant", "Set an alarm for 7 tomorrow morning.", ACT, [("set_alarm", {"time": "@7:00"})]),
    ("assistant", "Set an alarm for 6:30, no wait, make it 7.", ACT, [("set_alarm", {"time": "@7:00"})]),
    ("assistant", "Wake me up at 5, no, 6, no wait, 7.", ACT, [("set_alarm", {"time": "@7:00"})]),
    ("assistant", "Set a timer for 10 minutes. I mean 15.", ACT, [("set_timer", {"minutes": "^15(\\.0)?$"})]),
    ("assistant", "Turn the brightness down to 40. Actually, make it 30.", ACT, [("set_brightness", {"percent": "^30$"})]),
    ("assistant", "Turn on the torch.", ACT, [("set_torch", {"on": "true"})]),
    ("assistant", "Switch off the flashlight.", ACT, [("set_torch", {"on": "false"})]),
    ("assistant", "Pause the music.", ACT, [("control_media", {"action": "pause"})]),
    ("assistant", "Skip this song.", ACT, [("control_media", {"action": "next"})]),
    ("assistant", "Set the volume to 30 percent.", ACT, [("set_volume", {"percent": "^30$"})]),
    ("assistant", "Call Priya.", ACT, [("call_contact", {"name": "priya"})]),
    ("assistant", "Text Priya that I'm running late. Actually, just call her instead.", ACT,
     [("call_contact", {"name": "priya"})]),
    ("assistant", "Send a message to Rahul saying I'll be home by eight.", ACT,
     [("send_message", {"to": "rahul", "text": "eight|8"})]),
    ("assistant", "Open WhatsApp.", ACT, [("open_app", {"name": "whatsapp"})]),
    ("assistant", "Open the battery settings.", ACT, [("open_settings", {"page": "battery"})]),
    ("assistant", "Navigate to the airport.", ACT, [("navigate_to", {"destination": "airport"})]),
    ("assistant", "Turn on the AC.", ACT, [("control_home_device", {"device": "ac|air", "action": "on"})]),
    ("assistant", "Turn on the bedroom light and lock the front door.", ACT,
     [("control_home_device", {"device": "bedroom", "action": "on"}),
      ("control_home_device", {"device": "door|lock", "action": "lock"})]),
    ("assistant", "Set an alarm for 6:30 and a timer for 20 minutes.", ACT,
     [("set_alarm", {"time": "@6:30"}), ("set_timer", {"minutes": "^20(\\.0)?$"})]),
    ("assistant", "Set the screen timeout to 30 seconds.", ACT, [("set_screen_timeout", {"seconds": "^30$"})]),
    ("assistant", "Cancel my 7 o'clock alarm.", ACT, [("cancel_alarm", {"time": "@7:00"})]),
    ("care", "Remind me at 6 to call the pharmacy.", ACT, [("set_reminder", {"time": "@6:00", "text": "pharmac"})]),
    ("drive", "Take me to the office. No wait, the airport.", ACT, [("set_destination", {"place": "airport"})]),
    ("drive", "Take me to the gym. No, the office. Wait, actually, home.", ACT, [("set_destination", {"place": "home"})]),
    ("drive", "Set the AC to 22. No, 21.", ACT, [("set_car_climate", {"temperature": "^21(\\.0)?$"})]),
    # the cloud mind's: questions, judgement, care, the world, wavering
    ("assistant", "Call Rahul, or maybe Priya, hmm, I'm not sure.", HAND, []),
    ("assistant", "Wake me up at 6:30 tomorrow, or maybe 7, hmm.", HAND, []),
    ("assistant", "Why does my battery drain so fast?", HAND, []),
    ("assistant", "How many shopping apps do I have?", HAND, []),
    ("assistant", "What's the weather tomorrow?", HAND, []),
    ("assistant", "Who won the cricket match yesterday?", HAND, []),
    ("assistant", "Would lowering my brightness help my battery?", HAND, []),
    ("assistant", "Hi DUET, how are you?", HAND, []),
    ("care", "Did I take my blood pressure tablet today?", HAND, []),
    ("care", "I fell down and my hip really hurts.", HAND, []),
    ("care", "I'm feeling a bit lonely today.", HAND, []),
    ("assistant", "Set an alarm for 6 tomorrow. Actually, never mind.", NOTHING, []),
    ("assistant", "Add a dentist appointment on Monday at 4.", HAND, []),
    ("assistant", "Is it going to rain in Chennai today?", HAND, []),
    ("drive", "How far is the airport from here?", HAND, []),
    # cut off mid-sentence
    ("assistant", "Set an alarm for", NOTHING, []),
    ("assistant", "Call my", NOTHING, []),
]

# Held out: written after the fast mind's checks and examples, to measure what they did not see.
HELD_OUT = [
    ("assistant", "Wake me up at quarter past six.", ACT, [("set_alarm", {"time": "@6:15"})]),
    ("assistant", "Turn off the living room light.", ACT,
     [("control_home_device", {"device": "living", "action": "off"})]),
    ("assistant", "Lower the volume to twenty percent.", ACT, [("set_volume", {"percent": "^20$"})]),
    ("assistant", "Ring Rahul.", ACT, [("call_contact", {"name": "rahul"})]),
    ("assistant", "Open YouTube.", ACT, [("open_app", {"name": "youtube"})]),
    ("assistant", "Play the next track.", ACT, [("control_media", {"action": "next"})]),
    ("assistant", "Set a timer for 3 minutes, no, 4.", ACT, [("set_timer", {"minutes": "^4(\.0)?$"})]),
    ("drive", "Make it cooler, set the AC to 20.", ACT, [("set_car_climate", {"temperature": "^20(\.0)?$"})]),
    ("assistant", "What's my battery percentage?", HAND, []),
    ("assistant", "Which app did I use the most today?", HAND, []),
    ("assistant", "Should I turn on power saving?", HAND, []),
    ("assistant", "What's on my calendar tomorrow?", HAND, []),
    ("care", "I think I took my sugar tablet twice.", HAND, []),
    ("assistant", "Turn on the", NOTHING, []),
]


def match_value(pattern, value):
    v = str(value).lower() if value is not None else ""
    if pattern.startswith("@"):
        m, w = re.match(r"\s*(\d{1,2}):(\d{2})", v), re.match(r"@(\d{1,2}):(\d{2})", pattern)
        return bool(m and w) and int(m.group(1)) % 12 == int(w.group(1)) % 12 and m.group(2) == w.group(2)
    return re.search(pattern, v, re.I) is not None


class Box:
    """The phone app's own toolbox (its guards: undecided, cut off, times without AM or PM), over a
    phone that does whatever it is asked and says ok. `calls` is what really reached the phone."""

    def __init__(self, mode, words):
        from duet_voice.galaxy.tools import PhoneToolbox
        coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
        coord.heard(words)
        coord.turn_committed()
        self.calls = []

        async def send(tool, args, timeout):
            self.calls.append((tool, dict(args)))
            return json.dumps({"status": "ok"})
        self.box = PhoneToolbox(coord, MODES[mode]["tools"], send)
        self.coord = coord

    async def call(self, tool, args, epoch=None):
        return await self.box.call(tool, args, epoch=epoch)


async def judge(mode, words, expected, wanted):
    thinker = fast.make_fast_thinker(MODES[mode]["tools"], MODES[mode]["title"])
    thinker.vet = lambda tool, args: fast.vet(tool, args, words)   # as the phone app does
    box = Box(mode, words)
    t0 = time.time()
    first = None
    outcome = "silent"
    async for ev in thinker.run(words, box, note="Now: Friday 17 October 2026, 23:50."):
        first = first or time.time() - t0
        if ev.kind == "escalate":
            outcome = "hand"
        elif ev.kind == "listen":
            outcome = "listen"
        elif ev.kind == "decided" and not ev.tools:
            outcome = "hand"          # a reply without a tool goes to the cloud mind in the app
        elif ev.kind == "error":
            outcome = "error"
    total = time.time() - t0
    acted = [(t, a) for t, a in box.calls if t != fast.ASK_EXPERT]
    if expected == ACT:
        right = len(acted) == len(wanted) and all(
            any(t == wt and all(match_value(p, a.get(k)) for k, p in wa.items()) for t, a in acted)
            for wt, wa in wanted)
        verdict = "ok" if right else ("missed" if not acted else "WRONG ACTION")
    else:
        verdict = "ok" if not acted else "WRONG ACTION"
    return {"mode": mode, "words": words, "expected": expected, "outcome": outcome, "acted": acted,
            "verdict": verdict, "first_s": round(first or total, 2), "total_s": round(total, 2)}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    rows = []
    for _ in range(args.repeat):
        for held, case in [(False, c) for c in CASES] + [(True, c) for c in HELD_OUT]:
            r = await judge(*case)
            r["held_out"] = held
            rows.append(r)
            print("%-13s %-9s %-7s %5.2fs  %-40s %s" % (r["verdict"], r["expected"], r["outcome"], r["total_s"],
                                                       r["words"][:40], json.dumps(r["acted"])[:90]), flush=True)
    for name, part in (("dev set", [r for r in rows if not r["held_out"]]), ("held out", [r for r in rows if r["held_out"]])):
        w = sum(r["verdict"] == "WRONG ACTION" for r in part)
        m = sum(r["verdict"] == "missed" for r in part)
        print("%-9s %d decisions: %d right, %d wrong actions, %d missed" % (name, len(part), len(part) - w - m, w, m))
    n = len(rows)
    wrong = [r for r in rows if r["verdict"] == "WRONG ACTION"]
    missed = [r for r in rows if r["verdict"] == "missed"]
    acts = [r for r in rows if r["expected"] == ACT and r["verdict"] == "ok"]
    times = sorted(r["total_s"] for r in acts)
    print("\n%d decisions: %d right, %d wrong actions, %d missed (handed over)" % (
        n, n - len(wrong) - len(missed), len(wrong), len(missed)))
    if times:
        print("time to act (decision and every tool call): p50 %.2f s, max %.2f s" % (times[len(times) // 2], times[-1]))
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
