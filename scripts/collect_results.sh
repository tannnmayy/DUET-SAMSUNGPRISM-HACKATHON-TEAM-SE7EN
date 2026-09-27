#!/usr/bin/env bash
# Send a run back. Packs the latest live run (or the one given), with recent
# offline evaluations, the machine check and the model server's log: settings,
# scores, the benchmark's reports, per-item results, traces and logs. No audio.
#
#   bash scripts/collect_results.sh                         # -> duet_results_<run>.tar.gz
#   bash scripts/collect_results.sh results/live/<time>     # a specific run
#   bash scripts/collect_results.sh --push dgx_full_run     # also commit the run to
#                                                           # results/reported/dgx_full_run on a new
#                                                           # branch and push it (needs GitHub access)
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
PUSH=""
RUN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --push) PUSH="${2:?--push needs a name, e.g. --push dgx_full_run}"; shift 2 ;;
    *) RUN="$1"; shift ;;
  esac
done
RUN="${RUN:-$(ls -dt results/live/*/ 2>/dev/null | head -1)}"
RUN="${RUN%/}"
[ -n "$RUN" ] && [ -d "$RUN" ] || { echo "No run found under results/live/."; exit 1; }
name="duet_results_$(basename "$RUN").tar.gz"

extra=()
[ -f doctor_report.txt ] && extra+=(doctor_report.txt)
while IFS= read -r f; do extra+=("$f"); done < <(ls -t results/offline/eval_*.json 2>/dev/null | head -6)
tar -czf "$name" --exclude='*.wav' --exclude='livekit_server.log' "$RUN" "${extra[@]}"
echo "Packed $RUN into $ROOT/$name ($(du -h "$name" | cut -f1)). Send this file back."

if [ -n "$PUSH" ]; then
  "$PY_AGENT" bench/publish_run.py "$RUN" --name "$PUSH" --note "Run on $(hostname), GPU ${CUDA_VISIBLE_DEVICES:-0}."
  here=$(git rev-parse --abbrev-ref HEAD)
  branch="results-$PUSH-$(date +%Y%m%d-%H%M)"
  git checkout -q -b "$branch"
  git add "results/reported/$PUSH"
  git commit -q -m "Results: $PUSH (run $(basename "$RUN"))" \
    || { echo "git commit failed: set your name and email (git config user.name/user.email) and retry"; exit 1; }
  git push -q -u origin "$branch" && echo "Pushed to branch $branch on GitHub."
  git checkout -q "$here"
fi
