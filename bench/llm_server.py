#!/usr/bin/env python3
"""The local model server: vLLM serving Qwen3-30B-A3B-Instruct-2507 on this GPU.

    python bench/llm_server.py plan        # which weights and how much memory, on this GPU
    python bench/llm_server.py prefetch    # download those weights (before any timed run)
    python bench/llm_server.py serve       # run the server in the foreground (Ctrl-C stops it)

bench/run_live.py and bench/offline_eval.py --start-server start and stop it
themselves; `serve` is for running the agent under another harness.

Which weights. Both are Apache-2.0 public checkpoints, pinned to exact revisions:
  - w4a16 (the default, on every GPU): Red Hat's 4-bit build (16.7 GB), made with
    vLLM's llm-compressor. Its Marlin kernels run on Ampere, Ada and Hopper alike,
    so the configuration Samsung runs is the one we measured (first on an A100,
    28 Sep 2026), whatever its 48 GB card is.
  - fp8 (DUET_LLM_VARIANT=fp8, for comparison runs): Qwen's own FP8 build (31.2 GB).
    Its block-wise FP8 kernels need compute capability 8.9 or newer (Ada, Hopper).

How much memory. vLLM is given a fixed budget in GiB, not a fraction of the GPU,
so it takes the same memory on an 80 GB H100 as on a 48 GB card (about 44.7 GiB
usable). The rest is for the agent's speech models (~3.5 GiB) and the benchmark's
own scoring recognizer (~4-5 GiB):
  - w4a16: 22 GiB = 15.6 GiB weights + working memory + ~50k tokens of KV cache
           (the whole stack: about 31 GiB, so a 48 GB card keeps ~13 GiB spare)
  - fp8:   33 GiB = 29.1 GiB weights + working memory + ~22k tokens of KV cache
Override with DUET_LLM_GPU_GIB.

Where it runs. By default on the GPU in CUDA_VISIBLE_DEVICES, like everything
else. On a shared machine where no single GPU is free, DUET_LLM_GPUS=4,7 puts the
server on those GPUs and splits the model across them (tensor parallelism), each
holding half of the budget plus 1.5 GiB of working memory. This is for our own
experiments; Samsung's re-run uses one GPU.

The server listens on 127.0.0.1 only: it is part of the submission, running on the
evaluation machine, not a server of ours.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]

WEIGHTS = {
    "fp8": {"repo": "Qwen/Qwen3-30B-A3B-Instruct-2507-FP8",
            "revision": "5a5a776300a41aaa681dd7ff0106608ef2bc90db", "size_gb": 31.2, "budget_gib": 33.0},
    "w4a16": {"repo": "RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16",
              "revision": "e9c59cdcca9d73f24adeda09660af904a79f509f", "size_gb": 16.7, "budget_gib": 22.0},
}
SERVED_NAME = os.environ.get("DUET_LLM_MODEL", "qwen3-30b-a3b-instruct-2507")
BASE_URL = os.environ.get("DUET_LLM_BASE_URL", "http://127.0.0.1:18000/v1")
# Our longest request (instructions, the 13 tools, a few exchanges) is about 6k
# tokens. vLLM refuses to start unless its KV cache can hold one request of this
# length, so a modest limit keeps a safe margin inside the 33 GiB budget.
MAX_MODEL_LEN = int(os.environ.get("DUET_LLM_MAX_LEN", "12288"))


def server_gpus() -> str:
    """The GPUs the server runs on: DUET_LLM_GPUS if set, else CUDA_VISIBLE_DEVICES."""
    return os.environ.get("DUET_LLM_GPUS", os.environ.get("CUDA_VISIBLE_DEVICES", "")).strip()


def tensor_parallel() -> int:
    """How many GPUs the model is split across: one per GPU in DUET_LLM_GPUS."""
    explicit = os.environ.get("DUET_LLM_TP", "").strip()
    if explicit:
        return max(1, int(explicit))
    placed = os.environ.get("DUET_LLM_GPUS", "").strip()
    return len([g for g in placed.split(",") if g.strip()]) if placed else 1


def gpu() -> dict:
    """The first GPU the server will use."""
    visible = server_gpus().split(",")[0].strip()
    query = ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,compute_cap", "--format=csv,noheader,nounits"]
    if visible:
        query += ["-i", visible]
    try:
        line = subprocess.run(query, capture_output=True, text=True, timeout=30, check=True).stdout.strip().splitlines()[0]
    except Exception as exc:
        raise SystemExit("No NVIDIA GPU visible (nvidia-smi: %s). The local model needs one." % exc)
    index, name, total, used, cc = [x.strip() for x in line.split(",")]
    return {"index": index, "name": name, "total_mib": int(float(total)), "used_mib": int(float(used)),
            "compute_capability": float(cc)}


def plan() -> dict:
    g = gpu()
    variant = os.environ.get("DUET_LLM_VARIANT") or "w4a16"
    if variant not in WEIGHTS:
        raise SystemExit("DUET_LLM_VARIANT must be one of %s" % list(WEIGHTS))
    notes = []
    if variant == "fp8" and g["compute_capability"] < 8.9:
        notes.append("the fp8 build's kernels need compute capability 8.9 or newer; this GPU has %s"
                     % g["compute_capability"])
    w = WEIGHTS[variant]
    budget_gib = float(os.environ.get("DUET_LLM_GPU_GIB", w["budget_gib"]))
    tp = tensor_parallel()
    per_gpu_gib = budget_gib if tp == 1 else round(budget_gib / tp + 1.5, 2)
    total_gib = g["total_mib"] / 1024.0
    fraction = round(per_gpu_gib / total_gib, 3)
    if fraction > 0.92:
        notes.append("the budget is %.0f GiB but this GPU has %.1f GiB in total" % (per_gpu_gib, total_gib))
    free_gib = (g["total_mib"] - g["used_mib"]) / 1024.0
    shares_gpu = tp == 1 and not os.environ.get("DUET_LLM_GPUS")
    if free_gib < per_gpu_gib + (8 if shares_gpu else 0):
        notes.append("only %.1f GiB free on GPU %s: the model needs %.1f GiB there%s"
                     % (free_gib, g["index"], per_gpu_gib,
                        " plus ~8 GiB for the speech models and the scoring recognizer" if shares_gpu else ""))
    return {"gpu": g, "gpus": server_gpus(), "tensor_parallel": tp, "variant": variant, "repo": w["repo"],
            "revision": w["revision"], "size_gb": w["size_gb"], "budget_gib": budget_gib,
            "per_gpu_gib": per_gpu_gib, "gpu_memory_utilization": fraction, "served_name": SERVED_NAME,
            "base_url": BASE_URL, "max_model_len": MAX_MODEL_LEN, "notes": notes}


def command(python: str, p: dict) -> list:
    port = BASE_URL.rstrip("/").rsplit(":", 1)[-1].split("/")[0]
    cmd = [python, "-m", "vllm.entrypoints.openai.api_server",
           "--model", p["repo"], "--revision", p["revision"],
           "--served-model-name", p["served_name"],
           "--host", "127.0.0.1", "--port", port,
           "--gpu-memory-utilization", str(p["gpu_memory_utilization"]),
           "--max-model-len", str(p["max_model_len"]),
           "--max-num-seqs", "8",
           # Qwen3 writes tool calls in the hermes format; this turns them into
           # standard OpenAI tool calls
           "--enable-auto-tool-choice", "--tool-call-parser", "hermes",
           "--seed", os.environ.get("DUET_SEED", "7")]
    if p.get("tensor_parallel", 1) > 1:
        cmd += ["--tensor-parallel-size", str(p["tensor_parallel"])]
    return cmd


def server_env(p: dict, env: Optional[dict] = None) -> dict:
    """The server's environment: only its own GPUs visible."""
    env = dict(env or os.environ)
    if p.get("gpus"):
        env["CUDA_VISIBLE_DEVICES"] = p["gpus"]
    if p.get("tensor_parallel", 1) > 1:
        env.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")
    return env


def healthy(base_url: str = BASE_URL, timeout: float = 3.0) -> bool:
    root = base_url.rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    try:
        with urllib.request.urlopen(root + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def default_python() -> str:
    """The model server's own environment (reproduce.sh creates it)."""
    explicit = os.environ.get("DUET_LLM_PYTHON")
    if explicit:
        return explicit
    for rel in (".venv-llm/bin/python", ".venv-llm/Scripts/python.exe"):
        if (REPO / rel).exists():
            return str(REPO / rel)
    return ""


def start(python: str, log_dir: Path, env: Optional[dict] = None) -> Optional[subprocess.Popen]:
    """Start the server unless one already answers; wait until it is ready.
    Returns the process we started (None if one was already running)."""
    if healthy():
        print("Model server: already running at", BASE_URL)
        return None
    if not python:
        raise SystemExit("No model server environment: run reproduce.sh (it creates .venv-llm), "
                         "or set DUET_LLM_PYTHON, or start a server and set DUET_LLM_BASE_URL.")
    p = plan()
    for note in p["notes"]:
        print("WARNING:", note)
    if p["gpu_memory_utilization"] > 0.95:
        raise SystemExit("GPU %s (%s, %.1f GiB) is too small for the model's %.1f GiB budget."
                         % (p["gpu"]["index"], p["gpu"]["name"], p["gpu"]["total_mib"] / 1024, p["per_gpu_gib"]))
    free_gib = (p["gpu"]["total_mib"] - p["gpu"]["used_mib"]) / 1024.0
    if free_gib < p["per_gpu_gib"]:
        raise SystemExit("GPU %s has only %.1f GiB free and the model needs %.1f GiB there (other jobs are "
                         "using it). Pick a freer GPU, or split the model over two: see "
                         "`python bench/place_gpus.py`." % (p["gpu"]["index"], free_gib, p["per_gpu_gib"]))
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "llm_plan.json").write_text(json.dumps(p, indent=1), encoding="utf-8")
    log_path = log_dir / "llm_server.log"
    cmd = command(python, p)
    where = p["gpus"] or p["gpu"]["index"]
    print("Model server: %s (%s, %.1f GiB budget on %s, GPU %s%s); log: %s"
          % (p["repo"], p["variant"], p["budget_gib"], p["gpu"]["name"], where,
             ", split over %d GPUs" % p["tensor_parallel"] if p["tensor_parallel"] > 1 else "", log_path))
    proc = subprocess.Popen(cmd, stdout=open(log_path, "w", encoding="utf-8"), stderr=subprocess.STDOUT,
                            env=server_env(p, env), start_new_session=(os.name != "nt"))
    deadline = time.time() + float(os.environ.get("DUET_LLM_START_TIMEOUT", "1800"))
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            raise SystemExit("The model server exited while starting. Last lines of %s:\n%s" % (log_path, tail))
        if healthy():
            print("Model server: ready at", BASE_URL)
            return proc
        time.sleep(5)
    stop(proc)
    raise SystemExit("The model server did not become ready in time; see " + str(log_path))


def stop(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGINT)
        else:
            proc.terminate()
        proc.wait(timeout=60)
    except Exception:
        proc.kill()


def prefetch(python: str) -> None:
    p = plan()
    print("Downloading %s @ %s (%.1f GB) ..." % (p["repo"], p["revision"][:7], p["size_gb"]))
    code = ("import os; os.environ.setdefault('HF_HUB_ENABLE_HF_TRANSFER', '1'); "
            "from huggingface_hub import snapshot_download; "
            "print(snapshot_download(%r, revision=%r))" % (p["repo"], p["revision"]))
    subprocess.run([python, "-c", code], check=True)


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if what == "plan":
        p = plan()
        print(json.dumps(p, indent=1))
        print("command:", " ".join(command(default_python() or "python", p)))
    elif what == "prefetch":
        prefetch(default_python() or sys.executable)
    elif what == "serve":
        proc = start(default_python(), REPO / "results" / "llm_server")
        if proc is None:
            return 0
        print("Serving; Ctrl-C stops it.")
        try:
            proc.wait()
        except KeyboardInterrupt:
            stop(proc)
    elif what == "health":
        print("ready" if healthy() else "not running")
        return 0 if healthy() else 1
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
