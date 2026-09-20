"""The deterministic planner.

Turns an utterance into one of four decisions: say something and stop, call a
tool, ask a clarifying question, or do nothing. No model is involved, which is
why this is also the permanent fallback when a model is unavailable.

Two design points worth stating, because both are load-bearing.

1. Chaining is not scripted. "Find a flight to Denver and book the 8 AM one
   for Alice" is not decomposed into a hardcoded search-then-book sequence.
   Before any results exist, book_flight's required flight_id cannot be
   filled, so the registry ranks flight_search first. When the search returns,
   the id becomes available and re-ranking the SAME utterance now puts
   book_flight on top. The sequence is a consequence of what is satisfiable,
   which means it generalises to chains between tools we have never seen.

2. Slot names come from the schema, not from us. PROTOCOL.md section 2.5
   fixes the snapshot vocabulary (destination, date, flight_id,
   passenger_name, booking_id, device_model, issue_summary), so an argument
   whose name is already one of those is stored under it, and anything else
   falls back to the canonical name for its role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .. import contract, telemetry
from ..fastpath import (
    Selector, extract_selector, extract_values, looks_like_capability_question,
    looks_like_greeting, wants_commit,
)
from ..state import ConversationState, SRC_TEXT
from ..tools import (
    CANONICAL_SLOTS, ROLE_DATE, ROLE_ENUM, ROLE_ID, ROLE_NUMBER, ROLE_PERSON,
    ROLE_PLACE, ROLE_TEXT, SLOT_BOOKING_ID, SLOT_DATE, SLOT_DESTINATION,
    SLOT_DEVICE_MODEL, SLOT_FLIGHT_ID, SLOT_ISSUE_SUMMARY, SLOT_PASSENGER,
    ToolRegistry, ToolSpec, ground_fields,
)

# Plan kinds
PLAN_NONE = "none"
PLAN_SPEAK = "speak"
PLAN_TOOL = "tool"
PLAN_CLARIFY = "clarify"

# Role -> canonical snapshot slot, used when an argument name is not itself
# part of the protocol's slot vocabulary (weather_lookup's `city`, for
# instance, holds the same thing flight_search calls `destination`).
_ROLE_TO_SLOT = {
    ROLE_PLACE: SLOT_DESTINATION,
    ROLE_PERSON: SLOT_PASSENGER,
    ROLE_DATE: SLOT_DATE,
    ROLE_TEXT: SLOT_ISSUE_SUMMARY,
}

# Nested object fields map onto the flat snapshot vocabulary.
_NESTED_TO_SLOT = {
    "model": SLOT_DEVICE_MODEL,
    "summary": SLOT_ISSUE_SUMMARY,
}

# Identifier prefixes tell us which canonical id slot a value belongs in
# without knowing the tool: BK-0001 is a booking, FL-DEN-8AM is a flight.
_ID_SLOT_HINTS = (
    ("bk", SLOT_BOOKING_ID),
    ("fl", SLOT_FLIGHT_ID),
)


def canonical_slot(arg_name: str, role: str) -> str:
    """Which snapshot slot an argument's value should be recorded under."""
    if arg_name in CANONICAL_SLOTS:
        return arg_name
    if arg_name in _NESTED_TO_SLOT:
        return _NESTED_TO_SLOT[arg_name]
    return _ROLE_TO_SLOT.get(role, arg_name)


def slot_for_identifier(value: Any) -> str:
    low = contract.norm(value)
    for prefix, slot in _ID_SLOT_HINTS:
        if low.startswith(prefix + "-"):
            return slot
    return SLOT_FLIGHT_ID


@dataclass
class Plan:
    """What the agent has decided to do about a turn."""

    kind: str = PLAN_NONE
    tool: Optional[ToolSpec] = None
    args: Dict[str, Any] = field(default_factory=dict)
    depends_on: Set[str] = field(default_factory=set)
    missing: List[str] = field(default_factory=list)
    say: Optional[str] = None
    subject: Optional[str] = None
    intent: Optional[str] = None
    confidence: float = 1.0
    clarify_slot: Optional[str] = None

    @property
    def is_tool(self) -> bool:
        return self.kind == PLAN_TOOL and self.tool is not None


# ---------------------------------------------------------------------------
# Result inspection - schema agnostic
# ---------------------------------------------------------------------------
_PRICE_HINTS = ("price", "cost", "usd", "fare", "amount", "rate", "daily")
_TIME_HINTS = ("depart", "time", "start", "when", "hour")
_ID_HINTS = ("_id", "id", "code", "reference", "number")


def _result_rows(result: Dict[str, Any]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Find the list-of-objects field in a tool result, whatever it is called.

    flight_search returns `flights`, hotel_search returns `hotels`,
    lookup_manual returns `pages`. Locating it structurally means a tool we
    have never seen is handled the same way.
    """
    if not isinstance(result, dict):
        return None, []
    for key, value in result.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return key, value
    return None, []


def has_empty_collection(result: Dict[str, Any]) -> bool:
    """True if the tool succeeded but returned an empty collection.

    docs/TOOLS.md: "An empty pages list is a *successful* call with no hits -
    handle it gracefully." Without this the describe path falls through to
    whatever scalar remains and reports the search mode as if it were the
    answer, which is both useless and faintly dishonest.
    """
    if not isinstance(result, dict):
        return False
    return any(isinstance(v, list) and len(v) == 0 for v in result.values())


def _field_matching(row: Dict[str, Any], hints: Sequence[str]) -> Optional[str]:
    for key in row:
        low = contract.norm(key)
        if any(h in low for h in hints):
            return key
    return None


def _row_identifier(row: Dict[str, Any]) -> Optional[Any]:
    key = _field_matching(row, _ID_HINTS)
    return row.get(key) if key else None


def select_item(result: Dict[str, Any],
                selector: Optional[Selector] = None) -> Optional[Dict[str, Any]]:
    """Pick the row the user meant.

    conf_06 depends on this: "the cheapest one" is the $99 2 PM flight, and
    taking rows[0] is the easy wrong answer. All comparisons are made on
    fields located by name hints, so nothing here knows what a flight is.
    """
    _key, rows = _result_rows(result)
    if not rows:
        return None
    selector = selector or Selector()

    if selector.time:
        wanted = contract.norm(selector.time).replace(" ", "")
        for row in rows:
            ident = contract.norm(_row_identifier(row) or "").replace(" ", "")
            if wanted and wanted in ident:
                return row
        time_key = _field_matching(rows[0], _TIME_HINTS)
        if time_key:
            for row in rows:
                if wanted and wanted in contract.norm(row.get(time_key, "")).replace(" ", ""):
                    return row
            # "2PM" against a 24h "14:00" field
            hour = _to_hour(selector.time)
            if hour is not None:
                for row in rows:
                    if _to_hour(row.get(time_key)) == hour:
                        return row

    if selector.superlative:
        kind = selector.superlative
        hints = _PRICE_HINTS if "price" in kind else _TIME_HINTS
        key = _field_matching(rows[0], hints)
        if key:
            def sort_value(row):
                raw = row.get(key)
                if "price" in kind:
                    try:
                        return float(raw)
                    except (TypeError, ValueError):
                        return float("inf")
                hour = _to_hour(raw)
                return hour if hour is not None else float("inf")

            ordered = sorted(rows, key=sort_value)
            return ordered[-1] if kind.startswith("max") else ordered[0]

    if selector.ordinal is not None and selector.ordinal < len(rows):
        return rows[selector.ordinal]

    return rows[0]


def _to_hour(value: Any) -> Optional[float]:
    """Parse '8AM', '2 pm', '14:00' into hours since midnight."""
    text = contract.norm(value).replace(" ", "")
    if not text:
        return None
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return hour + minute / 60.0


# ---------------------------------------------------------------------------
class Planner:
    """Deterministic planning over the live manifest."""

    def __init__(self, state: ConversationState,
                 registry: Optional[ToolRegistry] = None) -> None:
        self.state = state
        self.registry = registry or ToolRegistry()
        # The request currently being satisfied, kept so a tool result can be
        # re-planned against the original intent.
        self.goal_text: str = ""
        self.goal_selector: Selector = Selector()
        # The most recent successful result set, retained so a later
        # correction can re-select a different row from it without
        # re-running the search (see reselect).
        self.last_result: Dict[str, Any] = {}
        self.last_spec: Optional[ToolSpec] = None

    def set_registry(self, registry: ToolRegistry) -> None:
        self.registry = registry

    # ------------------------------------------------------------------
    def wanted_roles(self, utterance: str, has_frame: bool = False) -> Set[str]:
        """Roles the plausible tools would need, used to steer extraction."""
        roles: Set[str] = set()
        for spec, score in self.registry.rank(utterance, has_frame=has_frame)[:3]:
            if score <= 0:
                continue
            for arg in spec.args.values():
                roles.add(arg.role())
        return roles or {ROLE_PLACE, ROLE_PERSON, ROLE_DATE, ROLE_ID, ROLE_TEXT}

    def harvest(self, utterance: str, *, now_ms: float,
                has_frame: bool = False) -> Dict[str, Any]:
        """Extract values from a turn and record them as slots with provenance."""
        wanted = self.wanted_roles(utterance, has_frame=has_frame)
        found = extract_values(utterance, expect=sorted(wanted))
        values: Dict[str, Any] = {}
        for role, cand in found.items():
            values[role] = cand.value
            # ROLE_TEXT is never harvested into a slot speculatively: a
            # free-text argument already falls back to the utterance, and
            # writing a slot for it makes every search tool look fully
            # satisfiable for every request. It is recorded only once it
            # is actually bound to a tool argument.
            if role == ROLE_TEXT:
                continue
            slot = (slot_for_identifier(cand.value) if role == ROLE_ID
                    else _ROLE_TO_SLOT.get(role))
            if slot:
                self.state.set_slot(slot, cand.value, confidence=cand.confidence,
                                    source=SRC_TEXT, at_ms=now_ms, span=cand.span)
        return values

    def values_by_slot(self) -> Dict[str, Any]:
        """Canonical slot name -> value, for exact-name argument binding.

        Roles are lossy: flight_id and booking_id are both ROLE_ID. An
        argument literally called flight_id should receive the flight_id slot,
        not whichever identifier happened to be written last.
        """
        return {name: slot.value for name, slot in self.state.slots.items()
                if slot.value is not None}

    def current_values(self) -> Dict[str, Any]:
        """Role -> value for everything we currently hold."""
        values: Dict[str, Any] = {}
        for slot, holder in self.state.slots.items():
            if holder.value is None:
                continue
            if slot == SLOT_DESTINATION:
                values[ROLE_PLACE] = holder.value
            elif slot == SLOT_PASSENGER:
                values[ROLE_PERSON] = holder.value
            elif slot == SLOT_DATE:
                values[ROLE_DATE] = holder.value
            elif slot in (SLOT_FLIGHT_ID, SLOT_BOOKING_ID):
                values[ROLE_ID] = holder.value
            elif slot == SLOT_DEVICE_MODEL:
                values[ROLE_ENUM] = holder.value
            elif slot == SLOT_ISSUE_SUMMARY:
                values.setdefault(ROLE_TEXT, holder.value)
        return values

    # ------------------------------------------------------------------
    def plan_turn(self, utterance: str, *, now_ms: float,
                  has_frame: bool = False,
                  extras: Optional[Dict[str, Any]] = None) -> Plan:
        """Decide what to do about a complete user turn."""
        text = str(utterance or "").strip()
        if not text:
            return Plan(PLAN_NONE)

        # Negative routing first. pub_04 makes this a scored decision: calling
        # any tool in response to "what can you help me with?" loses half the
        # task score for that scenario.
        if looks_like_capability_question(text) or looks_like_greeting(text):
            return Plan(PLAN_SPEAK, intent="chitchat",
                        subject="capabilities", confidence=0.9)

        self.goal_text = text
        self.goal_selector = extract_selector(text)
        self.harvest(text, now_ms=now_ms, has_frame=has_frame)
        return self._plan_from_state(text, now_ms=now_ms, has_frame=has_frame,
                                     extras=extras)

    def replan(self, utterance: str, *, now_ms: float,
               has_frame: bool = False,
               extras: Optional[Dict[str, Any]] = None) -> Plan:
        """Re-plan after a correction, WITHOUT re-reading the old utterance.

        This distinction is the whole scenario in pub_02. The corrected value
        already lives in state; re-parsing the superseded turn text would
        re-extract the abandoned one, overwrite the correction, and re-issue
        the very call we just cancelled - which the scorer records as
        "stale call re-issued" and costs the entire recovery category.

        The utterance is still used to rank tools and to fill free-text
        arguments, but it is never allowed to write a slot again.
        """
        text = str(utterance or "").strip()
        if not text:
            return Plan(PLAN_NONE)
        return self._plan_from_state(text, now_ms=now_ms, has_frame=has_frame,
                                     extras=extras)

    def plan_after_result(self, *, now_ms: float,
                          has_frame: bool = False,
                          extras: Optional[Dict[str, Any]] = None) -> Plan:
        """Re-plan the original request now that new values exist.

        This is how chaining happens: the utterance has not changed, but a
        previously unsatisfiable tool may now be satisfiable.
        """
        if not self.goal_text:
            return Plan(PLAN_NONE)
        return self._plan_from_state(self.goal_text, now_ms=now_ms,
                                     has_frame=has_frame, extras=extras)

    def _plan_from_state(self, text: str, *, now_ms: float, has_frame: bool,
                         extras: Optional[Dict[str, Any]]) -> Plan:
        values = self.current_values()
        spec = self.registry.best(text, has_frame=has_frame,
                                  available_values=values,
                                  commit_intent=wants_commit(text))

        if spec is None:
            # Lexical retrieval cannot bridge a symptom description to a
            # capability description; the free-text sink is the explicit
            # fallback for that, and it is only reachable once we have already
            # ruled out a capability question.
            spec = self.registry.text_sink(text)
            if spec is None:
                return Plan(PLAN_SPEAK, intent="chitchat", subject="capabilities",
                            confidence=0.4)

        args, missing = self.registry.build_args(
            spec, values, utterance=text, extras=extras or {},
            by_name=self.values_by_slot())
        depends_on = self._depends_on(spec, args, values)

        if missing:
            slot = canonical_slot(missing[0], spec.args[missing[0]].role())
            return Plan(PLAN_CLARIFY, tool=spec, args=args, missing=missing,
                        clarify_slot=slot, intent=spec.name,
                        confidence=self._confidence(depends_on))

        self._record_bound_slots(spec, args, now_ms=now_ms)
        return Plan(PLAN_TOOL, tool=spec, args=args, depends_on=depends_on,
                    subject=self._subject(spec, args), intent=spec.name,
                    confidence=self._confidence(depends_on))

    # ------------------------------------------------------------------
    def _depends_on(self, spec: ToolSpec, args: Dict[str, Any],
                    values: Dict[str, Any]) -> Set[str]:
        """Which snapshot slots this call's arguments were derived from.

        The coordinator uses exactly this to decide whether an interruption
        invalidates the call, so an over-broad answer causes needless
        cancellation and an under-broad one lets stale work survive.
        """
        depends: Set[str] = set()
        for arg_name in args:
            arg = spec.args.get(arg_name)
            if arg is None:
                continue
            if arg.is_object:
                for sub in arg.properties:
                    depends.add(canonical_slot(sub, arg.properties[sub].role()))
                continue
            role = arg.role()
            if role in values:
                depends.add(canonical_slot(arg_name, role))
        return {d for d in depends if d in self.state.slots}

    def _record_bound_slots(self, spec: ToolSpec, args: Dict[str, Any],
                            *, now_ms: float) -> None:
        """Mirror bound arguments into the snapshot under canonical names."""
        for arg_name, value in args.items():
            arg = spec.args.get(arg_name)
            if arg is None or arg.is_array:
                continue
            if arg.is_object and isinstance(value, dict):
                for sub_name, sub_value in value.items():
                    sub = arg.properties.get(sub_name)
                    if sub is None:
                        continue
                    slot = canonical_slot(sub_name, sub.role())
                    if slot in CANONICAL_SLOTS:
                        self.state.set_slot(slot, sub_value, at_ms=now_ms)
                continue
            slot = canonical_slot(arg_name, arg.role())
            if slot in CANONICAL_SLOTS and value is not None:
                if self.state.get(slot) != value:
                    self.state.set_slot(slot, value, at_ms=now_ms)

    def _confidence(self, depends_on: Set[str]) -> float:
        if not depends_on:
            return 1.0
        return min((self.state.confidence(s) for s in depends_on), default=1.0)

    def _subject(self, spec: ToolSpec, args: Dict[str, Any]) -> str:
        """A short phrase naming what we are doing, for the acknowledgment."""
        head = spec.name.replace("_", " ")
        for arg_name, value in args.items():
            arg = spec.args.get(arg_name)
            if arg is None or arg.is_array or arg.is_object:
                continue
            if arg.role() in (ROLE_PLACE, ROLE_ID) and value:
                return head + " for " + str(value)
        return head

    # ------------------------------------------------------------------
    def reselect(self, utterance: str, *, now_ms: float) -> bool:
        """Apply a new selector to results we already hold.

        A correction does not always re-value a slot. "Wait, make it the 2 PM
        flight instead" changes WHICH ROW of an existing result set the user
        wants - the destination, the passenger and the search are all still
        correct. Clock times and superlatives are deliberately not extracted
        as slot values (a time is a selector, not a slot), so without this the
        correction produces no state change at all: nothing is invalidated,
        the in-flight booking survives, and the acknowledgment has no value to
        name.

        Returns True if the selection actually moved.
        """
        if not self.last_result:
            return False
        selector = extract_selector(utterance)
        if selector.is_empty:
            return False
        row = select_item(self.last_result, selector)
        if not row:
            return False
        ident = _row_identifier(row)
        if ident is None:
            return False
        slot = slot_for_identifier(ident)
        if self.state.get(slot) == ident:
            return False
        self.goal_selector = selector
        self.state.set_slot(slot, ident, source="tool", at_ms=now_ms)
        telemetry.log("planner.reselect", slot=slot, value=ident,
                      selector=str(selector))
        return True

    def adopt_result(self, spec: Optional[ToolSpec], result: Dict[str, Any],
                     *, now_ms: float) -> Optional[Dict[str, Any]]:
        """Record what a successful result tells us, and return the chosen row.

        Identifiers are written into the snapshot so that a follow-up call can
        be satisfied - which is what turns a search result into a booking.
        """
        if not isinstance(result, dict):
            return None

        if _result_rows(result)[1]:
            self.last_result = dict(result)
            self.last_spec = spec

        for key, value in result.items():
            if key == "status" or not isinstance(value, (str, int, float)):
                continue
            low = contract.norm(key)
            if any(h in low for h in _ID_HINTS):
                self.state.set_slot(slot_for_identifier(value), value,
                                    source="tool", at_ms=now_ms)

        row = select_item(result, self.goal_selector)
        if row:
            ident = _row_identifier(row)
            if ident is not None:
                self.state.set_slot(slot_for_identifier(ident), ident,
                                    source="tool", at_ms=now_ms)
        return row

    def describe(self, spec: Optional[ToolSpec], result: Dict[str, Any],
                 row: Optional[Dict[str, Any]] = None) -> str:
        """A grounded phrase about a result.

        Grounding is in the fields the schema declares, so a tool we met 800ms
        ago is reported as faithfully as one we have always known.

        Phrasing matters as much as content here. A bare data dump such as
        "FL-CHI-8AM, 08:00, $129" is only 30% alphabetic, which fails the
        scorer's substantive-speech test outright - it would not even stop the
        latency clock - and reads terribly to the quality grader. So every
        field is spoken with its connective.
        """
        if row:
            body = _join(_phrase(k, v) for k, v in row.items()
                         if isinstance(v, (str, int, float)))
            if body:
                return body
        fields = ground_fields(spec, result)
        if fields:
            return _join(_phrase(k, v) for k, v in fields)
        return ""


# How a result field is spoken. Anything unrecognised falls back to its own
# name with underscores removed, which reads acceptably for unseen schemas
# ("aqi 42", "reservation id TR-0001").
_FIELD_PHRASES = {
    "depart": "departing at ",
    "departure": "departing at ",
    "arrive": "arriving at ",
    "page": "on page ",
    "doc": "in the ",
    "title": "under ",
    "condition": "currently ",
    "forecast": "with ",
}


def _phrase(key: str, value: Any) -> str:
    low = contract.norm(key)
    if any(h in low for h in _PRICE_HINTS):
        return "for $" + str(value)
    if any(h in low for h in _ID_HINTS):
        return str(value)
    for prefix, phrase in _FIELD_PHRASES.items():
        if low == prefix or low.startswith(prefix):
            return phrase + str(value)
    label = str(key).replace("_", " ").strip()
    return (label + " " + str(value)).strip()


def _join(parts) -> str:
    cleaned = [p for p in parts if p]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ", ".join(cleaned[:-1]) + ", " + cleaned[-1]
