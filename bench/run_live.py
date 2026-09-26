#!/usr/bin/env python3
"""The official FDB-v3 pipeline against the DUET agent, end to end.

    python bench/run_live.py                 # all 100 items, official runner + evaluations
    python bench/run_live.py --only travel_19,housing_04 --no-eval
    python bench/run_live.py --eval-only --run-dir results/live/<stamp>

Steps:
  1. LiveKit: use LIVEKIT_URL if set (e.g. LiveKit Cloud); otherwise start a local
     `livekit-server --dev` (keys devkey/secret) - no account needed.
  2. Start the agent worker (`python -m duet_voice.agent start`).
  3. Run the benchmark's own `run_tool_benchmark_all_released.py --provider duet`,
     which streams each input.wav into a fresh room, records the agent's audio,
     runs ASR and collects tool calls from /tmp/agent_tool_calls.log.
  4. Run the benchmark's three evaluation scripts (tool F1 and argument accuracy,
     strict pass rate, latency), with the gpt-4o judge when OPENAI_API_KEY is set.
  5. Copy every report, the per-item result JSONs, the agent log and our traces
     into results/live/<stamp>/, and print the headline numbers.

The benchmark scripts are run unmodified. The one exception is the scoring ASR
on machines where NVIDIA NeMo cannot be installed (Windows): `--scoring-asr
whisper` swaps in faster-whisper for Parakeet, and the run is labelled so.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bench.common import DATA_DIR, FDB_DIR  # noqa: E402

PROVIDER = "duet"


def port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def start_livekit(env: dict, logs: Path) -> subprocess.Popen | None:
    if env.get("LIVEKIT_URL"):
        print("LiveKit: using", env["LIVEKIT_URL"])
        return None
    env.update(LIVEKIT_URL="ws://127.0.0.1:7880", LIVEKIT_API_KEY="devkey", LIVEKIT_API_SECRET="secret")
    if port_open("127.0.0.1", 7880):
        print("LiveKit: a local server is already running on :7880")
        return None
    binary = shutil.which("livekit-server") or os.environ.get("LIVEKIT_SERVER_BIN", "")
    if not binary or not Path(binary).exists():
        sys.exit("No LIVEKIT_URL and no livekit-server binary. Set LIVEKIT_URL/KEY/SECRET "
                 "(LiveKit Cloud) or put livekit-server on PATH (scripts/get_livekit_server.sh).")
    proc = subprocess.Popen([binary, "--dev", "--bind", "127.0.0.1"], stdout=open(logs / "livekit_server.log", "w"),
                            stderr=subprocess.STDOUT)
    for _ in range(60):
        if port_open("127.0.0.1", 7880):
            print("LiveKit: local dev server up (pid %d)" % proc.pid)
            return proc
        time.sleep(0.5)
    sys.exit("livekit-server did not start; see " + str(logs / "livekit_server.log"))


def start_agent(env: dict, logs: Path) -> subprocess.Popen:
    agent_log = open(logs / "agent.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "duet_voice.agent", "start"], cwd=str(REPO), env=env,
                            stdout=agent_log, stderr=subprocess.STDOUT)
    # wait until the worker has registered and its first process has loaded the models
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(2)
        text = (logs / "agent.log").read_text(encoding="utf-8", errors="replace")
        if proc.poll() is not None:
            sys.exit("agent exited early; see " + str(logs / "agent.log"))
        if "Traceback" in text and "registered worker" in text and "ready in" not in text:
            print("Agent: a job process failed to start; see", logs / "agent.log")
        if "registered worker" in text and "ready in" in text:
            print("Agent: registered and warm (pid %d)" % proc.pid)
            time.sleep(3)
            return proc
    print("Agent: still warming after 10 min; continuing anyway")
    return proc


def run_runner(env: dict, args, logs: Path) -> None:
    if args.scoring_asr == "parakeet" and not args.only:
        # the unmodified official script
        cmd = [args.bench_python, str(FDB_DIR / "run_tool_benchmark_all_released.py")]
    else:
        cmd = [args.bench_python, str(REPO / "bench" / "fdb_runner.py"), "--scoring-asr", args.scoring_asr]
        if args.only:
            cmd += ["--only", args.only]
    cmd += ["--provider", PROVIDER, "--root_dir", str(args.data_dir)]
    if args.force:
        cmd.append("--force")
    print("Runner:", " ".join(cmd))
    with open(logs / "runner.log", "w", encoding="utf-8") as fh:
        subprocess.run(cmd, cwd=str(FDB_DIR), env=env, stdout=fh, stderr=subprocess.STDOUT, check=False)


def run_evaluations(env: dict, args, out: Path) -> dict:
    wrapper = str(REPO / "bench" / "official_eval.py")
    steps = [
        ("tool_calls", ["evaluate_tool_calls.py", "--benchmark", "benchmark_data_v2.json", "--results-dir",
                        str(args.data_dir), "--provider", PROVIDER, "--output", str(out / (PROVIDER + "_evaluation_report.json"))]),
        ("pass_rate", ["evaluate_pass_rate.py", "--benchmark", "benchmark_data_v2.json", "--results-dir",
                       str(args.data_dir), "--provider", PROVIDER, "--output", str(out / (PROVIDER + "_pass_rate_report.json"))]),
        ("latency", ["analyze_tool_latency.py", "--results-dir", str(args.data_dir), "--provider", PROVIDER]),
    ]
    for name, cmd in steps:
        if name != "latency" and not args.no_judge:
            cmd = cmd + ["--use-llm"]
        full = [args.bench_python, wrapper] + cmd
        print("Eval:", name)
        with open(out / ("eval_%s.log" % name), "w", encoding="utf-8") as fh:
            subprocess.run(full, cwd=str(FDB_DIR), env=env, stdout=fh, stderr=subprocess.STDOUT, check=False)
    latency_report = FDB_DIR / ("%s_latency_report.json" % PROVIDER)
    if latency_report.exists():
        shutil.copy(latency_report, out / latency_report.name)
    return summarize(out)


def summarize(out: Path) -> dict:
    summary: dict = {}
    ev = out / (PROVIDER + "_evaluation_report.json")
    pr = out / (PROVIDER + "_pass_rate_report.json")
    lat = out / (PROVIDER + "_latency_report.json")
    if ev.exists():
        d = json.loads(ev.read_text(encoding="utf-8"))
        summary.update(tool_selection_f1=d["by_metric"]["tool_selection_acc"], argument_acc=d["by_metric"]["argument_acc"],
                       response_qual=d["by_metric"]["response_qual"], turn_take_rate=d["turn_taking"]["turn_take_rate"],
                       interruption_rate=d["latency"]["interruption_rate"],
                       avg_response_latency_s=d["latency"]["avg_response_latency_s"])
    if pr.exists():
        d = json.loads(pr.read_text(encoding="utf-8"))
        summary.update(pass_at_1=d["overall_pass_rate"], passed=d["passed"], total=d["total_scenarios"],
                       pass_by_difficulty=d["by_difficulty"], pass_by_disfluency=d["by_disfluency_feature"],
                       pass_by_domain=d["by_domain"])
    if lat.exists():
        d = json.loads(lat.read_text(encoding="utf-8"))
        agg = d.get("aggregate", d)
        for k in ("first_response_latency", "tool_call_latency", "task_completion_latency"):
            if k in agg:
                summary[k + "_mean_s"] = agg[k].get("mean")
    summary["agent"] = trace_stats(out)
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def trace_stats(out: Path) -> dict:
    """What the agent did, from our traces: model errors (a key or quota problem
    must not pass for a low score), keep-listening decisions, talker lines, and the
    tokens used (for the cost analysis)."""
    stats = {"conversations": 0, "thinker_errors": 0, "keep_listening": 0, "superseded": 0,
             "talker_lines": 0, "thinker_answers": 0, "tool_calls": 0,
             "tokens": {"input": 0, "output": 0, "thinking": 0, "calls": 0}}
    first_error = ""
    for path in sorted((out / "traces").glob("*.jsonl")):
        stats["conversations"] += 1
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("kind")
            if kind == "thinker_error":
                stats["thinker_errors"] += 1
                first_error = first_error or str(ev.get("text", ""))[:200]
            elif kind == "thinker_listen":
                stats["keep_listening"] += 1
            elif kind == "superseded":
                stats["superseded"] += 1
            elif kind == "talker" and ev.get("text"):
                stats["talker_lines"] += 1
            elif kind == "tool_done":
                stats["tool_calls"] += 1
            elif kind == "thinker_say":
                stats["thinker_answers"] += 1
                for k, v in (ev.get("usage") or {}).items():
                    if k in stats["tokens"]:
                        stats["tokens"][k] += int(v or 0)
    if first_error:
        stats["first_thinker_error"] = first_error
        print("WARNING: %d thinker errors in this run, e.g. %s" % (stats["thinker_errors"], first_error))
    return stats


def collect(args, out: Path) -> None:
    items = out / "items"
    items.mkdir(exist_ok=True)
    for folder in Path(args.data_dir).iterdir():
        res = folder / ("result_%s.json" % PROVIDER)
        if res.exists():
            shutil.copy(res, items / (folder.name + ".json"))
    for p in ("/tmp/agent_tool_calls.log", "/tmp/agent_heartbeat.log"):
        if Path(p).exists():
            shutil.copy(p, out / Path(p).name)


def main() -> int:
    global PROVIDER
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--provider", default=PROVIDER,
                    help="name the results are filed under (result_<provider>.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="no LLM: the agent answers every turn with 'Okay.' (checks listening and plumbing)")
    ap.add_argument("--only", default="", help="comma-separated example ids or folder names")
    ap.add_argument("--force", action="store_true", help="re-run items that already have results")
    ap.add_argument("--no-eval", action="store_true")
    ap.add_argument("--eval-only", action="store_true")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--run-dir", default="")
    ap.add_argument("--bench-python", default=sys.executable,
                    help="interpreter for the benchmark runner and evaluations (its own venv)")
    ap.add_argument("--scoring-asr", choices=["parakeet", "whisper"],
                    default="whisper" if os.name == "nt" else "parakeet")
    args = ap.parse_args()
    PROVIDER = args.provider

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = Path(args.run_dir) if args.run_dir else REPO / "results" / "live" / stamp
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", FDB_V3_DIR=str(FDB_DIR),
               DUET_TRACE_DIR=str(out / "traces"), PYTHONPATH=str(REPO))
    if args.dry_run:
        env["DUET_DRY_RUN"] = "1"
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    if ffmpeg_dir:
        env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")
    (out / "run_config.json").write_text(json.dumps({
        "stamp": stamp, "provider": PROVIDER, "dry_run": args.dry_run,
        "scoring_asr": args.scoring_asr, "only": args.only,
        "config": {k: v for k, v in env.items() if k.startswith(("DUET_", "FDB_"))},
        "livekit": env.get("LIVEKIT_URL", "local dev server"),
    }, indent=1), encoding="utf-8")

    server = agent = None
    try:
        if not args.eval_only:
            if not args.only:
                for p in ("/tmp/agent_tool_calls.log",):
                    if Path(p).exists():
                        Path(p).unlink()
            server = start_livekit(env, out)
            agent = start_agent(env, out)
            run_runner(env, args, out)
            collect(args, out)
        if not args.no_eval:
            summary = run_evaluations(env, args, out)
            print(json.dumps(summary, indent=1))
        print("Run folder:", out)
    finally:
        for proc in (agent, server):
            stop(proc)
    return 0


def stop(proc: subprocess.Popen | None) -> None:
    """Stop a process and its children (the agent worker forks job processes)."""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
