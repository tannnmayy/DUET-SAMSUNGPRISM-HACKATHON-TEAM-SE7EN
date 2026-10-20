"""How well does the fast mind judge? Its decisions on 95 requests, text only, in about a minute.

    python scripts/galaxy_fast_eval.py                 # the fast mind at DUET_FAST_BASE_URL (llama.cpp)
    python scripts/galaxy_fast_eval.py --repeat 3
    python scripts/galaxy_fast_eval.py --only chat     # the small-talk cases alone

For each request the fast mind (duet_voice/galaxy/fast.py) must either:
  - act: carry out exactly the expected simple phone actions, with the final values;
  - chat: answer small talk itself, with words the app lets it speak (fast.chat_ok);
  - hand over: call ask_expert (or reply with words the app would not speak, which it also hands over);
  - keep listening: when the sentence is cut off.
The failures that matter most are counted apart:
  wrong action: it acted with a wrong or stale value, or acted on something it should have handed over;
  wrong chat: the app would have spoken its own words where the cloud mind had to answer;
  missed: it handed over a simple action or small talk it should have done (slower, but not wrong).
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
CHAT, EITHER = "chat", "chat|hand"                                 # EITHER: small talk or the cloud mind, both fine

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
    ("assistant", "Hi DUET, how are you?", CHAT, []),
    ("care", "Did I take my blood pressure tablet today?", HAND, []),
    ("care", "I fell down and my hip really hurts.", HAND, []),
    ("care", "I'm feeling a bit lonely today.", EITHER, []),
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

# As the recogniser writes them: "430" and "3.30" for clock times (seen live on 16 Oct 2026)
RECOGNISER = [
    ("assistant", "Set an alarm for 3.30. No, no, cancel that. Could you set it for 430 instead?", ACT,
     [("set_alarm", {"time": "@4:30"})]),
    ("assistant", "Wake me up at 630.", ACT, [("set_alarm", {"time": "@6:30"})]),
    ("assistant", "Set an alarm for 7.15, no, 7.45.", ACT, [("set_alarm", {"time": "@7:45"})]),
    ("assistant", "Set the volume to 30%.", ACT, [("set_volume", {"percent": "^30$"})]),
    ("assistant", "What alarms do I have now?", HAND, []),
    ("assistant", "Is the AC on at home?", HAND, []),
]

# Small talk, from the S24 runs of 20 Oct 2026 (where every one of these got "One moment." and then silence)
# and like them; and its neighbours that are not small talk. The fifth field is the conversation so far.
IPHONE = [("user", "When was the latest iPhone launched?"),
          ("assistant", "The iPhone 18 Pro was announced on September 9, 2026.")]
CHAT_CASES = [
    ("assistant", "Good morning.", CHAT, [], []),
    ("assistant", "Good night.", CHAT, [], []),
    ("assistant", "How are you?", CHAT, [], []),
    ("assistant", "How's it going?", CHAT, [], []),
    ("assistant", "Hey DUET.", CHAT, [], []),
    ("assistant", "Good afternoon, DUET.", CHAT, [], []),
    ("assistant", "What can you do?", CHAT, [], []),
    ("assistant", "Who are you?", CHAT, [], []),
    ("assistant", "Thank you.", CHAT, [], [("assistant", "Alarm set for 7 in the morning.")]),
    ("assistant", "Thanks a lot, that's really helpful.", CHAT, [], IPHONE),
    ("assistant", "Okay.", CHAT, [], IPHONE),
    ("assistant", "That's great.", CHAT, [], IPHONE),
    ("assistant", "I am not good.", CHAT, [], [("user", "How are you?"),
                                              ("assistant", "I'm doing well, thanks! How about you?")]),
    ("assistant", "I'm doing well, how about you?", CHAT, [], [("assistant", "Good morning! How are you today?")]),
    ("assistant", "I'm so tired today.", CHAT, [], []),
    ("assistant", "I'm bored.", CHAT, [], []),
    ("assistant", "Nothing much, I just want to rest today.", CHAT, [],
     [("user", "Good morning."), ("assistant", "Good morning, Tanmay! Any plans today?")]),
    ("assistant", "Bye, talk to you later.", CHAT, [], []),
    ("assistant", "Okay, good night.", CHAT, [], IPHONE),
    ("drive", "Good evening.", CHAT, [], []),
    ("care", "Good morning, dear.", CHAT, [], []),
    # not small talk: the cloud mind answers, or the fast mind acts
    ("assistant", "What do I do? I have a stomach pain.", HAND, [], [("user", "I am not good."),
                                                                    ("assistant", "I'm sorry to hear that. What's wrong?")]),
    ("assistant", "Can you check when is the next Apple event?", HAND, [], []),
    ("assistant", "When was the latest Samsung phone launched? No, check for the latest iPhone launch.", HAND, [], []),
    ("assistant", "My battery drains super fast these days. What's going on?", HAND, [], []),
    ("assistant", "Tell me a joke.", EITHER, [], []),
    ("assistant", "Yes.", HAND, [], [("assistant", "Should I go ahead and set the brightness to 40 percent?")]),
    ("assistant", "4:30.", HAND, [], [("assistant", "3:30 or 4:30?")]),
    ("assistant", "Okay, do that.", HAND, [], [("assistant", "Would you like me to lower the brightness?")]),
    ("assistant", "Good morning. Set an alarm for 7 tomorrow.", ACT, [("set_alarm", {"time": "@7:00"})], []),
    ("assistant", "Thanks. Now turn on the torch.", ACT, [("set_torch", {"on": "true"})], []),
    ("care", "Good morning. I have a headache today.", HAND, [], []),
    ("assistant", "Hi, what's the weather like today?", HAND, [], []),
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


NAME = {"user": ""}     # the user's name, as the phone app passes it (--name)


async def judge(mode, words, expected, wanted, history=()):
    thinker = fast.make_fast_thinker(MODES[mode]["tools"], MODES[mode]["title"], NAME["user"])
    box = Box(mode, words)
    thinker.vet = lambda calls: fast.vet_plan(calls, words, specs=box.box.specs)   # as the phone app does
    thinker.history = fast.chat_turns(history)                                    # as HybridThinker does
    last = next((t for role, t in reversed(list(history)) if role == "assistant"), "")
    t0 = time.time()
    first = None
    outcome = "silent"
    plain, said, why = False, "", ""
    async for ev in thinker.run(words, box, note="Now: Friday 17 October 2026, 23:50."):
        first = first or time.time() - t0
        if ev.kind == "escalate":
            outcome = "hand"
            said = (ev.args or {}).get("say", "")
        elif ev.kind == "listen":
            outcome = "listen"
        elif ev.kind == "decided" and not ev.tools:
            plain = True
        elif ev.kind == "chat" or (plain and ev.kind == "say"):
            # spoken only if the app's check agrees; otherwise the app hands it to the cloud mind
            said = ev.text
            why = fast.chat_ok(words, ev.text, fast.tool_names(thinker), awaiting=fast.awaiting_answer(last))
            outcome = "hand" if why else "chat"
        elif ev.kind == "error":
            outcome = "error"
    total = time.time() - t0
    acted = [(t, a) for t, a in box.calls if t != fast.ASK_EXPERT]
    if expected == ACT:
        right = len(acted) == len(wanted) and all(
            any(t == wt and all(match_value(p, a.get(k)) for k, p in wa.items()) for t, a in acted)
            for wt, wa in wanted)
        verdict = "ok" if right else ("missed" if not acted else "WRONG ACTION")
    elif acted:
        verdict = "WRONG ACTION"
    elif expected == CHAT:
        verdict = "ok" if outcome == "chat" else "missed"
    elif outcome == "chat" and expected != EITHER:
        verdict = "WRONG CHAT"
    else:
        verdict = "ok"
    return {"mode": mode, "words": words, "expected": expected, "outcome": outcome, "acted": acted,
            "said": said, "why": why, "verdict": verdict, "first_s": round(first or total, 2),
            "total_s": round(total, 2)}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--only", choices=["chat", "actions"], help="only the small-talk cases, or only the others")
    ap.add_argument("--name", default="", help="the user's name, as the phone app passes it")
    args = ap.parse_args()
    NAME["user"] = args.name
    sets = [("dev", c) for c in CASES + RECOGNISER] + [("held out", c) for c in HELD_OUT] + [
        ("chat", c) for c in CHAT_CASES]
    if args.only:
        sets = [(s, c) for s, c in sets if (s == "chat") == (args.only == "chat")]
    rows = []
    for _ in range(args.repeat):
        await fast.warm_up(fast.make_fast_thinker(MODES["assistant"]["tools"], MODES["assistant"]["title"],
                                                  NAME["user"]))
        for part, case in sets:
            r = await judge(*case)
            r["part"] = part
            rows.append(r)
            shown = json.dumps(r["acted"]) if r["acted"] else (('"%s"' % r["said"]) if r["said"] else "")
            print("%-13s %-9s %-7s %5.2fs  %-40s %s%s" % (r["verdict"], r["expected"], r["outcome"], r["total_s"],
                                                         r["words"][:40], shown[:110],
                                                         ("  [%s]" % r["why"]) if r["why"] else ""), flush=True)
    for name in ("dev", "held out", "chat"):
        part = [r for r in rows if r["part"] == name]
        if not part:
            continue
        w = sum(r["verdict"] == "WRONG ACTION" for r in part)
        c = sum(r["verdict"] == "WRONG CHAT" for r in part)
        m = sum(r["verdict"] == "missed" for r in part)
        print("%-9s %d decisions: %d right, %d wrong actions, %d wrong chats, %d missed" % (
            name, len(part), len(part) - w - c - m, w, c, m))
    n = len(rows)
    wrong = [r for r in rows if r["verdict"] in ("WRONG ACTION", "WRONG CHAT")]
    missed = [r for r in rows if r["verdict"] == "missed"]
    acts = [r for r in rows if r["expected"] == ACT and r["verdict"] == "ok"]
    times = sorted(r["total_s"] for r in acts)
    chats = sorted(r["total_s"] for r in rows if r["outcome"] == "chat")
    print("\n%d decisions: %d right, %d wrong (actions or chats), %d missed (handed over)" % (
        n, n - len(wrong) - len(missed), len(wrong), len(missed)))
    if times:
        print("time to act (decision and every tool call): p50 %.2f s, max %.2f s" % (times[len(times) // 2], times[-1]))
    if chats:
        print("time to a small-talk reply: p50 %.2f s, max %.2f s (%d replies)" % (
            chats[len(chats) // 2], chats[-1], len(chats)))
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
