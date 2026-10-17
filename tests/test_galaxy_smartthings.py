"""DUET for Galaxy's SmartThings home, against a fake SmartThings (no network, no token).

- the device the user named is found, and a name that fits two devices or none is never guessed;
- a change counts as done only when the device reports the new state; one SmartThings accepted but
  the device has not confirmed comes back as "sent";
- an expired token (they last 24 hours) is said plainly.
"""

import asyncio
import copy

import pytest

from duet_voice.galaxy import smartthings as st

LOC = "loc-1"
DEVICES = [
    {"deviceId": "light-1", "label": "Living room light", "roomId": "r-living",
     "components": [{"id": "main", "capabilities": [{"id": "switch"}, {"id": "switchLevel"}], "categories": [{"name": "Light"}]}]},
    {"deviceId": "light-2", "label": "Bedroom light", "roomId": "r-bed",
     "components": [{"id": "main", "capabilities": [{"id": "switch"}, {"id": "switchLevel"}], "categories": [{"name": "Light"}]}]},
    {"deviceId": "ac-1", "label": "Air conditioner", "roomId": "r-living",
     "components": [{"id": "main", "capabilities": [{"id": "switch"}, {"id": "airConditionerMode"},
                                                     {"id": "thermostatCoolingSetpoint"}, {"id": "temperatureMeasurement"}],
                     "categories": [{"name": "AirConditioner"}]}]},
    {"deviceId": "tv-1", "label": "TV", "roomId": "r-living",
     "components": [{"id": "main", "capabilities": [{"id": "switch"}, {"id": "audioVolume"}], "categories": [{"name": "Television"}]}]},
    {"deviceId": "lock-1", "label": "Front door lock", "roomId": "r-door",
     "components": [{"id": "main", "capabilities": [{"id": "lock"}], "categories": [{"name": "SmartLock"}]}]},
    {"deviceId": "vac-1", "label": "Robot vacuum", "roomId": "r-living",
     "components": [{"id": "main", "capabilities": [{"id": "robotCleanerMovement"}], "categories": [{"name": "RobotCleaner"}]}]},
]
ROOMS = [{"roomId": "r-living", "name": "Living room"}, {"roomId": "r-bed", "name": "Bedroom"},
         {"roomId": "r-door", "name": "Entrance"}]


def v(x):
    return {"value": x}


START = {
    "light-1": {"switch": {"switch": v("on")}, "switchLevel": {"level": v(70)}},
    "light-2": {"switch": {"switch": v("off")}, "switchLevel": {"level": v(50)}},
    "ac-1": {"switch": {"switch": v("off")}, "airConditionerMode": {"airConditionerMode": v("cool")},
             "thermostatCoolingSetpoint": {"coolingSetpoint": v(24)}, "temperatureMeasurement": {"temperature": v(29)}},
    "tv-1": {"switch": {"switch": v("off")}, "audioVolume": {"volume": v(20)}},
    "lock-1": {"lock": {"lock": v("locked")}},
    "vac-1": {"robotCleanerMovement": {"robotCleanerMovement": v("charging")}},
}
EFFECT = {("switch", "on"): ("switch", "switch", "on"), ("switch", "off"): ("switch", "switch", "off"),
          ("switchLevel", "setLevel"): ("switchLevel", "level", None),
          ("thermostatCoolingSetpoint", "setCoolingSetpoint"): ("thermostatCoolingSetpoint", "coolingSetpoint", None),
          ("airConditionerMode", "setAirConditionerMode"): ("airConditionerMode", "airConditionerMode", None),
          ("audioVolume", "setVolume"): ("audioVolume", "volume", None),
          ("lock", "lock"): ("lock", "lock", "locked"), ("lock", "unlock"): ("lock", "lock", "unlocked"),
          ("robotCleanerMovement", "setRobotCleanerMovement"): ("robotCleanerMovement", "robotCleanerMovement", None)}


class FakeSmartThings:
    def __init__(self, applies=True, refuse=None):
        self.state = copy.deepcopy(START)
        self.applies = applies        # False: commands are accepted but the devices never change
        self.refuse = refuse          # an HTTP code every request gets
        self.sent = []

    async def __call__(self, method, path, body=None, timeout=6.0):
        if self.refuse == 401:
            raise st.HomeError(401, st.EXPIRED)
        if self.refuse:
            raise st.HomeError(None, "SmartThings cannot be reached: no network")
        if path == "/locations":
            return {"items": [{"locationId": LOC, "name": "My home"}]}
        if path.startswith("/devices?locationId="):
            return {"items": DEVICES}
        if path.endswith("/rooms"):
            return {"items": ROOMS}
        dev = path.split("/")[2]
        if path.endswith("/status"):
            return {"components": {"main": copy.deepcopy(self.state[dev])}}
        if path.endswith("/commands"):
            self.sent.append((dev, body["commands"]))
            if self.applies:
                for c in body["commands"]:
                    cap, attr, value = EFFECT[(c["capability"], c["command"])]
                    self.state[dev].setdefault(cap, {})[attr] = v(value if value is not None else c["arguments"][0])
            return {"results": [{"status": "ACCEPTED"}]}
        raise AssertionError("unexpected request %s %s" % (method, path))


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        f = FakeSmartThings(**kw)
        monkeypatch.setattr(st, "request", f)
        monkeypatch.setenv("SMARTTHINGS_TOKEN", "test-token")
        st._cache.update(at=0.0, location=None, devices=None)
        return f
    return install


def run(coro):
    return asyncio.run(coro)


def test_the_device_the_user_named_is_found_and_never_guessed():
    items = [{"id": d["deviceId"], "label": d["label"], "room": {r["roomId"]: r["name"] for r in ROOMS}[d["roomId"]],
              "kind": st.kind_of(d)} for d in DEVICES]
    pick = lambda name: (lambda d, tied: (d["label"] if d else None, [t["label"] for t in tied]))(*st.match(items, name))
    assert pick("the AC") == ("Air conditioner", [])
    assert pick("air conditioning") == ("Air conditioner", [])
    assert pick("bedroom lamp") == ("Bedroom light", [])
    assert pick("lights in the living room") == ("Living room light", [])
    assert pick("front door") == ("Front door lock", [])
    assert pick("television") == ("TV", [])
    assert pick("the light") == (None, ["Living room light", "Bedroom light"])     # two lights: ask
    assert pick("garage door") == (None, [])                                        # not the front door
    assert pick("kitchen light") == (None, [])
    assert pick("") == (None, [])


def test_commands_for_each_kind_of_device():
    cmds, want, why = st.plan("ac", "set", "22 degrees")
    assert not why and want[("thermostatCoolingSetpoint", "coolingSetpoint")] == 22
    assert st.plan("ac", "set", "45")[1][("thermostatCoolingSetpoint", "coolingSetpoint")] == 30   # kept in range
    assert st.plan("ac", "set", "dry")[1][("airConditionerMode", "airConditionerMode")] == "dry"
    assert st.plan("ac", "set", "very nice")[2]
    assert st.plan("light", "set", "40%")[1][("switchLevel", "level")] == 40
    assert st.plan("lock", "turn_on", None)[2] == "cannot turn on"
    assert st.plan("vacuum", "dock", None)[1] == {("robotCleanerMovement", "robotCleanerMovement"): "homing"}
    assert st.plan("tv", "set", "35")[1][("audioVolume", "volume")] == 35
    # "turn on the AC and set it to 22", sent as turn_on with the value: on, at 22
    assert st.plan("ac", "turn_on", "22")[1] == {("switch", "switch"): "on", ("thermostatCoolingSetpoint", "coolingSetpoint"): 22}
    assert st.plan("ac", "turn_on", None)[1] == {("switch", "switch"): "on"}
    assert st.plan("lock", "turn_on", "5")[2] == "cannot turn on"


def test_a_change_is_done_only_when_the_device_reports_it(fake):
    f = fake()
    r = run(st.control_home_device({"device": "AC", "action": "set", "value": "22"}))
    assert r["status"] == "ok" and r["device"] == "Air conditioner" and r["now"].startswith("on, 22°C cool")
    assert f.state["ac-1"]["switch"]["switch"]["value"] == "on"
    r = run(st.control_home_device({"device": "front door", "action": "unlock"}))
    assert r == {"status": "ok", "device": "Front door lock", "now": "unlocked", "room": "Entrance", "source": "SmartThings"}


def test_a_command_the_device_never_confirms_is_sent_not_done(fake):
    fake(applies=False)
    r = run(st.control_home_device({"device": "TV", "action": "turn_on"}, confirm_s=0.3))
    assert r["status"] == "sent" and "not confirmed" in r["note"] and r["now"] == "off"


def test_a_name_that_fits_two_devices_or_none_sends_nothing(fake):
    f = fake()
    r = run(st.control_home_device({"device": "the light", "action": "turn_off"}))
    assert r["status"] == "error" and r["which"] == ["Living room light", "Bedroom light"] and "ask" in r["instruction"]
    r = run(st.control_home_device({"device": "garage door", "action": "unlock"}))
    assert r["status"] == "error" and "Front door lock" in r["devices"]
    r = run(st.control_home_device({"device": "front door lock", "action": "turn_on"}))
    assert r["status"] == "error" and r["can"] == ["lock", "unlock"]
    assert f.sent == []


def test_an_expired_token_is_said_plainly(fake):
    fake(refuse=401)
    r = run(st.control_home_device({"device": "AC", "action": "turn_on"}))
    assert r["status"] == "error" and "24 hours" in r["error"]
    assert run(st.get_home_status({}))["status"] == "error"
    out = run(st.check())
    assert out["ok"] is False and "account.smartthings.com/tokens" in out["why"]


def test_the_home_status_reads_every_device(fake):
    fake()
    r = run(st.get_home_status({}))
    assert r["status"] == "ok" and r["source"] == "SmartThings"
    assert "Air conditioner (Living room): off (set to 24°C), room 29°C" in r["devices"]
    assert "Robot vacuum (Living room): docked, charging" in r["devices"]
    snap = run(st.snapshot())
    assert {d["name"]: d["on"] for d in snap}["Living room light"] is True


def test_the_home_is_simulated_without_a_token(monkeypatch):
    monkeypatch.delenv("SMARTTHINGS_TOKEN", raising=False)
    assert not st.enabled()
    monkeypatch.setenv("SMARTTHINGS_TOKEN", "x")
    monkeypatch.setenv("DUET_HOME", "simulated")
    assert not st.enabled()
    monkeypatch.setenv("DUET_HOME", "smartthings")
    assert st.enabled()
