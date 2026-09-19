# Samsung PRISM GenAI Hackathon 3.0 — Theme 05: Interruptible Real-Time Agents
## Master Analysis, Architecture & Plan

> Working document. Everything here was verified against the actual participant kit
> (`participant-kit/`), the official FAQ (`Samsung_PRISM_GenAI_Hackathon_3_FAQ_v4.docx`),
> and `Theme 5_Guideee.pdf`. Where the docs and the code disagree, the code wins and
> that is noted.
>
> Last updated: 19 September 2026

---

## Part 0 — Decisions taken so far

| Decision | Choice | Rationale |
|---|---|---|
| Model stack | **Local-only, no network dependency** | FAQ Q32 only guarantees pypi.org + huggingface.co. A blocked API domain = ~0 across all 60 hidden scenarios with no feedback channel to detect it. 48 GB A6000 is guaranteed. |
| Dev hardware | RTX 3060 laptop (daily) → Kaggle/Colab (mid) → **DGX A100 (final validation)** | Build small, validate at real size. No training required anywhere in this project. |
| Demo scope | **Core + sandbox UI + live microphone adapter** | The product must be a real working application, not a test-suite harness. |
| Product philosophy | **Build a real application that happens to score well**, not an agent built around the test cases | Hidden set is re-skinned; hardcoding is manually reviewed and disqualifying. A genuinely general agent is both the safer score and the better demo. |

---

## Part 1 — What the problem actually is

### 1.1 The framing vs. the mechanism

The theme document sells this as a "voice-native assistant." That is the *motivation*. The
thing you actually build and are graded on is narrower:

> **A single Python class that reads dicts off an `asyncio.Queue` and writes dicts to another
> `asyncio.Queue`, on the harness's own event loop, scored entirely from a replay log.**

There is no microphone, no speaker, no TTS, no VAD, no wake word **in the graded path**.
`docs/PROTOCOL.md §7`: *"Python only. The evaluator imports your class in-process."* The theme
guide lists wake-word detection, voice synthesis tuning and UI design as explicitly out of scope.

**This does not mean those things are out of scope for the product** — see Part 6. It means
Samsung is grading the one layer they cannot get from an off-the-shelf component.

So the real problem statement is:

> **"Full-duplex is usually treated as a speech problem. Samsung is asking you to solve it as a
> concurrency and state-consistency problem."**

Your agent is the **coordination layer** underneath a voice stack, not the voice stack itself.

### 1.2 The concrete mechanism

```
                    ┌──────────────── harness event loop (ONE loop) ─────────────────┐
                    │                                                                │
  scenario.json ───►│  replay events at virtual timestamps ──► in_queue ──► YOUR run()│
                    │                                                           │    │
                    │  MockEnvironment (async, 0.6–3.0s delays) ◄── out_queue ◄──┘    │
                    │         │                                                      │
                    │         └──► tool_result back into in_queue                    │
                    │                                                                │
                    │  every event + action + completion ──► trace[] ──► scorer.py    │
                    └────────────────────────────────────────────────────────────────┘
```

Seven inbound event types, five outbound action types. That is the entire API surface.

| In | Out |
|---|---|
| `tool_manifest` (always first, t=0) | `filler_speech` |
| `user_speech_chunk` (text + `end_of_turn`) | `tool_call` (`call_id`, `api_name`, `args`) |
| `user_audio_chunk` (**raw MP3 path, no transcript**) | `cancel_tool` (`call_id`) |
| `video_frame` (**raw PNG path, no caption**) | `clarification_request` |
| `interruption` (user barged in) | `final_response` (+ mandatory top-level `state_snapshot`) |
| `tool_result` | |
| `scenario_end` (+6 s tail) | |

### 1.3 The rule that dominates everything

`PROTOCOL.md §5`: your `run()` shares the harness's event loop. A single blocking call freezes
the simulation — events get delivered late and bunched, tools stop progressing, and **the trace
blames you for things you did not do.**

The docs give a measured example: a 2-second sync call turned a 100-point `pub_02` run into 61,
recorded as *"re-issued a stale call after the interruption"* — because the interruption event was
still sitting in the queue when the agent emitted the call. Nothing in the trace reveals the real
cause.

This is not a gotcha. It *is* the exam.

---

## Part 2 — The scoring, precisely

Read from `harness/scorer.py`, not from the prose. Several things differ from the headline
numbers in ways that change strategy.

### 2.1 The weights are not 40/35/15/10

They are 40/35/15/10 **only when a scenario has both an interruption and a latency spec.**
Absent categories redistribute their weight:

| Scenario shape | task | recovery | latency | safety |
|---|---|---|---|---|
| interruption + latency | 40.0 | 35.0 | 15.0 | 10.0 |
| **latency, no interruption** | **61.5** | — | **23.1** | **15.4** |
| no latency, no interruption | 80.0 | — | — | 20.0 |

Confirmed against a live run — `pub_01` printed `task 61.5 / latency 23.1 / safety 15.4`.

**Consequence:** in every non-interruption scenario, speaking within 800 ms is worth **23 points**
and safety hygiene is worth **15**. That is ~38 points per scenario available from engineering
alone, with zero intelligence. Most teams will read "latency 15%" and under-invest.

### 2.2 Where the points actually live

Hidden set: ~60 scenarios, 50% text / 30% audio / 20% visual, with audio and visual at **×1.5**
and L3/L4 difficulty at ×1.25.

```
text    30 scenarios × 1.0 = 30.0  weight
audio   18 scenarios × 1.5 = 27.0  weight
visual  12 scenarios × 1.5 = 18.0  weight
                      total  75.0
```

**Multimodal is 45/75 = 60% of the final score.** Text is 40%.

The reference `BaselineAgent` scores **0.0** on both audio scenarios (it ignores
`user_audio_chunk` entirely) and 47.7 on the visual one. Measured: **49.9 overall at
`--time-scale 8`** (docs claim ~57 at scale 1; verified that `pub_04` alone goes 76.9 → 100.0 at
real time, which reconciles the difference).

> **The contest is won or lost on audio and vision, and the baseline does not even compete there.**

### 2.3 Exact scorer mechanics worth knowing

**Latency.** Only `filler_speech`, `clarification_request`, `final_response` stop the clock.
`tool_call` does **not**. Speech must be ≥3 chars and ≥50% alphabetic (`"..."` earns nothing).
The curve is linear 800 ms (full) → 2500 ms (zero), so a 1500 ms response still earns ~59%.

**Recovery (35 pts, two equally weighted halves).**

* *Stale work handled*: a violation is recorded if an invalidated call **completes >800 ms after
  the interruption without a cancel**, is **still pending at shutdown** (`tool_abandoned`), or is
  **re-issued with the old args after the interruption**.
* *State correct afterwards*: the **latest** `state_snapshot` at any point after the interrupt
  timestamp must carry the new value. No snapshot at all → automatic fail.

Two corollaries straight from the code:

1. `state_snapshot` may ride on **any** action, not just `final_response`. Attach it to the
   acknowledgment filler and the state half of recovery is locked in at ~100 ms.
2. Cancelling an already-finished call logs `cancel_noop` and is **not penalized**. A cancelled
   task never emits a `tool_completed` entry, and duplicate-detection counts *completions*, not
   emissions. Therefore **"blanket-cancel on interruption, then re-issue what is still valid"** is
   provably safe under this scorer.

**Safety** (starts at 1.0, deducted as fractions of the category):

| Violation | Deduction |
|---|---|
| duplicate successful state-modifying call, identical args | −0.5 each |
| filler beyond budget (default 4; `pub_03` sets 3) | −0.25 each |
| malformed action | −0.10 each (cap −0.5) |
| `final_response` without `state_snapshot` | −0.20 |
| claiming completion before the tool finished | −0.25 each (cap −0.5) |
| verbatim-repeated filler text | −0.15 each (cap −0.45) |

In a non-interruption scenario safety is worth 15.4 raw points, so **one filler over budget costs
~3.9 points** and two duplicate bookings zero the category.

**Premature-claim detection** is regex on spoken text (`\bbooked\b`,
`ticket (id|created|opened|filed)`, …) *unless* the utterance contains a future-guard word
(`will`, `'ll`, `let me`, `one moment`, `about to`, `now`, …). "I'll get that booked now" is fine;
"Booked!" before `book_flight` completes is −0.25.

**The no-participation gate.** An agent that neither speaks nor calls a tool scores a flat **0**,
regardless of how many negative checkpoints it vacuously satisfies. This is why `pub_05` and
`pub_06` are 0.0 for the baseline rather than ~40.

**`spoken_not_contains` with no window applies to the entire trace.** In `pub_07` the forbidden
words are `headphone`, `usb`, `ethernet` (weight 0.15). You cannot say "it's HDMI, not USB-C"
*anywhere* in the scenario, filler included.

**Quality multiplier.** The kit says ×0.90–1.10 (the older theme PDF says 0.80–1.20 — trust the
kit; it is newer and it is the code that ships). An LLM grades relevance / truthfulness /
naturalness / non-redundancy. Prompt and model unpublished. Text aimed at the grader scores
truthfulness 0.

---

## Part 3 — Scope, constraints, timeline

### 3.1 Hard boundaries

| Constraint | Value | Source |
|---|---|---|
| Runtime | Python 3.10–3.12, in-process import | PROTOCOL §7 |
| Wall clock | **300 s** per scenario (relaxed from 120 s) | FAQ Q33 |
| `setup()` | 300 s, off the clock, own cap | PROTOCOL §5.3 |
| GPU | **1× 48 GB A6000-class, guaranteed** | FAQ Q31 |
| Network | **not air-gapped**; pypi.org + huggingface.co reachable; *some domains blocked*; allowlisting must be requested in advance | FAQ Q32 |
| State | session-scoped only, fresh instance per scenario | Theme §6 |
| Submission | one final package, no leaderboard, no feedback during the event | SUBMISSION.md |

Two of these deserve alarm bells.

**The firewall (FAQ Q32) is an existential risk.** The kit encourages Gemini, but the FAQ only
*guarantees* pypi.org and huggingface.co. If the API domain is not allowlisted in the grading
environment, an API-dependent agent does not score badly — it scores near-zero across all 60
scenarios. Every call times out, every latency target misses, every task checkpoint fails. There
is **no leaderboard and no feedback**, so you would never find out until results day.

**You have a 48 GB GPU and a 300 s free warm-up.** Whisper-large-v3-turbo + a 3–4B VLM + a small
text model fits comfortably, and module-level caching means only the first scenario pays the load
cost. The organizers almost certainly provided the GPU because they expect local inference.

### 3.2 Timeline — the binding constraint

| Date | Milestone |
|---|---|
| 11 Sep 2026 | Launch |
| 16 Sep 2026 | Registration closed; kit released |
| **25 Sep 2026, 11:59 PM** | **Final submission** |
| 9 Oct 2026 | Top 15 announced |
| 15 Oct 2026 | **Live demo round** |
| 24 Oct 2026 | Final results |

Submission mechanics (FAQ Q17–Q21): public/shared GitHub repo, git tag
**`PRISM_GENAI_HACKATHON_Y2026`** on the final commit (*the tagged commit is what gets judged*),
README with reproducible setup + Docker, demo video ≤5 min, PPT/PDF named
`CollegeName_TeamName`.

```bash
git tag -a PRISM_GENAI_HACKATHON_Y2026 -m "PRISM Gen AI Hackathon Y2026 Final Submission"
git push origin PRISM_GENAI_HACKATHON_Y2026
```

### 3.3 The two scoreboards

**Scoreboard A — the automated hidden set.** 60 scenarios, `scorer.py`, run after the deadline,
3 reps, per-scenario median, weighted. Purely mechanical.

**Scoreboard B — the jury rubric** (FAQ Q22):

| Dimension | Weight |
|---|---|
| Working prototype & functionality | 30% |
| Technical depth & feasibility | 25% |
| Innovation & originality | 20% |
| Relevance to theme | 15% |
| Presentation & documentation | 10% |

Scoreboard A almost certainly feeds the 30% and part of the 25%. But **45% of the jury score —
innovation, relevance, presentation — is decided by the video, the deck, and the live demo.**
A team scoring 85 on the hidden set with a headless agent and a weak video can lose to a team
scoring 75 with a demo the jury can touch.

You need both, and they should be **the same codebase**.

---

## Part 4 — Architecture

### 4.1 The three-layer model, made concrete

```
                    ┌─────────────────────────────────────────────────┐
  in_queue ────────►│  DISPATCHER (run())                             │
                    │  pure sync logic, ZERO awaits on slow work      │
                    │  target: <20 ms per event, always               │
                    └───┬──────────────────┬───────────────┬──────────┘
                        │                  │               │
                ┌───────▼──────┐   ┌───────▼───────┐  ┌────▼─────────┐
                │ FAST PATH    │   │ COORDINATOR   │  │ SLOW PATH    │
                │ rules only   │   │ call registry │  │ asyncio tasks│
                │ • filler     │   │ • call_id map │  │ • ASR        │
                │ • ack + echo │   │ • cancel      │  │ • VLM / CLIP │
                │ • snapshot   │   │ • idempotency │  │ • LLM plan   │
                │ • clarify    │   │ • epoch/gen#  │  │ • arg build  │
                │  ~10 ms      │   │  ~1 ms        │  │  300–2000 ms │
                └──────────────┘   └───────────────┘  └──────────────┘
                        │                  │               │
                        └──────────────────┴───────────────┴──► out_queue
```

**The dispatcher never awaits anything slow.** It classifies the event, mutates state, emits a
fast-path utterance, and `asyncio.create_task`s the real work. Everything expensive happens in
tasks that write to `out_queue` when they finish. This one discipline is worth more points than
any model choice.

### 4.2 The coordination layer — the actual hard part

**1. Epoch / generation counter.** Every interruption or self-repair bumps `self.epoch`. Every
spawned task captures the epoch it was born under. On completion: `if my_epoch != self.epoch:
discard`. This is the clean, general solution to "never act on stale results" — it works
uniformly for tool results, ASR results, VLM results and LLM plans, and it does not require
enumerating what invalidates what.

**2. Call registry with invalidation policy.** Track
`call_id → {tool, args, kind, epoch, slots_depended_on}`. On interruption:

* cancel every in-flight call whose args depend on a slot that changed (rule-based, <50 ms)
* when uncertain, **cancel anyway** — cancellation is free under the scorer, a stale completion is not
* re-issue only what is still needed, and only after checking the idempotency ledger

**3. Idempotency ledger for state-modifying tools.** Key on `(tool, normalized_args)`. Before
emitting any `state_modifying` call: if that key is already `in_flight` or `committed`, refuse.
On a timeout from a state-modifying tool, **do not retry** — verify with a read-only call or ask
the user.

**4. Speculation policy, split by tool kind.**

| Tool kind | When to fire | Why |
|---|---|---|
| `read_only` | **immediately**, even on a partial turn | free to cancel, free to abandon, buys latency |
| `state_modifying` | only after end-of-turn + confidence gate | irreversible; every safety penalty lives here |

Firing read-only calls early buys the latency and task points. Holding state-modifying calls back
protects the safety and recovery score. The kit rewards exactly this asymmetry — and it is the
correct real-world behaviour, which makes it clean to defend in the deck.

### 4.3 Fast-path content-awareness without an LLM

`pub_02` has a checkpoint `ack_mentions_new_city`: a spoken action containing "new york" between
1900 and 3100 ms. The generated `search_interrupt` template uses a 1200 ms window. So the fast
path must **name the new value** in under ~800 ms, for a value it may never have seen.

The baseline cheats with a hardcoded 7-city gazetteer. That dies on hidden re-skins — the
walkthrough warns about it by name.

The general solution is a **repair-pattern extractor**: trigger phrases (`actually`, `make it`,
`wait`, `change to`, `instead`, `no, `, `I meant`, `scratch that`) followed by a noun-phrase grab,
plus a "what slot did the old value occupy" inference. Back it with a small local LLM (a 1–4B
model does slot extraction in 50–150 ms on GPU) and let whichever returns first win. This also
handles `pub_06` — a self-repair *inside a single turn*, where the abandoned value must never
reach a tool at all.

### 4.4 What each public scenario is really testing

| Scenario | Mechanic under test | Trap |
|---|---|---|
| `pub_01` | turn → tool → grounded answer | — |
| `pub_02` | cancel + re-plan + snapshot | snapshot must be post-interrupt |
| `pub_03` | chained call (`flight_search` → `book_flight`) | filler budget cut to 3; don't say "booked" early |
| `pub_04` | **negative routing** — no tool at all | over-eager tool calls lose 50% of task |
| `pub_05` | ASR ambiguity → **clarify, don't guess** | firing a tool before 4200 ms loses 0.25 |
| `pub_06` | intra-turn self-repair | the abandoned city must *never* be searched |
| `pub_07` | frame grounding + hybrid embedding | naming any other port anywhere costs 0.15 |
| `pub_08` | injected `timeout` → retry a read-only tool | needs ≥2 calls; also tests you keep talking |
| `pub_09` | **zero-shot tool from manifest schema** | ~10 hidden tools do this |

`pub_04` and `pub_09` predict hidden-set generalization best. `pub_04` says *don't force
everything into a tool*; `pub_09` says *your tool layer must be schema-driven, not name-driven*.
Build a generic `schema → arguments` compiler that reads `tool_manifest` and never mentions a tool
name in source.

### 4.5 A free win almost everyone will miss

In `pub_07` the `video_frame` arrives at **t=100 ms** and the question at **t=600 ms** — 500 ms of
advance notice.

Start the VLM and the CLIP embedding **the moment the frame arrives**, before you know what is
being asked. By the time "what is this port used for?" lands, you already know it is HDMI, and
your first utterance can be both instant and content-aware. Textbook speculative execution,
explicitly one of the theme's stated focus areas, and a beautiful timeline slide.

---

## Part 5 — The visual path (`pub_07`) in detail

Verified against `harness/mock_env.py`:

* **`_MANUAL_CORPUS[0]` is the headphone jack, and its keywords include `"port"`.** A query of
  `"what is this port used for"` matches `port` only, so pages 21/23/25/27/42 all score `hits=1`,
  and `scored.sort()` is stable → **headphone comes back first**. Parroting `pages[0]` fails both
  the HDMI checkpoint and the no-hedging one.
* **The embedding cannot rescue a bad query.** The boost is `if has_embedding and hits > 0:
  hits += 1`, applied uniformly to every page that already matched. It cannot promote a page the
  text query missed.
* **Latency anchors on the speech, not the frame** — `respond_to` is `event_index: 1` (t=600 ms).
* **A query of `"hdmi output external display"` ranks page 27 first**: `hdmi` + `external display`
  = 2 hits vs. QN90's 1. Once vision identifies the port, the lookup is deterministic.

**Checkpoint map:**

| Checkpoint | Weight | Passes when |
|---|---|---|
| `lookup_manual` called with `query` | 0.30 | easy |
| also passes `image_embedding` | 0.15 | presence only — but do it properly with CLIP |
| final contains `hdmi` | 0.30 | requires actually seeing the frame |
| never says headphone / usb / ethernet | 0.15 | **entire trace, no time window** |
| final contains `manual` or `page` | 0.10 | cite the tool result |

**Implementation notes.**

* Cite **only** the page matching the port vision identified. Summarising `pages[0:3]` leaks the
  string "USB Ports" and loses `final_not_hedging`.
* `device_model` must be one of `QN90 | S24 | WF45 | GENERIC` or the call returns `invalid_args`.
  Hidden frames may arrive with **no `device_hint`** — default to `GENERIC` or omit the arg.
* A dummy `[0.0] * 16` embedding would technically pass the 0.15 checkpoint (the mock only tests
  `isinstance(list) and non-empty`). **Do not.** `sentence-transformers` is already available,
  CLIP ViT-B/32 is ~0.6 GB and ~15 lines, and SCORING.md's anti-gaming section says degenerate
  passes are manually reviewed.
* This is **not a computer-vision research track.** No detector training, no CLIP fine-tuning.
  It is "look at the frame, then call `lookup_manual`." Budget 0.5–1 day.

---

## Part 6 — What we are actually building (the product)

### 6.1 The TTS/VAD confusion, resolved

> *"TTS/VAD is excluded from the graded scope — is that not the base of the entire project?"*

**Excluded from *grading*, not from the *product*.**

Samsung is comparing ~200 teams. They cannot compare microphones — mic quality, room noise and
sound cards are not the skill being tested, and they already own world-class on-device ASR and
TTS. So the harness **replaces the ears and the mouth with a deterministic simulator** and grades
only the brain, because the brain is the part nobody has solved.

```
  REAL PRODUCT                        WHAT THE GRADER SIMULATES
  ────────────                        ─────────────────────────
  microphone  ──┐                     scenario.json timestamps
  VAD/endpoint  ├─► events      ⇔     replayed into in_queue
  ASR         ──┘                     (text chunks, MP3 paths, PNG paths)

  ┌──────────────────────────────────────────────────────┐
  │  THE BRAIN  ←── this is what is graded, 100% of it    │
  └──────────────────────────────────────────────────────┘

  actions ──► TTS ──► speaker    ⇔     actions logged to trace[]
```

Samsung is saying: *"assume the ears and mouth work; prove the brain survives interruption."*

**We are building the ears and mouth anyway**, because a fully functional application is the
goal and because the live demo needs them. They just are not where the points are.

### 6.2 The three adapters — what they are and why

**The simple version.** Imagine the brain as a person in a sealed room with a letterbox (in) and
an outbox (out). They read letters describing what the user just did, and write letters saying
what to do. They have no idea whether those letters come from a test proctor, a real microphone,
or a web page. **An adapter is a translator between the outside world and that letterbox.**

```
                    ┌──────────────────────────────┐
                    │   ParticipantAgent (core)    │
                    │   in_queue ──► out_queue     │
                    │   ← this is the graded thing │
                    └──────────────────────────────┘
                       ▲            ▲            ▲
        ┌──────────────┘            │            └──────────────┐
  Adapter A: HARNESS         Adapter B: LIVE MIC       Adapter C: SANDBOX
  (Samsung wrote it)         (we write it)             (we write it)
  scenario.json → events     mic → VAD → ASR → events  web UI → events
  actions → trace log        actions → TTS → speaker   actions → live timeline
```

**Adapter A — the harness.** Already written by Samsung (`harness/runner.py`). We never touch it.
It reads a scenario JSON, pushes events at virtual timestamps, executes mock tools, records a
trace. This is what produces the score.

**Adapter B — live microphone.** Real product surface. Microphone → VAD (detect speech start/stop)
→ streaming ASR → `user_speech_chunk` events with `end_of_turn`. When the user starts talking
while the agent is speaking, the adapter emits an `interruption` event and truncates TTS playback.
Outbound: `filler_speech` / `final_response` → TTS → speaker.

**Adapter C — sandbox UI.** A web page: a judge types or speaks anything, clicks "barge in"
whenever they like, and watches the live trace — tool calls as in-flight bars that visibly get
**cut** when a cancel lands, the `state_snapshot` diffing in real time, a latency counter, and the
scorer's verdict computed live.

**What this solves — four things:**

1. **You physically cannot submit a mic-coupled agent.** The grader imports a class and hands it
   two queues. There is no microphone in that environment. If the brain is wired directly to
   PyAudio, it cannot be submitted at all.
2. **You cannot demo a harness-coupled agent.** If the brain only understands scenario JSON, you
   have no product — just a test runner.
3. **Without adapters you end up with two codebases that drift.** The classic hackathon death: the
   demo works, the submission scores 40, and a fix in one never reaches the other. One core + thin
   adapters means every bug fix helps both, and the thing the judge touches on 15 October is
   *byte-identical* to the thing that was scored.
4. **It is the honesty argument.** "The agent you are talking to right now is the exact class the
   grader imported" is the strongest possible answer to "did you hardcode the test cases?"

The adapters are thin — a few hundred lines each. The core is where all the work is.

### 6.3 What the application actually is

**An interruption-safe voice agent runtime** — a hands-free assistant that takes *real actions*
(book, cancel, create, look up, control) and that you can interrupt, correct, or redirect at any
moment without it doing something wrong, expensive, or twice.

Three properties define it:

* **It never goes silent.** Sub-800 ms acknowledgment on every turn, regardless of how slow the
  real work is.
* **It can be interrupted mid-action.** In-flight work is cancelled, state is corrected, plans are
  rebuilt — and results of abandoned work are never spoken.
* **It never does the irreversible thing twice.** Every state-changing action passes through an
  idempotency ledger.

**Crucially: the agent learns its capabilities from a manifest at runtime.** Samsung's grader
hands it flight search, booking and support tickets. In our demo we hand it a completely different
manifest — smart-home device control, alarms, media, appliance diagnostics — written by us, or
live by a judge, and **the same unmodified agent handles it zero-shot.** That is the proof that
this is a general runtime rather than a test-suite fitting exercise, and it is exactly the skill
`pub_09` is designed to measure.

### 6.4 Detailed end-user scenarios

#### Example 1 — In-car, hands-free (the theme's own use case)

> **User:** "Navigate to the Koramangala office and find me parking near—"
> **Agent (180 ms):** "Getting directions to the Koramangala office…"
> *[route calculation in flight, 2.4 s; parking search queued]*
> **User (at 1.1 s, cutting in):** "No wait, go to the airport instead, I'm late."
> **Agent (210 ms):** "Switching to the airport — rerouting now."
> *[route(Koramangala) CANCELLED at 240 ms · parking search never fired · state.destination = airport]*
> **Agent (2.9 s):** "Airport in 47 minutes via the Elevated Expressway. Traffic is light."

**What breaks without this system:** the assistant finishes calculating the Koramangala route,
announces it, and *then* handles the airport request — or worse, starts navigating to the wrong
place. The user is on the road and cannot look at a screen to fix it.

**Why it is hard:** the route calculation was already 1.1 s into a 2.4 s call. It must die, its
result must never be spoken, and the new route must start immediately — while the acknowledgment
lands in ~200 ms so the driver knows they were heard.

#### Example 2 — Booking change mid-flight (double-booking prevention)

> **User:** "Book the 8 AM Delhi flight for Ananya."
> **Agent (150 ms):** "I'll get that booked now."
> *[book_flight(FL-DEL-8AM, Ananya) in flight]*
> **User (at 700 ms):** "Actually make it the 2 PM one."
> **Agent (190 ms):** "Switching to the 2 PM — I haven't confirmed anything yet."
> *[book_flight(8AM) CANCELLED — never committed · ledger records nothing]*
> *[book_flight(FL-DEL-2PM, Ananya) fires after confidence gate]*
> **Agent (1.8 s):** "Done — 2 PM to Delhi for Ananya, booking BK-0042."

**What breaks without this system:** two bookings, two charges, one very annoyed user. Or the
assistant says "Booked!" for the 8 AM before the API confirmed, and now it has lied.

**Why it is hard:** `book_flight` is irreversible. The system must know the difference between
"cancel work that hasn't committed" and "cancel work that has" — and it must never *claim* the
booking happened before the API says so. Note the agent's phrasing: *"I haven't confirmed
anything yet"* is both truthful and reassuring.

#### Example 3 — Field troubleshooting with the camera

> *[User points phone at the side of a laptop]*
> **User:** "What is this port used for?"
> **Agent (170 ms):** "Let me take a look at that."
> *[frame was already being analysed from the moment it arrived — speculative]*
> *[lookup_manual(query="hdmi output external display", image_embedding=[…], device_model="GENERIC")]*
> **Agent (1.9 s):** "That's the HDMI output — it connects the laptop to an external display or
> projector. Page 27 of the manual covers it."
> **User (cutting in):** "No, the small one next to it."
> **Agent (200 ms):** "Got it — the one to the right."
> *[previous lookup cancelled · new frame region · new lookup]*

**What breaks without this system:** the assistant answers about the wrong port and keeps talking
over the correction. Or it hedges — "that could be USB, HDMI or Ethernet" — which is useless to
someone holding a cable.

**Why it is hard:** the frame is *context*, not a question. "This" and "the small one next to it"
are deictic references that only resolve against the image. And the agent must commit to one
answer rather than hedging.

#### Example 4 — Accessibility: disfluency and self-repair

> **User:** "I need to um… I need to cancel my… no sorry, I need to *change* my appointment, the
> one on… on Thursday. No, Friday."
> **Agent (200 ms):** "Sure — changing your Friday appointment."
> *[`cancel_appointment` NEVER fires · date slot resolves to Friday, not Thursday]*
> **Agent (1.4 s):** "Your Friday 3 PM with Dr. Rao. What time would you like instead?"

**What breaks without this system:** the assistant hears "cancel" and cancels. Or it books
Thursday. For a user with a speech impairment, a stutter, or simply an elderly user who thinks
aloud, a system that acts on the *first* thing it hears is actively dangerous.

**Why it is hard:** there are two self-repairs inside one turn, and the abandoned values
("cancel", "Thursday") must never reach a tool. This is `pub_06` generalised — and it is the
theme's stated accessibility use case.

#### Example 5 — Smart home / device control (the Bixby-adjacent demo)

> **User:** "Turn on the AC in the living room and set it to—"
> **Agent (160 ms):** "Turning on the living room AC…"
> **User (cutting in):** "—actually the bedroom one, and make it 22."
> **Agent (190 ms):** "Switching to the bedroom, 22 degrees."
> *[living-room command cancelled before commit · bedroom AC set to 22]*
> **Agent (1.1 s):** "Bedroom AC is on at 22 degrees."

**Why this one matters for the jury:** the tool manifest here is *nothing* the agent has ever
seen — `set_climate`, `list_rooms`, `set_temperature`, written by us (or by a judge, live).
The agent reads the schema, extracts the arguments, and handles the interruption identically. It
demonstrates in fifteen seconds that this is a general runtime, not a flight-booking bot.

### 6.5 How this maps back to the test suite

Nothing above was designed around the scenarios. But notice:

| Product behaviour | Test suite consequence |
|---|---|
| never goes silent | latency 15–23 pts/scenario |
| cancel in-flight work on correction | recovery 17.5 pts |
| correct state after correction | recovery 17.5 pts |
| never claim before commit | safety, quality multiplier |
| never do the irreversible thing twice | safety 10–15 pts |
| learn tools from a manifest | `pub_09` + ~10 hidden tools |
| clarify when perception is unsure | `pub_05` |
| honour self-repairs | `pub_06` |
| ground answers in what the camera saw | `pub_07` |
| don't force everything into a tool | `pub_04` |

**The test suite is a measurement of the product, not a specification of it.** Build the product
correctly and the score follows. That is the right way round, and it is also the only way to
survive re-skinned hidden scenarios.

---

## Part 7 — Positioning: Bixby, GPT-Live, and the gap

**What is true as of now.**

Bixby received a genuine overhaul in One UI 8.5 (February 2026) — LLM-backed, natural multi-step
commands, Perplexity-powered search, conversation history — and it is rolling out to 2026 smart
home appliances (Family Hub fridges, robot vacuums, air conditioners, washing machines). It is a
real agent with real device control. What it is **not** is full-duplex: still listen → think →
speak, and you cannot correct it mid-execution.

OpenAI shipped **GPT-Live** in July 2026 — genuinely full-duplex, listens and speaks
simultaneously, emits backchannels, and *delegates hard questions to a larger model while the
conversation continues.* That last part is literally the fast/slow split this theme describes.

**The gap.** GPT-Live solved full-duplex at the *speech* layer. Nobody has cleanly solved it at
the *action* layer. When a full-duplex model has already fired `book_flight(Boston)` and you say
"wait, New York" 200 ms later, the speech layer yielding the floor gracefully does not un-book the
flight. The research community is only now building benchmarks for this — **Full-Duplex-Bench v3**
explicitly targets *tool use under real-world disfluency*, and **EchoChain** benchmarks
*state-update reasoning under interruptions*. Both appeared in 2026.

**The thesis:**

> **"Full-duplex speech is solved. Full-duplex *action* is not. Bixby already controls the device —
> it just cannot survive you changing your mind while it's acting. We built the coordination layer
> that makes that safe."**

This is true, defensible in front of a Samsung jury, maps onto the theme's own use cases, and
costs nothing extra to build — it is just how you frame what the harness already forces you to
build.

**Caution:** do not claim you built a better Bixby. Claim you built the **execution-safety
substrate** a device agent needs in order to go full-duplex. More modest, much stronger.

---

## Part 8 — The model stack: all three options

### 8.1 Option A — Gemini-first (rejected)

**Shape.** `setup()` constructs an `AsyncClient` and warms it. Every hard decision is an API
round-trip: `user_audio_chunk` → upload MP3 with a transcription prompt; `video_frame` → send PNG;
intent/slot/tool-arg synthesis → JSON-mode call against a Flash model. ~250 lines, no VRAM, no
model loading.

Current-generation pieces: **Gemini 3.5 Transcribe** (non-streaming, word-level timestamps and
confidence — usable for the `pub_05` gate), **Gemini 3.5 Transcribe Live** (streaming),
**Gemini 3.1 Flash Live Preview** (audio-to-audio), **Gemini 3 / 3.8 Flash** (reasoning + vision).
Note `gemini-omni-flash-preview` is deprecated **30 September 2026** — five days after the
deadline.

**Attractive because:** zero-shot quality on an unseen tool schema is the hardest generalization
task in the hidden set, and a frontier model is simply better at it than a 3B local model. Same
for an indistinct city name in noisy audio. `setup()` becomes near-instant, sidestepping the
300 s cold-start risk entirely.

**Rejected because** of four compounding, unobservable failure modes:

1. **The firewall.** FAQ Q32 guarantees pypi.org and huggingface.co only. "Some domains are
   blocked"; additions must be requested *in advance*. If the API domain is not allowlisted →
   ~0 on all 60 scenarios.
2. **Burst rate limits.** 60 scenarios × 3 reps × several calls each = many hundreds of requests
   back-to-back. Throttling does not fail loudly; it adds seconds, silently zeroing every latency
   score and pushing work past the 6 s tail.
3. **Quota / key state at grading time**, days after you last touched it.
4. **Non-determinism**, even at temperature 0.

With no feedback channel, this is an unhedged bet on someone else's network config.

### 8.2 Option B — Hybrid (understood, not chosen)

**Shape.** Three capability interfaces — `ASRBackend`, `VisionBackend`, `PlannerBackend` — each
with a local and a remote implementation, plus a policy layer.

The right policy is **hedging with local as the floor**, not "try remote, fall back on failure":

```
launch local  ──┐
                ├──► first VALID result wins; remote preferred if it
launch remote ──┘    lands before the deadline and passes the validator
```

Local *always* returns, so the worst case is exactly Option C. Remote only ever improves things.

**Three things that make it ~1.5× the work:**

* **Connect timeouts are the whole game.** A blocked host does not fail fast — an unroutable TCP
  connect can hang 30–75 s by default. Get this wrong and a firewalled environment does not
  degrade gracefully, it *freezes the event loop's useful work on every scenario*. Explicit
  connect timeout 300–500 ms, total timeout under 2 s.
* **A circuit breaker is mandatory.** After N consecutive remote failures, disable remote for the
  rest of the process. Module-level state persists across scenarios in one process, so the breaker
  trips once and stays tripped — you pay the penalty twice, not sixty times.
* **A validator, not just a parser.** A remote result that parses as JSON but names a tool absent
  from the manifest is *worse* than the local answer. Schema-check every remote result against the
  live `tool_manifest` before letting it win.

**Gain:** mostly audio and zero-shot tools. Plus "graceful degradation under network partition"
is a real technical-depth talking point.
**Cost:** two prompt sets, two code paths, and a failure mode you must test by blackholing DNS —
a test most teams write and never run. On a seven-day clock, that is a day you do not have.

### 8.3 Option C — Local-only (chosen)

**Why it is right:** you are optimising for the worst case you cannot observe. Nothing in the
hidden set requires frontier-scale reasoning; it requires *correct orchestration*, which is code,
not model scale.

| Role | Candidate | VRAM (fp16) | Why |
|---|---|---|---|
| ASR | faster-whisper `large-v3-turbo`, int8 | ~1.5 GB | CTranslate2; ~100–300 ms on a 1–2 s clip. Exposes `avg_logprob`, `no_speech_prob`, word probabilities — **this is the `pub_05` confidence gate** |
| Vision **+** planning | one VLM, e.g. Qwen2.5-VL-7B-Instruct (or 3B) | ~16 GB (~7 GB) | describes the frame *and* does intent/slot/tool-arg JSON. One model, one warm-up, one prompt style |
| Frame embedding | CLIP ViT-B/32 via `sentence-transformers` | ~0.6 GB | real `image_embedding` for the hybrid bonus |

~18 GB of 48. Start with the shared VLM — fewer moving parts beats marginal quality this week.

**Two details that decide whether this works:**

1. **Cold start.** A 16 GB download will exceed the 300 s `setup()` cap on the first scenario,
   scoring that scenario 0 (≈1.3% of weighted total). Module-level caching means only scenario #1
   pays. Mitigate with a 3B VLM (~7 GB) or by pre-seeding the HF cache in the Docker image.
2. **Determinism.** Greedy decoding, fixed seeds, `temperature=0`, int8 for ASR. The sealed run
   takes a median of 3, so jitter is survivable, but keep the distribution tight.

**There is nothing to train.** This is pure inference plus orchestration. The entire 35-point
recovery category and the 10-point safety category are ordinary Python with no model involved.

### 8.4 Dev hardware plan

| Tier | Hardware | Use |
|---|---|---|
| Daily | RTX 3060 laptop | faster-whisper small/medium int8, CLIP, 3B VLM in 4-bit. ~90% of dev time. |
| Mid | Kaggle (30 free GPU-h/week, 16 GB T4) / Colab | testing the real ASR model |
| Final | **DGX A100** | closest mirror of the 48 GB A6000. Full stack at real size, `eval_submission.py --reps 3 --time-scale 1`, cold-start timing. |

Two or three A100 sessions all week, not continuous access. **Book Day 4 and Day 6 now.**

---

## Part 9 — How judges and real people will test it

Three distinct audiences:

**1. The automated grader (after 25 Sep).** Runs `eval_submission.py` against ~60 hidden
scenarios at `time_scale=1.0`, 3 reps, per-scenario median, weighted. Your agent is imported
in-process. Nothing in the README matters here. What matters: it imports, it never blocks, it
never crashes, it handles a missing media file without dying, it degrades gracefully when a model
fails.

**2. The video + deck jury (Oct).** ≤5 minutes. They have seen 40 submissions. They will not read
your code.

**3. The live demo jury (15 Oct).** They will ask you to do something you did not plan for.

### What to build for #3

**Adapter C wins the room.** Browser page, split screen:

* left: judge types or speaks freely; a "barge in" button they can hit at any moment
* right: the live trace — events streaming in, actions streaming out, tool calls as in-flight bars
  that visibly **get cut** when a cancel lands, the `state_snapshot` diffing in real time, a
  running latency number
* bottom: the scorer's verdict, computed live

Then hand the judge the keyboard. *"Change your mind whenever you want."*

**Adapter B is the emotional payoff for the video.** A 30-second clip of a real human interrupting
a real speaking agent, and the agent handling it cleanly, is worth more than three minutes of
architecture diagrams.

**Also ship a "chaos mode":** generate scenarios with random cities, random tool manifests, random
interruption timings, injected failures. Run 200 of them, publish the score distribution in the
README. That is how you prove generalization to a jury worried about hardcoding —
`harness/scenario_gen.py` is the starting point.

---

## Part 10 — Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Network/API blocked in eval env** | catastrophic, silent, unrecoverable | local-first inference (decided) |
| **Blocking call in `run()`** | corrupts the trace, phantom violations | architectural discipline + a test asserting no dispatcher `await` exceeds N ms |
| Model download exceeds 300 s on first scenario | that scenario scores 0 | small stack; pre-warm; module-level cache |
| Hardcoding city/tool lists | fails re-skins, flagged in manual review | schema-driven tools, general slot extraction, test only on generated scenarios |
| Over-eager tool calls | `pub_04`-style negatives, safety deductions | explicit "no tool needed" route |
| Blanket-cancelling a *still-valid* call | loses task points on refinement-type interruptions | dependency-aware invalidation, not blind cancel-all |
| Multimodal left until day 4–5 | 60% of weighted score, highest schedule risk, discovered too late | thin end-to-end multimodal spike on day 2–3 |
| A100 access depends on a third party | blocks final validation | book the slots now |
| Only 7 days | everything | the sequencing below |

**Do not over-fit to the nine public scenarios.** They are worth zero points. Real development
signal comes from `scenario_gen.py` output and scenarios you write yourself for cases the kit does
*not* cover — double interruptions, retractions ("never mind"), full intent changes ("forget the
flight, my TV is broken"), interruptions during a chained booking, frames with no `device_hint`.

---

## Part 11 — Plan

Principle: **every day ends with a working, scoreable agent.** Never a half-migrated one.

| Day | Date | Deliverable | Target |
|---|---|---|---|
| 1 | 19 Sep | Core engine: dispatcher, epoch/generation, call registry, idempotency ledger, snapshot-on-every-action, schema→args compiler, fast-path repair extractor. No models. | text scenarios ~90; overall ~65 |
| 2 | 20 Sep | **Thin multimodal spike** (tiny models, 3060) to de-risk: ASR in `setup()`, acknowledge-then-transcribe, frame-on-arrival speculation, CLIP embedding. | `pub_05/06/07` off zero |
| 3 | 21 Sep | Deepen: real ASR confidence gate, VLM grounding, local planner for intent/slots/tool-args behind the fast path. Chaining, retry, unseen-tool hardening. | overall ~85 |
| 4 | 22 Sep | **A100 session.** Full-size stack, cold-start timing, `--reps 3 --time-scale 1`. Adversarial hardening: chaos generator, double interruptions, retractions, missing media. | distribution tightens, no crashes |
| 5 | 23 Sep | Adapter C (sandbox + live trace visualiser) and Adapter B (mic/VAD/TTS). Demo tool manifest (smart home) to prove zero-shot generality. | judge can improvise |
| 6 | 24 Sep | **A100 session.** README + Dockerfile + `submission.yaml` + ≤5 min video + deck. Full `eval_submission.py` dry run. | `VERDICT` clean |
| 7 | 25 Sep | Buffer, final run, tag `PRISM_GENAI_HACKATHON_Y2026`, push. | done with slack |

Day 1 is the highest-leverage day: the coordination layer holds the 35-point recovery category and
the 10-point safety category, and neither needs a model.

---

## References

### Official materials
* `participant-kit/` — README, WALKTHROUGH, `docs/PROTOCOL.md`, `docs/TOOLS.md`,
  `docs/SCORING.md`, `docs/SUBMISSION.md`, `harness/scorer.py`, `harness/runner.py`,
  `harness/mock_env.py`, `harness/scenario_gen.py`, `eval_submission.py`
* `Theme 5_Guideee.pdf` — Theme 05 guide v1.0.0
* `Samsung_PRISM_GenAI_Hackathon_3_FAQ_v4.docx` — incl. Theme 5 Q31–Q33 (GPU, firewall, wall clock)
* Queries: prism@samsung.com

### Research — full-duplex and interruption
* [Full-Duplex-Bench v3: Benchmarking Tool Use for Full-Duplex Voice Agents Under Real-World Disfluency](https://arxiv.org/pdf/2604.04847)
* [EchoChain: A Full-Duplex Benchmark for State-Update Reasoning Under Interruptions](https://arxiv.org/pdf/2604.16456)
* [Full-Duplex-Bench v1.5: Evaluating Overlap Handling for Full-Duplex Speech Models](https://arxiv.org/pdf/2507.23159)
* [EVA-Bench: An End-to-end Framework for Evaluating Voice Agents](https://arxiv.org/pdf/2605.13841)
* [The ICASSP 2026 HumDial Challenge: Benchmarking Human-like Spoken Dialogue Systems](https://arxiv.org/pdf/2601.05564)
* [Testing full-duplex voice agents after GPT-Live](https://roark.ai/blog/testing-full-duplex-voice-agents-gpt-live)
* [Full-Duplex Voice Agents: Simultaneous Speech (2026)](https://www.evalgent.com/blog/full-duplex-voice-agents)
* [Real-Time vs Turn-Based Voice Agents 2026](https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture)

### Research — fast/slow execution and speculation
* [RelayS2S: A Dual-Path Speculative Generation for Real-Time Dialogue](https://arxiv.org/pdf/2603.23346)
* [Act While Thinking: Accelerating LLM Agents via Pattern-Aware Speculative Tool Execution](https://arxiv.org/html/2603.18897v1)
* [VoiceAgentRAG: Solving the RAG Latency Bottleneck Using Dual-Agent Architectures](https://arxiv.org/html/2603.02206v1)
* [Speculative Tool Calling for Voice](https://getstream.io/blog/speculative-tool-calling-voice/)
* [Voice agent latency optimization](https://elevenlabs.io/blog/voice-agent-latency-optimization)

### Models and tooling
* [Gemini API — Models](https://ai.google.dev/gemini-api/docs/models) · [Changelog](https://ai.google.dev/gemini-api/docs/changelog) · [Gemini 3.1 Flash Live Preview](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-live-preview)
* [New Gemini audio models for developers](https://blog.google/innovation-and-ai/technology/developers-tools/build-real-time-voice-applications-gemini-audio/)
* [Best open-source STT model in 2026 (benchmarks)](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
* [whisper.cpp vs faster-whisper 2026](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026)
* [Self-hosting faster-whisper on GPU](https://www.spheron.network/blog/faster-whisper-gpu-cloud-production-deployment-guide/)
* [WhisperKit: On-device Real-time ASR](https://arxiv.org/html/2507.10860v1)

### Context — Bixby and the competitive landscape
* [Samsung Officially Relaunches Bixby: An AI Agent for Your Galaxy Phone](https://www.androidheadlines.com/2026/02/samsung-bixby-one-ui-8-5-conversational-ai-update-perplexity-launched.html)
* [Talk to Your Fridge: New Bixby AI Update Hits Samsung Smart Home Appliances](https://www.androidheadlines.com/2026/03/samsung-bixby-upgrade-smart-home-appliances-2026.html)
* [Samsung Releases Upgraded Bixby To More Galaxy Phones (Forbes)](https://www.forbes.com/sites/jaymcgregor/2026/03/28/samsung-galaxy-bixby-perplexity-one-ui-85-update/)
* [How Samsung's new Bixby makes life easier for Galaxy S26 users](https://www.sammobile.com/news/how-samsung-new-bixby-makes-life-easier-for-galaxy-s26-users/)
