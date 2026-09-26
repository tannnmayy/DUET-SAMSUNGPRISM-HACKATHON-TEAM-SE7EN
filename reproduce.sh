#!/usr/bin/env bash
# One command: install, fetch FDB-v3 and its data, run the DUET agent through the
# benchmark's own pipeline, evaluate, and print the scores.
#
#   export GOOGLE_API_KEY=...          # required: Gemini (thinker and talker)
#   export OPENAI_API_KEY=...          # optional: the official gpt-4o judge
#   export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...   # optional
#   bash reproduce.sh                  # all 100 items (~2 h: the audio is streamed in real time)
#   bash reproduce.sh --only travel_19,housing_04   # a quick subset
#
# Target machine: Linux x86_64, one NVIDIA GPU (48 GB is far more than needed;
# about 4 GB is used), CUDA 12 or 13 driver, Python 3.10-3.12, ffmpeg, git, curl.
# Without LIVEKIT_URL a local LiveKit server (v1.13.7, checksum-verified) is
# downloaded and run in dev mode, so no LiveKit account is needed.
# Everything is written under this folder; results land in results/live/<time>/.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

FDB_REPO="https://github.com/DanielLin94144/Full-Duplex-Bench.git"
FDB_COMMIT="3e799c45a045256f47d5f1c9cda90157e2d2ec9e"
DATA_URL="https://drive.usercontent.google.com/download?id=1SO_4MTazWQ_jvCx0dtmpQ-t40bdd07yz&export=download&confirm=t"
DATA_SHA256="37545bd896f81718136598cf5be25d42ea9aa22efcd91f58370938d05d7d672f"
LK_VERSION="1.13.7"
LK_SHA256_LINUX_AMD64=""   # filled from the release's checksums.txt at download time
PY="${PYTHON:-python3}"

say() { printf '\n== %s\n' "$*"; }
need() { command -v "$1" >/dev/null || { echo "missing: $1 ($2)"; exit 1; }; }

need git "apt install git"; need curl "apt install curl"; need ffmpeg "apt install ffmpeg"; need "$PY" "Python 3.10-3.12"
: "${GOOGLE_API_KEY:?Set GOOGLE_API_KEY (Gemini API key) - DUET's thinker and talker run on Gemini}"

# --- 1. environments -----------------------------------------------------------------
# Two venvs: the agent's, and the benchmark runner's (NVIDIA NeMo for the scoring
# ASR has heavy pins of its own and must not constrain the agent).
if [ ! -x .venv/bin/python ]; then
  say "agent environment"
  "$PY" -m venv .venv
  .venv/bin/pip install -q -U pip
  .venv/bin/pip install -q -r requirements.txt
fi
if [ ! -x .venv-bench/bin/python ]; then
  say "benchmark runner environment"
  "$PY" -m venv .venv-bench
  .venv-bench/bin/pip install -q -U pip
  .venv-bench/bin/pip install -q -r requirements-bench.txt
fi

# --- 2. the benchmark, pinned ------------------------------------------------------------
if [ ! -d third_party/Full-Duplex-Bench/.git ]; then
  say "Full-Duplex-Bench @ ${FDB_COMMIT:0:7}"
  git clone -q "$FDB_REPO" third_party/Full-Duplex-Bench
fi
git -C third_party/Full-Duplex-Bench checkout -q "$FDB_COMMIT"
export FDB_V3_DIR="$ROOT/third_party/Full-Duplex-Bench/v3"
export FDB_DATA_DIR="$FDB_V3_DIR/fdb_v3_data_released"

# --- 3. the benchmark audio (Google Drive link from the v3 README), verified -------------
if [ ! -d "$FDB_DATA_DIR" ]; then
  say "benchmark data (736 MB)"
  mkdir -p third_party/downloads
  curl -L --fail -o third_party/downloads/fdb_v3_data_released.zip "$DATA_URL"
  echo "$DATA_SHA256  third_party/downloads/fdb_v3_data_released.zip" | sha256sum -c -
  (cd "$FDB_V3_DIR" && unzip -q -o "$ROOT/third_party/downloads/fdb_v3_data_released.zip" -x "__MACOSX/*")
fi

# --- 4. LiveKit: your Cloud project, or a local dev server --------------------------------
if [ -z "${LIVEKIT_URL:-}" ] && ! command -v livekit-server >/dev/null; then
  say "local LiveKit server v$LK_VERSION"
  mkdir -p third_party/livekit && cd third_party/livekit
  base="https://github.com/livekit/livekit/releases/download/v$LK_VERSION"
  curl -sSL -o lk.tgz "$base/livekit_${LK_VERSION}_linux_amd64.tar.gz"
  curl -sSL -o checksums.txt "$base/checksums.txt"
  grep "linux_amd64.tar.gz" checksums.txt | sed "s#livekit_${LK_VERSION}_linux_amd64.tar.gz#lk.tgz#" | sha256sum -c -
  tar -xzf lk.tgz livekit-server
  cd "$ROOT"
  export LIVEKIT_SERVER_BIN="$ROOT/third_party/livekit/livekit-server"
fi

# --- 5. models (downloads are not part of the timed run) -----------------------------------
say "prefetching models"
.venv/bin/python -m duet_voice.prefetch

# --- 6. run and evaluate ----------------------------------------------------------------------
say "running FDB-v3 against the DUET agent"
.venv/bin/python bench/run_live.py --bench-python "$ROOT/.venv-bench/bin/python" "$@"
