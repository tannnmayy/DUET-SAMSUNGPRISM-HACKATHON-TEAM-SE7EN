#!/usr/bin/env bash
# DUET on Full-Duplex-Bench v3 in one command: install, fetch the benchmark and its
# data, start the local model server, run the agent through the benchmark's own
# pipeline, evaluate, and print the scores.
#
#   bash reproduce.sh                                 # all 100 items (~2 h: the audio streams in real time)
#   bash reproduce.sh --only travel_19,housing_04     # a quick subset
#   bash reproduce.sh --dry-run                       # no language model: listening and plumbing only
#
# No API key is needed. The language model, Qwen3-30B-A3B-Instruct-2507 (open weights,
# Apache-2.0), runs on the same GPU through vLLM. Optional settings:
#   OPENAI_API_KEY                                  the official gpt-4o judge (otherwise a labelled
#                                                   proxy judge: the same local model)
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET  LiveKit Cloud instead of a local LiveKit server
#   FDB_DATA_DIR                                    the benchmark audio, if you already have it extracted
#   CUDA_VISIBLE_DEVICES                            which GPU (default: the one with the most free memory)
#   DUET_LLM_BACKEND=gemini and GOOGLE_API_KEY       the Gemini API instead of the local model
#
# Needs: Linux x86_64; one NVIDIA GPU with 48 GB (the model server takes 33 GiB, the
# speech models and the benchmark's scoring recognizer most of the rest); an NVIDIA
# driver for CUDA 12.x or 13.x; git, curl and internet access; about 90 GB of disk.
# No sudo: Python 3.11 (through a pinned uv), ffmpeg and the LiveKit server are
# fetched into third_party/, and every Python package is installed at the exact
# version of our runs (requirements*.lock). Results land in results/live/<time>/.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
source scripts/env.sh

FDB_REPO="https://github.com/DanielLin94144/Full-Duplex-Bench.git"
FDB_COMMIT="3e799c45a045256f47d5f1c9cda90157e2d2ec9e"
DATA_URL="https://drive.usercontent.google.com/download?id=1SO_4MTazWQ_jvCx0dtmpQ-t40bdd07yz&export=download&confirm=t"
DATA_SHA256="37545bd896f81718136598cf5be25d42ea9aa22efcd91f58370938d05d7d672f"
LK_VERSION="1.13.7"
UV_VERSION="0.12.19"
PYVER="${DUET_PYTHON_VERSION:-3.11}"
BACKEND="${DUET_LLM_BACKEND:-local}"
DRY_RUN=0
for a in "$@"; do [ "$a" = "--dry-run" ] && DRY_RUN=1; done

say() { printf '\n== %s\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing: $1 ($2)"; exit 1; }; }

# --- 0. what this machine has ---------------------------------------------------------------
need git "install git"; need curl "install curl"; need tar "install tar"; need sha256sum "install coreutils"
if [ "$BACKEND" = "gemini" ]; then
  : "${GOOGLE_API_KEY:?DUET_LLM_BACKEND=gemini needs GOOGLE_API_KEY}"
fi
if [ -n "${LIVEKIT_URL:-}" ] && { [ -z "${LIVEKIT_API_KEY:-}" ] || [ -z "${LIVEKIT_API_SECRET:-}" ]; }; then
  echo "LIVEKIT_URL is set, so LIVEKIT_API_KEY and LIVEKIT_API_SECRET are needed too"; exit 1
fi
if command -v nvidia-smi >/dev/null; then
  echo "GPU ${CUDA_VISIBLE_DEVICES:-0}: $(nvidia-smi -i "${CUDA_VISIBLE_DEVICES:-0}" \
        --query-gpu=name,driver_version,memory.total,memory.free --format=csv,noheader)"
elif [ "$BACKEND" = "local" ] && [ "$DRY_RUN" = 0 ]; then
  echo "No NVIDIA GPU visible (nvidia-smi not found): the local model needs one."; exit 1
fi

# --- 1. uv (pinned) and three Python environments ----------------------------------------------
# uv supplies its own Python 3.11, which carries the C headers that vLLM's kernel
# compiler (Triton) needs: no system Python, python3-venv or python3-dev required.
if ! command -v uv >/dev/null; then
  say "uv $UV_VERSION"
  mkdir -p third_party/uv
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" \
    | env UV_INSTALL_DIR="$ROOT/third_party/uv" UV_NO_MODIFY_PATH=1 sh >/dev/null
  export PATH="$ROOT/third_party/uv:$PATH"
fi
make_env() {  # $1: folder, $2: lock file. A marker file records a finished install,
  local dir="$1" lock="$2"  # so an interrupted one is completed on the next run.
  [ -f "$dir/.duet-installed" ] && return 0
  say "Python environment $dir (from $lock)"
  [ -x "$dir/bin/python" ] || uv venv -q --seed --managed-python --python "$PYVER" "$dir"
  uv pip install -q --python "$dir/bin/python" -r "$lock"
  touch "$dir/.duet-installed"
}
make_env .venv requirements.lock                  # the agent
make_env .venv-bench requirements-bench.lock      # the benchmark's runner and scoring recognizer (NeMo)
if [ "$BACKEND" = "local" ]; then
  make_env .venv-llm requirements-llm.lock        # the model server (vLLM)
fi

# --- 2. ffmpeg (the benchmark's runner needs it) --------------------------------------------------
if ! command -v ffmpeg >/dev/null; then
  say "ffmpeg (static build, into third_party/)"
  base="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
  name="ffmpeg-master-latest-linux64-gpl.tar.xz"
  mkdir -p third_party/downloads third_party/ffmpeg
  curl -sSL -o "third_party/downloads/$name" "$base/$name"
  (cd third_party/downloads && curl -sSL "$base/checksums.sha256" | grep " $name\$" | sha256sum -c -)
  tar -xJf "third_party/downloads/$name" -C third_party/downloads
  cp third_party/downloads/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg \
     third_party/downloads/ffmpeg-master-latest-linux64-gpl/bin/ffprobe third_party/ffmpeg/
  export PATH="$ROOT/third_party/ffmpeg:$PATH"
fi

# --- 3. the benchmark, pinned, and its audio (Google Drive link from the v3 README) --------------
if [ ! -d third_party/Full-Duplex-Bench/.git ]; then
  say "Full-Duplex-Bench @ ${FDB_COMMIT:0:7}"
  git clone -q "$FDB_REPO" third_party/Full-Duplex-Bench
fi
git -C third_party/Full-Duplex-Bench checkout -q "$FDB_COMMIT"
# Google Drive sometimes refuses a busy file for a while, so the download is retried;
# a zip placed at $ZIP by hand (or FDB_DATA_DIR pointing at an extracted copy) is used as is.
ZIP="third_party/downloads/fdb_v3_data_released.zip"
if [ ! -d "$FDB_DATA_DIR" ]; then
  say "benchmark data (736 MB)"
  mkdir -p third_party/downloads
  for attempt in 1 2 3; do
    if [ -f "$ZIP" ] && echo "$DATA_SHA256  $ZIP" | sha256sum -c --status -; then break; fi
    curl -L --fail -o "$ZIP" "$DATA_URL" || true
    if echo "$DATA_SHA256  $ZIP" | sha256sum -c --status -; then break; fi
    echo "download attempt $attempt did not match the checksum; retrying in 30 s"; sleep 30
  done
  if ! echo "$DATA_SHA256  $ZIP" | sha256sum -c --status -; then
    echo "Could not download the benchmark data. Download it from the Google Drive link in the"
    echo "FDB-v3 README to $ROOT/$ZIP (or set FDB_DATA_DIR to an extracted copy) and run again."
    exit 1
  fi
  "$PY_AGENT" -c 'import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
z.extractall(sys.argv[2], [n for n in z.namelist() if not n.startswith("__MACOSX")])' "$ROOT/$ZIP" "$FDB_V3_DIR"
fi
echo "benchmark data: $FDB_DATA_DIR ($(find "$FDB_DATA_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l) recordings)"

# --- 4. LiveKit: your Cloud project, or a local server (checksum-verified) ------------------------
if [ -z "${LIVEKIT_URL:-}" ] && [ -z "${LIVEKIT_SERVER_BIN:-}" ] && ! command -v livekit-server >/dev/null; then
  say "local LiveKit server v$LK_VERSION"
  mkdir -p third_party/livekit
  base="https://github.com/livekit/livekit/releases/download/v$LK_VERSION"
  (cd third_party/livekit \
    && curl -sSL -o lk.tgz "$base/livekit_${LK_VERSION}_linux_amd64.tar.gz" \
    && curl -sSL -o checksums.txt "$base/checksums.txt" \
    && grep "linux_amd64.tar.gz" checksums.txt | sed "s#livekit_${LK_VERSION}_linux_amd64.tar.gz#lk.tgz#" \
       | sha256sum -c - \
    && tar -xzf lk.tgz livekit-server)
  export LIVEKIT_SERVER_BIN="$ROOT/third_party/livekit/livekit-server"
fi

# --- 5. every model, downloaded before the timed run -------------------------------------------------
say "speech models"
"$PY_AGENT" -m duet_voice.prefetch
if [ "$BACKEND" = "local" ] && [ "$DRY_RUN" = 0 ]; then
  say "language model weights"
  "$PY_LLM" bench/llm_server.py prefetch
fi
say "the benchmark's scoring recognizer (Parakeet)"
"$PY_BENCH" -c "import nemo.collections.asr as a; a.models.ASRModel.from_pretrained('nvidia/parakeet-tdt-0.6b-v2')" \
  >/dev/null 2>&1 || echo "WARNING: could not pre-load Parakeet; the runner will try again"

# --- 6. run and evaluate ------------------------------------------------------------------------------
say "running FDB-v3 against the DUET agent"
status=0
"$PY_AGENT" bench/run_live.py --bench-python "$PY_BENCH" --llm-python "$PY_LLM" "$@" || status=$?

# Our own test machines only (DUET_AUTO_PUSH=1): store the results on GitHub right away,
# successful or not. Off by default, so a re-run elsewhere never touches git.
if [ "${DUET_AUTO_PUSH:-0}" = 1 ]; then
  tag="auto_live_$(date +%m%d_%H%M)"; [ "$status" -ne 0 ] && tag="${tag}_failed"
  bash scripts/push_results.sh "$tag" || echo "Auto-push failed; later run: bash scripts/push_results.sh <name>"
fi
exit "$status"
