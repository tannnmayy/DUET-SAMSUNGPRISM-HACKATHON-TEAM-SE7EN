#!/usr/bin/env bash
# DUET for Galaxy: everything the phone app talks to, on this machine, in one command.
#
#   bash scripts/galaxy_demo.sh
#
# It starts, and stops again on Ctrl-C:
#   1. the language model:
#      - with LLAMA_SERVER and LLAMA_MODEL set: llama.cpp on this machine (a laptop GPU,
#        e.g. 6 GB), unless a server already answers at DUET_LLM_BASE_URL;
#      - otherwise Gemma 4 through Google's API, as in the benchmark (GOOGLE_API_KEY or
#        GOOGLE_API_KEYS in .env.local);
#   2. the DUET for Galaxy agent (duet_voice/galaxy/agent.py), connected to LiveKit Cloud;
#   3. the token server and web app on port 8787 (duet_voice/galaxy/server.py).
#
# Needs .env.livekit with LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET.
# Laptop example (Windows, Git Bash):
#   LLAMA_SERVER=/e/duet_local/llama/llama-server.exe \
#   LLAMA_MODEL=/e/duet_local/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf bash scripts/galaxy_demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || PY=.venv/Scripts/python.exe
mkdir -p results/galaxy
[ -f .env.livekit ] || { echo "Missing .env.livekit (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)"; exit 1; }

pids=()
cleanup() {
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ready() { curl -sf "${DUET_LLM_BASE_URL%/}/models" >/dev/null 2>&1; }
wait_ready() {
  for _ in $(seq 1 360); do ready && return 0; sleep 2; done
  echo "The model server did not come up; see $1"; exit 1
}

# --- 1. the language model --------------------------------------------------------------------
if [ -n "${LLAMA_SERVER:-}" ]; then
  export DUET_LLM_BACKEND=local
  port="${LLAMA_PORT:-18001}"
  export DUET_LLM_BASE_URL="${DUET_LLM_BASE_URL:-http://127.0.0.1:$port/v1}"
  export DUET_LLM_MODEL="${DUET_LLM_MODEL:-qwen3-4b-instruct-2507}"
  if ! ready; then
    echo "== llama.cpp: $(basename "${LLAMA_MODEL:?set LLAMA_MODEL to the .gguf file}")"
    # a small context and two slots (thinker and talker): about 3.5 GB of GPU memory
    "$LLAMA_SERVER" -m "$LLAMA_MODEL" --alias "$DUET_LLM_MODEL" --jinja -ngl 99 -c 12288 -np 2 \
      -fa on -ctk q8_0 -ctv q8_0 -t "${LLAMA_THREADS:-6}" --host 127.0.0.1 --port "$port" --no-webui \
      > results/galaxy/llama-server.log 2>&1 &
    pids+=($!)
    wait_ready results/galaxy/llama-server.log
  fi
  # a small GPU: speech recognition in 8-bit next to the model, speech synthesis on the CPU
  export DUET_ASR_DEVICE="${DUET_ASR_DEVICE:-cuda}" DUET_ASR_COMPUTE="${DUET_ASR_COMPUTE:-int8_float16}"
  export DUET_TTS_DEVICE="${DUET_TTS_DEVICE:-cpu}"
  echo "== language model ready at $DUET_LLM_BASE_URL"
else
  echo "== language model: Gemma 4 through Google's API"
  "$PY" -m duet_voice.gemma_api >/dev/null || { echo "No usable Google API key: set GOOGLE_API_KEY in .env.local"; exit 1; }
fi

# --- 2. the agent ------------------------------------------------------------------------------
export DUET_TRACE_DIR="${DUET_TRACE_DIR:-$(pwd)/results/galaxy/traces}" PYTHONUNBUFFERED=1
"$PY" -m duet_voice.galaxy.agent start --log-level info > results/galaxy/agent.log 2>&1 &
pids+=($!)
for _ in $(seq 1 120); do grep -q "registered worker" results/galaxy/agent.log 2>/dev/null && break; sleep 2; done
grep -q "registered worker" results/galaxy/agent.log || { echo "The agent did not start; see results/galaxy/agent.log"; tail -20 results/galaxy/agent.log; exit 1; }
echo "== DUET agent connected to LiveKit (log: results/galaxy/agent.log)"

# --- 3. the token server and web app -------------------------------------------------------------
"$PY" -m duet_voice.galaxy.server &
pids+=($!)
sleep 2
echo
echo "Ready. On the phone: open DUET, Settings, and enter the server address printed above."
echo "On this laptop: http://localhost:8787 . Conversation traces: results/galaxy/traces/"
echo "Ctrl-C stops everything."
wait
