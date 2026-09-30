"""Which toolset the live agent loads.

Default is the FDB-v3 benchmark. `DUET_USE_CASE=appliance` selects Smart
Appliance Care. `DUET_USE_CASE=family` selects SmartThings Family Care.
Offline eval and reproduce.sh keep using the benchmark tools because they
construct `FdbToolbox` themselves.
"""

from __future__ import annotations

from typing import Any, List

from .config import CONFIG
from .prompts import (
    APPLIANCE_TALKER_INSTRUCTIONS,
    APPLIANCE_THINKER_INSTRUCTIONS,
    FAMILY_TALKER_INSTRUCTIONS,
    FAMILY_THINKER_INSTRUCTIONS,
    TALKER_INSTRUCTIONS,
    THINKER_INSTRUCTIONS,
)


def is_family() -> bool:
    return CONFIG.use_case in ("family", "family_care", "familycare")


def is_appliance() -> bool:
    return (not is_family()) and CONFIG.use_case in ("appliance", "appliances", "samsung", "care")


def thinker_instructions() -> str:
    if is_family():
        return FAMILY_THINKER_INSTRUCTIONS
    if is_appliance():
        return APPLIANCE_THINKER_INSTRUCTIONS
    return THINKER_INSTRUCTIONS


def talker_instructions() -> str:
    if is_family():
        return FAMILY_TALKER_INSTRUCTIONS
    if is_appliance():
        return APPLIANCE_TALKER_INSTRUCTIONS
    return TALKER_INSTRUCTIONS


def tool_specs() -> List[dict]:
    if is_family():
        from .family.tools import TOOL_SPECS
        return TOOL_SPECS
    if is_appliance():
        from .appliance.tools import TOOL_SPECS
        return TOOL_SPECS
    from .fdb_tools import TOOL_SPECS
    return TOOL_SPECS


def make_toolbox(room: str, coord) -> Any:
    if is_family():
        from .family.tools import FamilyToolbox
        return FamilyToolbox(room, coord)
    if is_appliance():
        from .appliance.tools import ApplianceToolbox
        return ApplianceToolbox(room, coord)
    from .fdb_tools import FdbToolbox
    return FdbToolbox(room, coord)
