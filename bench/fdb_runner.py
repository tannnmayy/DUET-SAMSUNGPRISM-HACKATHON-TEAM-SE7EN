#!/usr/bin/env python3
"""Thin driver around the benchmark's own inference functions.

    python bench/fdb_runner.py --provider duet --root_dir <data> [--only a,b] [--scoring-asr whisper]

It calls `run_tool_benchmark.process_single` for each input exactly as
`run_tool_benchmark_all_released.py` does, adding two things the official
script lacks: `--only` (run a subset) and `--scoring-asr whisper` (score with
faster-whisper where NVIDIA NeMo/Parakeet cannot be installed, e.g. Windows).
With `--scoring-asr parakeet` (the default on Linux) scoring is byte-for-byte
the official path.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

FDB_DIR = Path(os.environ.get("FDB_V3_DIR", "")).resolve()
sys.path.insert(0, str(FDB_DIR))


def whisper_asr():
    """Stand-in for Parakeet with the same output shape: text + word timestamps."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from duet_voice import speech_models
    speech_models._cuda_dll_paths()
    from faster_whisper import WhisperModel
    import ctranslate2
    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    model = WhisperModel("large-v3-turbo", device=device, compute_type="float16" if device == "cuda" else "int8")

    def run_asr(_model, audio_path):
        try:
            segments, _ = model.transcribe(str(audio_path), language="en", word_timestamps=True,
                                           vad_filter=True, condition_on_previous_text=False)
            chunks, text = [], []
            for seg in segments:
                for w in seg.words or []:
                    word = w.word.strip()
                    if word:
                        chunks.append({"text": word, "timestamp": [round(w.start, 2), round(w.end, 2)]})
                        text.append(word)
            return {"text": " ".join(text), "chunks": chunks}
        except Exception as exc:
            return {"text": "", "chunks": [], "error": str(exc)}

    return model, run_asr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="duet")
    ap.add_argument("--root_dir", required=True)
    ap.add_argument("--only", default=os.environ.get("DUET_ONLY", ""))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--scoring-asr", choices=["parakeet", "whisper"], default="parakeet")
    args = ap.parse_args()

    import run_tool_benchmark as rtb
    import run_tool_benchmark_all_released as rel

    data = rel.load_data_released()
    inputs = rel.discover_inputs_released(args.root_dir)
    data = {**data, **rel._PER_FOLDER_DATA}
    if args.only:
        wanted = set(args.only.split(","))
        inputs = [i for i in inputs if i[1] in wanted or i[2].parent.name in wanted]
    print("inputs:", len(inputs), "| scoring ASR:", args.scoring_asr, flush=True)

    if args.scoring_asr == "whisper":
        asr_model, run_asr = whisper_asr()
        rtb.run_asr = run_asr
    else:
        asr_model = rtb.load_asr_model()

    t0 = time.time()
    done = 0
    for speaker_id, example_id, input_path in inputs:
        print("\n[%d/%d] %s" % (done + 1, len(inputs), input_path.parent.name), flush=True)
        rtb.process_single(speaker_id, example_id, input_path, args.provider, data, asr_model,
                           asr_only=False, force=args.force)
        done += 1
    print("\nfinished %d items in %.1f min" % (done, (time.time() - t0) / 60), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
