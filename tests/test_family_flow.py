"""Family Care through the real DUET thinker loop, demo, and use-case switch."""

from __future__ import annotations

import asyncio
import dataclasses

from duet_voice import config, use_case
from duet_voice.appliance.tools import TOOL_SPECS as APPLIANCE_SPECS
from duet_voice.coordinator import Coordinator
from duet_voice.family.demo import run_demo
from duet_voice.family.tools import TOOL_SPECS as FAMILY_SPECS
from duet_voice.fdb_tools import FdbToolbox, TOOL_SPECS as FDB_SPECS
from duet_voice.llm_local import LocalThinker
from duet_voice.prompts import FAMILY_THINKER_INSTRUCTIONS, THINKER_INSTRUCTIONS
from duet_voice.thinker import make_thinker
from tests.test_family import call, make_box
from tests.test_llm_local import reply


def test_default_use_case_is_still_benchmark():
    assert config.CONFIG.use_case == "benchmark"
    assert use_case.is_family() is False
    assert use_case.is_appliance() is False
    assert use_case.thinker_instructions() == THINKER_INSTRUCTIONS
    assert use_case.tool_specs() is FDB_SPECS
    box = use_case.make_toolbox("bench", Coordinator(commit_hold_s=0.0))
    assert isinstance(box, FdbToolbox)


def test_family_use_case_selects_the_family_bundle(monkeypatch):
    patched = dataclasses.replace(config.CONFIG, use_case="family")
    monkeypatch.setattr(use_case, "CONFIG", patched)
    assert use_case.is_family() is True
    assert use_case.is_appliance() is False
    assert use_case.thinker_instructions() == FAMILY_THINKER_INSTRUCTIONS
    assert use_case.tool_specs() is FAMILY_SPECS
    box = use_case.make_toolbox("care", Coordinator(commit_hold_s=0.0))
    assert box.specs[0]["name"] == "list_household"


def test_appliance_alias_care_is_not_family(monkeypatch):
    patched = dataclasses.replace(config.CONFIG, use_case="care")
    monkeypatch.setattr(use_case, "CONFIG", patched)
    assert use_case.is_appliance() is True
    assert use_case.is_family() is False
    assert use_case.tool_specs() is APPLIANCE_SPECS


def test_make_thinker_defaults_exclude_family_tools():
    thinker = make_thinker()
    names = [t["function"]["name"] for t in thinker._tools]
    assert "track_order" in names
    assert "list_household" not in names and "list_appliances" not in names


def test_make_thinker_can_take_family_tools():
    thinker = make_thinker(tool_specs=FAMILY_SPECS, instructions=FAMILY_THINKER_INSTRUCTIONS)
    names = [t["function"]["name"] for t in thinker._tools]
    assert "list_household" in names and "send_care_text" in names
    assert "track_order" not in names
    assert thinker.instructions == FAMILY_THINKER_INSTRUCTIONS


def test_scripted_thinker_runs_family_tools_through_the_coordinator():
    box, coord, *_ = make_box()
    th = LocalThinker(model="qwen-test", tool_specs=FAMILY_SPECS,
                      instructions=FAMILY_THINKER_INSTRUCTIONS)
    script = [
        reply(calls=[("list_household", {"query": "Dad"}),
                     ("get_member_status", {"member_id": "member-dad"})]),
        reply("Dad is home, no inactivity alert."),
    ]

    async def fake_create(messages, tools):
        return script.pop(0)

    th._create = fake_create

    async def go():
        return [ev async for ev in th.run("Check on Dad.", box)]

    evs = asyncio.run(go())
    kinds = [e.kind for e in evs]
    assert kinds[0] == "decided" and evs[0].tools is True
    assert "tool_start" in kinds and "tool_done" in kinds
    assert evs[-1].kind == "say"
    assert box.state.selected_member_id == "member-dad"


def test_scripted_thinker_stops_when_the_user_barges_in():
    box, coord, *_ = make_box()
    th = LocalThinker(model="qwen-test", tool_specs=FAMILY_SPECS,
                      instructions=FAMILY_THINKER_INSTRUCTIONS)
    script = [
        reply(calls=[("get_member_status", {"member_id": "member-dad"})]),
        reply("should not be asked"),
    ]

    async def fake_create(messages, tools):
        return script.pop(0)

    th._create = fake_create

    async def go():
        original = box.call

        async def bump(tool, args, epoch=None):
            out = await original(tool, args, epoch=epoch)
            coord.user_started_speaking()
            return out

        box.call = bump
        from duet_voice.coordinator import Superseded
        events = []
        try:
            async for ev in th.run("check on dad", box):
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
    assert result["mum"]["inactivity_alert"] is True
    assert result["mum"]["watch_on_wrist"] is False
    assert result["home"]["anyone_home"] is True
    assert result["dad"]["member_id"] == "member-dad"
    assert result["dad"]["workflow_id"] >= 1
    assert result["dad"]["watch_on_wrist"] is True
    assert result["vitals_without_consent"] == "consent_required"
    assert result["vitals"]["band"] == "elevated"
    assert result["ambulance"]["status"] == "refused"
    assert result["text"]["status"] == "ok"
    assert result["second_text"]["status"] == "already_done"
    assert result["third_text"]["status"] == "already_done"
    assert result["texts_created"] == 1
    assert result["calls_created"] == 0
    handoff = result["handoff"]
    assert handoff["member"]["name"] == "Dad"
    assert handoff["member"]["watch_on_wrist"] is True
    assert handoff["knox_health_consent"] is True
    assert handoff["vitals"]["band"] == "elevated"
    assert result["simulated"]["knox"] and result["simulated"]["messaging"]
