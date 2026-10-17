"""Is everything ready for the live demo? One command, a green or red line per check.

    python scripts/demo_preflight.py

Checks, in order: the keys in .env.local; the SmartThings token (they last 24 hours) and the demo home;
the stack (token server, agent, fast mind, cloud mind) through the token server's /api/health; the
phone over USB (connected, DUET installed, its permissions and Appear on top, the USB route, battery,
Do Not Disturb, media volume). Nothing is changed, except that the USB route (adb reverse) is set.
Run it the night before and the morning of the demo; fix every red line before the call.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from dotenv import dotenv_values  # noqa: E402

ADB = os.environ.get("ADB", r"E:\android-toolchain\sdk\platform-tools\adb.exe")
PKG = "com.se7en.duet"
bad = 0


def line(ok, name, detail=""):
    global bad
    mark = {True: "OK  ", False: "FAIL", None: "--  "}[ok]
    if ok is False:
        bad += 1
    print("%s %-34s %s" % (mark, name, detail), flush=True)


def adb(*args, timeout=20) -> str:
    try:
        p = subprocess.run([ADB] + list(args), capture_output=True, text=True, timeout=timeout)
        return (p.stdout + p.stderr).strip()
    except Exception as exc:
        return "error: %s" % exc


def main() -> int:
    env = {**dotenv_values(os.path.join(ROOT, ".env.local")), **dotenv_values(os.path.join(ROOT, ".env.livekit"))}
    keys = [k for k in (env.get("GOOGLE_API_KEYS") or env.get("GOOGLE_API_KEY") or "").split(",") if k.strip()]
    line(bool(keys), "Google API keys", "%d key(s)" % len(keys))
    line(all(env.get(k) for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")), "LiveKit Cloud credentials")
    line(bool(env.get("SMARTTHINGS_TOKEN")) or None, "SmartThings token in .env.local",
         "" if env.get("SMARTTHINGS_TOKEN") else "none: the home is simulated")

    # the stack, as the phone sees it
    try:
        with urllib.request.urlopen("http://localhost:8787/api/health", timeout=15) as r:
            h = json.loads(r.read())
        line(True, "token server :8787", "answering")
        for name, key in (("cloud mind (Gemma 4 26B)", "cloud_mind"), ("fast mind (Gemma 4 E2B)", "fast_mind"),
                          ("SmartThings home", "smartthings")):
            x = h.get(key, {})
            line(x.get("ok"), name, x.get("why") or ("%s devices" % x["devices"] if x.get("devices") else ""))
        agent_up = "registered worker" in open(os.path.join(ROOT, "results", "galaxy", "agent.log"), encoding="utf-8",
                                                errors="replace").read()[-200000:]
        line(agent_up, "agent registered with LiveKit", "results/galaxy/agent.log")
    except Exception as exc:
        line(False, "token server :8787", "not answering (%s): start bash scripts/galaxy_demo.sh" % type(exc).__name__)

    # the phone
    devices = [l.split()[0] for l in adb("devices").splitlines()[1:] if l.strip().endswith("device")]
    if len(devices) != 1:
        line(False, "phone over USB", "%d devices: plug in the phone, allow USB debugging (Auto Blocker off)" % len(devices))
        return finish()
    model = adb("shell", "getprop", "ro.product.model")
    line(True, "phone over USB", "%s, Android %s" % (model, adb("shell", "getprop", "ro.build.version.release")))
    installed = PKG in adb("shell", "pm", "list", "packages", PKG)
    line(installed, "DUET installed", "" if installed else "run python scripts/phone_check.py")
    if installed:
        dump = adb("shell", "dumpsys", "package", PKG)
        for perm in ("RECORD_AUDIO", "CALL_PHONE", "SEND_SMS", "READ_CONTACTS", "WRITE_CALENDAR", "POST_NOTIFICATIONS"):
            m = re.search(r"android\.permission\.%s: granted=(true|false)" % perm, dump)
            line(bool(m and m.group(1) == "true"), "permission " + perm.lower())
        for op in ("SYSTEM_ALERT_WINDOW", "WRITE_SETTINGS", "GET_USAGE_STATS"):
            mode = adb("shell", "appops", "get", PKG, op)
            ok = "allow" in mode or (op == "SYSTEM_ALERT_WINDOW" and "default" in mode and
                                     re.search(r"SYSTEM_ALERT_WINDOW: granted=true", dump) is not None)
            line(ok, "appop " + op.lower(), mode.split(";")[0])
    adb("reverse", "tcp:8787", "tcp:8787")
    line("8787" in adb("reverse", "--list"), "USB route to the laptop (8787)")
    level = re.search(r"level: (\d+)", adb("shell", "dumpsys", "battery"))
    line(bool(level and int(level.group(1)) >= 60), "phone battery", (level.group(1) + "%") if level else "?")
    zen = adb("shell", "settings", "get", "global", "zen_mode").strip()
    line(zen not in ("0", ""), "Do Not Disturb", "on" if zen not in ("0", "") else "off: turn it on for the demo")
    vol = re.search(r"STREAM_MUSIC.*?Current: .*?(\d+)", adb("shell", "dumpsys", "audio"), re.S)
    line(None, "media volume (index)", vol.group(1) if vol else "?")
    return finish()


def finish() -> int:
    print("\n%s" % ("READY" if bad == 0 else "%d check(s) failed: fix them before the call" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
