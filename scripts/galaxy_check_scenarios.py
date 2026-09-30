"""Checks the latest run of each phone scenario (scripts/galaxy_fake_phone.py) against
what DUET must and must not do, and prints one line per scenario.

    python scripts/galaxy_check_scenarios.py            # every scenario with a saved run
    python scripts/galaxy_check_scenarios.py alarm care
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "galaxy")


def latest(name: str):
    # exactly this scenario: "alarm" must not pick up "alarm_late"
    pattern = re.compile(r"scenario_%s_\d{4}_\d{6}\.json$" % re.escape(name))
    files = sorted((p for p in glob.glob(os.path.join(OUT, "scenario_*.json")) if pattern.search(os.path.basename(p))),
                   key=os.path.getmtime)
    return json.load(open(files[-1], encoding="utf-8")) if files else None


def done(events, tool=None):
    return [e for e in events if e.get("kind") == "tool_done" and (tool is None or e.get("tool") == tool)]


def said(events):
    return [e.get("text", "") for e in events if e.get("kind") == "thinker_say"]


def hhmm(e) -> str:
    return str((e.get("args") or {}).get("time", ""))


def at(t: str, hour: int) -> bool:
    """'04:30' or '16:30' for hour 4 (the next 4:30, whichever half of the day)."""
    m = re.match(r"(\d{1,2}):(\d{2})", t)
    return bool(m) and int(m.group(1)) % 12 == hour % 12 and m.group(2) == "30"


def check(name: str, ev) -> tuple:
    alarms = done(ev, "set_alarm")
    if name == "alarm":
        ok = len(alarms) == 1 and at(hhmm(alarms[0]), 4)
        return ok, "one alarm, at 4:30; the 3:30 plan never ran"
    if name == "alarm_late":
        order = [(e["tool"], hhmm(e)) for e in done(ev) if e["tool"] in ("set_alarm", "cancel_alarm")]
        ok = (len(order) == 3 and order[0][0] == "set_alarm" and at(order[0][1], 3) and order[1][0] == "cancel_alarm"
              and at(order[1][1], 3) and order[2][0] == "set_alarm" and at(order[2][1], 4))
        return ok, "3:30 set, then undone, then 4:30 set"
    if name == "waver":
        first = said(ev)[:1]
        ok = len(alarms) == 1 and hhmm(alarms[0]).endswith(":00") and bool(first) and "?" in first[0]
        return ok, "asks 6:30 or 7 first, then sets 7:00 once"
    if name == "battery":
        bright = done(ev, "set_brightness")
        ok = bool(done(ev, "get_battery_report")) and len(bright) == 1 and (bright[0]["args"] or {}).get("percent") == 40
        return ok, "reads the battery; brightness 40% only when asked"
    if name == "apps":
        ok = bool(done(ev, "list_installed_apps")) and any(re.search(r"\d|one|two|three|four|five|six", s, re.I)
                                                          for s in said(ev))
        return ok, "counts from the installed apps"
    if name == "home":
        dev = done(ev, "control_home_device")
        ac_on = any(re.search(r"\bac\b|air", str(e["args"].get("device", "")), re.I) for e in dev)
        light_off = any("light" in str(e["args"].get("device", "")).lower() and
                        str(e["args"].get("action", "")).lower() in ("off", "turn_off", "switch_off") for e in dev)
        return ac_on and not light_off, "AC on; the light left on"
    if name == "care":
        ok = bool(done(ev, "get_medication_schedule")) and not done(ev, "log_medication") and bool(done(ev, "log_checkin"))
        return ok, "checks the record, no second dose, check-in logged"
    if name == "drive":
        dest = done(ev, "set_destination")
        msgs = done(ev, "send_message")
        ok = (len(dest) == 1 and "airport" in str(dest[0]["args"].get("place", "")).lower() and len(msgs) == 1
              and "priya" in str(msgs[0]["args"].get("to", "")).lower()
              and bool(re.search(r"\d", str(msgs[0]["args"].get("text", "")))))
        return ok, "only the airport is set; the message carries the arrival time"
    return False, "no expectation defined"


def main() -> int:
    names = sys.argv[1:] or sorted({re.sub(r"_\d{4}_\d{6}$", "", os.path.basename(p)[9:-5])
                                    for p in glob.glob(os.path.join(OUT, "scenario_*.json"))})
    passed = 0
    for name in names:
        run = latest(name)
        if run is None:
            print("%-11s  no run" % name)
            continue
        ev = run["events"]
        ok, what = check(name, ev)
        passed += ok
        model = next((e.get("model") for e in ev if e.get("kind") == "session_start"), "?")
        tools = ", ".join("%s(%s)" % (e["tool"], ",".join("%s" % v for v in (e.get("args") or {}).values()))
                          for e in done(ev))
        dropped = sum(1 for e in ev if e.get("kind") == "tool_dropped")
        print("%-11s  %s  %-58s  model %s" % (name, "PASS" if ok else "FAIL", what, model))
        print("             done: %s%s" % (tools or "nothing", ("; never ran: %d" % dropped) if dropped else ""))
        for s in said(ev):
            print("             DUET: %s" % s[:150])
    print("%d of %d scenarios as expected" % (passed, len(names)))
    return 0 if passed == len(names) else 1


if __name__ == "__main__":
    raise SystemExit(main())
