"""Word-timed transcripts of clips' audio with faster-whisper on the CPU (the GPU is busy with the live stack).

    python transcribe.py <out.json> <wav> [<wav> ...]
"""
import json
import os
import sys
import time

from faster_whisper import WhisperModel

out_path, wavs = sys.argv[1], sys.argv[2:]
model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", cpu_threads=6)
result = json.load(open(out_path, encoding="utf-8")) if os.path.exists(out_path) else {}
for wav in wavs:
    name = os.path.splitext(os.path.basename(wav))[0]
    if name in result:
        continue
    t = time.time()
    segments, info = model.transcribe(wav, language="en", word_timestamps=True, vad_filter=True,
                                      vad_parameters={"min_silence_duration_ms": 300}, beam_size=5,
                                      condition_on_previous_text=False)
    segs = []
    for s in segments:
        segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
                     "words": [{"w": w.word, "s": round(w.start, 2), "e": round(w.end, 2)} for w in (s.words or [])]})
    result[name] = {"duration": info.duration, "segments": segs}
    json.dump(result, open(out_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("%s: %d segments in %.0f s" % (name, len(segs), time.time() - t), flush=True)
