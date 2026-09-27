"""H9 schema fuzzer (notes/SPRINT_PLAN.md).

Random VALID manifests (1-6 invented tools, never the kit's own names) times
generated utterances (task + distractor), run through the real harness
in-process exactly like tests/test_invariants.py does. Everything is seeded
with stdlib `random` only - no hypothesis, no other dependency - so every
scenario here is reproducible from its seed alone (see tests/fuzz_common.py).

Properties P1-P7 are each their own test, parametrized over seeds. Where a
property fails on the CURRENT engine, the property is NOT weakened: the
exact failing seeds are marked `xfail(strict=False)` with a file:line root
cause and the SPRINT_PLAN item that owns the fix, so the case stays visible
and flips to XPASS the moment the fix lands. The failing seeds below were
found by actually running this generator (not guessed) - see the H9 report
for the full table.

Runs with DUET_NO_ASR=1 DUET_NO_EMBED=1 DUET_NO_VISION=1 (set in
fuzz_common.py) and time_scale=8, so no model loads and no GPU is touched -
H9 owns no models. Only this file and test_fuzz_events.py are run by this
agent; the full suite and tools/gate.py are the orchestrator's.
"""

from __future__ import annotations

import functools
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fuzz_common as fc  # noqa: E402

MIXED_N = 90
DISTRACTOR_N = 50
RECOVERY_N = 30

# ---------------------------------------------------------------------------
# Calibrated failure allow-lists.
#
# Determined empirically: generate MIXED_N / DISTRACTOR_N scenarios at these
# exact counts (generation is deterministic per seed - anyone re-running this
# file reproduces the identical sets) and record which seeds violate the
# property on commit 4d08c2b + the 23 Sep sprint-plan merge, before H1/H3
# land. Do NOT add a seed here without having actually seen it fail; do NOT
# remove one without the underlying fix landing (it will then XPASS and
# strict=False keeps the suite green either way, but an XPASS is the signal
# to delete the entry).
# ---------------------------------------------------------------------------
P3_XFAIL_SEEDS = {0, 4, 6, 26, 27, 35, 42, 48, 49, 51, 63, 68, 72, 75, 85}
P6_XFAIL_SEEDS = {0, 2, 5, 7, 11, 12, 15, 16, 18, 20, 22, 23, 24, 25, 26, 28,
                  29, 30, 31, 32, 36, 37, 38, 39, 42, 43, 49}

P3_REASON = (
    "duet/tools.py ArgSpec.role() (~line 144-166) special-cases only enum "
    "and type=='number' before falling back to keyword matching; a boolean "
    "argument whose name/description matches no ROLE hint defaults to "
    "ROLE_TEXT, and ToolRegistry._value_for (~line 605-630) then hands it "
    "the ENTIRE utterance as a fallback query value. Root cause matches "
    "notes/SPRINT_PLAN.md H3 verbatim ('tools.py:626-629 ... including "
    "optional ones and non-text types'). Owned by H-B (H3, tools.py "
    "build_args/_value_for)."
)
P6_REASON = (
    "duet/planner/rules.py Planner.plan_turn (~line 393) screens negative "
    "routing with only duet/fastpath.py::looks_like_greeting (a fixed list "
    "of exact phrases) and looks_like_capability_question (a fixed list of "
    "~12 substrings); a generic conversational act not on either list "
    "(an aside, a hold, a social close) falls through to "
    "ToolRegistry.rank()/text_sink() (duet/tools.py ~line 441-553), which "
    "either extracts a capitalised word as a place/person or routes the "
    "whole sentence to the read-only free-text sink. Matches "
    "notes/SPRINT_PLAN.md H1 symptoms verbatim ('Hmm, let me think' -> "
    "flight search to 'Hmm'; 'I'm just talking to my brother' -> flight "
    "search to 'Brother'). Owned by H-A (H1, duet/router.py - not yet "
    "landed on this branch)."
)


def _xfail_params(n, xfail_seeds, reason):
    out = []
    for s in range(n):
        if s in xfail_seeds:
            out.append(pytest.param(
                s, marks=pytest.mark.xfail(strict=False, reason=reason)))
        else:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# cached scenario runs - each seed's manifest is generated and run through
# the harness exactly once, then every property test below reuses the trace.
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def _mixed(seed):
    scenario, meta = fc.make_mixed_scenario(seed)
    trace = fc.run_inprocess(scenario)
    return scenario, meta, trace


@functools.lru_cache(maxsize=None)
def _distractor(seed):
    scenario, meta = fc.make_distractor_scenario(seed)
    trace = fc.run_inprocess(scenario)
    return scenario, meta, trace


@functools.lru_cache(maxsize=None)
def _recovery(seed):
    scenario, meta = fc.make_recovery_scenario(seed)
    trace = fc.run_inprocess(scenario)
    return scenario, meta, trace


# ---------------------------------------------------------------------------
# P1 - no agent_crash, no protocol_error
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(MIXED_N))
def test_p1_no_crash_no_protocol_error(seed):
    _scenario, _meta, trace = _mixed(seed)
    assert not fc.crashes(trace), (seed, fc.crashes(trace))
    assert not fc.protocol_errors(trace), (seed, fc.protocol_errors(trace))


# ---------------------------------------------------------------------------
# P2 - every tool_call names a tool in the manifest and validates
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(MIXED_N))
def test_p2_tool_calls_validate_against_manifest(seed):
    scenario, _meta, trace = _mixed(seed)
    problems = fc.validate_all_tool_calls(scenario, trace)
    assert not problems, (seed, problems)


# ---------------------------------------------------------------------------
# P3 - no fallback leakage: no non-free-text argument receives the whole
# utterance verbatim
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", _xfail_params(MIXED_N, P3_XFAIL_SEEDS, P3_REASON))
def test_p3_no_fallback_leakage(seed):
    scenario, meta, trace = _mixed(seed)
    turns = fc.turn_texts_from_events(scenario["events"])
    violations = fc.find_fallback_leakage(scenario, trace, turns)
    assert not violations, (seed, meta["focal_tool"], violations)


# ---------------------------------------------------------------------------
# P4 - no duplicate state-modifying completion with identical args, and no
# state-modifying call retried (identical args) after a timeout
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(MIXED_N))
def test_p4_no_duplicate_or_blind_retry(seed):
    scenario, _meta, trace = _mixed(seed)
    dups = fc.find_duplicate_state_modifying(scenario, trace)
    assert not dups, (seed, "duplicate state-modifying completions", dups)
    retries = fc.find_bad_timeout_retries(scenario, trace)
    assert not retries, (seed, "state-modifying call retried after timeout", retries)


# ---------------------------------------------------------------------------
# P5 - every final_response carries a state_snapshot; no verbatim repeated
# filler; fillers <= 4 per scenario
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(MIXED_N))
def test_p5_snapshot_and_filler_hygiene(seed):
    _scenario, _meta, trace = _mixed(seed)
    missing = fc.find_finals_without_snapshot(trace)
    assert not missing, (seed, "final_response without state_snapshot", missing)
    repeats = fc.find_repeated_fillers(trace)
    assert not repeats, (seed, "verbatim-repeated filler", repeats)
    fillers = fc.filler_texts(trace)
    assert len(fillers) <= 4, (seed, "too many fillers", fillers)


# ---------------------------------------------------------------------------
# P6 - distractor-only scenarios (greeting/thanks/small talk/off-topic) call
# no tool
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", _xfail_params(DISTRACTOR_N, P6_XFAIL_SEEDS, P6_REASON))
def test_p6_distractor_only_calls_no_tool(seed):
    scenario, meta, trace = _distractor(seed)
    assert not fc.crashes(trace) and not fc.protocol_errors(trace)
    calls = fc.tool_calls(trace)
    assert not calls, (seed, meta["phrases"],
                       [(c["api_name"], c["args"]) for c in calls])


# ---------------------------------------------------------------------------
# P7 - after an interruption that changes a value, no call with the old
# value is issued later and any pending stale call is cancelled
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(RECOVERY_N))
def test_p7_no_stale_call_after_interruption(seed):
    _scenario, meta, trace = _recovery(seed)
    assert not fc.crashes(trace) and not fc.protocol_errors(trace)
    violations = fc.check_recovery(meta, trace)
    assert not violations, (seed, meta, violations)


# ---------------------------------------------------------------------------
# meta: the generator itself must actually exercise the closed type set
# (docs/TOOLS.md section 1 point 3) - a cheap sanity check, no extra harness
# runs (reuses the _mixed cache the P1-P5 tests already populated).
# ---------------------------------------------------------------------------
def test_generator_covers_the_closed_schema_type_set():
    seen_types = set()
    saw_required = saw_optional = False
    saw_missing_desc = saw_enum = saw_nested_object = False
    for seed in range(MIXED_N):
        scenario, _meta, _trace = _mixed(seed)
        for spec in scenario["tool_manifest"].values():
            for _name, aspec in spec.get("args", {}).items():
                seen_types.add(aspec.get("type"))
                if aspec.get("required"):
                    saw_required = True
                else:
                    saw_optional = True
                if not aspec.get("description"):
                    saw_missing_desc = True
                if aspec.get("enum"):
                    saw_enum = True
                if aspec.get("type") == "object":
                    saw_nested_object = True
    assert seen_types >= {"string", "number", "boolean", "array", "object"}, seen_types
    assert saw_required and saw_optional
    assert saw_missing_desc and saw_enum and saw_nested_object
