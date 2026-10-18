"""The Round 2 deck's numbers, from the latest runs (so no number on a slide is typed by hand).

    python scripts/deck_numbers.py results/galaxy/suite/final_r2 > results/galaxy/deck_numbers.json
    node docs/deck/build_deck_r2.js SRM_SE7EN_Round2.pptx results/galaxy/deck_numbers.json

Reads the suite's summary.json (runs, passes, stale and duplicate actions, latency p50) and counts the unit
tests with pytest. The fast mind's and the end-to-end numbers stay as the deck builder has them (their
runs are named there).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results", "galaxy", "suite", "final_r2")
    s = json.load(open(os.path.join(folder, "summary.json"), encoding="utf-8"))
    lat = s["latency_s"]
    out = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "--collect-only", "-p", "no:cacheprovider"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    m = re.search(r"(\d+) tests? collected", out)
    print(json.dumps({
        "suiteRuns": s["runs"], "suitePassed": s["passed"], "suiteStale": s["stale_actions"], "suiteDup": s["duplicates"],
        "ackP50": "%.1f" % lat["ack"]["p50"], "actionP50": "%.1f" % lat["action"]["p50"], "answerP50": "%.1f" % lat["answer"]["p50"],
        "unitTests": int(m.group(1)) if m else "?",
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
