"""Renders the DUET live-demo film (or one scenario) from edl.py.

Each output frame is composited in Python (PIL): the S24 screen recording in the centre, the cameras either side, all
on the screen recording's clock. Zooms are camera moves with sub-pixel source boxes (no jitter), fast-forwards sample
every source at 4x, and every overlay (captions, chips, the minds and their latencies) is drawn as an antialiased
rounded sprite. ffmpeg only encodes. Audio: the S24 screen recording's own track.

    python render.py --scenario 3 --out ../out/preview_s3.mp4
    python render.py --all --out ../out/DUET_live_demo.mp4
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

import av
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edl  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
FONTS = os.path.join(HERE, "fonts")
FFMPEG = r"E:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
W, H, FPS, SR = 1920, 1080, 30, 48000

PHONE = (761, 44, 397, 860, 26)
CAM_L = (28, 262, 700, 394, 18)
CAM_R = (1192, 262, 700, 394, 18)
RECTS = {"phone": PHONE, "camL": CAM_L, "camR": CAM_R}

YELLOW = (255, 212, 0)
WHITE = (242, 244, 250)
MUTED = (150, 160, 190)
GREEN = (34, 197, 142)
BLUE = (91, 140, 255)
ORANGE = (255, 138, 76)
AMBER = (245, 184, 64)
ICON = {"ok": GREEN, "no": ORANGE, "info": BLUE, "hold": AMBER}

REG, SEMI, BOLD, BLACK = "segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "seguibl.ttf"
SS = 2              # sprites are drawn at 2x and reduced: smooth corners and text
ZOOM_S = 0.6        # a camera move takes this long
FADE_S = 0.4        # dip to black between scenarios


# --------------------------------------------------------------------------------------------- drawing helpers
_fonts = {}


def font(name, px):
    key = (name, px)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(os.path.join(FONTS, name), px)
    return _fonts[key]


def tlen(f, s, spacing=0):
    return f.getlength(s) + spacing * max(0, len(s) - 1)


def text(d, x, y, s, f, fill, spacing=0):
    if not spacing:
        d.text((x, y), s, font=f, fill=fill)
        return
    for ch in s:
        d.text((x, y), ch, font=f, fill=fill)
        x += f.getlength(ch) + spacing


def canvas(w, h):
    return Image.new("RGBA", (int(w * SS), int(h * SS)), (0, 0, 0, 0))


def reduce(img):
    return img.resize((img.width // SS, img.height // SS), Image.LANCZOS)


def ease(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def icon(d, kind, cx, cy, r):
    """A round status icon drawn as vectors (no font glyphs): check, cross, dot or pause."""
    col = ICON[kind]
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col + (255,))
    w = max(2, int(r * 0.26))
    if kind == "ok":
        d.line([(cx - r * 0.45, cy + r * 0.02), (cx - r * 0.12, cy + r * 0.36), (cx + r * 0.48, cy - r * 0.34)],
               fill=(255, 255, 255, 255), width=w, joint="curve")
    elif kind == "no":
        k = r * 0.38
        d.line([(cx - k, cy - k), (cx + k, cy + k)], fill=(255, 255, 255, 255), width=w)
        d.line([(cx - k, cy + k), (cx + k, cy - k)], fill=(255, 255, 255, 255), width=w)
    elif kind == "hold":
        k = r * 0.34
        d.rectangle((cx - k - w * 0.2, cy - k, cx - k + w * 0.8, cy + k), fill=(20, 20, 20, 255))
        d.rectangle((cx + k - w * 0.8, cy - k, cx + k + w * 0.2, cy + k), fill=(20, 20, 20, 255))
    else:
        d.ellipse((cx - r * 0.3, cy - r * 0.3, cx + r * 0.3, cy + r * 0.3), fill=(255, 255, 255, 255))


def wrap(s, f, maxw):
    words, lines, cur = s.split(), [], ""
    for w_ in words:
        nxt = (cur + " " + w_).strip()
        if cur and f.getlength(nxt) > maxw:
            lines.append(cur)
            cur = w_
        else:
            cur = nxt
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------------------------- sprites
def sprite_caption(speaker, s):
    f, ft = font(SEMI, 38 * SS), font(BOLD, 17 * SS)
    tag = "DUET" if speaker == "DUET" else "YOU"
    tag_w = tlen(ft, tag, 3 * SS) + 26 * SS
    lines = wrap(s, f, 1180 * SS - tag_w - 70 * SS)
    lh = 50 * SS
    tw = max(f.getlength(l) for l in lines)
    w = 28 * SS + tag_w + 18 * SS + tw + 30 * SS
    h = 16 * SS + lh * len(lines) + 12 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 22 * SS, fill=(6, 9, 20, 205), outline=(255, 255, 255, 22), width=SS)
    tx, ty = 28 * SS, 16 * SS + (lh - 30 * SS) / 2 + 2 * SS
    tag_fill = (91, 140, 255, 235) if tag == "DUET" else (255, 255, 255, 40)
    d.rounded_rectangle((tx, ty, tx + tag_w, ty + 30 * SS), 15 * SS, fill=tag_fill)
    text(d, tx + 13 * SS, ty + 4 * SS, tag, ft, (255, 255, 255, 255), 3 * SS)
    x = tx + tag_w + 18 * SS
    for i, l in enumerate(lines):
        text(d, x, 16 * SS + i * lh, l, f, YELLOW + (255,))
    return reduce(img)


def sprite_chip(kind, s):
    f = font(SEMI, 22 * SS)
    h = 46 * SS
    w = 16 * SS + 28 * SS + 14 * SS + f.getlength(s) + 22 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), h / 2, fill=(14, 20, 42, 225), outline=(255, 255, 255, 30), width=SS)
    icon(d, kind, 16 * SS + 14 * SS, h / 2, 13 * SS)
    text(d, 16 * SS + 28 * SS + 14 * SS, h / 2 - 16 * SS, s, f, WHITE + (255,))
    return reduce(img)


def sprite_title(n, title, sub):
    fk, ft, fs = font(SEMI, 17 * SS), font(BOLD, 42 * SS), font(REG, 22 * SS)
    kicker = "SCENARIO %02d / 05" % n
    w = max(tlen(fk, kicker, 4 * SS), ft.getlength(title), fs.getlength(sub)) + 10 * SS
    img = canvas(w / SS + 2, 136)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 6 * SS, 34 * SS, 10 * SS), 2 * SS, fill=YELLOW + (255,))
    text(d, 44 * SS, 0, kicker, fk, MUTED + (255,), 4 * SS)
    text(d, 0, 26 * SS, title, ft, WHITE + (255,))
    text(d, 0, 88 * SS, sub, fs, (200, 206, 226, 255))
    return reduce(img)


def sprite_live(views):
    f = font(SEMI, 15 * SS)
    s = "LIVE  \u00b7  SAMSUNG GALAXY S24  \u00b7  %d VIEWS IN SYNC" % views
    h = 30 * SS
    w = 16 * SS + 12 * SS + 10 * SS + tlen(f, s, 2 * SS) + 16 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), h / 2, fill=(14, 20, 42, 200), outline=(255, 255, 255, 26), width=SS)
    d.ellipse((16 * SS, h / 2 - 6 * SS, 28 * SS, h / 2 + 6 * SS), fill=(255, 77, 77, 255))
    text(d, 38 * SS, h / 2 - 11 * SS, s, f, (225, 230, 245, 255), 2 * SS)
    return reduce(img)


def sprite_ff(skipped):
    f = font(BOLD, 20 * SS)
    s = "4\u00d7  FAST-FORWARD  \u00b7  %.1f s OF WAITING" % skipped
    h = 44 * SS
    w = 20 * SS + 34 * SS + 12 * SS + tlen(f, s, 1 * SS) + 24 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), h / 2, fill=YELLOW + (245,))
    x0, cy = 20 * SS, h / 2
    for k in (0, 15 * SS):          # two play triangles
        d.polygon([(x0 + k, cy - 10 * SS), (x0 + k + 15 * SS, cy), (x0 + k, cy + 10 * SS)], fill=(16, 18, 30, 255))
    text(d, 20 * SS + 34 * SS + 12 * SS, h / 2 - 15 * SS, s, f, (16, 18, 30, 255), 1 * SS)
    return reduce(img)


def sprite_tag(s):
    f = font(BOLD, 13 * SS)
    h = 26 * SS
    w = 12 * SS + tlen(f, s, 3 * SS) + 12 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), h / 2, fill=(0, 0, 0, 150))
    text(d, 12 * SS, 5 * SS, s, f, (235, 238, 248, 255), 3 * SS)
    return reduce(img)


CARD_W = 480


def sprite_mind_card(mind, nlines):
    """The card's frame for one request: the mind's pill, room for its lines, the footnote."""
    label, sub, col = edl.MINDS[mind]
    fl, fs, ff = font(BOLD, 17 * SS), font(REG, 16 * SS), font(REG, 13 * SS)
    w = CARD_W * SS
    h = (12 + 40 + 8 + 31 * nlines + 8 + 22 + 10) * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 20 * SS, fill=(12, 17, 36, 228), outline=(255, 255, 255, 28), width=SS)
    pill = (12 * SS, 12 * SS, w - 12 * SS, 52 * SS)
    d.rounded_rectangle(pill, 20 * SS, fill=col + (46,))
    d.ellipse((26 * SS, 26 * SS, 38 * SS, 38 * SS), fill=col + (255,))
    text(d, 48 * SS, 20 * SS, label, fl, (255, 255, 255, 255), 1 * SS)
    text(d, 48 * SS + tlen(fl, label, SS) + 12 * SS, 21 * SS, sub, fs, (205, 212, 232, 255))
    text(d, 20 * SS, h - 30 * SS, "seconds after you stopped speaking  \u00b7  from DUET's log", ff, MUTED + (255,))
    return reduce(img)


def sprite_mind_line(label, value):
    fl, fv = font(SEMI, 19 * SS), font(BOLD, 19 * SS)
    w, h = (CARD_W - 40) * SS, 30 * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    text(d, 0, 2 * SS, label, fl, (226, 230, 244, 255))
    if value:
        vcol = ORANGE if value in ("held", "never ran") else WHITE
        text(d, w - fv.getlength(value), 2 * SS, value, fv, vcol + (255,))
    return reduce(img)


def sprite_steps_frame():
    w, h = CAM_L[2], CAM_L[3]
    img = canvas(w, h)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), CAM_L[4] * SS, fill=(12, 17, 36, 235),
                        outline=(255, 255, 255, 28), width=SS)
    text(d, 28 * SS, 24 * SS, "WHAT DUET DID", font(BOLD, 15 * SS), MUTED + (255,), 4 * SS)
    return reduce(img)


def sprite_step(kind, s, s2=None):
    f, f2 = font(SEMI, 21 * SS), font(REG, 17 * SS)
    w = (CAM_L[2] - 56) * SS
    lines = wrap(s, f, w - 46 * SS)
    h = (30 * len(lines) + (26 if s2 else 0) + 6) * SS
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    icon(d, kind, 14 * SS, 15 * SS, 13 * SS)
    for i, l in enumerate(lines):
        text(d, 44 * SS, i * 30 * SS, l, f, WHITE + (255,))
    if s2:
        text(d, 44 * SS, len(lines) * 30 * SS + 2 * SS, s2, f2, (190, 198, 222, 255))
    return reduce(img)


def sprite_intro():
    img = Image.new("RGBA", (W * SS, H * SS), (5, 8, 18, 238))
    d = ImageDraw.Draw(img)
    fw = font(BLACK, 150 * SS)
    s = "DUET"
    tw = tlen(fw, s, 18 * SS)
    text(d, (W * SS - tw) / 2, 300 * SS, s, fw, (255, 255, 255, 255), 18 * SS)
    d.rounded_rectangle(((W / 2 - 40) * SS, 510 * SS, (W / 2 + 40) * SS, 516 * SS), 3 * SS, fill=YELLOW + (255,))
    for y, s, f, col in [(548, "A full-duplex voice agent, live on a Samsung Galaxy S24", font(SEMI, 34 * SS), WHITE),
                         (612, "Fast mind: Gemma 4 E2B on our laptop GPU   \u00b7   Cloud mind: Gemma 4 26B", font(REG, 24 * SS), (196, 204, 228)),
                         (700, "TEAM SE7EN  \u00b7  SAMSUNG PRISM GENAI HACKATHON 3.0  \u00b7  THEME 05", font(SEMI, 17 * SS), MUTED)]:
        sp = 4 * SS if s.isupper() else 0
        text(d, (W * SS - tlen(f, s, sp)) / 2, y * SS, s, f, col + (255,), sp)
    return reduce(img)


def sprite_outro():
    img = Image.new("RGBA", (W * SS, H * SS), (5, 8, 18, 255))
    d = ImageDraw.Draw(img)
    fw = font(BLACK, 120 * SS)
    s = "DUET"
    text(d, (W * SS - tlen(fw, s, 14 * SS)) / 2, 230 * SS, s, fw, (255, 255, 255, 255), 14 * SS)
    d.rounded_rectangle(((W / 2 - 40) * SS, 404 * SS, (W / 2 + 40) * SS, 410 * SS), 3 * SS, fill=YELLOW + (255,))
    f = font(SEMI, 32 * SS)
    for i, (kind, s) in enumerate([("no", "What you take back never runs."),
                                   ("ok", "Every \u201cdone\u201d is the device's own reply."),
                                   ("info", "Fast mind on our GPU, cloud mind when it needs to think.")]):
        y = (462 + i * 64) * SS
        tw = f.getlength(s) + 50 * SS
        x = (W * SS - tw) / 2
        icon(d, kind, x + 16 * SS, y + 22 * SS, 15 * SS)
        text(d, x + 50 * SS, y, s, f, WHITE + (255,))
    s = "TEAM SE7EN  \u00b7  SRM INSTITUTE OF SCIENCE AND TECHNOLOGY  \u00b7  SAMSUNG PRISM GENAI HACKATHON 3.0  \u00b7  THEME 05"
    f = font(SEMI, 16 * SS)
    text(d, (W * SS - tlen(f, s, 4 * SS)) / 2, 720 * SS, s, f, MUTED + (255,), 4 * SS)
    return reduce(img).convert("RGB")


def make_background():
    """Deep navy gradient with a soft glow behind the phone, and the team watermark tiled faintly."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    t = (x / W * 0.55 + y / H * 0.45)
    c0, c1 = np.array([6, 9, 20], np.float32), np.array([17, 26, 58], np.float32)
    img = c0[None, None, :] * (1 - t[..., None]) + c1[None, None, :] * t[..., None]
    g = np.exp(-(((x - 960) / 620) ** 2 + ((y - 470) / 520) ** 2))
    img += g[..., None] * np.array([10, 16, 38], np.float32)
    v = np.clip(1 - 0.35 * (((x - 960) / 1100) ** 2 + ((y - 540) / 760) ** 2), 0.6, 1)
    img *= v[..., None]
    bg = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    # watermark: a diagonal tiling, very faint
    wm = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(wm)
    f = font(SEMI, 24)
    s = "TEAM SE7EN   \u00b7   SAMSUNG PRISM GENAI HACKATHON 3.0   \u00b7   THEME 05   \u00b7   "
    unit = tlen(f, s, 5)
    for row, yy in enumerate(range(0, H * 2, 210)):
        xx = -(row % 2) * unit / 2
        while xx < W * 2:
            text(d, xx, yy, s, f, (255, 255, 255, 7), 5)
            xx += unit
    wm = wm.rotate(-18, resample=Image.BICUBIC, center=(W, H))
    wm = wm.crop((W // 2, H // 2, W // 2 + W, H // 2 + H))
    bg.alpha_composite(wm)
    return bg


def panel_mask(rect):
    """The panel's rounded mask at 2x its size, antialiased (drawn at 8x and reduced)."""
    _, _, pw, ph, r = rect
    big = Image.new("L", (pw * 8, ph * 8), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, pw * 8 - 1, ph * 8 - 1), r * 8, fill=255)
    return big.resize((pw * 2, ph * 2), Image.LANCZOS)


def panel_shadow(rect):
    x, y, pw, ph, r = rect
    pad = 40
    img = Image.new("RGBA", (pw + 2 * pad, ph + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((pad, pad + 8, pad + pw, pad + ph + 8), r, fill=(0, 0, 0, 150))
    img = img.filter(ImageFilter.GaussianBlur(16))
    ImageDraw.Draw(img).rounded_rectangle((pad - 1, pad - 1, pad + pw, pad + ph), r + 1, outline=(255, 255, 255, 30), width=1)
    return img, (x - pad, y - pad)


# --------------------------------------------------------------------------------------------- sources
class Source:
    """Frames of one clip on the screen recording's clock: get(t) is the latest frame at or before t + offset."""

    def __init__(self, name, offset, blurs=None):
        self.path = os.path.join(SRC, name + ".mp4")
        self.offset = offset
        self.blurs = blurs or []
        self.c = av.open(self.path)
        self.s = self.c.streams.video[0]
        self.s.thread_type = "AUTO"
        self.size = (self.s.codec_context.width, self.s.codec_context.height)
        self.cur = self.nxt = self.img = None
        self.it = None

    def _t(self, fr):
        return fr.time if fr.time is not None else float(fr.pts * self.s.time_base)

    def start(self, t):
        t = t + self.offset
        self.c.seek(max(0, int((t - 1.5) / self.s.time_base)), stream=self.s, backward=True, any_frame=False)
        self.it = self.c.decode(self.s)
        self.cur, self.nxt, self.img = None, next(self.it, None), None

    def get(self, t):
        t = t + self.offset
        while self.nxt is not None and self._t(self.nxt) <= t + 1e-4:
            self.cur, self.img = self.nxt, None
            self.nxt = next(self.it, None)
        if self.cur is None:
            self.cur, self.img = self.nxt, None
        if self.img is None:
            img = self.cur.to_image()
            if img.size != self.size:
                self.size = img.size
            self.img = self._blur(img, self._t(self.cur))
        return self.img

    def _blur(self, img, t):
        ks = self.blurs
        if not ks or t < ks[0][0] - 0.1 or t > ks[-1][0] + 0.1:
            return img
        if t <= ks[0][0]:
            box = ks[0][1]
        elif t >= ks[-1][0]:
            box = ks[-1][1]
        else:
            i = max(j for j in range(len(ks)) if ks[j][0] <= t)
            (ta, a), (tb, b) = ks[i], ks[i + 1]
            u = (t - ta) / (tb - ta)
            box = tuple(a[k] + (b[k] - a[k]) * u for k in range(4))
        box = tuple(int(round(v)) for v in box)
        bw, bh = box[2] - box[0], box[3] - box[1]
        region = img.crop(box).filter(ImageFilter.GaussianBlur(10))
        m = Image.new("L", (bw, bh), 0)                      # a soft-edged rounded patch, not a hard square
        ImageDraw.Draw(m).rounded_rectangle((6, 6, bw - 7, bh - 7), 18, fill=255)
        m = m.filter(ImageFilter.GaussianBlur(4))
        img = img.copy()
        img.paste(region, box[:2], m)
        return img


def cover_box(size, rect):
    sw, sh = size
    a = rect[2] / rect[3]
    if sw / sh > a:
        cw, ch = sh * a, sh
        return ((sw - cw) / 2, 0.0, cw, ch)
    cw, ch = sw, sw / a
    return (0.0, (sh - ch) / 2, cw, ch)


def draw_panel(cv, img, rect, mask2, win):
    """Draw a panel through the camera window win = (x0, y0, k): source box and mask follow sub-pixel."""
    x0, y0, k = win
    X, Y, PW, PH, _ = rect
    sx, sy, sw, sh = (X - x0) * k, (Y - y0) * k, PW * k, PH * k
    vx0, vy0 = max(0, math.floor(sx)), max(0, math.floor(sy))
    vx1, vy1 = min(W, math.ceil(sx + sw)), min(H, math.ceil(sy + sh))
    if vx1 - vx0 < 2 or vy1 - vy0 < 2:
        return
    u0, v0 = max(0.0, (vx0 - sx) / k), max(0.0, (vy0 - sy) / k)
    u1, v1 = min(PW, (vx1 - sx) / k), min(PH, (vy1 - sy) / k)
    cx0, cy0, cw, ch = cover_box(img.size, rect)
    box = (cx0 + u0 / PW * cw, cy0 + v0 / PH * ch, cx0 + u1 / PW * cw, cy0 + v1 / PH * ch)
    size = (vx1 - vx0, vy1 - vy0)
    content = img.resize(size, Image.BICUBIC, box=box)
    m = mask2.resize(size, Image.BILINEAR, box=(u0 * 2, v0 * 2, u1 * 2, v1 * 2))
    cv.paste(content, (vx0, vy0), m)


def window_for(target, fy, z, rects):
    if target == "phone":
        x, y, pw, ph, _ = rects["phone"]
        # the focus sits a little above the middle, but the phone's bottom edge never rises above the captions
        y0 = min(y + fy * ph - 470 / z, y + ph - 975 / z)
        return (x + pw / 2, y0 + (H / z) / 2, W / z)
    x, y, pw, ph, _ = rects[target]
    return (x + pw / 2, y + ph / 2 + 40 / z, W / z)


WIDE = (W / 2, H / 2, float(W))


def lerp_win(a, b, u):
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, math.exp(math.log(a[2]) + (math.log(b[2]) - math.log(a[2])) * u))


# --------------------------------------------------------------------------------------------- timeline
def segments(sc):
    pieces, t = [], sc["t_in"]
    for a, b in sorted(sc["ff"]):
        if a > t:
            pieces.append((t, a, 1.0))
        pieces.append((a, b, edl.FF_SPEED))
        t = b
    if t < sc["t_out"]:
        pieces.append((t, sc["t_out"], 1.0))
    out, f = [], 0
    for a, b, sp in pieces:
        n = max(1, int(round((b - a) / sp * FPS)))
        out.append({"a": a, "b": b, "sp": sp, "n": n, "f0": f})
        f += n
    hold = int(round(sc.get("hold", 0.0) * FPS))
    return out, f, hold


def s2o(segs, s):
    """Screen time -> output time (seconds from the scenario's start)."""
    for g in segs:
        if s <= g["b"]:
            u = max(0.0, s - g["a"]) / (g["b"] - g["a"])
            return (g["f0"] + u * g["n"]) / FPS
    g = segs[-1]
    return (g["f0"] + g["n"]) / FPS + (s - g["b"])


class Overlay:
    def __init__(self, sprite, x, y, o0, o1, fin=0.3, fout=0.3, dx=0, dy=0, hide_zoom=False, anchor="tl"):
        self.sprite, self.o0, self.o1, self.fin, self.fout = sprite, o0, o1, fin, fout
        self.dx, self.dy, self.hide_zoom = dx, dy, hide_zoom
        w, h = sprite.size
        if anchor == "tr":
            x -= w
        elif anchor == "bc":
            x, y = x - w / 2, y - h
        elif anchor == "tc":
            x -= w / 2
        self.x, self.y = x, y
        self.cache = {}

    def alpha(self, o):
        if o < self.o0 or o > self.o1:
            return 0.0, 0.0
        a_in = ease_out((o - self.o0) / self.fin) if self.fin else 1.0
        a_out = ease((self.o1 - o) / self.fout) if self.fout else 1.0
        return min(a_in, a_out), a_in

    def draw(self, cv, o, zf):
        a, p = self.alpha(o)
        if self.hide_zoom:
            a *= 1 - zf
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
        x = int(round(self.x + self.dx * (1 - p)))
        y = int(round(self.y + self.dy * (1 - p)))
        x, y = max(0, min(W - spr.width, x)), max(0, min(H - spr.height, y))
        cv.alpha_composite(spr, dest=(x, y))


def build_overlays(sc, segs, n_total):
    o_end = n_total / FPS
    ov, so = [], lambda s: s2o(segs, s)
    n = sc["n"]
    views = 2 if sc.get("camL") is None else 3
    intro_end = so(5.55) if sc.get("intro") else 0.0
    t_title = max(0.25, intro_end - 0.1)
    ov.append(Overlay(sprite_title(n, sc["title"], sc["sub"]), 40, 30, t_title, o_end, 0.5, 0.3, dy=14, hide_zoom=True))
    ov.append(Overlay(sprite_live(views), W / 2, 9, t_title, o_end, 0.5, 0.3, hide_zoom=True, anchor="tc"))
    if sc.get("camL"):
        ov.append(Overlay(sprite_tag("FRONT VIEW"), CAM_L[0] + 14, CAM_L[1] + 14, 0.2, o_end, 0.4, 0.3, hide_zoom=True))
    ov.append(Overlay(sprite_tag("TOP-DOWN VIEW"), CAM_R[0] + 14, CAM_R[1] + 14, 0.2, o_end, 0.4, 0.3, hide_zoom=True))
    # captions: shown a little past their last word for reading, never over the next one
    caps = sc["captions"]
    for i, (spk, a, b, s) in enumerate(caps):
        nxt = caps[i + 1][1] if i + 1 < len(caps) else sc["t_out"] + sc.get("hold", 0)
        o0, o1 = so(a) - 0.05, min(so(b) + 0.45, so(nxt) - 0.06, o_end)
        for fa, fb in sc["ff"]:          # nothing captioned through a fast-forward
            if a < fa < b + 0.45:
                o1 = min(o1, so(fa))
        ov.append(Overlay(sprite_caption(spk, s), W / 2, 1046, o0, o1, 0.12, 0.12, dy=6, anchor="bc"))
    # fast-forward badges
    for fa, fb in sc["ff"]:
        ov.append(Overlay(sprite_ff(fb - fa), W / 2, 964, so(fa) - 0.1, so(fb) + 0.35, 0.15, 0.25, dy=8, anchor="bc"))
    # chips: up to three per side, each in the first free row
    rows = {"L": [], "R": []}
    for side, kind, a, b, s in sorted(sc["chips"], key=lambda c: c[2]):
        o0, o1 = so(a), min(so(b), o_end)
        slot = 0
        while any(r == slot and not (o0 > e + 0.35) for r, e in rows[side]):
            slot += 1
        rows[side].append((slot, o1))
        y = 690 + slot * 56
        spr = sprite_chip(kind, s)
        if side == "L":
            ov.append(Overlay(spr, 28, y, o0, o1, 0.35, 0.3, dx=-24, hide_zoom=True))
        else:
            ov.append(Overlay(spr, W - 28, y, o0, o1, 0.35, 0.3, dx=24, hide_zoom=True, anchor="tr"))
    # minds: a card per request, its lines added as they happen
    reqs = sc["minds"]
    for i, r in enumerate(reqs):
        o0 = so(r["t"])
        o1 = so(reqs[i + 1]["t"]) - 0.05 if i + 1 < len(reqs) else o_end
        # the card grows by a row as each line arrives (the line itself fades in)
        times = [max(o0 + 0.15, so(t)) for t, _, _ in r["lines"]]
        n_l = len(r["lines"])
        for k in range(1, n_l + 1):
            a = o0 if k == 1 else times[k - 1]
            b = times[k] if k < n_l else o1
            ov.append(Overlay(sprite_mind_card(r["mind"], k), W - 28, 28, a, min(b, o1), 0.3 if k == 1 else 0.0,
                              0.25 if k == n_l else 0.0, dy=-10 if k == 1 else 0, anchor="tr"))
        for j, (t, label, value) in enumerate(r["lines"]):
            ov.append(Overlay(sprite_mind_line(label, value), W - 28 - CARD_W + 20, 28 + (12 + 40 + 8 + 31 * j) * 1,
                              times[j], o1, 0.25, 0.25, dx=10))
    # the steps card, when there is no second camera
    if sc.get("steps"):
        ov.append(Overlay(sprite_steps_frame(), CAM_L[0], CAM_L[1], 0.2, o_end, 0.4, 0.3, hide_zoom=True))
        y = CAM_L[1] + 62
        for t_on, t_ch, k1, s1, k2, s2 in sc["steps"]:
            spr1 = sprite_step(k1, s1)
            if t_ch is None:
                ov.append(Overlay(spr1, CAM_L[0] + 28, y, so(t_on), o_end, 0.35, 0.3, dx=-14, hide_zoom=True))
                y += spr1.height + 14
            else:
                spr2 = sprite_step(k2, s1, s2)
                ov.append(Overlay(spr1, CAM_L[0] + 28, y, so(t_on), so(t_ch), 0.35, 0.2, dx=-14, hide_zoom=True))
                ov.append(Overlay(spr2, CAM_L[0] + 28, y, so(t_ch) - 0.1, o_end, 0.25, 0.3, hide_zoom=True))
                y += spr2.height + 14
    if sc.get("intro"):
        ov.append(Overlay(sprite_intro(), 0, 0, 0.0, intro_end, 0.0, 0.45))
    return ov


def zoom_plan(sc, segs):
    """(output time, target window) changes; the camera eases from wherever it is to each target."""
    plan, rects = [(0.0, WIDE)], dict(RECTS)
    for target, a, b, fy, z in sc["zooms"]:
        o0, o1 = s2o(segs, a), s2o(segs, b)
        if plan and abs(plan[-1][0] - o0) < 0.12 and plan[-1][1] is WIDE:
            plan.pop()
        plan.append((o0, window_for(target, fy, z, rects)))
        plan.append((o1, WIDE))
    return plan


class Camera:
    def __init__(self, plan):
        self.plan, self.i = plan, 0
        self.win, self.start_win, self.t_change, self.target = WIDE, WIDE, 0.0, WIDE

    def at(self, o):
        while self.i < len(self.plan) and self.plan[self.i][0] <= o + 1e-6:
            t, tgt = self.plan[self.i]
            if tgt is not self.target:
                self.start_win, self.t_change, self.target = self.win, t, tgt
            self.i += 1
        u = ease((o - self.t_change) / ZOOM_S)
        self.win = lerp_win(self.start_win, self.target, u)
        cx, cy, w = self.win
        k = W / w
        return (cx - w / 2, cy - (w * H / W) / 2, k)


# --------------------------------------------------------------------------------------------- audio
def screen_audio(name):
    path = os.path.join(HERE, "aud_%s.wav" % name)
    if not os.path.exists(path):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", os.path.join(SRC, name + ".mp4"), "-vn", "-ac", "2", "-ar", str(SR),
                        "-c:a", "pcm_f32le", path], check=True)
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    return x


def tempo4(x):
    tmp_in, tmp_out = os.path.join(HERE, "_ff_in.wav"), os.path.join(HERE, "_ff_out.wav")
    sf.write(tmp_in, x, SR, subtype="FLOAT")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", tmp_in, "-filter:a", "atempo=2,atempo=2", tmp_out], check=True)
    y, _ = sf.read(tmp_out, dtype="float32", always_2d=True)
    return y


def fade(x, n_in, n_out):
    x = x.copy()
    if n_in:
        x[:n_in] *= np.linspace(0, 1, n_in, dtype=np.float32)[:, None]
    if n_out:
        x[-n_out:] *= np.linspace(1, 0, n_out, dtype=np.float32)[:, None]
    return x


def scenario_audio(sc, segs, hold):
    src = screen_audio(sc["screen"])
    parts, fcount = [], 0
    for g in segs:
        s0, s1 = int(round(fcount / FPS * SR)), int(round((fcount + g["n"]) / FPS * SR))
        need = s1 - s0
        a0, a1 = int(round(g["a"] * SR)), int(round(g["b"] * SR))
        piece = src[a0:a1]
        if g["sp"] != 1.0:
            piece = tempo4(piece) * 0.16
        if len(piece) < need:
            piece = np.concatenate([piece, np.zeros((need - len(piece), 2), np.float32)])
        piece = fade(piece[:need], int(0.008 * SR), int(0.008 * SR))
        parts.append(piece)
        fcount += g["n"]
    if hold:
        parts.append(np.zeros((int(round((fcount + hold) / FPS * SR)) - int(round(fcount / FPS * SR)), 2), np.float32))
    x = np.concatenate(parts)
    return fade(x, int(0.12 * SR), int(0.3 * SR))


# --------------------------------------------------------------------------------------------- render
def render_scenario(sc, write, statics, fade_in=True, fade_out=True):
    segs, n_main, hold = segments(sc)
    n_total = n_main + hold
    blurs = sc.get("blurs", {})
    srcs = {"phone": Source(sc["screen"], 0.0)}
    if sc.get("camL"):
        srcs["camL"] = Source(sc["camL"][0], sc["camL"][1], blurs.get("camL"))
    if sc.get("camR"):
        srcs["camR"] = Source(sc["camR"][0], sc["camR"][1], blurs.get("camR"))
    for s in srcs.values():
        s.start(sc["t_in"])
    ov = build_overlays(sc, segs, n_total)
    cam = Camera(zoom_plan(sc, segs))
    bg, masks, shadows = statics
    black = Image.new("RGB", (W, H), (0, 0, 0))
    f = 0
    t0 = time.time()
    for g in segs + [{"a": sc["t_out"], "b": sc["t_out"], "sp": 1.0, "n": hold, "f0": n_main}]:
        for j in range(g["n"]):
            s = g["a"] + (g["b"] - g["a"]) * j / max(1, g["n"]) if g["b"] > g["a"] else g["a"]
            o = f / FPS
            win = cam.at(o)
            k = win[2]
            zf = min(1.0, max(0.0, (k - 1.0) / 0.3))
            cv = bg.copy()
            if zf < 1:
                for name in srcs:
                    sh, pos = shadows[name]
                    if zf > 0:
                        r_, g_, b_, a_ = sh.split()
                        sh = Image.merge("RGBA", (r_, g_, b_, a_.point(lambda v: int(v * (1 - zf)))))
                    cv.alpha_composite(sh, dest=pos)
            for name in ("camL", "camR", "phone"):
                if name in srcs:
                    draw_panel(cv, srcs[name].get(s), RECTS[name], masks[name], win)
            for item in ov:
                item.draw(cv, o, zf)
            out = cv.convert("RGB")
            a = 1.0
            if fade_in:
                a = min(a, ease(o / FADE_S))
            if fade_out:
                a = min(a, ease((n_total / FPS - o) / FADE_S))
            if a < 0.999:
                out = Image.blend(black, out, a)
            write(out.tobytes())
            f += 1
            if f % 150 == 0:
                print("  scenario %d: %d / %d frames (%.1f fps)" % (sc["n"], f, n_total, f / (time.time() - t0)), flush=True)
    audio = scenario_audio(sc, segs, hold)
    return n_total, audio


def outro(write, seconds=3.6):
    img = sprite_outro()
    black = Image.new("RGB", (W, H), (0, 0, 0))
    n = int(round(seconds * FPS))
    for i in range(n):
        o = i / FPS
        a = min(ease(o / 0.45), ease((seconds - o) / 0.6))
        write((Image.blend(black, img, a) if a < 0.999 else img).tobytes())
    return n, np.zeros((int(round(n / FPS * SR)), 2), np.float32)


def loudnorm(inp, outp):
    chain = "highpass=f=80,afftdn=nf=-32"
    r = subprocess.run([FFMPEG, "-hide_banner", "-y", "-i", inp, "-af", chain + ",loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    af = (chain + ",loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s:"
          "offset=%s:linear=true" % (m["input_i"], m["input_tp"], m["input_lra"], m["input_thresh"], m["target_offset"]))
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", inp, "-af", af, "-ar", str(SR), outp], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", default="", help="write PNG stills at these output seconds instead of a video")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    chosen = edl.SCENARIOS if args.all else [s for s in edl.SCENARIOS if s["n"] == args.scenario]
    statics = (make_background(), {k: panel_mask(v) for k, v in RECTS.items()}, {k: panel_shadow(v) for k, v in RECTS.items()})

    if args.frames:
        want = [float(x) for x in args.frames.split(",")]
        frames = []
        idx = [int(round(t * FPS)) for t in want]
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
    write = enc.stdin.write
    audio, frames = [], 0
    t0 = time.time()
    for i, sc in enumerate(chosen):
        n, a = render_scenario(sc, write, statics)
        frames += n
        audio.append(a)
    if args.all:
        n, a = outro(write)
        frames += n
        audio.append(a)
    enc.stdin.close()
    enc.wait()
    x = np.concatenate(audio)
    raw, norm = out + ".raw.wav", out + ".norm.wav"
    sf.write(raw, x, SR, subtype="FLOAT")
    loudnorm(raw, norm)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", tmp_v, "-i", norm, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], check=True)
    for p in (tmp_v, raw, norm):
        os.remove(p)
    print("wrote %s: %d frames (%.1f s) in %.0f s" % (out, frames, frames / FPS, time.time() - t0))


if __name__ == "__main__":
    main()
