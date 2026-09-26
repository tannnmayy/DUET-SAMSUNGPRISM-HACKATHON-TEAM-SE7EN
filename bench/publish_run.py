#!/usr/bin/env python3
"""Copy a run into results/reported/, the committed record the guide asks for
("your benchmark results and run logs (scores, seeds, configuration)").

    python bench/publish_run.py results/live/<stamp> --name best_run
    python bench/publish_run.py results/offline/eval_asr_<stamp>.json --name offline_asr

A live run keeps its settings, scores, the benchmark's own reports, per-item
results, our traces, the tool-call log and the process logs; audio is left out.
An index README.md is written next to them.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KEEP = ("run_config.json", "summary.json", "*_evaluation_report.json", "*_pass_rate_report.json",
        "*_latency_report.json", "eval_*.log", "runner.log", "agent.log", "agent_tool_calls.log",
        "livekit_server.log")


def headline(summary: dict) -> list:
    keys = ("pass_at_1", "tool_selection_f1", "argument_acc", "response_qual", "turn_take_rate",
            "interruption_rate", "avg_response_latency_s", "first_response_latency_mean_s",
            "task_completion_latency_mean_s", "pass@1", "tool_f1", "arg_acc", "resp_qual", "errors", "n")
    return ["| %s | %s |" % (k, summary[k]) for k in keys if k in summary and summary[k] is not None]


def publish_live(src: Path, dst: Path) -> dict:
    for pattern in KEEP:
        for f in src.glob(pattern):
            shutil.copy(f, dst / f.name)
    for sub in ("items", "traces"):
        if (src / sub).is_dir():
            shutil.copytree(src / sub, dst / sub, dirs_exist_ok=True)
    return json.loads((src / "summary.json").read_text(encoding="utf-8")) if (src / "summary.json").exists() else {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a results/live/<stamp> folder or a results/offline/eval_*.json file")
    ap.add_argument("--name", required=True, help="folder name under results/reported/")
    ap.add_argument("--note", default="", help="one line on what this run is")
    args = ap.parse_args()
    src = Path(args.source).resolve()
    dst = REPO / "results" / "reported" / args.name
    dst.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        summary = publish_live(src, dst)
        config = json.loads((src / "run_config.json").read_text(encoding="utf-8")) if (src / "run_config.json").exists() else {}
        kind = "live run through the official FDB-v3 runner"
    else:
        shutil.copy(src, dst / src.name)
        data = json.loads(src.read_text(encoding="utf-8"))
        summary, config = data.get("summary", {}), {}
        kind = "offline evaluation of the thinker (bench/offline_eval.py)"
    agent = config.get("agent", {})
    lines = ["# %s" % args.name, "", args.note or "", "",
             "Source: `%s` (%s)." % ((src.relative_to(REPO) if REPO in src.parents else src).as_posix(), kind), "",
             "| Metric | Value |", "|---|---|", *headline(summary), ""]
    if agent:
        lines += ["Settings: thinker `%s` (thinking %s, sampling %s), talker `%s`, seed %s, "
                  "endpointing %s-%s s, commit hold %s s." % (
                      agent.get("thinker_model"), agent.get("thinker_thinking"), agent.get("thinker_sampling"),
                      agent.get("talker_model"), agent.get("seed"), agent.get("endpoint_min_s"),
                      agent.get("endpoint_max_s"), agent.get("commit_hold_s")), ""]
    if "judge" in summary:
        lines += ["Judge: %s." % summary["judge"], ""]
    lines += ["Full settings: `run_config.json`. Headline numbers: `summary.json`."
              if src.is_dir() else "Per-item rows and the summary: `%s`." % src.name]
    (dst / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("published to", dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
