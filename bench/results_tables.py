#!/usr/bin/env python3
"""Markdown tables from a run's official reports, for docs/RESULTS.md.

    python bench/results_tables.py results/reported/<run>

Everything printed comes from the benchmark's own reports (evaluation, pass rate,
latency), DUET's summary.json, and the model requests logged in agent.log.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def load(run: Path, suffix: str):
    found = sorted(run.glob("*" + suffix))
    return json.loads(found[0].read_text(encoding="utf-8")) if found else None


def pct(x) -> str:
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def stats(values):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    return {"n": len(v), "mean": statistics.fmean(v), "median": statistics.median(v),
            "p90": v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))]}


def main() -> int:
    run = Path(sys.argv[1])
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    passrep = load(run, "_pass_rate_report.json")
    evalrep = load(run, "_evaluation_report.json")
    latrep = load(run, "_latency_report.json")

    print("## Headline\n")
    print("| Metric | Value |\n|---|---|")
    print("| Pass@1 (strict) | %.3f (%d of %d) |" % (summary["pass_at_1"], summary["passed"], summary["total"]))
    print("| Tool selection | %.3f |" % summary["tool_selection_f1"])
    print("| Argument accuracy | %.3f |" % summary["argument_acc"])
    print("| Response quality | %.3f |" % summary["response_qual"])
    print("| Turn-take rate | %s |" % pct(summary["turn_take_rate"]))
    print("| Interruption rate | %s |" % pct(summary["interruption_rate"]))
    for key, label in (("first_response_latency_mean_s", "First response, mean"),
                       ("tool_call_latency_mean_s", "First tool call, mean"),
                       ("task_completion_latency_mean_s", "Task completion, mean")):
        if summary.get(key) is not None:
            print("| %s | %.2f s |" % (label, summary[key]))

    if passrep:
        groups = defaultdict(lambda: [0, 0])
        for r in passrep["scenario_results"]:
            keys = ["Domain: " + r.get("domain", "?"), "Difficulty: " + r.get("difficulty", "?"),
                    "Tools expected: %s" % r.get("num_tools", "?"),
                    "State rollback: " + ("yes" if r.get("state_rollback") else "no")]
            keys += ["Disfluency: " + d for d in (r.get("disfluency") or ["none"])]
            for k in keys:
                groups[k][0] += bool(r.get("passed"))
                groups[k][1] += 1
        print("\n## Pass@1 by kind of item\n")
        print("| Items | Passed | Pass@1 |\n|---|---|---|")
        for k in sorted(groups, key=lambda k: (k.split(":")[0], -groups[k][1])):
            p, n = groups[k]
            print("| %s | %d of %d | %.2f |" % (k, p, n, p / n))
        print("\nFailure breakdown:", json.dumps(passrep.get("failure_breakdown")))

    if latrep:
        per = latrep.get("per_scenario") or []
        if isinstance(per, dict):
            per = list(per.values())
        valid = [m for m in per if m.get("turn_take_success") and (m.get("first_response_latency_s") or -1) >= 0]
        print("\n## Latency (the benchmark's measurement, non-interrupted conversations)\n")
        print("| Measure | n | Mean | Median | p90 |\n|---|---|---|---|---|")
        for key, label in (("first_response_latency_s", "First response"), ("tool_call_latency_s", "First tool call"),
                           ("task_completion_latency_s", "Key information spoken")):
            s = stats([m.get(key) for m in valid])
            if s:
                print("| %s | %d | %.2f s | %.2f s | %.2f s |" % (label, s["n"], s["mean"], s["median"], s["p90"]))
        agg = latrep.get("aggregate", {})
        print("\nTurn-taking:", json.dumps(agg.get("turn_taking")), "| fillers:", json.dumps(agg.get("filler_stats")))

    if evalrep:
        print("\nEvaluation latency block:", json.dumps(evalrep.get("latency")))

    agent = summary.get("agent", {})
    print("\n## What the agent did\n")
    print("| | |\n|---|---|")
    for k in ("conversations", "tool_calls", "keep_listening", "superseded", "thinker_answers", "fallback_acks",
              "thinker_errors"):
        if k in agent:
            print("| %s | %s |" % (k.replace("_", " "), agent[k]))
    for model, t in (agent.get("tokens") or {}).items():
        print("| %s | %s calls, %s input tokens, %s output, %s thinking |" % (
            model, t.get("calls"), t.get("input"), t.get("output"), t.get("thinking")))

    log = run / "agent.log"
    if log.exists():
        kinds = defaultdict(int)
        secs = []
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r'"message": "gemma: ([^"]*)"', line)
            if not m:
                continue
            msg = m.group(1)
            a = re.search(r"answered on key \d+ in ([\d.]+) s", msg)
            if a:
                kinds["answered"] += 1
                secs.append(float(a.group(1)))
            elif "asking key" in msg:
                kinds["backup requests"] += 1
            elif "failed (" in msg:
                kinds["transient errors, retried"] += 1
            elif "refused" in msg:
                kinds["rate-limit refusals"] += 1
            elif "no longer needed" in msg:
                kinds["calls answered while backups were pending"] += 1
            elif "cancelled" in msg:
                kinds["calls cancelled (plan overtaken)"] += 1
        s = stats(secs)
        print("\n## Model requests (agent.log)\n")
        print("| | |\n|---|---|")
        for k, v in sorted(kinds.items()):
            print("| %s | %d |" % (k, v))
        if s:
            print("| answer time | median %.1f s, p90 %.1f s |" % (s["median"], s["p90"]))
    gpu = summary.get("gpu") or {}
    if gpu:
        print("\nGPU:", gpu.get("gpu"), "peak %.1f GiB (whole card)" % (gpu.get("peak_used_mib", 0) / 1024))
    print("Code:", summary.get("git_commit"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
