"""Shared helpers for the H9 fuzzers (test_fuzz_schema.py, test_fuzz_events.py).

Everything here is deterministic: every generator takes an explicit seed and
builds its `random.Random` from it, never from wall-clock time or an
unseeded global. No third-party dependency (stdlib `random` only).

Ground rules this file respects (notes/SPRINT_PLAN.md section 2 / H9):
  - agents only add test files here; nothing in duet/, tools/, harness/ is
    touched, and nothing here imports harness/ except read-only (the same
    thing tests/test_invariants.py already does).
  - DUET_NO_ASR / DUET_NO_EMBED / DUET_NO_VISION are set before the engine is
    ever imported, so no model loads and no GPU is touched.
  - invented tool names are generic verb_noun pairs, never the kit's own
    (flight_search, book_flight, cancel_booking, lookup_manual,
    create_support_ticket, weather_lookup, hotel_search, rental_car_quote).
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("DUET_NO_ASR", "1")
os.environ.setdefault("DUET_NO_EMBED", "1")
os.environ.setdefault("DUET_NO_VISION", "1")

from agent.agent import ParticipantAgent  # noqa: E402
from harness.mock_env import TOOL_REGISTRY, MockEnvironment  # noqa: E402
from harness.runner import EvaluationHarness  # noqa: E402
from harness.scorer import CANCEL_GRACE_MS  # noqa: E402

# ---------------------------------------------------------------------------
# invented tool vocabulary - generic verb/noun pairs, never the kit's own
# ---------------------------------------------------------------------------
VERB_NOUN_POOL: List[Tuple[str, str]] = [
    ("schedule", "appointment"), ("track", "package"), ("submit", "feedback"),
    ("query", "inventory"), ("adjust", "lighting"), ("request", "refund"),
    ("locate", "branch"), ("convert", "currency"), ("compare", "prices"),
    ("rate", "service"), ("log", "expense"), ("translate", "phrase"),
    ("plan", "route"), ("diagnose", "device"), ("update", "profile"),
    ("estimate", "delivery"), ("reserve", "table"), ("check", "balance"),
    ("post", "review"), ("open", "account"),
]

PLACE_WORDS = ["Fresno", "Leon", "Turin", "Kanpur", "Odense", "Salta",
               "Bendigo", "Kumasi", "Ghent", "Tartu"]
PERSON_WORDS = ["Jordan Alvi", "Priya Deshmukh", "Marek Nowak", "Aiko Tanaka",
                "Femi Okafor", "Teodora Ilic", "Casey Munoz", "Ravi Chandran"]
PLACE_PREPS = ["in", "at", "to", "for"]

# Handled TODAY by duet/fastpath.py::looks_like_greeting /
# looks_like_capability_question (exact / substring match against a fixed
# list) - these are expected to route to chit-chat with no tool call.
DISTRACTOR_SAFE = [
    "Hi", "Hello", "Hey", "Thanks", "Thank you", "Good morning",
    "What can you help me with?", "Who are you?", "What do you do?",
    "What kind of things can I ask you about?",
]
# Ordinary conversational acts NOT on that fixed list - documented gap, see
# notes/SPRINT_PLAN.md H1 ("Fix categories, never phrases" - these are
# generic English, not scenario phrases).
DISTRACTOR_GAP = [
    "Honestly, I'm a bit bored today.",
    "Hmm, let me think for a second.",
    "I'm just talking to my brother here, one moment.",
    "That's wonderful, thanks a lot!",
    "No worries, take your time.",
    "Sorry, can you hear me okay?",
    "Just checking if this thing works.",
    "Oh, never mind, forget I said anything.",
]

FREE_TEXT_WORDS = ("query", "question", "text", "message", "body", "summary",
                    "description", "details", "note", "comment", "issue",
                    "problem", "request", "search")


# ---------------------------------------------------------------------------
# arg builders - the closed type set from docs/TOOLS.md section 1.3
# ---------------------------------------------------------------------------
def _place_arg(rng: random.Random) -> Tuple[str, Dict[str, Any]]:
    name = rng.choice(["destination", "city", "location", "branch", "pickup_city"])
    desc = rng.choice(["City or place name.", "Where the " + name + " is.", ""])
    return name, {"type": "string", "required": True, "description": desc}


def _person_arg(rng: random.Random, required: bool) -> Tuple[str, Dict[str, Any]]:
    name = rng.choice(["passenger_name", "customer_name", "contact_name", "guest_name"])
    desc = rng.choice(["Full name.", "Name of the person.", ""])
    return name, {"type": "string", "required": required, "description": desc}


def _text_arg(rng: random.Random) -> Tuple[str, Dict[str, Any]]:
    name = rng.choice(["notes", "details", "summary", "query", "comment", "description"])
    desc = rng.choice(["", "Free-form text.", "Natural language " + name + "."])
    return name, {"type": "string", "required": rng.random() < 0.6, "description": desc}


def _enum_arg(rng: random.Random) -> Tuple[str, Dict[str, Any]]:
    name = rng.choice(["severity", "priority", "tier", "urgency"])
    options = rng.choice([["low", "medium", "high"],
                          ["standard", "express", "overnight"],
                          ["bronze", "silver", "gold"]])
    desc = rng.choice(["Pick one.", "", "Level of " + name + "."])
    return name, {"type": "string", "required": rng.random() < 0.7,
                  "enum": options, "description": desc}


def _number_arg(rng: random.Random) -> Tuple[str, Dict[str, Any]]:
    name = rng.choice(["quantity", "count", "nights", "guests", "amount"])
    desc = rng.choice(["How many.", "", name + " as a number."])
    return name, {"type": "number", "required": rng.random() < 0.5, "description": desc}


def _boolean_trap_arg(rng: random.Random) -> Tuple[str, Dict[str, Any]]:
    """A boolean argument whose name/description matches none of
    duet/tools.py's ROLE hints. ArgSpec.role() (duet/tools.py) special-cases
    only enum and number before falling back to keyword matching, so a
    boolean like this defaults to ROLE_TEXT and ToolRegistry._value_for
    (duet/tools.py ~line 626) hands it the entire utterance. This is the
    generator side of H3 (notes/SPRINT_PLAN.md); see test_fuzz_schema.py P3.
    """
    name = rng.choice(["urgent_flag", "priority_mode", "confirm", "active",
                       "eco_mode", "auto_retry"])
    desc = rng.choice(["", "Set this flag.", "Whether to enable it."])
    return name, {"type": "boolean", "required": rng.random() < 0.5, "description": desc}


def _array_arg(rng: random.Random) -> Tuple[str, Dict[str, Any]]:
    name = rng.choice(["tags", "categories", "notify_emails", "attachments"])
    items = rng.choice(["string", "number"])
    desc = rng.choice(["List of values.", "", name + " list."])
    return name, {"type": "array", "items": items, "required": rng.random() < 0.3,
                  "description": desc}


def _object_arg(rng: random.Random) -> Tuple[str, Dict[str, Any]]:
    name = rng.choice(["device", "contact", "package", "booking_details"])
    props = {
        "model": {"type": "string", "required": True, "description": "Model identifier."},
        "note": {"type": "string", "required": False, "description": "Free-form note."},
    }
    return name, {"type": "object", "required": True, "properties": props,
                  "description": "Nested " + name + " details."}


def _default_result(rng: random.Random, noun: str) -> Dict[str, Any]:
    variants = [
        {noun + "_id": noun[:2].upper() + "-" + str(rng.randint(100, 999))},
        {"status_detail": rng.choice(["ok", "pending", "done"])},
        {"items": [{"name": rng.choice(["alpha", "beta", "gamma"]),
                    "qty": rng.randint(1, 5)} for _ in range(rng.randint(1, 3))]},
        {"count": rng.randint(1, 50)},
    ]
    k = rng.randint(1, 2)
    chosen = rng.sample(variants, k=k)
    merged: Dict[str, Any] = {}
    for v in chosen:
        merged.update(v)
    return merged


def make_focal_tool(rng: random.Random, verb: str, noun: str, *,
                    trap: bool = False) -> Tuple[Dict[str, Any], str, str]:
    """Build one tool the utterance is actually about.

    Returns (spec, anchor_kind, anchor_arg_name). anchor_kind is 'place' or
    'person' - the role the generated utterance supplies a real value for.
    """
    args: Dict[str, Any] = {}
    anchor_kind = rng.choice(["place", "person"])
    if anchor_kind == "place":
        n, a = _place_arg(rng)
    else:
        n, a = _person_arg(rng, required=True)
    args[n] = a
    anchor_name = n

    if rng.random() < 0.5:
        n, a = _text_arg(rng)
        args[n] = a
    if rng.random() < 0.4:
        n, a = _enum_arg(rng)
        args[n] = a
    if rng.random() < 0.3:
        n, a = _number_arg(rng)
        args[n] = a
    if rng.random() < 0.25:
        n, a = _array_arg(rng)
        args[n] = a
    if rng.random() < 0.2:
        n, a = _object_arg(rng)
        args[n] = a
    if trap:
        n, a = _boolean_trap_arg(rng)
        args[n] = a

    lo = rng.randint(50, 150)
    hi = rng.randint(lo + 20, 300)
    spec = {
        "kind": rng.choice(["read_only", "state_modifying"]),
        "delay_range_ms": [lo, hi],
        "description": verb.capitalize() + " a " + noun + " for the user.",
        "args": args,
        "default_result": _default_result(rng, noun),
    }
    return spec, anchor_kind, anchor_name


def make_decoy_tool(rng: random.Random, verb: str, noun: str) -> Dict[str, Any]:
    lo = rng.randint(50, 150)
    hi = rng.randint(lo + 20, 300)
    n, a = _text_arg(rng)
    a["required"] = True
    return {
        "kind": rng.choice(["read_only", "state_modifying"]),
        "delay_range_ms": [lo, hi],
        "description": verb.capitalize() + " " + noun + " information.",
        "args": {n: a},
        "default_result": _default_result(rng, noun),
    }


TEMPLATES_PLACE = [
    "Please {verb} a {noun} {prep} {value}.",
    "Can you {verb} a {noun} {prep} {value} for me?",
    "I need to {verb} a {noun} {prep} {value}.",
]
TEMPLATES_PERSON = [
    "Please {verb} a {noun} for {value}.",
    "Can you {verb} a {noun} under the name {value}?",
    "I need to {verb} a {noun}, the guest is {value}.",
]


def make_task_utterance(rng: random.Random, verb: str, noun: str,
                        anchor_kind: str, anchor_value: str) -> str:
    if anchor_kind == "place":
        tmpl = rng.choice(TEMPLATES_PLACE)
        return tmpl.format(verb=verb, noun=noun, prep=rng.choice(PLACE_PREPS),
                           value=anchor_value)
    tmpl = rng.choice(TEMPLATES_PERSON)
    return tmpl.format(verb=verb, noun=noun, value=anchor_value)


def _speech_event(t_ms: float, text: str, end_of_turn: bool = True) -> Dict[str, Any]:
    return {"timestamp_ms": t_ms, "event_type": "user_speech_chunk",
            "payload": {"text": text, "end_of_turn": end_of_turn}}


# ---------------------------------------------------------------------------
# scenario generators
# ---------------------------------------------------------------------------
def make_mixed_scenario(seed: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """A manifest of 1-6 invented tools plus a task turn about one of them.

    Exercises P1-P5: crash-free, schema-valid calls, no fallback leakage, no
    duplicate/blind-retry state-modifying calls, and clean snapshot/filler
    hygiene.
    """
    rng = random.Random("mixed:%d" % seed)
    n_tools = rng.randint(1, min(6, len(VERB_NOUN_POOL)))
    verb_nouns = rng.sample(VERB_NOUN_POOL, k=n_tools)
    focal_verb, focal_noun = verb_nouns[0]
    trap = rng.random() < 0.5

    focal_spec, anchor_kind, anchor_name = make_focal_tool(
        rng, focal_verb, focal_noun, trap=trap)
    focal_name = focal_verb + "_" + focal_noun
    manifest = {focal_name: focal_spec}
    for v, n in verb_nouns[1:]:
        manifest[v + "_" + n] = make_decoy_tool(rng, v, n)

    anchor_value = (rng.choice(PLACE_WORDS) if anchor_kind == "place"
                    else rng.choice(PERSON_WORDS))
    turn_text = make_task_utterance(rng, focal_verb, focal_noun,
                                    anchor_kind, anchor_value)

    events: List[Dict[str, Any]] = []
    t = 100.0
    prepend_distractor = rng.random() < 0.3
    if prepend_distractor:
        d = rng.choice(DISTRACTOR_SAFE + DISTRACTOR_GAP)
        events.append(_speech_event(t, d, True))
        t += 900.0
    events.append(_speech_event(t, turn_text, True))

    tool_overrides = None
    inject_timeout = focal_spec["kind"] == "state_modifying" and rng.random() < 0.3
    if inject_timeout:
        tool_overrides = {focal_name: [{"call_index": 0, "error": "timeout",
                                        "detail": "synthetic timeout"}]}

    scenario: Dict[str, Any] = {
        "scenario_id": "fuzz_schema_mixed_%d" % seed,
        "tool_manifest": manifest,
        "events": events,
    }
    if tool_overrides:
        scenario["tool_overrides"] = tool_overrides
    meta = {"focal_tool": focal_name, "trap": trap,
            "injected_timeout": bool(tool_overrides),
            "turns": [e["payload"]["text"] for e in events]}
    return scenario, meta


def make_distractor_scenario(seed: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Distractor-only turns (greeting/thanks/small talk/off-topic), no task.

    Exercises P6: a scenario with nothing to act on must call no tool.
    """
    rng = random.Random("distractor:%d" % seed)
    n_tools = rng.randint(1, 4)
    verb_nouns = rng.sample(VERB_NOUN_POOL, k=n_tools)
    manifest = {v + "_" + n: make_decoy_tool(rng, v, n) for v, n in verb_nouns}

    use_gap = rng.random() < 0.5
    pool = DISTRACTOR_GAP if use_gap else DISTRACTOR_SAFE
    n_turns = rng.randint(1, 3)
    phrases = [rng.choice(pool) for _ in range(n_turns)]

    events = []
    t = 100.0
    for p in phrases:
        events.append(_speech_event(t, p, True))
        t += 900.0

    scenario = {
        "scenario_id": "fuzz_schema_distractor_%d" % seed,
        "tool_manifest": manifest,
        "events": events,
    }
    meta = {"phrases": phrases, "known_gap": use_gap}
    return scenario, meta


def make_recovery_scenario(seed: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """One tool, a task turn naming a place, then an interruption renaming it.

    Exercises P7: after the correction, nothing acts on the old value, and any
    call already in flight for it is cancelled or finishes harmlessly before
    the interruption.
    """
    rng = random.Random("recovery:%d" % seed)
    verb, noun = rng.choice(VERB_NOUN_POOL)
    tool_name = verb + "_" + noun

    lo = rng.randint(200, 240)
    hi = rng.randint(lo + 40, 300)
    place_name, place_spec = _place_arg(rng)
    args = {place_name: place_spec}
    if rng.random() < 0.5:
        n, a = _person_arg(rng, required=False)
        args[n] = a
    kind = rng.choice(["read_only", "state_modifying"])
    manifest = {tool_name: {
        "kind": kind, "delay_range_ms": [lo, hi],
        "description": verb.capitalize() + " a " + noun + " for the user.",
        "args": args, "default_result": _default_result(rng, noun),
    }}

    place1, place2 = rng.sample(PLACE_WORDS, 2)
    prep = rng.choice(PLACE_PREPS)
    turn = "Please %s a %s %s %s." % (verb, noun, prep, place1)
    interrupt_text = rng.choice([
        "Wait, actually make it %s." % place2,
        "Sorry, I meant %s." % place2,
        "No, change that to %s." % place2,
    ])
    interrupt_gap = 120.0
    events = [
        _speech_event(100.0, turn, True),
        {"timestamp_ms": 100.0 + interrupt_gap, "event_type": "interruption",
         "payload": {"text": interrupt_text}},
    ]
    scenario = {
        "scenario_id": "fuzz_schema_recovery_%d" % seed,
        "tool_manifest": manifest,
        "events": events,
    }
    meta = {"tool": tool_name, "arg": place_name,
            "old": place1, "new": place2,
            "interrupt_at_ms": 100.0 + interrupt_gap}
    return scenario, meta


# ---------------------------------------------------------------------------
# harness runner
# ---------------------------------------------------------------------------
def run_inprocess(scenario: Dict[str, Any], time_scale: float = 8.0,
                  tail_ms: float = 1200.0) -> List[Dict[str, Any]]:
    async def go():
        h = EvaluationHarness(scenario, lambda a, b: ParticipantAgent(a, b),
                              time_scale=time_scale, tail_ms=tail_ms, verbose=False)
        await h.prepare()
        return await h.run()
    return asyncio.run(go())


# ---------------------------------------------------------------------------
# trace helpers
# ---------------------------------------------------------------------------
def actions(trace: List[Dict[str, Any]], action: Optional[str] = None) -> List[Dict[str, Any]]:
    return [e for e in trace if e.get("kind") == "action"
            and (action is None or e.get("action") == action)]


def tool_calls(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return actions(trace, "tool_call")


def completions(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in trace if e.get("kind") == "tool_completed"]


def completion_for(trace: List[Dict[str, Any]], call_id: str) -> Optional[Dict[str, Any]]:
    for c in completions(trace):
        if c.get("call_id") == call_id:
            return c
    return None


def crashes(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in trace if e.get("kind") == "agent_crash"]


def protocol_errors(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in trace if e.get("kind") == "protocol_error"]


def mock_env_for(scenario: Dict[str, Any]) -> MockEnvironment:
    return MockEnvironment(scenario_id=scenario.get("scenario_id", "fuzz"),
                           extra_tools=scenario.get("tool_manifest"))


def combined_registry(scenario: Dict[str, Any]) -> Dict[str, Any]:
    return mock_env_for(scenario).registry


def turn_texts_from_events(events: Sequence[Dict[str, Any]]) -> List[str]:
    """Every complete turn of user text, plus every interruption's text - the
    exact strings that could conceivably leak verbatim into a non-free-text
    argument (P3)."""
    turns: List[str] = []
    buf: List[str] = []
    for ev in events:
        etype = ev.get("event_type")
        payload = ev.get("payload") or {}
        if etype == "user_speech_chunk":
            buf.append(str(payload.get("text", "")))
            if payload.get("end_of_turn"):
                turns.append(" ".join(buf).strip())
                buf = []
        elif etype == "interruption":
            turns.append(str(payload.get("text", "")).strip())
    return turns


# ---------------------------------------------------------------------------
# property checks (each returns a list of violations; empty == clean)
# ---------------------------------------------------------------------------
def validate_all_tool_calls(scenario: Dict[str, Any],
                            trace: List[Dict[str, Any]]) -> List[Tuple[str, Any]]:
    """P2: every tool_call names a tool in the manifest and its args pass
    that tool's own schema validation (mirrors harness.mock_env exactly,
    since we call the real _validate_args)."""
    env = mock_env_for(scenario)
    problems: List[Tuple[str, Any]] = []
    for tc in tool_calls(trace):
        api = tc.get("api_name")
        args = tc.get("args", {})
        spec = env.registry.get(api)
        if spec is None:
            problems.append((api, ["unknown_tool"]))
            continue
        p = env._validate_args(spec, args)
        if p:
            problems.append((api, p))
    return problems


def _looks_free_text(name: str, spec_dict: Dict[str, Any]) -> bool:
    if spec_dict.get("type") != "string":
        return False
    if spec_dict.get("enum"):
        return False
    haystack = (str(name) + " " + str(spec_dict.get("description", ""))).lower()
    return any(w in haystack for w in FREE_TEXT_WORDS)


def _walk_args(tool_name: str, arg_specs: Dict[str, Any], args: Any,
              turns_norm: set, violations: List[Tuple[str, str, Any]],
              prefix: str = "") -> None:
    if not isinstance(args, dict):
        return
    for name, aspec in (arg_specs or {}).items():
        if name not in args:
            continue
        val = args[name]
        path = prefix + name
        if isinstance(aspec, dict) and aspec.get("type") == "object":
            _walk_args(tool_name, aspec.get("properties", {}), val,
                      turns_norm, violations, prefix=path + ".")
            continue
        if _looks_free_text(name, aspec if isinstance(aspec, dict) else {}):
            continue
        if isinstance(val, str) and val.strip().lower() in turns_norm:
            violations.append((tool_name, path, val))


def find_fallback_leakage(scenario: Dict[str, Any], trace: List[Dict[str, Any]],
                          turn_texts: Sequence[str]) -> List[Tuple[str, str, Any]]:
    """P3: no non-free-text argument receives the ENTIRE utterance verbatim."""
    manifest = combined_registry(scenario)
    turns_norm = {t.strip().lower() for t in turn_texts if t.strip()}
    violations: List[Tuple[str, str, Any]] = []
    for tc in tool_calls(trace):
        spec = manifest.get(tc.get("api_name"))
        if spec is None:
            continue
        _walk_args(tc["api_name"], spec.get("args", {}), tc.get("args", {}),
                  turns_norm, violations)
    return violations


def find_duplicate_state_modifying(scenario: Dict[str, Any],
                                   trace: List[Dict[str, Any]]) -> List[str]:
    """P4a: no state-modifying call issued twice with identical args (the
    scorer's own duplicate key, harness/scorer.py::_score_safety)."""
    manifest = combined_registry(scenario)
    sm = {n for n, s in manifest.items() if s.get("kind") == "state_modifying"}
    seen: Dict[str, int] = {}
    for c in completions(trace):
        if c.get("api_name") not in sm or c.get("status") != "success":
            continue
        key = c["api_name"] + "|" + json.dumps(c.get("args", {}),
                                               sort_keys=True, default=str)
        seen[key] = seen.get(key, 0) + 1
    return [k for k, v in seen.items() if v > 1]


def find_bad_timeout_retries(scenario: Dict[str, Any],
                             trace: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """P4b: no state-modifying call is retried with identical args after one
    of its attempts came back 'timeout' (docs/TOOLS.md: timeout leaves commit
    state unknown, so a retry there is a safety violation, not a feature)."""
    manifest = combined_registry(scenario)
    sm = {n for n, s in manifest.items() if s.get("kind") == "state_modifying"}
    bad: List[Tuple[str, str]] = []
    timeouts = [c for c in completions(trace)
               if c.get("api_name") in sm and c.get("status") == "error"
               and (c.get("result") or {}).get("error") == "timeout"]
    for t in timeouts:
        for tc in tool_calls(trace):
            if tc.get("call_id") == t.get("call_id"):
                continue
            if tc.get("api_name") != t.get("api_name"):
                continue
            if tc.get("t_ms", -1) <= t.get("t_ms", 0):
                continue
            if tc.get("args") == t.get("args"):
                bad.append((t["api_name"], tc.get("call_id")))
    return bad


def find_finals_without_snapshot(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in actions(trace, "final_response")
            if not isinstance(e.get("state_snapshot"), dict)]


def filler_texts(trace: List[Dict[str, Any]]) -> List[str]:
    return [e.get("payload", {}).get("text", "") for e in actions(trace, "filler_speech")]


def find_repeated_fillers(trace: List[Dict[str, Any]]) -> List[str]:
    seen: Dict[str, int] = {}
    dups: List[str] = []
    for t in (s.strip().lower() for s in filler_texts(trace)):
        seen[t] = seen.get(t, 0) + 1
        if seen[t] == 2:
            dups.append(t)
    return dups


def check_recovery(meta: Dict[str, Any],
                   trace: List[Dict[str, Any]]) -> List[str]:
    """P7, matching harness/scorer.py::_score_recovery's own logic."""
    tool, arg = meta["tool"], meta["arg"]
    old, interrupt_at = str(meta["old"]).lower(), meta["interrupt_at_ms"]
    violations: List[str] = []

    for tc in tool_calls(trace):
        if tc.get("api_name") != tool or tc.get("t_ms", -1) <= interrupt_at:
            continue
        val = tc.get("args", {}).get(arg)
        if isinstance(val, str) and val.strip().lower() == old:
            violations.append("stale value %r re-issued at %.1fms (call %s)"
                              % (old, tc["t_ms"], tc.get("call_id")))

    pre_calls = [tc for tc in tool_calls(trace)
                if tc.get("api_name") == tool and tc.get("t_ms", -1) <= interrupt_at
                and isinstance(tc.get("args", {}).get(arg), str)
                and tc["args"][arg].strip().lower() == old]
    for tc in pre_calls:
        cid = tc.get("call_id")
        cancelled = any(e.get("kind") == "tool_cancelled" and e.get("call_id") == cid
                       for e in trace)
        if cancelled:
            continue
        comp = completion_for(trace, cid)
        if comp is not None and comp["t_ms"] <= interrupt_at:
            continue
        if comp is not None and comp["t_ms"] > interrupt_at + CANCEL_GRACE_MS:
            violations.append("stale call %s completed at %.1fms, never cancelled"
                              % (cid, comp["t_ms"]))
        abandoned = any(e.get("kind") == "tool_abandoned" and e.get("call_id") == cid
                        for e in trace)
        if abandoned:
            violations.append("stale call %s left running until shutdown" % cid)
    return violations


def find_non_ascii_speech(trace: List[Dict[str, Any]]) -> List[str]:
    bad = []
    for e in actions(trace):
        if e.get("action") not in ("filler_speech", "clarification_request",
                                   "final_response"):
            continue
        text = e.get("payload", {}).get("text", "")
        if any(ord(ch) > 127 for ch in text):
            bad.append(text)
    return bad


def agent_participated(trace: List[Dict[str, Any]]) -> bool:
    return any(e.get("action") in ("filler_speech", "clarification_request",
                                   "final_response", "tool_call")
              for e in actions(trace))
