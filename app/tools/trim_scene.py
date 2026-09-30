"""A shorter cut of a recorded demo scene for the submission video: every silence longer
than 2 s is shortened to 1.2 s, nothing else is changed, and the clip says
"trimmed for time" in its corner. The uncut take stays the record.

    python app/tools/trim_scene.py results/galaxy/video/correction.mp4
      -> results/galaxy/video/correction_cut.mp4
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

FFMPEG = os.environ.get("FFMPEG", r"E:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe")
FONT = os.environ.get("CUT_FONT", "C\\:/Windows/Fonts/segoeui.ttf")
MIN_SILENCE = 2.0      # seconds of quiet before a gap is shortened
KEEP = 0.6             # seconds kept on each side of a shortened gap


def silences(path: str):
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", path, "-af",
                          "silencedetect=noise=-38dB:d=%s" % MIN_SILENCE, "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    # a clip that opens in silence reports its first start as slightly negative ("-0.003")
    starts = [max(0.0, float(x)) for x in re.findall(r"silence_start: (-?[\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", out)]
    h, m, sec = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return list(zip(starts, ends)), 3600 * int(h) + 60 * int(m) + float(sec)


def main() -> int:
    src = sys.argv[1]
    dst = re.sub(r"(_t\d+)?\.mp4$", "_cut.mp4", src)
    gaps, total = silences(src)
    keep, t = [], 0.0
    for s, e in gaps:
        if e - s <= 2 * KEEP:
            continue
        keep.append((t, s + KEEP))
        t = e - KEEP
    keep.append((t, total))
    parts, labels = [], []
    for i, (a, b) in enumerate(keep):
        parts.append("[0:v]trim=%.3f:%.3f,setpts=PTS-STARTPTS[v%d];[0:a]atrim=%.3f:%.3f,asetpts=PTS-STARTPTS[a%d]"
                     % (a, b, i, a, b, i))
        labels.append("[v%d][a%d]" % (i, i))
    graph = ";".join(parts) + ";" + "".join(labels) + "concat=n=%d:v=1:a=1[vc][ac];" % len(keep) + \
        "[vc]drawtext=fontfile='%s':text='trimmed for time':x=w-tw-36:y=h-th-28:fontsize=26:" \
        "fontcolor=white@0.55[vo]" % FONT
    res = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", src, "-filter_complex", graph,
                          "-map", "[vo]", "-map", "[ac]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", dst],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-2000:])
        return 1
    cut = sum(b - a for a, b in keep)
    print("%s: %.1f s -> %.1f s (%d silences shortened) -> %s" % (os.path.basename(src), total, cut, len(keep) - 1, dst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
