"""Does DUET survive what can go wrong during a live demo? On the S24 (or the emulator), behind Google Maps.

    python scripts/phone_resilience.py                     # every case below, in order
    python scripts/phone_resilience.py --only drop,long    # some of them
    python scripts/phone_resilience.py --long-min 10       # a longer stay behind Maps

Cases (each ends with a typed request that must be carried out, with Maps still in front):
  drop     the conversation is cut from LiveKit's side (the room is deleted): the app rejoins on its own,
           behind Maps, without restarting its microphone service (Android 14+ forbids that from the
           background, and the app used to crash on it)
  long     DUET stays behind Maps for --long-min minutes: still connected, the microphone still recording
           (not silenced by Android), the app's process alive
  network  the phone's network goes away for 20 s, then comes back: the conversation recovers
  restart  (only with --only restart, after `bash scripts/galaxy_stop.sh` and a new `galaxy_demo.sh`):
           waits until the phone is back in a conversation on its own, then a request

Needs the stack running and DUET installed (scripts/phone_check.py). The phone's own microphone stays on,
as in real use; on the emulator its input is switched to silence (the laptop's microphone is not used).
Results: results/galaxy/phone/resilience_<time>.json
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
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))
load_dotenv(os.path.join(ROOT, ".env.livekit"))

from phone_check import OUT, PKG, adb, cdp_eval, step  # noqa: E402
from phone_e2e import run_case, top_activity  # noqa: E402
from duet_voice.galaxy import smartthings as st  # noqa: E402

LIGHT_ON = ("Turn on the living room light.", {"done": [("control_home_device", {"device": "living|light"})],
                                               "behind_maps": True})
LIGHT_OFF = ("Turn off the living room light.", {"done": [("control_home_device", {"device": "living|light"})],
                                                 "behind_maps": True})


def android_mic() -> dict:
    """DUET's microphone as Android sees it, from AudioService's recording events: is a capture of DUET's
    running (its last event is not a stop), and is it silenced (what Android does to a background app)?"""
    last = {}
    for l in adb("shell", "dumpsys", "audio").splitlines():
        m = re.search(r"rec (start|update|stop) riid:(\d+) .*?((?:not )?silenced) pack:(\S+)", l)
        if m and m.group(4) == PKG:
            last[m.group(2)] = (m.group(1), m.group(3), l.strip())
    live = [v for v in last.values() if v[0] != "stop"]
    return {"recording": bool(live), "silenced": any(v[1] == "silenced" for v in live), "lines": [v[2] for v in live][:3]}


APP_MIC = """(async () => {
  const r = DuetApp.room; if (!r) return { published: false };
  const pub = [...r.localParticipant.audioTrackPublications.values()].find((p) => p.source === 'microphone');
  if (!pub || !pub.track) return { published: false };
  const sent = async () => { let b = 0; (await pub.track.getRTCStatsReport() || new Map()).forEach((s) => {
    if (s.type === 'outbound-rtp') b = s.bytesSent; }); return b; };
  const a = await sent(); await new Promise((ok) => setTimeout(ok, 2000)); const b = await sent();
  return { published: true, live: pub.track.mediaStreamTrack.readyState === 'live', muted: pub.isMuted, sending: b > a };
})()"""


async def recording() -> dict:
    """Is DUET really listening? Android runs its capture (not silenced) and the app sends the audio."""
    a = android_mic()
    try:
        app = await cdp_eval(APP_MIC, 20)
    except Exception as exc:
        app = {"published": False, "error": repr(exc)[:80]}
    ok = a["recording"] and not a["silenced"] and app.get("published") and app.get("live") and app.get("sending")
    return {"ok": bool(ok), "android": a, "app": app,
            "text": "listening" if ok else "NOT listening (Android %s, app %s)" % (
                "silenced" if a["silenced"] else ("recording" if a["recording"] else "not recording"), json.dumps(app))}


def fatal_since(mark: str) -> list:
    out = adb("logcat", "-d", "-v", "time", "-T", mark, "AndroidRuntime:E", "*:S")
    return [l for l in out.splitlines() if "FATAL" in l or PKG in l][:8]


def logcat_mark() -> str:
    return adb("shell", "date", "+%m-%d %H:%M:%S.000").strip()


async def state() -> dict:
    return await cdp_eval("""(() => ({ connected: !!(DuetApp.room && DuetApp.room.state === 'connected'),
        room: DuetApp.room ? DuetApp.room.name : null,
        agent: !!(DuetApp.room && [...DuetApp.room.remoteParticipants.values()].some((p) => p.isAgent || /agent/.test(p.identity))),
        mic: !!(DuetApp.room && DuetApp.room.localParticipant.isMicrophoneEnabled),
        listening: DuetPhone.backgroundListening, sessions: window.__ev.filter((e) => e.kind === 'session_start').length }))()""", 20)


async def wait_back(sessions_before: int, limit_s: float) -> float | None:
    """Seconds until the phone is in a new conversation with DUET (a new session_start), or None."""
    t = time.time()
    while time.time() - t < limit_s:
        try:
            s = await state()
            if s["connected"] and s["agent"] and s["sessions"] > sessions_before:
                return round(time.time() - t, 1)
        except Exception:
            pass
        await asyncio.sleep(1.0)
    return None


async def open_maps() -> bool:
    adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", "google.navigation:q=airport",
        "-p", "com.google.android.apps.maps")
    for _ in range(15):
        await asyncio.sleep(1.0)
        if "maps" in top_activity():
            return True
    return False


async def request(name: str, text: str, want: dict) -> dict:
    r = await run_case(name, text, want)
    step("  request: " + text, r["ok"], "; ".join(r["problems"]) or ", ".join(r["tools"]) + " · said: " + r["said"][:90])
    return {k: r[k] for k in ("ok", "problems", "tools", "said", "action_s")}


async def case_drop() -> dict:
    from livekit import api
    mark, pid = logcat_mark(), adb("shell", "pidof", PKG).strip()
    before = await state()
    lk = api.LiveKitAPI(os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=before["room"]))
    finally:
        await lk.aclose()
    back = await wait_back(before["sessions"], 90)
    after = await state() if back is not None else {}
    await asyncio.sleep(2.0)
    rec, fatal = await recording(), fatal_since(mark)
    same = adb("shell", "pidof", PKG).strip() == pid
    ok = back is not None and same and not fatal and "maps" in top_activity() and rec["ok"]
    step("drop: rejoined behind Maps", ok, "back in %ss · new room %s · same process %s · Maps in front %s · %s%s" % (
        back, after.get("room") != before["room"], same, "maps" in top_activity(), rec["text"],
        (" · CRASH: " + fatal[0]) if fatal else ""))
    req = await request("drop_after", *LIGHT_ON) if back is not None else {"ok": False}
    return {"ok": ok and req["ok"], "back_s": back, "same_process": same, "fatal": fatal, "mic": rec, "request": req}


async def case_long(minutes: float) -> dict:
    mark, pid = logcat_mark(), adb("shell", "pidof", PKG).strip()
    samples, t = [], time.time()
    while time.time() - t < minutes * 60:
        await asyncio.sleep(30)
        s, rec = await state(), await recording()
        samples.append({"min": round((time.time() - t) / 60, 1), "connected": s["connected"], "agent": s["agent"],
                        "mic": rec["text"],
                        "maps": "maps" in top_activity(), "alive": adb("shell", "pidof", PKG).strip() == pid})
        print("     %(min)4.1f min · connected %(connected)s · DUET here %(agent)s · mic %(mic)s · Maps in front %(maps)s · alive %(alive)s"
              % samples[-1], flush=True)
    fatal = fatal_since(mark)
    ok = all(x["connected"] and x["agent"] and x["mic"] == "listening" and x["maps"] and x["alive"] for x in samples) and not fatal
    step("long: %g min behind Maps" % minutes, ok, "%d samples%s" % (len(samples), (" · CRASH: " + fatal[0]) if fatal else ""))
    req = await request("long_after", *LIGHT_OFF)
    return {"ok": ok and req["ok"], "samples": samples, "fatal": fatal, "request": req}


async def case_network() -> dict:
    mark = logcat_mark()
    before = await state()
    adb("shell", "svc", "wifi", "disable")
    adb("shell", "svc", "data", "disable")
    await asyncio.sleep(20)
    adb("shell", "svc", "wifi", "enable")
    adb("shell", "svc", "data", "enable")
    t, back = time.time(), None
    while time.time() - t < 120:          # either LiveKit resumes the same conversation, or the app rejoins
        try:
            s = await state()
            if s["connected"] and s["agent"]:
                back = round(time.time() - t, 1)
                break
        except Exception:
            pass
        await asyncio.sleep(1.0)
    after = await state() if back is not None else {}
    await asyncio.sleep(2.0)
    rec, fatal = await recording(), fatal_since(mark)
    step("network: 20 s without network", back is not None and not fatal and rec["ok"],
         "back %ss after the network returned · %s · %s%s" % (
             back, "same conversation" if after.get("room") == before["room"] else "a new conversation", rec["text"],
             (" · CRASH: " + fatal[0]) if fatal else ""))
    await asyncio.sleep(3.0)
    if "maps" not in top_activity():
        await open_maps()
    req = await request("network_after", *LIGHT_ON) if back is not None else {"ok": False}
    return {"ok": back is not None and not fatal and rec["ok"] and req["ok"], "back_s": back,
            "same_room": after.get("room") == before["room"], "fatal": fatal, "mic": rec, "request": req}


async def case_restart() -> dict:
    """The stack was restarted by hand: wait for the phone to come back on its own."""
    sessions = (await state())["sessions"]
    back = await wait_back(sessions, 200)
    await asyncio.sleep(2.0)
    rec = await recording()
    step("restart: back in a conversation on its own", back is not None and rec["ok"], "%ss · %s" % (back, rec["text"]))
    if "maps" not in top_activity():
        await open_maps()
    req = await request("restart_after", *LIGHT_ON) if back is not None else {"ok": False}
    return {"ok": back is not None and rec["ok"] and req["ok"], "back_s": back, "mic": rec, "request": req}


async def setup(mode: str) -> bool:
    if st.enabled():
        from smartthings_setup import reset
        await reset(await st.location_id())
    adb("reverse", "tcp:8787", "tcp:8787")
    adb("emu", "avd", "hostmicoff")       # the emulator only: its microphone hears silence, not the laptop's
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
        localStorage.setItem('duet.settings', JSON.stringify(s)); return true; })()""")
    await cdp_eval("""(() => { window.__ev = []; DuetApp.hooks.events.push((e) => window.__ev.push(Object.assign({t: Date.now()}, e)));
        DuetApp.start('%s'); return true; })()""" % mode)
    back = await wait_back(0, 90)
    s = await state()
    step("conversation started", back is not None, "in %ss · microphone on %s · background listening %s" % (
        back, s["mic"], s["listening"]))
    if not step("Google Maps in front", await open_maps(), top_activity()):
        return False
    await asyncio.sleep(3.0)
    rec = await recording()
    step("microphone behind Maps", rec["ok"], rec["text"])
    return back is not None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="drop,long,network")
    ap.add_argument("--long-min", type=float, default=5.0)
    ap.add_argument("--mode", default="assistant")
    args = ap.parse_args()
    only = [x.strip() for x in args.only.split(",") if x.strip()]
    os.makedirs(OUT, exist_ok=True)
    results = {}
    if only != ["restart"] and not await setup(args.mode):
        return 1
    for name in only:
        fn = {"drop": case_drop, "long": lambda: case_long(args.long_min), "network": case_network,
              "restart": case_restart}[name]
        try:
            results[name] = await fn()
        except Exception as exc:
            results[name] = {"ok": False, "error": repr(exc)}
            step(name, False, repr(exc)[:200])
    adb("emu", "avd", "hostmicon")
    path = os.path.join(OUT, "resilience_%s.json" % time.strftime("%m%d_%H%M%S"))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"device": adb("shell", "getprop", "ro.product.model"), "results": results}, fh, indent=1)
    passed = sum(1 for r in results.values() if r.get("ok"))
    step("resilience", passed == len(results), "%d of %d · %s" % (passed, len(results), path))
    await st.close()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
