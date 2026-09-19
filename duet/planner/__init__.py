"""Planning: deciding what to do with a turn.

rules.py is the deterministic planner and the permanent floor. A model-backed
planner (Phase 3) races it rather than replacing it, so that losing the model
degrades quality instead of breaking the agent.
"""

from .rules import Plan, Planner, canonical_slot, select_item  # noqa: F401
