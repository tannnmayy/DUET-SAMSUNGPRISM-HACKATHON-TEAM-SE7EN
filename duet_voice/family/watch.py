"""Galaxy Watch vitals adapter.

Mock vitals for the demo household. A live binding would read Samsung Health /
Watch sensors only after Knox consent (enforced in the toolbox, not here).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional


class WatchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # timeout | offline | not_found | unavailable


DEMO_VITALS: Dict[str, Dict[str, Any]] = {
    "watch-mum": {
        "watch_id": "watch-mum",
        "member_id": "member-mum",
        "online": True,
        "on_wrist": False,
        "heart_rate_bpm": None,
        "baseline_bpm": 70,
        "band": "off_wrist",
        "source": "galaxy_watch",
    },
    "watch-dad": {
        "watch_id": "watch-dad",
        "member_id": "member-dad",
        "online": True,
        "on_wrist": True,
        "heart_rate_bpm": 118,
        "baseline_bpm": 72,
        "hrv_ms": 18,
        "spo2": 97,
        "band": "elevated",
        "source": "galaxy_watch",
    },
}


class GalaxyWatchAdapter:
    def get_vitals(self, watch_id: str) -> Dict[str, Any]:
        raise NotImplementedError


class MockGalaxyWatchAdapter(GalaxyWatchAdapter):
    def __init__(self, vitals: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self.vitals = copy.deepcopy(vitals or DEMO_VITALS)
        self.fail_next: Optional[str] = None
        self.simulated = True

    def _maybe_fail(self) -> None:
        mode = self.fail_next
        self.fail_next = None
        if mode == "timeout":
            raise WatchError("timeout", "Galaxy Watch timed out")
        if mode == "offline":
            raise WatchError("offline", "Galaxy Watch is offline")

    def get_vitals(self, watch_id: str) -> Dict[str, Any]:
        self._maybe_fail()
        rec = self.vitals.get(watch_id)
        if rec is None:
            raise WatchError("not_found", "No Galaxy Watch '%s'" % watch_id)
        if rec.get("online") is False:
            raise WatchError("offline", "Galaxy Watch is offline")
        payload = dict(rec)
        payload["status"] = "ok"
        payload["simulated"] = True
        payload["knox_protected"] = True
        if rec.get("on_wrist") is False:
            payload["heart_rate_bpm"] = None
            payload["hrv_ms"] = None
            payload["spo2"] = None
            payload["band"] = "off_wrist"
            payload["instruction"] = (
                "The Galaxy Watch is off-wrist. Do not invent a heart rate. "
                "Say that plainly and offer a caregiver text or inactivity context."
            )
            return payload
        payload["instruction"] = (
            "Report the band (normal or elevated) and the heart rate the Watch returned. "
            "Do not invent a diagnosis. Do not call emergency services unless the user asked."
        )
        return payload
