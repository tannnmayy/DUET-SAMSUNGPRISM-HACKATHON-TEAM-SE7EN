#!/usr/bin/env python3
"""One throwaway conversation before the benchmark's first recording.

The first room an agent process joins pays one-time costs: starting the WebRTC
stack (it probes the GPU's video decoders) and the first session's objects. On a
fresh WSL2 machine that took 16 s. The benchmark's runner does not wait for the
agent: it starts streaming a recording 2 s after it has joined itself, so the
first item's request was half over before the agent was listening.

This joins a room of its own, waits until the agent has joined and is listening,
then leaves. Uses LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET.

    python bench/warmup.py [--timeout 90]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid

from livekit import api, rtc


async def warm(timeout: float) -> int:
    url, key, secret = (os.environ[v] for v in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    name = "warmup-" + uuid.uuid4().hex[:8]
    token = (api.AccessToken(key, secret).with_identity("warmup-user")
             .with_grants(api.VideoGrants(room_join=True, room=name)).to_jwt())
    room = rtc.Room()
    start = time.monotonic()
    await room.connect(url, token)
    try:
        # a silent microphone, as the benchmark's runner publishes one
        source = rtc.AudioSource(48000, 1)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        joined = listening = None
        while time.monotonic() - start < timeout:
            agents = list(room.remote_participants.values())
            if agents and joined is None:
                joined = time.monotonic() - start
            if any(p.attributes.get("lk.agent.state") == "listening" for p in agents):
                listening = time.monotonic() - start
                break
            await asyncio.sleep(0.1)
        if listening is None:
            print("warm-up: the agent %s within %.0f s" % (
                "joined but was not listening" if joined is not None else "did not join", timeout))
            return 1
        print("warm-up: the agent joined after %.1f s and was listening after %.1f s" % (joined, listening))
        await asyncio.sleep(1.0)
        return 0
    finally:
        await room.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()
    return asyncio.run(warm(args.timeout))


if __name__ == "__main__":
    sys.exit(main())
