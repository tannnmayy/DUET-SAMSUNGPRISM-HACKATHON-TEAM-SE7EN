#!/usr/bin/env bash
# Validate DUET on the grading platform class: Linux x86_64, Python 3.12,
# NVIDIA GPU with CUDA 12, a clean virtual environment, the pinned
# requirements from public PyPI - exactly what the evaluation portal does.
#
#   git clone <repo> duet && cd duet
#   bash tools/gpu_validate.sh              # everything, ~40-60 min
#   bash tools/gpu_validate.sh --quick      # install + eval once + clock check
#   SKIP_DOCKER=1 bash tools/gpu_validate.sh
#
# Writes results/gpu_validate/<stamp>/ with one log per step and summary.json.
# Send summary.json (and any failing log) back to the team.
set -u
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$ROOT/results/gpu_validate/$STAMP"
mkdir -p "$OUT"
PY="${PYTHON:-python3.12}"
VENV="${VENV:-$HOME/.duet_validate_venv}"
declare -A STATUS

step() {  # step <name> <command...>
  local name="$1"; shift
  local start end
  start=$(date +%s)
  echo "=== $name"
  ( "$@" ) >"$OUT/$name.log" 2>&1
  local rc=$?
  end=$(date +%s)
  STATUS[$name]="exit=$rc seconds=$((end - start))"
  echo "    exit $rc in $((end - start))s  (log: $OUT/$name.log)"
  return 0
}

# --- 0. machine facts ------------------------------------------------------
{
  echo "date: $(date -Is)"; uname -a
  command -v "$PY" && "$PY" --version
  nvidia-smi || echo "NO nvidia-smi"
  df -h "$ROOT" "$HOME" 2>/dev/null
  free -g 2>/dev/null
} >"$OUT/machine.log" 2>&1

# --- 1. clean venv + pinned install (what the portal does) ------------------
step install bash -c "rm -rf '$VENV' && $PY -m venv '$VENV' && '$VENV/bin/pip' install -U pip && '$VENV/bin/pip' install -r requirements.txt && '$VENV/bin/pip' install pytest"
VPY="$VENV/bin/python"

step torch_cuda "$VPY" -c "import torch, ctranslate2; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None); print('ct2 cuda devices', ctranslate2.get_cuda_device_count())"

# --- 2. model download (timed separately: organisers exclude it from setup) --
step prefetch "$VPY" tools/prefetch.py

# --- 3. cold setup() with every enabled model, and peak VRAM ----------------
step cold_setup "$VPY" - <<'EOF'
import asyncio, time, json, torch
from agent.agent import ParticipantAgent
t = time.time()
a = ParticipantAgent(asyncio.Queue(), asyncio.Queue())
asyncio.run(a.setup())
print(json.dumps({"setup_s": round(time.time() - t, 1),
                  "asr": getattr(a.asr, "name", None), "asr_model": getattr(a.asr, "model_name", None),
                  "asr_device": getattr(a.asr, "device", None),
                  "vision": getattr(a.vision, "name", None), "embed": getattr(a.embed, "name", None),
                  "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2) if torch.cuda.is_available() else None}))
EOF

# --- 4. the official procedure ----------------------------------------------
if [ "$QUICK" = 1 ]; then
  step eval "$VPY" eval_submission.py . --time-scale 1 --reps 1 --out "$OUT/eval.json"
else
  step eval "$VPY" eval_submission.py . --time-scale 1 --reps 3 --out "$OUT/eval.json"
fi

# --- 5. clock behaviour on Linux (decides whether the speech floor matters) --
step clockcheck "$VPY" tools/clockcheck.py --reps 3

if [ "$QUICK" = 0 ]; then
  step pytest "$VPY" -m pytest tests -q -p no:cacheprovider
  step chaos "$VPY" tools/chaos.py --templates all --n 96 --seed "$RANDOM"
  step probes_dev "$VPY" tools/probes.py
  step killswitch "$VPY" tools/killswitch.py
  if [ -d tests/vision_bench ]; then
    step vision_bench "$VPY" tools/vision_bench.py
  fi
  if [ -n "${HOLDOUT:-}" ] && [ -d "$HOLDOUT" ]; then
    step probes_holdout "$VPY" tools/probes.py --dir "$HOLDOUT"
  fi
  if [ "${SKIP_DOCKER:-0}" != 1 ] && command -v docker >/dev/null; then
    step docker_build docker build -t duet-validate .
    step docker_run docker run --rm --gpus all duet-validate python eval_submission.py . --time-scale 1 --reps 1
  fi
fi

# --- summary -----------------------------------------------------------------
{
  echo "{"
  echo "  \"stamp\": \"$STAMP\","
  for k in "${!STATUS[@]}"; do echo "  \"$k\": \"${STATUS[$k]}\","; done
  echo "  \"eval_weighted\": \"$(grep -o 'WEIGHTED SCORE: *[0-9.]*' "$OUT/eval.log" | grep -o '[0-9.]*$')\","
  echo "  \"cold_setup\": $(grep -o '{.*}' "$OUT/cold_setup.log" | tail -1 || echo null),"
  echo "  \"clock_early\": \"$(grep -i 'early' "$OUT/clockcheck.log" | head -2 | tr '\n' ' ' | tr -d '"')\""
  echo "}"
} >"$OUT/summary.json"
cat "$OUT/summary.json"
echo "All logs: $OUT"
