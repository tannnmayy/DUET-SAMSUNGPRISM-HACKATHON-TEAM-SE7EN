"""DUET SmartThings Family Care: tools, Knox, Watch, texts, coordinator."""

from __future__ import annotations

import asyncio
import json

import pytest

from duet_voice.coordinator import Coordinator
from duet_voice.family.household import MockFamilyHousehold
from duet_voice.family.knox import MockKnoxVault, RealKnoxVault, KnoxError
from duet_voice.family.messaging import MockCareMessenger, RealCareMessenger, MessagingError
from duet_voice.family.privacy import redact
from duet_voice.family.tools import FamilyToolbox, TOOL_SPECS, default_messenger
from duet_voice.family.watch import MockGalaxyWatchAdapter
from duet_voice.fdb_tools import TOOL_SPECS as FDB_SPECS
from duet_voice.appliance.tools import TOOL_SPECS as APPLIANCE_SPECS


def run(coro):
    return asyncio.run(coro)


def make_box(**kw):
    coord = Coordinator(commit_hold_s=0.0, revising_hold_s=0.0, dangling_hold_s=0.0)
    coord.user_started_speaking()
    coord.user_stopped_speaking()
    coord.turn_committed()
    household = kw.pop("household", None) or MockFamilyHousehold()
    knox = kw.pop("knox", None) or MockKnoxVault()
    watch = kw.pop("watch", None) or MockGalaxyWatchAdapter()
    messenger = kw.pop("messenger", None) or MockCareMessenger()
    box = FamilyToolbox("test-room", coord, household=household, knox=knox,
                        watch=watch, messenger=messenger, **kw)
    return box, coord, household, knox, watch, messenger


async def call(box, name, epoch=None, **args):
    return json.loads(await box.call(name, args, epoch=box.coord.epoch if epoch is None else epoch))


def test_family_tools_do_not_collide_with_benchmark_or_appliance():
    family = {s["name"] for s in TOOL_SPECS}
    assert family.isdisjoint({s["name"] for s in FDB_SPECS})
    assert family.isdisjoint({s["name"] for s in APPLIANCE_SPECS})
    assert "send_care_text" in family and "get_watch_vitals" in family


def test_list_and_select_mum():
    box, *_ = make_box()

    async def go():
        listed = await call(box, "list_household", query="Mum")
        status = await call(box, "get_member_status", member_id="member-mum")
        return listed, status

    listed, status = run(go())
    assert listed["count"] == 1 and listed["members"][0]["name"] == "Mum"
    assert status["inactivity_alert"] is True and status["member_id"] == "member-mum"
    assert box.state.selected_member_id == "member-mum"


def test_switching_member_starts_a_new_workflow():
    box, *_ = make_box()

    async def go():
        await call(box, "get_member_status", member_id="member-mum")
        first = box.state.workflow_id
        await call(box, "get_member_status", member_id="member-dad")
        return first, box.state.workflow_id, box.state.selected_member_id

    first, second, selected = run(go())
    assert selected == "member-dad" and second == first + 1


def test_vitals_require_knox_consent():
    box, *_ = make_box()

    async def go():
        await call(box, "get_member_status", member_id="member-dad")
        denied = await call(box, "get_watch_vitals", member_id="member-dad")
        grant = await call(box, "request_health_consent", member_id="member-dad")
        vitals = await call(box, "get_watch_vitals", member_id="member-dad")
        return denied, grant, vitals

    denied, grant, vitals = run(go())
    assert denied["status"] == "consent_required"
    assert grant["status"] == "ok" and grant["scope"] == "health_read"
    assert vitals["band"] == "elevated" and vitals["heart_rate_bpm"] == 118
    assert vitals["knox_protected"] is True


def test_auth_failure_does_not_invent_members():
    household = MockFamilyHousehold()
    household.fail_next = "auth"
    box, *_rest = make_box(household=household)
    listed = run(call(box, "list_household"))
    assert listed["status"] == "error" and listed["error"] == "authentication_error"
    assert box.state.selected_member_id is None


def test_watch_offline_is_reported():
    watch = MockGalaxyWatchAdapter()
    watch.fail_next = "offline"
    box, coord, *_ = make_box(watch=watch)

    async def go():
        await call(box, "get_member_status", member_id="member-dad")
        await call(box, "request_health_consent", member_id="member-dad")
        return await call(box, "get_watch_vitals", member_id="member-dad")

    result = run(go())
    assert result["error"] == "offline"
    assert "invent" in result["instruction"].lower()


def test_ambulance_without_explicit_flag_is_refused():
    box, *_rest = make_box()
    messenger = box.messenger
    result = run(call(box, "place_care_call", target_id="ambulance", reason="heart rate"))
    assert result["status"] == "refused"
    assert messenger.calls == []


def test_text_is_exactly_once():
    box, *_rest = make_box()
    messenger = box.messenger

    async def go():
        await call(box, "get_member_status", member_id="member-dad")
        first = await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert",
                           body="Dad's Watch is elevated.")
        second = await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert",
                            body="Please check.")
        lookup = await call(box, "get_outbound_status", contact_id="Priya", purpose="vitals_alert")
        return first, second, lookup

    first, second, lookup = run(go())
    assert first["status"] == "ok" and first["record_id"].startswith("MSG-")
    assert second["status"] == "already_done"
    assert lookup["record"]["record_id"] == first["record_id"]
    assert len(messenger.texts) == 1


def test_rephrased_text_body_does_not_send_again():
    box, *_ = make_box()
    messenger = box.messenger

    async def go():
        first = await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert",
                           body="Dad's Watch is elevated.")
        second = await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert",
                            body="Please check on Dad now.")
        return first, second

    first, second = run(go())
    assert first["status"] == "ok"
    assert second["status"] == "already_done"
    assert len(messenger.texts) == 1


def test_stale_epoch_never_runs_the_old_plan():
    box, coord, *_ = make_box()

    async def go():
        await call(box, "get_member_status", member_id="member-mum")
        stale = coord.epoch
        coord.user_started_speaking()
        coord.user_stopped_speaking()
        coord.turn_committed()
        return await call(box, "get_member_status", epoch=stale, member_id="member-dad")

    result = run(go())
    assert result["status"] == "not_executed"
    assert box.state.superseded >= 1


def test_stale_committed_read_does_not_steal_selection():
    box, coord, household, *_ = make_box()
    inner = household.get_member

    def bump(member_id):
        coord.user_started_speaking()
        coord.user_stopped_speaking()
        return inner(member_id)

    household.get_member = bump
    run(call(box, "get_member_status", member_id="member-mum"))
    assert "member-mum" in box.state.members
    assert box.state.selected_member_id is None


def test_unknown_text_timeout_is_not_retried():
    messenger = MockCareMessenger()
    messenger.delay_s = 1.0
    box, *_ = make_box(messenger=messenger)
    box.timeout_write = 0.05

    async def go():
        first = await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert",
                           body="hello")
        second = await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert",
                            body="hello again")
        return first, second

    first, second = run(go())
    assert first["status"] == "unknown_outcome"
    assert second["status"] == "unknown_outcome"
    assert "do not send again" in second["instruction"].lower()
    assert len(messenger.texts) <= 1


def test_mock_timeout_failure_is_not_retried():
    messenger = MockCareMessenger()
    messenger.fail_next = "timeout"
    box, *_ = make_box(messenger=messenger)

    async def go():
        first = await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert",
                           body="hello")
        second = await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert",
                            body="hello again")
        return first, second

    first, second = run(go())
    assert first["status"] == "unknown_outcome"
    assert second["status"] == "unknown_outcome"
    assert len(messenger.texts) == 0


def test_explicit_emergency_places_one_ambulance_call():
    box, *_ = make_box()
    messenger = box.messenger
    result = run(call(box, "place_care_call", target_id="ambulance", reason="user asked",
                      explicit_emergency=True))
    assert result["status"] == "ok" and result["record_id"].startswith("CALL-")
    assert len(messenger.calls) == 1
    again = run(call(box, "place_care_call", target_id="ambulance", reason="user asked",
                     explicit_emergency=True))
    assert again["status"] == "already_done"
    assert len(messenger.calls) == 1


def test_barge_in_during_text_still_records_once():
    messenger = MockCareMessenger()
    messenger.delay_s = 0.2
    box, coord, *_rest = make_box(messenger=messenger)
    messenger = box.messenger

    async def go():
        task = asyncio.ensure_future(box.call(
            "send_care_text",
            {"contact_id": "Priya", "purpose": "vitals_alert", "body": "check dad"},
            epoch=coord.epoch))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.3)
        again = await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert",
                           body="check dad")
        return again

    again = run(go())
    assert len(messenger.texts) == 1
    assert again["status"] in ("already_done", "ok")


def test_handoff_contains_context():
    box, *_ = make_box()

    async def go():
        await call(box, "get_member_status", member_id="member-dad")
        await call(box, "request_health_consent", member_id="member-dad")
        await call(box, "get_watch_vitals", member_id="member-dad")
        await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert", body="elevated")
        packet = await call(box, "prepare_care_handoff", member_id="member-dad",
                            extra_note="I am driving.")
        return packet["handoff"]

    packet = run(go())
    assert packet["member"]["name"] == "Dad"
    assert packet["knox_health_consent"] is True
    assert packet["vitals"]["band"] == "elevated"
    assert packet["outbounds"]
    assert any("driving" in n.lower() for n in packet["user_observations"])


def test_redaction_masks_phones_and_heart_rate():
    payload = redact({"phone": "5550199", "heart_rate_bpm": 118,
                      "member_id": "member-dad", "token": "secret-token-value"})
    assert payload["member_id"] == "member-dad"
    assert "*" in str(payload["phone"])
    assert "*" in str(payload["heart_rate_bpm"])
    assert "*" in payload["token"]


def test_real_adapters_refuse_without_credentials():
    with pytest.raises(KnoxError) as knox:
        RealKnoxVault("")
    assert knox.value.code == "auth"
    with pytest.raises(MessagingError) as msg:
        RealCareMessenger("http://example.com/sms")
    assert msg.value.code == "unavailable"
    RealCareMessenger("https://sms.example.invalid/v1")


def test_http_sms_url_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("DUET_FAMILY_SMS_URL", "http://127.0.0.1/secret")
    box = default_messenger()
    assert isinstance(box, MockCareMessenger)


def test_session_note_mentions_an_existing_text():
    box, *_ = make_box()

    async def go():
        await call(box, "get_member_status", member_id="member-dad")
        await call(box, "send_care_text", contact_id="Priya", purpose="vitals_alert", body="hi")
        return box.session_note()

    note = run(go())
    assert "ACTIVE TEXT" in note and "Do not send another text" in note
