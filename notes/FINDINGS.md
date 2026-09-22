# Findings

Non-obvious things we learned by reading `harness/scorer.py` and measuring the
environment, rather than by reading the prose docs. Each entry says what we
observed, why it matters, and what we did about it.

---

## F1. Category weights redistribute when a category is absent

`scorer.score_scenario` only includes categories the ground truth defines, then
rescales to 100. So the headline 40/35/15/10 applies only to a scenario with
both an interruption and a latency requirement.

| scenario shape | task | recovery | latency | safety |
|---|---|---|---|---|
| interruption + latency | 40.0 | 35.0 | 15.0 | 10.0 |
| **latency, no interruption** | **61.5** | - | **23.1** | **15.4** |
| no latency, no interruption | 80.0 | - | - | 20.0 |

**Consequence.** In a scenario without an interruption, speaking within 800ms
is worth 23 points and safety hygiene 15. Roughly 38 points per scenario are
available from engineering alone. Our Phase 0 agent, with no planner and no
tool calls at all, scored 55.0 on the public set on exactly this basis.

---

## F2. A state_snapshot may ride on any action, not just final_response

`_score_recovery` reads the latest snapshot after the interruption timestamp
from any action carrying one, and the harness copies `state_snapshot` into the
trace for every spoken action.

**Consequence.** Attaching the snapshot to the acknowledgment filler locks in
the state half of the recovery score at ~100ms, instead of waiting for the
final answer that may be seconds away. Our emitter attaches it to every spoken
action unconditionally.

Note the harness does NOT copy `state_snapshot` for `tool_call` or
`cancel_tool` trace entries, so attaching one there is dead weight.

---

## F3. Cancellation is free; a stale completion is not

`cancel_tool` on an already-finished call is logged as `cancel_noop` and
carries no penalty. A cancelled call never produces a `tool_completed` entry,
and the duplicate-detection rule counts completions.

**Consequence.** Two things follow. Cancel-on-doubt is strictly correct: the
costs are asymmetric, so the policy is too. And cancel-then-reissue is safe for
state-modifying work, because the cancelled attempt can never count as a
duplicate - which is what makes conf_04 (interruption during a booking)
solvable at all.

---

## F4. The scorer's claim check is looser than truthfulness requires

`_score_safety` skips an utterance entirely if it contains any word from
`FUTURE_GUARDS` anywhere in the string. So "Booked! I'll email you." escapes
the penalty while being untruthful.

**What we did.** Our guard is sentence-scoped and positional: a future marker
must precede the completion verb in the same sentence. Our marker set is a
strict subset of the scorer's guard list, which guarantees we can only ever be
stricter, never more permissive - asserted by a property test over 540
phrasings. The LLM quality multiplier grades truthfulness separately, so being
honest beyond the deterministic requirement is not wasted.

---

## F5. spoken_not_contains is plain substring matching with no guard logic

`pub_07` uses `spoken_not_contains` with no time window, so it applies to the
ENTIRE trace including fillers. A hidden scenario could plausibly use
`spoken_not_contains: ["booked"]` with `before_tool_completes`.

**Consequence.** Our honest promise "I'll get that booked now" would fail such
a checkpoint even though the scorer's claim check permits it. Robust phrasing
avoids the completion verb root entirely before the tool returns. Encoded as a
checkpoint in `conf_08` so the NLG is held to it.

---

## F6. The no-participation gate

An agent that neither speaks nor calls a tool scores a flat 0, regardless of
how many negative checkpoints it satisfies vacuously.

**Consequence.** Never end a scenario silent. Also the reason the kit's
BaselineAgent scores 0 rather than ~40 on both audio scenarios: it ignores
`user_audio_chunk` entirely.

---

## F7. Vacuous negative checkpoints inflate a do-nothing agent

`tool_not_called` and `no_tool_calls` pass when the agent calls nothing at all.
Our Phase 0 agent scored ~60 mean on the conformance suite while calling zero
tools.

**Consequence.** Conformance scores DROP when real tool use lands, and that is
healthy rather than alarming. Track the task subscore separately from the
total, or the signal is invisible.

---

## F8. Harness clock undershoot on Windows (measurement artifact)

`_score_latency` measures from the timestamp DECLARED in the scenario file, but
only considers actions logged at `t_ms >= ev_t` on the harness's own virtual
clock. If the harness delivers an event EARLY, an immediate response is logged
before the declared time and scored "never responded".

Observed directly: `pub_04` scored 76.9 under `--all` and 100.0 in isolation.
Its turn is declared at 100ms; the harness logged it at 94ms; our 10ms response
was recorded as no response at all.

Measured with `tools/clockcheck.py`, 51 event deliveries over 3 repetitions of
the public set at `--time-scale 1`:

```
delivered EARLY      : 9 / 51  (17.6%)
worst early delivery : -9.0 ms
mean drift           : +4 to +9 ms per scenario
```

Cause: `asyncio.sleep` can return early on Windows because of the ~15.6ms
timer granularity of the proactor event loop. The magnitude we see (up to 9ms)
is consistent with that.

**Decision: document, do not work around yet.** On Linux, asyncio sleeps
overshoot rather than undershoot, and official runs are Linux. Adding a
deliberate pre-speech delay would immunise us, but it costs an await between
deciding and speaking - which opens a window for an interruption to arrive
mid-decision - to fix a problem that may not exist on the target platform.

**Action.** Re-run `tools/clockcheck.py` on the A100 (Linux) during Phase 3
validation. If undershoot occurs there too, add a ~12ms guarded pre-speech
delay with an epoch check before emitting. Until then, treat local latency
numbers as carrying roughly 18% noise and do not chase small latency
regressions on this machine.

---

## F9. Time-scale distorts latency, not correctness

`--time-scale 8` speeds the scenario clock but not our compute, so our think
time looks 8x worse. Protocol errors, crashes and task checkpoints are
scale-invariant; latency is not.

**Consequence.** Use scale 8 for "did it crash", scale 1 for any number we
intend to believe. Our invariant tests run at scale 8 deliberately, because
what they assert does not depend on it.

---

*F10-F14 are summarised in PROJECT.md Part IV. Entries below were added on
22 September 2026 (Phases 3b.1-3b.4).*

## F15. scenario_end arrives in the same instant as the last event

`harness/runner.py` enqueues `scenario_end` immediately after the last scripted
event, with no gap. When that event triggers deferred work - a re-plan behind
the 20 ms speech floor, a transcription, a frame read - the agent saw "nothing
pending" and spoke the capabilities list as a final answer: "Wait, actually
make it New York" was answered with "I can help you search flights to, book a
specific, ...". It happened in 6 of 17 scenarios, all of which scored 100.

**Fix.** Every spawned task counts as outstanding work; the tail flush waits for
it. **Lesson.** The automated score cannot hear the agent; `tools/quality.py`
now lints every transcript.

## F16. PyPI torch >= 2.11 is CUDA 13; the speech engine is CUDA 12

`torch` 2.11-2.14 on PyPI depend on `nvidia-*-cu13`. CTranslate2 4.8.2 (under
faster-whisper) links `libcublas.so.12`. Unpinned, the grading box gets CUDA 13
torch and a speech engine with no cuBLAS 12 - and the failure appears only at
the first transcription: locally, `cublas64_12.dll is not found` surfaced on
inference, after the model had "loaded" successfully.

**Fix.** Pin `torch==2.10.0` (the last CUDA 12 build, which brings cuBLAS 12 and
cuDNN 9 as dependencies), make those pip libraries visible to CTranslate2
(`duet/perception/cuda.py`), and run a trial transcription in `setup()` with a
CPU fallback. Only the driver version now matters.

## F17. The production speech model takes a different M5 branch

Thresholds were tuned with `base` on CPU. `large-v3-turbo` on GPU hears
pub_05's deliberately indistinct first clip as "I broke off my head to Austin",
with Austin at 0.47 - a value below the bar, where `base` heard no city at all.
The agent then asked "Which place did you want?", which matches none of the
checkpoint's phrasings: pub_05 fell from 100 to 81.5. It also heard pub_06's
"New York" as New 0.70 / York 0.99, against a 0.65 bar.

**Fix.** A value heard at >= 0.35 is confirmed by name ("Sorry, did you say
Austin?") and a bare "yes" confirms it; the slot label comes from the canonical
slot ("city", not "place"); value confidence is the mean over the value's
words. Both models now score 100 on both audio scenarios.

## F18. A 3B vision-language model cannot identify pub_07's port

`tools/vision_bench.py`: Qwen2.5-VL-3B named the HDMI port "USB-C port" on the
full frame, a lower-half crop and the mirror image (1 of 4 variants right),
under three prompts - and invented a matching printed label ("TEXT: USB-C").
A wrong reading costs more than none (the USB page is retrieved and the answer
names USB: 81.5 -> about 66).

**Decision.** Vision-model reading is off by default (`DUET_VISION=1` enables
it) until a model passes the bench on real GPU hardware. The frame barrier and
an honest "I could not tell which one - pages 21, 23 and 25 cover the likely
candidates" answer ship instead.

## F19. Claim patterns are keyed by public tool names, not by meaning

`scorer.CLAIM_PATTERNS` attributes "reserved" and "booked" to `book_flight`.
A sentence about an unseen tool - "the table is reserved" - is therefore a
premature flight-booking claim whenever no flight has been booked. Our guard
mirrors the scorer and replaced the whole final answer with "Still on it."

**Fix.** Completed actions of tools whose natural past tense trips a pattern
are reported neutrally ("All set - that went through, reservation reference
RS-0042").

## F20. A pinned-by-name model repository was renamed

faster-whisper's built-in `large-v3-turbo` alias points at
`mobiuslabsgmbh/faster-whisper-large-v3-turbo`, which now only answers with a
redirect to `dropbox-dash/...`. Scoring happens after the deadline; every model
is now pinned by repository AND commit (`duet/perception/checkpoints.py`).

## F21. Known gap: a self-repair whose trigger word is lost

Found while writing the WAV test. With audio degraded enough that "Actually make
that New York" is heard as "Hathory Migrate, New York", no repair trigger is
detected and the abandoned city - backed by a preposition ("to Boston") - beats
the repaired one, which only has casing. **Not fixed**: the candidate fixes
("the last place mentioned wins") also change behaviour for legitimate
two-place sentences, and there is no evidence yet that the hidden audio is this
degraded.

## F22. The paraphrase pack found three extraction gaps

conf_09-conf_18 (fragments, "what flies to", "need to get to", a date
self-repair, apology padding, disfluent text, lowercase multi-word cities,
number words, an unseen state-modifying tool) scored 7/10 at 100 on first run.
All three failures were structural: an infinitive read as a destination (and
non-overlapping regex matches hiding the real one), clock times accepted as
places, and number words never reaching argument binding. After structural
fixes: 10/10 at 100, no phrase added to any trigger list.
