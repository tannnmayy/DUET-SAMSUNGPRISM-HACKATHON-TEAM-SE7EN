# DUET - reproducible evaluation image.
#
#   docker build -t duet .
#   docker run --gpus all duet                         # official procedure, 3 reps
#   docker run --gpus all duet python tools/quality.py # transcripts, linted
#
# No CUDA base image is needed: the pinned torch==2.10.0 wheel ships the CUDA
# 12.8 runtime, cuBLAS 12 and cuDNN 9 as pip dependencies, and
# duet/perception/cuda.py makes them visible to the speech engine. The host
# needs only an NVIDIA driver and the NVIDIA container toolkit (--gpus all).
# Without a GPU the same image runs on CPU: setup() falls back by itself.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so code changes do not invalidate the (large) layer.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Bake every pinned, enabled model into the image: cold start then never
# depends on the network, and setup() stays far inside its 300 s cap.
RUN python tools/prefetch.py

CMD ["python", "eval_submission.py", ".", "--time-scale", "1", "--reps", "3"]
