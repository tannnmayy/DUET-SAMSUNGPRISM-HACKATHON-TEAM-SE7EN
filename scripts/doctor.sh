#!/usr/bin/env bash
# Is this machine ready to run DUET? Prints a checklist and writes doctor_report.txt.
# If anything says FAIL, fix it (the hint says how) or send doctor_report.txt back.
#
#   bash scripts/doctor.sh
set -uo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
out="$ROOT/doctor_report.txt"
: > "$out"
fails=0
line() { printf '%-5s %s\n' "$1" "$2" | tee -a "$out"; [ "$1" = "FAIL" ] && fails=$((fails + 1)); return 0; }

echo "DUET machine check, $(date -u '+%Y-%m-%d %H:%M UTC'), $(hostname)" | tee -a "$out"

# system
if [ "$(uname -s)" = "Linux" ] && [ "$(uname -m)" = "x86_64" ]; then
  line OK "Linux x86_64 ($(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"), kernel $(uname -r))"
else
  line FAIL "needs Linux x86_64 (this is $(uname -s) $(uname -m))"
fi

# GPU
if command -v nvidia-smi >/dev/null; then
  cuda=$(nvidia-smi | grep -o 'CUDA Version: [0-9.]*' | head -1)
  line OK "NVIDIA driver $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1) ($cuda)"
  while IFS= read -r g; do line INFO "GPU $g"; done < <(nvidia-smi \
    --query-gpu=index,name,memory.total,memory.used,memory.free,compute_cap --format=csv,noheader)
  sel="${CUDA_VISIBLE_DEVICES%%,*}"
  free=$(nvidia-smi -i "$sel" --query-gpu=memory.free --format=csv,noheader,nounits | tr -dc 0-9)
  if [ "${free:-0}" -ge 8000 ]; then
    line OK "GPU $sel will be used: ${free} MiB free (speech models and scoring recognizer need about 6 GiB)"
  else
    line FAIL "GPU $sel has only ${free:-0} MiB free; the speech models and the scoring recognizer need about 6 GiB. Pick a freer GPU (export CUDA_VISIBLE_DEVICES=<index>)"
  fi
else
  line FAIL "nvidia-smi not found: no NVIDIA driver, or not on PATH"
fi

# disk
avail=$(df -BG --output=avail "$ROOT" 2>/dev/null | tail -1 | tr -dc 0-9)
if [ "${avail:-0}" -ge 30 ]; then line OK "disk: ${avail} GB free where the repository is"
elif [ "${avail:-0}" -ge 22 ]; then line WARN "disk: ${avail} GB free; about 30 GB is recommended (tight but may fit)"
else line FAIL "disk: ${avail:-?} GB free; about 30 GB is needed (environments ~15 GB, speech models ~5 GB, caches)"; fi

# tools
for t in git curl tar sha256sum; do
  if command -v "$t" >/dev/null; then line OK "$t"; else line FAIL "$t is missing (ask the admin to install it)"; fi
done
if command -v ffmpeg >/dev/null; then line OK "ffmpeg"; else line INFO "ffmpeg not installed: reproduce.sh fetches a static build"; fi

# internet
for u in https://pypi.org/simple/ https://files.pythonhosted.org https://huggingface.co https://github.com \
         https://astral.sh https://drive.usercontent.google.com https://generativelanguage.googleapis.com; do
  if curl -sSI --max-time 15 -o /dev/null "$u" 2>/dev/null; then line OK "reach $u"
  else line FAIL "cannot reach $u (proxy or firewall?)"; fi
done

# things other users of a shared machine could be holding
if [ -e /tmp/agent_tool_calls.log ] && [ ! -w /tmp/agent_tool_calls.log ]; then
  line FAIL "/tmp/agent_tool_calls.log belongs to another user; the benchmark reads that exact path. Ask them to remove it"
else
  line OK "/tmp/agent_tool_calls.log is writable (or absent)"
fi

# the language model: Gemma 4 through Google's API
if [ -n "${GOOGLE_API_KEYS:-}${GOOGLE_API_KEY:-}" ]; then
  if [ -x "$PY_AGENT" ]; then
    res=$("$PY_AGENT" -m duet_voice.gemma_api 2>/dev/null | tail -1)
    if echo "$res" | grep -q '"tool_calling": "ok"'; then line OK "Gemma 4 through Google's API: $res"
    else line FAIL "the Google API key(s) cannot use Gemma 4: $res"; fi
  else
    line INFO "a Google API key is set (checked once the environments are installed)"
  fi
else
  line FAIL "no GOOGLE_API_KEY (or GOOGLE_API_KEYS): create one at https://aistudio.google.com in a project without billing (Gemma is free there)"
fi

# what is already installed
for e in .venv .venv-bench; do
  if [ -f "$e/.duet-installed" ]; then line OK "environment $e installed"; else line INFO "environment $e not installed yet (reproduce.sh does it)"; fi
done

echo | tee -a "$out"
if [ "$fails" -eq 0 ]; then
  echo "Ready. Next: bash reproduce.sh --only travel_19_695bd157114f0d2317f88617" | tee -a "$out"
else
  echo "$fails problem(s) above. Fix them, or send $out back." | tee -a "$out"
fi
exit "$fails"
