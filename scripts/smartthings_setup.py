"""The DUET demo home in SmartThings: create its devices once, and reset them before every rehearsal.

    python scripts/smartthings_setup.py            # create what is missing, then reset every device
    python scripts/smartthings_setup.py --reset    # only put every device back to its starting state
    python scripts/smartthings_setup.py --list     # what is in the home now
    python scripts/smartthings_setup.py --delete   # remove the demo devices (after the hackathon)

Needs SMARTTHINGS_TOKEN in .env.local (account.smartthings.com/tokens, with the Devices and
Locations boxes ticked; a token lasts 24 hours). The devices are SmartThings virtual devices in
the token's home (SMARTTHINGS_LOCATION picks another): they show and change live in Samsung's
SmartThings app and at my.smartthings.com. The starting state matches the phone app's simulated
home, so the demo reads the same either way.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env.local"))

from duet_voice.galaxy import smartthings as st  # noqa: E402


def cmd(cap, command, *args):
    return st._cmd(cap, command, *args)


def ev(cap, attr, value, unit=None):
    e = {"component": "main", "capability": cap, "attribute": attr, "value": value}
    if unit:
        e["unit"] = unit
    return e


def profile(caps, category, ocf):
    return {"components": [{"id": "main", "capabilities": [{"id": c, "version": 1} for c in caps + ["refresh"]],
                            "categories": [{"name": category}]}], "metadata": {"ocfDeviceType": ocf}}


def demo_devices():
    done_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return [
        {"label": "Living room light", "room": "Living room", "prototype": "VIRTUAL_DIMMER_SWITCH",
         "commands": [cmd("switch", "on"), cmd("switchLevel", "setLevel", 70)]},
        {"label": "Bedroom light", "room": "Bedroom", "prototype": "VIRTUAL_DIMMER_SWITCH",
         "commands": [cmd("switchLevel", "setLevel", 50), cmd("switch", "off")]},
        {"label": "Air conditioner", "room": "Living room",
         "profile": profile(["switch", "airConditionerMode", "thermostatCoolingSetpoint", "temperatureMeasurement"],
                            "AirConditioner", "oic.d.airconditioner"),
         "events": [ev("switch", "switch", "off"), ev("airConditionerMode", "supportedAcModes", list(st.AC_MODES[:4])),
                    ev("airConditionerMode", "availableAcModes", list(st.AC_MODES[:4])),
                    ev("airConditionerMode", "airConditionerMode", "cool"),
                    ev("thermostatCoolingSetpoint", "coolingSetpointRange", {"minimum": 16, "maximum": 30, "step": 1}, "C"),
                    ev("thermostatCoolingSetpoint", "coolingSetpoint", 24, "C"),
                    ev("temperatureMeasurement", "temperatureRange", {"minimum": -10, "maximum": 50, "step": 0.5}, "C"),
                    ev("temperatureMeasurement", "temperature", 29, "C")]},
        {"label": "TV", "room": "Living room", "profile": profile(["switch", "audioVolume"], "Television", "oic.d.tv"),
         "events": [ev("switch", "switch", "off"), ev("audioVolume", "volume", 20, "%")]},
        {"label": "Front door lock", "room": "Entrance", "prototype": "VIRTUAL_LOCK", "commands": [cmd("lock", "lock")]},
        {"label": "Robot vacuum", "room": "Living room",
         "profile": profile(["robotCleanerMovement", "battery"], "RobotCleaner", "oic.d.robotcleaner"),
         "events": [ev("robotCleanerMovement", "robotCleanerMovement", "charging"), ev("battery", "battery", 85, "%")]},
        {"label": "Washer", "room": "Utility room",
         "profile": profile(["switch", "washerOperatingState"], "Washer", "oic.d.washer"),
         "events": [ev("switch", "switch", "on"), ev("washerOperatingState", "supportedMachineStates", ["pause", "run", "stop"]),
                    ev("washerOperatingState", "machineState", "run"), ev("washerOperatingState", "washerJobState", "wash"),
                    ev("washerOperatingState", "completionTime", done_at)]},
    ]


async def rooms(loc, wanted):
    have = {r["name"]: r["roomId"] for r in (await st.request("GET", "/locations/%s/rooms" % loc)).get("items", [])}
    for name in wanted:
        if name not in have:
            have[name] = (await st.request("POST", "/locations/%s/rooms" % loc, {"name": name}))["roomId"]
            print("  room created:", name)
    return have


async def by_label(loc):
    items = (await st.request("GET", "/devices?locationId=" + loc)).get("items", [])
    return {(d.get("label") or d.get("name")): d for d in items}


async def create(loc):
    specs = demo_devices()
    room_ids = await rooms(loc, sorted({s["room"] for s in specs}))
    have = await by_label(loc)
    for s in specs:
        if s["label"] in have:
            continue
        body = {"name": s["label"], "owner": {"ownerType": "LOCATION", "ownerId": loc},
                "roomId": room_ids[s["room"]], "executionTarget": "CLOUD"}
        if "prototype" in s:
            body["prototype"] = s["prototype"]
            d = await st.request("POST", "/virtualdevices/prototypes", body)
        else:
            body["deviceProfile"] = s["profile"]
            d = await st.request("POST", "/virtualdevices", body)
        print("  device created: %s (%s)" % (s["label"], d["deviceId"]))


_HAVE: dict = {}   # the devices by label, looked up once per run: SmartThings allows about 10 lists a minute


async def reset(loc):
    if not _HAVE.get(loc):
        _HAVE[loc] = await by_label(loc)
    have = _HAVE[loc]
    for s in demo_devices():
        d = have.get(s["label"])
        if d is None:
            print("  missing: %s (run without --reset to create it)" % s["label"])
            continue
        if s.get("events"):
            await st.request("POST", "/virtualdevices/%s/events" % d["deviceId"], {"deviceEvents": s["events"]})
        if s.get("commands"):
            await st.request("POST", "/devices/%s/commands" % d["deviceId"], {"commands": s["commands"]})


async def show():
    st._cache.update(devices=None)
    for d in await st.snapshot():
        print("  %-18s %-12s %-7s %s" % (d["name"], d["room"], d["kind"], d["state"]))


async def delete(loc):
    have = await by_label(loc)
    for s in demo_devices():
        d = have.get(s["label"])
        if d is not None:
            await st.request("DELETE", "/devices/%s" % d["deviceId"])
            print("  deleted:", s["label"])


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--reset", action="store_true")
    g.add_argument("--list", action="store_true")
    g.add_argument("--delete", action="store_true")
    args = ap.parse_args()
    if not st.token():
        print("Set SMARTTHINGS_TOKEN in .env.local first (account.smartthings.com/tokens).")
        return 1
    try:
        loc = await st.location_id()
        if args.delete:
            await delete(loc)
            return 0
        if not args.list:
            if not args.reset:
                await create(loc)
            await reset(loc)
            await asyncio.sleep(1.5)       # the commands take a moment to show in the devices' state
        print("The DUET demo home (%s):" % loc)
        await show()
    except st.HomeError as exc:
        print("SmartThings:", exc)
        return 1
    finally:
        await st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
