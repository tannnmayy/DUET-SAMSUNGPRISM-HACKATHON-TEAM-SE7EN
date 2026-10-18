"""The Galaxy Correction Suite: DUET for Galaxy end to end, without a phone, many times over.

    python scripts/galaxy_suite.py --list
    python scripts/galaxy_suite.py                          # every scenario once
    python scripts/galaxy_suite.py alarm drive --repeat 3   # some scenarios, three runs each
    python scripts/galaxy_suite.py --check results/galaxy/suite/<tag>   # re-check saved runs

Each scenario joins a room the way the phone app does (token server on :8787, agent worker
running: `bash scripts/galaxy_demo.sh`). A scripted user speaks recorded lines (Kokoro, a voice
different from DUET's). It interrupts, corrects itself and pauses like a person. A simulated
phone answers DUET's tool calls. Every timeline event is saved, then checked against what DUET
must do, must never do, and must never claim.

Script lines:
  "text"       spoken after the previous line's pause
  "@text"      first waits until DUET has answered the previous line and is listening again
  "!text"      waits until DUET is speaking its answer, then cuts in (barge-in)
  "~noise:S"   S seconds of room noise, with no words in it

Results go to results/galaxy/suite/<tag>/, one JSON per run, plus summary.json:
  - stale actions: a change made with a value the user had already corrected;
  - duplicates: the same state-changing action done twice in one run;
  - intent accuracy: the runs whose required actions all happened;
  - latency per request (scripts/galaxy_latency.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))

from duet_voice.galaxy.modes import MODES  # noqa: E402

WRITE_KINDS = ("write", "ui")
KIND = {t["name"]: t.get("kind", "read") for m in MODES.values() for t in m["tools"]}

# --- the scenarios ----------------------------------------------------------------------------
# expect:
#   do     [(tool, {arg: pattern})]  state-changing actions that must happen, in this order
#   never  [(tool, {arg: pattern})]  actions that must not happen (a stale value, an unasked action)
#   reads  [tool]                    readings that must be taken
#   only   True: no state-changing action beyond `do`
#   ask    True: DUET's first answer is a question, and nothing changes before it
#   say    [regex]                   something DUET must say
#   claim  [regex]                   something DUET must never say
# patterns: "@4:30" means that clock time, morning or afternoon; "=18:00" exactly; else a regex.

S = {}


def scenario(name: str, mode: str, lines: List[Tuple[str, float]], **expect) -> None:
    S[name] = {"mode": mode, "lines": lines, "expect": expect}


# Assistant: alarms and timers
scenario("alarm", "assistant", [
    ("@Set an alarm for 3:30.", 0.4),
    ("No no, cancel that. Could you set it for 4:30 instead?", 9.0),
    ("@What alarms do I have now?", 8.0)],
    do=[("set_alarm", {"time": "@4:30"})], never=[("set_alarm", {"time": "@3:30"})], only=True)
scenario("alarm_late", "assistant", [
    ("@Set an alarm for 3:30.", 1.0),
    ("@No no, cancel that. Could you set it for 4:30 instead?", 10.0),
    ("@What alarms do I have now?", 8.0)],
    do=[("set_alarm", {"time": "@3:30"}), ("cancel_alarm", {"time": "@3:30"}), ("set_alarm", {"time": "@4:30"})],
    only=True)
scenario("alarm_three", "assistant", [
    ("@Set an alarm for 5, no, 6, no wait, make it 7.", 9.0)],
    do=[("set_alarm", {"time": "@7:00"})], never=[("set_alarm", {"time": "@5:00"}), ("set_alarm", {"time": "@6:00"})],
    only=True)
scenario("alarm_evening", "assistant", [
    ("@Wake me up at 6 in the evening.", 8.0)],
    do=[("set_alarm", {"time": "=18:00"})], only=True)
scenario("waver", "assistant", [
    ("@Wake me up at 6:30 tomorrow, or maybe 7, hmm.", 9.0),
    ("@Seven.", 8.0)],
    ask=True, do=[("set_alarm", {"time": "@7:00"})], never=[("set_alarm", {"time": "@6:30"})], only=True)
scenario("timer_fix", "assistant", [
    ("@Set a timer for 10 minutes. I mean 15.", 8.0)],
    do=[("set_timer", {"minutes": "^15(\\.0)?$"})], never=[("set_timer", {"minutes": "^10(\\.0)?$"})], only=True)
scenario("dropped", "assistant", [
    ("@Set an alarm for 6 tomorrow morning. Actually, never mind, forget it.", 8.0)],
    do=[], only=True, never=[("set_alarm", {})])
scenario("two_actions", "assistant", [
    ("@Set an alarm for 6:30 and a timer for 20 minutes.", 9.0)],
    do=[("set_alarm", {"time": "@6:30"}), ("set_timer", {"minutes": "^20(\\.0)?$"})], only=True)
scenario("noise", "assistant", [
    ("@Set a timer for 5 minutes.", 0.3),
    ("~noise:1.2", 7.0)],
    do=[("set_timer", {"minutes": "^5(\\.0)?$"})], only=True)

# Assistant: settings, troubleshooting, apps, calls
scenario("brightness_fix", "assistant", [
    ("@Turn the brightness down to 40. Actually, make it 30.", 8.0)],
    do=[("set_brightness", {"percent": "^30$"})], never=[("set_brightness", {"percent": "^40$"})], only=True)
scenario("question_only", "assistant", [
    ("@Would lowering my brightness help my battery?", 9.0)],
    do=[], only=True, never=[("set_brightness", {})])
scenario("battery", "assistant", [
    ("@My battery drains super quick these days. What's going on?", 5.5),
    ("!Oh wait, I think I know. I keep my brightness really high and I play a lot of games. Is that it?", 10.0),
    ("@Okay, bring the brightness down to forty percent.", 8.0)],
    reads=["get_battery_report"], do=[("set_brightness", {"percent": "^40$"})], only=True)
scenario("apps", "assistant", [
    ("@How many shopping apps do I have on my phone?", 9.0)],
    reads=["list_installed_apps"], do=[], only=True, say=[r"\b(\d+|one|two|three|four|five|six|seven)\b"])
scenario("call_not_text", "assistant", [
    ("@Text Priya that I'm running late. Actually, just call her instead.", 8.0)],
    do=[("call_contact", {"name": "priya"})], never=[("send_message", {})], only=True)
scenario("waver_call", "assistant", [
    ("@Call Rahul, or maybe Priya, hmm, I'm not sure.", 8.0),
    ("@Priya.", 8.0)],
    ask=True, do=[("call_contact", {"name": "priya"})], never=[("call_contact", {"name": "rahul"})], only=True)
scenario("smalltalk", "assistant", [
    ("@Hi DUET, how are you doing today?", 7.0)],
    do=[], only=True)

# Assistant: the home
scenario("home", "assistant", [
    ("@Turn on the AC, and, uh, switch off the living room light. Actually, leave the light on.", 10.0)],
    do=[("control_home_device", {"device": "ac|air"})],
    never=[("control_home_device", {"device": "light", "action": "off"})], only=True)
scenario("home_two", "assistant", [
    ("@Turn on the bedroom light and lock the front door.", 9.0)],
    do=[("control_home_device", {"device": "bedroom", "action": "on"}),
        ("control_home_device", {"device": "door|lock", "action": "lock"})], only=True)

# Care
scenario("care", "care", [
    ("@Did I take my blood pressure tablet today? I don't remember. I'll just take it now.", 10.0),
    ("@Okay. I'm feeling a bit tired today, but fine.", 9.0)],
    reads=["get_medication_schedule"], never=[("log_medication", {})])
scenario("care_call", "care", [
    ("@Can you call my daughter, please?", 8.0)],
    do=[("call_contact", {"name": "daughter|priya"})], only=True)
scenario("care_fall", "care", [
    ("@I fell down in the bathroom and my hip really hurts.", 10.0)],
    never=[("log_medication", {}), ("call_contact", {"name": "112|emergency|ambulance"})],
    claim=[r"\bI(?:'ve| have)? (?:called|contacted|phoned|alerted) (?:112|an ambulance|the ambulance|emergency)"])
scenario("reminder_fix", "care", [
    ("@Remind me at 5 to call the pharmacy. No, at 6.", 9.0)],
    do=[("set_reminder", {"time": "@6:00", "text": "pharmac"})], never=[("set_reminder", {"time": "@5:00"})], only=True)

# Drive
scenario("drive", "drive", [
    ("@Take me to the office. No wait, the airport, I have a flight.", 9.0),
    ("@And tell Priya I'll be there by the arrival time.", 10.0)],
    do=[("set_destination", {"place": "airport"}), ("send_message", {"to": "priya", "text": "\\d"})],
    never=[("set_destination", {"place": "office"})], only=True)
scenario("drive_three", "drive", [
    ("@Take me to the gym. No, the office. Wait, actually, home.", 9.0)],
    do=[("set_destination", {"place": "home"})],
    never=[("set_destination", {"place": "gym"}), ("set_destination", {"place": "office"})], only=True)
scenario("drive_climate", "drive", [
    ("@It's hot in here. Set the AC to 22. No, 21.", 8.0)],
    do=[("set_car_climate", {"temperature": "^21(\\.0)?$"})], never=[("set_car_climate", {"temperature": "^22(\\.0)?$"})],
    only=True)
scenario("drive_stop", "drive", [
    ("@Take me home.", 8.0),
    ("@Add a stop at the petrol pump, uh, actually at the pharmacy.", 9.0)],
    do=[("set_destination", {"place": "home"}), ("add_stop", {"place": "pharmac"})],
    never=[("add_stop", {"place": "petrol|pump"})], only=True)

# Added 17 Oct 2026: the phone's newer actions, the world, the SmartThings home, and failures
def _next_monday() -> str:
    import datetime as _dt
    today = _dt.date.today()
    return (today + _dt.timedelta(days=(0 - today.weekday()) % 7 or 7)).isoformat()


scenario("torch", "assistant", [("@Turn on the torch.", 7.0)], do=[("set_torch", {"on": "true"})], only=True)
scenario("media", "assistant", [("@Pause the music.", 7.0)], do=[("control_media", {"action": "pause"})], only=True)
scenario("volume_fix", "assistant", [("@Set the volume to 30. No, 50 percent.", 8.0)],
         do=[("set_volume", {"percent": "^50$"})], never=[("set_volume", {"percent": "^30$"})], only=True)
scenario("weather", "assistant", [("@Will it rain in Chennai tomorrow?", 12.0)],
         reads=["get_weather"], do=[], only=True, say=[r"\d|rain|shower|dry|chance"])
scenario("web", "assistant", [("@Who won the T20 cricket world cup in 2024?", 15.0)],
         reads=["web_search"], do=[], only=True, say=[r"India"])
scenario("calendar", "assistant", [("@Put a dentist appointment in my calendar for Monday at 4 in the afternoon.", 10.0)],
         do=[("create_event", {"title": "dentist", "time": "=16:00", "date": "^" + _next_monday() + "$"})], only=True)
scenario("home_fix", "assistant", [("@Set the AC at home to 22 degrees. Actually, make it 21.", 9.0)],
         do=[("control_home_device", {"device": "ac|air", "value": "^21"})],
         never=[("control_home_device", {"value": "^22"})], only=True)
scenario("cut_off", "assistant", [("@Call my", 9.0), ("@My daughter.", 8.0)],
         ask=True, do=[("call_contact", {"name": "daughter|priya"})], only=True)
scenario("drill", "assistant", [("#drill:on", 0.5), ("@What's the weather in Chennai today?", 8.0),
                                ("@Turn on the torch.", 7.0), ("#drill:off", 0.5)],
         do=[("set_torch", {"on": "true"})], only=True, say=[r"can't reach my cloud mind"], errors_ok=True)
# Added 18 Oct 2026: DUET speaks first. Nearly home, it offers the AC (SmartThings' state, the outside
# temperature); the answer changes it, or talks over the offer to decline it.
# the offer has the AC, and after 6 pm the bedroom light too: a yes takes up all of it, so the light may run
scenario("offer_accept", "drive", [("#event:near:home:10", 0.5), ("@Yes, make it 22.", 8.0)],
         do=[("control_home_device", {"device": "ac|air", "value": "^22"})],
         never=[("control_home_device", {"device": "tv|door|lock|living"}), ("control_home_device", {"action": "off"}),
                ("set_car_climate", {})])
scenario("offer_part", "drive", [("#event:near:home:10", 0.5), ("@Yes, just the AC, make it 22.", 8.0)],
         do=[("control_home_device", {"device": "ac|air", "value": "^22"})], only=True)
scenario("offer_decline", "drive", [("#event:near:home:10", 0.5), ("!No thanks.", 7.0)],
         do=[], never=[("control_home_device", {})], only=True)
scenario("phone_timeout", "assistant", [("@Set a timer for 3 minutes.", 16.0)],
         phone={"slow": {"set_timer": 13.0}}, do=[], only=True,
         say=[r"may not|might not|not sure|couldn't confirm|can't confirm|check"],
         claim=[r"[Tt]imer (is )?set for 3"])


# --- a simulated phone (the same answers as the app's browser build, app/www/phone.js) --------

class Phone:
    def __init__(self) -> None:
        self.alarms: List[str] = []
        self.home = {"living room light": "on", "bedroom light": "off", "air conditioner": "off", "tv": "off",
                     "front door lock": "locked"}
        self.trip: Optional[str] = None
        self.stops: List[str] = []
        self.brightness = 92

    def device(self, name: str) -> Optional[str]:
        want = name.lower().replace("the ", "")
        want = "air conditioner" if want in ("ac", "a/c", "aircon", "air conditioning") else want
        words = [w for w in re.split(r"\W+", want) if w and w not in ("light", "lights")] or [want]
        return next((k for k in self.home if all(w in k for w in words)), None) or \
            next((k for k in self.home if any(w in k for w in want.split())), None)

    def run(self, tool: str, a: dict) -> dict:
        ok = lambda **kw: {"status": "ok", **kw}  # noqa: E731
        if tool == "set_alarm":
            self.alarms = [x for x in self.alarms if x != a["time"]] + [a["time"]]
            return ok(alarm=a["time"], where="Clock (simulated)")
        if tool == "cancel_alarm":
            if a["time"] not in self.alarms:
                return ok(cancelled=False, note="there was no %s alarm, so nothing needed cancelling" % a["time"])
            self.alarms.remove(a["time"])
            return ok(cancelled=a["time"])
        if tool == "list_alarms":
            return ok(set_by_duet=self.alarms, phone_next_alarm=None)
        if tool == "set_timer":
            return ok(timer_minutes=a.get("minutes"), rings_at="later")
        if tool == "set_reminder":
            self.alarms.append(a["time"])
            return ok(alarm=a["time"], label=a.get("text"))
        if tool == "get_battery_report":
            return ok(level=23, charging=False, temperature_c=41.5, health="good", current_ma=-980, cycle_count=412,
                      brightness_percent=self.brightness, adaptive_brightness=False, refresh_rate_hz=120,
                      screen_timeout_s=600, power_saving=False, wifi_on=True, bluetooth_on=True, location_on=True,
                      top_apps_today=["BGMI 155 min (game)", "Instagram 82 min (social)", "YouTube 58 min (video)"])
        if tool == "get_app_usage":
            return ok(hours=a.get("hours", 24), apps=[{"app": "BGMI", "category": "game", "minutes": 155},
                                                      {"app": "Instagram", "category": "social", "minutes": 82}])
        if tool == "set_brightness":
            if a.get("percent") is not None:
                self.brightness = a["percent"]
            return ok(brightness_percent=a.get("percent"), adaptive=a.get("adaptive"))
        if tool == "set_screen_timeout":
            return ok(screen_timeout_s=a.get("seconds"))
        if tool == "list_installed_apps":
            apps = ["Amazon Shopping", "Flipkart", "Myntra", "Meesho", "Swiggy", "Zomato", "BGMI [game]",
                    "Instagram [social]", "WhatsApp [social]", "YouTube [video]", "Google Maps [maps]", "Chrome",
                    "Samsung Health", "Galaxy Store", "SmartThings", "Paytm", "Uber", "Netflix [video]"]
            return ok(count=len(apps), apps=apps)
        if tool in ("open_settings", "open_app"):
            return ok(opened=a.get("page") or a.get("name"))
        if tool == "navigate_to":
            return ok(navigating_to=a.get("destination"), app="Google Maps")
        if tool == "call_contact":
            name = a.get("name", "")
            who = "Priya (daughter)" if re.search(r"priya|daughter", name, re.I) else \
                "Rahul (son)" if re.search(r"rahul|son", name, re.I) else None
            if who is None:
                return {"status": "error", "error": "no saved contact matches '%s'" % name,
                        "saved": ["Priya (daughter)", "Rahul (son)"]}
            return ok(calling=who, state="ringing (simulated call)", note="nothing is known yet about the conversation itself")
        if tool == "send_message":
            return ok(to=a.get("to"), text=a.get("text"), how="sent (simulated)")
        if tool == "get_home_status":
            return ok(devices=["%s: %s" % kv for kv in self.home.items()])
        if tool == "control_home_device":
            name = self.device(a.get("device", ""))
            if name is None:
                return {"status": "error", "error": "no device called " + a.get("device", ""), "devices": list(self.home)}
            action = a.get("action", "")
            self.home[name] = {"turn_on": "on", "turn_off": "off", "lock": "locked", "unlock": "unlocked"}.get(
                action, action + (" " + str(a["value"]) if a.get("value") else ""))
            return ok(device=name, now=self.home[name])
        if tool == "get_medication_schedule":
            return ok(medicines=[
                {"medicine": "Metformin 500 mg", "for": "blood sugar",
                 "right_now": "already taken today at 9:02 AM: do NOT take another dose now; the next one is today at 8:00 PM",
                 "doses_today": ["9:00 AM: taken at 9:02 AM", "8:00 PM: later today"], "next_dose": "today 8:00 PM"},
                {"medicine": "Amlodipine 5 mg", "for": "blood pressure",
                 "right_now": "already taken today at 9:02 AM: do NOT take another dose now; the next one is tomorrow at 9:00 AM",
                 "doses_today": ["9:00 AM: taken at 9:02 AM"], "next_dose": "tomorrow 9:00 AM"},
                {"medicine": "Atorvastatin 10 mg", "for": "cholesterol", "right_now": "not due yet",
                 "doses_today": ["10:00 PM: later today"], "next_dose": "today 10:00 PM"}])
        if tool == "log_medication":
            if re.search(r"amlo|pressure|metformin|sugar", a.get("medicine", ""), re.I):
                return {"status": "refused_double_dose", "medicine": a.get("medicine"), "taken_at": "9:02 AM",
                        "next_due": "later", "instruction": "tell the user kindly that this dose was already taken"}
            return ok(logged=a.get("medicine"))
        if tool == "alert_family":
            return ok(alerted=["Priya (daughter)", "Rahul (son)"], how="family app alert (simulated)")
        if tool == "log_checkin":
            return ok(recorded=a.get("mood"), shared_with="family")
        if tool == "set_destination":
            self.trip, self.stops = a["place"], []
            return ok(destination=a["place"], minutes=58, arrival="8:12 PM", km=31)
        if tool == "add_stop":
            if not self.trip:
                return {"status": "error", "error": "there is no trip yet; set a destination first"}
            self.stops.append(a["place"])
            return ok(stop=a["place"], destination=self.trip, arrival="8:24 PM", minutes=70)
        if tool == "get_trip_status":
            return ok(trip={"destination": self.trip, "stops": self.stops, "minutes_left": 57, "arrival": "8:12 PM"}
                      if self.trip else "no active trip", car={"range_km": 212, "ac_on": False, "cabin_temp_c": 26})
        if tool == "set_car_climate":
            return ok(ac_on=a.get("on", True) is not False, cabin_temp_c=a.get("temperature", 24))
        if tool == "open_navigation":
            return ok(navigating_to=self.trip, app="Google Maps")
        return ok(tool=tool, args=a)


# --- running one scenario ------------------------------------------------------------------------

def token(mode: str, name: str = "Tanmay") -> dict:
    q = urllib.parse.urlencode({"mode": mode, "name": name, "device": "scripted test phone",
                                "contacts": "Priya (daughter); Rahul (son)"})
    with urllib.request.urlopen("http://localhost:8787/api/token?" + q, timeout=10) as r:
        return json.loads(r.read())


def noise(seconds: float, rate: int = 24000, level: float = 0.06, seed: int = 0) -> np.ndarray:
    """Room noise with no words: low-passed (pinkish) noise, the kind a fan or a cough burst makes."""
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.standard_normal(int(rate * seconds))).astype(np.float32)
    x -= np.convolve(x, np.ones(64) / 64, mode="same")       # remove the drift, keep the low hiss
    return (x / (np.abs(x).max() + 1e-9) * level).astype(np.float32)


async def run_one(name: str, sc: dict, speech: List[tuple], quiet: bool = False) -> dict:
    from livekit import rtc
    t0 = time.time()

    def log(*a):
        if not quiet:
            print("%6.2f" % (time.time() - t0), *a, flush=True)
    tok = token(sc["mode"], "Ramesh" if sc["mode"] == "care" else "Tanmay")
    room = rtc.Room()
    phone = Phone()
    state = {"agent": "initializing", "said_at": 0.0, "greeted": False}
    record: List[dict] = []

    slow = (sc.get("expect", {}).get("phone") or {}).get("slow", {})

    async def on_tool(data: rtc.RpcInvocationData) -> str:
        req = json.loads(data.payload)
        if req["tool"] in slow:   # a phone that does not answer in time (the timeout drill)
            log("   PHONE (silent for %.0f s) <- %s" % (slow[req["tool"]], req["tool"]))
            await asyncio.sleep(slow[req["tool"]])
        out = phone.run(req["tool"], req.get("args", {}))
        log("   PHONE <- %s %s -> %s" % (req["tool"], json.dumps(req.get("args")), json.dumps(out)[:120]))
        return json.dumps(out)

    @room.on("data_received")
    def _data(p: rtc.DataPacket) -> None:
        if p.topic != "duet":
            return
        ev = json.loads(p.data.decode())
        record.append({"t": round(time.time() - t0, 2), **ev})
        if ev["kind"] == "agent_state":
            state["agent"] = ev["state"]
        if ev["kind"] == "agent_said":
            state["greeted"] = True
            state["said_at"] = time.time()
        if ev["kind"] not in ("user_state", "agent_state", "think_start", "listen_done"):
            log("DUET", json.dumps(ev)[:200])

    await room.connect(tok["url"], tok["token"])
    room.local_participant.register_rpc_method("duet.tool", on_tool)
    log("joined", tok["room"])
    source = rtc.AudioSource(48000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    async def push(pcm24k: np.ndarray) -> None:
        x = np.interp(np.arange(0, len(pcm24k), 0.5), np.arange(len(pcm24k)), pcm24k)  # 24 -> 48 kHz
        data = (np.clip(x, -1, 1) * 32767).astype(np.int16)
        for i in range(0, len(data), 480):
            chunk = data[i:i + 480]
            if len(chunk) < 480:
                chunk = np.pad(chunk, (0, 480 - len(chunk)))
            frame = rtc.AudioFrame.create(48000, 1, 480)
            np.copyto(np.frombuffer(frame.data, dtype=np.int16), chunk)
            await source.capture_frame(frame)

    async def silence(seconds: float) -> None:
        await push(np.zeros(int(24000 * seconds), dtype=np.float32))

    async def until(test, limit: float) -> None:
        end = time.time() + limit
        while not test() and time.time() < end:
            await silence(0.2)

    try:
        await silence(1.0)
        await until(lambda: state["greeted"], 90)
        await silence(0.8)
        line_end = 0.0
        for text, clean, audio, pause in speech:
            if text.startswith("#drill:"):   # the app's drill button: cut or restore the cloud mind
                await room.local_participant.publish_data(json.dumps({"kind": "drill", "cloud_off": text.endswith(":on")}),
                                                          reliable=True, topic="duet.phone")
                log("DRILL ", text)
                await silence(pause)
                continue
            if text.startswith("#event:"):   # the car reports it is nearly there: DUET may speak first
                _, what, place, minutes = text.split(":")
                await room.local_participant.publish_data(json.dumps({
                    "kind": "event", "event": "near_destination", "destination": place, "minutes": int(minutes),
                    "text": "The car is %s minutes from %s." % (minutes, place)}), reliable=True, topic="duet.phone")
                log("EVENT ", text)
                await until(lambda: state["agent"] == "speaking", 20)
                line_end = time.time()          # the next "@" line waits for DUET's offer to end
                await silence(pause)
                continue
            if text.startswith("@"):
                await until(lambda: state["agent"] == "listening" and state["said_at"] >= line_end, 75)
                await silence(0.6)
            elif text.startswith("!"):
                await until(lambda: state["agent"] == "speaking", 30)
                await silence(1.5)
            log("USER  ", clean)
            await push(audio)
            line_end = time.time()
            await silence(pause)
        await until(lambda: state["agent"] == "listening" and state["said_at"] >= line_end, 75)
        await silence(1.0)
    finally:
        await room.disconnect()
    return {"scenario": name, "mode": sc["mode"], "room": tok["room"], "lines": [s[1] for s in speech],
            "started": t0, "events": record}


def prepare(sc: dict, pipe, voice: str) -> List[tuple]:
    out = []
    for i, (text, pause) in enumerate(sc["lines"]):
        if text.startswith("~noise:"):
            out.append((text, "(room noise %ss)" % text.split(":")[1], noise(float(text.split(":")[1]), seed=i), pause))
            continue
        if text.startswith("#"):
            out.append((text, text, None, pause))
            continue
        clean = text.lstrip("@!")
        # the scripted voice is the same every run: synthesized once, kept on disk
        import hashlib
        cache = os.path.join(ROOT, "results", "galaxy", "suite", "_voice_cache",
                             hashlib.sha1((voice + "|" + clean).encode()).hexdigest() + ".npy")
        if os.path.exists(cache):
            audio = np.load(cache)
        else:
            audio = np.concatenate([np.asarray(r.audio, dtype=np.float32) for r in pipe(clean, voice=voice)])
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            np.save(cache, audio)
        out.append((text, clean, audio, pause))
    return out


# --- checking a run ------------------------------------------------------------------------------

def _match_value(pattern: str, value: Any) -> bool:
    v = str(value if value is not None else "")
    if pattern.startswith(("@", "=")):
        m, w = re.match(r"\s*(\d{1,2}):(\d{2})", v), re.match(r"[@=](\d{1,2}):(\d{2})", pattern)
        if not m or not w:
            return False
        if pattern[0] == "=":
            return int(m.group(1)) == int(w.group(1)) and m.group(2) == w.group(2)
        return int(m.group(1)) % 12 == int(w.group(1)) % 12 and m.group(2) == w.group(2)
    return re.search(pattern, v, re.I) is not None


def _match(call: dict, tool: str, args: dict) -> bool:
    return call.get("tool") == tool and all(_match_value(p, (call.get("args") or {}).get(k)) for k, p in args.items())


# the key pool's answer when every free-tier key's per-minute input budget is spent (duet_voice/gemma_api.py)
QUOTA = re.compile(r"no key has room|budget is spent", re.I)


def check(run: dict, expect: dict) -> dict:
    ev = run["events"]
    done = [e for e in ev if e.get("kind") == "tool_done"]
    writes = [e for e in done if KIND.get(e.get("tool"), "read") in WRITE_KINDS]
    spoken = " ".join(e.get("text", "") for e in ev if e.get("kind") == "agent_said")
    problems: List[str] = []

    stale = [w for w in writes for t, a in expect.get("never", []) if _match(w, t, a)]
    for w in stale:
        problems.append("STALE/UNWANTED %s(%s)" % (w["tool"], json.dumps(w.get("args"))))
    seen, dup = set(), []
    for w in writes:
        key = (w["tool"], json.dumps(w.get("args"), sort_keys=True))
        if key in seen and not any(k == key and n > 1 for k, n in _expected_counts(expect).items()):
            dup.append(w)
        seen.add(key)
    for w in dup:
        problems.append("DUPLICATE %s(%s)" % (w["tool"], json.dumps(w.get("args"))))

    # the required actions, in order
    want, i = list(expect.get("do", [])), 0
    for w in writes:
        if i < len(want) and _match(w, *want[i]):
            i += 1
    intent = i == len(want)
    if not intent:
        problems.append("MISSING %s" % ", ".join("%s(%s)" % (t, json.dumps(a)) for t, a in want[i:]))
    if expect.get("only"):
        extra = [w for w in writes if not any(_match(w, t, a) for t, a in want)]
        for w in extra:
            if w not in stale:
                problems.append("EXTRA %s(%s)" % (w["tool"], json.dumps(w.get("args"))))
    for tool in expect.get("reads", []):
        if not any(d.get("tool") == tool for d in done):
            problems.append("NOT READ %s" % tool)
    if expect.get("ask"):
        says = [e for e in ev if e.get("kind") == "thinker_say" and "<silent>" not in e.get("text", "")]
        first_write_t = writes[0]["t"] if writes else 1e9
        if not says or "?" not in says[0].get("text", "") or says[0]["t"] > first_write_t:
            problems.append("DID NOT ASK FIRST")
    for rx in expect.get("say", []):
        if not re.search(rx, spoken, re.I):
            problems.append("DID NOT SAY /%s/" % rx)
    for rx in expect.get("claim", []):
        if re.search(rx, spoken, re.I):
            problems.append("FALSE CLAIM /%s/" % rx)
    errors = [e for e in ev if e.get("kind") == "thinker_error"
              and not (expect.get("errors_ok") and re.search(r"cannot reach|offline drill", e.get("text", "")))]
    for e in errors:
        problems.append("MODEL ERROR %s" % e.get("text", "")[:90])
    return {"ok": not problems, "problems": problems, "stale": len(stale), "duplicates": len(dup),
            "intent": intent, "errors": len(errors), "writes": ["%s(%s)" % (w["tool"], json.dumps(w.get("args")))
                                                               for w in writes], "spoken": spoken[:400]}


def _expected_counts(expect: dict) -> Dict[tuple, int]:
    counts: Dict[tuple, int] = {}
    for t, a in expect.get("do", []):
        counts[(t, json.dumps(a, sort_keys=True))] = counts.get((t, json.dumps(a, sort_keys=True)), 0) + 1
    return counts


def summarize(results: List[dict], folder: str) -> dict:
    from galaxy_latency import pct, requests
    lat = {"ack": [], "action": [], "answer": []}
    for r in results:
        for row in requests(r["run"]["events"]):
            if not row["error"]:
                for k in lat:
                    if row[k] is not None:
                        lat[k].append(row[k])
    n = len(results)
    out = {
        "runs": n, "passed": sum(r["check"]["ok"] for r in results),
        "stale_actions": sum(r["check"]["stale"] for r in results),
        "duplicates": sum(r["check"]["duplicates"] for r in results),
        "intent_correct": sum(r["check"]["intent"] for r in results),
        "model_errors": sum(r["check"]["errors"] for r in results),
        "latency_s": {k: {"p50": pct(v, 0.5), "p90": pct(v, 0.9), "n": len(v)} for k, v in lat.items()},
        "by_scenario": {},
    }
    for r in results:
        s = out["by_scenario"].setdefault(r["name"], {"runs": 0, "passed": 0, "problems": []})
        s["runs"] += 1
        s["passed"] += r["check"]["ok"]
        s["problems"] += r["check"]["problems"]
    out["quota_retries"] = [r["name"] for r in results if r.get("quota_retry")]
    with open(os.path.join(folder, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    return out


def report(summary: dict) -> None:
    print("\n%-15s %s" % ("scenario", "passed"))
    for name, s in summary["by_scenario"].items():
        print("%-15s %d/%d  %s" % (name, s["passed"], s["runs"], "; ".join(sorted(set(s["problems"])))[:150]))
    print("\nruns %d · passed %d · stale actions %d · duplicates %d · intent correct %d/%d · model errors %d" % (
        summary["runs"], summary["passed"], summary["stale_actions"], summary["duplicates"],
        summary["intent_correct"], summary["runs"], summary["model_errors"]))
    for k, v in summary["latency_s"].items():
        print("  %-6s p50 %s s · p90 %s s (n=%d)" % (k, v["p50"], v["p90"], v["n"]))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--voice", default="am_michael")
    ap.add_argument("--tag", default=time.strftime("%m%d_%H%M"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", default="", help="re-check the saved runs in this folder")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.list:
        for name, sc in S.items():
            print("%-15s %-9s %s" % (name, sc["mode"], " / ".join(l for l, _ in sc["lines"])))
        return 0
    if args.check:
        results = []
        for f in sorted(os.listdir(args.check)):
            if f.endswith(".json") and f != "summary.json":
                run = json.load(open(os.path.join(args.check, f), encoding="utf-8"))
                results.append({"name": run["scenario"], "run": run, "check": check(run, S[run["scenario"]]["expect"])})
        report(summarize(results, args.check))
        return 0
    names = args.names or list(S)
    unknown = [n for n in names if n not in S]
    if unknown:
        print("unknown scenarios:", unknown)
        return 2
    folder = os.path.join(ROOT, "results", "galaxy", "suite", args.tag)
    os.makedirs(folder, exist_ok=True)
    os.environ.setdefault("DUET_TTS_DEVICE", "cpu")  # the agent's models keep the GPU
    from duet_voice import speech_models
    from duet_voice.galaxy import smartthings as st
    pipe = speech_models.kokoro()
    results = []
    home_loc = None
    if st.enabled():   # the SmartThings home starts every scenario in the same state
        from smartthings_setup import reset
        try:
            home_loc = await st.location_id()
        except st.HomeError as exc:
            print("SmartThings unavailable (%s): the agent will use the simulated home" % exc)
    for rep in range(args.repeat):
        for name in names:
            sc = S[name]
            print("\n=== %s (run %d of %d)" % (name, rep + 1, args.repeat), flush=True)
            run = None
            for attempt in (1, 2):
                if home_loc:
                    try:
                        await reset(home_loc)
                    except st.HomeError as exc:
                        print("SmartThings reset failed: %s" % exc)
                try:
                    run = await run_one(name, sc, prepare(sc, pipe, args.voice), quiet=args.quiet)
                except Exception as exc:  # one broken run must not stop the suite
                    print("RUN FAILED: %s" % exc, flush=True)
                    run = None
                    break
                quota = [e for e in run["events"] if e.get("kind") == "thinker_error" and QUOTA.search(e.get("text", ""))]
                if not quota or check(run, sc["expect"])["ok"] or attempt == 2:
                    break
                # the free Gemma tier's per-minute token budget was spent by the scenarios before this one (it
                # runs about 40 back to back; a person talks far slower): wait for the next minute, run it again,
                # and say so in the summary
                print("the cloud's per-minute budget was spent: running %s again in 61 s" % name, flush=True)
                await asyncio.sleep(61.0)
            if run is None:
                continue
            run["quota_retry"] = attempt == 2
            path = os.path.join(folder, "%s_%d.json" % (name, rep + 1))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(run, fh, indent=1)
            result = {"name": name, "run": run, "check": check(run, sc["expect"]), "quota_retry": attempt == 2}
            results.append(result)
            c = result["check"]
            print("--> %s  %s" % ("PASS" if c["ok"] else "FAIL", "; ".join(c["problems"]) or ", ".join(c["writes"])),
                  flush=True)
            await asyncio.sleep(2.0)   # the room closes before the next one opens
    report(summarize(results, folder))
    print("\nsaved:", folder)
    if home_loc:
        await st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
