"""The package as the evaluator will see it.

Three failure modes, each invisible to every other test here because they
only exist on a machine we never touch:

  * dependencies drift between requirements.txt and submission.yaml, or float
    to versions we never ran (scoring happens after the deadline);
  * state leaks between scenarios - the organizers confirmed every scenario
    and repetition runs in ONE process, so module-level state that survives a
    scenario would make the three repetitions disagree;
  * the hidden set ships WAV where the kit ships MP3 (the organizers say
    expect MP3, support both).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import wave

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from duet import config  # noqa: E402
from duet.perception import checkpoints  # noqa: E402
from harness.runner import EvaluationHarness  # noqa: E402
from harness.scorer import score_scenario  # noqa: E402

_REQ = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(.*)$")


def _normalise(line: str):
    match = _REQ.match(line.strip())
    assert match, "unparseable requirement: " + line
    return match.group(1).lower().replace("_", "-"), match.group(2).replace(" ", "")


def _requirements_txt():
    out = []
    with open(os.path.join(ROOT, "requirements.txt"), "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(_normalise(line))
    return out


def _submission():
    with open(os.path.join(ROOT, "submission.yaml"), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------- dependencies
def test_requirements_and_submission_yaml_are_identical():
    txt = sorted(_requirements_txt())
    yml = sorted(_normalise(r) for r in _submission().get("requirements") or [])
    assert txt == yml, "requirements.txt and submission.yaml disagree:\n%r\n%r" % (txt, yml)


def test_every_graded_dependency_is_pinned_exactly():
    loose = [name + spec for name, spec in _requirements_txt()
             if not spec.startswith("==")]
    assert not loose, "unpinned: " + repr(loose)


def test_graded_requirements_exclude_the_app_stack():
    names = {name for name, _ in _requirements_txt()}
    with open(os.path.join(ROOT, "requirements-app.txt"), "r", encoding="utf-8") as fh:
        app = {_normalise(l.split("#")[0])[0] for l in fh if l.split("#")[0].strip()}
    assert not names & app, names & app


def test_torch_is_the_cuda_12_build_the_speech_engine_needs():
    """CTranslate2 4.x links cuBLAS 12; torch 2.11+ on PyPI is CUDA 13 and
    would leave the speech model without its libraries on a CUDA 12 box."""
    pins = dict(_requirements_txt())
    assert pins.get("torch") == "==2.10.0", pins.get("torch")
    assert pins.get("torchvision") == "==0.25.0", pins.get("torchvision")


def test_submission_identity():
    sub = _submission()
    assert sub["entry_point"] == "agent.agent:ParticipantAgent"
    assert re.fullmatch(r"[A-Za-z0-9]+_[A-Za-z0-9]+", str(sub["team"])), sub["team"]
    assert "CollegeName" not in str(sub["team"])
    assert str(sub["python"]) in ("3.10", "3.11", "3.12")


def test_every_model_is_pinned_to_a_commit():
    for role, ckpt in checkpoints.all_pinned().items():
        assert "/" in ckpt.repo, role
        assert ckpt.revision and re.fullmatch(r"[0-9a-f]{40}", ckpt.revision), (
            role + " is not pinned to a commit hash")


# ---------------------------------------------------------------- isolation
def _run(scenario, time_scale=4.0):
    from agent.agent import ParticipantAgent

    async def go():
        h = EvaluationHarness(scenario, lambda a, b: ParticipantAgent(a, b),
                              time_scale=time_scale, verbose=False)
        await h.prepare()
        return await h.run()
    return asyncio.run(go())


def _behaviour(trace):
    """What the agent did, without timestamps or call ids."""
    out = []
    for e in trace:
        if e.get("kind") != "action":
            continue
        if e.get("action") == "tool_call":
            out.append(("call", e.get("api_name"), json.dumps(e.get("args"), sort_keys=True)))
        elif e.get("action") == "cancel_tool":
            out.append(("cancel",))
        else:
            out.append((e.get("action"), (e.get("payload") or {}).get("text"),
                        json.dumps(e.get("state_snapshot"), sort_keys=True)))
    return out


def _load(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_repetitions_in_one_process_behave_identically():
    """The sealed run executes every scenario three times in ONE process and
    takes the median. Anything a scenario leaves behind - in a module cache,
    a class attribute, telemetry - would make repetition two differ from
    repetition one. Another scenario with a different manifest runs in
    between, to catch leakage across scenarios as well as repeats."""
    original = config.STRICT
    config.STRICT = False
    try:
        booking = _load("tests/conformance/conf_04_interrupt_during_booking.json")
        first = _behaviour(_run(booking))
        _run(_load("scenarios/pub_09_text_unseen_tool.json"))
        _run(_load("scenarios/pub_02_text_interrupt.json"))
        again = _behaviour(_run(booking))
    finally:
        config.STRICT = original
    assert first == again, "\n".join(map(str, first)) + "\n---\n" + "\n".join(map(str, again))


# ---------------------------------------------------------------- audio format
def _mp3_to_wav(src: str, dst: str, rate: int, channels: int) -> None:
    """The same recording as a WAV file.

    Resampled with PyAV's own resampler (via decode_audio), never by naive
    interpolation: the first version of this test upsampled 16 kHz audio
    linearly to 44.1 kHz, degraded it audibly, and "failed" on an artifact of
    its own making - Whisper heard "Hathory Migrate" for "Actually make that".
    The kit's clips are 48 kHz mono, so 48 kHz is the lossless case.
    """
    import numpy as np
    from faster_whisper import decode_audio

    mono = decode_audio(src, sampling_rate=rate)
    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)
    with wave.open(dst, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())


@pytest.mark.parametrize("rate, channels", [(48000, 2), (44100, 1), (16000, 1)])
def test_wav_audio_scores_like_the_mp3(tmp_path, rate, channels):
    """Organizer clarification 8: expect MP3, support WAV too. The same
    self-repair clip as WAV - at its native 48 kHz in stereo, at CD rate, and
    at the model's own 16 kHz - must be understood as well as the MP3."""
    scenario = _load("scenarios/pub_06_audio_disfluency.json")
    for event in scenario["events"]:
        ref = event["payload"].get("audio_ref")
        if ref:
            dst = str(tmp_path / (os.path.basename(ref).rsplit(".", 1)[0] + ".wav"))
            _mp3_to_wav(os.path.join(ROOT, ref), dst, rate, channels)
            event["payload"]["audio_ref"] = dst
    original = config.STRICT
    config.STRICT = False
    try:
        trace = _run(scenario, time_scale=1.0)
    finally:
        config.STRICT = original
    result = score_scenario(scenario, trace)
    assert not [e for e in trace if e.get("kind") in ("agent_crash", "protocol_error")]
    assert result["total"] >= 95.0, json.dumps(result["breakdown"], indent=1)[:3000]
