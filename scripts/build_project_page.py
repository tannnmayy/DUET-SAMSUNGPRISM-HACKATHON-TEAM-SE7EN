"""Build docs/DUET_Project.html: the project page, one self-contained file (images and the clip embedded).

    python scripts/build_project_page.py

Reads docs/project/duet_project.template.html and fills:
  {{img:name}}    docs/project/media/<name>.jpg or .png, as a data URI
  {{video:name}}  docs/project/media/<name>.mp4, as a data URI
  {{num:key}}     numbers from the latest runs (results/verify, results/galaxy/suite, the phone runs)
  {{date}}        today
The page opens straight from the repository, or can be published as it is.
"""

from __future__ import annotations

import base64
import glob
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "docs", "project", "duet_project.template.html")
MEDIA = os.path.join(ROOT, "docs", "project", "media")
OUT = os.path.join(ROOT, "docs", "DUET_Project.html")


def data_uri(path: str, mime: str) -> str:
    with open(path, "rb") as fh:
        return "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode("ascii"))


def latest(pattern: str):
    files = sorted(glob.glob(pattern), key=os.path.getmtime)
    return files[-1] if files else None


def numbers() -> dict:
    n = {"unit": "—", "suite": "—", "e2e": "—", "resilience": "—", "ack": "—", "action": "—", "answer": "—"}
    v = latest(os.path.join(ROOT, "results", "verify", "verify_*.json"))
    if v:
        rep = json.load(open(v, encoding="utf-8"))
        for st in rep.get("steps", []):
            if st["step"] == "unit tests":
                m = re.match(r"(\d+) passed", st["detail"])
                n["unit"] = m.group(1) if m else n["unit"]
            if st["step"] == "spoken suite":
                m = re.match(r"(\d+/\d+) runs", st["detail"])
                n["suite"] = (m.group(1) + " runs") if m else n["suite"]
            if st["step"] == "phone: resilience behind Maps":
                n["resilience"] = st["detail"]
        e2e = [st for st in rep.get("steps", []) if st["step"].startswith("phone: end to end")]
        if e2e:
            done = total = 0
            for st in e2e:
                m = re.match(r"(\d+) of (\d+)", st["detail"])
                if m:
                    done, total = done + int(m.group(1)), total + int(m.group(2))
            n["e2e"] = "%d of %d cases" % (done, total)
        lat = rep.get("latency_s", {})
        for k in ("ack", "action", "answer"):
            if lat.get(k, {}).get("p50") is not None:
                n[k] = "%.2f s" % lat[k]["p50"]
    return n


def main() -> int:
    html = open(TEMPLATE, encoding="utf-8").read()

    def img(m):
        for ext, mime in ((".jpg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp")):
            path = os.path.join(MEDIA, m.group(1) + ext)
            if os.path.exists(path):
                return data_uri(path, mime)
        print("missing image:", m.group(1))
        return ""

    def video(m):
        path = os.path.join(MEDIA, m.group(1) + ".mp4")
        if os.path.exists(path):
            return data_uri(path, "video/mp4")
        print("missing video:", m.group(1))
        return ""
    nums = numbers()
    html = re.sub(r"\{\{img:(\w+)\}\}", img, html)
    html = re.sub(r"\{\{video:(\w+)\}\}", video, html)
    html = re.sub(r"\{\{num:(\w+)\}\}", lambda m: str(nums.get(m.group(1), "—")), html)
    html = html.replace("{{date}}", time.strftime("%d %B %Y"))
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    print("wrote %s (%.1f MB) · numbers %s" % (os.path.relpath(OUT, ROOT), os.path.getsize(OUT) / 1e6, nums))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
