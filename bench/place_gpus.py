#!/usr/bin/env python3
"""Choose GPUs for a run on a shared machine, and print the settings for the shell.

    eval "$(python bench/place_gpus.py)"             # a live benchmark run (model + agent + scorer)
    eval "$(python bench/place_gpus.py --offline)"   # an offline evaluation (the model only)
    python bench/place_gpus.py --show                # just the table and the choice

Samsung's machine has one free 48 GB GPU, and everything runs on it. A shared
machine often has no free GPU at all, only leftovers on several (on 28 Sep every
A100 on the DGX had 15-20 GB free). This picks, from what is free right now:

  1. one GPU with room for everything (about 31 GiB, so 34 GiB free): the same
     layout as Samsung's re-run;
  2. otherwise, pieces on different GPUs:
     - the model server on one GPU with 23 GiB free, or split over two with
       13 GiB free each (DUET_LLM_GPUS);
     - the agent's speech models on a GPU with 5 GiB free (DUET_AGENT_GPUS);
     - the benchmark's scoring recognizer on a GPU with 6 GiB free
       (DUET_SCORING_GPUS). It always runs on a GPU.

The measured memory of our own processes (summary.json, gpu.ours_peak_gib) is still
comparable with a single card: it adds up the pieces wherever they run. Latency
on a shared GPU is slower than on a free one, so say so when you report it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

NEED_MIB = {"whole": 34000, "llm": 23100, "llm_half": 13100, "agent": 5000, "scoring": 6000}
PLACEMENT = ("DUET_LLM_GPUS", "DUET_LLM_TP", "DUET_AGENT_GPUS", "DUET_SCORING_GPUS")


def gpus() -> list:
    out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
    rows = []
    for line in out.stdout.strip().splitlines():
        index, name, total, free, util = [x.strip() for x in line.split(",")]
        rows.append({"index": index, "name": name, "total": int(float(total)), "free": int(float(free)),
                     "util": int(float(util)) if util.replace(".", "").isdigit() else 0})
    return rows


def choose(rows: list, offline: bool) -> dict:
    """Returns {"single": index} or {"llm": [..], "agent": index|"", "scoring": index}; raises SystemExit."""
    free = {r["index"]: r["free"] for r in rows}
    util = {r["index"]: r["util"] for r in rows}

    def best(min_mib, exclude=()):
        fits = [g for g in free if free[g] >= min_mib and g not in exclude]
        return max(fits, key=lambda g: (free[g], -util[g])) if fits else None

    whole = best(NEED_MIB["llm"] if offline else NEED_MIB["whole"])
    if whole is not None:
        return {"single": whole}
    plan = {}
    one = best(NEED_MIB["llm"])
    if one is not None:
        plan["llm"] = [one]
        free[one] -= NEED_MIB["llm"]
    else:
        a = best(NEED_MIB["llm_half"])
        b = best(NEED_MIB["llm_half"], exclude=(a,)) if a is not None else None
        if b is None:
            raise SystemExit("No room for the model: it needs one GPU with %d MiB free, or two with %d MiB "
                             "each. Free now: %s. Wait for jobs to finish, or ask for a GPU."
                             % (NEED_MIB["llm"], NEED_MIB["llm_half"],
                                ", ".join("GPU %s %d MiB" % (g, f) for g, f in sorted(free.items()))))
        plan["llm"] = sorted([a, b], key=int)
        free[a] -= NEED_MIB["llm_half"]
        free[b] -= NEED_MIB["llm_half"]
    if offline:
        return plan
    agent = best(NEED_MIB["agent"])
    if agent is None:
        raise SystemExit("The model fits, but no GPU has %d MiB left for the agent's speech models. On the CPU "
                         "the agent hears with a smaller model, and that run is not comparable. Wait for "
                         "memory to free up." % NEED_MIB["agent"])
    free[agent] -= NEED_MIB["agent"]
    scoring = best(NEED_MIB["scoring"])
    if scoring is None:
        raise SystemExit("No GPU has %d MiB left for the benchmark's scoring recognizer (it always runs on "
                         "a GPU). Wait for memory to free up." % NEED_MIB["scoring"])
    plan.update(agent=agent, scoring=scoring)
    return plan


def exports(plan: dict) -> list:
    if "single" in plan:
        return ["export CUDA_VISIBLE_DEVICES=%s" % plan["single"]] + ["unset %s" % v for v in PLACEMENT]
    lines = ["export CUDA_VISIBLE_DEVICES=%s" % plan["llm"][0],
             "export DUET_LLM_GPUS=%s" % ",".join(plan["llm"]),
             "unset DUET_LLM_TP"]
    if "agent" in plan:
        lines += ["export DUET_AGENT_GPUS=%s" % plan["agent"], "export DUET_SCORING_GPUS=%s" % plan["scoring"]]
    else:
        lines += ["unset DUET_AGENT_GPUS DUET_SCORING_GPUS"]
    return lines


def describe(rows: list, plan: dict) -> str:
    table = ["GPU  free MiB  busy  name"] + ["%3s  %8d  %3d%%  %s" % (r["index"], r["free"], r["util"], r["name"])
                                             for r in rows]
    if "single" in plan:
        choice = "Everything on GPU %s: the same layout as Samsung's re-run." % plan["single"]
    else:
        parts = ["model server on GPU %s%s" % (",".join(plan["llm"]),
                                                " (split over 2)" if len(plan["llm"]) > 1 else "")]
        if "agent" in plan:
            parts += ["agent's speech on GPU %s" % plan["agent"], "scoring recognizer on GPU %s" % plan["scoring"]]
        choice = "No single GPU is free enough; " + ", ".join(parts) + "."
    return "\n".join(table + ["", choice])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="only the model server is needed")
    ap.add_argument("--show", action="store_true", help="print the table and the choice, no shell lines")
    args = ap.parse_args()
    rows = gpus()
    if not rows:
        print("nvidia-smi shows no GPU.", file=sys.stderr)
        return 1
    try:
        plan = choose(rows, args.offline)
    except SystemExit as exc:
        print(describe(rows, {"single": "?"}).rsplit("\n", 1)[0], file=sys.stderr)
        print(exc, file=sys.stderr)
        print("false")   # so that `eval "$(...)" && ...` stops here
        return 1
    print(describe(rows, plan), file=sys.stderr)
    if not args.show:
        print("\n".join(exports(plan)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
