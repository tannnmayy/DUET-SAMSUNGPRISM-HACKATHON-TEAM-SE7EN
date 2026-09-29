"""Samsung service booking adapter.

There is no authorized public Samsung technician-scheduling API in this
repository. `MockSamsungServiceAdapter` is the demo backend. A real HTTP
backend is used only when `SAMSUNG_SERVICE_API_URL` is set; otherwise the
real adapter refuses rather than pretending.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

# Demo calendar, relative to the hackathon demo date (Monday 28 Sep 2026).
DEMO_SLOTS: List[Dict[str, Any]] = [
    {"slot_id": "slot-2026-09-29-pm", "window": "tomorrow_afternoon",
     "when_label": "Tuesday 29 September, 1 to 5 pm", "start": "2026-09-29T13:00:00",
     "end": "2026-09-29T17:00:00"},
    {"slot_id": "slot-2026-09-30-am", "window": "wednesday_morning",
     "when_label": "Wednesday 30 September, 8 am to 12 pm", "start": "2026-09-30T08:00:00",
     "end": "2026-09-30T12:00:00"},
    {"slot_id": "slot-2026-10-02-am", "window": "friday_morning",
     "when_label": "Friday 2 October, 8 am to 12 pm", "start": "2026-10-02T08:00:00",
     "end": "2026-10-02T12:00:00"},
    {"slot_id": "slot-2026-10-02-pm", "window": "friday_afternoon",
     "when_label": "Friday 2 October, 1 to 5 pm", "start": "2026-10-02T13:00:00",
     "end": "2026-10-02T17:00:00"},
]


class ServiceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # timeout | http | unavailable | not_found | conflict


class SamsungServiceAdapter:
    def find_slots(self, *, device_id: str, window: str = "", postal_code: str = "") -> List[Dict[str, Any]]:
        raise NotImplementedError

    def create_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def get_request(self, request_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def cancel_request(self, request_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def attach_handoff(self, request_id: str, handoff: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class MockSamsungServiceAdapter(SamsungServiceAdapter):
    """In-memory service desk for the demo. Clearly simulated."""

    def __init__(self, slots: Optional[List[Dict[str, Any]]] = None) -> None:
        self.slots = list(slots or DEMO_SLOTS)
        self.requests: Dict[str, Dict[str, Any]] = {}
        self.fail_next: Optional[str] = None  # timeout | unavailable
        self.delay_s: float = 0.0
        self._n = 0
        self.simulated = True

    def _maybe_fail(self) -> None:
        mode = self.fail_next
        self.fail_next = None
        if mode == "timeout":
            raise ServiceError("timeout", "Samsung service timed out")
        if mode == "unavailable":
            raise ServiceError("unavailable", "Samsung service is unavailable")

    def find_slots(self, *, device_id: str, window: str = "", postal_code: str = "") -> List[Dict[str, Any]]:
        self._maybe_fail()
        if window:
            matched = [s for s in self.slots if s["window"] == window or s["slot_id"] == window]
            return [dict(s, device_id=device_id) for s in matched]
        return [dict(s, device_id=device_id) for s in self.slots]

    def create_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._maybe_fail()
        if self.delay_s:
            time.sleep(self.delay_s)
        self._n += 1
        request_id = "SSR-%04d" % self._n
        slot = next((s for s in self.slots if s["slot_id"] == payload.get("slot_id")), None)
        rec = {
            "status": "ok",
            "request_id": request_id,
            "device_id": payload.get("device_id"),
            "slot_id": payload.get("slot_id"),
            "window": payload.get("preferred_window") or (slot or {}).get("window"),
            "when_label": (slot or {}).get("when_label"),
            "issue_summary": payload.get("issue_summary"),
            "model": payload.get("model"),
            "created": time.time(),
            "state": "confirmed",
            "simulated": True,
            "handoff": None,
        }
        self.requests[request_id] = rec
        return dict(rec)

    def get_request(self, request_id: str) -> Dict[str, Any]:
        self._maybe_fail()
        rec = self.requests.get(request_id)
        if rec is None:
            raise ServiceError("not_found", "no service request '%s'" % request_id)
        return dict(rec)

    def cancel_request(self, request_id: str) -> Dict[str, Any]:
        self._maybe_fail()
        rec = self.requests.get(request_id)
        if rec is None:
            raise ServiceError("not_found", "no service request '%s'" % request_id)
        rec["state"] = "cancelled"
        rec["status"] = "ok"
        return dict(rec)

    def attach_handoff(self, request_id: str, handoff: Dict[str, Any]) -> Dict[str, Any]:
        rec = self.requests.get(request_id)
        if rec is None:
            raise ServiceError("not_found", "no service request '%s'" % request_id)
        rec["handoff"] = handoff
        return dict(rec)


class RealSamsungServiceAdapter(SamsungServiceAdapter):
    """HTTP adapter for an authorized Samsung service endpoint, if one is provided.

    Without SAMSUNG_SERVICE_API_URL this adapter refuses. It does not invent
    bookings and does not call any unofficial scraping endpoint.
    """

    def __init__(self, base_url: str, token: str = "", timeout_s: float = 10.0) -> None:
        if not base_url:
            raise ServiceError(
                "unavailable",
                "No authorized Samsung service API is configured. Use the mock adapter for the demo.",
            )
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ServiceError(
                "unavailable",
                "SAMSUNG_SERVICE_API_URL must be an https URL. Refusing %s" % (parsed.scheme or "missing-scheme"),
            )
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.simulated = False

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(self.base_url + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            raise ServiceError("http", "Samsung service HTTP %s" % exc.code) from exc
        except URLError as exc:
            raise ServiceError("timeout", "Samsung service failed: %s" % exc.reason) from exc

    def find_slots(self, *, device_id: str, window: str = "", postal_code: str = "") -> List[Dict[str, Any]]:
        query = urlencode({"device_id": device_id, "window": window, "postal_code": postal_code})
        payload = self._request("GET", "/slots?%s" % query)
        return list(payload.get("slots") or [])

    def create_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/requests", payload)

    def get_request(self, request_id: str) -> Dict[str, Any]:
        return self._request("GET", "/requests/%s" % quote(request_id, safe=""))

    def cancel_request(self, request_id: str) -> Dict[str, Any]:
        return self._request("POST", "/requests/%s/cancel" % quote(request_id, safe=""))

    def attach_handoff(self, request_id: str, handoff: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/requests/%s/handoff" % quote(request_id, safe=""), {"handoff": handoff})
