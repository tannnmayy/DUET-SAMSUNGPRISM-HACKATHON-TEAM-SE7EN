"""The home through Samsung SmartThings: the real smart-home tools of DUET for Galaxy.

With SMARTTHINGS_TOKEN set (a personal access token from account.smartthings.com/tokens), the
home tools (get_home_status, control_home_device) act on the devices of a SmartThings home through
SmartThings' public REST API. DUET runs them on its own side, like the weather, so the token never
reaches the phone. The devices may be real or virtual; virtual ones (scripts/smartthings_setup.py)
appear and change live in Samsung's own SmartThings app and at my.smartthings.com.
Without a token, or with DUET_HOME=simulated, the phone app's simulated home answers instead.

A change is reported as done only once the device itself reports the new state (read back within a
few seconds). A command SmartThings accepted but the device has not yet confirmed comes back as
"sent", never as done.

Tokens made since 30 Dec 2024 last 24 hours: check() says so plainly when one has expired.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("duet.galaxy.smartthings")

API = "https://api.smartthings.com/v1"
EXPIRED = ("the SmartThings token was refused; tokens last 24 hours, so make a new one at "
           "account.smartthings.com/tokens and put it in .env.local as SMARTTHINGS_TOKEN")


def token() -> str:
    return (os.environ.get("SMARTTHINGS_TOKEN") or "").strip()


def enabled() -> bool:
    """SmartThings answers the home tools: a token is set and the home is not switched to simulated."""
    return bool(token()) and os.environ.get("DUET_HOME", "smartthings").strip().lower() != "simulated"


class HomeError(Exception):
    def __init__(self, code: Optional[int], message: str) -> None:
        super().__init__(message)
        self.code = code


# One connection pool per event loop (each conversation runs in its own), kept open: a new TLS
# connection for every request cost about 0.3 s each, and a change takes three requests.
_sessions: Dict[int, Tuple[Any, Any]] = {}


def _session():
    import aiohttp
    loop = asyncio.get_running_loop()
    held = _sessions.get(id(loop))
    if held is None or held[0] is not loop or held[1].closed:
        held = (loop, aiohttp.ClientSession())
        _sessions[id(loop)] = held
    return held[1]


async def close() -> None:
    """Close this event loop's connections (a script's last step)."""
    held = _sessions.pop(id(asyncio.get_running_loop()), None)
    if held is not None and not held[1].closed:
        await held[1].close()


# the app's drill: SmartThings answers nothing, as in a real outage (reset at each conversation start)
DRILL = {"down": False}


# SmartThings allows a token about 10 requests a minute per kind of request (X-RateLimit-Limit: 10, measured
# 18 Oct 2026). A limit that resets within this many seconds is waited out once; a longer one is reported.
RATE_WAIT_S = 3.0


def _reset_s(headers) -> Optional[float]:
    """Seconds until SmartThings' rate limit resets (X-RateLimit-Reset is in milliseconds)."""
    for key, scale in (("X-RateLimit-Reset", 1000.0), ("Retry-After", 1.0)):
        try:
            return float(headers.get(key)) / scale
        except (TypeError, ValueError):
            continue
    return None


async def request(method: str, path: str, body: Any = None, timeout: float = 6.0) -> Any:
    import aiohttp
    if DRILL["down"]:
        raise HomeError(None, "SmartThings cannot be reached (a drill: the home is cut off)")
    headers = {"Authorization": "Bearer " + token(), "Accept": "application/json"}
    for attempt in (1, 2):
        try:
            async with _session().request(method, API + path, json=body, headers=headers,
                                          timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 401:
                    raise HomeError(401, EXPIRED)
                if r.status == 403:
                    raise HomeError(403, "the SmartThings token is not allowed to do this; make one with every "
                                         "Devices and Locations box ticked")
                if r.status == 429:
                    # too many requests in the last minute (18 Oct 2026: the spoken suite, 40 scenarios in a
                    # row): a short wait is waited out once, so a command still goes through
                    wait = _reset_s(r.headers)
                    if attempt == 1 and wait is not None and wait <= RATE_WAIT_S:
                        log.info("SmartThings rate limit: waiting %.1f s", wait)
                        await asyncio.sleep(wait + 0.1)
                        continue
                    raise HomeError(429, "SmartThings is limiting requests right now (too many in the last "
                                         "minute)%s" % (": try again in %d s" % round(wait) if wait else ""))
                if r.status >= 400:
                    raise HomeError(r.status, "SmartThings answered %d: %s" % (r.status, (await r.text())[:160]))
                return await r.json(content_type=None) if r.content_length != 0 else {}
        except HomeError:
            raise
        except asyncio.TimeoutError:
            raise HomeError(None, "SmartThings did not answer within %.0f s" % timeout)
        except Exception as exc:  # no network, DNS, TLS
            raise HomeError(None, "SmartThings cannot be reached: %s" % (str(exc)[:120] or type(exc).__name__))
    raise HomeError(429, "SmartThings is limiting requests right now (too many in the last minute)")


# --- the home's devices ------------------------------------------------------------------------

_cache: Dict[str, Any] = {"at": 0.0, "location": None, "devices": None, "rooms": {}}
# The list of devices changes rarely (a name nobody has fetches it again at once), and SmartThings allows a
# token about 10 lists a minute: 18 Oct 2026, a fresh list at every conversation start and readiness check
# ran out, and the arrival offer could not read the home.
CACHE_S = 600.0


async def location_id() -> str:
    """SMARTTHINGS_LOCATION (an id or a home's name), else the token's first home."""
    if _cache["location"]:
        return _cache["location"]
    want = (os.environ.get("SMARTTHINGS_LOCATION") or "").strip().lower()
    items = (await request("GET", "/locations")).get("items", [])
    if not items:
        raise HomeError(None, "this SmartThings account has no home yet")
    pick = next((l for l in items if want and want in (l["locationId"].lower(), l["name"].lower())), items[0])
    _cache["location"] = pick["locationId"]
    return pick["locationId"]


def kind_of(device: Dict[str, Any]) -> str:
    """What a device is, from its capabilities (and its category, for a TV)."""
    main = next((c for c in device.get("components", []) if c.get("id") == "main"), {})
    caps = {c["id"] for c in main.get("capabilities", [])}
    cats = {c.get("name", "") for c in main.get("categories", [])}
    label = (device.get("label") or device.get("name") or "").lower()
    if caps & {"airConditionerMode", "thermostatCoolingSetpoint"}:
        return "ac"
    if "lock" in caps:
        return "lock"
    if "robotCleanerMovement" in caps:
        return "vacuum"
    if "washerOperatingState" in caps:
        return "washer"
    if "Television" in cats or re.search(r"\btv\b|television", label):
        return "tv"
    if "switchLevel" in caps or re.search(r"light|lamp|bulb", label):
        return "light"
    if "switch" in caps:
        return "switch"
    return "other"


ALIASES = {"ac": "ac aircon air conditioner conditioning cooler", "tv": "tv television telly",
           "lock": "lock door", "vacuum": "vacuum robot cleaner hoover", "washer": "washer washing machine laundry",
           "light": "light lights lamp bulb", "switch": "switch plug"}


async def devices(fresh: bool = False) -> List[Dict[str, Any]]:
    """The home's devices: [{id, label, room, kind}], cached for ten minutes. When SmartThings limits
    requests, the last list is used (devices are rarely added mid-conversation)."""
    now = time.monotonic()
    if not fresh and _cache["devices"] is not None and now - _cache["at"] < CACHE_S:
        return _cache["devices"]
    loc = await location_id()
    try:
        raw, rooms = await asyncio.gather(request("GET", "/devices?locationId=" + loc),
                                          request("GET", "/locations/%s/rooms" % loc))
    except HomeError as exc:
        if exc.code == 429 and _cache["devices"] is not None:
            log.info("SmartThings limits requests: the last list of devices is used")
            return _cache["devices"]
        raise
    names = {r["roomId"]: r["name"] for r in rooms.get("items", [])}
    out = [{"id": d["deviceId"], "label": d.get("label") or d.get("name") or "device",
            "room": names.get(d.get("roomId"), ""), "kind": kind_of(d)} for d in raw.get("items", [])]
    _cache.update(at=now, devices=out)
    return out


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 1 and w not in ("the", "my", "please", "in", "of", "on", "at", "our")]


def match(items: List[Dict[str, Any]], name: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """(the device the user named, the devices tied with it). A name that fits two devices equally
    ("the light", with two lights) is not guessed: the caller asks which one. Most of the words of
    the name must fit the device: "garage door" is not the front door lock."""
    q = _words(name)
    if not q:
        return None, []
    scored = []
    for d in items:
        words = set(_words(d["label"] + " " + d["room"] + " " + ALIASES.get(d["kind"], "")))
        score = sum(1 for w in q if any(x == w or x.startswith(w) or w.startswith(x) for x in words))
        if score * 2 <= len(q):
            continue
        exact = (d["label"].lower() == " ".join(q))
        scored.append((score + (2 if exact else 0), d))
    scored.sort(key=lambda s: -s[0])
    if not scored:
        return None, []
    best = [d for s, d in scored if s == scored[0][0]]
    return (best[0], []) if len(best) == 1 else (None, best)


def _value(status: Dict[str, Any], cap: str, attr: str) -> Any:
    return (((status.get("components") or {}).get("main") or {}).get(cap) or {}).get(attr, {}).get("value")


MOVEMENT = {"cleaning": "cleaning", "homing": "returning to its dock", "charging": "docked, charging",
            "idle": "idle", "pause": "paused", "powerOff": "off", "alarm": "stuck, needs help"}


def describe(kind: str, st: Dict[str, Any]) -> str:
    """A device's state in a few words, as the phone app's simulated home says it."""
    on = _value(st, "switch", "switch") == "on"
    if kind == "light":
        level = _value(st, "switchLevel", "level")
        return ("on, %s%%" % level if level is not None else "on") if on else "off"
    if kind == "ac":
        sp, mode, room = (_value(st, "thermostatCoolingSetpoint", "coolingSetpoint"),
                          _value(st, "airConditionerMode", "airConditionerMode"), _value(st, "temperatureMeasurement", "temperature"))
        temp = ("%s°C" % _num(sp)) if sp is not None else ""
        inside = (", room %s°C" % _num(room)) if room is not None else ""
        return ("on, %s %s%s" % (temp, mode or "", inside)).replace("  ", " ").strip() if on else \
            "off%s%s" % ((" (set to %s)" % temp) if temp else "", inside)
    if kind == "tv":
        vol = _value(st, "audioVolume", "volume")
        return ("on, volume %s" % vol if vol is not None else "on") if on else "off"
    if kind == "lock":
        return {"locked": "locked", "unlocked": "unlocked"}.get(_value(st, "lock", "lock"), "unknown")
    if kind == "vacuum":
        return MOVEMENT.get(_value(st, "robotCleanerMovement", "robotCleanerMovement"), "unknown")
    if kind == "washer":
        m = _value(st, "washerOperatingState", "machineState")
        job = _value(st, "washerOperatingState", "washerJobState")
        return {"run": "running" + (", %s" % job if job and job not in ("none", "finish") else ""),
                "pause": "paused", "stop": "finished" if job == "finish" else "stopped"}.get(m, "unknown")
    if kind == "switch":
        return "on" if on else "off"
    return "connected"


def _num(v: Any) -> Any:
    try:
        f = float(v)
        return int(f) if f.is_integer() else round(f, 1)
    except (TypeError, ValueError):
        return v


AC_MODES = ("cool", "dry", "fan", "auto", "heat")
CAN = {"light": ["turn_on", "turn_off", "set"], "ac": ["turn_on", "turn_off", "set"], "tv": ["turn_on", "turn_off", "set"],
       "switch": ["turn_on", "turn_off"], "lock": ["lock", "unlock"], "vacuum": ["start", "stop", "dock"],
       "washer": ["start", "stop"]}


def _cmd(cap: str, command: str, *args: Any) -> Dict[str, Any]:
    c = {"component": "main", "capability": cap, "command": command}
    if args:
        c["arguments"] = list(args)
    return c


def plan(kind: str, action: str, value: Any) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, str], Any], str]:
    """(commands, the state they should produce {(capability, attribute): value}, error)."""
    action = (action or "").strip().lower()
    v = None if value is None else str(value).strip().lower()
    if action == "turn_on" and v and "set" in CAN.get(kind, []):
        # "turn on the AC and set it to 22" often arrives as turn_on with the value: on, at that value
        # (measured 17 Oct 2026: the fast mind sent exactly that, and the AC came on at its old 24)
        action = "set"
    if action not in CAN.get(kind, []):
        return [], {}, "cannot %s" % action.replace("_", " ")
    num = None
    if v:
        m = re.search(r"-?\d+(\.\d+)?", v)
        num = float(m.group()) if m else None
    if action in ("turn_on", "turn_off"):
        state = "on" if action == "turn_on" else "off"
        return [_cmd("switch", state)], {("switch", "switch"): state}, ""
    if action in ("lock", "unlock"):
        state = "locked" if action == "lock" else "unlocked"
        return [_cmd("lock", action)], {("lock", "lock"): state}, ""
    if kind == "vacuum":
        mode = {"start": "cleaning", "stop": "idle", "dock": "homing"}[action]
        return [_cmd("robotCleanerMovement", "setRobotCleanerMovement", mode)], \
            {("robotCleanerMovement", "robotCleanerMovement"): mode}, ""
    if kind == "washer":
        state = {"start": "run", "stop": "pause"}[action]
        return [_cmd("washerOperatingState", "setMachineState", state)], {("washerOperatingState", "machineState"): state}, ""
    # set
    if kind == "light":
        if num is None:
            return [], {}, "say the brightness as a percent"
        level = int(max(1, min(100, round(num))))
        return [_cmd("switch", "on"), _cmd("switchLevel", "setLevel", level)], \
            {("switch", "switch"): "on", ("switchLevel", "level"): level}, ""
    if kind == "ac":
        if num is not None:
            temp = int(max(16, min(30, round(num))))
            return [_cmd("switch", "on"), _cmd("thermostatCoolingSetpoint", "setCoolingSetpoint", temp)], \
                {("switch", "switch"): "on", ("thermostatCoolingSetpoint", "coolingSetpoint"): temp}, ""
        mode = next((m for m in AC_MODES if v and m in v), None)
        if mode is None:
            return [], {}, "say a temperature (16 to 30) or a mode (%s)" % ", ".join(AC_MODES)
        return [_cmd("switch", "on"), _cmd("airConditionerMode", "setAirConditionerMode", mode)], \
            {("switch", "switch"): "on", ("airConditionerMode", "airConditionerMode"): mode}, ""
    if kind == "tv":
        if num is None:
            return [], {}, "say the volume as a number"
        vol = int(max(0, min(100, round(num))))
        return [_cmd("switch", "on"), _cmd("audioVolume", "setVolume", vol)], \
            {("switch", "switch"): "on", ("audioVolume", "volume"): vol}, ""
    return [], {}, "cannot set this device"


def _reached(st: Dict[str, Any], want: Dict[Tuple[str, str], Any]) -> bool:
    for (cap, attr), value in want.items():
        got = _value(st, cap, attr)
        if isinstance(value, (int, float)):
            try:
                if abs(float(got) - float(value)) > 0.5:
                    return False
            except (TypeError, ValueError):
                return False
        elif got != value:
            return False
    return True


async def status(device_id: str) -> Dict[str, Any]:
    return await request("GET", "/devices/%s/status" % device_id)


def _fail(error: HomeError, **extra: Any) -> Dict[str, Any]:
    return {"status": "error", "error": str(error), "source": "SmartThings",
            "instruction": "tell the user, in one short sentence, that the home cannot be reached right now",
            **extra}


# --- the two tools --------------------------------------------------------------------------------

async def get_home_status(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        items = await devices()
        states = await asyncio.gather(*(status(d["id"]) for d in items))
    except HomeError as exc:
        return _fail(exc)
    return {"status": "ok", "source": "SmartThings",
            "devices": ["%s%s: %s" % (d["label"], " (%s)" % d["room"] if d["room"] else "", describe(d["kind"], st))
                        for d, st in zip(items, states)]}


async def control_home_device(args: Dict[str, Any], confirm_s: float = 3.0) -> Dict[str, Any]:
    name, action, value = str(args.get("device") or ""), str(args.get("action") or ""), args.get("value")
    try:
        items = await devices()
        device, tied = match(items, name)
        if device is None and not tied:
            items = await devices(fresh=True)        # a device added since the last look
            device, tied = match(items, name)
    except HomeError as exc:
        return _fail(exc)
    if tied:
        # an error, not a result: nothing ran, so nothing may be remembered as done
        return {"status": "error", "error": "more than one device fits '%s'" % name,
                "which": [d["label"] for d in tied],
                "instruction": "ask the user which one, naming the options"}
    if device is None:
        return {"status": "error", "error": "no device called %s in the home" % name,
                "devices": [d["label"] for d in items]}
    commands, want, why = plan(device["kind"], action, value)
    if why:
        return {"status": "error", "error": "%s: %s" % (device["label"], why), "can": CAN.get(device["kind"], [])}
    try:
        await request("POST", "/devices/%s/commands" % device["id"], {"commands": commands})
    except HomeError as exc:
        return _fail(exc, device=device["label"])
    deadline = time.monotonic() + confirm_s
    st: Dict[str, Any] = {}
    # read back with growing pauses: a device usually reports at the first or second look, and every look
    # counts against SmartThings' ~10 requests a minute (every 0.25 s for 3 s could be 12 looks)
    pauses = iter((0.3, 0.5, 0.8, 1.2))
    try:
        while True:
            st = await status(device["id"])
            if _reached(st, want) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(min(next(pauses, 1.2), max(0.05, deadline - time.monotonic())))
    except HomeError as exc:
        # the command went out, and only the read-back failed: sent, not confirmed (never "failed", and
        # never "done")
        log.warning("SmartThings read-back of %s failed: %s", device["label"], exc)
    now = describe(device["kind"], st) if st else "unknown"
    if not _reached(st, want):
        return {"status": "sent", "device": device["label"], "now": now, "source": "SmartThings",
                "note": "SmartThings accepted the command, but the device has not confirmed the change yet",
                "instruction": "say it was sent and has not been confirmed yet; do not say it is done"}
    return {"status": "ok", "device": device["label"], "now": now, "room": device["room"], "source": "SmartThings"}


TOOLS = {"get_home_status": get_home_status, "control_home_device": control_home_device}


async def snapshot(kinds: Optional[set] = None) -> List[Dict[str, Any]]:
    """Every device and its state, for the phone's home panel; only these kinds of device, when given."""
    items = [d for d in await devices() if kinds is None or d["kind"] in kinds]
    states = await asyncio.gather(*(status(d["id"]) for d in items))
    out = []
    for d, st in zip(items, states):
        on = _value(st, "switch", "switch") == "on" or _value(st, "lock", "lock") == "unlocked" or \
            _value(st, "robotCleanerMovement", "robotCleanerMovement") == "cleaning" or \
            _value(st, "washerOperatingState", "machineState") == "run"
        out.append({"name": d["label"], "room": d["room"], "kind": d["kind"], "state": describe(d["kind"], st), "on": on})
    return out


async def finish_washer() -> str:
    """Demo and drill control: the home's (virtual) washer reports its cycle finished, as a real one would.
    Returns the washer's name, or '' when the home has none."""
    washer = next((d for d in await devices(fresh=True) if d["kind"] == "washer"), None)
    if washer is None:
        return ""
    state = lambda attr, value: {"component": "main", "capability": "washerOperatingState",  # noqa: E731
                                 "attribute": attr, "value": value}
    await request("POST", "/virtualdevices/%s/events" % washer["id"], {"deviceEvents": [
        state("machineState", "stop"), state("washerJobState", "finish"),
        {"component": "main", "capability": "switch", "attribute": "switch", "value": "off"}]})
    return washer["label"]


async def check() -> Dict[str, Any]:
    """Is the token alive, and what is in the home? For start-up and the readiness check."""
    if not token():
        return {"ok": False, "why": "no SmartThings token: the home is simulated"}
    try:
        items = await devices()      # the cached list when it is recent: a check must not spend the home's quota
    except HomeError as exc:
        return {"ok": False, "why": str(exc)}
    return {"ok": True, "devices": len(items), "location": _cache["location"]}


def main() -> int:
    import json
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env.local"))

    async def run():
        try:
            out = await check()
            if out.get("ok"):
                out["home"] = await snapshot()
            return out
        finally:
            await close()
    result = asyncio.run(run())
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
