"""The Galaxy Correction Suite's checker catches what it claims to catch.

A checker that passes everything is worse than none, so each kind of failure is planted in a
hand-made run and must be reported: a stale value, a duplicate, a missing action, an unasked
action, a question that was never asked, a false claim. A clean run must pass.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import galaxy_suite as suite  # noqa: E402


def run(*events):
    return {"events": [dict(e, t=i) for i, e in enumerate(events)]}


def done(tool, **args):
    return {"kind": "tool_done", "tool": tool, "args": args}


def said(text):
    return {"kind": "agent_said", "text": text}


ALARM = suite.S["alarm"]["expect"]


def test_a_clean_correction_passes():
    c = suite.check(run(done("set_alarm", time="04:30"), said("Alarm set for 4:30.")), ALARM)
    assert c["ok"] and c["intent"] and c["stale"] == 0 and c["duplicates"] == 0


def test_a_stale_value_is_caught_even_when_the_final_value_is_right():
    c = suite.check(run(done("set_alarm", time="03:30"), done("set_alarm", time="04:30")), ALARM)
    assert not c["ok"] and c["stale"] == 1 and c["intent"]


def test_the_same_action_twice_is_a_duplicate():
    c = suite.check(run(done("set_alarm", time="16:30"), done("set_alarm", time="16:30")), ALARM)
    assert not c["ok"] and c["duplicates"] == 1


def test_a_missing_action_and_an_unasked_one_are_both_reported():
    c = suite.check(run(done("set_brightness", percent=40)), ALARM)
    assert not c["ok"] and not c["intent"]
    assert any(p.startswith("MISSING") for p in c["problems"]) and any(p.startswith("EXTRA") for p in c["problems"])


def test_clock_times_match_morning_or_afternoon_unless_exact():
    assert suite._match_value("@4:30", "16:30") and suite._match_value("@4:30", "04:30")
    assert not suite._match_value("@4:30", "04:00")
    assert suite._match_value("=18:00", "18:00") and not suite._match_value("=18:00", "06:00")


def test_acting_before_asking_fails_the_waver_scenario():
    waver = suite.S["waver"]["expect"]
    acted_first = run(done("set_alarm", time="07:00"), {"kind": "thinker_say", "text": "6:30 or 7?"})
    assert "DID NOT ASK FIRST" in suite.check(acted_first, waver)["problems"]
    asked_first = run({"kind": "thinker_say", "text": "6:30 or 7?"}, done("set_alarm", time="07:00"))
    assert suite.check(asked_first, waver)["ok"]


def test_a_false_claim_is_caught():
    fall = suite.S["care_fall"]["expect"]
    assert not suite.check(run(said("I've called an ambulance for you.")), fall)["ok"]
    assert suite.check(run(said("Please call 112 now. Shall I alert your family?")), fall)["ok"]


def test_a_reading_is_not_a_state_change_and_a_model_error_fails_the_run():
    apps = suite.S["apps"]["expect"]
    assert suite.check(run(done("list_installed_apps"), said("You have six shopping apps.")), apps)["ok"]
    failed = run(done("list_installed_apps"), said("You have six."), {"kind": "thinker_error", "text": "504"})
    assert not suite.check(failed, apps)["ok"]
