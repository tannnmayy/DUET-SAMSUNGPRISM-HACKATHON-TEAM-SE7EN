"""DUET for Galaxy end to end on an Android device (the S24, or the emulator): the real app, the real
agent and both minds, Android's own actions and the SmartThings home, also while Google Maps is in front.

    python scripts/phone_e2e.py                 # every case
    python scripts/phone_e2e.py --no-maps       # skip the cases behind Google Maps

Needs the stack running (bash scripts/galaxy_demo.sh) and DUET installed (scripts/phone_check.py). The
requests are typed into the live conversation instead of spoken, so the test covers everything after the
ears: the fast and cloud minds, the coordinator, LiveKit to the phone, the phone's actions, SmartThings,
and the app's timeline. The ears and the voice are covered by scripts/galaxy_suite.py, and by talking.
Each case checks what reached the phone (or the home), and what was actually changed there.
Results: results/galaxy/phone/e2e_<time>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))

from phone_check import ADB, OUT, PKG, adb, cdp_eval, step  # noqa: E402
from duet_voice.galaxy import smartthings as st  # noqa: E402

SEND_AND_WAIT = """
(async () => {
  const LIMIT = %(limit)d, text = %(text)s;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const start = Date.now(), from = window.__ev.length;
  await DuetApp.room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify({kind: "text", text})),
    {reliable: true, topic: "duet.phone"});
  while (Date.now() - start < LIMIT) {
    await sleep(250);
    const evs = window.__ev.slice(from);
    // done when DUET has finished speaking its reply (agent_said comes when the speech has played out)
    const answered = evs.some((e) => e.kind === "thinker_say" || e.kind === "thinker_error");
    const spoken = evs.filter((e) => e.kind === "agent_said").length;
    const speaking = evs.filter((e) => e.kind === "agent_state").map((e) => e.state).pop() === "speaking";
    const last = evs.length ? evs[evs.length - 1].t : start;
    if (answered && spoken && !speaking && Date.now() - last > 2000) break;
  }
  return {sent: start, events: window.__ev.slice(from)};
})()
"""

# (name, typed request, checks) - checks: tools that must be done (with argument patterns), tools that
# must not be done, a regex the spoken words must match, and a check of the phone or home afterwards
CASES = [
    ("alarm", "Set an alarm for 6:30 tomorrow morning.",
     {"done": [("set_alarm", {"time": "^06:30$"})], "after": "alarm_has:06:30"}),
    ("alarm_cancel", "Actually, cancel that alarm.",
     {"done": [("cancel_alarm", {"time": "^06:30$"})], "after": "alarm_gone:06:30"}),
    ("torch", "Turn on the torch.", {"done": [("set_torch", {"on": "true"})]}),
    ("torch_off", "Turn the torch off.", {"done": [("set_torch", {"on": "false"})]}),
    ("volume", "Set the volume to 40 percent.", {"done": [("set_volume", {"percent": "^40$"})]}),
    ("home_ac", "Turn on the AC at home and set it to 22 degrees.",
     {"done": [("control_home_device", {"device": "ac|air"})], "after": "home:Air conditioner:on, 22"}),
    ("home_read", "Is the front door locked?", {"reads": ["get_home_status"], "say": r"lock"}),
    ("weather", "What's the weather in Chennai today?", {"reads": ["get_weather"], "say": r"\d"}),
    ("apps", "How many apps do I have on this phone?", {"reads": ["list_installed_apps"], "say": r"\d"}),
    ("battery", "How much battery do I have left?", {"reads": ["get_battery_report"], "say": r"\d"}),
]
# the offline drill (the app's button): cut the cloud mind; simple actions go on, the rest is said at once
DRILL = [
    ("drill_ask", "What's the weather in Chennai today?",
     {"drill": True, "offline": True, "say": r"can't reach my cloud mind", "spoken_within_s": 3.0}),
    ("drill_torch", "Turn on the torch.", {"done": [("set_torch", {"on": "true"})]}),
    ("drill_torch2", "Turn the torch off.", {"done": [("set_torch", {"on": "false"})]}),
    ("drill_over", "How much battery do I have left?", {"drill": False, "reads": ["get_battery_report"], "say": r"\d"}),
]
MAPS = [
    ("navigate", "Navigate to the airport.", {"done": [("navigate_to", {"destination": "airport"})], "after": "maps_front"}),
    ("bg_home", "Turn off the AC at home.",
     {"done": [("control_home_device", {"device": "ac|air"})], "after": "home:Air conditioner:off", "behind_maps": True}),
    ("bg_alarm", "Set an alarm for 7 tomorrow morning.",
     {"done": [("set_alarm", {"time": "^07:00$"})], "after": "alarm_has:07:00", "behind_maps": True}),
    ("bg_timer", "Set a timer for 5 minutes.", {"done": [("set_timer", {"minutes": "^5(\\.0)?$"})], "behind_maps": True}),
    ("bg_cancel", "Cancel the 7 o'clock alarm.",
     {"done": [("cancel_alarm", {"time": "^07:00$"})], "after": "alarm_gone:07:00", "behind_maps": True}),
]


def top_activity() -> str:
    out = adb("shell", "dumpsys", "activity", "activities")
    m = re.search(r"topResumedActivity=ActivityRecord\{\S+ \S+ ([^/\s]+)", out)
    return m.group(1) if m else ""


async def duet_alarms() -> list:
    r = await cdp_eval("DuetPhone.native.duetAlarmList().then((r) => r.alarms.map((a) => "
                       "String(a.hour).padStart(2, '0') + ':' + String(a.minute).padStart(2, '0')))")
    return r or []


async def home_state(device: str) -> str:
    st._cache.update(devices=None)
    for d in await st.snapshot():
        if d["name"] == device:
            return d["state"]
    return ""


def match(ev: dict, tool: str, args: dict) -> bool:
    return ev.get("tool") == tool and all(re.search(p, str((ev.get("args") or {}).get(k, "")).lower())
                                         for k, p in args.items())


async def run_case(name: str, text: str, want: dict, limit_s: int = 45) -> dict:
    if "drill" in want:   # the app's drill button: cut (or restore) the cloud mind
        await cdp_eval("DuetApp.room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify("
                       "{kind: 'drill', cloud_off: %s})), {reliable: true, topic: 'duet.phone'}).then(() => true)"
                       % ("true" if want["drill"] else "false"), 20)
        await asyncio.sleep(1.0)
    out = await cdp_eval(SEND_AND_WAIT % {"limit": limit_s * 1000, "text": json.dumps(text)}, timeout=limit_s + 15)
    evs, sent = out["events"], out["sent"]
    done = [e for e in evs if e.get("kind") == "tool_done"]
    failed = [e for e in evs if e.get("kind") in ("tool_failed", "tool_unknown", "tool_asked", "tool_dropped")]
    said = " ".join(e.get("text", "") for e in evs if e.get("kind") == "agent_said")
    route = [e.get("where") for e in evs if e.get("kind") == "route"]
    problems = []
    for tool, args in want.get("done", []):
        if not any(match(e, tool, args) for e in done):
            problems.append("NOT DONE %s %s" % (tool, args))
    for tool in want.get("reads", []):
        if not any(e.get("tool") == tool for e in done):
            problems.append("NOT READ %s" % tool)
    if want.get("say") and not re.search(want["say"], said, re.I):
        problems.append("DID NOT SAY /%s/" % want["say"])
    for e in failed:
        problems.append("%s %s %s" % (e["kind"].upper(), e.get("tool"), json.dumps(e.get("result") or e.get("error") or "")[:120]))
    errors = [e for e in evs if e.get("kind") == "thinker_error"]
    if want.get("offline"):
        if not any(re.search(r"cannot reach|offline drill", e.get("text", "")) for e in errors):
            problems.append("NO OFFLINE ANSWER")
    elif errors:
        problems.append("MODEL ERROR")
    if want.get("spoken_within_s") and want.get("say"):
        # the honest line itself (not just "One moment.") must have started within the limit
        line_t = [e["t"] for e in evs if e.get("kind") == "thinker_error"]
        if not line_t or (line_t[0] - sent) / 1000 > want["spoken_within_s"]:
            problems.append("OFFLINE ANSWER TOO LATE")
    after = want.get("after", "")
    if after.startswith("alarm_has:") and after.split(":", 1)[1] not in await duet_alarms():
        problems.append("ALARM NOT SET ON THE PHONE")
    if after.startswith("alarm_gone:") and after.split(":", 1)[1] in await duet_alarms():
        problems.append("ALARM STILL SET ON THE PHONE")
    if after.startswith("home:"):
        _, device, state = after.split(":", 2)
        now = await home_state(device)
        if not now.startswith(state):
            problems.append("SMARTTHINGS SHOWS %s: %s" % (device, now))
    if after == "maps_front":
        await asyncio.sleep(2.0)
        if "maps" not in top_activity():
            problems.append("GOOGLE MAPS NOT IN FRONT (%s)" % top_activity())
    if want.get("behind_maps") and "maps" not in top_activity():
        problems.append("DUET WAS NOT BEHIND MAPS (%s)" % top_activity())
    first_done = min((e["t"] for e in done), default=None)
    rel = lambda t: round((t - sent) / 1000, 2) if t else None  # noqa: E731
    talk = [e["t"] for e in evs if e.get("kind") == "agent_state" and e.get("state") == "speaking"]
    ready = [e["t"] for e in evs if e.get("kind") in ("confirmed",) or (e.get("kind") == "thinker_say" and "<silent>" not in (e.get("text") or ""))]
    return {"name": name, "text": text, "ok": not problems, "problems": problems, "route": route,
            "speaks_s": rel(min(talk)) if talk else None,          # DUET's first sound for this request
            "action_s": rel(first_done),                           # the action ran on the phone or home
            "answer_s": rel(min(ready)) if ready else None,        # the answer (or confirmation) was ready to say
            "done_s": rel(max(e["t"] for e in evs)) if evs else None,
            "tools": ["%s(%s)%s" % (e["tool"], json.dumps(e.get("args")), " [SmartThings]" if (e.get("result") or {}).get("source") == "SmartThings" else "")
                      for e in done],
            "said": said[:300], "events": evs}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-maps", action="store_true")
    ap.add_argument("--mode", default="assistant")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if st.enabled():   # the SmartThings home starts every run in the same state
        from smartthings_setup import reset
        await reset(await st.location_id())
        await asyncio.sleep(1.5)
    adb("reverse", "tcp:8787", "tcp:8787")
    adb("shell", "am", "force-stop", PKG)
    adb("shell", "am", "start", "-n", PKG + "/.MainActivity")
    for _ in range(60):
        try:
            if await cdp_eval("typeof DuetApp === 'object' && typeof DuetPhone === 'object' && DuetPhone.NATIVE", 10):
                break
        except Exception:
            pass
        await asyncio.sleep(1.0)
    await cdp_eval("""(() => { const s = JSON.parse(localStorage.getItem('duet.settings') || '{}');
        Object.assign(s, {server: 'localhost:8787', name: 'Tanmay', city: 'Chennai'}); delete s.simulate;
        localStorage.setItem('duet.settings', JSON.stringify(s)); location.reload(); return true; })()""")
    await asyncio.sleep(4.0)
    for _ in range(30):
        try:
            if await cdp_eval("typeof DuetApp === 'object'", 10):
                break
        except Exception:
            pass
        await asyncio.sleep(1.0)
    await cdp_eval("""(() => { window.__ev = []; DuetApp.hooks.events.push((e) => window.__ev.push(Object.assign({t: Date.now()}, e)));
        DuetApp.start('%s'); return true; })()""" % args.mode)
    greeted = await cdp_eval("""(async () => { for (let i = 0; i < 120; i++) { if (window.__ev.some((e) => e.kind === 'agent_said')) return window.__ev.find((e) => e.kind === 'session_start') || true;
        await new Promise((r) => setTimeout(r, 500)); } return false; })()""", timeout=80)
    if not step("conversation started, DUET greeted", bool(greeted), json.dumps(greeted)[:160] if greeted else "no greeting in 60 s"):
        return 1
    # typed requests only: the device's microphone (on the emulator, the laptop's) must not add words
    await cdp_eval("DuetApp.room.localParticipant.setMicrophoneEnabled(false).then(() => true)", 20)
    results = []
    for name, text, want in CASES + DRILL + ([] if args.no_maps else MAPS):
        if want.get("behind_maps") and "maps" not in top_activity():
            await asyncio.sleep(1.0)
        r = await run_case(name, text, want)
        results.append(r)
        step("%-12s %s" % (name, text[:44]), r["ok"], "%s · speaks %ss · action %ss · answer %ss · %s · said: %s" % (
            "/".join(x for x in r["route"] if x) or "-", r["speaks_s"], r["action_s"], r["answer_s"],
            "; ".join(r["problems"]) or ", ".join(r["tools"]), r["said"][:110]))
        await asyncio.sleep(1.5)
    adb("shell", "am", "start", "-n", PKG + "/.MainActivity")       # DUET back in front
    await asyncio.sleep(2.0)
    try:
        await cdp_eval("DuetApp.end().then(() => true)", 20)
    except Exception:
        pass
    path = os.path.join(OUT, "e2e_%s.json" % time.strftime("%m%d_%H%M%S"))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"device": adb("shell", "getprop", "ro.product.model"), "results": results}, fh, indent=1)
    passed = sum(r["ok"] for r in results)
    step("end to end", passed == len(results), "%d of %d cases · %s" % (passed, len(results), path))
    await st.close()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
