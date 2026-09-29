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
import glob
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bench import llm_server  # noqa: E402
from bench.common import DATA_DIR, FDB_DIR, provenance  # noqa: E402

PROVIDER = "duet"


def port_open(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def udp_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def start_livekit(env: dict, logs: Path) -> subprocess.Popen | None:
    """Our own local LiveKit server on free ports (7880-7882, else 17880-17882, ...).
    A server that is already running is never reused: on a shared machine it may
    be someone else's, and their agents would join our benchmark rooms."""
    if env.get("LIVEKIT_URL"):
        print("LiveKit: using", env["LIVEKIT_URL"])
        return None
    binary = shutil.which("livekit-server") or os.environ.get("LIVEKIT_SERVER_BIN", "")
    if not binary or not Path(binary).exists():
        sys.exit("No LIVEKIT_URL and no livekit-server binary. Set LIVEKIT_URL/KEY/SECRET "
                 "(LiveKit Cloud) or put livekit-server on PATH (reproduce.sh downloads it).")
    base = next((b for b in (7880, 17880, 27880, 37880, 47880)
                 if not port_open("127.0.0.1", b) and not port_open("127.0.0.1", b + 1) and udp_free(b + 2)), None)
    if base is None:
        sys.exit("No free ports for a local LiveKit server (tried 7880, 17880, ..., 47880).")
    cfg = logs / "livekit.yaml"
    cfg.write_text("port: %d\nrtc:\n  tcp_port: %d\n  udp_port: %d\n  use_external_ip: false\n"
                   "keys:\n  devkey: secret\n" % (base, base + 1, base + 2), encoding="utf-8")
    env.update(LIVEKIT_URL="ws://127.0.0.1:%d" % base, LIVEKIT_API_KEY="devkey", LIVEKIT_API_SECRET="secret")
    proc = subprocess.Popen([binary, "--config", str(cfg), "--dev", "--bind", "127.0.0.1"],
                            stdout=open(logs / "livekit_server.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        if port_open("127.0.0.1", base):
            print("LiveKit: local server up on port %d (pid %d)" % (base, proc.pid))
            return proc
        time.sleep(0.5)
    sys.exit("livekit-server did not start; see " + str(logs / "livekit_server.log"))


def our_pids() -> set:
    """This run's processes: everything started from here (model server, agent and its
    job processes, LiveKit, the benchmark's runner), plus a model server this user
    started separately (llm_server.py serve), with all their children. Linux only."""
    if not os.path.isdir("/proc"):
        return set()
    children, roots, uid = {}, {os.getpid()}, os.getuid()
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = Path("/proc/%s/stat" % d).read_text()
            children.setdefault(int(stat.rsplit(")", 1)[1].split()[1]), []).append(int(d))
            if os.stat("/proc/" + d).st_uid == uid and \
                    b"vllm.entrypoints" in Path("/proc/%s/cmdline" % d).read_bytes():
                roots.add(int(d))
        except (OSError, ValueError, IndexError):
            continue
    found, todo = set(roots), list(roots)
    while todo:
        for c in children.get(todo.pop(), []):
            if c not in found:
                found.add(c)
                todo.append(c)
    return found


class GpuMonitor(threading.Thread):
    """GPU memory, sampled every 2 s: the evidence that the whole stack (model server,
    agent, scoring recognizer) fits Samsung's 48 GB card.
      - ours: the memory of this run's own processes, summed over every GPU they use.
        On a shared machine this is the number that counts: it leaves out other
        people's jobs, and it adds up the parts when they are placed on several GPUs.
      - card: everything on the first GPU in use, other people's jobs included."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.index = os.environ.get("CUDA_VISIBLE_DEVICES", "0").split(",")[0].strip() or "0"
        self.peak_mib = 0
        self.total_mib = 0
        self.name = ""
        self.ours_peak_mib = 0
        self.ours_at_peak = {}
        self._halt = threading.Event()

    def _card(self) -> None:
        line = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total",
                               "--format=csv,noheader,nounits", "-i", self.index],
                              capture_output=True, text=True, timeout=20).stdout.strip().splitlines()[0]
        name, used, total = [x.strip() for x in line.split(",")]
        self.name, self.total_mib = name, int(float(total))
        self.peak_mib = max(self.peak_mib, int(float(used)))

    def _ours(self) -> None:
        mine = our_pids()
        if not mine:
            return
        rows = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory,gpu_bus_id",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
        now = {}
        for row in rows:
            try:
                pid, used, bus = [x.strip() for x in row.split(",")]
                if int(pid) in mine:
                    now["%s on %s" % (pid, bus)] = int(float(used))
            except ValueError:
                continue
        if sum(now.values()) > self.ours_peak_mib:
            self.ours_peak_mib, self.ours_at_peak = sum(now.values()), now

    def run(self) -> None:
        while not self._halt.is_set():
            for sample in (self._card, self._ours):
                try:
                    sample()
                except Exception:
                    pass
            self._halt.wait(2.0)

    def result(self) -> dict:
        self._halt.set()
        return {"gpu": self.name, "index": self.index, "total_mib": self.total_mib, "peak_used_mib": self.peak_mib,
                "peak_used_gib": round(self.peak_mib / 1024, 1),
                "ours_peak_mib": self.ours_peak_mib, "ours_peak_gib": round(self.ours_peak_mib / 1024, 1),
                "ours_at_peak_mib": self.ours_at_peak}


def placed(env: dict, var: str) -> dict:
    """A copy of env in which one part of the stack sees only the GPUs named in var
    (DUET_AGENT_GPUS, DUET_SCORING_GPUS). Unset: the same GPU as everything else."""
    env = dict(env)
    if var in os.environ:
        env["CUDA_VISIBLE_DEVICES"] = os.environ[var]
    return env


def start_agent(env: dict, logs: Path) -> subprocess.Popen:
    env = placed(env, "DUET_AGENT_GPUS")
    if os.name != "nt":
        # the pip-installed CUDA libraries that faster-whisper (CTranslate2) loads by
        # name; only this process gets them (the model server has its own)
        libs = sorted(glob.glob(os.path.join(sys.prefix, "lib", "python3*", "site-packages", "nvidia", "*", "lib")))
        if libs:
            env["LD_LIBRARY_PATH"] = os.pathsep.join(libs + [env.get("LD_LIBRARY_PATH", "")]).rstrip(os.pathsep)
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
        code = subprocess.run(cmd, cwd=str(FDB_DIR), env=placed(env, "DUET_SCORING_GPUS"), stdout=fh,
                              stderr=subprocess.STDOUT, check=False).returncode
    if code != 0:
        # Scoring now would score whatever results an earlier run left behind
        # (on 28 Sep a crashed runner turned into "total: 1").
        tail = (logs / "runner.log").read_text(encoding="utf-8", errors="replace")[-2500:]
        sys.exit("The benchmark's runner failed (exit code %d), so nothing is scored. Last lines of %s:\n%s"
                 % (code, logs / "runner.log", tail))


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
            subprocess.run(full, cwd=str(FDB_DIR), env=placed(env, "DUET_SCORING_GPUS"), stdout=fh,
                           stderr=subprocess.STDOUT, check=False)
    latency_report = FDB_DIR / ("%s_latency_report.json" % PROVIDER)
    if latency_report.exists():
        shutil.copy(latency_report, out / latency_report.name)
    return summarize(out)


def summarize(out: Path) -> dict:
    try:
        return _summarize(out)
    except Exception as exc:  # the run's own files are all still on disk
        print("WARNING: could not summarize the reports (%s); see the files in %s" % (exc, out))
        return {"error": str(exc)}


def _summarize(out: Path) -> dict:
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
    must not pass for a low score), keep-listening decisions, talker lines, and
    the Gemini tokens and cost (bench/cost_report.py)."""
    from bench import cost_report
    stats = {"conversations": 0, "thinker_errors": 0, "keep_listening": 0, "superseded": 0,
             "talker_lines": 0, "fallback_acks": 0, "thinker_answers": 0, "tool_calls": 0}
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
                stats["fallback_acks"] += bool(ev.get("fallback"))
            elif kind == "tool_done":
                stats["tool_calls"] += 1
            elif kind == "thinker_say":
                stats["thinker_answers"] += 1
    if stats["conversations"]:
        cost = cost_report.report(out)
        stats.update(tokens=cost["by_model"], cost_usd=cost["total_usd"],
                     cost_usd_per_conversation=cost["usd_per_conversation"])
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


def warn_earlier_results(args) -> None:
    """The benchmark's runner skips a recording that already has a result filed
    under this provider name, and its evaluations score every such result in the
    data folder. Say so before a new run is quietly mixed with an earlier one."""
    done = [p.parent for p in Path(args.data_dir).glob("*/result_%s.json" % PROVIDER)]
    if not done:
        return
    if not args.force:
        print("WARNING: %d recordings already have results filed under '%s' from an earlier run. "
              "The runner skips them and the scores reuse them. After any change, add --force."
              % (len(done), PROVIDER))
    if args.only:
        wanted = set(args.only.split(","))
        others = [f for f in done if f.name not in wanted and f.name.rsplit("_", 1)[0] not in wanted]
        if others:
            print("WARNING: the scores also include %d other recordings' earlier results. To score "
                  "this subset alone, file it under a new name: --provider duet_<something>." % len(others))


def preflight(env: dict, out: Path, backend: str) -> None:
    """Before a two-hour run: does the language model answer, and does tool
    calling work? (Gemini: can this key reach the models, and at what tier?)
    A failure stops here instead of producing 100 apologies."""
    module = "duet_voice.llm_local" if backend == "local" else "duet_voice.gemini"
    proc = subprocess.run([sys.executable, "-m", module], cwd=str(REPO), env=env,
                          capture_output=True, text=True, timeout=300)
    lines = [l for l in proc.stdout.splitlines() if l.startswith("{")]
    models = json.loads(lines[-1]) if lines else {"error": (proc.stderr or proc.stdout)[-500:]}
    cfg_path = out / "run_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["models_in_use"] = models
    cfg_path.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    print("Models:", models.get("thinker"), "(thinker),", models.get("talker"), "(talker)")
    for note in models.get("notes", []):
        print("WARNING:", note)
    if models.get("error"):
        print("ERROR:", models["error"])
    if proc.returncode != 0 or "error" in models:
        sys.exit("Preflight failed: the language model cannot be used (see above). Nothing was run.")
    if backend == "local":
        print("Tool calling:", models.get("tool_calling"))


def effective_config(env: dict) -> dict:
    """The settings the agent process will run with: its defaults plus overrides,
    resolved exactly as the agent resolves them, and the sampling actually used."""
    code = ("import json, dataclasses; from duet_voice.config import CONFIG; from duet_voice import gemini, llm_local; "
            "c = dataclasses.asdict(CONFIG); local = CONFIG.llm_backend == 'local'; "
            "c['thinker_sampling'] = llm_local.sampling() if local else gemini.sampling(CONFIG.thinker_model); "
            "c['talker_sampling'] = llm_local.sampling() if local else gemini.sampling(CONFIG.talker_model); "
            "print(json.dumps(c))")
    try:
        out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env, capture_output=True,
                             text=True, timeout=120, check=True).stdout
        return json.loads(out.strip().splitlines()[-1])
    except Exception as exc:  # never block a run on the record of it
        return {"error": str(exc)[:200]}


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
    ap.add_argument("--llm-python", default=llm_server.default_python(),
                    help="interpreter of the model server's environment (.venv-llm), to start vLLM")
    args = ap.parse_args()
    # the runner and the evaluations run inside the benchmark's folder, so a relative
    # path given here must not be looked up from there (a bare command name stays as is)
    for name in ("bench_python", "llm_python"):
        path = getattr(args, name)
        if path and os.path.dirname(path):
            setattr(args, name, os.path.abspath(path))
    args.data_dir = os.path.abspath(args.data_dir)
    if args.run_dir:
        args.run_dir = os.path.abspath(args.run_dir)
    PROVIDER = args.provider
    from duet_voice.config import CONFIG
    backend = CONFIG.llm_backend

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
        # the agent's effective settings (models, thinking, sampling, seed, listening)
        "agent": effective_config(env),
        "overrides": {k: v for k, v in env.items() if k.startswith(("DUET_", "FDB_"))},
        "livekit": env.get("LIVEKIT_URL", "local dev server"),
        # the code version, machine, GPUs and placement that produced this run
        "provenance": provenance(),
        "model_server": "started by this run" if backend == "local" and not args.dry_run
                        and not llm_server.healthy() else "already running at " + CONFIG.llm_base_url,
    }, indent=1), encoding="utf-8")

    if not args.eval_only:
        warn_earlier_results(args)
    monitor = GpuMonitor()
    monitor.start()
    server = agent = llm = None
    try:
        if backend == "local" and not args.dry_run:
            # the model server first: the preflight tests it, and the evaluations'
            # local proxy judge uses it too
            llm = llm_server.start(args.llm_python, out, env)
        if not args.eval_only and not args.dry_run:
            preflight(env, out, backend)
        if not args.eval_only:
            log_path = Path("/tmp/agent_tool_calls.log")  # fixed by the benchmark's runner
            try:
                if not args.only and log_path.exists():
                    log_path.unlink()
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a", encoding="utf-8"):
                    pass
            except OSError as exc:
                sys.exit("Cannot write %s (%s). The benchmark reads tool calls from that exact path; "
                         "on a shared machine it may belong to another user. Remove it or run as that "
                         "user, then start again." % (log_path, exc))
            server = start_livekit(env, out)
            agent = start_agent(env, out)
            run_runner(env, args, out)
            collect(args, out)
        if not args.no_eval:
            summary = run_evaluations(env, args, out)
            summary["gpu"] = monitor.result()
            summary["git_commit"] = provenance()["git_commit"]
            (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
            print(json.dumps(summary, indent=1))
        print("Run folder:", out)
    finally:
        for proc in (agent, server):
            stop(proc)
        llm_server.stop(llm)
        gpu = monitor.result()
        print("GPU %s: peak %.1f GiB used of %.1f GiB (the whole card, other users' jobs included)"
              % (gpu["gpu"] or gpu["index"], gpu["peak_used_mib"] / 1024, gpu["total_mib"] / 1024))
        if gpu["ours_peak_mib"]:
            print("This run's own processes: peak %.1f GiB over all GPUs used (fits a 48 GB card at 43 GiB or less)"
                  % (gpu["ours_peak_mib"] / 1024))
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
