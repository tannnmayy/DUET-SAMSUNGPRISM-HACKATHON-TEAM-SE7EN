#!/usr/bin/env python3
"""Run a directory of probe scenarios and report per-scenario failures.

    python tools/probes.py                                   # tests/probes_dev
    python tools/probes.py --dir E:/duet_holdout --show      # a held-out set
    python tools/probes.py --pattern "probe_neg_*.json"

The public nine and our own conformance suite are what DUET was built against,
and our chaos templates now score 100 on every seed, so none of them can say
how the agent does on phrasing it has never seen. Probe sets are written by
someone who did NOT read duet/, from WALKTHROUGH.md section 4 alone:

  tests/probes_dev/   the development set. Fix CATEGORIES of failure here,
                      never a phrase.
  (held out)          kept outside the repo and run only at gates. If a change
                      improves the dev set but not the held-out one, it is a
                      hardcode and it gets reverted.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import importlib
import json
import os
import statistics
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from harness.runner import EvaluationHarness  # noqa: E402
from harness.scorer import score_scenario  # noqa: E402

SPOKEN = ("filler_speech", "clarification_request", "final_response")


def load_agent(spec: str):
    module_name, _, class_name = spec.partition(":")
    return getattr(importlib.import_module(module_name), class_name)


def failures(result: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for part in result.get("breakdown", {}).values():
        detail = part["detail"]
        out += [c["id"] + ": " + c["note"] for c in detail.get("checkpoints", []) if not c["passed"]]
        out += [c["check"] + ": " + c["note"] for c in detail.get("checks", []) if not c["passed"]]
        out += ["latency ev#%s delta=%s" % (r["event_index"], r["delta_ms"])
                for r in detail.get("responses", []) if r["fraction"] < 1]
        out += ["safety: " + n for n in detail.get("notes", []) if n != "clean"]
    if "note" in result:
        out.append(result["note"])
    return out


def run_one(scenario: Dict[str, Any], cls, scale: float) -> List[Dict[str, Any]]:
    async def go():
        h = EvaluationHarness(scenario, lambda a, b: cls(a, b), time_scale=scale, verbose=False)
        await h.prepare()
        return await h.run()
    return asyncio.run(go())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=os.path.join(ROOT, "tests", "probes_dev"))
    ap.add_argument("--pattern", default="*.json")
    ap.add_argument("--agent", default="agent.agent:ParticipantAgent")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--show", action="store_true", help="print calls and speech")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cls = load_agent(args.agent)
    paths = sorted(glob.glob(os.path.join(args.dir, args.pattern)))
    rows = []
    for path in paths:
        scenario = json.load(open(path, encoding="utf-8"))
        trace = run_one(scenario, cls, args.scale)
        result = score_scenario(scenario, trace)
        fails = failures(result)
        modality = scenario.get("metadata", {}).get("modality", "text")
        rows.append({"id": scenario.get("scenario_id"), "modality": modality,
                     "total": result["total"], "fails": fails})
        print("%-38s %6.1f  %s" % (scenario.get("scenario_id"), result["total"],
                                   "; ".join(fails)[:200]))
        if args.show:
            for e in trace:
                if e.get("kind") != "action":
                    continue
                if e.get("action") == "tool_call":
                    shown = {k: ("<%d floats>" % len(v) if isinstance(v, list) and len(v) > 8 else v)
                             for k, v in (e.get("args") or {}).items()}
                    print("      %6d CALL %s %s" % (e["t_ms"], e["api_name"], shown))
                elif e.get("action") in SPOKEN:
                    print("      %6d %-5s %s" % (e["t_ms"], e["action"][:5].upper(),
                                                 e["payload"].get("text")))

    if not rows:
        print("no scenarios matched")
        return 1
    totals = [r["total"] for r in rows]
    print("\nprobes %d  mean %.1f  min %.1f  <75: %d" % (
        len(rows), statistics.mean(totals), min(totals), sum(t < 75 for t in totals)))
    for modality in sorted({r["modality"] for r in rows}):
        sub = [r["total"] for r in rows if r["modality"] == modality]
        print("  %-7s n=%-3d mean %.1f" % (modality, len(sub), statistics.mean(sub)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
