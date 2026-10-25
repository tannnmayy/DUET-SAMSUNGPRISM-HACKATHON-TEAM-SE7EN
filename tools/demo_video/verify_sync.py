"""Checks the finished film: every caption against the speech actually heard in the film's own audio.

Whisper re-transcribes the film; each caption's first word is looked for near the time the edit put the caption
on screen. Reports the offset per caption and any caption whose words are not heard near it.

    python verify_sync.py <film.mp4>
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edl  # noqa: E402
import render  # noqa: E402

film = sys.argv[1]
wav = film + ".check.wav"
subprocess.run([render.FFMPEG, "-v", "error", "-y", "-i", film, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
from faster_whisper import WhisperModel  # noqa: E402

model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", cpu_threads=8)
segs, _ = model.transcribe(wav, language="en", word_timestamps=True, vad_filter=True)
words = [(re.sub(r"[^a-z0-9]", "", w.word.lower()), w.start) for s in segs for w in (s.words or [])]
os.remove(wav)

norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
base, worst, missing = 0.0, 0.0, []
for sc in edl.SCENARIOS:
    segments, n, hold = render.segments(sc)
    for spk, a, b, s in sc["captions"]:
        want = base + render.s2o(segments, a)
        first = [norm(x) for x in s.split()][:3]
        cands = [t for w, t in words if w and (w == first[0] or (len(first) > 1 and w == first[1])) and abs(t - want) < 1.6]
        if not cands:
            missing.append((sc["n"], round(want, 2), s))
            continue
        t = min(cands, key=lambda x: abs(x - want))
        worst = max(worst, abs(t - want))
        print("S%d %7.2f  heard %7.2f  %+.2f  %s" % (sc["n"], want, t, t - want, s[:60]))
    base += (n + hold) / render.FPS
print("\nworst offset %.2f s; captions not matched: %d" % (worst, len(missing)))
for m in missing:
    print("  not matched:", m)
