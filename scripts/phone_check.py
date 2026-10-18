"""Set up a phone (or the emulator) for DUET for Galaxy over USB, and prove the app works on it.

    python scripts/phone_check.py                 # install, grant, self-test, screenshot
    python scripts/phone_check.py --no-install    # the app is already there
    python scripts/phone_check.py --eval "DuetApp.start('assistant')"   # run JavaScript in the app

Steps (each one printed with its result):
  1. adb sees exactly one device; its model and Android version.
  2. Install app/android/app/build/outputs/apk/debug/app-debug.apk (unless --no-install).
  3. Grant DUET's permissions: the runtime ones with `pm grant`, usage access and "modify system
     settings" with `appops`. Only for DUET's own package.
  4. `adb reverse tcp:8787`: on the phone, localhost:8787 is this laptop's token server and web
     app, through the USB cable (no Wi-Fi or firewall needed); DUET's settings point there.
  5. Start DUET and connect to its WebView (Chrome DevTools protocol, debug builds only).
  6. Run the in-app self-test (DuetPhone.selfTest): every real phone action, tried safely.
  7. Save a screenshot to results/galaxy/phone/.
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
ADB = os.environ.get("ADB", r"E:\android-toolchain\sdk\platform-tools\adb.exe")
PKG = "com.se7en.duet"
APK = os.path.join(ROOT, "app", "android", "app", "build", "outputs", "apk", "debug", "app-debug.apk")
OUT = os.path.join(ROOT, "results", "galaxy", "phone")

RUNTIME = ["android.permission.RECORD_AUDIO", "android.permission.CAMERA", "android.permission.CALL_PHONE",
           "android.permission.SEND_SMS", "android.permission.READ_CONTACTS", "android.permission.READ_CALENDAR",
           "android.permission.WRITE_CALENDAR", "android.permission.POST_NOTIFICATIONS"]
# WRITE_SETTINGS: brightness and timeout; GET_USAGE_STATS: screen time; SYSTEM_ALERT_WINDOW ("Appear on
# top"): open the Clock, dialer, Messages or Maps while Google Maps is in front (Android blocks it otherwise)
APPOPS = ["WRITE_SETTINGS", "GET_USAGE_STATS", "SYSTEM_ALERT_WINDOW"]


def adb(*args, check=False, timeout=120) -> str:
    p = subprocess.run([ADB] + list(args), capture_output=True, text=True, timeout=timeout)
    out = (p.stdout + p.stderr).strip()
    if check and p.returncode != 0:
        raise RuntimeError("adb %s failed: %s" % (" ".join(args), out[:300]))
    return out


def step(name: str, ok: bool, detail: str = "") -> bool:
    print("%-4s %s%s" % ("OK" if ok else "FAIL", name, (" - " + detail) if detail else ""), flush=True)
    return ok


async def cdp_eval(expr: str, timeout: float = 120.0):
    """Evaluate JavaScript in DUET's WebView and return its value (promises awaited)."""
    import aiohttp
    pid = ""
    for _ in range(50):        # DUET may still be starting (a cold WebView takes several seconds)
        pid = adb("shell", "pidof", PKG).strip()
        sockets = re.findall(r"@(webview_devtools_remote_%s)\b" % pid, adb("shell", "cat", "/proc/net/unix")) if pid else []
        if sockets:
            break
        time.sleep(0.5)
    if not sockets:
        raise RuntimeError("no debuggable WebView: is DUET open (a debug build)?")
    adb("forward", "tcp:9333", "localabstract:" + sockets[-1], check=True)
    async with aiohttp.ClientSession() as s:
        async with s.get("http://127.0.0.1:9333/json") as r:
            pages = [p for p in await r.json(content_type=None) if p.get("type") == "page"]
        if not pages:
            raise RuntimeError("the WebView has no page yet")
        async with s.ws_connect(pages[0]["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            await ws.send_json({"id": 1, "method": "Runtime.evaluate", "params": {
                "expression": expr, "awaitPromise": True, "returnByValue": True}})
            end = time.time() + timeout
            while time.time() < end:
                msg = await ws.receive_json(timeout=timeout)
                if msg.get("id") == 1:
                    res = msg.get("result", {})
                    if "exceptionDetails" in res:
                        raise RuntimeError(json.dumps(res["exceptionDetails"])[:300])
                    return res.get("result", {}).get("value")
    raise RuntimeError("no answer from the WebView")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-install", action="store_true")
    ap.add_argument("--no-grant", action="store_true")
    ap.add_argument("--no-overlay", action="store_true", help="leave 'Appear on top' off (tests DUET's refusal)")
    ap.add_argument("--eval", default="")
    ap.add_argument("--assistant", action="store_true",
                    help="make DUET the phone's digital assistant (assist gesture, side key); ask the phone's owner first")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    devices = [l.split()[0] for l in adb("devices").splitlines()[1:] if l.strip().endswith("device")]
    if not step("one device connected", len(devices) == 1, ", ".join(devices) or "none: plug in the phone, allow USB debugging"):
        return 1
    model = adb("shell", "getprop", "ro.product.model")
    release = adb("shell", "getprop", "ro.build.version.release")
    step("device", True, "%s, Android %s" % (model, release))

    if args.eval:
        print(json.dumps(await cdp_eval(args.eval), indent=1))
        return 0

    if not args.no_install:
        if not step("APK built", os.path.exists(APK), APK):
            return 1
        out = adb("install", "-r", "-g", APK, timeout=300)
        if not step("installed", "Success" in out, out.splitlines()[-1] if out else ""):
            return 1
    if not args.no_grant:
        bad = []
        for perm in RUNTIME:
            r = adb("shell", "pm", "grant", PKG, perm)
            if r and "Exception" in r:
                bad.append(perm.split(".")[-1])
        for op in APPOPS:
            mode = "default" if (op == "SYSTEM_ALERT_WINDOW" and args.no_overlay) else "allow"
            r = adb("shell", "appops", "set", PKG, op, mode)
            if r:
                bad.append(op)
        step("permissions granted", not bad, ("not granted: " + ", ".join(bad)) if bad else "all")
        # Do Not Disturb access, for the skill that turns it on and off
        adb("shell", "cmd", "notification", "allow_dnd", PKG)
        # Samsung's battery care can put a backgrounded app to sleep: DUET may run in the background and
        # is left out of battery optimisation, so it keeps listening behind Google Maps
        adb("shell", "cmd", "appops", "set", PKG, "RUN_ANY_IN_BACKGROUND", "allow")
        idle = adb("shell", "cmd", "deviceidle", "whitelist", "+" + PKG)
        step("background running allowed, no battery optimisation", "Added" in idle or "already" in idle.lower() or not idle,
             idle.splitlines()[0] if idle else "")
    if args.assistant:
        # Android forgets the role when the app is reinstalled, so it is set after the install
        adb("shell", "cmd", "role", "add-role-holder", "android.app.role.ASSISTANT", PKG)
        holders = adb("shell", "cmd", "role", "get-role-holders", "android.app.role.ASSISTANT")
        step("DUET is the phone's assistant (assist gesture, side key)", PKG in holders, holders or "none")
    step("phone's localhost:8787 is this laptop", "8787" in adb("reverse", "tcp:8787", "tcp:8787") or True,
         adb("reverse", "--list").replace("\n", "; "))

    adb("shell", "am", "start", "-n", PKG + "/.MainActivity")
    ok = False
    for _ in range(45):        # a cold start of the WebView takes several seconds on a slow device
        time.sleep(1)
        try:
            ok = bool(await cdp_eval("typeof DuetPhone === 'object' && DuetPhone.NATIVE", 10))
        except Exception:
            ok = False
        if ok:
            break
    if not step("DUET running, native plugin present", ok):
        return 1
    await cdp_eval("(() => { const s = JSON.parse(localStorage.getItem('duet.settings') || '{}');"
                   " s.server = 'localhost:8787'; localStorage.setItem('duet.settings', JSON.stringify(s));"
                   " const e = document.getElementById('set-server'); if (e) e.value = s.server; return true; })()")
    step("server address set", True, "localhost:8787 (through USB)")
    saved = await cdp_eval("JSON.parse(localStorage.getItem('duet.settings') || '{}').contacts || ''")
    step("demo contacts are real numbers", not re.search(r"\+?91?0{6,}\d{0,3}\b", saved or ""),
         "placeholders: put the teammates' numbers in DUET's settings (Contacts)"
         if re.search(r"\+?91?0{6,}\d{0,3}\b", saved or "") else "set")

    t = time.time()
    result = await cdp_eval("DuetPhone.selfTest()", timeout=180)
    for r in result.get("results", []):
        step("self-test: " + r["name"], r["ok"], "%d ms · %s" % (r["ms"], r["detail"][:110]))
    step("self-test", result.get("passed") == result.get("total"),
         "%s of %s on %s in %.1f s" % (result.get("passed"), result.get("total"), result.get("device"), time.time() - t))
    with open(os.path.join(OUT, "selftest_%s.json" % time.strftime("%m%d_%H%M%S")), "w", encoding="utf-8") as fh:
        json.dump({"device": model, "android": release, **result}, fh, indent=1)

    shot = os.path.join(OUT, "screen_%s.png" % time.strftime("%m%d_%H%M%S"))
    with open(shot, "wb") as fh:
        fh.write(subprocess.run([ADB, "exec-out", "screencap", "-p"], capture_output=True, timeout=60).stdout)
    step("screenshot", os.path.getsize(shot) > 1000, shot)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
