"""Everything DUET for Galaxy is checked by, in one command, with one dated report.

    python scripts/verify_all.py                 # the whole run (stack up, phone or emulator on USB)
    python scripts/verify_all.py --quick         # unit tests, 10 spoken scenarios, Assistant end to end
    python scripts/verify_all.py --no-phone      # unit tests and the spoken suite only
    python scripts/verify_all.py --device s24    # the real phone: the preflight's red lines count

Steps, in order (each one's exit code and summary line are kept; a failed step does not stop the run):
  1. unit tests (pytest)
  2. the stack answers (/api/health: fast mind, cloud mind, SmartThings, LiveKit)
  3. the spoken suite (scripts/galaxy_suite.py): every scenario, or a quick set
  4. the phone: install and self-test (phone_check.py), end to end in Assistant, Drive, arrival and
     washer modes (phone_e2e.py), resilience behind Maps (phone_resilience.py)
  5. the demo-day preflight (demo_preflight.py): on the emulator its red lines are expected (Do Not
     Disturb, volume, demo contacts) and do not fail the run; with --device s24 they do

Report: results/verify/verify_<time>.md and .json, with each step's result and the latency table
(the spoken suite's first sound, action and answer, p50 and p90).
Run it after every change that is committed, twice before the S24 night, and on the S24 that night.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
OUT = os.path.join(ROOT, "results", "verify")

QUICK_SUITE = ["alarm", "alarm_three", "waver_call", "dropped", "home_fix", "cut_off", "drill", "torch",
               "offer_accept", "offer_decline"]


def run(cmd, timeout: int, env=None):
    """(exit code, output, seconds) of one command; a timeout is exit code 124."""
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})})
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, out + "\n(timed out after %d s)" % timeout, time.time() - t


def last(pattern: str, text: str, default: str = "") -> str:
    hits = re.findall(pattern, text, re.M)
    return hits[-1] if hits else default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-phone", action="store_true")
    ap.add_argument("--no-suite", action="store_true")
    ap.add_argument("--device", default="emulator", choices=["emulator", "s24"])
    ap.add_argument("--suite-repeat", type=int, default=1)
    ap.add_argument("--long-min", type=float, default=5.0, help="minutes behind Maps in the resilience run")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    stamp = time.strftime("%m%d_%H%M%S")
    steps = []

    def step(name: str, ok, detail: str, seconds: float, gate: bool = True, **extra) -> None:
        mark = "PASS" if ok else ("FAIL" if gate else "NOTE")
        steps.append({"step": name, "result": mark, "detail": detail, "seconds": round(seconds), **extra})
        print("%-4s %-34s %s  (%ds)" % (mark, name, detail, seconds), flush=True)

    # 1. unit tests
    code, out, s = run([PY, "-m", "pytest", "-q", "tests"], 900)
    step("unit tests", code == 0, last(r"(\d+ (?:passed|failed).*?) in ", out, "no summary"), s)

    # 2. the stack
    t = time.time()
    try:
        with urllib.request.urlopen("http://localhost:8787/api/health", timeout=20) as r:
            h = json.loads(r.read())
        parts = {k: v.get("ok") for k, v in h.items() if isinstance(v, dict)}
        stack_ok = all(v is not False for k, v in parts.items() if k != "smartthings")
        step("stack (/api/health)", stack_ok, ", ".join("%s %s" % (k, "ok" if v else ("-" if v is None else "DOWN"))
                                                       for k, v in parts.items() if k != "smartthings"), time.time() - t)
        st_h = h.get("smartthings", {})
        # without SmartThings DUET uses the app's simulated home (and says so): a note, not a failure
        step("SmartThings home", st_h.get("ok") is not False,
             "%s devices" % st_h.get("devices") if st_h.get("ok") else (st_h.get("why") or "off")[:90], 0,
             gate=args.device == "s24")
    except Exception as exc:
        stack_ok = False
        step("stack (/api/health)", False, "not answering (%s): start bash scripts/galaxy_demo.sh" % type(exc).__name__,
             time.time() - t)

    # 3. the spoken suite
    latency = {}
    if not args.no_suite and stack_ok:
        tag = "verify_" + stamp
        names = QUICK_SUITE if args.quick else []
        code, out, s = run([PY, "scripts/galaxy_suite.py", *names, "--repeat", str(args.suite_repeat), "--tag", tag,
                            "--quiet"], 7200)
        summary_path = os.path.join(ROOT, "results", "galaxy", "suite", tag, "summary.json")
        if os.path.exists(summary_path):
            sm = json.load(open(summary_path, encoding="utf-8"))
            latency = sm.get("latency_s", {})
            failed = [n for n, v in sm.get("by_scenario", {}).items() if v["passed"] < v["runs"]]
            ok = sm["passed"] == sm["runs"] and sm["stale_actions"] == 0 and sm["duplicates"] == 0
            step("spoken suite", ok, "%d/%d runs · stale %d · duplicates %d · model errors %d%s" % (
                sm["passed"], sm["runs"], sm["stale_actions"], sm["duplicates"], sm.get("model_errors", 0),
                (" · failed: " + ", ".join(failed)) if failed else ""), s, folder=os.path.relpath(os.path.dirname(summary_path), ROOT))
        else:
            step("spoken suite", False, "no summary (exit %d): %s" % (code, out.strip().splitlines()[-1:] or ""), s)

    # 4. the phone
    if not args.no_phone and stack_ok:
        code, out, s = run([PY, "scripts/phone_check.py"], 600)
        step("phone: install and self-test", code == 0 and "FAIL self-test" not in out,
             last(r"self-test - (.*)$", out, "no self-test") + ("" if code == 0 else " (exit %d)" % code), s)
        modes = [("assistant", 1800), ("drive", 1200), ("arrival", 900), ("washer", 600)]
        if args.quick:
            modes = modes[:1]
        for m, limit in modes:
            code, out, s = run([PY, "scripts/phone_e2e.py", "--mode", m], limit)
            step("phone: end to end (%s)" % m, code == 0, last(r"end to end - (.*?) · ", out, "no result (exit %d)" % code), s)
        if not args.quick:
            code, out, s = run([PY, "scripts/phone_resilience.py", "--long-min", str(args.long_min)], 2400)
            step("phone: resilience behind Maps", code == 0, last(r"resilience - (.*?) · ", out, "no result (exit %d)" % code), s)
        code, out, s = run([PY, "scripts/demo_preflight.py"], 300)
        failed = re.findall(r"^FAIL (.+?)\s{2,}", out, re.M)
        step("demo-day preflight", code == 0, "READY" if code == 0 else "red: " + ", ".join(f.strip() for f in failed),
             s, gate=args.device == "s24")

    # the report
    gates = [x for x in steps if x["result"] != "NOTE"]
    verdict = "ALL GREEN" if all(x["result"] == "PASS" for x in gates) else "%d of %d steps failed" % (
        sum(x["result"] == "FAIL" for x in gates), len(gates))
    md = ["# DUET verification, %s" % time.strftime("%d %b %Y %H:%M"), "",
          "Device: %s · %s · **%s**" % (args.device, "quick" if args.quick else "full", verdict), "",
          "| Step | Result | Detail | Time |", "|---|---|---|---|"]
    md += ["| %s | %s | %s | %d s |" % (x["step"], x["result"], x["detail"].replace("|", "/"), x["seconds"]) for x in steps]
    if latency:
        md += ["", "Spoken suite latency, from the end of the user's speech:", "", "| | p50 | p90 | n |", "|---|---|---|---|"]
        md += ["| %s | %s s | %s s | %s |" % (k, v.get("p50"), v.get("p90"), v.get("n")) for k, v in latency.items()]
    path = os.path.join(OUT, "verify_%s" % stamp)
    with open(path + ".md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    with open(path + ".json", "w", encoding="utf-8") as fh:
        json.dump({"device": args.device, "quick": args.quick, "verdict": verdict, "steps": steps, "latency_s": latency},
                  fh, indent=1)
    print("\n%s · %s.md" % (verdict, os.path.relpath(path, ROOT)))
    return 0 if verdict == "ALL GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
