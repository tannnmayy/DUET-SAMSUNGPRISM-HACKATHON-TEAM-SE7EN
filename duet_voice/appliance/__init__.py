"""DUET Smart Appliance Care.

Samsung appliance support on the existing dual-mind agent. The talker, thinker,
coordinator, epochs, commit gate and idempotency ledger are unchanged. This
package is the toolset and deterministic state for `DUET_USE_CASE=appliance`.
"""

from .state import ApplianceSessionState
from .tools import ApplianceToolbox, TOOL_SPECS

__all__ = ["ApplianceSessionState", "ApplianceToolbox", "TOOL_SPECS"]
