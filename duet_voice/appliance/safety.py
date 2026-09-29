"""Safety categories for appliance troubleshooting.

The knowledge base, not the model, decides whether a step is safe to speak.
Dangerous electrical, refrigerant, high-voltage and safety-bypass work is never
returned as a self-service instruction.
"""

from __future__ import annotations

SAFE_SELF_SERVICE = "SAFE_SELF_SERVICE"
CAUTION = "CAUTION"
PROFESSIONAL_REQUIRED = "PROFESSIONAL_REQUIRED"

ALLOWED = frozenset({SAFE_SELF_SERVICE, CAUTION, PROFESSIONAL_REQUIRED})

# Phrases that must never appear in spoken or stored instructions.
FORBIDDEN = (
    "bypass the lock",
    "defeat the fuse",
    "jumper the",
    "short the",
    "disable the safety",
    "tape the door switch",
    "cut the thermal",
    "recover refrigerant",
    "recharge refrigerant",
    "open the sealed system",
    "test live mains",
    "probe the live",
    "work on a live",
    "remove the high-voltage",
    "discharge the capacitor",  # technician-only; we never instruct this
)


def assert_safe_text(text: str) -> None:
    lower = (text or "").lower()
    for phrase in FORBIDDEN:
        if phrase in lower:
            raise ValueError("forbidden troubleshooting instruction: %s" % phrase)


def may_instruct_user(safety: str) -> bool:
    return safety in (SAFE_SELF_SERVICE, CAUTION)
