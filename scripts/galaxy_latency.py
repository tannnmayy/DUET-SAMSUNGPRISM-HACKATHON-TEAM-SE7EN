"""Per-request latency from DUET for Galaxy event logs: the numbers behind the Round 2 gate G3.

    python scripts/galaxy_latency.py                       # the latest run of every scenario
    python scripts/galaxy_latency.py results/galaxy/scenario_alarm_*.json

Reads the timeline events a run saved (scripts/galaxy_fake_phone.py writes them; the phone app's
timeline has the same events). For every request DUET answered, measured from the end of the
user's speech:
  ack     DUET starts speaking (the fast voice, or the answer itself)
  action  the first phone action has run
  answer  the slow mind's answer is ready to speak
and prints p50 / p90 over all requests, plus the requests that ended in an error.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "galaxy")


def latest_runs() -> List[str]:
    """The newest saved run of each scenario."""
    newest: Dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(OUT, "scenario_*.json")), key=os.path.getmtime):
        name = re.sub(r"_\d{4}_\d{6}$", "", os.path.basename(path)[len("scenario_"):-len(".json")])
        newest[name] = path
    return [newest[k] for k in sorted(newest)]


def requests(events: List[dict]) -> List[dict]:
    """One row per answered request: when the user stopped, and when each step happened."""
    rows = []
    for i, ev in enumerate(events):
        if ev.get("kind") not in ("thinker_say", "thinker_error"):
            continue
        start = next((j for j in range(i, -1, -1) if events[j].get("kind") == "think_start"), None)
        if start is None:
            continue
        # the end of the user's speech: the last switch from speaking to listening before thinking began
        end = next((events[j]["t"] for j in range(start, -1, -1)
                    if events[j].get("kind") == "user_state" and events[j].get("state") == "listening"), None)
        if end is None:
            continue

        def first(kind: str, **match) -> Optional[float]:
            for e in events:
                if e["t"] >= end and e.get("kind") == kind and all(e.get(k) == v for k, v in match.items()):
                    if e["t"] <= ev["t"] + 0.01 or kind == "agent_state":
                        return e["t"]
            return None

        rows.append({
            "request": events[start].get("text", "")[-70:],
            "speech_end": end,
            "ack": _rel(first("agent_state", state="speaking"), end),
            "action": _rel(first("tool_done"), end),
            "answer": round(ev["t"] - end, 2),
            "error": ev.get("kind") == "thinker_error",
        })
    return rows


def _rel(t: Optional[float], end: float) -> Optional[float]:
    return None if t is None else round(t - end, 2)


def pct(values: List[float], q: float) -> Optional[float]:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def main() -> int:
    paths = sys.argv[1:] or latest_runs()
    all_rows = []
    for path in paths:
        run = json.load(open(path, encoding="utf-8"))
        rows = requests(run["events"])
        print("%s (%s)" % (run.get("scenario", os.path.basename(path)), os.path.basename(path)))
        for r in rows:
            print("  ack %5s  action %5s  answer %5s%s  | %s" % (
                r["ack"], r["action"], r["answer"], "  ERROR" if r["error"] else "", r["request"]))
        all_rows += rows
    ok = [r for r in all_rows if not r["error"]]
    print("\n%d requests, %d errors" % (len(all_rows), len(all_rows) - len(ok)))
    for key in ("ack", "action", "answer"):
        vals = [r[key] for r in ok]
        print("  %-6s p50 %5s s   p90 %5s s   (n=%d)" % (key, pct(vals, 0.5), pct(vals, 0.9),
                                                     len([v for v in vals if v is not None])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
