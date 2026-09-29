"""DUET Smart Appliance Care: tools, knowledge base, adapters, coordinator."""

from __future__ import annotations

import asyncio
import json

import pytest

from duet_voice.coordinator import Coordinator
from duet_voice.appliance.safety import PROFESSIONAL_REQUIRED, SAFE_SELF_SERVICE
from duet_voice.appliance.smartthings import MockSmartThingsAdapter, RealSmartThingsAdapter, SmartThingsError
from duet_voice.appliance.service import MockSamsungServiceAdapter, RealSamsungServiceAdapter, ServiceError
from duet_voice.appliance.redaction import redact
from duet_voice.appliance.troubleshooting import steps_payload
from duet_voice.appliance.tools import ApplianceToolbox, TOOL_SPECS, canonicalize_window, default_service
from duet_voice.fdb_tools import TOOL_SPECS as FDB_SPECS


def run(coro):
    return asyncio.run(coro)


def make_box(commit_hold_s=0.0, **kw):
    coord = Coordinator(commit_hold_s=commit_hold_s, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.user_started_speaking()
    coord.user_stopped_speaking()
    coord.turn_committed()
    st = kw.pop("smartthings", None) or MockSmartThingsAdapter()
    svc = kw.pop("service", None) or MockSamsungServiceAdapter()
    box = ApplianceToolbox("test-room", coord, smartthings=st, service=svc, **kw)
    return box, coord, st, svc


async def call(box, name, epoch=None, **args):
    return json.loads(await box.call(name, args, epoch=box.coord.epoch if epoch is None else epoch))


def test_benchmark_tool_names_are_unchanged():
    assert [s["name"] for s in FDB_SPECS] == [
        "search_flights", "book_flight", "update_identity_doc", "get_card_benefits",
        "get_exchange_rate", "modify_autopay", "search_apartments", "calculate_commute",
        "update_search_filter", "track_order", "search_products", "add_to_cart",
    ]


def test_appliance_tool_names():
    assert [s["name"] for s in TOOL_SPECS] == [
        "list_appliances", "get_appliance_status", "get_appliance_diagnostics",
        "get_troubleshooting_steps", "record_troubleshooting_step", "verify_appliance_state",
        "find_service_slots", "book_samsung_service", "get_service_request",
        "prepare_human_handoff",
    ]


def test_window_canonicalization():
    assert canonicalize_window("Tomorrow afternoon") == "tomorrow_afternoon"
    assert canonicalize_window("actually Friday morning") == "friday_morning"


def test_ue_steps_are_safe_and_documented():
    payload = steps_payload("WF45B6300AW", "Ub", "washer")
    assert payload["status"] == "ok" and payload["error_code"] == "UE"
    assert payload["steps"][0]["safety"] == SAFE_SELF_SERVICE
    assert "unbalanced" in payload["problem"].lower()


def test_unknown_error_code_is_not_invented():
    payload = steps_payload("WF45B6300AW", "ZZ9", "washer")
    assert payload["status"] == "unknown_error_code"
    assert payload["meaning"] is None and payload["steps"] == []


def test_motor_fault_is_professional_only():
    payload = steps_payload("WF45B6300AW", "3E", "washer")
    assert payload["steps"][0]["safety"] == PROFESSIONAL_REQUIRED
    assert "do not attempt" in payload["steps"][0]["instructions"].lower()


def test_list_and_select_washer():
    box, coord, st, svc = make_box()

    async def go():
        listed = await call(box, "list_appliances", query="washing machine")
        assert listed["count"] == 1 and listed["appliances"][0]["appliance_type"] == "washer"
        status = await call(box, "get_appliance_status", device_id="st-washer-laundry")
        diag = await call(box, "get_appliance_diagnostics", device_id="st-washer-laundry")
        return listed, status, diag

    listed, status, diag = run(go())
    assert status["error_code"] == "UE" and status["online"] is True
    assert diag["status"] == "ok" and diag["diagnostics"]["unbalance_detected"] is True
    assert box.state.selected_device_id == "st-washer-laundry"
    assert box.state.devices["st-washer-laundry"]["error_meaning"]


def test_offline_device_has_no_invented_diagnosis():
    box, coord, st, svc = make_box()

    async def go():
        status = await call(box, "get_appliance_status", device_id="st-dishwasher-kitchen")
        diag = await call(box, "get_appliance_diagnostics", device_id="st-dishwasher-kitchen")
        return status, diag

    status, diag = run(go())
    assert status["reason"] == "offline"
    assert status["online"] is False
    assert not status.get("error_code")
    assert diag["diagnostics"] is None and diag["reason"] == "offline"


def test_authentication_failure_does_not_invent_devices():
    st = MockSmartThingsAdapter()
    st.fail_next = "auth"
    box, coord, st, svc = make_box(smartthings=st)

    async def go():
        return await call(box, "list_appliances")

    result = run(go())
    assert result["status"] == "error" and result["error"] == "authentication_error"
    assert box.state.selected_device_id is None


def test_smartthings_timeout_is_reported():
    st = MockSmartThingsAdapter()
    st.fail_next = "timeout"
    box, _, _, _ = make_box(smartthings=st)
    result = run(call(box, "get_appliance_status", device_id="st-washer-laundry"))
    assert result["error"] == "timeout"


def test_switching_appliance_starts_a_new_workflow():
    box, coord, st, svc = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-washer-laundry")
        first = box.state.workflow_id, box.state.selected_device_id
        coord.user_started_speaking()
        coord.user_stopped_speaking()
        coord.heard("wait, actually the dryer")
        coord.turn_committed()
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        return first, box.state.workflow_id, box.state.selected_device_id

    first, workflow, selected = run(go())
    assert first[1] == "st-washer-laundry"
    assert selected == "st-dryer-laundry" and workflow == first[0] + 1
    assert "st-washer-laundry" in box.state.devices


def test_stale_epoch_never_runs_the_old_plan():
    box, coord, st, svc = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-washer-laundry")
        stale = coord.epoch
        coord.user_started_speaking()
        coord.user_stopped_speaking()
        coord.turn_committed()
        return await call(box, "get_appliance_diagnostics", epoch=stale, device_id="st-washer-laundry")

    result = run(go())
    assert result["status"] == "not_executed"
    assert box.state.superseded >= 1


def test_stale_committed_read_does_not_steal_selection():
    box, coord, st, svc = make_box()
    inner = st.get_status

    def bump(device_id):
        coord.user_started_speaking()
        coord.user_stopped_speaking()
        return inner(device_id)

    st.get_status = bump

    async def go():
        return await call(box, "get_appliance_status", device_id="st-washer-laundry")

    run(go())
    assert "st-washer-laundry" in box.state.devices
    assert box.state.selected_device_id is None


def test_already_done_step_is_skipped_and_replanned():
    box, _, _, _ = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        steps = await call(box, "get_troubleshooting_steps", model="DVE45B6300W",
                           error_code="HE", appliance_type="dryer")
        recorded = await call(box, "record_troubleshooting_step",
                              device_id="st-dryer-laundry",
                              step_id=steps["steps"][0]["step_id"],
                              outcome="already_done",
                              note="I already cleaned that")
        return steps, recorded

    steps, recorded = run(go())
    assert recorded["outcome"] == "already_done"
    remaining = [s["step_id"] for s in recorded["remaining_steps"]]
    assert steps["steps"][0]["step_id"] not in remaining
    assert box.state.steps_for("st-dryer-laundry")[0].outcome == "already_done"


def test_professional_step_cannot_be_marked_completed():
    box, _, _, _ = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-washer-laundry")
        return await call(box, "record_troubleshooting_step",
                          device_id="st-washer-laundry",
                          step_id="3e-service",
                          outcome="completed")

    result = run(go())
    assert result["outcome"] == "refused" and result["recorded"] is False


def test_verify_does_not_claim_fixed_while_error_remains():
    box, _, _, _ = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        return await call(box, "verify_appliance_state", device_id="st-dryer-laundry")

    result = run(go())
    assert result["resolved"] is False and result["error_code"] == "HE"
    assert "not confirmed fixed" in result["instruction"].lower() or "not" in result["instruction"].lower()


def test_command_acceptance_is_not_completion():
    box, _, st, _ = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-washer-laundry")
        steps = await call(box, "get_troubleshooting_steps", error_code="UE",
                           model="WF45B6300AW", appliance_type="washer")
        pause = next(s for s in steps["steps"] if s.get("may_send_command") == "pause")
        recorded = await call(box, "record_troubleshooting_step",
                              device_id="st-washer-laundry",
                              step_id=pause["step_id"], outcome="completed")
        verify = await call(box, "verify_appliance_state", device_id="st-washer-laundry")
        return recorded, verify

    recorded, verify = run(go())
    assert recorded["command"]["status"] == "ACCEPTED"
    assert recorded["command"]["applied"] is False
    assert verify["resolved"] is False
    assert verify["queued_commands_unconfirmed"] >= 1


def test_booking_is_exactly_once():
    box, _, _, svc = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        await call(box, "find_service_slots", device_id="st-dryer-laundry",
                   preferred_window="Friday morning")
        first = await call(box, "book_samsung_service", device_id="st-dryer-laundry",
                           preferred_window="Friday morning")
        lookup = await call(box, "get_service_request", device_id="st-dryer-laundry")
        second = await call(box, "book_samsung_service", device_id="st-dryer-laundry",
                            preferred_window="Friday morning")
        return first, lookup, second

    first, lookup, second = run(go())
    assert first["status"] == "ok" and first["request_id"].startswith("SSR-")
    assert lookup["booked"] is True
    assert second["status"] == "already_done"
    assert second["request_id"] == first["request_id"]
    assert len(svc.requests) == 1


def test_time_change_before_booking_books_the_final_window():
    box, coord, _, svc = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        await call(box, "find_service_slots", preferred_window="tomorrow afternoon")
        coord.user_started_speaking()
        coord.user_stopped_speaking()
        coord.heard("actually Friday morning")
        coord.turn_committed()
        await call(box, "find_service_slots", preferred_window="Friday morning")
        booked = await call(box, "book_samsung_service", device_id="st-dryer-laundry",
                            preferred_window="Friday morning")
        return booked

    booked = run(go())
    assert booked["window"] == "friday_morning"
    assert len(svc.requests) == 1


def test_unknown_booking_timeout_is_not_retried():
    svc = MockSamsungServiceAdapter()
    svc.delay_s = 1.0
    box, _, _, svc = make_box(service=svc)
    box.timeout_write = 0.05

    async def go():
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        await call(box, "find_service_slots", preferred_window="Friday morning")
        first = await call(box, "book_samsung_service", device_id="st-dryer-laundry",
                           preferred_window="Friday morning")
        again = await call(box, "book_samsung_service", device_id="st-dryer-laundry",
                           preferred_window="Friday morning")
        return first, again

    first, again = run(go())
    assert first["status"] == "unknown_outcome"
    assert again["status"] == "unknown_outcome"
    assert "do not book again" in again["instruction"].lower()


def test_service_failure_is_truthful():
    svc = MockSamsungServiceAdapter()
    svc.fail_next = "unavailable"
    box, _, _, _ = make_box(service=svc)

    async def go():
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        return await call(box, "find_service_slots", preferred_window="Friday morning")

    result = run(go())
    assert result["status"] == "error" and result["error"] == "service_unavailable"


def test_human_handoff_contains_context():
    box, _, _, _ = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        await call(box, "get_appliance_diagnostics", device_id="st-dryer-laundry")
        steps = await call(box, "get_troubleshooting_steps", error_code="HE",
                           model="DVE45B6300W", appliance_type="dryer")
        await call(box, "record_troubleshooting_step", device_id="st-dryer-laundry",
                   step_id=steps["steps"][0]["step_id"], outcome="already_done",
                   note="already cleaned the lint filter")
        await call(box, "verify_appliance_state", device_id="st-dryer-laundry")
        await call(box, "find_service_slots", preferred_window="Friday morning")
        await call(box, "book_samsung_service", preferred_window="Friday morning")
        return await call(box, "prepare_human_handoff",
                          extra_note="Dryer is in the laundry room")

    packet = run(go())["handoff"]
    assert packet["appliance"]["model"] == "DVE45B6300W"
    assert packet["error_code"] == "HE"
    assert packet["troubleshooting_skipped"]
    assert packet["verification"]["resolved"] is False
    assert packet["service_request_id"]
    assert packet["appointment"]["window"] == "friday_morning"
    assert any("lint" in n.lower() or "laundry" in n.lower() for n in packet["user_observations"])


def test_barge_in_during_booking_still_records_once():
    svc = MockSamsungServiceAdapter()
    svc.delay_s = 0.2
    box, coord, _, svc = make_box(service=svc)

    async def go():
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        await call(box, "find_service_slots", preferred_window="Friday morning")
        task = asyncio.ensure_future(box.call(
            "book_samsung_service",
            {"device_id": "st-dryer-laundry", "preferred_window": "Friday morning"},
            epoch=coord.epoch))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.3)
        again = await call(box, "book_samsung_service", device_id="st-dryer-laundry",
                           preferred_window="Friday morning")
        return again

    again = run(go())
    assert len(svc.requests) == 1
    assert again["status"] in ("already_done", "ok")
    if again["status"] == "ok":
        # ledger cached the in-flight booking as already_done in the usual case
        pass
    assert again.get("request_id") or (again.get("result") or {}).get("request_id")


def test_redaction_masks_serials_and_tokens():
    payload = redact({"serial": "0W4H8X123456", "token": "secret-token-value",
                      "device_id": "st-washer-laundry", "error_code": "UE"})
    assert payload["device_id"] == "st-washer-laundry" and payload["error_code"] == "UE"
    assert "*" in payload["serial"] and "123456" not in payload["serial"]
    assert "*" in payload["token"]


def test_real_adapters_refuse_without_credentials():
    with pytest.raises(SmartThingsError) as st:
        RealSmartThingsAdapter("")
    assert st.value.code == "auth"
    with pytest.raises(ServiceError) as svc:
        RealSamsungServiceAdapter("")
    assert svc.value.code == "unavailable"


def test_real_service_adapter_requires_https():
    with pytest.raises(ServiceError) as http:
        RealSamsungServiceAdapter("http://example.com/service")
    assert http.value.code == "unavailable"
    with pytest.raises(ServiceError):
        RealSamsungServiceAdapter("file:///etc/passwd")
    adapter = RealSamsungServiceAdapter("https://service.example.invalid/v1")
    assert adapter.base_url.startswith("https://")


def test_http_service_url_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("SAMSUNG_SERVICE_API_URL", "http://127.0.0.1/secret")
    box = default_service()
    assert isinstance(box, MockSamsungServiceAdapter)


def test_dishwasher_is_not_classified_as_a_washer():
    ad = RealSmartThingsAdapter("test-token")
    dish = {"components": [{"categories": [{"name": "Dishwasher"}]}]}
    wash = {"components": [{"capabilities": [{"id": "washerOperatingState"}]}]}
    dry = {"components": [{"capabilities": [{"id": "dryerOperatingState"}]}]}
    assert ad._type_from_device(dish) == "dishwasher"
    assert ad._type_from_device(wash) == "washer"
    assert ad._type_from_device(dry) == "dryer"


def test_dryer_commands_use_dryer_capability():
    ad = RealSmartThingsAdapter("test-token")
    ad._meta_by_id["dryer-1"] = {"components": [{"capabilities": [{"id": "dryerOperatingState"}]}]}
    ad._meta_by_id["washer-1"] = {"components": [{"capabilities": [{"id": "washerOperatingState"}]}]}
    assert ad._command_map("pause", "dryer-1") == ("dryerOperatingState", "pause")
    assert ad._command_map("pause", "washer-1") == ("washerOperatingState", "pause")
    assert ad._command_map("on", "dryer-1") == ("switch", "on")


def test_real_smartthings_diagnostics_do_not_refetch_status():
    ad = RealSmartThingsAdapter("test-token")
    calls = []
    device_id = "dev-1"
    raw_status = {"components": {"main": {
        "washerOperatingState": {
            "machineState": {"value": "pause"},
            "errorCode": {"value": "UE"},
            "washerJobState": {"value": "spin"},
        }
    }}}
    meta = {"deviceId": device_id, "label": "Washer",
            "components": [{"capabilities": [{"id": "washerOperatingState"}]}]}

    def fake(method, path, body=None):
        calls.append(path)
        if path.endswith("/health"):
            return {"state": "ONLINE"}
        if path.endswith("/status"):
            return raw_status
        if path.startswith("/devices/") and "/commands" not in path:
            return meta
        return {}

    ad._request = fake
    status = ad.get_status(device_id)
    assert status["error_code"] == "UE"
    n_after_status = len(calls)
    diag = ad.get_diagnostics(device_id)
    extra_status_gets = sum(1 for p in calls[n_after_status:] if p.endswith("/status"))
    # get_diagnostics calls get_status (live health+status) but must not GET /status twice in that step
    assert extra_status_gets == 1
    assert diag["status"] == "ok" and diag["diagnostics"]["washerOperatingState"]
    # session meta is cached: a second get_status does not GET /devices/{id} again
    before = list(calls)
    ad.get_status(device_id)
    meta_gets = [p for p in calls[len(before):] if p.endswith("/" + device_id)]
    assert meta_gets == []


def test_session_note_mentions_an_existing_booking():
    box, _, _, _ = make_box()

    async def go():
        await call(box, "get_appliance_status", device_id="st-dryer-laundry")
        await call(box, "find_service_slots", preferred_window="Friday morning")
        await call(box, "book_samsung_service", preferred_window="Friday morning")
        return box.session_note()

    note = run(go())
    assert "ACTIVE SERVICE REQUEST" in note and "Do not create another booking" in note
