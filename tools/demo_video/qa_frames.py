"""Frame QA of the finished film: black or frozen stretches in the centre of the frame (where the phone or a zoomed
view sits), outside the deliberate dips to black between scenarios.

    python qa_frames.py <film.mp4>
"""
import os
import sys

import av
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edl  # noqa: E402
import render  # noqa: E402

film = sys.argv[1]
# the dips: the first and last 0.5 s of every scenario, and the outro's fades
edges, base = [], 0.0
for sc in edl.SCENARIOS:
    segs, n, hold = render.segments(sc)
    d = (n + hold) / render.FPS
    edges += [(base - 0.05, base + 0.5), (base + d - 0.5, base + d + 0.05)]
    base += d
edges += [(base - 0.05, base + 0.6), (base + 3.0, base + 3.7)]

c = av.open(film)
s = c.streams.video[0]
dark, frozen, prev, run_dark, run_frozen = [], [], None, None, None
for fr in c.decode(s):
    t = fr.time
    g = fr.to_ndarray(format="gray")[120:900, 700:1220].astype(np.int16)
    in_edge = any(a <= t <= b for a, b in edges)
    is_dark = g.mean() < 14 and not in_edge
    if is_dark:
        run_dark = run_dark or [t, t]
        run_dark[1] = t
    elif run_dark:
        dark.append(tuple(round(x, 2) for x in run_dark))
        run_dark = None
    if prev is not None:
        same = np.abs(g - prev).mean() < 0.3
        if same and not in_edge:
            run_frozen = run_frozen or [t, t]
            run_frozen[1] = t
        elif run_frozen:
            if run_frozen[1] - run_frozen[0] > 2.5:
                frozen.append(tuple(round(x, 2) for x in run_frozen))
            run_frozen = None
    prev = g
print("dark stretches in the centre (outside the dips):", dark or "none")
print("frozen stretches over 2.5 s (a static screen is normal while DUET speaks):", frozen or "none")
