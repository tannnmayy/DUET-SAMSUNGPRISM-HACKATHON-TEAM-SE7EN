#!/usr/bin/env bash
# Offline experiments: the thinker on all 100 recordings under different settings,
# officially scored, each result stamped with its code version, settings, weights
# and machine. About 6 minutes per evaluation once the model server is up.
#
#   bash scripts/experiments.sh noise       # the same settings 3 times (seeds 7, 8, 9): the noise band
#   bash scripts/experiments.sh greedy      # temperature 0 instead of the model card's 0.7
#   bash scripts/experiments.sh hearing     # on what the agent's ears hear (large-v3-turbo), at 0.7 and at 0
#   bash scripts/experiments.sh baseline    # noise + greedy + hearing, about 45 minutes
#   bash scripts/experiments.sh fp8         # Qwen's FP8 build instead of the 4-bit one (Ada/Hopper GPUs only)
#   bash scripts/experiments.sh one <tag> [offline_eval options]   # one evaluation, e.g. after a prompt change
#
# It uses the model server that is already running (for example one split over two
# GPUs, see DGX_EXPERIMENTS.md), or starts one with the GPU placement in the
# environment and stops it at the end. Every run is appended to
# results/offline/experiments.log, and the table of all results is rewritten to
# results/offline/EXPERIMENTS.md.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
if [ ! -f .venv/.duet-installed ] || [ ! -f .venv-llm/.duet-installed ] || [ ! -d "$FDB_DATA_DIR" ]; then
  echo "Not installed yet. Run once: bash reproduce.sh --only travel_19_695bd157114f0d2317f88617"
  exit 1
fi
SET="${1:-}"
[ $# -gt 0 ] && shift
LOG=results/offline/experiments.log
mkdir -p results/offline
started=""
cleanup() {
  local status=$?
  if [ -n "$started" ]; then kill -INT "$started" 2>/dev/null || true; wait "$started" 2>/dev/null || true; fi
  # our own test machines only (DUET_AUTO_PUSH=1): results go to GitHub at once, even after a failure
  if [ "${DUET_AUTO_PUSH:-0}" = 1 ] && [ -n "$SET" ]; then
    local tag="auto_offline_${SET}_$(date +%m%d_%H%M)"; [ "$status" -ne 0 ] && tag="${tag}_failed"
    bash scripts/push_results.sh "$tag" || echo "Auto-push failed; later run: bash scripts/push_results.sh <name>"
  fi
}
trap cleanup EXIT

served() {  # the checkpoint the running model server loaded, or nothing
  "$PY_AGENT" - <<'PY'
import json, os, urllib.request
url = os.environ.get("DUET_LLM_BASE_URL", "http://127.0.0.1:18000/v1").rstrip("/") + "/models"
try:
    print(json.loads(urllib.request.urlopen(url, timeout=5).read())["data"][0].get("root", ""))
except Exception:
    print("")
PY
}

ensure_server() {  # $1: the build this set needs (w4a16 or fp8)
  local root
  root="$(served)"
  if [ -n "$root" ]; then
    case "$1:$root" in
      w4a16:*w4a16*|fp8:*FP8*) echo "== model server already running: $root"; return ;;
      *) echo "A model server is running with $root, but this set needs the $1 build."
         echo "Stop that server first (Ctrl-C in its window), then run this again."; exit 1 ;;
    esac
  fi
  echo "== starting the model server ($1 build, GPUs ${DUET_LLM_GPUS:-${CUDA_VISIBLE_DEVICES:-default}})"
  DUET_LLM_VARIANT="$1" "$PY_AGENT" bench/llm_server.py serve > results/offline/llm_server_experiments.out 2>&1 &
  started=$!
  for _ in $(seq 1 360); do
    if [ -n "$(served)" ]; then echo "== model server ready"; return; fi
    if ! kill -0 "$started" 2>/dev/null; then
      echo "The model server stopped while starting:"
      tail -40 results/offline/llm_server_experiments.out
      tail -40 results/llm_server/llm_server.log 2>/dev/null || true
      started=""
      exit 1
    fi
    sleep 5
  done
  echo "The model server did not become ready in 30 minutes; see results/llm_server/llm_server.log"
  exit 1
}

transcribe() {  # what the agent's ears hear on each recording, once (a few minutes on a GPU)
  [ -f results/offline/asr_large-v3-turbo.json ] && return
  local gpu="${DUET_AGENT_GPUS-$(nvidia-smi --query-gpu=index,memory.free --format=csv,noheader,nounits \
               | sort -t, -k2 -nr | head -1 | cut -d, -f1 | tr -d ' ')}"
  local libs
  libs="$(ls -d "$ROOT"/.venv/lib/python3*/site-packages/nvidia/*/lib 2>/dev/null | paste -sd: - || true)"
  echo "== transcribing the 100 recordings with large-v3-turbo on GPU ${gpu:-none} (once)"
  CUDA_VISIBLE_DEVICES="$gpu" LD_LIBRARY_PATH="${libs}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$PY_AGENT" bench/offline_asr.py
}

evaluate() {  # $1: tag; the rest: offline_eval options. Settings come from the environment.
  local tag="$1"
  shift
  echo
  echo "== $tag: $* (temperature ${DUET_TEMPERATURE:-model card}, seed ${DUET_SEED:-7})"
  "$PY_AGENT" bench/offline_eval.py --judge --tag "$tag" "$@" > "results/offline/last_eval.out" 2>&1 || {
    echo "The evaluation failed:"; tail -40 results/offline/last_eval.out; exit 1; }
  grep -E '"(pass@1|tool_f1|arg_acc|resp_qual|errors)"|^wrote|WARNING' results/offline/last_eval.out || true
  local file
  file="$(ls -t results/offline/eval_*_"$tag".json | head -1)"
  mkdir -p results/offline/failures
  "$PY_AGENT" bench/failures.py "$file" --write "results/offline/failures/$(basename "$file" .json).md" > /dev/null
  echo "   failures, grouped: results/offline/failures/$(basename "$file" .json).md"
  echo "$(date '+%F %T') | set=$SET | tag=$tag | options=$* | temperature=${DUET_TEMPERATURE:-card} seed=${DUET_SEED:-7} | $file" >> "$LOG"
}

noise() { for s in 7 8 9; do DUET_SEED=$s evaluate "noise_s$s" --text script; done; }
greedy() { DUET_TEMPERATURE=0 evaluate greedy --text script; }
hearing() { transcribe; evaluate asr_t07 --text asr; DUET_TEMPERATURE=0 evaluate asr_t0 --text asr; }

case "$SET" in
  noise) ensure_server w4a16; noise ;;
  greedy) ensure_server w4a16; greedy ;;
  hearing) ensure_server w4a16; hearing ;;
  baseline) ensure_server w4a16; noise; greedy; hearing ;;
  fp8) ensure_server fp8; evaluate fp8_script --text script; transcribe; evaluate fp8_asr --text asr ;;
  one) tag="${1:?give a tag, e.g.: bash scripts/experiments.sh one prompt_v2 --text script}"; shift
       ensure_server "${DUET_LLM_VARIANT:-w4a16}"; evaluate "$tag" "$@" ;;
  *) sed -n '2,20p' "$0"; exit 1 ;;
esac

"$PY_AGENT" bench/compare_runs.py --write results/offline/EXPERIMENTS.md > /dev/null
echo
echo "Done. Every result so far: results/offline/EXPERIMENTS.md (log: $LOG)"
