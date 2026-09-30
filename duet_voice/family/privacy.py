"""Redact credentials, phones and health fields before they are logged."""

from __future__ import annotations

from typing import Any

_SENSITIVE_KEYS = {
    "serial", "serial_number", "token", "access_token", "refresh_token",
    "authorization", "password", "api_key", "apikey", "pat", "secret",
    "email", "phone", "phone_number", "address", "postal_code", "zip",
    "contact_name", "customer_name",
    "heart_rate_bpm", "heart_rate", "hrv_ms", "spo2", "baseline_bpm",
    "vitals", "knox_token", "consent_token",
}


def mask_secret(value: str) -> str:
    if not value:
        return value
    if len(value) <= 4:
        return "****"
    keep = 2 if len(value) < 12 else 4
    return value[:keep] + "*" * max(4, len(value) - 2 * keep) + value[-keep:]


def _key_sensitive(key: str) -> bool:
    k = (key or "").lower()
    if k in _SENSITIVE_KEYS:
        return True
    return any(part in k for part in (
        "token", "secret", "password", "authorization", "serial", "phone",
        "heart_rate", "hrv", "spo2", "vitals"))


def redact(value: Any, key: str = "") -> Any:
    """A JSON-safe copy with secrets, phones and vitals masked. Member ids stay visible."""
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key) for v in value]
    if _key_sensitive(key) and value is not None:
        return mask_secret(str(value))
    return value
