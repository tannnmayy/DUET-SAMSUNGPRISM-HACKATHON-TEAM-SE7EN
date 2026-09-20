"""Schema-driven tool handling.

Roughly ten tools exist only in the hidden set, delivered as a schema in the
tool_manifest event 800ms before we are expected to call them correctly. So
nothing here may know a tool by name: selection, argument construction and
validation all derive from the schema alone.

Three jobs:

  1. Parse the manifest into typed specs (ToolSpec / ArgSpec).
  2. Rank tools against an utterance, using lexical evidence from each tool's
     own schema with IDF computed across the live manifest. A word shared by
     every tool carries no signal; a word unique to one tool carries a lot.
     This is ordinary sparse retrieval, chosen because it needs no model, is
     deterministic, and degrades sensibly on schemas we have never seen.
  3. Bind extracted values to arguments by ROLE rather than by name, so that
     `destination`, `city` and `pickup_city` all receive a place, and validate
     the result before it costs us a round trip.

Argument validation mirrors mock_env._validate_args exactly. An invalid call
does not crash anything, but it burns real seconds we cannot get back, and in
a scenario with a 6-second tail that can be the difference between answering
and not.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import contract

# ---------------------------------------------------------------------------
# Value roles.
#
# The canonical slot vocabulary is fixed by PROTOCOL.md section 2.5: it lists
# the slot names used in public AND hidden scenarios. Using it is reading the
# protocol, not memorising scenarios.
# ---------------------------------------------------------------------------
ROLE_PLACE = "place"
ROLE_PERSON = "person"
ROLE_DATE = "date"
ROLE_ID = "id"
ROLE_TEXT = "text"
ROLE_NUMBER = "number"
ROLE_ENUM = "enum"

# Canonical snapshot slot names (PROTOCOL.md section 2.5).
SLOT_DESTINATION = "destination"
SLOT_DATE = "date"
SLOT_FLIGHT_ID = "flight_id"
SLOT_PASSENGER = "passenger_name"
SLOT_BOOKING_ID = "booking_id"
SLOT_DEVICE_MODEL = "device_model"
SLOT_ISSUE_SUMMARY = "issue_summary"

CANONICAL_SLOTS = (
    SLOT_DESTINATION, SLOT_DATE, SLOT_FLIGHT_ID, SLOT_PASSENGER,
    SLOT_BOOKING_ID, SLOT_DEVICE_MODEL, SLOT_ISSUE_SUMMARY,
)

# Which argument names attract which role. Matched as substrings against the
# argument name and its description, so unseen names like `pickup_city` or
# `drop_off_location` still bind correctly.
_ROLE_HINTS: Dict[str, Tuple[str, ...]] = {
    ROLE_PLACE: ("city", "destination", "location", "place", "town", "airport",
                 "origin", "pickup", "dropoff", "drop_off", "where"),
    ROLE_PERSON: ("passenger", "name", "customer", "guest", "traveller",
                  "traveler", "person", "who"),
    ROLE_DATE: ("date", "day", "when", "departure", "checkin", "check_in",
                "start", "time"),
    ROLE_ID: ("_id", "id", "reference", "confirmation", "booking", "ticket",
              "code"),
    ROLE_TEXT: ("query", "summary", "description", "issue", "problem", "text",
                "question", "detail", "message", "symptom"),
}

# Tokens carrying no retrieval signal.
_STOPWORDS = frozenset("""
a an the and or of to for in on at by with from is are was were be been being
i me my you your he she it we they this that these those what which who whom
whose how when where why can could would should will shall may might must do
does did done have has had get got please just now then there here about into
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Ranking weights. Tuned against the public set and the unseen-tool fixtures
# in tests/test_tools.py; the ordering they produce matters, not the exact
# magnitudes.
NO_MATCH = -99.0            # nothing in the request points at this tool
VISUAL_BONUS = 1.5          # frame present and the schema accepts one
SATISFIABLE_BONUS = 0.75    # every required argument can be filled now
UNSATISFIABLE_PENALTY = 0.9  # required arguments we cannot supply
READ_ONLY_PREFERENCE = 0.3  # M2: free to cancel, free to abandon
SELECTION_THRESHOLD = 0.0   # best() requires a score above this
TEXT_FALLBACK_CREDIT = 0.35  # a catch-all query arg is weak evidence, not strong
COMMIT_BONUS = 0.8          # user asked to act AND we can act


def _tokens(text: Any) -> List[str]:
    return [t for t in _TOKEN_RE.findall(contract.norm(text)) if t not in _STOPWORDS]


def _stem(token: str) -> str:
    """Deliberately crude suffix stripping - enough to match 'flights'/'flight'
    and 'booking'/'book' without a dependency."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _stems(text: Any) -> List[str]:
    return [_stem(t) for t in _tokens(text)]


@dataclass
class ArgSpec:
    """One argument of one tool, as declared in the manifest."""

    name: str
    type: str = "string"
    required: bool = False
    enum: Optional[List[Any]] = None
    items: Optional[str] = None
    properties: Dict[str, "ArgSpec"] = field(default_factory=dict)
    description: str = ""

    @property
    def is_object(self) -> bool:
        return self.type == "object"

    @property
    def is_array(self) -> bool:
        return self.type == "array"

    def role(self) -> str:
        """Which kind of value this argument wants.

        Decided from the declared type first (an enum or a number is
        unambiguous) and then from name and description keywords.
        """
        if self.enum:
            return ROLE_ENUM
        if self.type == "number":
            return ROLE_NUMBER
        haystack = contract.norm(self.name) + " " + contract.norm(self.description)
        # Longest hint wins, so 'destination' beats a bare 'to'.
        best_role, best_len = ROLE_TEXT, 0
        for role, hints in _ROLE_HINTS.items():
            for hint in hints:
                if hint in haystack and len(hint) > best_len:
                    best_role, best_len = role, len(hint)
        return best_role

    def document(self) -> List[str]:
        doc = _stems(self.name) + _stems(self.description)
        for value in self.enum or []:
            doc += _stems(value)
        for sub in self.properties.values():
            doc += sub.document()
        return doc


def _parse_arg(name: str, spec: Any) -> ArgSpec:
    if not isinstance(spec, dict):
        return ArgSpec(name=name)
    props = {}
    raw_props = spec.get("properties")
    if isinstance(raw_props, dict):
        for sub_name, sub_spec in raw_props.items():
            props[sub_name] = _parse_arg(sub_name, sub_spec)
    return ArgSpec(
        name=name,
        type=str(spec.get("type", "string")),
        required=bool(spec.get("required", False)),
        enum=list(spec["enum"]) if isinstance(spec.get("enum"), list) else None,
        items=spec.get("items") if isinstance(spec.get("items"), str) else None,
        properties=props,
        description=str(spec.get("description", "")),
    )


@dataclass
class ToolSpec:
    """One tool, exactly as the manifest describes it."""

    name: str
    kind: str = contract.READ_ONLY
    delay_range_ms: Tuple[float, float] = (600.0, 3000.0)
    description: str = ""
    args: Dict[str, ArgSpec] = field(default_factory=dict)
    default_result: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_state_modifying(self) -> bool:
        return self.kind == contract.STATE_MODIFYING

    @property
    def max_delay_ms(self) -> float:
        return float(self.delay_range_ms[1])

    def required_args(self) -> List[ArgSpec]:
        return [a for a in self.args.values() if a.required]

    def result_fields(self) -> List[str]:
        """Field names a successful call is expected to return.

        For manifest-declared tools the schema tells us the result shape via
        default_result, which is how we ground an answer in a tool we have
        never seen (docs/TOOLS.md, conventions 6 and 7).
        """
        return [k for k in self.default_result.keys() if k != "status"]

    def document(self) -> List[str]:
        doc = _stems(self.name.replace("_", " ")) + _stems(self.description)
        for arg in self.args.values():
            doc += doc_tokens_for(arg)
        for key in self.default_result:
            doc += _stems(key)
        return doc

    def wants_visual(self) -> bool:
        """True if this tool accepts an image/visual embedding argument.

        Structural signal, not a name match: an array-of-number argument whose
        name or description mentions vision is how a schema advertises that it
        can use a camera frame.
        """
        for arg in self.args.values():
            if not arg.is_array:
                continue
            haystack = contract.norm(arg.name) + " " + contract.norm(arg.description)
            if any(w in haystack for w in ("image", "visual", "embedding", "frame",
                                           "photo", "picture")):
                return True
        return False

    def visual_arg(self) -> Optional[ArgSpec]:
        for arg in self.args.values():
            if arg.is_array:
                haystack = contract.norm(arg.name) + " " + contract.norm(arg.description)
                if any(w in haystack for w in ("image", "visual", "embedding",
                                               "frame", "photo", "picture")):
                    return arg
        return None

    # -- validation (mirror of mock_env._validate_args) -------------------
    def validate(self, args: Any) -> List[str]:
        problems: List[str] = []
        if not isinstance(args, dict):
            return ["args must be an object"]
        for name, aspec in self.args.items():
            if aspec.required and name not in args:
                problems.append("missing required arg '" + name + "'")
                continue
            if name not in args:
                continue
            val = args[name]
            if aspec.enum is not None and val not in aspec.enum:
                problems.append("arg '" + name + "' must be one of " + repr(aspec.enum))
            if aspec.is_object:
                if not isinstance(val, dict):
                    problems.append("arg '" + name + "' must be an object")
                else:
                    for sub, sspec in aspec.properties.items():
                        if sspec.required and sub not in val:
                            problems.append(
                                "missing required field '" + name + "." + sub + "'")
                        elif sub in val and sspec.enum is not None and val[sub] not in sspec.enum:
                            problems.append("field '" + name + "." + sub
                                            + "' must be one of " + repr(sspec.enum))
            if aspec.is_array and not isinstance(val, list):
                problems.append("arg '" + name + "' must be an array")
        return problems


def doc_tokens_for(arg: ArgSpec) -> List[str]:
    return arg.document()


def _parse_tool(name: str, spec: Any) -> ToolSpec:
    if not isinstance(spec, dict):
        return ToolSpec(name=name)
    delay = spec.get("delay_range_ms")
    if isinstance(delay, (list, tuple)) and len(delay) == 2:
        try:
            delay_range = (float(delay[0]), float(delay[1]))
        except (TypeError, ValueError):
            delay_range = (600.0, 3000.0)
    else:
        delay_range = (600.0, 3000.0)
    # A manifest field of the wrong type must never raise: this is parsed on
    # the first event of every scenario, so a crash here is a zero.
    args = {}
    raw_args = spec.get("args")
    if isinstance(raw_args, dict):
        for arg_name, arg_spec in raw_args.items():
            args[arg_name] = _parse_arg(arg_name, arg_spec)
    default_result = spec.get("default_result")
    return ToolSpec(
        name=name,
        kind=str(spec.get("kind", contract.READ_ONLY)),
        delay_range_ms=delay_range,
        description=str(spec.get("description", "")),
        args=args,
        default_result=dict(default_result) if isinstance(default_result, dict) else {},
    )


class ToolRegistry:
    """Everything we know about the tools available in this scenario."""

    def __init__(self, tools: Optional[Dict[str, Any]] = None) -> None:
        self.tools: Dict[str, ToolSpec] = {}
        self._idf: Dict[str, float] = {}
        if tools:
            self.load(tools)

    def load(self, tools: Dict[str, Any]) -> None:
        self.tools = {name: _parse_tool(name, spec)
                      for name, spec in (tools or {}).items()
                      if isinstance(name, str)}
        self._build_idf()

    def _build_idf(self) -> None:
        """Inverse document frequency across the live manifest.

        A token appearing in every tool's schema ('city' when every tool takes
        a city) tells us nothing about which tool to pick; a token appearing in
        one ('weather') tells us almost everything.
        """
        n = max(len(self.tools), 1)
        df: Dict[str, int] = {}
        for spec in self.tools.values():
            for token in set(spec.document()):
                df[token] = df.get(token, 0) + 1
        self._idf = {t: math.log((n + 1) / (c + 0.5)) + 1.0 for t, c in df.items()}

    # -- lookups ---------------------------------------------------------
    def __contains__(self, name: object) -> bool:
        return name in self.tools

    def __len__(self) -> int:
        return len(self.tools)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self.tools.get(name)

    def names(self) -> List[str]:
        return sorted(self.tools)

    def descriptions(self) -> List[str]:
        return [s.description for s in self.tools.values() if s.description]

    def state_modifying_names(self) -> Set[str]:
        return {n for n, s in self.tools.items() if s.is_state_modifying}

    def read_only_names(self) -> Set[str]:
        return {n for n, s in self.tools.items() if not s.is_state_modifying}

    # -- ranking ---------------------------------------------------------
    def rank(
        self,
        utterance: str,
        *,
        has_frame: bool = False,
        available_values: Optional[Dict[str, Any]] = None,
        commit_intent: bool = False,
    ) -> List[Tuple[ToolSpec, float]]:
        """Score every tool against the utterance. Highest first.

        `available_values` are role->value pairs we already hold; a tool whose
        required arguments we can actually fill is preferred over one we would
        have to interrogate the user about.
        """
        query = set(_stems(utterance))
        values = available_values or {}
        scored: List[Tuple[ToolSpec, float]] = []

        for spec in self.tools.values():
            doc = set(spec.document())
            overlap = query & doc
            lexical = sum(self._idf.get(t, 1.0) for t in overlap)
            # Normalise by query length so long utterances do not dominate.
            if query:
                lexical /= math.sqrt(len(query))

            visual = has_frame and spec.wants_visual()

            # A tool is only a candidate if something in the request points at
            # it: lexical evidence, or a camera frame plus a schema that
            # advertises it can consume one. Without that we would happily
            # call a tool with no required arguments in response to "hello".
            if lexical <= 0.0 and not visual:
                scored.append((spec, NO_MATCH))
                continue

            score = lexical
            if visual:
                score += VISUAL_BONUS

            # Satisfiability. A tool whose required arguments we cannot fill is
            # not actionable now - book_flight before any flight_id exists is
            # the canonical case, and preferring it would start a chain we
            # cannot complete.
            fillable = self._fillable_fraction(spec, values, utterance)
            score += SATISFIABLE_BONUS * fillable
            score -= UNSATISFIABLE_PENALTY * (1.0 - fillable)

            # Reversibility (M2). On otherwise equal evidence prefer the tool
            # that is free to cancel and free to abandon. Small enough never to
            # override real lexical evidence for a state-modifying tool.
            if not spec.is_state_modifying:
                score += READ_ONLY_PREFERENCE
            elif commit_intent and fillable >= 1.0:
                # The user asked for the irreversible thing AND every argument
                # is already in hand. Deliberately gated on full satisfiability:
                # without that, "find a flight to Denver and book the 8 AM one"
                # would promote booking before any flight id exists.
                score += COMMIT_BONUS

            scored.append((spec, score))

        scored.sort(key=lambda pair: (-pair[1], pair[0].name))
        return scored

    def _fillable_fraction(self, spec: ToolSpec, values: Dict[str, Any],
                           utterance: str) -> float:
        """How well we could supply this tool's required arguments right now.

        An argument filled from a value we actually extracted (a place, a
        person, an id) is real evidence that this is the right tool. An
        argument filled only by the free-text fallback is not: a `query`
        parameter absorbs any sentence at all, so counting it fully would make
        every search tool look perfectly satisfiable for every request. That
        is how `lookup_manual` outranked `flight_search` for "find a flight to
        Denver and book the 8 AM one for Alice".
        """
        required = spec.required_args()
        if not required:
            return 1.0
        total = 0.0
        for arg in required:
            if self._value_for(arg, values, "") is not None:
                total += 1.0
            elif self._value_for(arg, values, utterance) is not None:
                total += TEXT_FALLBACK_CREDIT
        return total / len(required)

    def text_sink(self, utterance: str) -> Optional[ToolSpec]:
        """The best read-only tool that can absorb a free-text request.

        Lexical retrieval fails when the user describes a symptom and the tool
        describes a capability: "my TV is showing a blinking red light" shares
        no vocabulary with "retrieve pages from indexed device manuals". There
        is no overlap to find, so rank() correctly reports no evidence.

        This is the deliberate fallback for that case. It is exposed
        separately rather than folded into rank() so that the caller has to
        choose it: answering "what can you help me with?" must NOT reach here,
        and a tool call there is a scored routing failure.
        """
        candidates = []
        for spec in self.tools.values():
            if spec.is_state_modifying:
                continue
            required = spec.required_args()
            if not required:
                continue
            if all(a.role() == ROLE_TEXT or not a.required for a in required):
                candidates.append(spec)
        if not candidates:
            return None
        # Prefer the one with the most specific schema, then by name so the
        # choice is deterministic.
        candidates.sort(key=lambda s: (-len(s.args), s.name))
        return candidates[0]

    def best(self, utterance: str, **kwargs: Any) -> Optional[ToolSpec]:
        ranked = self.rank(utterance, **kwargs)
        if not ranked:
            return None
        spec, score = ranked[0]
        return spec if score > SELECTION_THRESHOLD else None

    # -- argument construction -------------------------------------------
    def build_args(
        self,
        spec: ToolSpec,
        values: Dict[str, Any],
        *,
        utterance: str = "",
        extras: Optional[Dict[str, Any]] = None,
        by_name: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Bind role->value pairs onto a tool's schema.

        Returns (args, missing_required_arg_names). Binding is by role so that
        `destination`, `city` and `pickup_city` all receive a place, and nested
        objects are constructed field by field.
        """
        args: Dict[str, Any] = {}
        missing: List[str] = []
        extras = extras or {}
        by_name = by_name or {}

        for name, aspec in spec.args.items():
            if name in extras:
                args[name] = extras[name]
                continue
            # An exact name match beats a role match. Roles collapse distinct
            # things: flight_id and booking_id are both ROLE_ID, so binding by
            # role alone lets a booking reference end up in a flight argument.
            if name in by_name and by_name[name] is not None:
                args[name] = self._coerce(aspec, by_name[name])
                continue
            value = self._value_for(aspec, values, utterance)
            if value is not None:
                args[name] = value
            elif aspec.required:
                missing.append(name)
        return args, missing

    def _value_for(self, aspec: ArgSpec, values: Dict[str, Any],
                   utterance: str) -> Any:
        if aspec.is_object:
            obj: Dict[str, Any] = {}
            for sub_name, sub_spec in aspec.properties.items():
                sub_value = self._value_for(sub_spec, values, utterance)
                if sub_value is not None:
                    obj[sub_name] = sub_value
            # Only offer the object if every required field is present.
            for sub_name, sub_spec in aspec.properties.items():
                if sub_spec.required and sub_name not in obj:
                    return None
            return obj or None

        if aspec.enum is not None:
            return self._pick_enum(aspec, values, utterance)

        role = aspec.role()
        if role in values and values[role] is not None:
            return self._coerce(aspec, values[role])

        # A free-text argument can always fall back to the utterance itself:
        # a search query is exactly "what the user said".
        if role == ROLE_TEXT and utterance.strip():
            return utterance.strip()
        return None

    def _pick_enum(self, aspec: ArgSpec, values: Dict[str, Any],
                   utterance: str) -> Any:
        """Choose an enum member, never invent one.

        A wrong enum value is an immediate invalid_args error, so when nothing
        matches we return None and let the caller decide between omitting an
        optional argument and asking the user.
        """
        options = aspec.enum or []
        low = contract.norm(utterance)
        # Direct mention wins.
        for option in options:
            if contract.norm(option) and contract.norm(option) in low:
                return option
        # A value we already hold, if it is a legal member.
        candidate = values.get(ROLE_ENUM)
        for option in options:
            if candidate is not None and contract.norm(option) == contract.norm(candidate):
                return option
        # Severity-style ordinal enums: a sensible neutral default is the
        # middle member, which is safer than guessing an extreme.
        if aspec.required and options:
            return options[len(options) // 2]
        return None

    @staticmethod
    def _coerce(aspec: ArgSpec, value: Any) -> Any:
        if aspec.type == "number":
            try:
                num = float(value)
                return int(num) if num.is_integer() else num
            except (TypeError, ValueError):
                return None
        if aspec.type == "boolean":
            if isinstance(value, bool):
                return value
            return contract.norm(value) in ("true", "yes", "1", "on")
        if aspec.is_array:
            return value if isinstance(value, list) else [value]
        if aspec.type == "string":
            return str(value)
        return value


def ground_fields(spec: Optional[ToolSpec], result: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """Pick the fields of a result worth speaking aloud.

    For a tool we have never seen, the schema's default_result names the
    fields that carry the answer. Falling back to whatever scalar fields the
    result actually contains keeps us useful when it does not.
    """
    if not isinstance(result, dict):
        return []
    preferred = spec.result_fields() if spec else []
    out: List[Tuple[str, Any]] = []
    for key in preferred:
        if key in result and _speakable(result[key]):
            out.append((key, result[key]))
    if out:
        return out
    for key, value in result.items():
        if key == "status":
            continue
        if _speakable(value):
            out.append((key, value))
    return out


def _speakable(value: Any) -> bool:
    return isinstance(value, (str, int, float)) and str(value).strip() != ""
