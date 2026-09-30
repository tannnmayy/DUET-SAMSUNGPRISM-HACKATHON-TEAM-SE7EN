"""Live routing: washer/UE and Mum/Watch switch toolsets; FDB stays the default."""

from __future__ import annotations

from duet_voice import config, use_case
from duet_voice.appliance.tools import ApplianceToolbox
from duet_voice.family.tools import FamilyToolbox
from duet_voice.fdb_tools import FdbToolbox


def test_detect_family_and_appliance_and_ignore_flights():
    assert use_case.detect_domain("Check on Mum.") == "family"
    assert use_case.detect_domain("what's mom's heart rate") == "family"
    assert use_case.detect_domain("text Priya") == "family"
    assert use_case.detect_domain("My Samsung washing machine is showing a UE error") == "appliance"
    assert use_case.detect_domain("the dryer has an HE error") == "appliance"
    assert use_case.detect_domain("find flights to Denver tomorrow") is None
    assert use_case.detect_domain("My Samsung") is None
    assert use_case.detect_domain("Did you text her?") is None
    assert use_case.detect_domain("book Friday morning") is None
    assert use_case.detect_domain("what's the error code") is None


def test_follow_up_stays_on_the_pinned_live_case(monkeypatch):
    monkeypatch.delenv("DUET_USE_CASE", raising=False)
    assert use_case.detect_domain("Did you text her?") is None
    assert use_case.resolve_live_use_case("Did you text her?", "family") == "family"
    assert use_case.resolve_live_use_case("Did you text her?", "appliance") == "appliance"
    assert use_case.resolve_live_use_case("book Friday morning", "appliance") == "appliance"
    assert use_case.resolve_live_use_case("what's his heart rate", "benchmark") == "family"
    assert use_case.resolve_live_use_case("the washer isn't working", "benchmark") == "appliance"
    assert use_case.resolve_live_use_case("Check on Mum.", "appliance") == "family"
    assert use_case.resolve_live_use_case("the dryer has an HE error", "family") == "appliance"


def test_pin_scored_env_overwrites_appliance_and_keeps_other_keys():
    env = {"DUET_USE_CASE": "appliance", "PATH": "/bin", "FDB_V3_DIR": "/tmp/fdb"}
    prev = use_case.pin_scored_env(env)
    assert prev == "appliance"
    assert env["DUET_USE_CASE"] == "benchmark"
    assert env["PATH"] == "/bin" and env["FDB_V3_DIR"] == "/tmp/fdb"
    assert use_case.pin_scored_env({"DUET_USE_CASE": "family"}) == "family"
    assert use_case.pin_scored_env({}) == ""


def test_benchmark_env_pins_fdb_and_ignores_washer_or_mum(monkeypatch):
    monkeypatch.setenv("DUET_USE_CASE", "benchmark")
    config.reload()
    try:
        assert use_case.auto_route_enabled() is False
        assert use_case.resolve_live_use_case("Check on Mum.", "benchmark") == "benchmark"
        assert use_case.resolve_live_use_case("the washer isn't working", "benchmark") == "benchmark"
    finally:
        monkeypatch.delenv("DUET_USE_CASE", raising=False)
        config.reload()
    assert use_case.auto_route_enabled() is True


def test_explicit_env_pins_and_ignores_the_utterance(monkeypatch):
    monkeypatch.setenv("DUET_USE_CASE", "appliance")
    config.reload()
    try:
        assert use_case.auto_route_enabled() is False
        assert use_case.resolve_live_use_case("check on Mum", "appliance") == "appliance"
    finally:
        monkeypatch.delenv("DUET_USE_CASE", raising=False)
        config.reload()
    assert use_case.auto_route_enabled() is True
    assert config.CONFIG.use_case == "benchmark"


def test_chat_stays_on_fdb_when_benchmark_is_pinned(monkeypatch):
    monkeypatch.setenv("DUET_USE_CASE", "benchmark")
    config.reload()
    try:
        from duet_voice.chat import ChatSession
        session = ChatSession()
        assert isinstance(session.toolbox, FdbToolbox)
        session.bind("My Samsung washing machine is showing a UE error")
        session.bind("Check on Mum. What's her heart rate?")
        assert session.live_case == "benchmark"
        assert isinstance(session.toolbox, FdbToolbox)
        names = [s["name"] for s in session.toolbox.specs]
        assert "search_flights" in names
        assert "list_appliances" not in names and "list_household" not in names
    finally:
        monkeypatch.delenv("DUET_USE_CASE", raising=False)
        config.reload()


def test_chat_session_switches_tools_on_the_first_family_line(monkeypatch):
    monkeypatch.delenv("DUET_USE_CASE", raising=False)
    from duet_voice.chat import ChatSession
    pinned = config.CONFIG.use_case
    session = ChatSession()
    assert isinstance(session.toolbox, FdbToolbox)
    session.bind("Check on Mum. What's her heart rate?")
    assert session.live_case == "family"
    assert isinstance(session.toolbox, FamilyToolbox)
    assert config.CONFIG.use_case == pinned
    session.bind("Did you text her?")
    assert session.live_case == "family"
    session.bind("My washing machine is showing a UE error")
    assert session.live_case == "appliance"
    session.bind("Did you text her?")
    assert session.live_case == "appliance"
    assert isinstance(session.toolbox, ApplianceToolbox)
    assert use_case.is_family() is False
    assert use_case.make_toolbox("x", session.coord).specs[0]["name"] != "list_household"
