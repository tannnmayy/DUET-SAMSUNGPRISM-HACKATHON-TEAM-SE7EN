"""Knox-style consent for family health reads.

There is no public Knox Health API in this repository. The mock vault is the
demo backend. A real adapter is used only when DUET_KNOX_TOKEN is set; otherwise
the real adapter refuses rather than inventing consent or vitals.
"""

from __future__ import annotations

from typing import Dict, Set
from urllib.parse import urlparse


class KnoxError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # auth | denied | unavailable | timeout


class KnoxVault:
    def grant_health_read(self, member_id: str) -> Dict[str, object]:
        raise NotImplementedError

    def has_health_read(self, member_id: str) -> bool:
        raise NotImplementedError


class MockKnoxVault(KnoxVault):
    """In-process consent flags. Failures are injected for tests."""

    def __init__(self) -> None:
        self._granted: Set[str] = set()
        self.fail_next: str | None = None  # auth | timeout
        self.simulated = True

    def _maybe_fail(self) -> None:
        mode = self.fail_next
        self.fail_next = None
        if mode == "auth":
            raise KnoxError("auth", "Knox authentication failed")
        if mode == "timeout":
            raise KnoxError("timeout", "Knox consent timed out")

    def grant_health_read(self, member_id: str) -> Dict[str, object]:
        self._maybe_fail()
        if not member_id:
            raise KnoxError("denied", "No family member was named for consent")
        self._granted.add(member_id)
        return {
            "status": "ok",
            "member_id": member_id,
            "scope": "health_read",
            "knox": "mock_vault",
            "simulated": True,
            "instruction": "Health reads for this member are allowed in this session only.",
        }

    def has_health_read(self, member_id: str) -> bool:
        self._maybe_fail()
        return member_id in self._granted


class RealKnoxVault(KnoxVault):
    """Placeholder for an authorized Knox / Personal Data Engine binding.

    Refuses unless DUET_KNOX_TOKEN is set and, if a URL is given, it is https.
    """

    def __init__(self, token: str, base_url: str = "") -> None:
        if not token:
            raise KnoxError("auth", "DUET_KNOX_TOKEN is missing")
        if base_url:
            parsed = urlparse(base_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise KnoxError("unavailable", "DUET_KNOX_URL must be an https URL")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.simulated = False

    def grant_health_read(self, member_id: str) -> Dict[str, object]:
        raise KnoxError(
            "unavailable",
            "No authorized Knox Health API is configured. Use the mock vault for the demo.",
        )

    def has_health_read(self, member_id: str) -> bool:
        raise KnoxError(
            "unavailable",
            "No authorized Knox Health API is configured. Use the mock vault for the demo.",
        )
