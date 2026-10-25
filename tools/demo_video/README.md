# The demo film's editing scripts

How [`docs/round2/deck/duet_demo.mp4`](../../docs/round2/deck/duet_demo.mp4) was cut. Every frame is composited in
Python (PIL, PyAV, numpy, from DUET's own environment), and ffmpeg encodes the result.

| Script | What it does |
|---|---|
| `transcribe.py` | Word-timed transcripts of each clip's audio (faster-whisper on the CPU). |
| `sync.py` | Each camera's offset against the S24 screen recording, by cross-correlating their audio. |
| `trace_turns.py`, `trace_events.py` | DUET's conversation traces as turns and timed events: which mind answered, and the latency of each step. |
| `speech_edges.py` | Where sound really starts and stops, so cuts and fast-forwards leave natural pauses. |
| `edl2.py` | The edit: in and out points, fast-forwards, captions, chips, and the minds with their latencies. |
| `render2.py` | Renders the film: the white layout, speed-ramped fast-forwards, rounded overlays, and captions snapped to the sound. |
| `verify2.py` | Checks the finished film: every caption against its audio (Whisper and the waveform), and black frames. |
| `edl.py`, `render.py`, `verify_sync.py`, `qa_frames.py` | The first cut, with the dark look and zooms. `render2.py` reuses its helpers. |

## Folder layout

The scripts expect this layout, which is not in the repo:
- `../src/clip_HHMMSS.mp4`: the raw clips.
- `audio/`: 16 kHz copies of the clips' audio.
- `fonts/`: Segoe UI, copied from Windows.

They also call ffmpeg at the path set at the top of `render.py`.

To render:

```bash
python render2.py --all --out ../out/v2/DUET_live_demo_v2.mp4
python verify2.py ../out/v2/DUET_live_demo_v2.mp4
```
