#!/usr/bin/env bash
# Store every result from this machine on GitHub, so nothing lives only here. Safe to
# run as often as you like: each push holds everything so far.
#
#   bash scripts/push_results.sh <session>        # e.g. bash scripts/push_results.sh a100_0929_am
#
# It copies (no audio, no LiveKit debug log):
#   - every live run in results/live/            -> results/reported/dgx/live_<time>/
#   - every offline evaluation in results/offline -> results/reported/dgx/offline_<time>_<tag>/
#   - the table of all results                   -> results/reported/dgx/EXPERIMENTS.md
#   - the experiment log, your journal (results/journal.md), the machine check,
#     the model server's log (compressed) and a snapshot of the GPUs
# then commits that on a new branch results-<session>-<time>, pushes it, returns to
# the branch you were on, and leaves a backup archive duet_results_<session>.tar.gz.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
SESSION="${1:?give the session a name, e.g.: bash scripts/push_results.sh a100_0929_am}"
SESSION="$(echo "$SESSION" | tr -c 'A-Za-z0-9_.-' '_' | sed 's/_*$//')"
OUT=results/reported/dgx
mkdir -p "$OUT"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "NOTE: tracked files were edited on this machine (git status). They are not pushed; tell Tanmay:"
  git status --short --untracked-files=no
fi

n=0
for run in results/live/*/; do
  run="${run%/}"
  [ -f "$run/run_config.json" ] || continue
  "$PY_AGENT" bench/publish_run.py "$run" --name "dgx/live_$(basename "$run")" \
    --note "Live run on $(hostname). Placement and code version: run_config.json, provenance." > /dev/null
  n=$((n + 1))
done
for f in results/offline/eval_*.json; do
  [ -f "$f" ] || continue
  name="$(basename "$f" .json)"
  "$PY_AGENT" bench/publish_run.py "$f" --name "dgx/offline_${name#eval_}" \
    --note "Offline evaluation on $(hostname). Settings and code version: the summary's settings and provenance." > /dev/null
  n=$((n + 1))
done
"$PY_AGENT" bench/compare_runs.py --write "$OUT/EXPERIMENTS.md" > /dev/null
[ -d results/offline/failures ] && mkdir -p "$OUT/failures" && cp results/offline/failures/*.md "$OUT/failures/" 2>/dev/null || true
[ -f results/journal.md ] && cp results/journal.md "$OUT/JOURNAL.md"
[ -f results/offline/experiments.log ] && cp results/offline/experiments.log "$OUT/experiments.log"
[ -f doctor_report.txt ] && cp doctor_report.txt "$OUT/doctor_report.txt"
if [ -f results/llm_server/llm_server.log ]; then
  gzip -c results/llm_server/llm_server.log > "$OUT/llm_server_latest.log.gz"
  [ -f results/llm_server/llm_plan.json ] && cp results/llm_server/llm_plan.json "$OUT/llm_plan_latest.json"
fi
{ echo "# $(hostname), $(date -u '+%F %T UTC')"; nvidia-smi 2>/dev/null || true; echo; df -h . 2>/dev/null || true; } \
  > "$OUT/machine_$SESSION.txt"
echo "Published $n results into $OUT"
KEYS='sk-(proj-)?[A-Za-z0-9_-]{20}|AIza[0-9A-Za-z_-]{30}|AQ\.[A-Za-z0-9_-]{20}'
if grep -rElq "$KEYS" "$OUT"; then
  echo "STOP: something that looks like an API key is in these files, so nothing was committed:"
  grep -rEl "$KEYS" "$OUT"
  echo "Tell Tanmay before pushing anything."
  exit 1
fi

tar -czf "duet_results_$SESSION.tar.gz" "$OUT"
echo "Backup: $ROOT/duet_results_$SESSION.tar.gz ($(du -h "duet_results_$SESSION.tar.gz" | cut -f1)) - copy it off the machine too"

here="$(git rev-parse --abbrev-ref HEAD)"
branch="results-$SESSION-$(date +%Y%m%d-%H%M)"
git checkout -q -b "$branch"
trap 'git checkout -q "$here" 2>/dev/null || true' EXIT   # back to your branch, even after an error
git add -f "$OUT"   # -f: the run logs (*.log) are git-ignored elsewhere, but belong in the record
if ! git commit -q -m "Results from $(hostname): session $SESSION"; then
  echo "git commit failed: set git config user.name / user.email, then run this again"
  git checkout -q "$here"
  exit 1
fi
if git push -q -u origin "$branch"; then
  echo "Pushed to GitHub: branch $branch"
else
  echo "Push failed (network or GitHub access). The commit is on local branch $branch;"
  echo "retry later with: git push -u origin $branch   - and send the backup archive meanwhile."
fi
git checkout -q "$here"
