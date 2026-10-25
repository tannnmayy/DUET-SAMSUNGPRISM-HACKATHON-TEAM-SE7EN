"""Renders the DUET live-demo film, second cut (edl2.py): a clean white, Apple-like look, no zooms, and natural
fast-forwards (the speed ramps up and down, with real pauses kept either side).

Each frame is composited in Python (PIL): the S24 screen recording in a phone frame in the centre, the cameras either
side, all on the screen recording's clock. Overlays are antialiased rounded sprites with soft shadows. Captions are
snapped to where the sound really starts. ffmpeg only encodes. Audio: the S24 screen recording's own track.

    python render2.py --scenario 3 --out ../out/v2/preview_s3.mp4
    python render2.py --all --out ../out/v2/DUET_live_demo_v2.mp4
    python render2.py --scenario 2 --out ../out/v2/stills/s2.png --frames 1.0,9.5
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edl2 as edl  # noqa: E402
from render import (FFMPEG, FPS, SR, W, H, HERE, SRC, Source, cover_box, ease, ease_out, font, fade, screen_audio,  # noqa: E402
                    text, tlen, wrap, REG, SEMI, BOLD, BLACK, SS, loudnorm)

PHONE = (761, 50, 397, 860, 34)
CAM_L = (28, 262, 700, 394, 22)
CAM_R = (1192, 262, 700, 394, 22)
RECTS = {"phone": PHONE, "camL": CAM_L, "camR": CAM_R}

INK = (29, 29, 31)
INK2 = (110, 110, 115)
INK3 = (134, 134, 139)
BLUE = (0, 113, 227)
GREEN = (52, 199, 89)
ORANGE = (255, 149, 0)
RED = (255, 59, 48)
YELLOW = (255, 214, 10)
ICONS = {"ok": GREEN, "no": ORANGE, "info": (0, 122, 255), "hold": (255, 179, 0)}
TAGS = {"YOU": ((142, 142, 147), "YOU"), "FAST": ((52, 199, 89), "FAST MIND"), "CLOUD": ((0, 122, 255), "CLOUD MIND")}
FADE_S = 0.4
WHITE_FRAME = Image.new("RGB", (W, H), (255, 255, 255))


# --------------------------------------------------------------------------------------------- sprite helpers
def shadowed(card, blur=14, alpha=34, dy=5, pad=28):
    """A sprite with a soft drop shadow drawn from its own alpha; returns (sprite, pad)."""
    w, h = card.size
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    a = card.split()[3].point(lambda v: v * alpha // 255)
    sh.paste((0, 0, 0, 255), (pad, pad + dy), a)
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(sh)
    out.alpha_composite(card, (pad, pad))
    return out, pad


def reduce(img):
    return img.resize((img.width // SS, img.height // SS), Image.LANCZOS)


def icon(d, kind, cx, cy, r):
    col = ICONS[kind]
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col + (255,))
    w = max(2, int(r * 0.26))
    if kind == "ok":
        d.line([(cx - r * 0.45, cy + r * 0.02), (cx - r * 0.12, cy + r * 0.36), (cx + r * 0.48, cy - r * 0.34)],
               fill=(255, 255, 255, 255), width=w, joint="curve")
    elif kind == "no":
        k = r * 0.36
        d.line([(cx - k, cy - k), (cx + k, cy + k)], fill=(255, 255, 255, 255), width=w)
        d.line([(cx - k, cy + k), (cx + k, cy - k)], fill=(255, 255, 255, 255), width=w)
    elif kind == "hold":
        k = r * 0.32
        d.rectangle((cx - k - w * 0.3, cy - k, cx - k + w * 0.7, cy + k), fill=(255, 255, 255, 255))
        d.rectangle((cx + k - w * 0.7, cy - k, cx + k + w * 0.3, cy + k), fill=(255, 255, 255, 255))
    else:
        d.ellipse((cx - r * 0.3, cy - r * 0.3, cx + r * 0.3, cy + r * 0.3), fill=(255, 255, 255, 255))


def gradient_text(s, f, spacing, c0, c1):
    """Text filled with a left-to-right gradient (the wordmark)."""
    w = int(tlen(f, s, spacing)) + 8
    asc, desc = f.getmetrics()
    h = asc + desc + 8
    mask = Image.new("L", (w, h), 0)
    text(ImageDraw.Draw(mask), 4, 4, s, f, 255, spacing)
    g = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    col = (np.array(c0, np.float32) * (1 - g) + np.array(c1, np.float32) * g)
    rgb = np.repeat(col, h, axis=0).astype(np.uint8)
    img = Image.fromarray(rgb, "RGB").convert("RGBA")
    img.putalpha(mask)
    return img


# --------------------------------------------------------------------------------------------- sprites
def sprite_caption(speaker, s):
    f, ft = font(SEMI, 38 * SS), font(BOLD, 16 * SS)
    tag = "DUET" if speaker == "DUET" else "YOU"
    tag_w = tlen(ft, tag, 3 * SS) + 24 * SS
    lines = wrap(s, f, 1180 * SS - tag_w - 70 * SS)
    lh = 50 * SS
    tw = max(f.getlength(l) for l in lines)
    w = 26 * SS + tag_w + 18 * SS + tw + 30 * SS
    h = 16 * SS + lh * len(lines) + 12 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 24 * SS, fill=(29, 29, 31, 238))
    tx, ty = 26 * SS, 16 * SS + (lh - 30 * SS) / 2 + 2 * SS
    tag_fill = (10, 132, 255, 255) if tag == "DUET" else (84, 84, 90, 255)
    d.rounded_rectangle((tx, ty, tx + tag_w, ty + 30 * SS), 15 * SS, fill=tag_fill)
    text(d, tx + 12 * SS, ty + 5 * SS, tag, ft, (255, 255, 255, 255), 3 * SS)
    x = tx + tag_w + 18 * SS
    for i, l in enumerate(lines):
        text(d, x, 16 * SS + i * lh, l, f, YELLOW + (255,))
    return reduce(img)


def sprite_chip(kind, s):
    f = font(SEMI, 21 * SS)
    h = 46 * SS
    w = 14 * SS + 28 * SS + 12 * SS + f.getlength(s) + 22 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), h / 2, fill=(255, 255, 255, 255), outline=(0, 0, 0, 14), width=SS)
    icon(d, kind, 14 * SS + 14 * SS, h / 2, 13 * SS)
    text(d, 14 * SS + 28 * SS + 12 * SS, h / 2 - 15 * SS, s, f, INK + (255,))
    return shadowed(reduce(img), blur=10, alpha=30, dy=4, pad=22)


def sprite_title(n, title, sub):
    fk, ft, fs = font(SEMI, 15 * SS), font(SEMI, 44 * SS), font(REG, 22 * SS)
    kicker = "SCENARIO %02d / 05" % n
    w = max(44 * SS + tlen(fk, kicker, 4 * SS), ft.getlength(title), fs.getlength(sub)) + 10 * SS
    img = Image.new("RGBA", (int(w), 140 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 6 * SS, 32 * SS, 10 * SS), 2 * SS, fill=BLUE + (255,))
    text(d, 44 * SS, 0, kicker, fk, INK3 + (255,), 4 * SS)
    text(d, -2 * SS, 22 * SS, title, ft, INK + (255,))
    text(d, 0, 90 * SS, sub, fs, INK2 + (255,))
    return reduce(img)


def sprite_live(views):
    f = font(SEMI, 14 * SS)
    s = "LIVE  ·  SAMSUNG GALAXY S24  ·  %d VIEWS IN SYNC" % views
    h = 30 * SS
    w = 14 * SS + 12 * SS + 10 * SS + tlen(f, s, 2 * SS) + 16 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), h / 2, fill=(255, 255, 255, 255), outline=(0, 0, 0, 14), width=SS)
    d.ellipse((14 * SS, h / 2 - 6 * SS, 26 * SS, h / 2 + 6 * SS), fill=RED + (255,))
    text(d, 36 * SS, h / 2 - 10 * SS, s, f, INK + (255,), 2 * SS)
    return shadowed(reduce(img), blur=8, alpha=26, dy=3, pad=16)


def sprite_ff(label):
    f = font(BOLD, 19 * SS)
    s = "FAST-FORWARD  ·  " + label
    h = 44 * SS
    w = 20 * SS + 32 * SS + 12 * SS + tlen(f, s, 1 * SS) + 24 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), h / 2, fill=(29, 29, 31, 240))
    x0, cy = 20 * SS, h / 2
    for k in (0, 14 * SS):
        d.polygon([(x0 + k, cy - 9 * SS), (x0 + k + 14 * SS, cy), (x0 + k, cy + 9 * SS)], fill=YELLOW + (255,))
    text(d, 20 * SS + 32 * SS + 12 * SS, h / 2 - 14 * SS, s, f, (255, 255, 255, 255), 1 * SS)
    return reduce(img)


def sprite_tag(s):
    f = font(BOLD, 12 * SS)
    h = 26 * SS
    w = 12 * SS + tlen(f, s, 3 * SS) + 12 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), h / 2, fill=(0, 0, 0, 120))
    text(d, 12 * SS, 6 * SS, s, f, (255, 255, 255, 255), 3 * SS)
    return reduce(img)


CARD_W = 470
ROW = 31
HEAD = 58


def sprite_mind_card(mind, nlines):
    label, sub, col = edl.MINDS[mind]
    fl, fs, ff = font(BOLD, 16 * SS), font(REG, 16 * SS), font(REG, 13 * SS)
    w = CARD_W * SS
    h = (HEAD + 6 + ROW * nlines + 8 + 24 + 6) * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 22 * SS, fill=(255, 255, 255, 255), outline=(0, 0, 0, 12), width=SS)
    d.ellipse((22 * SS, 23 * SS, 34 * SS, 35 * SS), fill=col + (255,))
    text(d, 44 * SS, 18 * SS, label, fl, col + (255,), 2 * SS)
    text(d, 44 * SS + tlen(fl, label, 2 * SS) + 12 * SS, 19 * SS, sub, fs, INK2 + (255,))
    d.line((20 * SS, HEAD * SS, w - 20 * SS, HEAD * SS), fill=(0, 0, 0, 20), width=SS)
    text(d, 22 * SS, h - 30 * SS, "seconds after you stopped speaking  ·  from DUET's log", ff, INK3 + (255,))
    return shadowed(reduce(img), blur=16, alpha=34, dy=6, pad=30)


def sprite_mind_line(label, value):
    fl, fv = font(SEMI, 18 * SS), font(BOLD, 18 * SS)
    w, h = (CARD_W - 44) * SS, ROW * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    text(d, 0, 3 * SS, label, fl, INK + (255,))
    if value:
        vcol = ORANGE if value in ("held", "never ran") else INK
        text(d, w - fv.getlength(value), 3 * SS, value, fv, vcol + (255,))
    return reduce(img)


def sprite_steps_frame():
    w, h = CAM_L[2], CAM_L[3]
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), CAM_L[4] * SS, fill=(255, 255, 255, 255), outline=(0, 0, 0, 12),
                        width=SS)
    text(d, 26 * SS, 22 * SS, "WHAT DUET IS DOING", font(BOLD, 14 * SS), INK3 + (255,), 4 * SS)
    return shadowed(reduce(img), blur=22, alpha=40, dy=10, pad=40)


def sprite_step(kind, who, s):
    f, ft = font(SEMI, 18 * SS), font(BOLD, 11 * SS)
    w, h = (CAM_L[2] - 48) * SS, 40 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    icon(d, kind, 12 * SS, h / 2, 11 * SS)
    col, label = TAGS[who]
    tw = 94 * SS
    d.rounded_rectangle((32 * SS, h / 2 - 12 * SS, 32 * SS + tw, h / 2 + 12 * SS), 12 * SS, fill=col + (36,))
    text(d, 32 * SS + (tw - tlen(ft, label, 2 * SS)) / 2, h / 2 - 8 * SS, label, ft, tuple(int(c * 0.75) for c in col) + (255,), 2 * SS)
    x = 32 * SS + tw + 14 * SS
    while f.getlength(s) > w - x and len(s) > 4:
        s = s[:-2].rstrip() + "…"
    text(d, x, h / 2 - 13 * SS, s, f, INK + (255,))
    return reduce(img)


def sprite_intro():
    img = Image.new("RGBA", (W, H), (255, 255, 255, 246))
    word = gradient_text("DUET", font(BOLD, 172), 10, BLUE, (175, 82, 222))
    img.alpha_composite(word, ((W - word.width) // 2, 282))
    big = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    for y, s, f, col in [(540, "A full-duplex voice agent, live on a Samsung Galaxy S24", font(SEMI, 36 * SS), INK),
                         (606, "Fast mind: Gemma 4 E2B on our laptop GPU   ·   Cloud mind: Gemma 4 26B", font(REG, 24 * SS), INK2),
                         (690, "TEAM SE7EN  ·  SAMSUNG PRISM GENAI HACKATHON 3.0  ·  THEME 05", font(SEMI, 15 * SS), INK3)]:
        sp = 4 * SS if s.isupper() else 0
        text(d, (W * SS - tlen(f, s, sp)) / 2, y * SS, s, f, col + (255,), sp)
    img.alpha_composite(reduce(big))
    return img


def sprite_outro():
    img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    word = gradient_text("DUET", font(BOLD, 140), 8, BLUE, (175, 82, 222))
    img.alpha_composite(word, ((W - word.width) // 2, 222))
    big = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    f = font(SEMI, 32 * SS)
    for i, (kind, s) in enumerate([("no", "What you take back never runs."),
                                   ("ok", "Every “done” is the device's own reply."),
                                   ("info", "Fast mind on our GPU, cloud mind when it needs to think.")]):
        y = (452 + i * 66) * SS
        tw = f.getlength(s) + 52 * SS
        x = (W * SS - tw) / 2
        icon(d, kind, x + 16 * SS, y + 23 * SS, 15 * SS)
        text(d, x + 52 * SS, y, s, f, INK + (255,))
    s = "TEAM SE7EN  ·  SRM INSTITUTE OF SCIENCE AND TECHNOLOGY  ·  SAMSUNG PRISM GENAI HACKATHON 3.0  ·  THEME 05"
    f = font(SEMI, 15 * SS)
    text(d, (W * SS - tlen(f, s, 4 * SS)) / 2, 712 * SS, s, f, INK3 + (255,), 4 * SS)
    img.alpha_composite(reduce(big))
    return img.convert("RGB")


def make_background(has_left_cam):
    """White to the faintest grey, a whisper of the team watermark, and the panels' frames and shadows baked in."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((x - 960) / 1250) ** 2 + ((y - 470) / 900) ** 2)
    v = np.clip(r, 0, 1)[..., None]
    img = np.array([255, 255, 255], np.float32) * (1 - v) + np.array([240, 240, 243], np.float32) * v
    bg = Image.fromarray(img.astype(np.uint8), "RGB").convert("RGBA")
    wm = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(wm)
    f = font(SEMI, 22)
    s = "TEAM SE7EN   ·   SAMSUNG PRISM GENAI HACKATHON 3.0   ·   THEME 05   ·   "
    unit = tlen(f, s, 5)
    for row, yy in enumerate(range(0, H * 2, 230)):
        xx = -(row % 2) * unit / 2
        while xx < W * 2:
            text(d, xx, yy, s, f, (0, 0, 0, 6), 5)
            xx += unit
    wm = wm.rotate(-18, resample=Image.BICUBIC, center=(W, H)).crop((W // 2, H // 2, W // 2 + W, H // 2 + H))
    bg.alpha_composite(wm)
    # soft shadows under the cameras and the phone, and the phone's graphite frame
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(sh)
    cams = [CAM_R] + ([CAM_L] if has_left_cam else [])
    for x0, y0, pw, ph, rr in cams:
        ds.rounded_rectangle((x0, y0 + 14, x0 + pw, y0 + ph + 14), rr, fill=(0, 0, 0, 52))
    px, py, pw, ph, pr = PHONE
    ds.rounded_rectangle((px - 11, py - 11 + 20, px + pw + 11, py + ph + 11 + 20), pr + 11, fill=(0, 0, 0, 70))
    bg.alpha_composite(sh.filter(ImageFilter.GaussianBlur(26)))
    d = ImageDraw.Draw(bg)
    d.rounded_rectangle((px - 11, py - 11, px + pw + 11, py + ph + 11), pr + 11, fill=(32, 32, 35, 255))
    d.rounded_rectangle((px - 11, py - 11, px + pw + 11, py + ph + 11), pr + 11, outline=(78, 78, 84, 255), width=1)
    d.rounded_rectangle((px - 2, py - 2, px + pw + 2, py + ph + 2), pr + 2, fill=(10, 10, 12, 255))
    return bg


def panel_mask(rect):
    _, _, pw, ph, r = rect
    big = Image.new("L", (pw * 6, ph * 6), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, pw * 6 - 1, ph * 6 - 1), r * 6, fill=255)
    return big.resize((pw, ph), Image.LANCZOS)


def draw_panel(cv, img, rect, mask):
    x, y, pw, ph, _ = rect
    cx0, cy0, cw, ch = cover_box(img.size, rect)
    content = img.resize((pw, ph), Image.LANCZOS, box=(cx0, cy0, cx0 + cw, cy0 + ch))
    cv.paste(content, (x, y), mask)


# --------------------------------------------------------------------------------------------- timeline
def ramp_times(a, b, peak, ramp=0.3):
    """Screen times for a fast-forward from a to b: the speed eases from 1x up to the peak and back to 1x."""
    acc = (peak - 1.0) / ramp
    s, v, out, dt = a, 1.0, [], 1.0 / FPS
    while s < b - 1e-6:
        out.append(s)
        remaining = b - s
        decel = (v * v - 1.0) / (2 * acc) if acc > 0 else 0.0
        v = max(1.0, v - acc * dt) if remaining <= decel + v * dt else min(peak, v + acc * dt)
        s += v * dt
    return out


def auto_peak(d):
    return 3.5 if d < 3.5 else (4.0 if d < 5.0 else 6.0)


def timeline(sc):
    """Per output frame, the screen time it shows; and the pieces (1x or fast-forward) with their frame ranges."""
    pieces, t = [], sc["t_in"]
    for ff in sorted(sc["ff"]):
        a, b = ff[0], ff[1]
        if a > t:
            pieces.append({"a": t, "b": a, "ff": False})
        peak = ff[2] if len(ff) > 2 else auto_peak(b - a)
        label = ff[3] if len(ff) > 3 else "%.1f s OF WAITING" % (b - a)
        pieces.append({"a": a, "b": b, "ff": True, "peak": peak, "label": label})
        t = b
    if t < sc["t_out"]:
        pieces.append({"a": t, "b": sc["t_out"], "ff": False})
    times = []
    for p in pieces:
        p["f0"] = len(times)
        if p["ff"]:
            times += ramp_times(p["a"], p["b"], p["peak"], 0.45 if p["peak"] <= 2.5 else 0.3)
        else:
            n = int(round((p["b"] - p["a"]) * FPS))
            times += [p["a"] + i / FPS for i in range(n)]
        p["f1"] = len(times)
    n_main = len(times)
    times += [sc["t_out"]] * int(round(sc.get("hold", 0.0) * FPS))
    return times, pieces, n_main


class Clock:
    """Screen time <-> output time of one scenario."""

    def __init__(self, times, n_main):
        self.s = np.array(times[:n_main], np.float64)
        self.f = np.arange(n_main, dtype=np.float64)

    def o(self, s):
        return float(np.interp(s, self.s, self.f)) / FPS


# --------------------------------------------------------------------------------------------- captions on the sound
_aud16 = {}


def onset_after(clip, s):
    """Where sound really starts at or after s (Whisper's word starts can be early): s itself if the speech is already
    going (a caption split mid-sentence), else the first onset in the next 0.8 s."""
    if clip not in _aud16:
        x, sr = sf.read(os.path.join(HERE, "audio", clip + ".wav"), dtype="float32")
        _aud16[clip] = x
    x, sr, hop = _aud16[clip], 16000, 320
    a, b = int((s - 1.5) * sr), int((s + 1.5) * sr)
    seg = x[max(0, a): b]
    n = len(seg) // hop
    db = 20 * np.log10(np.sqrt(np.square(seg[: n * hop]).reshape(n, hop).mean(axis=1)) + 1e-9)
    floor = np.percentile(db, 15)
    on = db > max(floor + 14, -48)
    t0 = max(0, a) / sr
    i0 = int(round((s - t0) * 50))
    if 0 <= i0 < len(on) and on[max(0, i0 - 2): i0 + 1].any():
        return s
    for i in range(max(0, i0 - 4), min(len(on), i0 + 41)):
        if on[i] and not on[max(0, i - 6): i].any():
            return max(s, t0 + i / 50 - 0.04)
    return s


# --------------------------------------------------------------------------------------------- overlays
class Overlay:
    def __init__(self, sprite, x, y, o0, o1, fin=0.3, fout=0.3, dx=0, dy=0, anchor="tl"):
        pad = 0
        if isinstance(sprite, tuple):
            sprite, pad = sprite
        self.sprite, self.o0, self.o1, self.fin, self.fout, self.dx, self.dy = sprite, o0, o1, fin, fout, dx, dy
        w, h = sprite.width - 2 * pad, sprite.height - 2 * pad
        if anchor == "tr":
            x -= w
        elif anchor == "bc":
            x, y = x - w / 2, y - h
        elif anchor == "tc":
            x -= w / 2
        self.x, self.y = x - pad, y - pad
        self.cache = {}

    def draw(self, cv, o):
        if o < self.o0 or o > self.o1:
            return
        a_in = ease_out((o - self.o0) / self.fin) if self.fin else 1.0
        a = min(a_in, ease((self.o1 - o) / self.fout) if self.fout else 1.0)
        if a <= 0.01:
            return
        q = int(round(a * 24))
        spr = self.cache.get(q)
        if spr is None:
            spr = self.sprite
            if q < 24:
                r, g, b, al = spr.split()
                spr = Image.merge("RGBA", (r, g, b, al.point(lambda v, q=q: v * q // 24)))
            self.cache[q] = spr
        x = int(round(self.x + self.dx * (1 - a_in)))
        y = int(round(self.y + self.dy * (1 - a_in)))
        x, y = max(0, min(W - spr.width, x)), max(0, min(H - spr.height, y))
        cv.alpha_composite(spr, dest=(x, y))


def build_overlays(sc, clock, pieces, o_end):
    ov, so = [], clock.o
    views = 2 if sc.get("camL") is None else 3
    intro_end = so(5.55) if sc.get("intro") else 0.0
    t_title = max(0.25, intro_end - 0.1)
    ov.append(Overlay(sprite_title(sc["n"], sc["title"], sc["sub"]), 40, 28, t_title, o_end, 0.5, 0.3, dy=12))
    ov.append(Overlay(sprite_live(views), W / 2, 8, t_title, o_end, 0.5, 0.3, anchor="tc"))
    if sc.get("camL"):
        ov.append(Overlay(sprite_tag("FRONT VIEW"), CAM_L[0] + 16, CAM_L[1] + 16, t_title, o_end, 0.4, 0.3))
    ov.append(Overlay(sprite_tag("TOP-DOWN VIEW"), CAM_R[0] + 16, CAM_R[1] + 16, t_title, o_end, 0.4, 0.3))
    ffs = [(p["a"], p["b"], p["label"]) for p in pieces if p["ff"]]
    # captions, each starting on its sound
    caps = [(spk, onset_after(sc["screen"], a) if a > sc["t_in"] + 0.3 else a, b, s) for spk, a, b, s in sc["captions"]]
    starts = {round(c[1], 2): c for c in sc["captions"]}
    snapped = {round(orig[1], 2): new[1] for orig, new in zip(sc["captions"], caps)}
    for i, (spk, a, b, s) in enumerate(caps):
        nxt = caps[i + 1][1] if i + 1 < len(caps) else sc["t_out"] + sc.get("hold", 0)
        o0, o1 = so(a) - 0.04, min(so(max(b, a + 0.5)) + 0.45, so(nxt) - 0.06, o_end)
        for fa, fb, _ in ffs:            # nothing captioned through a fast-forward
            if a < fa < b + 0.45:
                o1 = min(o1, so(fa))
        ov.append(Overlay(sprite_caption(spk, s), W / 2, 1046, o0, o1, 0.12, 0.12, dy=6, anchor="bc"))
    for fa, fb, label in ffs:
        ov.append(Overlay(sprite_ff(label), W / 2, 1046, so(fa) - 0.05, so(fb) + 0.15, 0.18, 0.18, dy=6, anchor="bc"))
    # chips: up to three per side, each in the first free row
    rows = {"L": [], "R": []}
    for side, kind, a, b, s in sorted(sc["chips"], key=lambda c: c[2]):
        o0, o1 = so(a), min(so(b), o_end)
        slot = 0
        while any(r == slot and not (o0 > e + 0.35) for r, e in rows[side]):
            slot += 1
        rows[side].append((slot, o1))
        y = 690 + slot * 58
        if side == "L":
            ov.append(Overlay(sprite_chip(kind, s), 28, y, o0, o1, 0.35, 0.3, dx=-20))
        else:
            ov.append(Overlay(sprite_chip(kind, s), W - 28, y, o0, o1, 0.35, 0.3, dx=20, anchor="tr"))
    # the minds: a card per request that grows by a row as each step happens; quoted lines sit on DUET's voice
    reqs = sc["minds"]
    for i, r in enumerate(reqs):
        o0 = so(r["t"])
        o1 = so(reqs[i + 1]["t"]) - 0.05 if i + 1 < len(reqs) else o_end
        lines = []
        for t, label, value in r["lines"]:
            if label.startswith("“"):
                near = [snapped[k] for k in snapped if abs(k - t) < 0.35]
                t = near[0] if near else t
            lines.append((t, label, value))
        times = [max(o0 + 0.15, so(t)) for t, _, _ in lines]
        n_l = len(lines)
        for k in range(1, n_l + 1):
            a = o0 if k == 1 else times[k - 1]
            b = times[k] if k < n_l else o1
            ov.append(Overlay(sprite_mind_card(r["mind"], k), W - 28, 26, a, min(b, o1), 0.3 if k == 1 else 0.0,
                              0.25 if k == n_l else 0.0, dy=-8 if k == 1 else 0, anchor="tr"))
        for j, (t, label, value) in enumerate(lines):
            ov.append(Overlay(sprite_mind_line(label, value), W - 28 - CARD_W + 22, 26 + HEAD + 6 + ROW * j,
                              times[j], o1, 0.25, 0.25, dx=8))
    # what DUET is doing, when there is no second camera
    if sc.get("steps"):
        ov.append(Overlay(sprite_steps_frame(), CAM_L[0], CAM_L[1], 0.2, o_end, 0.4, 0.3))
        y = CAM_L[1] + 58
        for t_on, t_ch, k1, who, s1, k2, s2 in sc["steps"]:
            if t_ch is None:
                ov.append(Overlay(sprite_step(k1, who, s1), CAM_L[0] + 24, y, so(t_on), o_end, 0.35, 0.3, dx=-12))
            else:
                ov.append(Overlay(sprite_step(k1, who, s1), CAM_L[0] + 24, y, so(t_on), so(t_ch), 0.35, 0.2, dx=-12))
                ov.append(Overlay(sprite_step(k2, who, s2), CAM_L[0] + 24, y, so(t_ch) - 0.1, o_end, 0.25, 0.3))
            y += 52
    if sc.get("intro"):
        ov.insert(0, Overlay(sprite_intro(), 0, 0, 0.0, intro_end, 0.0, 0.45))
    return ov


# --------------------------------------------------------------------------------------------- audio
def tempo(x, factor):
    tmp_in, tmp_out = os.path.join(HERE, "_ff_in.wav"), os.path.join(HERE, "_ff_out.wav")
    sf.write(tmp_in, x, SR, subtype="FLOAT")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", tmp_in, "-filter:a", "atempo=%.4f" % factor, tmp_out], check=True)
    y, _ = sf.read(tmp_out, dtype="float32", always_2d=True)
    return y


def scenario_audio(sc, pieces, n_total):
    src = screen_audio(sc["screen"])
    parts = []
    for p in pieces:
        s0, s1 = int(round(p["f0"] / FPS * SR)), int(round(p["f1"] / FPS * SR))
        need = s1 - s0
        piece = src[int(round(p["a"] * SR)): int(round(p["b"] * SR))]
        if p["ff"]:
            piece = tempo(piece, max(0.5, (p["b"] - p["a"]) / (need / SR))) * 0.14
        if len(piece) < need:
            piece = np.concatenate([piece, np.zeros((need - len(piece), 2), np.float32)])
        n_f = int((0.08 if p["ff"] else 0.012) * SR)
        parts.append(fade(piece[:need], n_f, n_f))
    total = int(round(n_total / FPS * SR))
    x = np.concatenate(parts)
    if len(x) < total:
        x = np.concatenate([x, np.zeros((total - len(x), 2), np.float32)])
    return fade(x[:total], int(0.12 * SR), int(0.3 * SR))


# --------------------------------------------------------------------------------------------- render
def render_scenario(sc, write, statics):
    times, pieces, n_main = timeline(sc)
    n_total = len(times)
    clock = Clock(times, n_main)
    blurs = sc.get("blurs", {})
    srcs = {"phone": Source(sc["screen"], 0.0)}
    if sc.get("camL"):
        srcs["camL"] = Source(sc["camL"][0], sc["camL"][1], blurs.get("camL"))
    srcs["camR"] = Source(sc["camR"][0], sc["camR"][1], blurs.get("camR"))
    for s in srcs.values():
        s.start(sc["t_in"])
    bg = statics["bg3"] if sc.get("camL") else statics["bg2"]
    masks = statics["masks"]
    ov = build_overlays(sc, clock, pieces, n_total / FPS)
    t0 = time.time()
    for f, s in enumerate(times):
        o = f / FPS
        cv = bg.copy()
        for name in ("camL", "camR", "phone"):
            if name in srcs:
                draw_panel(cv, srcs[name].get(s), RECTS[name], masks[name])
        for item in ov:
            item.draw(cv, o)
        out = cv.convert("RGB")
        a = min(ease(o / FADE_S), ease((n_total / FPS - o) / FADE_S))
        if a < 0.999:
            out = Image.blend(WHITE_FRAME, out, a)
        write(out.tobytes())
        if (f + 1) % 300 == 0:
            print("  scenario %d: %d / %d frames (%.1f fps)" % (sc["n"], f + 1, n_total, (f + 1) / (time.time() - t0)), flush=True)
    return n_total, scenario_audio(sc, pieces, n_total)


def outro(write, seconds=3.0):
    img = sprite_outro()
    n = int(round(seconds * FPS))
    for i in range(n):
        o = i / FPS
        a = min(ease(o / 0.45), ease((seconds - o) / 0.6))
        write((Image.blend(WHITE_FRAME, img, a) if a < 0.999 else img).tobytes())
    return n, np.zeros((int(round(n / FPS * SR)), 2), np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", default="")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    chosen = edl.SCENARIOS if args.all else [s for s in edl.SCENARIOS if s["n"] == args.scenario]
    statics = {"bg3": make_background(True), "bg2": make_background(False), "masks": {k: panel_mask(v) for k, v in RECTS.items()}}

    if args.frames:
        idx = {int(round(float(t) * FPS)) for t in args.frames.split(",")}
        counter = {"f": 0}

        def write(b):
            if counter["f"] in idx:
                Image.frombytes("RGB", (W, H), b).save(out.replace(".png", "_%05.1f.png" % (counter["f"] / FPS)))
            counter["f"] += 1
        for sc in chosen:
            render_scenario(sc, write, statics)
        return

    tmp_v = out + ".video.mp4"
    enc = subprocess.Popen([FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", str(FPS),
                            "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-tune", "film",
                            tmp_v], stdin=subprocess.PIPE)
    audio, frames, t0 = [], 0, time.time()
    for sc in chosen:
        n, a = render_scenario(sc, enc.stdin.write, statics)
        frames += n
        audio.append(a)
    if args.all:
        n, a = outro(enc.stdin.write)
        frames += n
        audio.append(a)
    enc.stdin.close()
    enc.wait()
    raw, norm = out + ".raw.wav", out + ".norm.wav"
    sf.write(raw, np.concatenate(audio), SR, subtype="FLOAT")
    loudnorm(raw, norm)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", tmp_v, "-i", norm, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], check=True)
    for p in (tmp_v, raw, norm):
        os.remove(p)
    print("wrote %s: %d frames (%.1f s) in %.0f s" % (out, frames, frames / FPS, time.time() - t0))


if __name__ == "__main__":
    main()
