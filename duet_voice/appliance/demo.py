"""Scripted DUET Smart Appliance Care demonstration.

Runs the 16-beat demo through the real coordinator, mock SmartThings and mock
Samsung service. No GPU and no language model: this proves the tools, state,
interruptions and exactly-once booking. The live agent uses the same toolbox
when `DUET_USE_CASE=appliance`.

    python -m duet_voice.appliance.demo
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Tuple

from ..coordinator import Coordinator
from .smartthings import MockSmartThingsAdapter
from .service import MockSamsungServiceAdapter
from .tools import ApplianceToolbox


def _open_turn(coord: Coordinator, text: str) -> int:
    coord.user_started_speaking()
    coord.user_stopped_speaking()
    coord.heard(text)
    coord.turn_committed()
    return coord.epoch


async def _call(box: ApplianceToolbox, name: str, args: Dict[str, Any], epoch: int) -> Dict[str, Any]:
    raw = await box.call(name, args, epoch=epoch)
    return json.loads(raw)


async def run_demo(interrupt_booking: bool = False) -> Dict[str, Any]:
    """Diagnose washer → correct to dryer → skip a step → book Friday once → handoff."""
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    st = MockSmartThingsAdapter()
    svc = MockSamsungServiceAdapter()
    box = ApplianceToolbox("demo-appliance", coord, smartthings=st, service=svc)
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
                           "device": box.state.selected_device_id,
                           "workflow": box.state.workflow_id})
        return results

    # 1-5. User reports a washer problem; identify, status, diagnostics, explain, start troubleshooting.
    washer_turn = await user("My Samsung washing machine isn't working.", [
        ("list_appliances", {"query": "washer"}),
        ("get_appliance_status", {"device_id": "st-washer-laundry"}),
        ("get_appliance_diagnostics", {"device_id": "st-washer-laundry"}),
        ("get_troubleshooting_steps", {"model": "WF45B6300AW", "error_code": "UE",
                                       "appliance_type": "washer"}),
    ])
    washer_id = washer_turn[1]["result"]["device_id"]
    washer_code = washer_turn[1]["result"]["error_code"]
    washer_steps = washer_turn[3]["result"]["steps"]

    # 6-8. User interrupts: it is the dryer. Old washer workflow is left behind.
    dryer_turn = await user("Wait, actually the dryer.", [
        ("list_appliances", {"query": "dryer"}),
        ("get_appliance_status", {"device_id": "st-dryer-laundry"}),
        ("get_appliance_diagnostics", {"device_id": "st-dryer-laundry"}),
        ("get_troubleshooting_steps", {"model": "DVE45B6300W", "error_code": "HE",
                                       "appliance_type": "dryer"}),
    ])
    dryer_steps = dryer_turn[3]["result"]["steps"]

    # 9-10. User already cleaned the lint filter; skip and replan.
    skip_turn = await user("I already cleaned that.", [
        ("record_troubleshooting_step", {
            "device_id": "st-dryer-laundry",
            "step_id": dryer_steps[0]["step_id"],
            "outcome": "already_done",
            "note": "User already cleaned the lint filter",
        }),
        ("record_troubleshooting_step", {
            "device_id": "st-dryer-laundry",
            "step_id": dryer_steps[1]["step_id"],
            "outcome": "completed",
            "note": "Power-cycled as asked",
        }),
    ])

    # 11-12. Still unresolved; offer service.
    verify_turn = await user("Is it working now?", [
        ("verify_appliance_state", {"device_id": "st-dryer-laundry"}),
        ("find_service_slots", {"device_id": "st-dryer-laundry",
                                "preferred_window": "tomorrow afternoon"}),
    ])

    # 13-14. Preferred time, then a correction. Do not book the stale window.
    await user("Tomorrow afternoon.", [
        ("find_service_slots", {"device_id": "st-dryer-laundry",
                                "preferred_window": "tomorrow afternoon"}),
    ])
    book_turn = await user("Actually Friday morning.", [
        ("find_service_slots", {"device_id": "st-dryer-laundry",
                                "preferred_window": "Friday morning"}),
        ("book_samsung_service", {"device_id": "st-dryer-laundry",
                                  "preferred_window": "Friday morning"}),
    ])

    # 15. Exactly once: asking again does not create a second request.
    again = await user("Did you book it?", [
        ("get_service_request", {"device_id": "st-dryer-laundry"}),
        ("book_samsung_service", {"device_id": "st-dryer-laundry",
                                  "preferred_window": "Friday morning"}),
    ])

    # 16. Technician handoff with the full context.
    handoff_turn = await user("Please send this to the technician.", [
        ("prepare_human_handoff", {"device_id": "st-dryer-laundry",
                                   "extra_note": "Dryer sits in the laundry room next to the washer."}),
    ])

    state = box.state.snapshot()
    return {
        "transcript": transcript,
        "calls": [(n, r.get("status")) for n, r in calls],
        "washer": {"device_id": washer_id, "error_code": washer_code,
                   "first_step": washer_steps[0]["step_id"] if washer_steps else None},
        "dryer": {"device_id": box.state.selected_device_id,
                  "error_code": (box.state.devices.get("st-dryer-laundry") or {}).get("error_code"),
                  "workflow_id": box.state.workflow_id},
        "skipped": skip_turn[0]["result"].get("outcome"),
        "remaining_after_skip": [s["step_id"] for s in skip_turn[0]["result"].get("remaining_steps") or []],
        "verified_resolved": verify_turn[0]["result"].get("resolved"),
        "booking": book_turn[1]["result"],
        "second_book": again[1]["result"],
        "lookup": again[0]["result"],
        "handoff": handoff_turn[0]["result"].get("handoff"),
        "state": state,
        "service_request_count": len(svc.requests),
        "simulated": {
            "smartthings": True,
            "samsung_service": True,
        },
    }


def render(result: Dict[str, Any]) -> str:
    lines = ["DUET Smart Appliance Care — scripted demo", ""]
    for turn in result["transcript"]:
        lines.append("USER: %s" % turn["user"])
        lines.append("  epoch=%s workflow=%s device=%s" % (
            turn["epoch"], turn["workflow"], turn["device"]))
        for item in turn["tools"]:
            status = item["result"].get("status") or item["result"].get("error_code")
            extra = item["result"].get("request_id") or item["result"].get("reason") or ""
            lines.append("  %s -> %s %s" % (item["tool"], status, extra))
        lines.append("")
    booking = result["booking"]
    lines.append("Booked once: %s %s (%s)" % (
        booking.get("request_id"), booking.get("when_label"), booking.get("window")))
    lines.append("Second book status: %s" % result["second_book"].get("status"))
    lines.append("Service requests created: %s" % result["service_request_count"])
    handoff = result["handoff"] or {}
    lines.append("Handoff error %s, skipped %s, appointment %s" % (
        (handoff.get("error_code"),
         [s["step_id"] for s in handoff.get("troubleshooting_skipped") or []],
         (handoff.get("appointment") or {}).get("when"))))
    lines.append("SmartThings and Samsung service backends: simulated mock adapters.")
    return "\n".join(lines)


def main() -> int:
    result = asyncio.run(run_demo())
    print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
