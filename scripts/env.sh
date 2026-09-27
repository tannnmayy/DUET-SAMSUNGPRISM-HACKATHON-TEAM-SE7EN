# Shared settings for reproduce.sh and scripts/*.sh. Sourced, not run.
#
# Everything this project downloads (Python, packages, model weights, caches) goes
# under third_party/ in the repository, never into the home directory: shared GPU
# machines often have a small home quota. Set any of these variables beforehand
# to override them.

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export ROOT
TP="$ROOT/third_party"

export HF_HOME="${HF_HOME:-$TP/hf}"
export HF_HUB_ENABLE_HF_TRANSFER="${HF_HUB_ENABLE_HF_TRANSFER:-1}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$TP/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$TP/python}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$TP/pip-cache}"
export VLLM_CACHE_ROOT="${VLLM_CACHE_ROOT:-$TP/vllm-cache}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-$TP/triton-cache}"
export TORCHINDUCTOR_CACHE_DIR="${TORCHINDUCTOR_CACHE_DIR:-$TP/inductor-cache}"
export FLASHINFER_WORKSPACE_BASE="${FLASHINFER_WORKSPACE_BASE:-$TP/flashinfer-cache}"
export NEMO_CACHE_DIR="${NEMO_CACHE_DIR:-$TP/nemo-cache}"

export FDB_V3_DIR="${FDB_V3_DIR:-$TP/Full-Duplex-Bench/v3}"
export FDB_DATA_DIR="${FDB_DATA_DIR:-$FDB_V3_DIR/fdb_v3_data_released}"

[ -x "$TP/ffmpeg/ffmpeg" ] && export PATH="$TP/ffmpeg:$PATH"
[ -x "$TP/uv/uv" ] && export PATH="$TP/uv:$PATH"
[ -x "$TP/livekit/livekit-server" ] && export LIVEKIT_SERVER_BIN="${LIVEKIT_SERVER_BIN:-$TP/livekit/livekit-server}"

# One GPU for everything, as on Samsung's machine. CUDA numbers GPUs the way
# nvidia-smi does; unless CUDA_VISIBLE_DEVICES is already set, the GPU with the
# most free memory is used.
export CUDA_DEVICE_ORDER=PCI_BUS_ID
if [ -z "${CUDA_VISIBLE_DEVICES:-}" ] && command -v nvidia-smi >/dev/null 2>&1; then
  _gpu=$(nvidia-smi --query-gpu=index,memory.free --format=csv,noheader,nounits 2>/dev/null \
         | sort -t, -k2 -nr | head -1 | cut -d, -f1 | tr -d ' ')
  [ -n "$_gpu" ] && export CUDA_VISIBLE_DEVICES="$_gpu"
  unset _gpu
fi

# the three Python environments
PY_AGENT="$ROOT/.venv/bin/python"
PY_BENCH="$ROOT/.venv-bench/bin/python"
PY_LLM="$ROOT/.venv-llm/bin/python"
export DUET_LLM_PYTHON="${DUET_LLM_PYTHON:-$PY_LLM}"
