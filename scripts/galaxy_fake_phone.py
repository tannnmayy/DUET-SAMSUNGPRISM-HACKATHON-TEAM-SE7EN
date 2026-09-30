"""A scripted stand-in for the phone app, for testing DUET for Galaxy end to end
without a phone: it joins a room like the app does, speaks scripted lines (Kokoro,
a different voice from DUET's), answers DUET's tool calls with a simulated phone,
and prints DUET's timeline events.

    python scripts/galaxy_fake_phone.py alarm          # a scenario name (see SCENARIOS)
    python scripts/galaxy_fake_phone.py --list

The token server (python -m duet_voice.galaxy.server) and the agent worker
(python -m duet_voice.galaxy.agent start) must be running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))
from livekit import rtc  # noqa: E402

# (mode, [(line, seconds of silence after it)]). A line starting with "@" waits for
# DUET to be listening again first; "!" speaks while DUET is still talking (barge-in).
SCENARIOS = {
    "alarm": ("assistant", [          # corrected at once: the 3:30 alarm must never be set
        ("@Set an alarm for 3:30.", 0.4),
        ("No no, cancel that. Could you set it for 4:30 instead?", 9.0),
        ("@What alarms do I have now?", 8.0),
    ]),
    "alarm_late": ("assistant", [     # corrected after DUET acted: it must undo 3:30, then set 4:30
        ("@Set an alarm for 3:30.", 1.0),
        ("@No no, cancel that. Could you set it for 4:30 instead?", 10.0),
        ("@What alarms do I have now?", 8.0),
    ]),
    "waver": ("assistant", [
        ("@Wake me up at 6:30 tomorrow, or maybe 7, hmm.", 9.0),
        ("@Seven.", 8.0),
    ]),
    "battery": ("assistant", [
        ("@My battery drains super quick these days. What's going on?", 5.5),
        ("!Oh wait, I think I know. I keep my brightness really high and I play a lot of games. Is that it?", 10.0),
        ("@Okay, bring the brightness down to forty percent.", 8.0),
    ]),
    "apps": ("assistant", [
        ("@How many shopping apps do I have on my phone?", 9.0),
    ]),
    "home": ("assistant", [
        ("@Turn on the AC, and, uh, switch off the living room light. Actually leave the light on.", 10.0),
    ]),
    "care": ("care", [
        ("@Did I take my blood pressure tablet today? I don't remember. I'll just take it now.", 10.0),
        ("@Okay. I'm feeling a bit tired today, but fine.", 9.0),
    ]),
    "drive": ("drive", [
        ("@Take me to the office. No wait, the airport, I have a flight.", 9.0),
        ("@And tell Priya I'll be there by the arrival time.", 10.0),
    ]),
}


def token(mode: str) -> dict:
    q = urllib.parse.urlencode({"mode": mode, "name": "Tanmay", "device": "scripted test phone",
                                "contacts": "Priya (daughter); Rahul (son)"})
    with urllib.request.urlopen("http://localhost:8787/api/token?" + q, timeout=10) as r:
        return json.loads(r.read())


# --- a simulated phone (enough of phone.js for the scripted scenarios) ------------------------

class Phone:
    def __init__(self) -> None:
        self.alarms = []
        self.home = {"living room light": "on", "air conditioner": "off", "tv": "off", "front door lock": "locked"}
        self.trip = None
        self.brightness = 92

    def run(self, tool: str, a: dict) -> dict:
        if tool == "set_alarm":
            self.alarms = [x for x in self.alarms if x != a["time"]] + [a["time"]]
            return {"status": "ok", "alarm": a["time"], "where": "Clock (simulated)"}
        if tool == "cancel_alarm":
            if a["time"] not in self.alarms:
                return {"status": "ok", "cancelled": False,
                        "note": "there was no %s alarm, so nothing needed cancelling" % a["time"]}
            self.alarms.remove(a["time"])
            return {"status": "ok", "cancelled": a["time"]}
        if tool == "list_alarms":
            return {"status": "ok", "set_by_duet": self.alarms, "phone_next_alarm": None}
        if tool == "get_battery_report":
            return {"status": "ok", "level": 23, "charging": False, "temperature_c": 41.5, "health": "good",
                    "current_ma": -980, "cycle_count": 412, "brightness_percent": self.brightness,
                    "adaptive_brightness": False, "refresh_rate_hz": 120, "screen_timeout_s": 600,
                    "power_saving": False, "wifi_on": True, "bluetooth_on": True, "location_on": True,
                    "top_apps_today": ["BGMI 155 min (game)", "Instagram 82 min (social)", "YouTube 58 min (video)"]}
        if tool == "set_brightness":
            self.brightness = a.get("percent", self.brightness)
            return {"status": "ok", "brightness_percent": self.brightness}
        if tool == "list_installed_apps":
            apps = ["Amazon Shopping", "Flipkart", "Myntra", "Meesho", "Swiggy", "Zomato", "BGMI [game]",
                    "Instagram [social]", "WhatsApp [social]", "YouTube [video]", "Google Maps [maps]", "Chrome",
                    "Samsung Health", "Galaxy Store", "SmartThings", "Paytm", "Uber", "Netflix [video]"]
            return {"status": "ok", "count": len(apps), "apps": apps}
        if tool == "get_home_status":
            return {"status": "ok", "devices": ["%s: %s" % kv for kv in self.home.items()]}
        if tool == "control_home_device":
            want = a["device"].lower()
            want = "air conditioner" if want in ("ac", "the ac", "a/c") else want
            name = next((k for k in self.home if any(w in k for w in want.split())), None)
            if name is None:
                return {"status": "error", "error": "no device " + a["device"]}
            self.home[name] = a["action"].replace("turn_", "")
            return {"status": "ok", "device": name, "now": self.home[name]}
        if tool == "get_medication_schedule":
            return {"status": "ok", "medicines": [
                {"medicine": "Metformin 500 mg", "for": "blood sugar",
                 "right_now": "already taken today at 9:02 AM: do NOT take another dose now; the next one is today at 8:00 PM",
                 "doses_today": ["9:00 AM: taken at 9:02 AM", "8:00 PM: later today"], "next_dose": "today 8:00 PM"},
                {"medicine": "Amlodipine 5 mg", "for": "blood pressure",
                 "right_now": "already taken today at 9:02 AM: do NOT take another dose now; the next one is tomorrow at 9:00 AM",
                 "doses_today": ["9:00 AM: taken at 9:02 AM"], "next_dose": "tomorrow 9:00 AM"},
                {"medicine": "Atorvastatin 10 mg", "for": "cholesterol", "right_now": "not due yet",
                 "doses_today": ["10:00 PM: later today"], "next_dose": "today 10:00 PM"}]}
        if tool == "log_medication":
            if "amlo" in a["medicine"].lower() or "pressure" in a["medicine"].lower():
                return {"status": "refused_double_dose", "medicine": "Amlodipine 5 mg", "taken_at": "9:02 AM",
                        "next_due": "tomorrow 9:00 AM",
                        "instruction": "tell the user kindly that this dose was already taken and not to take another now"}
            return {"status": "ok", "logged": a["medicine"]}
        if tool == "set_destination":
            self.trip = a["place"]
            return {"status": "ok", "destination": a["place"], "minutes": 58, "arrival": "8:12 PM", "km": 31}
        if tool == "get_trip_status":
            return {"status": "ok", "trip": {"destination": self.trip, "minutes_left": 57, "arrival": "8:12 PM"}
                    if self.trip else "no active trip", "car": {"range_km": 212, "ac_on": False}}
        return {"status": "ok", "tool": tool, "args": a}


# --- the script --------------------------------------------------------------------------------

async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="alarm")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--voice", default="am_michael")
    args = ap.parse_args()
    if args.list:
        print("\n".join(SCENARIOS))
        return 0
    mode, lines = SCENARIOS[args.scenario]

    os.environ.setdefault("DUET_TTS_DEVICE", "cpu")  # keep the laptop GPU for the language model
    from duet_voice import speech_models
    pipe = speech_models.kokoro()
    speech = []
    for text, pause in lines:
        clean = text.lstrip("@!")
        audio = np.concatenate([np.asarray(r.audio, dtype=np.float32) for r in pipe(clean, voice=args.voice)])
        speech.append((text, clean, audio, pause))

    t0 = time.time()
    log = lambda *a: print("%6.2f" % (time.time() - t0), *a, flush=True)
    tok = token(mode)
    room = rtc.Room()
    phone = Phone()
    agent_state = {"v": "initializing"}
    record = []

    async def on_tool(data: rtc.RpcInvocationData) -> str:
        req = json.loads(data.payload)
        out = phone.run(req["tool"], req.get("args", {}))
        log("   PHONE <- %s %s -> %s" % (req["tool"], json.dumps(req.get("args")), json.dumps(out)[:160]))
        return json.dumps(out)

    @room.on("data_received")
    def _data(p: rtc.DataPacket) -> None:
        if p.topic != "duet":
            return
        ev = json.loads(p.data.decode())
        record.append({"t": round(time.time() - t0, 2), **ev})
        if ev["kind"] == "agent_state":
            agent_state["v"] = ev["state"]
        if ev["kind"] == "agent_said":
            agent_state["greeted"] = True
        if ev["kind"] in ("user_state", "agent_state", "think_start", "listen_done"):
            return
        log("DUET", json.dumps(ev)[:220])

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

    # wait for DUET's greeting to finish
    await silence(1.0)
    deadline = time.time() + 90
    while not agent_state.get("greeted") and time.time() < deadline:
        await silence(0.2)
    await silence(0.8)
    for text, clean, audio, pause in speech:
        if text.startswith("@"):
            deadline = time.time() + 60
            while agent_state["v"] != "listening" and time.time() < deadline:
                await silence(0.2)
            await silence(0.6)
        elif text.startswith("!"):
            deadline = time.time() + 30
            while agent_state["v"] != "speaking" and time.time() < deadline:
                await silence(0.1)
            await silence(1.5)  # let DUET get a sentence out, then cut in
        log("USER  ", clean)
        await push(audio)
        await silence(pause)
    deadline = time.time() + 30
    while agent_state["v"] != "listening" and time.time() < deadline:
        await silence(0.2)
    await silence(1.0)
    out = os.path.join(ROOT, "results", "galaxy", "scenario_%s_%s.json" % (args.scenario, time.strftime("%m%d_%H%M%S")))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"scenario": args.scenario, "mode": mode, "room": tok["room"], "events": record}, fh, indent=1)
    log("events saved to", out)
    await room.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
