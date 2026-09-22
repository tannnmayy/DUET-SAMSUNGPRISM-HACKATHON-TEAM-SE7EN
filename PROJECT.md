# DUET — Project Reference

**The canonical document for this project.** If you are a new session, a new
agent, or a teammate picking this up cold: read this file first and trust it
over any recollection. Everything here is either quoted from a source file in
the repo or is a number produced by a command recorded alongside it.

| | |
|---|---|
| **Project** | DUET — *Duplex Utterance-Epoch Transaction runtime* |
| **Competition** | Samsung PRISM GenAI Hackathon 3.0, Theme 05: Interruptible Real-Time Agents |
| **Repo root** | `E:\HACKATHONPRISM` (repo root **is** the submission root) |
| **Entry point** | `agent.agent:ParticipantAgent` |
| **Last updated** | 22 September 2026 (Phases 3b.1–3b.6 and first packaging) |
| **Team** | SE7EN, SRM — `SRM_SE7EN` in `submission.yaml` |
| **Submission deadline** | 25 September 2026, 23:59 |
| **Current official score** | **97.4 weighted** on the pinned GPU stack (`eval_submission.py . --time-scale 1`) |

Companion documents, all still current:

| File | Contents |
|---|---|
| `README.md` | Judge-facing overview: architecture, results, setup, honest limits |
| `notes/THEME5_MASTER_ANALYSIS.md` | Problem analysis, verified scorer mechanics, constraints, competitive positioning |
| `notes/BUILD_PLAN.md` | The seven-phase plan this document reports against |
| `notes/FINDINGS.md` | Numbered findings F1–F9 and F15–F22 with evidence (F10–F14 in Part IV here) |
| `notes/ORGANIZER_CLARIFICATIONS.md` | The organizers' binding answers on the evaluation environment |
| `PROJECT.md` | **this file** — state of the build and what happens next |

---

# PART I — THE SITUATION

## 1.1 What is actually being graded

Not a voice assistant. A **Python class** that reads dicts off an
`asyncio.Queue` and writes dicts to another, on the harness's own event loop,
scored entirely from a replay log.

```
                    ┌──────────── harness event loop (ONE loop) ─────────────┐
  scenario.json ───►│ replay events at virtual timestamps ─► in_queue ─► run()│
                    │ MockEnvironment (async, 0.6–3.0s) ◄─ out_queue ◄────────┘
                    │        └─► tool_result back into in_queue               │
                    │ every event + action + completion ─► trace ─► scorer.py │
                    └────────────────────────────────────────────────────────┘
```

Seven inbound event types, five outbound action types. That is the entire
integration surface. No microphone, no TTS, no VAD, no UI — those are
explicitly out of scope *for grading*, though we build them anyway for the
15 October demo (see §6.4).

## 1.2 The hard constraints

| Constraint | Value | Source |
|---|---|---|
| Runtime | Python 3.10–3.12, imported in-process | `docs/PROTOCOL.md` §7 |
| Wall clock | **300 s** per scenario (relaxed from 120) | FAQ Q33 |
| `setup()` | 300 s, off the clock, own cap | `docs/PROTOCOL.md` §5.3 |
| GPU | 1× 48 GB A6000-class, guaranteed | FAQ Q31 |
| Network | **Not air-gapped but firewalled.** Only pypi.org and huggingface.co guaranteed | FAQ Q32 |
| State | Session-scoped only; fresh instance per scenario | Theme §6 |
| Feedback | **None.** No leaderboard, one submission | `docs/SUBMISSION.md` |

**The dominant rule** (`PROTOCOL.md` §5): `run()` shares the harness's event
loop. One blocking call freezes the simulation — events arrive late and
bunched, in-flight tools stop progressing, and *the trace blames you for
things you did not do*. The docs give a measured example: a 2 s sync call
turned a 100-point `pub_02` into 61, recorded as *"re-issued a stale call
after the interruption"*, because the interruption was still in the queue.

## 1.3 Where the points live

Hidden set: ~60 scenarios, 50% text / 30% audio / 20% visual, multimodal at
**×1.5**, L3/L4 at ×1.25.

```
text    30 × 1.0 = 30.0        →  40% of weighted total
audio   18 × 1.5 = 27.0        →  36%
visual  12 × 1.5 = 18.0        →  24%
                   75.0           multimodal = 60%
```

The kit's `BaselineAgent` scores ~57 overall and **0 on both audio scenarios**
because it ignores `user_audio_chunk` entirely.

## 1.4 Timeline

| Date | Milestone | Status |
|---|---|---|
| 19 Sep | Day 1 — Phases 0, 1a | done |
| 20 Sep | Day 2 — Phases 1b, 1c, 1-gate, 2a, 2b, 2c, 3a | done |
| 21 Sep | Day 3 — context review; organizer questions answered | done |
| 22 Sep | Day 4 — Phases 3b.1–3b.6, GPU stack pinned, README + Dockerfile | done (see §3.10–3.14) |
| 23 Sep | Day 5 — Docker build on a GPU host, deck, timeline viewer for the video | **next** |
| 24 Sep | Day 6 — video, final A100/Kaggle validation, PROJECT/README numbers | |
| 25 Sep | Day 7 — Phase 6 (freeze at noon, tag, submit) | |
| 9 Oct | Top 15 announced | |
| 15 Oct | Live demo round | |

**Submission mechanics (FAQ Q17–Q21):** public GitHub repo, git tag
`PRISM_GENAI_HACKATHON_Y2026` on the final commit (*the tagged commit is what
is judged*), README with reproducible setup + Docker, demo video ≤5 min, deck
named `CollegeName_TeamName`.

---

# PART II — THE ARCHITECTURE

## 2.1 Thesis

> **Full-duplex speech is solved. Full-duplex *action* is not.** Bixby already
> controls the device; it just cannot survive you changing your mind while
> it is acting. DUET is the coordination layer that makes that safe.

GPT-Live (July 2026) solved simultaneous speech. Nobody has cleanly solved
what happens to *work already in flight* when the user changes their mind —
the 2026 literature is only now building benchmarks for it (Full-Duplex-Bench
v3 targets tool use under disfluency; EchoChain targets state-update reasoning
under interruptions).

## 2.2 The six mechanisms

| # | Mechanism | Implementation | Literature anchor |
|---|---|---|---|
| **M1** | **Epoch-versioned state** — every computation carries the epoch it was conceived under; an interruption bumps it and atomically orphans all descendants | `state.py` `bump_epoch`/`changed_since`, `coordinator.py` `should_invalidate` | EchoChain |
| **M2** | **Reversibility-gated commitment** — read-only fires freely, state-modifying passes a gate; idempotency ledger keys on the **scorer's own** duplicate key | `coordinator.py` `may_issue`, `idempotency_key` | Act While Thinking; Speculative Tool Calling |
| **M3** | **Slot provenance** — a slot carries source, confidence, utterance id, span, epoch, enabling *localized* corrections | `state.py` `Slot` | Full-Duplex-Bench v3 |
| **M4** | **Perception-ahead** — vision starts when a frame *arrives*, not when it is asked about | `runtime.py` `on_video_frame` → `_read_frame` | RelayS2S |
| **M5** | **Calibrated abstention** — gate on the *primary slot word's* probability, not the sentence | `asr.py` `value_confidence`, `runtime.py` `_on_audio_turn` | theme accessibility case |
| **M6** | **Conversational undo** — epoch checkpoints make "go back" a state restore | `state.py` `restore_epoch`/`undo_last` | falls out of M1 |

## 2.3 The two invariants

> **INVARIANT 1 — the dispatcher never blocks.**
> No handler may occupy the loop >20 ms. Slow work is `create_task`-ed.
> *Enforced:* `telemetry.Watchdog` + `tests/test_invariants.py` (p99 assertion)
> + `tools/bench.py`. **Measured worst: 0.469 ms.**

> **INVARIANT 2 — one queue write.**
> `put_nowait` appears **exactly once** in `duet/`, inside `Emitter._send`.
> *Enforced:* grep test. Every safety rule is implemented there once and
> cannot be forgotten at a call site.

`emit()` is **synchronous** by design: `asyncio.Queue` is unbounded so
`put_nowait` never blocks, which means there is no await point between
deciding to speak and speaking.

## 2.4 Layer map

```
  in_queue ─► DISPATCHER (runtime.py)   sync, <20 ms, never raises
                 │
    ┌────────────┼────────────────┬──────────────────┐
    ▼            ▼                ▼                  ▼
 FAST PATH   COORDINATOR      SLOW PATH          STATE
 fastpath.py coordinator.py   perception/*       state.py
 nlg.py      • epoch     M1   planner/llm        • slots+provenance M3
 • repair    • registry       • asr   M5         • epochs           M1
 • ack+echo  • ledger    M2   • vision M4        • undo             M6
 • clarify   • gate      M2   • embed
 ~5–15 ms    ~1 ms            150–2000 ms
    └────────────┴────────────────┴─────► EMITTER (emitter.py) ─► out_queue
```

## 2.5 Degradation ladder

Every layer has a defined fallback; **the agent never crashes and never goes
silent**. Verified by `tools/killswitch.py` — **89.1 average with every model
disabled, 0 crashes, 0 protocol errors, 0 silent scenarios**.

| Layer fails | Fallback | Cost |
|---|---|---|
| LLM planner absent | `planner/rules.py` | small, on paraphrases |
| ASR absent | acknowledge + clarify | `pub_05` still scores **72.3** |
| ASR low confidence | clarify — *this is correct behaviour*, not a fallback | full credit |
| VLM absent | honest citation without asserting a label | −18.5 on visual |
| CLIP absent | omit `image_embedding` | −0.15 weight |
| Media file missing | treat as unintelligible → clarify | avoids `agent_crash` |
| Any exception | caught at dispatcher; fast path still speaks | avoids total loss |

---

# PART III — WHAT HAS BEEN BUILT

11 commits. **5,805 lines** of engine, **3,821 lines** of tests and tools —
a test-to-code ratio of about 0.66:1.

## 3.1 Phase 0 — Foundations & contract lock (`3eacd9b`)

**Repo restructure.** The kit was nested two deep. Flattened so repo root =
submission root (what `eval_submission.py .` and the release tag need). All
34 kit files md5-verified **byte-identical** after the move. `harness/`,
`scenarios/`, `docs/`, `run_local.py`, `eval_submission.py` remain unmodified.

| Module | Lines | Role |
|---|---|---|
| `config.py` | 156 | Every tunable; `[SCORER]` vs `[OURS]` tags |
| `contract.py` | 257 | Vendored protocol + scorer safety rules |
| `state.py` | 272 | M1 epochs, M3 provenance, M6 undo |
| `nlg.py` | 471 | Deterministic non-repeating speech, ASCII-only |
| `emitter.py` | 248 | Invariant 2, the single queue write |
| `runtime.py` | 888 | Invariant 1, the dispatcher |
| `telemetry.py` | 100 | Ring log + watchdog |

**Why `contract.py` is vendored, not imported:** `duet/` must run where the
grading harness does not exist (mic/sandbox adapters), and our correctness
must not be coupled to a file the organizers may swap. Duplication rots, so
`tests/test_contract_equivalence.py` runs both implementations over a
1000+-case corpus and fails on drift — including drift in the scorer's
`CLAIM_PATTERNS` and `FUTURE_GUARDS` tables.

**Result:** 101 tests; 55.0 on the public set **with no task logic at all** —
empirically confirming that latency + safety are worth ~38 points/scenario.

### Defects caught by our own tests in Phase 0

1. **Phrasebook repeated itself** once pools exhausted — emitting the exact
   verbatim-repeat penalty the guard exists to prevent. Replaced with a 12×10
   combinatorial last-resort pool.
2. **`"Nearly finished"` would have deadlocked the emitter.** It contains
   `finished`, which trips our own completion-claim regex — the emitter would
   reject its own fallback and substitute in a loop. Now there is a test
   asserting *no* NLG fragment trips the claim guard, verified non-vacuous
   with a negative control.
3. **Claim guard was stricter than correct** — it blocked *"I'll get that
   booked now"*, a truthful promise the scorer permits. Rewritten as
   sentence-scoped positional analysis: a future marker must precede the
   completion verb in the same sentence. This is *stricter* than the official
   check in one direction (it catches `"Booked! I'll email you."`, which the
   scorer's whole-string scan misses) and **provably never looser** — a
   540-case property test asserts we never permit what the scorer penalises.

## 3.2 Phase 1a — Conformance suite & schema-driven tools (`7b33f46`)

**Eight conformance scenarios** (`tools/make_conformance.py` → `tests/conformance/`)
covering what the kit does not: two interruptions 800 ms apart, retraction,
full intent change, **interruption during an in-flight booking**, visual with
no `device_hint`, paraphrase + superlative, empty-but-successful result,
state-modifying timeout.

**Delays are pinned with `tool_overrides`,** not left to seeded defaults,
because a conformance scenario is only a real test if an uncancelled stale
call *provably* completes >800 ms after the interruption. Each records its
margin in `_design_notes`.

**Testing the tests.** `test_conformance_discriminates.py` runs two probe
agents differing only in whether they cancel, and asserts: the non-canceller
fails, the canceller passes, the recovery subscore actually moves, and —
read straight from the trace rather than trusted from a comment — that an
uncancelled call really does land past its grace window.

**`tools.py` (650 lines).** Selection, binding and validation entirely from
schemas. No tool name anywhere in `duet/` (grep-enforced). Selection is sparse
lexical retrieval over each tool's own schema with **IDF computed across the
live manifest**. Arguments bind by **role**, so `destination`, `city` and
`pickup_city` all receive a place.

### Defects found

4. **Manifest parse crash (critical).** `spec.get("args") or {}` does not
   guard a *truthy non-dict* — with `args` as a string, `.items()` raised.
   This fires on `tool_manifest`, **the first event of every scenario**, so it
   was a silent per-scenario zero.
5. **`book_flight` outranked `flight_search`** for *"find flights to
   Chicago"*. Both schemas match only the token `flight`, so it fell to an
   alphabetical tiebreak. Fixed by promoting **satisfiability** and
   **reversibility** to first-class signals — M2 applied to selection.
6. **Catch-all args bought unearned credit.** A free-text `query` absorbs any
   sentence. Fallback-filled args now earn partial credit (`TEXT_FALLBACK_CREDIT`).

**A limit deliberately not papered over:** lexical retrieval cannot connect
*"my TV is showing a blinking red light"* to *"retrieve pages from indexed
device manuals"* — zero shared vocabulary. `rank()` reports **no evidence**
rather than inventing a score, and `text_sink()` is an explicit fallback the
planner must choose. Kept separate on purpose: *"what can you help me with?"*
must never reach it (`pub_04` scores a tool call there as a routing failure).

## 3.3 Phase 1b — The coordination core (`deba333`)

`coordinator.py` (430 lines): call registry, epoch invalidation, idempotency
ledger, commitment gate.

**The ledger keys on the scorer's own duplicate key,** reproduced exactly and
verified by a differential test, so what we refuse is *precisely* what it
penalises rather than something approximately similar.

**Cancellation policy is deliberately asymmetric: cancel on doubt.** The
harness logs a cancel for an already-finished call as `cancel_noop` and does
not penalise it; a stale completion 800 ms after an interruption costs half
the recovery category. A call is kept only when *provably* unaffected — which
is what lets a refinement (*"and add a window seat"*) leave a valid search
running.

**Retry policy** (`docs/TOOLS.md`): read-only retries once; state-modifying
only with evidence the attempt did not commit. `invalid_args` is evidence;
**`timeout` is not**, and is recorded `LEDGER_UNKNOWN` so the gate refuses a
fresh attempt with the same arguments.

`fastpath.py` (617 lines): repair classification (correction / retraction /
intent change / refinement / undo) and structural extraction. **No gazetteer.**
Every pattern works on lowercase unpunctuated text, because ASR output is.

`planner/rules.py` (678 lines). **Chaining is not scripted:** before results
exist, `book_flight`'s required `flight_id` cannot be filled so the registry
ranks `flight_search` first; when the search returns, the id exists and
re-ranking the *same* utterance puts `book_flight` on top. The sequence is a
consequence of satisfiability, so it generalises to chains between tools we
have never seen.

### Six defects, all found by tracing

7. `destination` extracted as **"Chicago for Friday"** — the span was trimmed
   only at its edges, but the junk sits in the middle. Spans now cut at the
   first internal preposition.
8. `passenger_name` extracted as **"Friday"** — `extract_people` used `re.I`,
   which makes `[A-Z]` match lowercase, so `"for friday"` looked like a
   person. Case-sensitive patterns separated; date-like values rejected.
9. Final response was **"One moment."** — `describe()` produced
   `"FL-CHI-8AM, 08:00, $129"`, only 30% alphabetic, failing the scorer's
   substantive-speech test, so the emitter correctly substituted. **The guard
   was right; the generator was wrong.** Fields now spoken with connectives.
10. **THE recovery bug.** After a correction, `_replan` re-parsed the
    *original* turn, re-extracted the abandoned city, overwrote the corrected
    slot and re-issued the call just cancelled — recorded as *"stale call
    re-issued"*, costing all 35 recovery points. `pub_02` went 30.2 → 100 on
    this fix alone. Re-planning now works from **state** and never lets a
    superseded utterance write a slot again.
11. *"book a flight to Boston"* also produced a **passenger called Boston** —
    the capitalised-span fallback handed the same value to a second role.
12. `lookup_manual` outranked `flight_search` for *"find a flight to Denver
    and book the 8 AM one"* — a **clock time was being offered as a free-text
    value**, making a manual-search tool look fully satisfiable. Times are
    selectors, not slots.

## 3.4 Phase 1c — Correction kinds & stable measurement (`4a5231e`)

13. **`conf_03` 61 → 100.** The remainder after `"forget the"` still began
    with the abandoned domain's own noun — *"flight. My TV is showing…"* — and
    that one word pulled ranking straight back to the domain we were told to
    drop. The abandoned noun phrase is now cut at the first clause boundary.
    Side benefit: *"forget the flight"* with nothing after is correctly a
    **retraction**, not an intent change.
14. **`conf_07` 68.6 → 100.** An empty collection is a *successful* call with
    no hits (`docs/TOOLS.md`), but `describe()` fell through to whatever
    scalar remained and reported the **search mode** as if it were the answer.
15. **`conf_06` 53.8 → 100.** Two faults: the place/person reassignment fired
    on an *explicit* cue (`"name's Alice"` made Alice the destination), and
    *"put me on the cheapest one"* contains no booking verb so nothing
    promoted a state-modifying tool. Added a generic **commit-intent** signal
    — ordinary English verbs of commitment, not domain vocabulary — **gated on
    full satisfiability** so it can never promote a tool we cannot yet call.
    That gate is what keeps `pub_03` correct.
16. **`conf_04` 96 → 100.** Identifiers bound by role alone, and `flight_id`
    and `booking_id` are both `ROLE_ID`, so a booking reference overwrote the
    flight slot. Arguments now bind by **exact name first**, role second.

### The speech floor (finding F8, §4.8)

Measured, not assumed: the same scenario scored latency **0.10 / 1.00 / 1.00**
across three identical runs. The harness delivered the turn early, our 12 ms
filler was logged *before* the declared timestamp, the scorer skipped it, and
the next spoken action was the final response 2.3 s later — a **20-point swing
from timer jitter**.

`SPEECH_FLOOR_MS = 20` removes it. This changes what the trace *records*, not
what the agent *does*: 2.5% of the budget, inaudible, and a no-op where the
harness is punctual. **The motivation is measurement integrity** — without it
a regression is indistinguishable from jitter. Guarded by an epoch check so an
interruption inside the window drops the deferred utterance.

## 3.5 Phase 1 gate — Generalisation (`fc8440c`)

`tools/chaos.py` generates scenarios from the kit's own templates on **seeds
never tuned against**, runs them in-process, and reports the **distribution**
— the useful question is not how well it usually does but how badly it fails.

**First run (seed 4242) passed the gate** (mean 99.1, min 82.6) **and caught a
bug the public set never would.** Three `gen_interrupt` scenarios failed
`search_new_city` while scoring 100 in isolation; one run six times gave five
passes and one failure on identical code.

17. Same clock artifact, on a surface I had **explicitly reasoned past**. The
    Phase 1c commit said *"tool calls still go out immediately — they do not
    stop the latency clock, and earlier is strictly better."* Wrong. Task
    checkpoints carry `after_ms` windows anchored to the **declared**
    timestamp, so a call emitted 1 ms after an early-delivered interruption
    lands *before its own window opens*. The floor now covers
    interruption-triggered re-planning too.

> **Recorded lesson.** The symptom that was visible (latency) got fixed, and
> the same cause was assumed confined to it. Only scenarios nobody wrote by
> hand surfaced the rest.

## 3.6 Phase 2a — Perception orchestration, built without models (`4aa396d`)

Deliberate ordering: the models are the *uncertain* part; the *hard* part is
the scheduling, and that is deterministic. Built and verified against stub
backends — no GPU needed, and the tests stay valid when real backends land
because the orchestration never learns which backend produced a `Transcript`.

`perception/base.py` — **perception returns a posterior, not a string.**
`available()` is part of the contract. Backends cached **per process**, warmed
in `setup()`.

Wired: acknowledge-then-perceive; M4 frame-ahead (**deliberately not
epoch-guarded** — a frame is an observation about the world, not a plan
derived from an intent, so a change of mind does not invalidate what is in
front of the camera); M5 gating; out-of-order chunk assembly by arrival index;
schema-driven embedding binding.

18. **Bug found by the stubs:** `pub_06`'s self-repair is **inside one turn**,
    not an interruption event, so `_on_turn` never ran `classify_repair` and
    the agent searched the abandoned city. Extracting only from the post-repair
    segment would drop unrelated slots (*"…to Boston for Alice, actually New
    York"* loses Alice), so the turn is harvested whole then re-harvested after
    the trigger, overwriting only what was corrected.

## 3.7 Phase 2b — Real speech recognition (`b29ef0e`)

**Both audio scenarios 53.8/56.9 → 100** with `faster-whisper` on CPU.

M5 was designed **from measurement**, not principle. Whisper `base` on the
kit's own clips:

```
pub_05_turn1  conf 0.38  "And look up now to the question."      no city
pub_05_turn2  conf 0.57  "I said Boston."               Boston 0.95
pub_06_part1  conf 0.40  "Look up light to Boston."     Boston 0.31
pub_06_part2  conf 0.52  "Actually make that New York." New 0.77 York 1.00
```

`pub_06` must be **acted on at 0.40** while `pub_05_turn1` must be
**questioned at 0.38**. No sentence-level threshold separates them. The signal
is the **slot word**, and after one more failure, specifically the **primary**
slot word.

### Four iterations, each from a trace

19. *"to the question"* yielded `destination = "Question"`. Abstract nouns
    excluded from place/person spans.
20. Slot threshold 0.50 admitted `"question."` at 0.55. Raised to **0.65**,
    sitting in a measured gap below `"New York"` at 0.77.
21. *"I said Boston"* is the answer to our own clarification but names no
    domain, so it routed to whatever tool absorbs free text. A pending
    question now carries its request forward.
22. Taking the **minimum across every extracted role** let an incidental
    capitalised span read as a passenger name (0.16) block a search whose
    destination was unambiguous. The gate now judges the **primary** value; a
    shaky name is M2's problem at commit time. When a backend exposes no word
    probabilities, the sentence score is the fallback judge.

### One structural fix with reach beyond audio

23. `base` hears *"Look up **light** to Boston"* — zero lexical overlap with
    `flight_search`, so the right tool was **not even a candidate**. But **a
    tool whose required argument is exactly what the user just supplied has
    evidence** — the filled slot *is* the evidence, not a shared word. That is
    now a third admissibility route alongside lexical overlap and visual
    affinity, and it makes selection robust to imperfect transcription in
    general, including for unseen tools.

## 3.8 Phase 2c — Visual grounding, and an idea that failed (`2d553e3`)

**The idea:** avoid a VLM entirely. A frame question produces a text query
matching every comparable row equally (headphone, USB, ethernet, HDMI pages
all contain "port"), so the tool's ordering is near-arbitrary. Rather than
invent a label vocabulary, let the **tool** propose candidates and let CLIP
pick which the image depicts. Schema-driven, generalises to unseen frames,
600 MB instead of 16 GB.

**It does not work.** Measured over four crops × two prompt templates:

```
full      USB Ports 0.300  Ethernet 0.286  HDMI 0.268
center50  Ethernet 0.317   USB 0.309       HDMI 0.293
center40  Ethernet 0.342   USB 0.305       HDMI 0.290
midband   Ethernet 0.349   USB 0.299       HDMI 0.295
```

CLIP **never** picks HDMI. It cannot read the printed label, and the panel
genuinely contains four connector types, so "USB Ports" is a defensible
description of the *whole image*.

**Kept, but gated on margin** (`CLIP_RERANK_MARGIN = 0.05`). The 0.014 gap
here is noise and it declines. The mechanism is retained because candidates
that *are* visually distinct — a washing machine vs a television — are
plausible in hidden scenarios.

**What earned the points was not identifying the port but being honest that we
had not.** We previously cited the headphone page *by name*, leaking a
forbidden word and asserting a component never verified. Now, absent a
confident identification, we cite the page and stay silent about its title:

> *"I found page 21 of the GENERIC laptop manual."*

**Naming the wrong component is worse than naming none** — M5 applied to vision.

24. `identified = ... or chosen is not None` was **vacuously true** (the
    fallback always sets `chosen`), so labels leaked anyway. Now tracks
    whether re-ranking actually fired.
25. The date pattern `(next|this|last)\s+\w+` matched **"this port"**,
    recording a connector question as a date. Restricted to date nouns.
26. Result fields were spoken in the tool's own key order, producing *"in the
    GENERIC-laptop-manual, on page 21"*. Citations now composed as a person
    would say them.

## 3.9 Phase 3a — Submission validation & degradation (`74195b1`)

**The package validates.** All three official stages pass.
`requirements.txt` and `submission.yaml` kept in step; mic/sandbox
dependencies deliberately excluded from both.

**`tools/killswitch.py`** runs the public set with `DUET_NO_ASR`,
`DUET_NO_VISION`, `DUET_NO_EMBED` set:

```
average with no models : 89.1
crashes / protocol errors / silent scenarios : 0 / 0 / 0
```

`pub_05` scores **72.3 with no speech model at all** — higher than it scored
earlier *with* a working one and a broken decision path. When you genuinely
cannot hear, asking is the correct answer.

Wired into the build gate as **Invariant 7**.

---

## 3.10 Phase 3b.1 — What the agent says (`25158c7`)

The automated score cannot hear the agent. A read-through of every transcript
found a garbled capabilities list spoken as a **final answer** in 6 of 17
scenarios that all scored 100: the harness enqueues `scenario_end` in the same
instant as the last event, and the re-plan answering that event was still
behind the speech floor (F15). `tools/quality.py` now lints every transcript
(repeats, tool names read aloud, stacked answers, cut-off clauses) and was
proven first on the old agent (17/17 flagged). Also fixed: capabilities read as
complete clauses; acknowledgments name the request, not the tool ("Looking up
flights to Chicago for Friday"); bookings reported only from confirmed success
("All set - flight FL-DEN-8AM is booked for Alice, booking reference
BK-0001"); a state-modifying timeout reported as outcome-unknown; audio
acknowledged at end of turn only; two substring bugs ("part" in "depart",
"id" in "humidity").

## 3.11 Phase 3b.2 — The grading machine (`88cc2f7`)

PyPI torch ≥ 2.11 is a CUDA 13 build; CTranslate2 4.8.2 links cuBLAS 12, and
the failure appears only at the first transcription (F16). `torch==2.10.0` is
pinned (last CUDA 12 build, brings cuBLAS 12/cuDNN 9); `perception/cuda.py`
makes them visible; `setup()` runs a trial transcription and falls back to CPU —
proven locally both ways. Every graded dependency is pinned exactly, mirrored in
`submission.yaml` (tested). Every model is pinned by repo **and commit**
(`perception/checkpoints.py`) — one repo had already been renamed (F20).
`tools/prefetch.py` downloads the pinned set. WAV input (48 k stereo, 44.1 k,
16 k) and same-process repetition are tested.

**Calibration under the production model (F17).** On GPU, `large-v3-turbo` hears
pub_05's indistinct clip as "…to Austin" (0.47) and the agent asked "Which place
did you want?" — 100 → 81.5. A value heard at ≥ 0.35 is now confirmed by name
("Sorry, did you say Austin?"), "yes" confirms it, and value confidence is the
mean over a name's words. Both speech models score 100 on both audio scenarios.

## 3.12 Phase 3b.3 — Vision, decided by measurement (`88cc2f7`)

`tools/vision_bench.py`: Qwen2.5-VL-3B named pub_07's HDMI port "USB-C" on 3 of 4
variants under three prompts and invented a matching label (F18). **Vision-model
reading ships off** (`DUET_VISION=1` enables it). Shipped instead: a barrier that
holds a frame question's plan (not its acknowledgment) until the frame is read —
which also fixed a race that dropped the image embedding — and an honest answer
("I could not tell … pages 21, 23 and 25 … cover the likely candidates. Which
one do you mean?") instead of citing the headphone page. A latent crash with no
embedding model is fixed.

## 3.13 Phase 3b.4 — The paraphrase pack (`88cc2f7`)

`conf_09`–`conf_18`: fragments, "what flies to", "need to get to", date
self-repair, apology padding, disfluent text, lowercase multi-word city, number
words, an unseen state-modifying tool. First run 7/10. Structural fixes only:
infinitives after "to" and non-overlapping regex matches ("to get to Tucson"
searched "Get"); clock times as places; number words never reaching binding;
"Restaurant name." read as a person; proper names ending at their last capital;
and the scorer attributing "reserved" to `book_flight` whatever the sentence is
about (F19) — unseen commits are reported neutrally. Now 10/10.

## 3.14 Phases 3b.5–3b.6 — Adversarial timing (`0c65ce7`) and wider chaos

`conf_19`–`conf_23`: interruption 40 ms before a stale result, inside the 20 ms
speech floor, a retraction during a booking, three corrections in 800 ms, a
second correction inside the commitment hold. All scored 100; the transcript
lint still found two real bugs — "Don't book anything" re-planned as a new
request, and "for Priya, not Alice" making Alice the **destination**. Rejected
values are never extracted; negated commands are retractions. Four of the five
are proven to separate a cancelling probe from a naive one.

`tools/scenario_templates.py` adds five randomized templates to `tools/chaos.py`
(paraphrase, retraction, pivot, chained booking, unseen commit). First run
(seed 101): **mean 88.1, 23/100 below 75** — the kit's own templates had never
shown it. Six general causes, all fixed without phrase lists: time-of-day
selectors ("the afternoon flight"); "for Leeds" read as a person when no tool
fits; an incidental argument-description word ("airport **code**") outranking
the right tool — lexical evidence is now field-weighted; "scratch the trip"
not read as abandoning a topic; a sentence's first capitalised word taken as a
name ("Could", "Scratch"); and role selection that required a tool to be
satisfied before extraction could satisfy it.

On a seed never used before (202, all eight templates): **mean 99.7**,
119/120 at 100; the one miss ("The Olive Room" stripped to "Olive Room") is
fixed and tested. Recorded as F23.

---

# PART IV — FINDINGS

Consolidated from `notes/FINDINGS.md` plus Phase 2. Each is something we
learned by reading `harness/scorer.py` or measuring, not from the prose docs.

## F1 — Category weights redistribute
40/35/15/10 applies only when a scenario has **both** an interruption and a
latency spec.

| shape | task | recovery | latency | safety |
|---|---|---|---|---|
| interruption + latency | 40.0 | 35.0 | 15.0 | 10.0 |
| **latency only** | **61.5** | — | **23.1** | **15.4** |
| neither | 80.0 | — | — | 20.0 |

→ ~38 points/scenario available from engineering alone. Confirmed: our
Phase 0 agent scored 55.0 with **no task logic**.

## F2 — `state_snapshot` may ride on any action
Recovery reads the *latest* snapshot after the interruption, on whatever
action carries one. Attaching it to the acknowledgment filler locks in half
the recovery score at ~100 ms. The harness does **not** copy it for
`tool_call`/`cancel_tool` entries, so attaching there is dead weight.

## F3 — Cancellation is free; a stale completion is not
`cancel_noop` is unpenalised; a cancelled call never emits `tool_completed`,
and duplicate-detection counts completions. Therefore cancel-on-doubt is
strictly correct, **and cancel-then-reissue is safe for state-modifying work**
— which is what makes `conf_04` solvable.

## F4 — The scorer's claim check is looser than truthfulness
It skips an utterance containing any `FUTURE_GUARDS` word *anywhere*, so
`"Booked! I'll email you."` escapes. Ours is sentence-scoped and positional —
stricter, and provably never looser (540-case property test).

## F5 — `spoken_not_contains` is plain substring matching, no window
`pub_07` applies it to the **entire trace including fillers**. A hidden
scenario could use `spoken_not_contains: ["booked"]` with
`before_tool_completes`, which would penalise even an honest promise. Encoded
in `conf_08`.

## F6 — The no-participation gate
Neither speaking nor calling a tool scores a flat **0**, whatever the negative
checkpoints say. This is why the kit baseline scores 0 (not ~40) on audio.

## F7 — Vacuous negatives inflate a do-nothing agent
`tool_not_called` passes when nothing is called. Our Phase 0 agent scored ~60
mean on conformance while calling zero tools. **Conformance scores drop when
real tool use lands, and that is healthy.** Track task subscore separately.

## F8 — Harness clock undershoot on Windows *(measurement artifact)*
The scorer measures from the **declared** timestamp but only considers actions
logged at `t_ms >= ev_t` on the harness's clock. Early delivery ⇒ a perfect
response scores "never responded".

```
tools/clockcheck.py, 51 deliveries, 3 reps, scale 1:
  delivered EARLY      : 9 / 51  (17.6%)
  worst early delivery : -9.0 ms
```
Cause: `asyncio.sleep` returning early on Windows (~15.6 ms proactor timer
granularity). **Mitigated** by `SPEECH_FLOOR_MS = 20`, covering both speech
and interruption-triggered tool calls.
**ACTION: re-run `tools/clockcheck.py` on the A100 (Linux).** If undershoot
does not occur there, consider whether the floor remains justified.

## F9 — `--time-scale` distorts latency, not correctness
Scale 8 speeds the scenario clock but not our compute. With ASR at ~1 s real,
scale 8 makes it look like 8 s: `pub_05` scores **53.8 at scale 8 and 100.0 at
scale 1**. Use scale 8 for "did it crash", scale 1 for any number to believe.

## F10 — Whisper must have `language="en"` pinned
Unpinned, `small` returned **Devanagari** for `pub_05_turn1`: Whisper
language-detects per clip and drifts on noisy input. Pinning also skips the
detection pass and **roughly halves latency**.

## F11 — Transcripts must be folded to ASCII
Model output can contain anything; the harness prints actions to a console
that may be cp1252, and a `UnicodeEncodeError` **inside the harness** is
recorded against us. It crashed a probe script here before it could crash the
agent. `asr.py::_to_ascii` and `vision.py::_ascii` handle this.

## F12 — MP3 decoding is not free
This machine had **no** ffmpeg, `soundfile`, `librosa`, `av` or `pydub`, and
`torchaudio` 2.11 now requires `torchcodec`. Nothing could read the kit's
audio at all. `faster-whisper` brings `av`, solving it — one of the reasons it
is the chosen backend.

## F13 — CLIP cannot ground `pub_07`
See §3.8. Zero-shot CLIP never ranks HDMI first on any crop or prompt.
Whole-frame similarity cannot isolate "the one the user means".

## F14 — Slot-level confidence beats utterance-level
`pub_06` (act at 0.40) vs `pub_05_turn1` (ask at 0.38) is unresolvable at
sentence level. Gate on the **primary slot word's** probability.

## F15–F22 — see `notes/FINDINGS.md`
F15 `scenario_end` in the same instant as the last event (phantom final) ·
F16 PyPI torch is CUDA 13, the speech engine CUDA 12 · F17 the production
speech model takes a different M5 branch · F18 a 3B VLM cannot identify
pub_07's port · F19 claim patterns are keyed by public tool names · F20 a
model repository was renamed · F21 known gap: a self-repair whose trigger word
is lost · F22 the paraphrase pack's three extraction gaps.

---

# PART V — CURRENT MEASUREMENTS

All produced by commands in this repo, at `--time-scale 1` unless stated.
Updated 22 September on the pinned GPU stack (RTX 3060, torch 2.10.0+cu128,
large-v3-turbo on CUDA, vision-language model off).

```
OFFICIAL DRY RUN          python eval_submission.py . --time-scale 1 --reps 1
  plain average           97.9
  by modality             text=100.0   audio=100.0   visual=81.5
  WEIGHTED SCORE          97.4

PUBLIC + CONFORMANCE      python tools/quality.py            (32 scenarios)
  30 at 100.0 · pub_07 and conf_05 at 81.5 · lint: 0 flagged (speech + failed irreversible calls)

CHAOS - kit templates     python tools/chaos.py --n 60 --seed <s>
  seeds 2026, 7331        120/120 at 100.0
CHAOS - all 8 templates   python tools/chaos.py --templates all --n 120 --seed <s>
  seed 101 (diagnostic)   ours only: mean 88.1, 23/100 below 75  -> six general fixes
  seed 202 (fresh)        mean 99.7, 119/120 at 100, min 66.2 (fixed: "The Olive Room")
  seed 303 (fresh)        120/120 at 100.0 (after the typed-id fix, F24)
  protocol errors / crashes / abandoned calls: 0 / 0 / 0

KILL SWITCH               python tools/killswitch.py
  no ASR, no vision, no embeddings: 89.1 average, 0 crashes

SPEECH, both models       pub_05 and pub_06 at 100 with large-v3-turbo (GPU) AND base (CPU)
WAV INPUT                 pub_06 as 48k stereo / 44.1k / 16k WAV: >= 95 each (tests)

DISPATCHER                python tools/bench.py
  worst handler           0.469 ms   (budget 20 ms)

UNIT TESTS                python -m pytest tests/ -q
  373 passing
```

**Trajectory:** 55.0 (no task logic) → 82.6 (Phase 1b) → 86.0 (Phase 1c) →
97.9 (Phase 2) → 97.9 on the pinned GPU stack with the transcript defects,
calibration and paraphrase gaps fixed (Phase 3b). Kit baseline: ~57.

**The only unmet checkpoint anywhere** is naming what is in a photograph
(`final_grounded_hdmi`, and its `conf_05` twin). The 3B vision model was
measured and rejected (F18); a larger model needs a bigger GPU to test.

---

# PART VI — THE METHOD

How this codebase is built. Any agent continuing it should work this way.

## 6.1 Verify, never assume

Every claim in this document traces to a command. When the docs and
`harness/scorer.py` disagree, **the code wins** — several findings above exist
only because the prose was less specific than the implementation.

## 6.2 Test the test

A conformance scenario that passes whether or not the agent cancels is *worse*
than no scenario. `test_conformance_discriminates.py` proves ours separate a
cancelling from a non-cancelling agent. The NLG claim-guard test was verified
**non-vacuous** with a negative control.

## 6.3 Diagnose from traces, never from guesses

Every one of the 26 defects above was found by reading a trace or a test
failure, not by intuition. The workflow:

```
run scenario verbose  →  read the actual actions  →  name the root cause
      →  fix the cause  →  re-run  →  full regression  →  commit
```

## 6.4 Distinguish "the guard was right" from "the guard was wrong"

Defect #9: the emitter rejected `"FL-CHI-8AM, 08:00, $129"` as
non-substantive. The temptation is to relax the guard. **The guard was right;
the generator was wrong.** Fix the producer, not the check.

## 6.5 Prefer the general fix

Defect #5 could have been an alphabetical tiebreak hack. Instead,
satisfiability and reversibility became first-class ranking signals — which
then also fixed defect #23 (imperfect transcription) for free.

## 6.6 Kill your own ideas when the data says so

CLIP re-ranking (§3.8) was elegant and does not work. Tested across four crops
and two prompt templates *before* trusting it. Had it shipped on assumption,
`pub_07` would have asserted "USB Ports" about an HDMI port.

## 6.7 Honesty is a scoring strategy

Repeatedly, the truthful behaviour scored better than the clever one:
clarifying when we cannot hear (`pub_05` = 72.3 with **no** ASR); citing a
page without asserting its title (+18 on `pub_07`); refusing to retry a
state-modifying timeout.

## 6.8 Never chase noise

F8/F9 exist because a 20-point swing turned out to be timer jitter. Before
investigating any regression, confirm it reproduces.

## 6.9 Hard rules

- `harness/`, `scenarios/`, `docs/`, `run_local.py`, `eval_submission.py` —
  **never modify**.
- `duet/` never imports `app/` or `harness/`.
- No tool name, no city list, no `ground_truth` read inside `duet/`.
- **If a change improves the public set but worsens the chaos distribution, it
  is a hardcode and it gets reverted.**
- Commits: no AI attribution anywhere (verified: `git log --format='%B' | grep -i claude` is empty).

---

# PART VII — NEXT IMPLEMENTATION PLAN

> **Status, 22 September.** Phase 3b is complete (§3.10–3.14): 3b.1 quality,
> 3b.2 paraphrases (as conf_09–18), 3b.3 adversarial timing (as conf_19–23),
> 3b.4 chaos at volume (now with our own templates), and 3b.5's decision
> stands - no LLM planner. Of Phase 3c, the parts that did not need an A100 are
> done on the local RTX 3060: the pinned GPU stack runs, large-v3-turbo is
> calibrated (3c.3), and the VLM question is answered for the 3B model (3c.4:
> **no**, F18). What still needs a Linux GPU host is listed under IMMEDIATE
> ACTIONS at the end. The plan text below is kept as written for the record.

Granular, ordered, with acceptance criteria. Each task states *why*, *how to
verify*, and *what could go wrong*.

## PHASE 3b — Adversarial hardening & transcript quality
**Day 3 (21 Sep) · ~8 h · no GPU required**

### 3b.1 Transcript quality self-grading *(highest value, unmeasured)*

**Why.** The LLM quality multiplier is **×0.90–1.10** — a 20% swing on the
final score, larger than every remaining checkpoint combined. We have never
measured ours. `docs/SCORING.md` names the four dimensions (relevance,
truthfulness, naturalness, non-redundancy) and gives an illustrative prompt;
the real prompt and model are unpublished.

**Build** `tools/quality.py`:
- Load traces from `run_local.py --json` output.
- Extract the spoken transcript per scenario in order.
- Emit a report: utterance count, filler count, repeats (there must be none),
  average length, and any utterance matching a completion-claim pattern.
- Support `--grade` using an available LLM if one is configured; otherwise
  print the transcripts for manual reading. **Do not** make grading a
  dependency of the build.

**Verify.** Read every public + conformance transcript end to end, aloud.
Look for: generic fillers where a content-aware one was possible; repeated
sentence shapes; anything that over-claims.

**Acceptance.** No verbatim repeats anywhere (already enforced). No utterance
asserting an unverified result. Every interruption acknowledgment names the
new value. Subjectively natural on a read-through.

**Risk.** Over-tuning to an unpublished judge. Mitigation: optimise for
*truthfulness and relevance*, which are stable, not for cleverness.

### 3b.2 Paraphrase pack (`conf_09`…`conf_16`)

**Why.** `WALKTHROUGH.md` states hidden scenarios use paraphrases — *"I need
to get to Denver"*, *"any seats to Denver Friday"*, *"Denver, Friday, book
it"* — and warns keyword lists will not survive. Our chaos generator only
varies cities and timings, **not phrasing**, so paraphrase robustness is
currently **unmeasured**.

**Build.** Extend `tools/make_conformance.py` with ≥8 scenarios expressing the
*same* intent in structurally different ways:
- fragment style: *"Denver. Friday. Two people."*
- question style: *"Could you see what flies to Denver on Friday?"*
- indirect: *"I have a meeting in Denver Friday morning."*
- imperative + selector: *"Get me on whatever leaves Denver earliest."*
- negation: *"Not Friday — Saturday."*
- politeness padding: *"Sorry to bother you, but would you mind …"*
- disfluent text: *"I want to, uh, go to, um, Denver I think Friday"*
- multi-slot in one clause: *"Denver Friday for Priya window seat"*

**Verify.** `python run_local.py --scenario tests/conformance/conf_09*.json`.

**Acceptance.** ≥80 each; no scenario below 60. Any below 80 gets a *general*
fix (extraction or ranking), never a phrase added to a trigger list.

**Risk.** The temptation to add each failing phrase to `_CORRECTION_TRIGGERS`.
**That is a gazetteer by another name.** Prefer structural fixes; if a trigger
must be added, it must be ordinary English usable in any domain.

### 3b.3 Adversarial timing conformance (`conf_17`…`conf_22`)

**Why.** `WALKTHROUGH.md`: *"interruptions during chained calls, during a tool
that is about to return, twice in one scenario"*. We cover double
interruptions and interrupt-during-booking; we do **not** cover:

| Case | What it attacks |
|---|---|
| Interruption 50 ms *before* a tool completes | the cancel/complete race |
| Interruption *inside* the `SPEECH_FLOOR_MS` window | the deferred-utterance epoch guard |
| Interruption during the tail after `scenario_end` | `_tail_flush` |
| `tool_result` arriving for a call cancelled 10 ms earlier | `on_result` discard path |
| Three interruptions in 1.2 s | epoch churn, filler budget |
| Interruption while a *deferred commit* is pending | `_deferred_commit` epoch check |

**Verify.** Each must be *discriminating* — extend
`test_conformance_discriminates.py` so a naive probe fails and a correct one
passes, and assert the margin directly from the trace (as the existing ones do).

**Acceptance.** All ≥85. Zero duplicate state-modifying completions. Zero
`tool_abandoned`. Recovery = 1.00 wherever recovery is scored.

**Risk.** Writing a scenario whose timing does not actually force the failure.
Mitigation: compute margins explicitly in `_design_notes`, as §3.2 does.

### 3b.4 Chaos at volume

**Run.** `python tools/chaos.py --n 200 --seed 777 --out results/chaos_200.json`

**Acceptance.** mean ≥95, p10 ≥85, min ≥60, zero crashes/protocol
errors/abandoned calls. This number goes in the README as our generalisation
claim.

### 3b.5 Decision: the LLM planner

**Recommendation: do not build it.** Reasoning:
- `planner/rules.py` scores **100** on every text scenario, every conformance
  scenario bar visual, and 100.0 on three unseen chaos seeds.
- `notes/BUILD_PLAN.md` already designates it optional (*"rules-only is a
  valid shipping configuration"*).
- It adds a model to load (setup budget), non-determinism (the sealed run
  takes a median of 3), and latency, for gains we **cannot measure** before
  the deadline.
- The same hours spent on 3b.1–3b.3 improve things we *can* measure.

**Revisit only if** the paraphrase pack (3b.2) scores <80 after two rounds of
general fixes. In that case the LLM planner races the rules planner, first
valid result wins, hard timeout, rules are the floor — never a replacement.

## PHASE 3c — A100 validation session #1
**Day 4 (22 Sep) · ~4 h on the A100 · BLOCKED ON BOOKING**

Run in this order; each step gates the next.

### 3c.1 Environment parity
```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
pip install -r requirements.txt
python -m pytest tests/ -q
```
**Acceptance.** 266 tests pass on Linux + CUDA.

### 3c.2 Clock re-measurement *(decides whether the speech floor stays)*
```bash
python tools/clockcheck.py --reps 5
```
**If early deliveries = 0%,** the floor is unnecessary on the target platform.
Keep it anyway (it costs 20 ms of an 800 ms budget and protects against a
slower grading box), but **record the finding in F8**.
**If early deliveries > 0%,** the floor is load-bearing — document that.

### 3c.3 ASR at full size
```bash
DUET_ASR_MODEL=large-v3-turbo python run_local.py --all --time-scale 1 --quiet
```
**Measure.** Per-clip transcription latency; `setup()` cold-start wall time;
VRAM.
**Acceptance.** `pub_05`/`pub_06` stay at 100; per-clip <500 ms; cold start
<240 s (leaving headroom under the 300 s cap).
**Watch for.** Better transcription changing which branch fires — e.g. if
turn 1 of `pub_05` becomes intelligible, the agent may act instead of
clarifying, which would **fail** `asked_before_acting`. If so, the
`ASR_SLOT_CONFIDENCE` threshold needs re-tuning *upward*, because the clip is
designed to be ambiguous and clarifying is the intended behaviour.

### 3c.4 VLM — the last unmet checkpoint
```bash
python -c "from duet.perception.vision import VlmVision; import asyncio; print(asyncio.run(VlmVision().warm()))"
python run_local.py --scenario scenarios/pub_07_visual_port_lookup.json
```
**Acceptance.** `final_grounded_hdmi` passes; `final_not_hedging` **still**
passes (the answer must name HDMI *without* enumerating the other ports);
`conf_05` (no `device_hint`) also ≥95.
**If the default model underperforms,** try in order: a larger Qwen2.5-VL, a
dedicated OCR path (the label is *printed* — reading beats recognising), or
keep the honest-citation fallback at 81.5.
**Cold start risk.** A 3B VLM is ~7 GB. Measure the download. If `setup()`
approaches 240 s, pre-seed the HF cache in the Dockerfile (§5.2).

### 3c.5 Full official procedure
```bash
python eval_submission.py . --reps 3 --out results/a100_reps3.json
```
**Acceptance.** Weighted ≥97, all three reps within 5 points per scenario
(non-determinism check).

### 3c.6 Kill switch on the target platform
```bash
python tools/killswitch.py
```
**Acceptance.** ≥85, zero crashes. This is the insurance policy; verify it on
the machine that matters.

## PHASE 4 — The application
**Day 5 (23 Sep) · ~10 h · designated slip buffer**

**Cut this first if Phase 3 overruns.** The graded score never slips for the
demo.

### 4.1 `app/mic_adapter.py` — Adapter B
Microphone → VAD → endpointing → `user_speech_chunk` with `end_of_turn`.
Barge-in: speech detected during TTS truncates playback and emits an
`interruption` carrying the partial transcript. Outbound speech → local TTS.

**Constraint.** Reuses the *same* `duet` core. Adapter code only translates.
**Verify.** `tests/test_invariants.py::test_engine_does_not_import_the_app`
must still pass, and the public set must score identically with `app/`'s
dependencies uninstalled.

### 4.2 `app/server.py` + `app/static/` — Adapter C, the judge sandbox
Split screen: free text + mic + a prominent **BARGE IN** button + a
tool-manifest editor; live timeline with in-flight tool bars **visibly cut**
by cancels, slot diffs, latency counter; and a **commit log** showing each
epoch as a transaction with rollbacks marked — M1 made visible.

### 4.3 `app/demo_env.py` — Bring Your Own Tools
A smart-home manifest in exactly `mock_env`'s shape (`list_rooms`,
`set_climate`, `set_temperature`, `run_diagnostic`). The **unmodified** agent
handles it zero-shot. Demo beat: *"the grader gave it flights; here is a
manifest I wrote this morning for a fridge — same binary."*

**Acceptance.** Human can interrupt mid-sentence; barge-in truncates TTS
<300 ms; sandbox renders a live cancel; smart-home manifest works with zero
changes to `duet/`; **public score unchanged**.

## PHASE 5 — Packaging
**Day 6 (24 Sep) · ~10 h · includes A100 session #2**

### 5.1 `README.md` (root)
Architecture, the six mechanisms with literature anchors, reproducible setup,
**the chaos distribution**, the degradation ladder, how to run all three
adapters. Must let a judge reproduce our numbers from a clean clone.

### 5.2 `Dockerfile`
Required by FAQ Q17. **Pre-seed the HF model cache** so cold start is
predictable and the 300 s cap is never at risk. Verify: build from clean
clone, run the public set inside the container.

### 5.3 Demo video (≤5 min)
| Time | Content |
|---|---|
| 0:00–0:30 | The problem, shown not told |
| 0:30–1:30 | Live barge-in, real voice, real cancellation |
| 1:30–2:30 | The commit log — M1 underneath |
| 2:30–3:30 | Bring Your Own Tools, manifest written live |
| 3:30–4:30 | Multimodal: camera grounding + accessibility self-repair |
| 4:30–5:00 | Results: chaos distribution, kill-switch, the thesis |

### 5.4 Deck (`CollegeName_TeamName.pdf`)
Problem → the gap (speech vs action) → six mechanisms with anchors →
architecture → results → Bixby positioning → "taken further as a PRISM
worklet" (FAQ Q24 says the jury weighs it).

**Include the CLIP negative result.** A team that shows what it tested and
rejected reads as more rigorous than one that only shows wins.

### 5.5 A100 session #2
Final `eval_submission.py . --reps 3`. Docker build + run. Cold-start timing
with the final model set.

## PHASE 6 — Freeze & submit
**Day 7 (25 Sep) · code freeze at 12:00 noon**

- [ ] `python -m pytest tests/ -q` — all green
- [ ] `python eval_submission.py . --reps 3` — clean verdict
- [ ] `python tools/chaos.py --n 200 --seed <fresh>` — no regression
- [ ] `python tools/killswitch.py` — degrades gracefully
- [ ] Checklist against `WALKTHROUGH.md` §6 (*"mistakes that cost the most points"*)
- [ ] Checklist against `docs/SUBMISSION.md` pre-submit list
- [ ] `submission.yaml` team name is real, entry point spelled correctly
- [ ] No secrets; `git log --format='%B' | grep -i claude` returns nothing
- [ ] Tag and push:
```bash
git tag -a PRISM_GENAI_HACKATHON_Y2026 -m "PRISM Gen AI Hackathon Y2026 Final Submission"
git push origin PRISM_GENAI_HACKATHON_Y2026
```
- [ ] **Verify the tagged commit contains every referenced artifact** (video
      link, deck, README). *The tagged commit is what is judged.*
- [ ] Submit with ≥4 h to spare

---

# PART VIII — RISK REGISTER

| # | Risk | Severity | Status | Response |
|---|---|---|---|---|
| R1 | Blocking call in the dispatcher | Critical | **Mitigated** | Invariant 1; worst 0.469 ms |
| R2 | Network/API blocked in eval env | Critical | **Eliminated** | Zero network dependency; all models from public checkpoints |
| R3 | Model unavailable on grading box | High | **Mitigated** | Kill switch: 89.1 with no models |
| R4 | Model download exceeds 300 s cap | Medium | **Retired** | Organizers: download time is not charged to setup(); Dockerfile pre-seeds models anyway |
| R5 | No GPU validation host | High | **Open** | Local RTX 3060 now runs the pinned GPU stack; Docker build + 3-rep run still need a Linux GPU host (A100 or Kaggle) |
| R6 | Hardcoding creeps in | High | **Mitigated** | Grep invariants + auto-revert rule; chaos now uses our own templates too |
| R7 | Quality multiplier drags score | Med | **Mitigated** | Phase 3b.1: transcript lint, 0 flagged across 32 scenarios |
| R8 | Paraphrase brittleness | Med | **Mitigated** | conf_09–18 at 100; randomized paraphrase template in chaos |
| R9 | Clock artifact on Linux | Low | **Open** | Re-measure with tools/clockcheck.py on the Linux host |
| R10 | Packaging error at submission | Critical | **Mitigated** | Dry run passes; pins tested; Dockerfile written (not yet built) |
| R11 | App work regresses the score | Med | **Mitigated** | Import isolation test + re-run after every app commit |
| R12 | Team name placeholder ships | Low | **Closed** | `submission.yaml`: `SRM_SE7EN` |
| R13 | GPU speech stack fails at inference on the grading box | High | **Mitigated** | torch 2.10.0 (CUDA 12) pinned; trial transcription in setup() with CPU fallback (F16) |
| R14 | A model repository changes after the deadline | Med | **Mitigated** | Every model pinned by repo + commit (F20) |
| R15 | Vision model names the wrong component | Med | **Mitigated** | Vision-model reading off by default until it passes vision_bench (F18) |

---

# PART IX — FILE MAP

```
E:\HACKATHONPRISM\                    repo root == submission root
├── PROJECT.md                        ← this file
├── README.md                         (Phase 5.1 — not yet written)
├── Dockerfile                        (Phase 5.2 — not yet written)
├── submission.yaml                   ⚠ team name is a placeholder
├── requirements.txt                  graded deps only
├── requirements-app.txt              mic/sandbox deps, excluded from grading
│
├── agent/agent.py           156 L    ParticipantAgent shell + kit BaselineAgent
│
├── duet/                   5805 L    THE ENGINE
│   ├── config.py            156      every tunable, [SCORER] vs [OURS]
│   ├── contract.py          257      vendored protocol + safety rules
│   ├── state.py             272      M1 epochs, M3 provenance, M6 undo
│   ├── coordinator.py       430      M1 invalidation, M2 ledger + gate
│   ├── tools.py             650      schema-driven selection/binding/validation
│   ├── fastpath.py          617      repair classification, extraction
│   ├── nlg.py               471      deterministic non-repeating speech
│   ├── emitter.py           248      Invariant 2 — the single queue write
│   ├── runtime.py           888      Invariant 1 — the dispatcher
│   ├── telemetry.py         100      ring log + watchdog
│   ├── perception/
│   │   ├── base.py          279      interfaces, null backends, caching
│   │   ├── asr.py           221      faster-whisper, M5 value_confidence
│   │   ├── vision.py        196      VLM (GPU only, refuses CPU)
│   │   └── embed.py         153      CLIP embed + visual re-ranking
│   └── planner/rules.py     678      deterministic planner, chaining
│
├── tests/                  3821 L    (with tools/)
│   ├── conformance/*.json    8       our scenarios, generated
│   ├── probes.py                     naive vs cancelling fixtures
│   ├── test_invariants.py            the build gate (7 invariants)
│   ├── test_contract_equivalence.py  differential vs live harness
│   ├── test_conformance_discriminates.py  testing the tests
│   └── test_{state,emitter,nlg,tools,coordinator,fastpath,perception}.py
│
├── tools/
│   ├── bench.py                      Invariant 1 monitor
│   ├── chaos.py                      generalisation harness
│   ├── clockcheck.py                 harness clock drift (F8)
│   ├── killswitch.py                 degradation test (Invariant 7)
│   └── make_conformance.py           conformance generator
│
├── notes/                            analysis, plan, findings
├── results/                          scored run history (gitignored JSON)
└── [KIT — UNCHANGED] harness/ scenarios/ docs/ audio/ frames/
                      run_local.py eval_submission.py KIT_README.md WALKTHROUGH.md
```

## Commands

```bash
# daily
python run_local.py --all --time-scale 1 --quiet --agent agent.agent:ParticipantAgent
python -m pytest tests/ -q

# before believing any number
python tools/clockcheck.py --reps 3

# generalisation (USE A SEED YOU HAVE NOT TUNED AGAINST)
python tools/chaos.py --n 60 --seed <fresh>

# insurance
python tools/killswitch.py

# the official procedure
python eval_submission.py . --time-scale 1 --reps 3

# regenerate conformance after editing the generator
python tools/make_conformance.py
```

## Environment switches

| Variable | Effect |
|---|---|
| `DUET_DEBUG=1` | mirror internal log to stderr |
| `DUET_STRICT=1` | guard violations raise instead of degrade (tests) |
| `DUET_NO_ASR/VISION/EMBED=1` | force the degradation ladder |
| `DUET_ASR_MODEL` | GPU Whisper model (default `large-v3-turbo`) |
| `DUET_ASR_MODEL_CPU` | CPU Whisper model (default `base`) |
| `DUET_VLM_MODEL` | vision model (default `Qwen/Qwen2.5-VL-3B-Instruct`) |
| `DUET_VLM_ON_CPU` | allow the VLM on CPU (slow; testing only) |
| `DUET_CLIP_MODEL` | embedding model (default `clip-ViT-B-32`) |

---

# PART X — REFERENCES

**Official:** `docs/PROTOCOL.md`, `docs/TOOLS.md`, `docs/SCORING.md`,
`docs/SUBMISSION.md`, `WALKTHROUGH.md`, `harness/scorer.py` (authoritative),
`harness/mock_env.py`, `notes/reference/` (theme guide, FAQ).
Queries: prism@samsung.com

**Full-duplex & interruption**
- [Full-Duplex-Bench v3 — tool use under disfluency](https://arxiv.org/pdf/2604.04847) → M3
- [EchoChain — state-update reasoning under interruptions](https://arxiv.org/pdf/2604.16456) → M1
- [Full-Duplex-Bench v1.5 — overlap handling](https://arxiv.org/pdf/2507.23159) → acceptance gates
- [Testing full-duplex agents after GPT-Live](https://roark.ai/blog/testing-full-duplex-voice-agents-gpt-live) → conformance design

**Fast/slow execution & speculation**
- [RelayS2S — dual-path speculative generation](https://arxiv.org/pdf/2603.23346) → M4
- [Act While Thinking — speculative tool execution](https://arxiv.org/html/2603.18897v1) → M2
- [Speculative tool calling for voice](https://getstream.io/blog/speculative-tool-calling-voice/) → M2 gate

**Models**
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [Open-source STT benchmarks 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Gemini API models](https://ai.google.dev/gemini-api/docs/models) — evaluated and **rejected** (firewall risk, FAQ Q32)

**Positioning**
- [Samsung relaunches Bixby, One UI 8.5](https://www.androidheadlines.com/2026/02/samsung-bixby-one-ui-8-5-conversational-ai-update-perplexity-launched.html)
- [Bixby on 2026 smart home appliances](https://www.androidheadlines.com/2026/03/samsung-bixby-upgrade-smart-home-appliances-2026.html)

---

## IMMEDIATE ACTIONS

**Blocking, owner = team:**
1. **A Linux GPU host for one session** (A100 slot or a Kaggle GPU notebook):
   `docker build` + `docker run --gpus all duet` (the Dockerfile is written but
   has never been built - there is no Docker on the dev laptop), then
   `tools/clockcheck.py` (F8/R9), and optionally `tools/vision_bench.py --model
   Qwen/Qwen2.5-VL-7B-Instruct --variants` to decide whether a larger vision
   model clears the bar (F18).
2. **Send the follow-up to the organizers** (draft in
   `notes/ORGANIZER_CLARIFICATIONS.md`): CUDA 12.x + cuDNN 9, how download time
   is excluded, Linux/Python 3.12, and whether the 15 Oct demo may use app code
   added after the tag.

**Next engineering tasks:** deck (`SRM_SE7EN.pdf`), a trace-timeline viewer for
the demo video (Phase 4, cut down), the video itself, then Phase 6.
