#!/usr/bin/env python3
"""Offline perception pass: what the agent's ears hear on every FDB-v3 input.

    python bench/offline_asr.py                      # all 100, default ASR model
    python bench/offline_asr.py --model large-v3 --limit 10

For each input it runs the same Silero VAD segmentation and per-segment
faster-whisper transcription (with the non-speech filter) the live agent uses,
and writes results/offline/asr_<model>.json:
  segments   start/end/text/confidence/dropped, in input time
  gaps       pauses between kept segments (turn-taking risk)
  recall     share of expected argument values that appear in the transcript,
             a rough proxy for "could the thinker possibly get this right"

The expected arguments are read only to *score* perception, never to change it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.common import DATA_DIR, REPO, discover, read_wav, resample  # noqa: E402


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


NUMS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10}


def value_in_text(value, text: str) -> bool:
    t = _norm(text)
    if isinstance(value, bool):
        return True  # booleans are inferred, not spoken
    if isinstance(value, (int, float)):
        v = int(value) if float(value).is_integer() else value
        cands = {str(v), "{:,}".format(v).replace(",", "")}
        if isinstance(v, int) and v >= 1000 and v % 1000 == 0:
            cands.add("%dthousand" % (v // 1000))
        if isinstance(v, int) and v in NUMS.values():
            cands.update(k for k, n in NUMS.items() if n == v)
        return any(c in t for c in cands)
    s = str(value)
    if s.startswith("$"):
        return True  # a reference to an earlier result
    return _norm(s) in t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("DUET_ASR_MODEL", "large-v3-turbo"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--min-silence-ms", type=int, default=350)
    args = ap.parse_args()
    os.environ["DUET_ASR_MODEL"] = args.model
    # without a GPU the agent swaps to a small model for speed; here the transcripts
    # must come from the model the output file is named after
    os.environ["DUET_ASR_CPU_MODEL"] = args.model

    from faster_whisper.vad import VadOptions, get_speech_timestamps
    from duet_voice import speech_models

    items = discover()
    if args.limit:
        items = items[: args.limit]
    out_dir = REPO / "results" / "offline"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    t0 = time.time()
    for example_id, speaker, folder, meta in items:
        x, sr = read_wav(folder / "input.wav")
        pcm = resample(x, sr)
        stamps = get_speech_timestamps(pcm, VadOptions(threshold=args.threshold,
                                                       min_silence_duration_ms=args.min_silence_ms,
                                                       speech_pad_ms=120))
        segments = []
        for st in stamps:
            chunk = pcm[st["start"]:st["end"]]
            tr = speech_models.transcribe(chunk)
            segments.append({"start": round(st["start"] / 16000, 2), "end": round(st["end"] / 16000, 2),
                             "text": tr.text, "confidence": round(tr.confidence, 3),
                             "no_speech": round(tr.no_speech_prob, 3), "dropped": tr.dropped})
        kept = [s for s in segments if s["text"]]
        text = " ".join(s["text"] for s in kept)
        gaps = [round(b["start"] - a["end"], 2) for a, b in zip(kept, kept[1:])]
        expected = [(c["function"], k, v) for c in meta["expected_tool_calls"] for k, v in c.get("args", {}).items()]
        hits = [value_in_text(v, text) for _, _, v in expected]
        results.append({"example_id": example_id, "speaker": speaker, "folder": folder.name,
                        "difficulty": meta["difficulty"], "disfluency": meta.get("disfluency_features", []),
                        "segments": segments, "transcript": text, "gaps": gaps,
                        "recall": round(sum(hits) / len(hits), 3) if hits else 1.0,
                        "missed": [f"{f}.{k}={v}" for (f, k, v), h in zip(expected, hits) if not h]})
        print("%-44s rec=%.2f segs=%d maxgap=%.1f | %s" % (
            folder.name[:44], results[-1]["recall"], len(kept), max(gaps) if gaps else 0, text[:110]), flush=True)
    path = out_dir / ("asr_%s.json" % args.model.replace("/", "_"))
    path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    rec = [r["recall"] for r in results]
    print("\n%d items in %.0f s | mean value recall %.3f | items with every value heard: %d" % (
        len(results), time.time() - t0, sum(rec) / len(rec), sum(1 for r in rec if r == 1.0)))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
