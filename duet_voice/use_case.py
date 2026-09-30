"""Which toolset the live agent loads.

Default `CONFIG.use_case` is still FDB-v3. `DUET_USE_CASE=appliance` or
`family` pins that toolset. If the env var is unset, the live agent and
typed chat route on the user's words (washer → appliance, Mum/Watch → family).
Offline eval and reproduce.sh construct `FdbToolbox` themselves, so routing
cannot change a scored run.
"""

from __future__ import annotations

import os
import re
from typing import Any, List, Optional

from .config import CONFIG
from .prompts import (
    APPLIANCE_TALKER_INSTRUCTIONS,
    APPLIANCE_THINKER_INSTRUCTIONS,
    FAMILY_TALKER_INSTRUCTIONS,
    FAMILY_THINKER_INSTRUCTIONS,
    TALKER_INSTRUCTIONS,
    THINKER_INSTRUCTIONS,
)

# Strong signals only. Weak follow-ups ("did you text her?", "Friday morning")
# must not yank a live session onto the other toolset.
_STRONG_FAMILY_RE = re.compile(
    r"\b(mum|mom|dad|priya|knox)\b"
    r"|heart\s*rate|\bhrv\b|\bspo2\b|galaxy watch"
    r"|check on (mum|mom|dad)"
    r"|text priya"
    r"|\bambulance\b",
    re.I,
)
_STRONG_APPLIANCE_RE = re.compile(
    r"\b(washer|washing machine|dryer|dishwasher|fridge|refrigerator|oven|appliance)\b"
    r"|\b(ue|he|5e|le|od)\s*error\b"
    r"|lint trap|lint filter"
    r"|samsung (washer|dryer|fridge|laundry|dishwasher)"
    r"|book .{0,24}(technician|service)",
    re.I,
)
_APPLIANCE_KIND_RE = re.compile(
    r"\b(washer|dryer|dishwasher|fridge|oven|appliance|washing machine)\b", re.I)


def auto_route_enabled() -> bool:
    return os.environ.get("DUET_USE_CASE") in (None, "", "auto")


def detect_domain(text: str) -> Optional[str]:
    """Strong domain from the user's words. None = stay on the current toolset."""
    q = (text or "").strip()
    if not q:
        return None
    family = bool(_STRONG_FAMILY_RE.search(q))
    appliance = bool(_STRONG_APPLIANCE_RE.search(q))
    if appliance and family:
        return "appliance" if _APPLIANCE_KIND_RE.search(q) else "family"
    if family:
        return "family"
    if appliance:
        return "appliance"
    return None


def resolve_live_use_case(text: str, current: str) -> str:
    if not auto_route_enabled():
        return CONFIG.use_case
    detected = detect_domain(text)
    if detected is None:
        return current or "benchmark"
    current = current or "benchmark"
    if current in ("family", "appliance") and detected != current:
        return detected
    return detected


def normalize_use_case(name: Optional[str] = None) -> str:
    name = (name if name is not None else CONFIG.use_case) or "benchmark"
    name = name.strip().lower()
    if name not in (
        "benchmark", "appliance", "appliances", "samsung", "care",
        "family", "family_care", "familycare",
    ):
        return "benchmark"
    return name


def is_family(name: Optional[str] = None) -> bool:
    return normalize_use_case(name) in ("family", "family_care", "familycare")


def is_appliance(name: Optional[str] = None) -> bool:
    return (not is_family(name)) and normalize_use_case(name) in (
        "appliance", "appliances", "samsung", "care")


def thinker_instructions(name: Optional[str] = None) -> str:
    if is_family(name):
        return FAMILY_THINKER_INSTRUCTIONS
    if is_appliance(name):
        return APPLIANCE_THINKER_INSTRUCTIONS
    return THINKER_INSTRUCTIONS


def talker_instructions(name: Optional[str] = None) -> str:
    if is_family(name):
        return FAMILY_TALKER_INSTRUCTIONS
    if is_appliance(name):
        return APPLIANCE_TALKER_INSTRUCTIONS
    return TALKER_INSTRUCTIONS


def tool_specs(name: Optional[str] = None) -> List[dict]:
    if is_family(name):
        from .family.tools import TOOL_SPECS
        return TOOL_SPECS
    if is_appliance(name):
        from .appliance.tools import TOOL_SPECS
        return TOOL_SPECS
    from .fdb_tools import TOOL_SPECS
    return TOOL_SPECS


def make_toolbox(room: str, coord, use_case: Optional[str] = None) -> Any:
    if is_family(use_case):
        from .family.tools import FamilyToolbox
        return FamilyToolbox(room, coord)
    if is_appliance(use_case):
        from .appliance.tools import ApplianceToolbox
        return ApplianceToolbox(room, coord)
    from .fdb_tools import FdbToolbox
    return FdbToolbox(room, coord)


def allow_long_tool_chains() -> None:
    """Appliance and family live turns need more than the FDB default of 8."""
    if os.environ.get("DUET_MAX_TOOL_STEPS") in (None, "") and CONFIG.max_tool_steps < 12:
        object.__setattr__(CONFIG, "max_tool_steps", 12)
