"""DUET SmartThings Family Care.

Caregiver voice on the existing dual-mind agent. The talker, thinker,
coordinator, epochs, commit gate and idempotency ledger are unchanged. This
package is the toolset and deterministic state for `DUET_USE_CASE=family`.

Live speech uses the same faster-whisper stack as the rest of DUET. Health
reads go through a Knox-style consent gate. Outbound texts go through the
ledger so a caregiver is never messaged twice.
"""

from .state import FamilySessionState
from .tools import FamilyToolbox, TOOL_SPECS

__all__ = ["FamilySessionState", "FamilyToolbox", "TOOL_SPECS"]
