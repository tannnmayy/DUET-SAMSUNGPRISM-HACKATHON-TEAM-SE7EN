"""The demo's user voices: every scripted line of app/www/demo/scenes.json, spoken by
Kokoro in the scene's voice (a different voice from DUET's), saved as
app/www/demo/audio/<scene>_<n>.wav for the app's demo mode to play into the room; and
the two lines of the film's opening (app/www/demo/intro.html) as intro_<n>.wav.

    python scripts/galaxy_demo_voices.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DUET_TTS_DEVICE", "cpu")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))

# the opening's request and its correction, as timed in app/www/demo/intro.html
INTRO = ["Book me a flight to Rome...", "No wait, Milan."]


def main() -> int:
    from duet_voice import speech_models
    demo = os.path.join(ROOT, "app", "www", "demo")
    scenes = json.load(open(os.path.join(demo, "scenes.json"), encoding="utf-8"))["scenes"]
    out = os.path.join(demo, "audio")
    os.makedirs(out, exist_ok=True)
    pipe = speech_models.kokoro()
    for name, scene in scenes.items():
        for i, line in enumerate(scene["lines"]):
            if "say" not in line:
                continue
            audio = np.concatenate([np.asarray(r.audio, dtype=np.float32)
                                    for r in pipe(line["say"], voice=scene["voice"], speed=1.0)])
            path = os.path.join(out, "%s_%d.wav" % (name, i))
            sf.write(path, audio, 24000, subtype="PCM_16")
            print("%-28s %4.1f s  %s" % (os.path.basename(path), len(audio) / 24000, line["say"]))
    for i, text in enumerate(INTRO):
        audio = np.concatenate([np.asarray(r.audio, dtype=np.float32) for r in pipe(text, voice="am_michael", speed=1.0)])
        path = os.path.join(out, "intro_%d.wav" % i)
        sf.write(path, audio, 24000, subtype="PCM_16")
        print("%-28s %4.1f s  %s" % (os.path.basename(path), len(audio) / 24000, text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
