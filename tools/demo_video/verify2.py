"""Checks the second-cut film (edl2/render2): every caption against the film's own audio (Whisper), the film's
audio against the edit on the waveform at each caption, and black frames outside the dips to white.

    python verify2.py <film.mp4>
"""
import os
import re
import subprocess
import sys

import av
import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edl2 as edl  # noqa: E402
import render2  # noqa: E402

film = sys.argv[1]
wav = film + ".check.wav"
subprocess.run([render2.FFMPEG, "-v", "error", "-y", "-i", film, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
film_audio, _ = sf.read(wav, dtype="float32")

from faster_whisper import WhisperModel  # noqa: E402
model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", cpu_threads=8)
segs, _ = model.transcribe(wav, language="en", word_timestamps=True, vad_filter=True)
words = [(re.sub(r"[^a-z0-9]", "", w.word.lower()), w.start) for s in segs for w in (s.words or [])]
os.remove(wav)


def env(x):
    n = len(x) // 160
    e = np.log(np.square(x[: n * 160]).reshape(n, 160).mean(1) + 1e-8)
    return e - e.mean()


norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
base, worst_wave, missing, edges = 0.0, 0.0, [], []
for sc in edl.SCENARIOS:
    times, pieces, n_main = render2.timeline(sc)
    clock = render2.Clock(times, n_main)
    src, _ = sf.read(os.path.join(render2.HERE, "audio", sc["screen"] + ".wav"), dtype="float32")
    for spk, a, b, s in sc["captions"]:
        a2 = render2.onset_after(sc["screen"], a) if a > sc["t_in"] + 0.3 else a
        want = base + clock.o(a2)
        first = [norm(x) for x in s.split()][:2]
        cands = [t for w, t in words if w and w in first and abs(t - want) < 1.6]
        heard = min(cands, key=lambda x: abs(x - want)) if cands else None
        # the waveform: 1.5 s of the source from the caption start, found in the film near where it should be
        seg = src[int(a2 * 16000): int((a2 + 1.5) * 16000)]
        lo = int((want - 0.6) * 16000)
        win = film_audio[max(0, lo): int((want + 2.1) * 16000)]
        ea, eb = env(seg), env(win)
        lag = None
        if len(eb) > len(ea) > 20:
            best = max(range(0, len(eb) - len(ea)), key=lambda l: float(np.dot(ea, eb[l:l + len(ea)])))
            lag = (max(0, lo) / 16000 + best / 100) - want
            worst_wave = max(worst_wave, abs(lag))
        if heard is None:
            missing.append((sc["n"], round(want, 2), s))
        print("S%d %7.2f  whisper %s  wave %s  %s" % (sc["n"], want, "%+.2f" % (heard - want) if heard is not None else "  -  ",
                                                    "%+.2f" % lag if lag is not None else "-", s[:56]))
    d = len(times) / render2.FPS
    edges += [(base - 0.05, base + 0.5), (base + d - 0.5, base + d + 0.05)]
    base += d
edges += [(base - 0.05, base + 0.6), (base + 2.4, base + 3.1)]
print("\nwaveform: worst offset %.2f s; captions Whisper did not match: %d" % (worst_wave, len(missing)))
for m in missing:
    print("  not matched by Whisper:", m)

c = av.open(film)
dark, run = [], None
for fr in c.decode(c.streams.video[0]):
    t = fr.time
    g = fr.to_ndarray(format="gray")[60:900, 761:1158]
    bad = g.mean() < 12 and not any(a <= t <= b for a, b in edges)
    if bad:
        run = run or [t, t]
        run[1] = t
    elif run:
        dark.append(tuple(round(x, 2) for x in run))
        run = None
print("phone panel fully black (outside dips):", dark or "none")
