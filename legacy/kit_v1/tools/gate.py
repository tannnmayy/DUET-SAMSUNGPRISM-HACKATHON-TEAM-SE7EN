#!/usr/bin/env python3
"""The merge gate: every measurement that must not regress, run one at a time.

    python tools/gate.py                 # full gate, writes results/gate_<time>.json
    python tools/gate.py --quick         # unit tests (no models) + dev probes, text only
    python tools/gate.py --holdout DIR   # also run a held-out probe directory

Several agents now work on DUET at once. The latency parts of the score are
measured in real time, so two gates running together on one laptop slow each
other down and report regressions that are not there. A lock file makes the
full gate exclusive: a second gate waits instead of racing.

Baselines on 23 Sep 2026 (commit 4d08c2b), for comparison:
    pytest 373 passed · eval weighted 97.4 · chaos all-templates 100.0
    dev probes mean 74.6 (24) and 50.0 (8 negatives)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Outside the repo on purpose: agents work in separate git worktrees, and the
# lock has to be shared by all of them on this machine.
LOCK = os.environ.get("DUET_GATE_LOCK",
                      os.path.join(tempfile.gettempdir(), "duet_gate.lock"))


def acquire(timeout_s: float = 3600.0) -> None:
    start = time.time()
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            # A lock older than two hours belongs to a gate that died.
            if time.time() - os.path.getmtime(LOCK) > 7200:
                os.remove(LOCK)
                continue
            if time.time() - start > timeout_s:
                raise SystemExit("gate lock held for over an hour: " + LOCK)
            time.sleep(10)


def release() -> None:
    try:
        os.remove(LOCK)
    except FileNotFoundError:
        pass


def run(label: str, cmd, env_extra=None, timeout: int = 3600) -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **(env_extra or {}))
    started = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    out = proc.stdout + proc.stderr
    took = round(time.time() - started, 1)
    print("[%s] exit %d in %ss" % (label, proc.returncode, took))
    return {"label": label, "exit": proc.returncode, "seconds": took, "tail": out[-4000:]}


def grab(pattern: str, text: str):
    match = re.search(pattern, text)
    return match.group(1) if match else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--holdout", default="")
    ap.add_argument("--seed", type=int, default=0, help="chaos seed (default: time-based)")
    args = ap.parse_args()
    py = sys.executable
    steps = []

    if args.quick:
        no_models = {"DUET_NO_ASR": "1", "DUET_NO_EMBED": "1", "DUET_NO_VISION": "1"}
        steps.append(run("pytest-fast", [py, "-m", "pytest", "tests", "-q", "-x",
                                         "-p", "no:cacheprovider",
                                         "--ignore=tests/test_invariants.py",
                                         "--ignore=tests/test_conformance_discriminates.py",
                                         "--ignore=tests/test_perception.py",
                                         "--ignore=tests/test_packaging.py"], no_models))
        steps.append(run("probes-dev-text", [py, "tools/probes.py"], no_models))
    else:
        acquire()
        try:
            seed = args.seed or int(time.time()) % 100000
            steps.append(run("pytest", [py, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]))
            steps.append(run("eval", [py, "eval_submission.py", ".", "--time-scale", "1", "--reps", "1"]))
            steps.append(run("quality", [py, "tools/quality.py"]))
            steps.append(run("chaos", [py, "tools/chaos.py", "--templates", "all", "--n", "48",
                                       "--seed", str(seed)]))
            steps.append(run("probes-dev", [py, "tools/probes.py"]))
            if args.holdout:
                steps.append(run("probes-holdout", [py, "tools/probes.py", "--dir", args.holdout]))
        finally:
            release()

    summary = {}
    for step in steps:
        tail = step["tail"]
        if step["label"].startswith("pytest"):
            summary[step["label"]] = grab(r"(\d+ passed[^\n]*)", tail) or "FAILED"
        elif step["label"] == "eval":
            summary["eval_weighted"] = grab(r"WEIGHTED SCORE:\s+([\d.]+)", tail)
        elif step["label"] == "chaos":
            summary["chaos_mean"] = grab(r"mean\s+:\s+([\d.]+)", tail)
            summary["chaos_min"] = grab(r"min\s+:\s+([\d.]+)", tail)
        elif step["label"].startswith("probes"):
            summary[step["label"]] = grab(r"(probes \d+\s+mean [\d.]+\s+min [\d.]+\s+<75: \d+)", tail)
        elif step["label"] == "quality":
            summary["quality_exit"] = step["exit"]
    summary["all_exit_zero"] = all(s["exit"] == 0 for s in steps)
    print(json.dumps(summary, indent=1))

    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(ROOT, "results", "gate_%s.json" % stamp), "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "steps": steps}, handle, indent=1)
    return 0 if summary["all_exit_zero"] else 1


if __name__ == "__main__":
    sys.exit(main())
