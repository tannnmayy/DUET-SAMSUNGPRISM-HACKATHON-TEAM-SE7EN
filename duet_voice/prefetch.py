"""Download and warm every model the agent uses, before any timed run.

    python -m duet_voice.prefetch
"""

from __future__ import annotations

import logging
import time


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t = time.time()
    from . import speech_models
    from .config import CONFIG
    speech_models.whisper()
    if CONFIG.tts_backend == "kokoro":
        speech_models.kokoro()
    from livekit.plugins import silero
    silero.VAD.load()
    from livekit.agents import inference
    inference.TurnDetector(version="v1-mini")  # weights ship inside livekit-local-inference
    print("models ready in %.0f s" % (time.time() - t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
