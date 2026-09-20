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
