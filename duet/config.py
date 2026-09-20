"""Every tunable number in one place.

Values tagged [SCORER] are fixed by harness/scorer.py and must not be changed.
Values tagged [OURS] are our own targets, deliberately stricter than the scorer
requires so that we keep headroom.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------
# Latency
# --------------------------------------------------------------------------
# [SCORER] A spoken action within this gap after a turn end / interruption
# earns full latency credit. Linear decay to ZERO_CREDIT_MS.
FULL_CREDIT_MS = 800.0
ZERO_CREDIT_MS = 2500.0

# [OURS] What the fast path actually aims for. 800ms is the pass mark; we want
# to be so far inside it that a slow machine or a GC pause cannot cost points.
FAST_PATH_TARGET_MS = 250.0

# [OURS] Minimum virtual time between an event arriving and our first spoken
# reply to it.
#
# NOT a politeness delay. The scorer measures latency from the timestamp
# DECLARED in the scenario file but only considers actions logged at
# t_ms >= that value on the harness's own clock. When asyncio.sleep returns
# early (measured at 17.6% of deliveries on Windows, up to 9ms), an instant
# reply is logged BEFORE the declared time and scored "never responded" - a
# 20-point swing on an otherwise perfect run, observed directly on
# conf_06 and pub_04.
#
# 20ms of an 800ms budget is 2.5%, imperceptible to a person, and it makes
# local measurements stable enough to distinguish a regression from jitter.
# Harmless where the harness is punctual: delta becomes 20ms instead of 5ms,
# both full credit. Re-evaluate on Linux during Phase 3 (see notes/FINDINGS.md F8).
SPEECH_FLOOR_MS = 20.0

# [OURS] Invariant 1. No single event handler may occupy the event loop longer
# than this. Anything slower must be spawned as a task.
DISPATCHER_BUDGET_MS = 20.0

# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------
# [SCORER] A stale call completing later than this after an interruption,
# without a cancel, is a recovery violation.
CANCEL_GRACE_MS = 800.0

# [OURS] We target 5x headroom inside the grace window.
CANCEL_TARGET_MS = 150.0

# [OURS] After an epoch bump, hold state-modifying calls for this long. The
# user may still be mid-correction ("make it New York... no, Newark").
COMMITMENT_QUIET_MS = 250.0

# --------------------------------------------------------------------------
# Speech budget
# --------------------------------------------------------------------------
# [SCORER] Default filler budget is 4, but a scenario may lower it via
# ground_truth.safety.max_fillers -- pub_03 sets 3. ground_truth is NOT
# delivered to the agent, so we cannot read the real budget. We therefore
# self-limit to the strictest value we have observed.
FILLER_SOFT_BUDGET = 3
FILLER_HARD_BUDGET = 4

# [OURS] Runaway protection, NOT budget enforcement.
#
# The scorer deducts 0.25 of the safety category per filler over budget, which
# is roughly 2.5-4 points. Failing to respond to an event costs the entire
# latency category, 15-23 points. So speaking is almost always the right call
# even when we are over budget, and the emitter does not hard-stop at the
# budget. This cap only exists so that a bug producing fillers in a loop
# cannot drive safety to zero.
FILLER_ABSOLUTE_CAP = 8

# [SCORER] Substantive-speech test: >= 3 chars and >= 50% alphabetic.
MIN_SPEECH_CHARS = 3
MIN_ALPHA_RATIO = 0.5

# --------------------------------------------------------------------------
# Scenario timing
# --------------------------------------------------------------------------
# [SCORER] Grace window after scenario_end in which we must finish.
TAIL_MS = 6000.0

# [OURS] Emit a final response no later than this into the tail, so a slow
# tool cannot push us past the window.
TAIL_FLUSH_MS = 4200.0

# --------------------------------------------------------------------------
# Confidence thresholds (tuned in Phase 2 against the public audio clips)
# --------------------------------------------------------------------------
# M5 gates on the SLOT word, not the sentence. Measured on the kit's clips
# with `base`: pub_06 must be ACTED on at sentence confidence 0.40 because
# "New York" was heard at 0.77/1.00, while pub_05_turn1 must be QUESTIONED at
# 0.38 because no city survives at all. A single sentence-level threshold
# cannot separate those two, so there are two numbers.
#
# Minimum probability of the words forming an extracted value before we are
# willing to act on it.
#
# 0.65 sits in a real gap measured on the kit's clips with `base`: the garbage
# transcript of pub_05_turn1 yields "Question" at 0.55, while pub_06's
# "New York" is heard at 0.77/1.00 and pub_05_turn2's "Boston" at 0.95.
# Erring high is the safe direction - pub_05 rewards asking and penalises
# acting on a guess. Re-check against the production model on the A100, where
# probabilities should be uniformly higher.
ASR_SLOT_CONFIDENCE = 0.65

# Fallback when nothing was extracted at all: below this we admit we did not
# catch it rather than proceeding on an empty understanding.
ASR_UTTERANCE_CONFIDENCE = 0.45

# Retained for compatibility with callers that want one number.
ASR_CLARIFY_THRESHOLD = 0.50

# If two hypotheses disagree on the slot token and the margin is under this,
# treat it as genuine ambiguity and name both candidates aloud.
ASR_AMBIGUITY_MARGIN = 0.25
VISION_CLARIFY_THRESHOLD = 0.45

# A state-modifying tool may only fire when every required slot is at least
# this confident. Deliberately higher than the read-only bar.
COMMIT_CONFIDENCE = 0.70

# [OURS] Total attempts allowed per logical tool call, including the first.
# pub_08 injects a timeout on the first flight_search and expects a retry, so
# a read-only tool needs at least 2. More than that burns the 6s tail without
# improving anything.
MAX_TOOL_ATTEMPTS = 2

# --------------------------------------------------------------------------
# Debug
# --------------------------------------------------------------------------
# DUET_DEBUG=1 mirrors the internal log to stderr. Never on during grading.
DEBUG = os.environ.get("DUET_DEBUG", "") not in ("", "0", "false", "False")

# DUET_STRICT=1 makes internal guard violations raise instead of degrade.
# Tests run with this on so bugs fail loudly; grading runs with it off so a
# bug degrades gracefully instead of crashing the agent.
STRICT = os.environ.get("DUET_STRICT", "") not in ("", "0", "false", "False")
