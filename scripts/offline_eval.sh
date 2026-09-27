#!/usr/bin/env bash
# The thinker alone on all 100 recordings, scored by the benchmark's own scorers
# (with the local model as a labelled proxy judge): minutes instead of hours, so
# this is the loop for comparing settings. Starts the model server if none is up.
#
#   bash scripts/offline_eval.sh                          # our own transcripts (what the agent hears)
#   bash scripts/offline_eval.sh --text script            # the exact scripts (the reasoning ceiling)
#   DUET_TEMPERATURE=0 bash scripts/offline_eval.sh --tag greedy    # compare a setting
#
# Results: results/offline/eval_<text>_<time>[_<tag>].json, and a summary printed at the end.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
if [ ! -f .venv/.duet-installed ] || [ ! -f .venv-llm/.duet-installed ] || [ ! -d "$FDB_DATA_DIR" ]; then
  echo "Not installed yet. Run once: bash reproduce.sh --only travel_19_695bd157114f0d2317f88617"
  exit 1
fi
# what the agent's ears hear on each recording (made once, on the GPU, a few minutes)
if [ ! -f results/offline/asr_large-v3-turbo.json ]; then
  echo "== transcribing the 100 recordings with the agent's own speech recognizer (once)"
  "$PY_AGENT" bench/offline_asr.py
fi
"$PY_AGENT" bench/offline_eval.py --start-server --judge "$@"
