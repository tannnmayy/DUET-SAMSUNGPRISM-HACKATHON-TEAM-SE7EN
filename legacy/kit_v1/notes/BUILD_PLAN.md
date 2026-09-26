# DUET — Build Plan
### Samsung PRISM GenAI Hackathon 3.0 · Theme 05: Interruptible Real-Time Agents

> **DUET** — *Duplex Utterance-Epoch Transaction runtime.*
> A conversation is a duet, not a pair of monologues. The name is a placeholder we can
> change, but every module and the deck assume it from here.

| | |
|---|---|
| **Plan date** | 19 September 2026 (Day 1 of 7) |
| **Submission deadline** | 25 September 2026, 11:59 PM |
| **Demo round** | 15 October 2026 |
| **Model policy** | Local-only. No network dependency at scoring time. |
| **Scope** | Graded agent + real microphone application + judge sandbox |
| **Companion doc** | `THEME5_MASTER_ANALYSIS.md` (problem, scorer mechanics, constraints) |

---

## 0. What we are building, stated once and precisely

**DUET is a runtime that makes voice agents safe to interrupt.**

It sits between a speech layer (ears and mouth) and an action layer (tools, device control,
APIs). Its job is to guarantee four properties that no shipping assistant currently guarantees
together:

| Property | Guarantee |
|---|---|
| **Never silent** | A truthful, content-aware spoken response within 800 ms of every user turn or interruption, regardless of how slow the real work is. |
| **Always interruptible** | Any in-flight work can be invalidated mid-execution. Results of invalidated work are never spoken and never committed. |
| **Never double-commits** | No irreversible action is ever executed twice, even under retries, cancellations and re-plans. |
| **Honest about uncertainty** | When perception is unreliable, it asks instead of guessing — and says what it is unsure about. |

Samsung's harness measures exactly these four properties. That is not a coincidence we are
exploiting; it is the reason we build them properly rather than fitting to the tests.

### 0.1 What makes this *not* "just another full-duplex agent"

Full-duplex **speech** is a solved product problem — GPT-Live shipped it in July 2026, and
open benchmarks (Full-Duplex-Bench v1.5) already measure overlap handling. What is *not*
solved, and what the 2026 literature is only now benchmarking, is full-duplex **action**:
what happens to work already in flight when the user changes their mind.

DUET's contribution is five named mechanisms, each addressing a documented failure mode:

| # | Mechanism | What it is | Failure mode it kills | Literature anchor |
|---|---|---|---|---|
| **M1** | **Epoch-Versioned State** (Conversational MVCC) | Every computation carries the epoch it was conceived under. An interruption bumps the epoch, atomically orphaning every descendant — tool calls, ASR jobs, VLM jobs, LLM plans. | Acting on results computed under a superseded intent. | EchoChain (state-update under interruption) |
| **M2** | **Reversibility-Gated Speculation** (the Commitment Ladder) | Speculation depth is a function of *action reversibility*, not confidence alone. Read-only work fires on partial turns; irreversible work waits behind a commitment gate. | Speculative execution causing double-bookings. | Act While Thinking; Speculative Tool Calling for Voice |
| **M3** | **Slot Provenance & Surgical Repair** | Every slot records the utterance span, timestamp and confidence that produced it, so a correction rewrites *one* slot instead of rebuilding the plan. | Self-repairs ("Boston — no, New York") corrupting unrelated state. | Full-Duplex-Bench v3 (tool use under disfluency) |
| **M4** | **Perception-Ahead Scheduling** | Perception begins when a *modality* arrives, not when a *question* arrives. A camera frame is analysed the instant it lands, before anyone asks about it. | Multimodal latency stacking on top of reasoning latency. | RelayS2S (dual-path speculative generation) |
| **M5** | **Calibrated Abstention** | ASR/VLM posteriors drive an explicit clarify-vs-act decision, with the uncertainty *named* out loud ("did you say Austin or Boston?"). | Confidently acting on a misheard slot. | Theme accessibility use case; STT benchmark literature |

And one product feature that falls out of M1 for free, which no shipping assistant has:

> **M6 — Conversational Undo.** Because state is epoch-versioned, "wait, go back to what I
> said before" is a state restore, not a re-conversation. Retractions ("never mind") are the
> same primitive. This is ~30 lines once M1 exists, and it demos in eight seconds.

**The deck thesis:** *"Full-duplex speech is solved. Full-duplex action is not. Bixby already
controls the device — it just cannot survive you changing your mind while it's acting. We built
the layer that makes that safe."*

### 0.2 What we are explicitly NOT building

Scope discipline is how we finish. We are not building:

- a speech-to-speech end-to-end model (out of scope, and we would lose the controllability)
- wake-word detection (explicitly out of scope per the theme guide)
- TTS voice tuning (explicitly out of scope; we use an off-the-shelf local voice)
- a general LLM agent framework (LangChain-style abstraction); DUET is purpose-built
- any fine-tuning or training of any model, at any point this week
- a mobile app, a Bixby plugin, or anything requiring Samsung device APIs

---

## 1. Architecture

### 1.1 The layered picture

```
╔══════════════════════════════════════════════════════════════════════════╗
║  ADAPTERS  (interchangeable; the core never knows which one is attached)  ║
║                                                                          ║
║   A: harness         B: live mic              C: sandbox                  ║
║   scenario.json      mic→VAD→ASR              browser (type / speak)      ║
║   → in_queue         → in_queue               → in_queue                  ║
║   trace ← out_queue  speaker←TTS← out_queue   timeline ← out_queue        ║
╚══════════════════════════════════════════════════════════════════════════╝
                                  │  in_queue / out_queue  (the ONLY interface)
                                  ▼
╔══════════════════════════════════════════════════════════════════════════╗
║  DISPATCHER  —  duet/runtime.py                                          ║
║  Synchronous. Never awaits slow work. Hard budget: < 20 ms per event.    ║
║  Classify → update state → speak → spawn. Nothing else.                  ║
╚══════════════════════════════════════════════════════════════════════════╝
      │                        │                           │
      ▼                        ▼                           ▼
┌───────────────┐   ┌────────────────────────┐   ┌──────────────────────┐
│ FAST PATH     │   │ COORDINATOR            │   │ SLOW PATH            │
│ duet/fastpath │   │ duet/coordinator.py    │   │ (asyncio tasks)      │
│ duet/nlg.py   │   │                        │   │                      │
│               │   │ • epoch counter   (M1) │   │ perception/asr.py    │
│ • repair      │   │ • call registry        │   │ perception/vision.py │
│   extraction  │   │ • idempotency ledger   │   │ perception/embed.py  │
│ • ack + echo  │   │ • commitment gate (M2) │   │ planner/llm.py       │
│ • clarify     │   │ • cancellation         │   │ tools.py (arg build) │
│ • snapshot    │   │ • undo stack      (M6) │   │                      │
│  ~5–15 ms     │   │  ~1 ms                 │   │  150–2000 ms         │
└───────────────┘   └────────────────────────┘   └──────────────────────┘
      │                        │                           │
      └────────────────────────┴───────────────────────────┴──► out_queue
                                  │
                        ┌─────────▼──────────┐
                        │ STATE   duet/state │
                        │ slots + provenance │
                        │ epoch history (M3) │
                        └────────────────────┘
```

### 1.2 The two invariants that govern all code

> **INVARIANT 1 — The dispatcher never blocks.**
> No `await` inside `run()`'s event-handling path may exceed 20 ms. Slow work is
> `asyncio.create_task`-ed and writes to `out_queue` when it finishes.
> *Enforcement:* a watchdog wrapper times every handler and logs a violation; the conformance
> suite fails the build if p99 > 20 ms.

> **INVARIANT 2 — Every outbound action passes through `emit()`.**
> One function constructs every action. It attaches `state_snapshot`, validates against
> `protocol.validate_action()` before sending, enforces the filler budget, blocks verbatim
> repeats, and refuses premature completion claims.
> *Enforcement:* `out_q.put` appears exactly once in the codebase. Grep test in CI.

Every category the scorer measures maps to one of these two invariants plus the coordinator.
Get these right and the score is mostly determined.

### 1.3 Repository layout

```
HACKATHONPRISM/                         (repo root = submission root)
│
├── submission.yaml                     ← entry point + requirements + env
├── README.md                           ← reproducible setup, architecture, results
├── Dockerfile                          ← required by FAQ Q17
├── requirements.txt                    ← graded agent deps ONLY
├── requirements-app.txt                ← mic/TTS/UI deps (NOT in submission.yaml)
│
├── agent/
│   ├── __init__.py
│   └── agent.py                        ← ParticipantAgent: ~40-line shell → duet.runtime
│
├── duet/                               ← THE ENGINE (all our work)
│   ├── __init__.py
│   ├── runtime.py                      ← dispatcher, event loop, watchdog
│   ├── coordinator.py                  ← M1 epoch, call registry, idempotency, M2 gate
│   ├── state.py                        ← slots, provenance (M3), snapshots, undo (M6)
│   ├── tools.py                        ← manifest parsing, schema→args compiler
│   ├── fastpath.py                     ← rule-based intent/repair extraction
│   ├── nlg.py                          ← truthful, varied, non-repeating utterances
│   ├── config.py                       ← thresholds, budgets, feature flags
│   ├── perception/
│   │   ├── __init__.py
│   │   ├── asr.py                      ← faster-whisper + confidence (M5)
│   │   ├── vision.py                   ← local VLM, frame-ahead (M4)
│   │   └── embed.py                    ← CLIP image embedding
│   └── planner/
│       ├── __init__.py
│       ├── base.py                     ← Plan dataclass + interface
│       ├── rules.py                     ← deterministic planner (always available)
│       └── llm.py                      ← local LLM planner (optional, degrades)
│
├── app/                                ← THE APPLICATION (not graded, not imported by agent)
│   ├── mic_adapter.py                  ← Adapter B: VAD → ASR → events; TTS out
│   ├── tts.py
│   ├── server.py                       ← Adapter C: websocket sandbox
│   ├── static/                         ← timeline UI, commit-log view
│   └── demo_env.py                     ← smart-home tool manifest (mock_env-shaped)
│
├── tools/
│   ├── chaos.py                        ← bulk generate + score + distribution report
│   ├── bench.py                        ← dispatcher latency + model warm-up timing
│   └── judge_quality.py                ← self-grade transcripts on the 4 quality dims
│
├── tests/
│   ├── conformance/                    ← OUR scenarios (not Samsung's)
│   │   ├── c01_double_interrupt.json
│   │   ├── c02_retraction.json
│   │   ├── c03_intent_change.json
│   │   ├── c04_interrupt_during_booking.json
│   │   ├── c05_no_device_hint.json
│   │   ├── c06_paraphrase_pack.json
│   │   └── ...
│   ├── test_invariants.py
│   ├── test_coordinator.py
│   └── test_tools.py
│
├── generated/                          ← gitignored; scenario_gen output
│
└── [KIT — SHIPPED UNCHANGED]
    ├── harness/  scenarios/  docs/  audio/  frames/
    ├── run_local.py  eval_submission.py
    └── README.md → renamed KIT_README.md (our README takes the root)
```

**Hard rule:** `harness/`, `scenarios/`, `docs/`, `run_local.py`, `eval_submission.py` are
**never modified**. The kit's submission layout marks them `(unchanged)`. Our tests live in
`tests/conformance/`, never in `scenarios/`.

**Import rule:** `duet/` must never import from `app/`. The graded agent must run in an
environment where `app/`'s dependencies (sounddevice, TTS, websockets) are absent.

### 1.4 Degradation ladder — what happens when a layer fails

Every layer has a defined fallback. **The agent never crashes and never goes silent.**

| Layer fails | Fallback | Score impact |
|---|---|---|
| Local LLM planner unavailable | `planner/rules.py` deterministic planner | small task loss on paraphrases |
| ASR model unavailable | acknowledge + `clarification_request` ("I didn't catch that — where to?") | partial credit instead of 0 |
| ASR low confidence | clarify (M5) — this is *correct behaviour*, not a fallback | full credit on `pub_05`-type |
| VLM unavailable | `lookup_manual` with a generic text query; final answer cites the page | ~0.4 of `pub_07` retained |
| CLIP unavailable | omit `image_embedding` | lose 0.15 only |
| Referenced media file missing | treat as unintelligible input → clarify | avoids `agent_crash` = 0 |
| Any unexpected exception | caught at dispatcher, logged, fast path still speaks | avoids total loss |

This ladder is a **deliverable**, not a nice-to-have: it is what converts "one bad install in
the grading environment" from a zero into a 60.

---

## 2. The phases

Seven phases across seven days. **Every phase ends with a runnable, scoreable agent.** We never
leave the tree in a half-migrated state overnight.

---

## PHASE 0 — Foundations & Contract Lock
**Day 1 (19 Sep), morning · ~4 hours**

### Goal
Make it impossible to violate the protocol by accident, and establish the measurement
infrastructure we will use for the other six days.

### Why this is first
Every point in the safety category (10–20 per scenario) and a meaningful chunk of task comes
from *not making mistakes*. If `emit()` is airtight on Day 1, we never spend another minute on
protocol errors, missing snapshots, filler spam or premature claims. It also means every
measurement we take for the rest of the week is trustworthy.

### Build

**0.1 Repo + git**
- `git init`, `.gitignore` extended (`generated/`, `__pycache__/`, `.env`, `models/`)
- Rename kit `README.md` → `KIT_README.md`; our README takes the root
- First commit before any code: the kit, untouched, as the baseline

**0.2 `duet/config.py`** — every magic number in one place, none of them scattered:
```
FULL_CREDIT_MS = 800        ZERO_CREDIT_MS = 2500
FAST_PATH_BUDGET_MS = 300   (our own target, well inside 800)
DISPATCHER_BUDGET_MS = 20   (Invariant 1)
CANCEL_GRACE_MS = 800       FILLER_BUDGET = 4  (read from scenario when given)
ASR_CLARIFY_THRESHOLD = ... (tuned in Phase 2)
COMMITMENT_GATE = ...       (Phase 1)
```

**0.3 `duet/nlg.py` v1** — utterance generation with three hard rules:
- never repeat a filler verbatim (rotating variant pools, seeded per scenario)
- never emit text matching a completion-claim pattern before the corresponding tool succeeds
- always ≥3 chars, ≥50% alphabetic (the scorer's substantive-speech test)

**0.4 `emit()` in `duet/runtime.py`** — Invariant 2. The single exit point.

**0.5 `tools/bench.py`** — measures dispatcher handler latency distribution and model warm-up.

**0.6 `tests/test_invariants.py`** — the build gate:
- grep assertion: `out_q.put` occurs exactly once in `duet/`
- grep assertion: no `requests.`, `time.sleep`, or sync client construction inside `duet/`
- every action emitted in a full public run passes `protocol.validate_action()`
- dispatcher p99 < 20 ms across all 9 public scenarios

### Acceptance gate — Phase 0 is done when
- [ ] `python run_local.py --all --time-scale 1 --agent agent.agent:ParticipantAgent` runs with **zero `protocol_error`** and **zero `agent_crash`** entries in any trace
- [ ] `pytest tests/test_invariants.py` passes
- [ ] baseline recorded: our agent's score today, per scenario, committed as `results/day1_baseline.json`

### Risks
| Risk | Mitigation |
|---|---|
| Over-engineering config before we know the thresholds | Ship placeholder values; Phase 2 tunes them |
| Spending the morning on repo aesthetics | Timebox to 4 h; if `emit()` and the invariant tests exist, move on |

---

## PHASE 1 — The Coordination Core
**Day 1 (19 Sep) afternoon → Day 2 (20 Sep) morning · ~12 hours**

### Goal
A fully working interruptible agent **with no models at all** — pure Python. Target: ~90 on
every text scenario and on generated variants.

### Why this is the highest-leverage phase of the week
Three of the four scoring categories — **recovery (35), safety (10), latency (15)** — are
decided entirely here, by code that involves no machine learning. That is up to 60 points per
scenario available from careful engineering. It is also the phase with the fewest unknowns, so
it is the phase where estimates are reliable.

### Build

**1.1 `duet/state.py` — slots with provenance (M3)**

A slot is not a string. It is:
```
Slot = { name, value, confidence, source_utterance_id, source_span,
         set_at_ms, epoch, superseded_by }
```
Why: the theme's objective #3 is *"apply localized slot corrections."* Provenance is what makes
a correction local. When the user says "no, New York", we rewrite the `destination` slot and
leave `passenger_name` and `date` untouched — and we can *explain* what changed, which feeds
M6 and the quality multiplier.

Also here: `snapshot()` producing `{"intent": ..., "slots": {...}}`, and the **epoch history
ring buffer** that makes M6 (undo) possible.

**1.2 `duet/coordinator.py` — the heart (M1, M2)**

*Epoch counter.* `self.epoch: int`. Bumped by: `interruption` event, intra-turn self-repair,
detected intent change. Every spawned task captures `epoch_at_birth`. Every task result is
checked: `if born_epoch != current_epoch: discard silently`.

*Call registry.* `call_id → {api_name, args, kind, epoch, depends_on_slots, status}`.
`call_id` is always ours (`c{n}`), never auto-assigned — an auto-assigned id cannot be
cancelled, which is an automatic recovery violation.

*Invalidation policy.* On epoch bump, for each in-flight call:
- if any slot in `depends_on_slots` changed → **cancel**
- if we cannot determine dependence → **cancel** (cancellation is free under the scorer; a
  stale completion is not)
- if the call is read-only and provably unaffected → keep
Then decide what to re-issue. Target: cancels emitted **within 150 ms** of the interruption
(the grace window is 800 ms; we aim for 5× headroom).

*Idempotency ledger (M2).* Key `(api_name, normalized_args)` → `NONE | IN_FLIGHT | COMMITTED`.
Before any `state_modifying` emission: refuse if `IN_FLIGHT` or `COMMITTED`. On a `timeout`
error from a state-modifying tool: **never blind-retry** — mark `UNKNOWN`, and either verify
with a read-only call or ask the user.

*Commitment gate (M2).* A state-modifying call may only be emitted when all hold:
1. the turn has ended (`end_of_turn == true`), and
2. every required slot is filled with confidence ≥ threshold, and
3. no epoch bump occurred within the last N ms (default 250), and
4. the ledger permits it.
Read-only calls bypass the gate entirely and may fire on partial turns.

*Undo stack (M6).* Epoch snapshots retained; `restore(epoch)` for retractions and "go back".

**1.3 `duet/tools.py` — the schema→arguments compiler**

Reads the `tool_manifest` event and builds, per tool: required args, types, enums, nested
object shapes, array element types, the `kind` tag, the delay range, and `default_result`.

Provides:
- `candidates(intent, slots)` → ranked tools, **by schema semantics, not by name**
- `build_args(tool, slots)` → arguments, type-coerced and enum-validated *before* emission
- `validate(tool, args)` → mirrors `mock_env._validate_args` so we never burn a real 200 ms
  round-trip on an `invalid_args` error
- `describe_result(tool, result)` → which fields to ground the final answer in, derived from
  `default_result` when the tool is unknown

**No tool name appears anywhere in `duet/`.** This is the `pub_09` skill and the ~10 hidden
tools. Enforced by a grep test.

**1.4 `duet/fastpath.py` — content-aware acknowledgment without a model**

- **Repair detection:** trigger lexicon (`actually`, `wait`, `make it`, `change to`, `instead`,
  `no,`, `I meant`, `scratch that`, `never mind`, `forget the`) → classify as
  `CORRECTION | RETRACTION | INTENT_CHANGE | REFINEMENT`. The distinction matters: a
  *refinement* ("and make it window seat") must **not** cancel the in-flight search, while a
  *correction* must.
- **Value extraction:** grab the noun phrase following the trigger. Generalises to unseen
  cities, unlike the baseline's 7-city gazetteer.
- **Slot targeting:** infer which slot the new value replaces by type compatibility against the
  live manifest schema, not by hardcoded names.

**1.5 `duet/planner/rules.py` — deterministic planner**

Intent classification and slot filling by pattern. Must correctly handle the **negative case**
(`pub_04`): a question that needs no tool at all. This planner is the permanent fallback —
Phase 3's LLM planner sits *in front* of it, never replaces it.

**1.6 `duet/runtime.py` — the dispatcher**

One handler per event type, each under the 20 ms budget:

| Event | Fast-path action (≤ ~200 ms) | Spawned |
|---|---|---|
| `tool_manifest` | parse, index | — |
| `user_speech_chunk` (partial) | buffer; speculate read-only if confident | read-only call |
| `user_speech_chunk` (end) | filler + snapshot | plan → tools |
| `user_audio_chunk` | filler immediately | ASR job (Phase 2) |
| `video_frame` | none (silent) | vision job on arrival (M4, Phase 2) |
| `interruption` | **bump epoch → cancel → ack naming the new value → snapshot** | re-plan |
| `tool_result` | epoch check → ground or discard | final response |
| `scenario_end` | ensure a final response inside the tail | flush |

**1.7 `tests/conformance/` — our own scenarios**

The 9 public scenarios are worth zero points. We author the cases the kit *doesn't* cover, in
the kit's own JSON format so `run_local.py` scores them:
- `c01` two interruptions in one scenario
- `c02` retraction ("actually, never mind")
- `c03` full intent change ("forget the flight, my TV is broken")
- `c04` interruption *during* a chained booking (the double-book trap)
- `c05` visual frame with **no `device_hint`**
- `c06` paraphrase pack — 12 ways of saying the same booking request
- `c07` tool returns empty-but-successful result
- `c08` state-modifying tool times out (must not retry)

### Acceptance gate — Phase 1 is done when
- [ ] `pub_01, 02, 03, 04, 08, 09` all **≥ 90** at `--time-scale 1`
- [ ] 50 generated scenarios (`simple_search` ×20, `search_interrupt` ×20, `unseen_tool` ×10, unseen seeds): **mean ≥ 90, minimum ≥ 75**
- [ ] all 8 conformance scenarios ≥ 80
- [ ] recovery subscore = **1.00** on every scenario containing an interruption
- [ ] zero duplicate state-modifying completions across the entire suite
- [ ] cancel latency after interruption: **p99 < 200 ms**
- [ ] grep test: no tool name literal anywhere in `duet/`

### Risks
| Risk | Trigger | Mitigation |
|---|---|---|
| Refinement-vs-correction misclassification cancels valid work | `c04` task score drops | Default to cancel-and-reissue for read-only (free); only refinements on state-modifying need care |
| Value extraction fails on unusual phrasing | `c06` paraphrase pack below 80 | Phase 3's LLM planner races the rules; first valid wins |
| Over-speculation causes `pub_04` regressions | `no_tool_calls` checkpoint fails | Explicit negative-routing branch with its own tests |

---

## PHASE 2 — Perception
**Day 2 (20 Sep) afternoon → Day 3 (21 Sep) · ~14 hours**

### Goal
Audio and visual scenarios from 0 → 85+. This is **60% of the weighted hidden score**.

### Why it is pulled this early
It carries every schedule risk we have — model downloads against the 300 s `setup()` cap, VRAM
sizing, third-party A100 access, and the ASR confidence gate (the fiddliest single thing in the
kit). The core (Phase 1) has almost no unknowns; perception has all of them. Discovering a
problem on Day 3 is recoverable; discovering it on Day 6 is not.

**Day 2 afternoon is a deliberate *thin spike*** — tiny models on the RTX 3060, end-to-end,
just to prove the plumbing. Day 3 makes it good.

### Build

**2.1 `duet/perception/asr.py` (M5)**
- faster-whisper (CTranslate2), int8. `large-v3-turbo` on the A6000; `small`/`base` on the 3060
- loaded in `setup()`, cached at **module level** so only the first scenario pays
- returns `{text, segments, avg_logprob, no_speech_prob, word_probs}`
- **the confidence gate:** compute a per-slot confidence from the word probabilities covering
  the extracted value. Below threshold → `clarification_request` that *names both candidates*
  ("did you say Austin or Boston?"). Above → proceed.
- **n-best disambiguation:** with `beam_size > 1`, if the top-2 hypotheses disagree on the slot
  token, that disagreement *is* the ambiguity — surface both. This is exactly `pub_05`.
- **disfluency handling:** strip fillers, detect intra-turn self-repair markers, and feed the
  repair through the same M3 path as an interruption. The abandoned value must never reach a
  tool (`pub_06`).

**2.2 `duet/perception/vision.py` (M4)**
- a small local VLM (Qwen2.5-VL-3B first; 7B only if 3B under-performs and VRAM allows)
- **frame-ahead:** the job starts on `video_frame` arrival, *before the question*. In `pub_07`
  that is 500 ms of free compute. Result cached against `frame_id`.
- produces a structured reading: salient object, its label/OCR text, position, and a
  **query string suitable for the manual search** — not prose
- `device_hint` may be absent (our `c05`): default to `GENERIC` or omit the enum arg

**2.3 `duet/perception/embed.py`**
- CLIP ViT-B/32 via `sentence-transformers` → real `image_embedding`
- A dummy `[0.0]*16` would pass the checkpoint; we do not do that. It is ~15 lines to do
  honestly and degenerate passes are explicitly subject to manual review.

**2.4 Grounding discipline**
The final answer cites **only** the page matching the perceived object. Summarising the top-3
manual pages leaks the strings "USB Ports"/"Headphone" and fails the no-hedging checkpoint,
which applies to the **entire trace with no time window**.

**2.5 Warm-up budget measurement** — `tools/bench.py` records cold-start wall time. If the
first-scenario `setup()` risks the 300 s cap, we drop to a smaller VLM or pre-seed the cache
in the Docker image.

### Acceptance gate — Phase 2 is done when
- [ ] `pub_05` ≥ 85 — clarifies *before* 4200 ms, fires no tool before then, then searches the confirmed city
- [ ] `pub_06` ≥ 90 — repaired city searched, abandoned city **never** searched
- [ ] `pub_07` ≥ 85 — HDMI named, embedding passed, page cited, no forbidden word anywhere
- [ ] `c05` (no `device_hint`) ≥ 80
- [ ] first-turn spoken response after `user_audio_chunk`: **< 400 ms** (filler before transcription)
- [ ] `setup()` cold start measured and documented; warm start < 2 s
- [ ] every perception failure path exercised by a test (missing file, corrupt media, model absent)

### Risks
| Risk | Trigger | Mitigation |
|---|---|---|
| Model download blows the 300 s cap on scenario #1 | bench shows > 240 s | smaller VLM; pre-seed HF cache in Docker; accept 1/60 scenario loss as worst case |
| 3060 (6 GB) cannot host the dev stack | OOM | 4-bit quantisation for dev; real sizes only on Kaggle/A100 |
| ASR threshold overfits to the 4 public MP3s | `pub_05` passes but synthetic ambiguity fails | build a small ambiguity test set by mixing noise into TTS clips |
| A100 unavailable when needed | scheduling | **book Day 4 and Day 6 slots today** |

---

## PHASE 3 — Reasoning & Generalization
**Day 4 (22 Sep) · ~10 hours · includes A100 session #1**

### Goal
Survive paraphrases, unseen tools and adversarial timing. Move from "passes our tests" to
"passes tests we have never seen."

### Why now
The core and perception are working; this phase is about *breadth*, and breadth is only
measurable once the depth exists. It is also the right time for the first full-size validation
run, because we now know what we are validating.

### Build

**3.1 `duet/planner/llm.py`** — a small local instruct model, strictly behind the fast path
- races `planner/rules.py`; first valid result wins; rules are the floor
- constrained JSON output; a parse failure falls back silently, never crashes
- greedy decoding, fixed seed, `temperature=0` (determinism is a stated requirement)
- **hard timeout.** If the plan is not back within budget, the rules plan proceeds.
- jobs are epoch-stamped (M1): a plan computed under a superseded intent is discarded

**3.2 Zero-shot tool handling, hardened**
- `unseen_tool` generated scenarios ×30, unseen seeds
- nested-object and array args exercised (`create_support_ticket` is the public example; the
  TOOLS.md coverage map is the checklist)
- hidden `state_modifying` tools: the ledger applies to them too, identified by their `kind`
  tag from the manifest, not by name

**3.3 Adversarial timing**
- interruption arriving 50 ms before a tool completes
- two interruptions 400 ms apart
- interruption during the tail window after `scenario_end`
- `tool_result` arriving for a call cancelled 10 ms earlier

**3.4 `tools/chaos.py`** — generate 200 scenarios across all templates with unseen seeds, run,
and produce a **score distribution** (mean, p10, min, per-category breakdown). This number goes
in the README and is our honest generalisation claim.

**3.5 `tools/judge_quality.py`** — self-grade transcripts on relevance / truthfulness /
naturalness / non-redundancy using the illustrative prompt in SCORING.md. The real judge prompt
is unpublished; the four dimensions are the contract. Target: no dimension below 4/5.

**3.6 A100 session #1** — full-size stack, `eval_submission.py --reps 3 --time-scale 1`,
cold-start timing, VRAM ceiling.

### Acceptance gate — Phase 3 is done when
- [ ] chaos run (200 scenarios): **mean ≥ 88, p10 ≥ 78, min ≥ 60**
- [ ] `unseen_tool` ×30 unseen seeds: mean ≥ 92
- [ ] all 9 public scenarios ≥ 88 at `--time-scale 1`
- [ ] weighted score from `eval_submission.py --reps 3` on the A100: **≥ 88**
- [ ] self-graded quality: no dimension below 4/5
- [ ] **kill-switch test:** with every model deliberately unavailable, the agent still scores ≥ 55 (the degradation ladder works)

### Risks
| Risk | Trigger | Mitigation |
|---|---|---|
| LLM planner adds latency without adding score | before/after chaos comparison | feature-flag it off; rules-only is a valid shipping configuration |
| Non-determinism across reps | rep spread > 5 points on any scenario | greedy decode, fixed seeds, reduce LLM surface |
| Chaos reveals a systemic gap | mean < 80 | Day 5 is reserved as buffer; the application slips before the score does |

---

## PHASE 4 — The Application
**Day 5 (23 Sep) · ~10 hours**

### Goal
Turn the graded engine into something a human can talk to and a judge can break.

### Why this phase exists at all
45% of the jury rubric — innovation (20%), relevance (15%), presentation (10%) — is decided by
the video and the 15 October live demo, not by the automated score. And the brief we set
ourselves is a *working application*, not a test submission.

**This phase is the designated slip buffer.** If Phase 3's gate is not met, this phase is cut
down, not the score.

### Build

**4.1 `app/mic_adapter.py` — Adapter B**
- microphone capture → VAD → endpointing → `user_speech_chunk` events with `end_of_turn`
- **barge-in:** speech detected while TTS is playing → truncate playback immediately, emit an
  `interruption` event carrying the partial transcript
- outbound: `filler_speech` / `clarification_request` / `final_response` → local TTS → speaker
- reuses the *same* `duet` core; adapter code only translates

**4.2 `app/server.py` + `app/static/` — Adapter C, the judge sandbox**
Split screen:
- **left:** free text input, mic button, a prominent **"BARGE IN"** button, and a tool-manifest
  editor (a judge can paste a tool schema live)
- **right:** the live timeline — events in, actions out, tool calls as in-flight bars that
  visibly **get cut** when a cancel lands, slot diffs highlighting on change, a latency counter
- **bottom:** the **commit log** — every epoch as a transaction, with rollbacks marked. This is
  M1 made visible, and it is the single most persuasive thing we can put on a screen.
- optional: live score from `harness/scorer.py` when replaying a scenario

**4.3 `app/demo_env.py` — Bring Your Own Tools**
A smart-home manifest in exactly `mock_env`'s shape: `list_rooms`, `set_climate`,
`set_temperature`, `set_alarm`, `run_diagnostic`. The **unmodified** agent handles them
zero-shot. The demo beat: *"the grader gave it flights; here's a manifest I wrote this morning
for a fridge — same binary, no retraining."*

**4.4 Demo script** — four scripted beats mapped to the theme's own four use cases (in-car,
support booking, camera troubleshooting, accessibility self-repair), plus one unscripted
"you try it" segment.

### Acceptance gate — Phase 4 is done when
- [ ] a human can hold a spoken conversation and interrupt mid-sentence; barge-in truncates TTS in < 300 ms
- [ ] the sandbox renders a live cancel visibly cutting an in-flight tool bar
- [ ] the smart-home manifest works with **zero changes** to `duet/`
- [ ] `duet/` still imports and runs with `app/`'s dependencies uninstalled (import-isolation test)
- [ ] public-set score **unchanged** from Phase 3 (no regression from app work)

---

## PHASE 5 — Validation & Packaging
**Day 6 (24 Sep) · ~10 hours · includes A100 session #2**

### Goal
Produce the actual submission artifacts and prove the package works exactly as the organizers
will run it.

### Build

**5.1 Full official dry run on the A100**
```bash
python eval_submission.py . --time-scale 8 --reps 1     # fast: imports? boots?
python eval_submission.py . --reps 3                     # the real procedure
```
Must print a clean verdict. `VERDICT: INVALID SUBMISSION` means we would score 0.

**5.2 `submission.yaml`** — team name (`CollegeName_TeamName`), entry point, python 3.12,
requirements (graded deps **only** — app deps live in `requirements-app.txt`), env names.

**5.3 `Dockerfile`** — required by FAQ Q17. Pre-seeds the HF model cache so cold start is
predictable. Reproducible from a clean clone.

**5.4 `README.md`** — architecture, the five mechanisms, reproducible setup, **the chaos
distribution**, the degradation ladder, and how to run all three adapters.

**5.5 Demo video (≤ 5 min)** — structure:
| Time | Content |
|---|---|
| 0:00–0:30 | The problem, shown not told: an assistant that can't be interrupted |
| 0:30–1:30 | Live barge-in, real voice, real cancellation |
| 1:30–2:30 | The commit log: what M1 is doing underneath |
| 2:30–3:30 | Bring Your Own Tools — a manifest written live |
| 3:30–4:30 | Multimodal: camera grounding + accessibility self-repair |
| 4:30–5:00 | Results: chaos distribution, architecture, the thesis |

**5.6 Presentation (`CollegeName_TeamName.pdf`)** — problem, the gap (speech vs. action),
the five mechanisms with their literature anchors, architecture, results, the Bixby
positioning, and the "taken further as a PRISM worklet" angle (FAQ Q24 says the jury weighs it).

### Acceptance gate — Phase 5 is done when
- [ ] `eval_submission.py . --reps 3` prints a clean verdict with weighted score ≥ Phase 3's
- [ ] a clean `git clone` + `pip install -r requirements.txt` + one command reproduces the score
- [ ] Docker image builds and runs the public set end to end
- [ ] no secrets in the repo; every env var declared
- [ ] video ≤ 5:00; PPT named correctly

---

## PHASE 6 — Freeze & Submit
**Day 7 (25 Sep) · deadline 11:59 PM**

**Code freeze at 12:00 noon.** Afternoon is verification only. No feature is worth a broken
submission.

- [ ] final `eval_submission.py . --reps 3` on the A100
- [ ] final `run_local.py --all --time-scale 1`
- [ ] checklist against WALKTHROUGH §6 ("mistakes that cost the most points")
- [ ] checklist against SUBMISSION.md pre-submit list
- [ ] push, then tag:
```bash
git tag -a PRISM_GENAI_HACKATHON_Y2026 -m "PRISM Gen AI Hackathon Y2026 Final Submission"
git push origin PRISM_GENAI_HACKATHON_Y2026
```
- [ ] verify the tagged commit contains **every** referenced artifact (video link, PPT, README)
- [ ] submit on the portal with ≥ 4 hours to spare

⚠️ **The tagged commit is what is judged.** A perfect repo with the tag on the wrong commit
scores whatever that commit scores.

---

## 3. Compliance matrix — every Samsung requirement, mapped

| Requirement | Source | Where satisfied |
|---|---|---|
| Python 3.10–3.12, in-process import | PROTOCOL §7 | `submission.yaml`, Phase 0 |
| `__init__(in_queue, out_queue)`, `async run()` | PROTOCOL §5 | `agent/agent.py`, Phase 0 |
| Optional `async setup()` under 300 s | PROTOCOL §5.3 | Phase 2, measured in `bench.py` |
| Never block the event loop | PROTOCOL §5.1 | Invariant 1, enforced by test |
| No thread-unsafe queue writes | PROTOCOL §5.2 | `emit()` is the only writer |
| Per-scenario wall clock (300 s) | FAQ Q33 | measured every run |
| Fresh instance per scenario; module-level model cache | PROTOCOL §5.6 | `perception/*` |
| Session-scoped state only | Theme §6 | `state.py`; no cross-scenario persistence |
| `state_snapshot` on every `final_response` | PROTOCOL §2.5 | `emit()` |
| Own `call_id` on every `tool_call` | PROTOCOL §2.2 | `coordinator.py` |
| No duplicate state-modifying calls | SCORING §4 | idempotency ledger (M2) |
| Filler budget respected | SCORING §4 | `emit()` reads scenario budget |
| No premature completion claims | SCORING §4 | `nlg.py` |
| Tools read from `tool_manifest`, not hardcoded | TOOLS.md Part 1 | `tools.py` + grep test |
| No scenario IDs / timestamps / `ground_truth` read | README Rules | grep test in CI |
| Determinism (seeds, temperature 0) | SUBMISSION.md | `planner/llm.py`, `config.py` |
| Decision logic inside the submission | SUBMISSION.md | no external service calls at all |
| Public PyPI deps only | SUBMISSION.md | `requirements.txt` |
| Kit files unchanged | SUBMISSION.md | repo rule + CI diff check |
| GitHub repo + README + Docker | FAQ Q17 | Phase 5 |
| Release tag `PRISM_GENAI_HACKATHON_Y2026` | FAQ Q19–20 | Phase 6 |
| Demo video ≤ 5 min | FAQ Q17 | Phase 5 |
| PPT named `CollegeName_TeamName` | FAQ Q21 | Phase 5 |

---

## 4. How we work together

**My role:** reviewer and judge of the implementation, and co-author on request.

**Review protocol — every module gets the same four-question review:**
1. **Correctness:** does it do what the protocol/scorer actually requires (verified against the
   code, not the prose)?
2. **Invariants:** does it violate Invariant 1 (blocking) or Invariant 2 (emit path)?
3. **Generality:** would this survive a re-skin? Any hardcoded name, city, or tool is a defect.
4. **Degradation:** what happens when the model/file/network it depends on is absent?

**Objective gates, not opinions.** Every phase has a numeric acceptance gate above. A phase is
not done because it feels done; it is done when the numbers clear. I will run the suite and
report the numbers, including when they are worse than the previous run.

**Cadence:** at the end of each phase, a scored run committed to `results/` so we always have a
regression trail. If a change improves the 9 public scenarios but worsens the chaos
distribution, it is a hardcode and it gets reverted — that rule is not negotiable, because it
is the exact failure mode the hidden set is designed to catch.

---

## 5. Risk register

| # | Risk | Severity | Trigger / early warning | Response |
|---|---|---|---|---|
| R1 | Blocking call sneaks into the dispatcher | **Critical** | `bench.py` p99 > 20 ms | Invariant test fails the build |
| R2 | Model download exceeds 300 s cap | High | cold-start bench > 240 s | smaller VLM; pre-seed Docker cache |
| R3 | A100 unavailable on Day 4 or 6 | High | scheduling | **book both slots today**; Kaggle T4 as degraded fallback |
| R4 | Hardcoding creeps in under time pressure | High | public up, chaos down | automatic revert rule; grep tests |
| R5 | Phase 2 slips (perception is the unknown) | Medium | Day 3 gate missed | cut the LLM planner (Phase 3.1) before cutting perception |
| R6 | App work regresses the graded score | Medium | Phase 4 gate | import isolation + score re-run after every app commit |
| R7 | 3060 too small for dev iteration | Medium | OOM | 4-bit dev models; correctness locally, quality on A100 |
| R8 | Quality multiplier drags the score | Low–Med | self-judge < 4/5 | `nlg.py` variant pools; truthfulness rules |
| R9 | Practice drop lands mid-week with new mechanics | Low | organizer announcement | watch the portal daily; it is *extra* test signal, not a rewrite |
| R10 | Submission packaging error | **Critical** | — | Phase 5 dry run + Phase 6 tag verification |

---

## 6. The one-line summary of the whole plan

> **Days 1–2: build a perfect interruption handler with no models.
> Days 2–4: give it eyes, ears and judgement, and prove it generalises.
> Day 5: give it a voice and a face.
> Days 6–7: prove it, package it, ship it with a day to spare.**

---

## Appendix A — Research anchors

| Paper / source | What we take from it | Where it lands |
|---|---|---|
| [Full-Duplex-Bench v3](https://arxiv.org/pdf/2604.04847) | tool use must survive disfluency and correction | M3, Phase 1.4, `c06` |
| [EchoChain](https://arxiv.org/pdf/2604.16456) | conversation *and* execution state must update atomically on interruption | M1, Phase 1.2 |
| [Full-Duplex-Bench v1.5](https://arxiv.org/pdf/2507.23159) | measure timing and interruption handling, not just final accuracy | acceptance gates, `bench.py` |
| [Testing after GPT-Live](https://roark.ai/blog/testing-full-duplex-voice-agents-gpt-live) | tests must include barge-in, overlap, mid-response cancellation | `tests/conformance/`, Phase 4.1 |
| [RelayS2S](https://arxiv.org/pdf/2603.23346) | start reasoning from partial input | M4, Phase 1.6 partial-turn speculation |
| [Act While Thinking](https://arxiv.org/html/2603.18897v1) | speculative tool calls must be cancellable | M2, Phase 1.2 |
| [Speculative Tool Calling for Voice](https://getstream.io/blog/speculative-tool-calling-voice/) | high-confidence actions can begin before the turn ends | M2 commitment gate |
| [Bixby One UI 8.5](https://www.androidheadlines.com/2026/02/samsung-bixby-one-ui-8-5-conversational-ai-update-perplexity-launched.html) | context across turns, not per-utterance | M3, positioning |
| [Bixby smart home 2026](https://www.androidheadlines.com/2026/03/samsung-bixby-upgrade-smart-home-appliances-2026.html) | action execution is first-class | `app/demo_env.py` |
| [Gemini API models](https://ai.google.dev/gemini-api/docs/models) | cloud capability baseline; availability must not be assumed | rejected-option rationale |
| [Gemini audio models](https://blog.google/innovation-and-ai/technology/developers-tools/build-real-time-voice-applications-gemini-audio/) | audio as a first-class modality | `perception/asr.py` design |
| [Open-source STT benchmarks 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks) | pick ASR on latency + VRAM, not WER alone | Phase 2.1 model choice |
