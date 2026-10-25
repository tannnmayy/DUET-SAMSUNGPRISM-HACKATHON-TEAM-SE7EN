"""Where sound actually is in a screen recording's audio, window by window (20 ms frames), so cuts and fast-forwards
can leave a natural pause before and after speech. Whisper's word times can be a few hundred ms early.

    python speech_edges.py <clip> <a-b> [<a-b> ...]
"""
import sys

import numpy as np
import soundfile as sf

clip, wins = sys.argv[1], sys.argv[2:]
x, sr = sf.read("audio/%s.wav" % clip, dtype="float32")
hop = sr // 50
for w in wins:
    a, b = (float(v) for v in w.split("-"))
    seg = x[int(a * sr): int(b * sr)]
    n = len(seg) // hop
    rms = np.sqrt(np.square(seg[: n * hop]).reshape(n, hop).mean(axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    floor = np.percentile(db, 15)
    on = db > max(floor + 14, -48)
    runs, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        if not v and start is not None:
            if i - start >= 4:
                runs.append((a + start / 50, a + i / 50))
            start = None
    if start is not None:
        runs.append((a + start / 50, b))
    merged = []
    for r in runs:                       # gaps under 0.25 s belong to the same phrase
        if merged and r[0] - merged[-1][1] < 0.25:
            merged[-1] = (merged[-1][0], r[1])
        else:
            merged.append(r)
    print("%s %5.1f-%5.1f floor %.0f dB: %s" % (clip, a, b, floor, ", ".join("%.2f-%.2f" % r for r in merged)))
