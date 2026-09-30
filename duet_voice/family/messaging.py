"""Outbound caregiver texts and calls.

There is no carrier SMS API in this repository. The mock desk is the demo
backend. A real adapter refuses unless DUET_FAMILY_SMS_URL is https.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


class MessagingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # timeout | unavailable | auth | refused


class CareMessenger:
    def send_text(self, *, contact_id: str, body: str, purpose: str) -> Dict[str, Any]:
        raise NotImplementedError

    def place_call(self, *, target_id: str, reason: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get(self, record_id: str) -> Dict[str, Any]:
        raise NotImplementedError


class MockCareMessenger(CareMessenger):
    def __init__(self) -> None:
        self.texts: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []
        self.fail_next: Optional[str] = None
        self.delay_s: float = 0.0
        self.simulated = True
        self._n = 0

    def _maybe_fail(self) -> None:
        mode = self.fail_next
        self.fail_next = None
        if mode == "timeout":
            raise MessagingError("timeout", "Messaging timed out")
        if mode == "unavailable":
            raise MessagingError("unavailable", "Messaging is unavailable")

    def send_text(self, *, contact_id: str, body: str, purpose: str) -> Dict[str, Any]:
        self._maybe_fail()
        if self.delay_s:
            time.sleep(self.delay_s)
        self._n += 1
        rec = {
            "status": "ok",
            "record_id": "MSG-%04d" % self._n,
            "kind": "text",
            "contact_id": contact_id,
            "purpose": purpose,
            "body_preview": (body or "")[:80],
            "state": "sent",
            "simulated": True,
            "created": time.time(),
        }
        self.texts.append(rec)
        return dict(rec)

    def place_call(self, *, target_id: str, reason: str) -> Dict[str, Any]:
        self._maybe_fail()
        if self.delay_s:
            time.sleep(self.delay_s)
        self._n += 1
        rec = {
            "status": "ok",
            "record_id": "CALL-%04d" % self._n,
            "kind": "call",
            "target_id": target_id,
            "reason": reason,
            "state": "placed",
            "simulated": True,
            "created": time.time(),
        }
        self.calls.append(rec)
        return dict(rec)

    def get(self, record_id: str) -> Dict[str, Any]:
        for rec in self.texts + self.calls:
            if rec.get("record_id") == record_id:
                return dict(rec)
        raise MessagingError("unavailable", "No outbound record '%s'" % record_id)


class RealCareMessenger(CareMessenger):
    def __init__(self, base_url: str, token: str = "") -> None:
        if not base_url:
            raise MessagingError("unavailable", "DUET_FAMILY_SMS_URL is missing")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise MessagingError("unavailable", "DUET_FAMILY_SMS_URL must be an https URL")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.simulated = False

    def send_text(self, *, contact_id: str, body: str, purpose: str) -> Dict[str, Any]:
        raise MessagingError(
            "unavailable",
            "No authorized messaging API is configured. Use the mock messenger for the demo.",
        )

    def place_call(self, *, target_id: str, reason: str) -> Dict[str, Any]:
        raise MessagingError(
            "unavailable",
            "No authorized calling API is configured. Use the mock messenger for the demo.",
        )

    def get(self, record_id: str) -> Dict[str, Any]:
        raise MessagingError("unavailable", "No authorized messaging API is configured.")
