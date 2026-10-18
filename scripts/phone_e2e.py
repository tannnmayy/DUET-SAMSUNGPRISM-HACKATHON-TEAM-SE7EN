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

# the trip is fast-forwarded to N minutes from its destination; DUET speaks first. Done when its offer has
# been said and the conversation has been quiet for 2 s.
SKIP_AND_WAIT = """
(async () => {
  const LIMIT = %(limit)d;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const start = Date.now(), from = window.__ev.length;
  %(action)s
  while (Date.now() - start < LIMIT) {
    await sleep(250);
    const evs = window.__ev.slice(from);
    const offered = evs.some((e) => e.kind === "offer");
    const spoken = evs.filter((e) => e.kind === "agent_said").length;
    const speaking = evs.filter((e) => e.kind === "agent_state").map((e) => e.state).pop() === "speaking";
    const last = evs.length ? evs[evs.length - 1].t : start;
    if (offered && spoken && !speaking && Date.now() - last > 2000) break;
  }
  return {sent: start, events: window.__ev.slice(from)};
})()
"""

# the drill "Drop the connection": done when a new conversation has started and DUET is in the room again
DROP_AND_WAIT = """
(async () => {
  const LIMIT = %(limit)d;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const start = Date.now(), from = window.__ev.length;
  const sessions = window.__ev.filter((e) => e.kind === "session_start").length;
  document.getElementById("drill-drop").click();
  while (Date.now() - start < LIMIT) {
    await sleep(250);
    const r = DuetApp.room;
    const agent = r && r.state === "connected" && [...r.remoteParticipants.values()].length > 0;
    if (agent && window.__ev.filter((e) => e.kind === "session_start").length > sessions)
      return {back_s: (Date.now() - start) / 1000, events: window.__ev.slice(from)};
  }
  return {back_s: null, events: window.__ev.slice(from)};
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
# a number said in digits or in words (spoken times come as "nine oh nine")
NUMBER = r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty)\b"

# the offline drill (the app's button): cut the cloud mind; simple actions go on, the rest is said at once
DRILL = [
    ("drill_ask", "What's the weather in Chennai today?",
     {"drill": True, "offline": True, "say": r"can't reach my cloud mind", "spoken_within_s": 3.0}),
    ("drill_torch", "Turn on the torch.", {"done": [("set_torch", {"on": "true"})]}),
    ("drill_torch2", "Turn the torch off.", {"done": [("set_torch", {"on": "false"})]}),
    # asked twice within minutes, the answer may come from the earlier reading (a known limit): a number is enough
    ("drill_over", "How much battery do I have left?", {"drill": False, "say": r"\d"}),
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


# Drive mode: the destination corrected mid-sentence, the route handed to Google Maps, then the home, a
# message and the music while Maps stays in front
DRIVE = [
    ("destination", "Take me to the office. No wait, the airport, I have a flight.",
     {"done": [("set_destination", {"place": "airport"})], "never": [("set_destination", {"place": "office"})]}),
    ("to_maps", "Hand it to Google Maps.", {"done": [("open_navigation", {})], "after": "maps_front"}),
    ("d_home", "Turn on the AC at home.",
     {"done": [("control_home_device", {"device": "ac|air"})], "after": "home:Air conditioner:on", "behind_maps": True}),
    ("d_music", "Play some music.", {"done": [("control_media", {"action": "play"})], "behind_maps": True}),
    ("d_eta", "When will I get there?", {"reads": ["get_trip_status"], "say": NUMBER}),
    ("d_text", "Tell Priya I'm on my way and when I'll get there.",
     {"done": [("send_message", {"to": "priya", "text": r"\d"})]}),
]


# DUET speaks first: nearly home, it offers to get the home ready (the AC, from SmartThings' state and the
# outside temperature). A plain no runs nothing and is answered at once; a yes with a change runs the change.
ARRIVAL = [
    ("trip_home", "Take me home.", {"done": [("set_destination", {"place": "home"})]}),
    ("offer", None, {"skip_ahead": 10, "offered": True, "say": r"home in about \d+ minutes.*\?"}),
    ("offer_no", "No thanks.", {"say": r"leave it", "never": [("control_home_device", {})], "no_model": True}),
    ("offer2", None, {"skip_ahead": 10, "offered": True, "say": r"AC at 24"}),
    ("offer_yes", "Yes, make it 22.", {"done": [("control_home_device", {"device": "ac|air", "value": "22"})],
                                       "after": "home:Air conditioner:on, 22", "offer_all": True}),
]


# The home reports by itself: the washer finishes, DUET offers a laundry timer (Assistant mode)
WASHER = [
    ("washer", None, {"finish_washer": True, "offered": True, "say": r"washer has finished.*timer"}),
    ("washer_yes", "Yes please.", {"done": [("set_timer", {"minutes": "^10(\.0)?$"})]}),
]


# Skills declared in app/www/skills.json (no code of their own): said, done, and the setting read back
SKILLS = [
    ("rotate_off", "Turn off auto-rotate.", {"done": [("set_auto_rotate", {"on": "false"})],
                                             "after": "setting:accelerometer_rotation:0"}),
    ("text_big", "Make the text on my phone bigger.", {"done": [("set_text_size", {"size": "large|largest"})],
                                                       "after": "setting:font_scale:1.15|1.3"}),
    ("dnd_on", "I'm going into a meeting. Turn on do not disturb.", {"done": [("set_do_not_disturb", {"on": "true"})],
                                                                    "after": "zen:on"}),
    ("camera", "Open the camera.", {"done": [("open_camera", {})], "after": "front:camera"}),
    ("rotate_on", "Turn auto-rotate back on.", {"done": [("set_auto_rotate", {"on": "true"})],
                                                "after": "setting:accelerometer_rotation:1"}),
    ("text_back", "Put the text size back to normal.", {"done": [("set_text_size", {"size": "default"})],
                                                        "after": "setting:font_scale:1.0"}),
    ("dnd_off", "Turn do not disturb off.", {"done": [("set_do_not_disturb", {"on": "false"})], "after": "zen:off"}),
]


# The scalability check: a skill that is not in the app is added to the laptop's skills.json just before the
# conversation starts (no new APK, no restart), used, and removed again afterwards
NEW_SKILL = {"name": "set_touch_vibration", "modes": ["assistant"], "kind": "write", "consent": False,
             "named": "vibrat|haptic", "group": "touch_vibration",
             "description": "Turn the small vibration on taps and key presses on or off.",
             "parameters": {"on": {"type": "boolean", "description": "true to turn touch vibration on."}},
             "required": ["on"], "label": "Touch vibration {on?on:off}", "confirm": "Touch vibration is {on?on:off}.",
             "run": {"type": "setting", "namespace": "system", "key": "haptic_feedback_enabled", "value": "{on?1:0}"},
             "verify": True}
NEWSKILL = [
    ("vibration_off", "Turn off touch vibration.", {"done": [("set_touch_vibration", {"on": "false"})],
                                                    "after": "setting:haptic_feedback_enabled:0"}),
    ("vibration_on", "Turn touch vibration back on.", {"done": [("set_touch_vibration", {"on": "true"})],
                                                       "after": "setting:haptic_feedback_enabled:1"}),
]
SKILLS_FILE = os.path.join(ROOT, "app", "www", "skills.json")


# The drills panel: each failure on demand, and DUET's recovery from it
DRILLS = [
    ("slow_weather", "What's the weather in Chennai today?",
     {"drill": {"slow_cloud_s": 6}, "reads": ["get_weather"], "say": r"still working on it"}),
    ("slow_torch", "Turn on the torch.", {"done": [("set_torch", {"on": "true"})], "max_action_s": 3.0}),
    ("slow_off", "Turn the torch off.", {"drill": {"slow_cloud_s": 0}, "done": [("set_torch", {"on": "false"})]}),
    ("refuses", "Turn on the torch.", {"phone_refuses": True, "failures_ok": True, "no_say": r"torch (is )?on",
                                       "say": r"allow|permission|didn'?t|did not|couldn'?t|could not|wasn'?t able|unable"}),
    ("refuses_off", "Turn on the torch.", {"phone_refuses": False, "done": [("set_torch", {"on": "true"})]}),
    ("drop", None, {"drop": True}),
    ("after_drop", "Turn the torch off.", {"done": [("set_torch", {"on": "false"})]}),
]
DRILLS_HOME = [
    ("home_down", "Turn on the bedroom light.", {"drill": {"home_down": True}, "failures_ok": True,
                                                 "no_say": r"is now on", "say": r"smartthings|reach|couldn'?t|can'?t"}),
    ("home_back", "Turn on the bedroom light.", {"drill": {"home_down": False}, "done": [("control_home_device", {"device": "bedroom"})]}),
]


def top_activity() -> str:
    out = adb("shell", "dumpsys", "activity", "activities")
    m = re.search(r"topResumedActivity=ActivityRecord\{\S+ \S+ ([^/\s]+)", out)
    return m.group(1) if m else ""


async def duet_alarms() -> list:
    r = await cdp_eval("DuetPhone.native.duetAlarmList().then((r) => r.alarms.map((a) => "
                       "String(a.hour).padStart(2, '0') + ':' + String(a.minute).padStart(2, '0')))")
    return r or []


HOME_LIVE = {"ok": False}   # SmartThings answers (else DUET and these checks use the app's simulated home)


async def home_state(device: str) -> str:
    if HOME_LIVE["ok"]:
        st._cache.update(devices=None)
        for d in await st.snapshot():
            if d["name"] == device:
                return d["state"]
        return ""
    # the app's simulated home, as DuetPhone describes it ("on, 22°C cool")
    return await cdp_eval("(() => { const d = DuetPhone.state.home.find((x) => x.name === %s); "
                          "return d ? DuetPhone.describe(d) : ''; })()" % json.dumps(device)) or ""


LAST_OFFER: dict = {"items": []}   # the latest offer DUET made in this run (what a yes takes up)


def match(ev: dict, tool: str, args: dict) -> bool:
    return ev.get("tool") == tool and all(re.search(p, str((ev.get("args") or {}).get(k, "")).lower())
                                         for k, p in args.items())


async def run_case(name: str, text: str, want: dict, limit_s: int = 45) -> dict:
    if "drill" in want:   # the app's drill buttons: cut or slow the cloud mind, cut SmartThings
        msg = want["drill"] if isinstance(want["drill"], dict) else {"cloud_off": bool(want["drill"])}
        await cdp_eval("DuetApp.room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify("
                       "Object.assign({kind: 'drill'}, %s))), {reliable: true, topic: 'duet.phone'}).then(() => true)"
                       % json.dumps(msg), 20)
        await asyncio.sleep(1.0)
    if "phone_refuses" in want:   # the phone-side drill: every action refused, as if a permission were off
        await cdp_eval("(() => { DuetPhone.drill.refuse = %s; return true; })()" % ("true" if want["phone_refuses"] else "false"))
    if want.get("drop"):          # the drill button "Drop the connection": DUET must come back by itself
        out = await cdp_eval(DROP_AND_WAIT % {"limit": limit_s * 1000}, timeout=limit_s + 15)
        evs = out["events"]
        back = out.get("back_s")
        problems = [] if back is not None else ["DID NOT REJOIN"]
        return {"name": name, "text": "(drop)", "ok": not problems, "problems": problems, "route": [],
                "speaks_s": None, "action_s": back, "answer_s": None, "done_s": back, "tools": [],
                "said": "back in %s s" % back, "events": evs}
    if text is None and want.get("skip_ahead"):
        out = await cdp_eval(SKIP_AND_WAIT % {"limit": limit_s * 1000, "action": "DuetPhone.skipAhead(%d);" % want["skip_ahead"]},
                             timeout=limit_s + 15)
    elif text is None and want.get("finish_washer"):
        await st.finish_washer()          # the home reports it; DUET's check of the home notices within seconds
        out = await cdp_eval(SKIP_AND_WAIT % {"limit": limit_s * 1000, "action": ""}, timeout=limit_s + 15)
    else:
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
    for tool, args in want.get("never", []):
        if any(match(e, tool, args) for e in done):
            problems.append("STALE %s %s" % (tool, args))
    for tool in want.get("reads", []):
        if not any(e.get("tool") == tool for e in done):
            problems.append("NOT READ %s" % tool)
    if want.get("no_say") and re.search(want["no_say"], said, re.I):
        problems.append("SAID /%s/ (a claim it must not make)" % want["no_say"])
    if want.get("max_action_s") and (not done or (min(e["t"] for e in done) - sent) / 1000 > want["max_action_s"]):
        problems.append("ACTION SLOWER THAN %s s" % want["max_action_s"])
    if want.get("say") and not re.search(want["say"], said, re.I):
        problems.append("DID NOT SAY /%s/" % want["say"])
    if want.get("offered") and not any(e.get("kind") == "offer" for e in evs):
        problems.append("NO OFFER")
    for e in evs:
        if e.get("kind") == "offer":
            LAST_OFFER["items"] = e.get("items") or []
    if want.get("offer_all"):
        # a yes that changed one part takes up the whole offer: every offered device was acted on (18 Oct
        # 2026: "AC at 24 and the bedroom light?" "Yes, make it 22." set the AC and left the light out)
        for item in LAST_OFFER["items"]:
            dev = str((item.get("args") or {}).get("device", "")).lower()
            if not any(e.get("tool") == item.get("tool") and dev in str((e.get("args") or {}).get("device", "")).lower()
                       for e in done):
                problems.append("OFFER PART LEFT OUT: %s" % item.get("label"))
    if want.get("no_model") and any(e.get("kind") == "route" and e.get("where") == "cloud" for e in evs):
        problems.append("A PLAIN NO WENT TO THE CLOUD MIND")
    for e in failed if not want.get("failures_ok") else []:
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
    if after.startswith("setting:"):
        _, key, want_v = after.split(":", 2)
        got = adb("shell", "settings", "get", "system", key).strip()
        if not re.fullmatch(want_v, got):
            problems.append("SETTING %s IS %s" % (key, got))
    if after.startswith("zen:"):
        zen = adb("shell", "settings", "get", "global", "zen_mode").strip()
        if (zen != "0") != (after == "zen:on"):
            problems.append("DO NOT DISTURB zen_mode=%s" % zen)
    if after == "front:camera":
        await asyncio.sleep(2.0)
        if "camera" not in top_activity():
            problems.append("THE CAMERA IS NOT IN FRONT (%s)" % top_activity())
        adb("shell", "am", "start", "-n", PKG + "/.MainActivity")       # back to DUET for the next request
        await asyncio.sleep(2.0)
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
    if args.mode != "newskill":
        return await run_all(args)
    original = open(SKILLS_FILE, encoding="utf-8").read()
    data = json.loads(original)
    data["skills"] = [s for s in data["skills"] if s["name"] != NEW_SKILL["name"]] + [NEW_SKILL]
    with open(SKILLS_FILE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    step("a new skill in the laptop's skills.json", True, "%s (no new app, no restart)" % NEW_SKILL["name"])
    try:
        return await run_all(args)
    finally:
        with open(SKILLS_FILE, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(original)


async def run_all(args) -> int:
    os.makedirs(OUT, exist_ok=True)
    HOME_LIVE["ok"] = st.enabled() and bool((await st.check()).get("ok"))
    if HOME_LIVE["ok"]:   # the SmartThings home starts every run in the same state
        from smartthings_setup import reset
        await reset(await st.location_id())
        await asyncio.sleep(1.5)
    else:
        step("SmartThings", True, "not available: the app's simulated home is used and checked")
        if args.mode == "washer":
            step("end to end", True, "0 of 0 cases · skipped: the washer offer needs the SmartThings home")
            return 0
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
    if not HOME_LIVE["ok"]:   # the app's simulated home starts every run in the same state too
        await cdp_eval("(() => { DuetPhone.reset(); return true; })()")
    await cdp_eval("""(() => { window.__ev = []; DuetApp.hooks.events.push((e) => window.__ev.push(Object.assign({t: Date.now()}, e)));
        DuetApp.start('%s'); return true; })()""" % {"arrival": "drive", "washer": "assistant", "skills": "assistant", "newskill": "assistant",
         "drills": "assistant"}.get(args.mode, args.mode))
    greeted = await cdp_eval("""(async () => { for (let i = 0; i < 120; i++) { if (window.__ev.some((e) => e.kind === 'agent_said')) return window.__ev.find((e) => e.kind === 'session_start') || true;
        await new Promise((r) => setTimeout(r, 500)); } return false; })()""", timeout=80)
    if not step("conversation started, DUET greeted", bool(greeted), json.dumps(greeted)[:160] if greeted else "no greeting in 60 s"):
        return 1
    # typed requests only: the device's microphone (on the emulator, the laptop's) must not add words
    await cdp_eval("DuetApp.room.localParticipant.setMicrophoneEnabled(false).then(() => true)", 20)
    results = []
    cases = {"drive": DRIVE, "arrival": ARRIVAL, "washer": WASHER, "skills": SKILLS, "newskill": NEWSKILL,
             "drills": DRILLS + (DRILLS_HOME if HOME_LIVE["ok"] else [])}.get(
        args.mode, CASES + DRILL + ([] if args.no_maps else MAPS))
    for name, text, want in cases:
        if want.get("behind_maps") and "maps" not in top_activity():
            await asyncio.sleep(1.0)
        r = await run_case(name, text, want)
        results.append(r)
        step("%-12s %s" % (name, (text or ("(the trip: %d min from home)" % want["skip_ahead"] if want.get("skip_ahead")
                                           else "(the connection is dropped)" if want.get("drop")
                                           else "(the washer finishes)"))[:44]), r["ok"], "%s · speaks %ss · action %ss · answer %ss · %s · said: %s" % (
            "/".join(x for x in r["route"] if x) or "-", r["speaks_s"], r["action_s"], r["answer_s"],
            "; ".join(r["problems"]) or ", ".join(r["tools"]), r["said"][:110]))
        await asyncio.sleep(1.5)
    adb("shell", "am", "start", "-n", PKG + "/.MainActivity")       # DUET back in front
    await asyncio.sleep(2.0)
    try:
        await cdp_eval("DuetApp.end().then(() => true)", 20)
    except Exception:
        pass
    if adb("shell", "getprop", "ro.kernel.qemu").strip() == "1" and any(
            e.get("tool") == "set_timer" for r in results for e in r.get("events", []) if e.get("kind") == "tool_done"):
        # the emulator only: the test's timers would ring on (18 Oct 2026: 14 expired timers ringing at once).
        # Never on a real phone: stopping the Clock there would also cancel its owner's own alarms
        adb("shell", "am", "force-stop", "com.google.android.deskclock")
    path = os.path.join(OUT, "e2e_%s.json" % time.strftime("%m%d_%H%M%S"))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"device": adb("shell", "getprop", "ro.product.model"), "results": results}, fh, indent=1)
    passed = sum(r["ok"] for r in results)
    step("end to end", passed == len(results), "%d of %d cases · %s" % (passed, len(results), path))
    await st.close()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
