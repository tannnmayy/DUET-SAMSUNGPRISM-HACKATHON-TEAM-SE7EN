"""Which toolset the live agent loads.

Default is the FDB-v3 benchmark. `DUET_USE_CASE=appliance` selects Smart
Appliance Care. Offline eval and reproduce.sh keep using the benchmark tools
because they construct `FdbToolbox` themselves.
"""

from __future__ import annotations

from typing import Any, List

from .config import CONFIG
from .prompts import (
    APPLIANCE_TALKER_INSTRUCTIONS,
    APPLIANCE_THINKER_INSTRUCTIONS,
    TALKER_INSTRUCTIONS,
    THINKER_INSTRUCTIONS,
)


def is_appliance() -> bool:
    return CONFIG.use_case in ("appliance", "appliances", "samsung", "care")


def thinker_instructions() -> str:
    return APPLIANCE_THINKER_INSTRUCTIONS if is_appliance() else THINKER_INSTRUCTIONS


def talker_instructions() -> str:
    return APPLIANCE_TALKER_INSTRUCTIONS if is_appliance() else TALKER_INSTRUCTIONS


def tool_specs() -> List[dict]:
    if is_appliance():
        from .appliance.tools import TOOL_SPECS
        return TOOL_SPECS
    from .fdb_tools import TOOL_SPECS
    return TOOL_SPECS


def make_toolbox(room: str, coord) -> Any:
    if is_appliance():
        from .appliance.tools import ApplianceToolbox
        return ApplianceToolbox(room, coord)
    from .fdb_tools import FdbToolbox
    return FdbToolbox(room, coord)
