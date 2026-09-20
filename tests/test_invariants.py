"""The build gate: architectural invariants, enforced mechanically.

These are the rules that cannot be left to discipline, because violating them
is cheap to do and expensive to discover. Each one corresponds to a documented
failure mode:

  Invariant 1  no handler blocks the event loop     (PROTOCOL.md section 5.1)
  Invariant 2  one queue write in the whole engine  (every safety rule at once)
  Invariant 3  no synchronous I/O anywhere in duet/ (freezes the simulation)
  Invariant 4  no tool name hardcoded in duet/      (~10 hidden tools)
  Invariant 5  no scenario-derived cheating         (README rules; disqualifying)
  Invariant 6  duet/ never imports app/             (graded env lacks app deps)
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet import config, telemetry  # noqa: E402
from harness.runner import EvaluationHarness  # noqa: E402


def _duet_sources():
    paths = glob.glob(os.path.join(ROOT, "duet", "**", "*.py"), recursive=True)
    assert paths, "no duet sources found"
    out = {}
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            out[os.path.relpath(path, ROOT)] = fh.read()
    return out


def _strip_comments_and_docstrings(src: str) -> str:
    """Crude but adequate: remove # comments and triple-quoted blocks so that
    prose mentioning a tool name does not trip the source greps."""
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    src = re.sub(r"#[^\n]*", "", src)
    return src


# ------------------------------------------------------------ Invariant 2
def test_exactly_one_queue_write_in_the_engine():
    """Every outbound action must pass through Emitter._send, which is where
    snapshots, validation, repeat-guarding and claim-guarding live. A second
    queue write anywhere is a bypass of all of them at once."""
    hits = []
    for rel, src in _duet_sources().items():
        code = _strip_comments_and_docstrings(src)
        for match in re.finditer(r"out_q\w*\.put(?:_nowait)?\s*\(", code):
            hits.append((rel, match.group(0)))
        for match in re.finditer(r"_out_q\w*\.put(?:_nowait)?\s*\(", code):
            hits.append((rel, match.group(0)))
    # both patterns match the same single call site, so dedupe by file+offset
    unique_files = {rel for rel, _ in hits}
    assert unique_files == {os.path.join("duet", "emitter.py")}, hits
    emitter_src = _strip_comments_and_docstrings(
        _duet_sources()[os.path.join("duet", "emitter.py")])
    count = len(re.findall(r"\.put_nowait\s*\(", emitter_src))
    assert count == 1, "expected exactly one put_nowait in emitter.py, found " + str(count)


# ------------------------------------------------------------ Invariant 3
def test_no_synchronous_io_in_the_engine():
    """A blocking call inside run() freezes the whole simulation: events are
    delivered late and bunched, in-flight tools stop progressing, and the
    scorer blames us for violations we did not commit."""
    banned = [
        (r"\btime\.sleep\s*\(", "time.sleep blocks the event loop"),
        (r"\brequests\.(get|post|put|delete)\s*\(", "requests is synchronous"),
        (r"\burllib\.request\.urlopen\s*\(", "urlopen is synchronous"),
        (r"\bhttpx\.(get|post)\s*\(", "use httpx.AsyncClient"),
        (r"\bsubprocess\.(run|call|check_output)\s*\(", "subprocess blocks"),
        (r"\basyncio\.run\s*\(", "asyncio.run inside the agent nests loops"),
        (r"\.result\s*\(\s*\)", "Future.result() blocks"),
        (r"loop\.run_until_complete", "run_until_complete blocks"),
    ]
    problems = []
    for rel, src in _duet_sources().items():
        code = _strip_comments_and_docstrings(src)
        for pattern, why in banned:
            if re.search(pattern, code):
                problems.append(rel + ": " + why)
    assert not problems, problems


# ------------------------------------------------------------ Invariant 4
def test_no_tool_names_hardcoded_in_the_engine():
    """Tools must be driven from the tool_manifest event. Roughly 10 hidden
    tools exist that we have never seen, and hidden scenarios re-skin the
    public ones. A tool name in engine code is a defect."""
    tool_names = [
        "flight_search", "book_flight", "cancel_booking",
        "lookup_manual", "create_support_ticket", "weather_lookup",
        "hotel_search", "rental_car_quote",
    ]
    problems = []
    for rel, src in _duet_sources().items():
        code = _strip_comments_and_docstrings(src)
        code = _strip_published_claim_table(code)
        for name in tool_names:
            if name in code:
                problems.append(rel + " references tool name " + repr(name))
    assert not problems, problems


def _strip_published_claim_table(code: str) -> str:
    """Remove CLAIM_PATTERNS_PUBLISHED before the tool-name grep.

    That dict is a verbatim mirror of scorer.CLAIM_PATTERNS, kept so
    tests/test_contract_equivalence.py can detect drift. It names tools, but
    it does not select them: it is a truthfulness guard, and unknown tools are
    covered by the generic completion pattern beside it. The rest of
    contract.py is still checked.
    """
    return re.sub(
        r"CLAIM_PATTERNS_PUBLISHED[^\n]*=\s*\{.*?\n\}",
        "CLAIM_PATTERNS_PUBLISHED = {}",
        code,
        flags=re.S,
    )


def test_no_city_gazetteer_in_the_engine():
    """The kit's baseline hardcodes 7 cities and dies on re-skinned scenarios.
    Slot values must be extracted structurally, never matched against a list."""
    cities = ["Boston", "Denver", "Seattle", "Chicago", "Miami", "Austin"]
    problems = []
    for rel, src in _duet_sources().items():
        code = _strip_comments_and_docstrings(src)
        found = [c for c in cities if re.search(r"\b" + c + r"\b", code)]
        if len(found) >= 3:
            problems.append(rel + " looks like a city gazetteer: " + repr(found))
    assert not problems, problems


# ------------------------------------------------------------ Invariant 5
def test_engine_never_reads_ground_truth_or_scenario_identity():
    """README rules: the agent sees only the event stream and tool results.
    Reading ground_truth is disqualifying, and branching on scenario_id or
    raw timestamps is the hardcoding the organizers review for."""
    # NB: "checkpoints" is deliberately NOT banned - EpochCheckpoint is our
    # undo mechanism (M6) and has nothing to do with scenario ground truth.
    # The word only ever appears inside ground_truth, which is banned below,
    # so nothing is lost by allowing it.
    banned = [r"ground_truth", r"scenario_id", r"_reference_text",
              r"_reference_image"]
    problems = []
    for rel, src in _duet_sources().items():
        code = _strip_comments_and_docstrings(src)
        for pattern in banned:
            if re.search(pattern, code):
                problems.append(rel + " references " + repr(pattern))
    assert not problems, problems


# ------------------------------------------------------------ Invariant 6
def test_engine_does_not_import_the_app_or_the_harness():
    """duet/ must import cleanly in an environment with neither app/'s
    dependencies (sounddevice, TTS, websockets) nor the grading harness."""
    problems = []
    for rel, src in _duet_sources().items():
        code = _strip_comments_and_docstrings(src)
        if re.search(r"^\s*(from|import)\s+app\b", code, re.M):
            problems.append(rel + " imports app/")
        if re.search(r"^\s*(from|import)\s+harness\b", code, re.M):
            problems.append(rel + " imports harness/")
    assert not problems, problems


# ------------------------------------------------------------ Invariant 1
def _run_scenario(path, time_scale=8.0):
    from agent.agent import ParticipantAgent

    with open(path, "r", encoding="utf-8") as fh:
        scenario = json.load(fh)

    async def go():
        h = EvaluationHarness(scenario, lambda a, b: ParticipantAgent(a, b),
                              time_scale=time_scale, verbose=False)
        await h.prepare()
        return await h.run()

    trace = asyncio.run(go())
    return scenario, trace, telemetry.timings(), telemetry.violations()


PUBLIC = sorted(glob.glob(os.path.join(ROOT, "scenarios", "*.json")))


@pytest.mark.parametrize("path", PUBLIC, ids=lambda p: os.path.basename(p)[:6])
def test_no_protocol_errors_or_crashes(path):
    """Every action we emit must be accepted by the harness, and run() must
    never raise. A protocol_error costs 0.10 safety each; an agent_crash ends
    our participation in the scenario."""
    original = config.STRICT
    config.STRICT = False
    try:
        _scenario, trace, _timings, _violations = _run_scenario(path)
    finally:
        config.STRICT = original

    errors = [e for e in trace if e.get("kind") == "protocol_error"]
    crashes = [e for e in trace if e.get("kind") == "agent_crash"]
    assert not errors, "protocol errors: " + repr(errors[:3])
    assert not crashes, "agent crashed: " + repr(crashes[:3])


@pytest.mark.parametrize("path", PUBLIC, ids=lambda p: os.path.basename(p)[:6])
def test_dispatcher_never_blocks_the_event_loop(path):
    """Invariant 1, measured rather than assumed."""
    original = config.STRICT
    config.STRICT = False
    try:
        _scenario, _trace, timings, violations = _run_scenario(path)
    finally:
        config.STRICT = original

    assert timings, "no handler timings recorded - watchdog not wired?"
    worst = max(t["ms"] for t in timings)
    assert not violations, (
        "dispatcher budget violations: " + repr(violations[:3]))
    assert worst <= config.DISPATCHER_BUDGET_MS, (
        "slowest handler " + str(round(worst, 2)) + "ms exceeds budget "
        + str(config.DISPATCHER_BUDGET_MS) + "ms")


@pytest.mark.parametrize("path", PUBLIC, ids=lambda p: os.path.basename(p)[:6])
def test_agent_always_participates(path):
    """The no-participation gate scores a flat 0 for an agent that neither
    spoke nor called a tool, whatever the negative checkpoints say."""
    original = config.STRICT
    config.STRICT = False
    try:
        _scenario, trace, _timings, _violations = _run_scenario(path)
    finally:
        config.STRICT = original

    spoke = any(e.get("kind") == "action"
                and e.get("action") in ("filler_speech", "clarification_request",
                                        "final_response")
                for e in trace)
    called = any(e.get("kind") == "action" and e.get("action") == "tool_call"
                 for e in trace)
    assert spoke or called, "agent produced neither speech nor a tool call"


@pytest.mark.parametrize("path", PUBLIC, ids=lambda p: os.path.basename(p)[:6])
def test_every_final_response_carries_a_snapshot(path):
    original = config.STRICT
    config.STRICT = False
    try:
        _scenario, trace, _timings, _violations = _run_scenario(path)
    finally:
        config.STRICT = original

    finals = [e for e in trace
              if e.get("kind") == "action" and e.get("action") == "final_response"]
    for entry in finals:
        assert isinstance(entry.get("state_snapshot"), dict), entry


# ------------------------------------------------------------ Invariant 7
def test_agent_survives_with_every_model_disabled():
    """The insurance policy, asserted rather than assumed.

    The largest unhedged risk here is that the evaluation machine differs from
    ours - a dependency that will not install, a checkpoint that cannot be
    fetched, a busy GPU - and with no leaderboard we would never find out. So
    duet/ treats every model as optional, and this checks that the fallback
    actually works end to end rather than only in principle.

    Perception is disabled via the environment, which is read inside
    duet.perception.base at load time, so this runs the real agent through the
    real harness with genuinely nothing behind it.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "killswitch.py"),
         "--floor", "55"],
        capture_output=True, text=True, cwd=ROOT, timeout=900)
    assert "DEGRADES GRACEFULLY" in result.stdout, (
        result.stdout[-2000:] + "\n" + result.stderr[-2000:])
