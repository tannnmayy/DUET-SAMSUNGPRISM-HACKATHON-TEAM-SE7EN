"""SmartThings adapter: device discovery, status, health, diagnostics, commands.

`MockSmartThingsAdapter` is the demo backend. `RealSmartThingsAdapter` talks to
the public SmartThings REST API when `SMARTTHINGS_TOKEN` is set.

A command that returns ACCEPTED is queued, not proof the appliance finished.
Status after a command is whatever a later read or webhook reports.
"""

from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

log = logging.getLogger("duet.appliance.smartthings")

ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
ONLINE = "ONLINE"
OFFLINE = "OFFLINE"
UNHEALTHY = "UNHEALTHY"


class SmartThingsError(Exception):
    def __init__(self, code: str, message: str, retry_after_s: float = 0.0) -> None:
        super().__init__(message)
        self.code = code  # auth | timeout | rate_limit | http | offline | not_found
        self.retry_after_s = retry_after_s


def _now() -> float:
    return time.time()


# Demo household used by MockSmartThingsAdapter. Serials are fictional.
DEMO_DEVICES: Dict[str, Dict[str, Any]] = {
    "st-washer-laundry": {
        "device_id": "st-washer-laundry",
        "name": "Laundry washer",
        "label": "Washer",
        "manufacturer": "Samsung",
        "model": "WF45B6300AW",
        "serial": "0W4H8X123456",
        "appliance_type": "washer",
        "room": "Laundry",
        "online": True,
        "health": ONLINE,
        "error_code": "UE",
        "operating_state": "pause",
        "job_state": "spin",
        "diagnostics": {
            "unbalance_detected": True,
            "door_locked": True,
            "water_level": "low",
            "source": "mock_smartthings",
        },
    },
    "st-dryer-laundry": {
        "device_id": "st-dryer-laundry",
        "name": "Laundry dryer",
        "label": "Dryer",
        "manufacturer": "Samsung",
        "model": "DVE45B6300W",
        "serial": "0D9K2Y654321",
        "appliance_type": "dryer",
        "room": "Laundry",
        "online": True,
        "health": ONLINE,
        "error_code": "HE",
        "operating_state": "stop",
        "job_state": "finished",
        "diagnostics": {
            "temperature_sensor": "fault_reported",
            "door_closed": True,
            "source": "mock_smartthings",
            # lint filter is not a SmartThings attribute here; do not invent it
        },
    },
    "st-fridge-kitchen": {
        "device_id": "st-fridge-kitchen",
        "name": "Kitchen refrigerator",
        "label": "Fridge",
        "manufacturer": "Samsung",
        "model": "RF28R7351SR",
        "serial": "0RF28R000111",
        "appliance_type": "refrigerator",
        "room": "Kitchen",
        "online": True,
        "health": ONLINE,
        "error_code": None,
        "operating_state": "running",
        "job_state": "cooling",
        "diagnostics": {"cooler_temperature_c": 3, "source": "mock_smartthings"},
    },
    "st-dishwasher-kitchen": {
        "device_id": "st-dishwasher-kitchen",
        "name": "Kitchen dishwasher",
        "label": "Dishwasher",
        "manufacturer": "Samsung",
        "model": "DW80R9950UG",
        "serial": "0DW80R000222",
        "appliance_type": "dishwasher",
        "room": "Kitchen",
        "online": False,
        "health": OFFLINE,
        "error_code": None,
        "operating_state": None,
        "job_state": None,
        "diagnostics": None,
    },
}


class SmartThingsAdapter(ABC):
    @abstractmethod
    def list_devices(self) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    def get_status(self, device_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get_health(self, device_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get_diagnostics(self, device_id: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def execute_command(self, device_id: str, command: str, arguments: Optional[List[Any]] = None) -> Dict[str, Any]:
        ...

    def apply_webhook(self, event: Dict[str, Any]) -> None:
        """Optional. Real adapters receive SmartThings events; the mock applies them in tests."""


class MockSmartThingsAdapter(SmartThingsAdapter):
    """In-memory household. Failures are injected for tests; they never invent status."""

    def __init__(self, devices: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.devices = copy.deepcopy(devices or DEMO_DEVICES)
        self.queued: List[Dict[str, Any]] = []
        self.listeners: List[Callable[[Dict[str, Any]], None]] = []
        self.fail_next: Optional[str] = None  # auth | timeout | rate_limit
        self.command_delay_s: float = 0.0
        self.auto_apply_commands: bool = False

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        self.listeners.append(callback)

    def _maybe_fail(self) -> None:
        mode = self.fail_next
        self.fail_next = None
        if mode == "auth":
            raise SmartThingsError("auth", "SmartThings authentication failed")
        if mode == "timeout":
            raise SmartThingsError("timeout", "SmartThings request timed out")
        if mode == "rate_limit":
            raise SmartThingsError("rate_limit", "SmartThings rate limit", retry_after_s=1.0)

    def _device(self, device_id: str) -> Dict[str, Any]:
        if device_id not in self.devices:
            raise SmartThingsError("not_found", "no device '%s'" % device_id)
        return self.devices[device_id]

    def _public(self, raw: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out = {
            "device_id": raw["device_id"],
            "name": raw["name"],
            "label": raw.get("label"),
            "manufacturer": raw.get("manufacturer"),
            "model": raw.get("model"),
            "serial": raw.get("serial"),
            "appliance_type": raw.get("appliance_type"),
            "room": raw.get("room"),
            "online": bool(raw.get("online")),
            "health": raw.get("health"),
            "error_code": raw.get("error_code"),
            "operating_state": raw.get("operating_state"),
            "job_state": raw.get("job_state"),
            "source": "mock_smartthings",
        }
        if extra:
            out.update(extra)
        return out

    def list_devices(self) -> List[Dict[str, Any]]:
        self._maybe_fail()
        return [self._public(d) for d in self.devices.values()]

    def get_status(self, device_id: str) -> Dict[str, Any]:
        self._maybe_fail()
        raw = self._device(device_id)
        if raw.get("health") == OFFLINE or not raw.get("online"):
            return self._public(raw, extra={
                "status": "unavailable",
                "reason": "offline",
                "instruction": "The device is offline. Do not invent a diagnosis. Ask the user to check power and Wi-Fi, or pick another appliance.",
            })
        if raw.get("health") == UNHEALTHY:
            return self._public(raw, extra={
                "status": "ok",
                "health_note": "SmartThings reports the device unhealthy (inactive longer than its health interval). Readings may be stale.",
            })
        return self._public(raw, extra={"status": "ok"})

    def get_health(self, device_id: str) -> Dict[str, Any]:
        self._maybe_fail()
        raw = self._device(device_id)
        return {
            "device_id": device_id,
            "state": raw.get("health"),
            "online": bool(raw.get("online")),
            "source": "mock_smartthings",
        }

    def get_diagnostics(self, device_id: str) -> Dict[str, Any]:
        self._maybe_fail()
        raw = self._device(device_id)
        if raw.get("health") == OFFLINE or not raw.get("online"):
            return {
                "status": "unavailable",
                "reason": "offline",
                "device_id": device_id,
                "diagnostics": None,
                "instruction": "Diagnostics are unavailable while the device is offline. Do not invent them.",
            }
        diag = raw.get("diagnostics")
        if not diag:
            return {
                "status": "unavailable",
                "reason": "diagnostics_unavailable",
                "device_id": device_id,
                "error_code": raw.get("error_code"),
                "diagnostics": None,
                "instruction": "SmartThings did not return diagnostics for this device. Do not invent any.",
            }
        return {
            "status": "ok",
            "device_id": device_id,
            "error_code": raw.get("error_code"),
            "operating_state": raw.get("operating_state"),
            "job_state": raw.get("job_state"),
            "diagnostics": copy.deepcopy(diag),
        }

    def execute_command(self, device_id: str, command: str, arguments: Optional[List[Any]] = None) -> Dict[str, Any]:
        self._maybe_fail()
        raw = self._device(device_id)
        if raw.get("health") == OFFLINE or not raw.get("online"):
            return {
                "status": REJECTED,
                "reason": "offline",
                "device_id": device_id,
                "instruction": "SmartThings did not accept a command because the device is offline.",
            }
        if self.command_delay_s:
            time.sleep(self.command_delay_s)
        command_id = "cmd-" + uuid.uuid4().hex[:10]
        queued = {
            "command_id": command_id,
            "device_id": device_id,
            "command": command,
            "arguments": list(arguments or []),
            "acceptance": ACCEPTED,
            "applied": False,
            "queued_at": _now(),
        }
        self.queued.append(queued)
        if self.auto_apply_commands:
            self.apply_webhook({"device_id": device_id, "command_id": command_id,
                                "operating_state": command})
        return {
            "status": ACCEPTED,
            "command_id": command_id,
            "device_id": device_id,
            "command": command,
            "applied": False,
            "note": "SmartThings accepted the command and queued it. This is not proof the appliance finished the action.",
        }

    def apply_webhook(self, event: Dict[str, Any]) -> None:
        """Simulate an asynchronous state update (the real API would push this)."""
        device_id = event.get("device_id")
        if not device_id or device_id not in self.devices:
            return
        raw = self.devices[device_id]
        for key in ("error_code", "operating_state", "job_state", "online", "health"):
            if key in event:
                raw[key] = event[key]
        if "diagnostics" in event and event["diagnostics"] is not None:
            raw["diagnostics"] = {**(raw.get("diagnostics") or {}), **event["diagnostics"]}
        command_id = event.get("command_id")
        for q in self.queued:
            if q["command_id"] == command_id or (command_id is None and q["device_id"] == device_id and not q["applied"]):
                q["applied"] = True
                break
        payload = {"kind": "device_event", "device_id": device_id, "event": event, "t": _now()}
        for cb in self.listeners:
            try:
                cb(payload)
            except Exception:
                log.exception("webhook listener failed")


def _extract_attr(status: Dict[str, Any], capability: str, attribute: str) -> Any:
    components = status.get("components") or {}
    main = components.get("main") or {}
    cap = main.get(capability) or {}
    attr = cap.get(attribute) or {}
    return attr.get("value")


class RealSmartThingsAdapter(SmartThingsAdapter):
    """Public SmartThings REST API (`https://api.smartthings.com/v1`).

    Requires a personal access token or OAuth bearer token in SMARTTHINGS_TOKEN.
    There is no private Samsung appliance-diagnostics API here: only the public
    device list, status, health and commands. Missing attributes stay missing.
    """

    BASE = "https://api.smartthings.com/v1"

    def __init__(self, token: str, timeout_s: float = 8.0) -> None:
        if not token:
            raise SmartThingsError("auth", "SMARTTHINGS_TOKEN is missing")
        self._token = token
        self.timeout_s = timeout_s
        # Identity (name, capabilities) is stable for a session. Health and
        # status stay live so a changed error code is not served from cache.
        self._meta_by_id: Dict[str, Dict[str, Any]] = {}
        self._last_status_raw: Dict[str, Dict[str, Any]] = {}

    def _device_path(self, device_id: str, suffix: str = "") -> str:
        return "/devices/%s%s" % (quote(device_id, safe=""), suffix)

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(self.BASE + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self._token)
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json;charset=utf-8")
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise SmartThingsError("auth", "SmartThings authentication failed") from exc
            if exc.code == 429:
                retry = float(exc.headers.get("Retry-After") or 1)
                raise SmartThingsError("rate_limit", "SmartThings rate limit", retry_after_s=retry) from exc
            if exc.code == 404:
                raise SmartThingsError("not_found", "SmartThings device not found") from exc
            raise SmartThingsError("http", "SmartThings HTTP %s" % exc.code) from exc
        except URLError as exc:
            raise SmartThingsError("timeout", "SmartThings request failed: %s" % exc.reason) from exc

    def _meta(self, device_id: str) -> Dict[str, Any]:
        cached = self._meta_by_id.get(device_id)
        if cached is not None:
            return cached
        raw = self._request("GET", self._device_path(device_id))
        self._meta_by_id[device_id] = raw
        return raw

    def list_devices(self) -> List[Dict[str, Any]]:
        payload = self._request("GET", "/devices?includeHealth=true")
        out = []
        for d in payload.get("items") or []:
            device_id = d.get("deviceId")
            if device_id:
                self._meta_by_id[device_id] = d
            out.append(self._summarise_device(d))
        return out

    def get_status(self, device_id: str) -> Dict[str, Any]:
        health = self.get_health(device_id)
        if health.get("state") == OFFLINE:
            return {
                "status": "unavailable",
                "reason": "offline",
                "device_id": device_id,
                "health": OFFLINE,
                "online": False,
                "instruction": "The device is offline. Do not invent a diagnosis.",
                "source": "smartthings",
            }
        status = self._request("GET", self._device_path(device_id, "/status"))
        self._last_status_raw[device_id] = status
        summary = self._summarise_device(self._meta(device_id))
        summary.update({
            "status": "ok",
            "health": health.get("state"),
            "online": health.get("state") == ONLINE,
            "error_code": self._error_from_status(status),
            "operating_state": _extract_attr(status, "washerOperatingState", "machineState")
            or _extract_attr(status, "dryerOperatingState", "machineState")
            or _extract_attr(status, "switch", "switch"),
            "job_state": _extract_attr(status, "washerOperatingState", "washerJobState")
            or _extract_attr(status, "dryerOperatingState", "dryerJobState"),
            "source": "smartthings",
        })
        return summary

    def get_health(self, device_id: str) -> Dict[str, Any]:
        payload = self._request("GET", self._device_path(device_id, "/health"))
        return {
            "device_id": device_id,
            "state": payload.get("state"),
            "online": payload.get("state") == ONLINE,
            "last_updated": payload.get("lastUpdatedDate"),
            "source": "smartthings",
        }

    def get_diagnostics(self, device_id: str) -> Dict[str, Any]:
        status = self.get_status(device_id)
        if status.get("status") == "unavailable":
            return {**status, "diagnostics": None}
        # Reuse the status body from get_status. Never synthesise an error.
        raw = self._last_status_raw.get(device_id) or {}
        diagnostics = {
            "washerOperatingState": (raw.get("components") or {}).get("main", {}).get("washerOperatingState"),
            "dryerOperatingState": (raw.get("components") or {}).get("main", {}).get("dryerOperatingState"),
            "source": "smartthings",
        }
        present = {k: v for k, v in diagnostics.items() if v}
        if len(present) <= 1:  # only source
            return {
                "status": "unavailable",
                "reason": "diagnostics_unavailable",
                "device_id": device_id,
                "error_code": status.get("error_code"),
                "diagnostics": None,
                "instruction": "SmartThings did not return appliance diagnostics. Do not invent them.",
            }
        return {
            "status": "ok",
            "device_id": device_id,
            "error_code": status.get("error_code"),
            "operating_state": status.get("operating_state"),
            "job_state": status.get("job_state"),
            "diagnostics": present,
        }

    def execute_command(self, device_id: str, command: str, arguments: Optional[List[Any]] = None) -> Dict[str, Any]:
        capability, name = self._command_map(command, device_id)
        body = {"commands": [{
            "component": "main",
            "capability": capability,
            "command": name,
            "arguments": list(arguments or []),
        }]}
        payload = self._request("POST", self._device_path(device_id, "/commands"), body)
        results = payload.get("results") or []
        first = results[0] if results else {}
        acceptance = str(first.get("status") or ACCEPTED)
        return {
            "status": acceptance,
            "command_id": first.get("id"),
            "device_id": device_id,
            "command": command,
            "applied": False,
            "note": "SmartThings accepted the command and queued it. This is not proof the appliance finished the action.",
            "source": "smartthings",
        }

    def _summarise_device(self, d: Dict[str, Any]) -> Dict[str, Any]:
        health = ((d.get("health") or {}).get("state") if isinstance(d.get("health"), dict) else None)
        model = None
        for comp in d.get("components") or []:
            for cat in (comp.get("categories") or []):
                model = model or cat.get("name")
        return {
            "device_id": d.get("deviceId"),
            "name": d.get("label") or d.get("name"),
            "label": d.get("label"),
            "manufacturer": (d.get("deviceManufacturerCode") or None),
            "model": model,
            "serial": None,  # not provided by the public list endpoint
            "appliance_type": self._type_from_device(d),
            "room": None,
            "health": health,
            "online": health == ONLINE if health else None,
            "source": "smartthings",
        }

    def _type_from_device(self, d: Dict[str, Any]) -> Optional[str]:
        names = []
        for comp in d.get("components") or []:
            for cap in (comp.get("capabilities") or []):
                names.append(str(cap.get("id") or "").lower())
            for cat in (comp.get("categories") or []):
                names.append(str(cat.get("name") or "").lower())
        blob = " ".join(names)
        # dishwasher contains the substring "washer"; check it first.
        if "dishwasher" in blob:
            return "dishwasher"
        if "washer" in blob:
            return "washer"
        if "dryer" in blob:
            return "dryer"
        if "refrigerator" in blob or "fridge" in blob:
            return "refrigerator"
        return None

    def _error_from_status(self, status: Dict[str, Any]) -> Optional[str]:
        for cap, attr in (
            ("washerOperatingState", "errorCode"),
            ("dryerOperatingState", "errorCode"),
            ("custom.error", "errorCode"),
        ):
            value = _extract_attr(status, cap, attr)
            if value:
                return str(value)
        return None

    def _command_map(self, command: str, device_id: str = "") -> tuple:
        c = (command or "").lower()
        if c in ("on", "off"):
            return "switch", c
        kind = self._type_from_device(self._meta_by_id.get(device_id) or {})
        capability = "dryerOperatingState" if kind == "dryer" else "washerOperatingState"
        if c in ("pause", "stop", "start", "setmachinestate"):
            # Public API still only queues this; verify_appliance_state confirms.
            return capability, "setMachineState" if c == "setmachinestate" else c
        return capability, command
