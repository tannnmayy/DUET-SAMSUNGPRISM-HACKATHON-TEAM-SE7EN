"""Appliance care through the real DUET thinker loop, demo, and use-case switch."""

from __future__ import annotations

import asyncio
import dataclasses

from duet_voice import config, use_case
from duet_voice.appliance.demo import run_demo
from duet_voice.appliance.tools import TOOL_SPECS as APPLIANCE_SPECS
from duet_voice.coordinator import Coordinator
from duet_voice.fdb_tools import FdbToolbox, TOOL_SPECS as FDB_SPECS
from duet_voice.llm_local import LocalThinker
from duet_voice.prompts import APPLIANCE_THINKER_INSTRUCTIONS, THINKER_INSTRUCTIONS
from duet_voice.thinker import make_thinker
from tests.test_appliance import call, make_box
from tests.test_llm_local import reply


def test_default_use_case_is_benchmark():
    assert config.CONFIG.use_case == "benchmark"
    assert use_case.is_appliance() is False
    assert use_case.thinker_instructions() == THINKER_INSTRUCTIONS
    assert use_case.tool_specs() is FDB_SPECS
    coord = Coordinator(commit_hold_s=0.0)
    box = use_case.make_toolbox("bench", coord)
    assert isinstance(box, FdbToolbox)


def test_appliance_use_case_selects_the_appliance_bundle(monkeypatch):
    patched = dataclasses.replace(config.CONFIG, use_case="appliance")
    monkeypatch.setattr(use_case, "CONFIG", patched)
    assert use_case.is_appliance() is True
    assert use_case.thinker_instructions() == APPLIANCE_THINKER_INSTRUCTIONS
    assert use_case.tool_specs() is APPLIANCE_SPECS
    coord = Coordinator(commit_hold_s=0.0)
    box = use_case.make_toolbox("care", coord)
    assert box.specs[0]["name"] == "list_appliances"


def test_make_thinker_defaults_to_benchmark_tools():
    thinker = make_thinker()
    assert isinstance(thinker, LocalThinker)
    names = [t["function"]["name"] for t in thinker._tools]
    assert "track_order" in names and "list_appliances" not in names


def test_make_thinker_can_take_appliance_tools():
    thinker = make_thinker(tool_specs=APPLIANCE_SPECS, instructions=APPLIANCE_THINKER_INSTRUCTIONS)
    names = [t["function"]["name"] for t in thinker._tools]
    assert "list_appliances" in names and "book_samsung_service" in names
    assert "track_order" not in names
    assert thinker.instructions == APPLIANCE_THINKER_INSTRUCTIONS


def test_scripted_thinker_runs_appliance_tools_through_the_coordinator():
    """The slow mind's own loop, with a scripted model, on the appliance toolbox."""
    box, coord, st, svc = make_box()
    th = LocalThinker(model="qwen-test", tool_specs=APPLIANCE_SPECS,
                      instructions=APPLIANCE_THINKER_INSTRUCTIONS)
    script = [
        reply(calls=[("list_appliances", {"query": "washer"}),
                     ("get_appliance_status", {"device_id": "st-washer-laundry"})]),
        reply("The washer is reporting an unbalanced load."),
    ]

    async def fake_create(messages, tools):
        return script.pop(0)

    th._create = fake_create

    async def go():
        return [ev async for ev in th.run("My washing machine isn't working.", box)]

    evs = asyncio.run(go())
    kinds = [e.kind for e in evs]
    assert kinds[0] == "decided" and evs[0].tools is True
    assert "tool_start" in kinds and "tool_done" in kinds
    assert evs[-1].kind == "say" and "unbalanced" in evs[-1].text.lower()
    assert box.state.selected_device_id == "st-washer-laundry"


def test_scripted_thinker_stops_when_the_user_barges_in():
    box, coord, st, svc = make_box()
    th = LocalThinker(model="qwen-test", tool_specs=APPLIANCE_SPECS,
                      instructions=APPLIANCE_THINKER_INSTRUCTIONS)
    script = [
        reply(calls=[("get_appliance_status", {"device_id": "st-washer-laundry"})]),
        reply("should not be asked"),
    ]
    async def fake_create(messages, tools):
        return script.pop(0)

    th._create = fake_create

    async def go():
        # the user starts speaking while the tool runs: after the call returns,
        # the thinker must raise Superseded rather than keep the stale plan.
        original = box.call

        async def bump(tool, args, epoch=None):
            out = await original(tool, args, epoch=epoch)
            coord.user_started_speaking()
            return out

        box.call = bump
        from duet_voice.coordinator import Superseded
        events = []
        try:
            async for ev in th.run("check the washer", box):
                events.append(ev)
        except Superseded:
            return "superseded", events
        return "finished", events

    outcome, events = asyncio.run(go())
    assert outcome == "superseded"
    assert any(e.kind == "tool_start" for e in events)
    assert all(e.kind != "say" for e in events)


def test_full_demo_flow():
    result = asyncio.run(run_demo())
    assert result["washer"]["error_code"] == "UE"
    assert result["dryer"]["device_id"] == "st-dryer-laundry"
    assert result["dryer"]["workflow_id"] >= 1
    assert result["skipped"] == "already_done"
    assert result["verified_resolved"] is False
    assert result["booking"]["status"] == "ok"
    assert result["booking"]["window"] == "friday_morning"
    assert result["second_book"]["status"] == "already_done"
    assert result["service_request_count"] == 1
    handoff = result["handoff"]
    assert handoff["error_code"] == "HE"
    assert handoff["appliance"]["model"] == "DVE45B6300W"
    assert handoff["service_request_id"] == result["booking"]["request_id"]
    assert handoff["appointment"]["window"] == "friday_morning"
    assert handoff["verification"]["resolved"] is False
    assert handoff["troubleshooting_skipped"]
    assert result["simulated"]["smartthings"] and result["simulated"]["samsung_service"]


def test_error_code_change_replans_from_new_diagnostics():
    box, coord, st, svc = make_box()

    async def go():
        first = await call(box, "get_appliance_status", device_id="st-washer-laundry")
        assert first["error_code"] == "UE"
        st.apply_webhook({"device_id": "st-washer-laundry", "error_code": "5E",
                          "diagnostics": {"drain_slow": True, "source": "mock_smartthings"}})
        coord.user_started_speaking()
        coord.user_stopped_speaking()
        coord.heard("the error changed")
        coord.turn_committed()
        status = await call(box, "get_appliance_status", device_id="st-washer-laundry")
        steps = await call(box, "get_troubleshooting_steps", error_code=status["error_code"],
                           model="WF45B6300AW", appliance_type="washer")
        return status, steps

    status, steps = asyncio.run(go())
    assert status["error_code"] == "5E"
    assert steps["status"] == "ok" and steps["error_code"] == "5E"
    assert "drain" in steps["problem"].lower()


def test_reload_picks_up_appliance_use_case(monkeypatch):
    monkeypatch.setenv("DUET_USE_CASE", "appliance")
    config.reload()
    try:
        assert config.CONFIG.use_case == "appliance"
        assert use_case.is_appliance() is True
        assert use_case.tool_specs() is APPLIANCE_SPECS
        box = use_case.make_toolbox("care", Coordinator(commit_hold_s=0.0))
        assert box.specs[0]["name"] == "list_appliances"
    finally:
        monkeypatch.delenv("DUET_USE_CASE", raising=False)
        config.reload()
    assert config.CONFIG.use_case == "benchmark"
    assert use_case.is_appliance() is False


def test_chat_banner_warns_when_benchmark_tools_are_loaded(monkeypatch):
    from duet_voice.chat import banner
    monkeypatch.delenv("DUET_USE_CASE", raising=False)
    auto = banner("benchmark", ["flight_search", "book_flight"], "local")
    assert "Auto-route" in auto
    monkeypatch.setenv("DUET_USE_CASE", "benchmark")
    pinned = banner("benchmark", ["flight_search", "book_flight"], "local")
    assert "trip" in pinned or "pinned" in pinned.lower()
    appliance = banner("appliance", ["list_appliances", "get_appliance_status"], "gemini")
    assert "list_appliances" in appliance
    assert "Auto-route" not in appliance


def test_chat_turn_uses_appliance_tools_not_flights():
    from duet_voice.chat import run_turn
    from duet_voice.prompts import APPLIANCE_THINKER_INSTRUCTIONS

    box, coord, *_ = make_box()
    th = LocalThinker(model="qwen-test", tool_specs=APPLIANCE_SPECS,
                      instructions=APPLIANCE_THINKER_INSTRUCTIONS)
    script = [
        reply(calls=[("list_appliances", {"query": "washer"}),
                     ("get_appliance_status", {"device_id": "st-washer-laundry"})]),
        reply("The washer is reporting an unbalanced load, error UE."),
    ]

    async def fake_create(messages, tools):
        names = [t["function"]["name"] for t in tools]
        assert "list_appliances" in names
        assert "flight_search" not in names
        return script.pop(0)

    th._create = fake_create
    lines = asyncio.run(run_turn(
        "My Samsung washing machine is showing a UE error",
        coord=coord, toolbox=box, thinker=th))
    joined = "\n".join(lines)
    assert "list_appliances" in joined
    assert "flight_search" not in joined
    assert "unbalanced" in joined.lower() or "UE" in joined
    assert box.state.selected_device_id == "st-washer-laundry"
