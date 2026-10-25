"""Offset of each camera clip against its scenario's screen recording, from their audio.

Both hear the same room (the user, DUET's voice from the phone's speaker), so the loudness envelopes line up.
Envelope: log energy per 10 ms in the speech band, its slow trend removed; the offset is the peak of the
normalised cross-correlation, refined at 1 ms on the raw band-passed audio around that peak.

    offset = t_cam - t_screen   (the camera's time of an event minus the screen recording's time of it)
"""
import json
import sys

import numpy as np
import soundfile as sf

SR = 16000
HOP = 160


def correlate(b, a, mode="full", method="fft"):
    """Full cross-correlation of b against a (lags -len(a)+1 .. len(b)-1), by FFT."""
    n = len(a) + len(b) - 1
    size = 1 << (n - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)
    return np.concatenate([c[size - len(a) + 1:], c[:len(b)]])


def load(path):
    x, sr = sf.read(path, dtype="float32")
    assert sr == SR, path
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec[(f < 250) | (f > 3800)] = 0          # the speech band
    return np.fft.irfft(spec, len(x)).astype(np.float32)


def envelope(x):
    n = len(x) // HOP
    e = np.log(np.square(x[: n * HOP]).reshape(n, HOP).mean(axis=1) + 1e-8)
    k = 100                                   # 1 s trend
    trend = np.convolve(e, np.ones(k) / k, mode="same")
    d = e - trend
    return (d - d.mean()) / (d.std() + 1e-9)


def offset(screen, cam):
    a, b = envelope(screen), envelope(cam)
    c = correlate(b, a, mode="full", method="fft")
    # normalise by the overlap length so short overlaps do not win
    lags = np.arange(-len(a) + 1, len(b))
    overlap = np.minimum(len(a), len(b) + 0) - 0
    ov = np.array([min(len(b), l + len(a)) - max(0, l) for l in lags], dtype=np.float32)
    score = c / np.maximum(ov, 300)
    score[ov < 1000] = -1                    # at least 10 s of overlap
    i = int(np.argmax(score))
    coarse = lags[i] * HOP / SR
    # second best peak at least 2 s away: how clear the match is
    s2 = score.copy()
    s2[max(0, i - 200): i + 200] = -1
    clarity = float(score[i] / max(1e-6, s2.max()))
    # refine at sample level within +-30 ms on the raw signals
    lag0 = int(round(coarse * SR))
    best, best_l = -1e9, lag0
    lo, hi = max(0, -lag0), min(len(screen), len(cam) - lag0)
    seg = slice(lo + SR, min(hi - SR, lo + SR * 30))   # up to 30 s of overlap for the fine search
    for l in range(lag0 - 480, lag0 + 481, 8):
        a_ = screen[seg]
        start = seg.start + l
        if start < 0 or start + len(a_) > len(cam):
            continue
        v = float(np.dot(a_, cam[start: start + len(a_)]))
        if v > best:
            best, best_l = v, l
    return coarse, best_l / SR, float(score[i]), clarity


pairs = json.loads(sys.argv[1])
out = {}
for screen, cams in pairs.items():
    s = load("work/audio/%s.wav" % screen)
    for cam in cams:
        c = load("work/audio/%s.wav" % cam)
        coarse, fine, sc, clarity = offset(s, c)
        out[cam] = {"screen": screen, "offset_s": round(fine, 3), "coarse_s": round(coarse, 2),
                    "score": round(sc, 3), "clarity": round(clarity, 2)}
        print("%s vs %s: offset %+.3f s (coarse %+.2f), score %.3f, clarity %.2f" % (cam, screen, fine, coarse, sc, clarity))
json.dump(out, open("work/sync.json", "w"), indent=1)
