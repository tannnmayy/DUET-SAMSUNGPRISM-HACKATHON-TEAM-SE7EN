"""Scripted DUET SmartThings Family Care demonstration.

Runs the caregiver story through the real coordinator, mock Family Care
household, mock Knox vault, mock Galaxy Watch and mock texts. No GPU and no
language model. Live voice uses the same toolbox when `DUET_USE_CASE=family`,
with faster-whisper as the ears.

    python -m duet_voice.family.demo
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Tuple

from ..coordinator import Coordinator
from .household import MockFamilyHousehold
from .knox import MockKnoxVault
from .messaging import MockCareMessenger
from .tools import FamilyToolbox
from .watch import MockGalaxyWatchAdapter


def _open_turn(coord: Coordinator, text: str) -> int:
    coord.user_started_speaking()
    coord.user_stopped_speaking()
    coord.heard(text)
    coord.turn_committed()
    return coord.epoch


async def _call(box: FamilyToolbox, name: str, args: Dict[str, Any], epoch: int) -> Dict[str, Any]:
    raw = await box.call(name, args, epoch=epoch)
    return json.loads(raw)


async def run_demo() -> Dict[str, Any]:
    """Check Mum → correct to Dad → Knox consent → Watch HR → refuse ambulance → text Priya once."""
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    household = MockFamilyHousehold()
    knox = MockKnoxVault()
    watch = MockGalaxyWatchAdapter()
    messenger = MockCareMessenger()
    box = FamilyToolbox("demo-family", coord, household=household, knox=knox, watch=watch, messenger=messenger)
    transcript: List[Dict[str, Any]] = []
    calls: List[Tuple[str, Dict[str, Any]]] = []

    async def user(text: str, tools: List[Tuple[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
        epoch = _open_turn(coord, text)
        results = []
        for name, args in tools:
            result = await _call(box, name, args, epoch)
            calls.append((name, result))
            results.append({"tool": name, "args": args, "result": result})
        transcript.append({"user": text, "epoch": epoch, "tools": results,
                           "member": box.state.selected_member_id,
                           "workflow": box.state.workflow_id})
        return results

    mum_turn = await user("Check on Mum.", [
        ("list_household", {"query": "Mum"}),
        ("get_member_status", {"member_id": "member-mum"}),
    ])

    dad_turn = await user("Wait, actually Dad.", [
        ("list_household", {"query": "Dad"}),
        ("get_member_status", {"member_id": "member-dad"}),
    ])

    denied = await user("What's his heart rate?", [
        ("get_watch_vitals", {"member_id": "member-dad"}),
    ])

    vitals_turn = await user("Yes, allow a Knox health read.", [
        ("request_health_consent", {"member_id": "member-dad"}),
        ("get_watch_vitals", {"member_id": "member-dad"}),
    ])

    refused = await user("Call an ambulance. No wait, don't. Just text Priya.", [
        ("place_care_call", {"target_id": "ambulance", "reason": "heart rate", "explicit_emergency": False}),
        ("send_care_text", {
            "contact_id": "Priya",
            "purpose": "vitals_alert",
            "body": "Dad's Watch band is elevated. Please check on him.",
        }),
    ])

    again = await user("Did you text her?", [
        ("get_outbound_status", {"contact_id": "Priya", "purpose": "vitals_alert"}),
        ("send_care_text", {"contact_id": "Priya", "purpose": "vitals_alert",
                            "body": "Did you get this?"}),
    ])

    handoff_turn = await user("Send this to Priya as a handoff.", [
        ("prepare_care_handoff", {"member_id": "member-dad",
                                  "extra_note": "I am on the road, cannot go over myself."}),
    ])

    return {
        "transcript": transcript,
        "calls": [(n, r.get("status")) for n, r in calls],
        "mum": {"member_id": mum_turn[1]["result"].get("member_id"),
                "inactivity_alert": mum_turn[1]["result"].get("inactivity_alert")},
        "dad": {"member_id": box.state.selected_member_id,
                "workflow_id": box.state.workflow_id,
                "inactivity_alert": dad_turn[1]["result"].get("inactivity_alert")},
        "vitals_without_consent": denied[0]["result"].get("status"),
        "vitals": vitals_turn[1]["result"],
        "ambulance": refused[0]["result"],
        "text": refused[1]["result"],
        "second_text": again[1]["result"],
        "lookup": again[0]["result"],
        "handoff": handoff_turn[0]["result"].get("handoff"),
        "state": box.state.snapshot(),
        "texts_created": len(messenger.texts),
        "calls_created": len(messenger.calls),
        "simulated": {
            "smartthings_family_care": True,
            "knox": True,
            "galaxy_watch": True,
            "messaging": True,
            "speech": "faster-whisper (live agent only)",
        },
    }


def render(result: Dict[str, Any]) -> str:
    lines = ["DUET SmartThings Family Care — scripted demo", ""]
    for turn in result["transcript"]:
        lines.append("USER: %s" % turn["user"])
        lines.append("  epoch=%s workflow=%s member=%s" % (
            turn["epoch"], turn["workflow"], turn["member"]))
        for item in turn["tools"]:
            status = item["result"].get("status") or item["result"].get("band")
            extra = item["result"].get("record_id") or item["result"].get("reason") or item["result"].get("band") or ""
            lines.append("  %s -> %s %s" % (item["tool"], status, extra))
        lines.append("")
    lines.append("Mum inactivity: %s" % result["mum"]["inactivity_alert"])
    lines.append("Dad selected, workflow=%s" % result["dad"]["workflow_id"])
    lines.append("Vitals without consent: %s" % result["vitals_without_consent"])
    lines.append("Watch band: %s HR=%s" % (
        result["vitals"].get("band"), result["vitals"].get("heart_rate_bpm")))
    lines.append("Ambulance call: %s" % result["ambulance"].get("status"))
    lines.append("Texted once: %s" % result["text"].get("record_id"))
    lines.append("Second text status: %s" % result["second_text"].get("status"))
    lines.append("Texts created: %s  Calls created: %s" % (
        result["texts_created"], result["calls_created"]))
    handoff = result["handoff"] or {}
    lines.append("Handoff member %s, consent=%s, band=%s" % (
        (handoff.get("member") or {}).get("name"),
        handoff.get("knox_health_consent"),
        (handoff.get("vitals") or {}).get("band")))
    lines.append("Household, Knox, Watch and messaging: simulated mock adapters.")
    lines.append("Live speech: existing faster-whisper stack (DUET_USE_CASE=family).")
    return "\n".join(lines)


def main() -> int:
    result = asyncio.run(run_demo())
    print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
