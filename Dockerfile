# DUET on Full-Duplex-Bench v3 in a container: the same one-command reproduction
# (reproduce.sh) on a clean Ubuntu 22.04. Python, every package (from the lock files),
# ffmpeg, the LiveKit server, the benchmark and its data are fetched by reproduce.sh
# at run time, exactly as on a bare machine.
#
#   docker build -t duet .
#   docker run --rm --gpus all --shm-size 8g \
#     -e GOOGLE_API_KEYS=key1,key2 \
#     -v duet-cache:/duet/third_party -v "$PWD/results/live:/duet/results/live" \
#     duet --only travel_19_695bd157114f0d2317f88617      # a quick check
#   ... duet --force                                        # all 100 recordings
#
# Keys can also come from a file: -v "$PWD/.env.local:/duet/.env.local:ro".
# Optional: -e OPENAI_API_KEY=... (the official gpt-4o judge), -e FDB_DATA_DIR=... with
# the benchmark audio mounted, or -e LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET.
# Needs the NVIDIA Container Toolkit on the host for --gpus.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# What reproduce.sh expects on the machine: git, curl, tar with xz, sha256sum. Every
# Python package installs from a prebuilt wheel (no compiler needed).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git tar xz-utils coreutils procps libgomp1 libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /duet
COPY . /duet

ENTRYPOINT ["bash", "reproduce.sh"]
