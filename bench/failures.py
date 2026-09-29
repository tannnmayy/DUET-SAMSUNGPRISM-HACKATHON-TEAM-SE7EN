#!/usr/bin/env python3
"""What went wrong in an offline evaluation, grouped by kind of mistake.

    python bench/failures.py results/offline/eval_script_<time>_<tag>.json
    python bench/failures.py <new.json> --compare <old.json>      # which items a change fixed or broke
    python bench/failures.py <eval.json> --write results/reported/<name>/FAILURES.md

Kinds (from the tool names alone, then the benchmark's own verdict):
  - model error: the model call failed (a server or network problem, not a mistake);
  - no call: nothing was called, though something was expected;
  - wrong tool: a different tool than expected;
  - extra call: every expected tool, plus more (one stale or repeated call fails the item);
  - missing call: some expected tools never called (usually a chain stopped early);
  - wrong arguments: exactly the right tools, but a value was wrong.
The point is to find general patterns to fix, never individual items: a fix that
names a benchmark value or scenario is forbidden (and tests/test_voice_integrity.py
fails on one).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def kind(row: dict) -> str:
    if row.get("error"):
        return "model error"
    got = Counter(c["function"] for c in row.get("calls") or [])
    want = Counter(c["function"] for c in row.get("expected") or [])
    if not got and want:
        return "no call"
    if got == want:
        return "wrong arguments"
    if not (set(got) & set(want)):
        return "wrong tool"
    if all(got[t] >= n for t, n in want.items()):
        return "extra call"
    if all(want[t] >= n for t, n in got.items()):
        return "missing call"
    return "wrong tool"


def fmt_calls(calls) -> str:
    return "; ".join("%s(%s)" % (c["function"], json.dumps(c.get("args", {}), ensure_ascii=False)[1:-1])
                     for c in calls or []) or "(none)"


def report(path: Path, compare: Path | None) -> str:
    d = json.loads(path.read_text(encoding="utf-8"))
    s, rows = d["summary"], d["rows"]
    failed = [r for r in rows if not r["passed"]]
    out = ["# Failures in `%s`" % path.name, "",
           "Pass@1 %s on %d recordings (%s text, judge: %s). %d failed." % (
               s.get("pass@1"), s.get("n"), s.get("text"), s.get("judge"), len(failed)), ""]
    kinds = Counter(kind(r) for r in failed)
    out += ["| kind | items |", "|---|---|"] + ["| %s | %d |" % kv for kv in kinds.most_common()] + [""]
    for label, key in (("disfluency", "disfluency"), ("difficulty", "difficulty"), ("domain", "domain")):
        tally = Counter()
        total = Counter()
        for r in rows:
            for v in (r[key] if isinstance(r[key], list) else [r[key]]) or ["NONE"]:
                total[v] += 1
                tally[v] += 0 if r["passed"] else 1
        out.append("By %s: " % label + ", ".join("%s %d/%d failed" % (v, tally[v], total[v])
                                                  for v in sorted(total)))
    out.append("")
    if compare:
        old = {r["folder"]: r for r in json.loads(compare.read_text(encoding="utf-8"))["rows"]}
        fixed = [r for r in rows if r["passed"] and r["folder"] in old and not old[r["folder"]]["passed"]]
        broke = [r for r in rows if not r["passed"] and r["folder"] in old and old[r["folder"]]["passed"]]
        out += ["## Against `%s`" % compare.name, "",
                "Fixed %d, broke %d (net %+d)." % (len(fixed), len(broke), len(fixed) - len(broke)), ""]
        out += ["- fixed: %s" % r["folder"] for r in fixed] + ["- broke: %s" % r["folder"] for r in broke] + [""]
    for k, _ in kinds.most_common():
        out += ["## %s" % k, ""]
        for r in (r for r in failed if kind(r) == k):
            out += ["### %s (%s, %s)" % (r["folder"], r["difficulty"], ", ".join(r["disfluency"] or ["no disfluency"])),
                    "", "- heard: %s" % r["input"][:600],
                    "- expected: %s" % fmt_calls(r["expected"]),
                    "- called: %s" % fmt_calls(r["calls"]),
                    "- verdict: %s" % r.get("why", ""),
                    "- said: %s" % (r.get("final_text") or "")[:300]]
            if r.get("error"):
                out.append("- error: %s" % r["error"][:300])
            out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("eval_json")
    ap.add_argument("--compare", default="")
    ap.add_argument("--write", default="")
    args = ap.parse_args()
    text = report(Path(args.eval_json), Path(args.compare) if args.compare else None)
    print(text)
    if args.write:
        Path(args.write).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write).write_text(text, encoding="utf-8")
        print("wrote", args.write, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
