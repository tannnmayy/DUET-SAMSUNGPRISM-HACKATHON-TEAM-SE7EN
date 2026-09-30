# DUET: making voice agents safe to interrupt

**Samsung PRISM GenAI Hackathon 3.0 · Theme 05: Interruptible Real-Time Agents · Team SE7EN, SRM (`SRM_SE7EN`)**

> Full-duplex *speech* is solved. Full-duplex *action* is not. An assistant can
> already hear you talk over it - but if you change your mind while it is
> booking, searching or filing, the work already in flight is what breaks.
> DUET is the coordination layer that makes that safe.

DUET (*Duplex Utterance-Epoch Transaction* runtime) sits between the speech
layer and the action layer of a voice agent and guarantees four things together:

| Property | Guarantee |
|---|---|
| **Never silent** | A truthful spoken response within tens of milliseconds of every turn or interruption, however slow the real work is. |
| **Always interruptible** | Any in-flight work can be invalidated mid-execution; its results are never spoken and never committed. |
| **Never double-commits** | No irreversible action is executed twice - not under retries, cancellations or re-plans. A timed-out booking is never blindly resent. |
| **Honest about uncertainty** | When perception is unsure it asks, and names what it is unsure of ("Sorry, did you say Austin?"). |

The kit's organizer instructions are in [KIT_README.md](KIT_README.md) and
[WALKTHROUGH.md](WALKTHROUGH.md); the full engineering record is
[PROJECT.md](PROJECT.md), with evidence for every non-obvious decision in
[notes/FINDINGS.md](notes/FINDINGS.md).

---

## Results

All numbers below are produced by commands in this repository, at the
official `--time-scale 1`.

| Measurement | Result | Command |
|---|---|---|
| Official evaluator, public set | **97.4 weighted** (97.9 plain; text 100, audio 100, visual 81.5) | `python eval_submission.py . --time-scale 1` |
| Our conformance suite (23 scenarios the kit does not cover) | 22 at 100; visual-without-hint at 81.5 | `python tools/quality.py` |
| Kit templates on unseen seeds (2026, 7331) | 120/120 at 100.0; 0 crashes, 0 protocol errors | `python tools/chaos.py --n 60 --seed <new>` |
| All 8 templates - the kit's 3 and our 5 - on never-used seeds | seed 202: **99.7** (119/120; the miss is fixed) · seed 303: **120/120 at 100.0** | `python tools/chaos.py --templates all --n 120 --seed <new>` |
| Every model disabled | 89.1 average, 0 crashes, 0 silent scenarios | `python tools/killswitch.py` |
| Transcript quality lint (32 scenarios; speech and failed irreversible calls) | 0 flagged | `python tools/quality.py` |
| Dispatcher, slowest event handler | 0.47 ms (budget 20 ms) | `python tools/bench.py` |
| Test suite | 373 passing | `python -m pytest tests/ -q` |

The kit's reference agent scores about 57 on the same public set, and 0 on
both audio scenarios.

The one checkpoint we do not pass is naming the port in pub_07's photo.
We tested a vision-language model for it and **it was wrong more often than
right** (see [Honest limits](#honest-limits)), so the agent says it cannot
tell and asks, rather than guessing.

---

## Architecture

```
  in_queue --> DISPATCHER (duet/runtime.py)   synchronous, never awaits slow work, < 20 ms per event
                  |
     +------------+----------------+-------------------+
     v            v                v                   v
  FAST PATH   COORDINATOR       SLOW PATH            STATE
  fastpath.py coordinator.py    perception/*         state.py
  nlg.py      - epochs     M1   - speech (Whisper)   - slots + provenance  M3
  - repairs   - call registry   - frames (CLIP)      - epoch history       M1
  - extraction- idempotency M2  - (vision model,     - undo                M6
  - ack+echo  - commit gate M2    off by default)
     |            |                |                   |
     +------------+----------------+-------------------+--> EMITTER (emitter.py) --> out_queue
```

**Two invariants govern all of it, and both are enforced by tests:**

1. **The dispatcher never blocks.** No event handler may occupy the shared
   event loop for more than 20 ms; anything slower is a task. The harness
   runs our agent on its own event loop, so one blocking call would delay
   event delivery and make the scorer blame us for things we did not do.
2. **Every outbound action passes through one function.** `put_nowait`
   appears exactly once in `duet/`. That single exit attaches the state
   snapshot, validates the payload, refuses verbatim repeats, and refuses any
   sentence that claims an irreversible action completed before its tool
   said so.

### The six mechanisms

| # | Mechanism | What it does | Where |
|---|---|---|---|
| M1 | **Epoch-versioned state** | Every computation carries the epoch it was conceived under. An interruption bumps the epoch, orphaning all descendants - tool calls, transcriptions, plans - in one step. | `state.py`, `coordinator.py` |
| M2 | **Reversibility-gated commitment** | Read-only work fires freely; state-modifying work passes a gate (turn ended, confident slots, a 250 ms quiet window after a correction, and an idempotency ledger keyed exactly like the scorer's duplicate check). | `coordinator.py` |
| M3 | **Slot provenance** | A slot records the utterance, span, time, modality and confidence that produced it, so a correction rewrites one slot and leaves the rest. | `state.py` |
| M4 | **Perception-ahead** | A camera frame is read the moment it arrives, before anyone asks about it; a question about the frame waits (bounded) for that reading while the acknowledgment is already spoken. | `runtime.py` |
| M5 | **Calibrated abstention** | Speech confidence is judged per slot word, not per sentence. A value heard but not trusted is confirmed by name; nothing heard is asked about openly. | `perception/asr.py`, `runtime.py` |
| M6 | **Conversational undo** | "Go back to what I said" restores an epoch checkpoint instead of re-conversing. | `state.py` |

### Schema-driven tools

About ten hidden tools are delivered only as a schema at the start of a
scenario, so nothing in `duet/` knows any tool by name (enforced by a grep
test). Tools are chosen by sparse retrieval over their own schemas, arguments
bind by *role* (a `city`, a `destination` and a `pickup_city` all receive a
place), and chaining falls out of satisfiability: `book_flight` needs a
`flight_id` that only exists after `flight_search` returns, so the search
ranks first and the booking follows.

---

## Setup

Python 3.10-3.12. Every graded dependency is pinned in `requirements.txt`,
mirrored exactly in `submission.yaml`.

```bash
pip install -r requirements.txt
python tools/prefetch.py            # optional: download every pinned model now
python eval_submission.py . --time-scale 1
```

**GPU.** On Linux, `torch==2.10.0` from PyPI is a CUDA 12.8 build and brings
the cuBLAS 12 / cuDNN 9 libraries the speech engine (CTranslate2) needs;
`duet/perception/cuda.py` makes them visible to it. Only an NVIDIA driver is
required - no system CUDA toolkit. On Windows, install the CUDA build of the
same torch version first:

```bash
pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128
```

`setup()` runs a trial transcription on the GPU and falls back to CPU if it
fails, so a broken GPU stack costs accuracy, not the scenario.

**Models** (all pinned by repository and commit in `duet/perception/checkpoints.py`):

| Role | Model | Where it runs |
|---|---|---|
| Speech | `dropbox-dash/faster-whisper-large-v3-turbo` | GPU (CTranslate2 float16) |
| Speech fallback | `Systran/faster-whisper-base` | CPU (int8) |
| Frame embedding | `sentence-transformers/clip-ViT-B-32` | GPU or CPU |
| Vision-language (off by default) | `Qwen/Qwen2.5-VL-3B-Instruct` | GPU; enable with `DUET_VISION=1` |

No network dependency at scoring time beyond downloading these checkpoints,
no API keys, no hosted models.

### Docker

```bash
docker build -t duet .
docker run --gpus all duet                       # runs the official evaluator
```

The image pre-downloads every pinned model, so cold start does not depend on
the network.

### Presentation and demo

- Deck of this retired v1 (not the submission deck): [`DUET_v1_kit_deck.pptx`](DUET_v1_kit_deck.pptx) /
  [`DUET_v1_kit_deck.pdf`](DUET_v1_kit_deck.pdf), generated by `tools/deck/build_deck.js`
  (every number in one table at the top).
- Demo video script: [`notes/VIDEO_SCRIPT.md`](notes/VIDEO_SCRIPT.md).
- Timelines for any run: `python tools/timeline.py <scenario.json>` writes a self-contained
  HTML page - tool calls as bars, cancelled calls drawn cut at the cancel, epochs marked,
  changed slots highlighted.

### Useful commands

```bash
python run_local.py --scenario scenarios/pub_02_text_interrupt.json --agent agent.agent:ParticipantAgent
python tools/quality.py --show            # every transcript, linted
python tools/chaos.py --templates all --n 100 --seed <new>
python tools/killswitch.py                # everything off: does it degrade?
python tools/vision_bench.py --variants   # measure a vision model before trusting it
```

Environment switches: `DUET_NO_ASR`, `DUET_NO_VISION`, `DUET_NO_EMBED` force
the degradation ladder; `DUET_ASR_DEVICE=cpu|cuda` forces a speech device;
`DUET_DEBUG=1` mirrors the internal log to stderr.

---

## Generalisation

The nine public scenarios are worth nothing on their own - they are what we
developed against. What matters is behaviour on scenarios nobody wrote by
hand, so we measure three ways:

1. **Our conformance suite** (`tests/conformance/`, generated by
   `tools/make_conformance.py`): 23 scenarios for what the kit leaves
   untested - double and triple interruptions, retraction and topic change,
   interruption during a booking, a correction inside the commitment hold,
   an interruption 40 ms before a stale result returns, empty results, a
   booking timeout, ten paraphrase shapes, number words, and an unseen
   state-modifying tool. Each interruption scenario is **proven** to fail an
   agent that does not cancel (`tests/test_conformance_discriminates.py`).
2. **Chaos runs** on fresh seeds, from the kit's templates and from ours
   (`tools/scenario_templates.py`), which draw cities, names, phrasings and
   timings from pools that appear nowhere in the engine. The kit's templates
   alone had shown 100.0 for days. Our first run of our own templates
   (seed 101) scored **88.1, with 23 of 100 below 75**. That exposed six general
   gaps: time-of-day selectors, "for Leeds" read as a person, an incidental
   word in an argument description outranking the right tool, "scratch the
   trip" not read as a topic change, a sentence's first capital read as a
   name, and argument roles chosen before extraction could fill them. After
   fixing those, a seed never used before (202) scored **99.7**. Its one miss
   ("The Olive Room" shortened to "Olive Room") is fixed and tested too, and
   the next fresh seed (303) scored **120/120 at 100.0**.
3. **Transcript lint** (`tools/quality.py`), because the automated score cannot
   hear the agent: repeats, tool names read aloud, answers stacked on each
   other, cut-off clauses. The first read-through found a garbled answer in
   6 of 17 scenarios that all scored 100.

---

## Degradation ladder

The agent never crashes and never goes silent. `tools/killswitch.py` proves it
with every model disabled (89.1 average).

| If this fails | DUET does this |
|---|---|
| GPU speech stack | CPU speech model, after a trial transcription catches it in `setup()` |
| Speech model entirely | Acknowledges and asks the user to repeat |
| Low speech confidence | Confirms the heard value by name - correct behaviour, not a fallback |
| Frame embedding model | Searches by text alone |
| A referenced media file | Treated as unintelligible; asks |
| Any exception in a handler | Caught at the dispatcher; the fast path still speaks |

---

## Honest limits

- **Naming what is in a photo.** Qwen2.5-VL-3B named pub_07's HDMI port
  "USB-C" on 3 of 4 image variants under three prompts, and invented a
  printed label to match. A wrong reading retrieves the wrong manual page
  and fails two checkpoints instead of one, so vision-language reading ships
  **off**; the agent says it cannot tell which port it is, names the candidate
  pages, and asks. A larger model may clear the bar on a big GPU;
  `tools/vision_bench.py` is how to find out before trusting it.
- **CLIP re-ranking** of candidate pages was tried and rejected for the same
  reason: it never ranked HDMI first (F13).
- **A self-repair whose trigger word is lost in noise** ("actually" heard as
  something else) can leave the abandoned value in place (F21). The obvious
  fix - "last place mentioned wins" - breaks legitimate two-place sentences,
  so it is documented rather than papered over.

---

## Repository map

```
agent/agent.py        entry point (ParticipantAgent) - a thin shell over duet/
duet/                 the engine: runtime, coordinator, state, tools, fast path, NLG, emitter
duet/perception/      speech, frame embedding, vision; checkpoints and CUDA setup
duet/planner/rules.py deterministic planner (chaining by satisfiability)
tests/                373 tests: invariants, contract equivalence, conformance discrimination,
                      quality lint, packaging (pins, WAV, same-process repetition)
tests/conformance/    our 23 scenarios
tools/                chaos (+ our templates), quality lint, timeline, killswitch, bench, clock check,
                      vision bench, prefetch, conformance generator, deck generator
notes/                problem analysis, build plan, findings F1-F24, organizer clarifications, video script
harness/ scenarios/ docs/ run_local.py eval_submission.py   the organizers' kit, unchanged
```
