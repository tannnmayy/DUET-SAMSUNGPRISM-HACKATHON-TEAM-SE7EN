"""Understanding a turn without a model, in under a millisecond.

Two jobs, both on the critical path for the 800ms latency target:

  1. Classify a repair. "Wait, make it New York" and "actually, never mind"
     and "and make it a window seat" all look like interruptions, but they
     demand opposite responses. A CORRECTION invalidates in-flight work; a
     REFINEMENT must NOT, because cancelling a still-valid call throws away
     the task points it would have earned.

  2. Extract values structurally, never from a list. The kit's baseline
     recognises seven cities and dies on the eighth; hidden scenarios are
     explicitly re-skinned with different cities, names and tools. So values
     are found by grammar - prepositions, appositives, identifier shapes -
     and typed into roles the tool layer can bind.

Design constraint that shapes everything here: audio turns arrive as raw
speech, and a transcript may be entirely lowercase with no punctuation.
Capitalisation is therefore treated as corroborating evidence, never as the
primary signal. Every pattern below works on "book a flight to boston" as
well as on "Book a flight to Boston."
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import contract
from .tools import (
    ROLE_DATE, ROLE_ID, ROLE_NUMBER, ROLE_PERSON, ROLE_PLACE, ROLE_TEXT,
)

# ---------------------------------------------------------------------------
# Repair kinds
# ---------------------------------------------------------------------------
REPAIR_NONE = "none"
REPAIR_CORRECTION = "correction"          # replace a value, re-plan
REPAIR_RETRACTION = "retraction"          # abandon the request entirely
REPAIR_INTENT_CHANGE = "intent_change"    # abandon the domain, not just a slot
REPAIR_REFINEMENT = "refinement"          # add a constraint, keep the work
REPAIR_UNDO = "undo"                      # revert the previous correction (M6)

# Ordered by specificity: the first matching family wins, so "forget the
# flight, my TV is broken" is an intent change rather than a bare retraction.
_RETRACTION_TRIGGERS = (
    "never mind", "nevermind", "forget it", "forget that", "cancel that",
    "drop it", "drop that", "leave it", "skip it", "don't bother",
    "do not bother", "no thanks", "not any more", "not anymore",
    "stop that", "stop it", "abandon that",
)

_UNDO_TRIGGERS = (
    "go back", "undo that", "undo it", "as i said before", "like i said before",
    "what i said before", "revert that", "back to what i said",
)

_INTENT_CHANGE_TRIGGERS = (
    "forget the", "never mind the", "instead of the", "change of plan",
    "different question", "something else", "new question", "unrelated",
    # Ordinary verbs of abandoning a topic: "scratch the trip - my phone
    # won't charge" once became a flight search to a city called Scratch.
    # "cancel the" is deliberately absent: "cancel the booking" is a request.
    "scratch the", "ditch the", "drop the", "skip the", "forget about the",
)

_CORRECTION_TRIGGERS = (
    "actually", "wait", "hold on", "make it", "make that", "change it to",
    "change that to", "change to", "switch to", "switch it to", "instead",
    "i meant", "i mean", "sorry", "no sorry", "rather", "correction",
    "scratch that", "let's say", "lets say", "on second thought",
    "on second thoughts",
)

_REFINEMENT_TRIGGERS = (
    "also", "as well", "and add", "plus", "additionally", "in addition",
    "and make it", "can you also", "one more thing", "while you're at it",
    "while you are at it",
)

# Bare negation at the start of a barge-in ("no, Chicago") is a correction.
_LEADING_NEGATION = re.compile(r"^\s*(no|nope|nah)\b[,\s]", re.I)


@dataclass
class Candidate:
    """One extracted value, with where it came from and how sure we are."""

    value: str
    role: str
    confidence: float = 0.7
    span: Optional[Tuple[int, int]] = None
    evidence: str = ""


@dataclass
class Repair:
    kind: str
    trigger: Optional[str] = None
    remainder: str = ""          # the text after the trigger
    confidence: float = 0.0

    @property
    def invalidates(self) -> bool:
        """Does this repair make in-flight work stale?

        A refinement does not: "and make it a window seat" leaves the flight
        search perfectly valid, and cancelling it would cost us the result.
        """
        return self.kind in (REPAIR_CORRECTION, REPAIR_RETRACTION,
                             REPAIR_INTENT_CHANGE, REPAIR_UNDO)


def _find_trigger(low: str, triggers: Sequence[str]) -> Optional[Tuple[str, int]]:
    best: Optional[Tuple[str, int]] = None
    for trigger in triggers:
        idx = low.find(trigger)
        if idx < 0:
            continue
        # Prefer the earliest trigger, and the longest at the same position.
        if best is None or idx < best[1] or (idx == best[1] and len(trigger) > len(best[0])):
            best = (trigger, idx)
    return best


def classify_repair(text: str) -> Repair:
    """Work out what kind of course correction this is.

    Order matters. An intent change is a retraction plus a new topic, and a
    retraction is a correction with nothing to correct to, so the most
    specific family is tested first.
    """
    raw = str(text or "")
    low = contract.norm(raw)
    if not low:
        return Repair(REPAIR_NONE)

    hit = _find_trigger(low, _UNDO_TRIGGERS)
    if hit:
        return Repair(REPAIR_UNDO, hit[0], raw[hit[1] + len(hit[0]):].strip(), 0.8)

    hit = _find_trigger(low, _INTENT_CHANGE_TRIGGERS)
    if hit:
        tail = raw[hit[1] + len(hit[0]):].strip()
        # "forget the FLIGHT. my TV is broken" - the trigger consumes only up
        # to "the", leaving the abandoned domain's own noun at the front of
        # the remainder. That one word is enough to pull tool ranking straight
        # back to the domain we were just told to drop, so the abandoned noun
        # phrase is cut at the first clause boundary.
        remainder = _after_first_boundary(tail)
        if _has_new_content(remainder):
            return Repair(REPAIR_INTENT_CHANGE, hit[0], remainder, 0.75)
        # Nothing follows the abandoned noun: this is a plain retraction.
        return Repair(REPAIR_RETRACTION, hit[0], tail, 0.8)

    hit = _find_trigger(low, _RETRACTION_TRIGGERS)
    if hit:
        remainder = raw[hit[1] + len(hit[0]):].strip()
        # "never mind the flight, what's the weather" retracts AND redirects.
        if _has_new_content(remainder):
            return Repair(REPAIR_INTENT_CHANGE, hit[0], remainder, 0.7)
        return Repair(REPAIR_RETRACTION, hit[0], remainder, 0.85)

    hit = _find_trigger(low, _REFINEMENT_TRIGGERS)
    correction_hit = _find_trigger(low, _CORRECTION_TRIGGERS)
    if hit and (correction_hit is None or hit[1] < correction_hit[1]):
        return Repair(REPAIR_REFINEMENT, hit[0], raw[hit[1] + len(hit[0]):].strip(), 0.6)

    if correction_hit:
        remainder = raw[correction_hit[1] + len(correction_hit[0]):].strip()
        return Repair(REPAIR_CORRECTION, correction_hit[0], remainder, 0.8)

    if _LEADING_NEGATION.match(raw):
        return Repair(REPAIR_CORRECTION, "no", _LEADING_NEGATION.sub("", raw).strip(), 0.7)

    # An interruption with no trigger word at all is still a correction: the
    # user barged in for a reason.
    return Repair(REPAIR_CORRECTION, None, raw.strip(), 0.4)


# "don't book anything", "do not send it", "no need to check", "stop the
# search" - a whole clause that withdraws an action.
_NEGATED_COMMAND = re.compile(
    r"\b(?:please\s+)?(?:don'?t|do not|dont|no need to|there'?s no need to|stop)\b[^.,;!?]*")

# A value the user is REJECTING: "for Priya, not Alice", "instead of Boston".
# It must never be extracted - it once became the destination.
# Keywords match in any case; a multi-word name continues only while its
# words are capitalised (case-SENSITIVE), so "not to Boston but to Chicago"
# rejects Boston and nothing after it.
_REJECTED_VALUE = re.compile(
    r"(?i:\b(?:not|instead of|rather than|except))\s+"
    r"(?i:(?:to|for|in|at|on|the)\s+)?"
    r"([A-Za-z][\w'\-]*(?:\s+[A-Z][\w'\-]*)*)")


def _rejected_spans(text: str) -> List[Tuple[int, int]]:
    return [m.span(1) for m in _REJECTED_VALUE.finditer(text)]


def _inside(span: Optional[Tuple[int, int]], spans: List[Tuple[int, int]]) -> bool:
    return span is not None and any(a <= span[0] < b for a, b in spans)


def _after_first_boundary(text: str) -> str:
    """Text following the first clause boundary, or empty if there is none.

    A spaced dash is a boundary too: "scratch the trip - my phone won't
    charge" is two clauses.
    """
    match = re.search(r"[.,;!?]|\s[-]+\s", text)
    return text[match.end():].strip() if match else ""


def _has_new_content(remainder: str) -> bool:
    """Is there a fresh request after the retraction, or just the retraction?

    Counting raw tokens is not enough: "Actually, never mind. Forget it."
    leaves "forget it" behind, which is more retraction, not a new request.
    So other trigger phrases are stripped first and only content words are
    counted - grammar words and filler cannot signal a change of topic.
    """
    text = contract.norm(remainder)
    # A negated command is more retraction, not a new request: "never mind.
    # Don't book anything." was re-planned as a fresh request and answered
    # "Sure, let me switch to that - which flight id did you want?".
    text = _NEGATED_COMMAND.sub(" ", text)
    for trigger in (_RETRACTION_TRIGGERS + _CORRECTION_TRIGGERS
                    + _INTENT_CHANGE_TRIGGERS + _UNDO_TRIGGERS):
        text = text.replace(trigger, " ")
    tokens = [t for t in re.findall(r"[a-z'0-9]+", text)
              if t not in _FILLER_WORDS and t not in _NON_VALUE]
    return len(tokens) >= 2


# ---------------------------------------------------------------------------
# Value extraction
# ---------------------------------------------------------------------------
_FILLER_WORDS = frozenset("""
uh um er ah erm hmm like you know i mean sort of kind of well so okay ok right
just really actually basically literally please thanks thank
""".split())

# Words that can never be the head of an extracted value. Deliberately generic
# grammar words rather than any domain vocabulary.
_NON_VALUE = frozenset("""
it that this those these them there here one ones thing things something
anything me you us him her he she they we i my your our their his its
a an the and or but if then than so to for in on at from into of with by
is are was were be been being do does did doing have has had having
please thanks thank sorry okay ok yes no not
what which who whom whose when where why how
tomorrow today tonight now soon later
flight flights hotel hotels car cars ticket tickets booking bookings
question questions thing things way ways place places problem problems
issue issues idea ideas point moment minute second hour hours day days
week weeks month months year years morning evening afternoon night
number answer reason detail details information info matter subject
""".split())

# Identifier shapes: FL-DEN-8AM, BK-0001, TK-0001, RC-7781, HT-0001.
_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{0,4}(?:-[A-Z0-9]+){1,3})\b")
_ID_RE_LOWER = re.compile(r"\b([a-z][a-z0-9]{0,4}(?:-[a-z0-9]+){1,3})\b")

# Clock times: "8 AM", "8am", "2 pm", "14:00", "08:00".
_TIME_RE = re.compile(r"\b(\d{1,2}\s*(?::\s*\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2})\b", re.I)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")
_DATE_WORDS = ("today", "tomorrow", "tonight", "overnight", "next week",
               "this week", "weekend", "this evening", "this afternoon",
               "this morning")
# Nouns a determiner like "next" / "this" / "last" may legitimately precede in
# a date expression. Without this restriction the pattern matched "this port"
# and recorded a question about a connector as a date.
_DATE_NOUNS = ("week", "weekend", "month", "year", "morning", "afternoon",
               "evening", "night", "day", "time") + _WEEKDAYS

_DATE_RE = re.compile(
    r"\b(" + "|".join(_WEEKDAYS + _DATE_WORDS) + r"|\d{4}-\d{2}-\d{2}"
    r"|(?:next|this|last)\s+(?:" + "|".join(_DATE_NOUNS) + r"))\b", re.I)

# A place follows a spatial preposition; a person follows a benefactive one.
_PLACE_PREPS = ("to", "in", "at", "from", "into", "toward", "towards", "for")
_PERSON_PREPS = ("for", "under", "name is", "name's", "names", "passenger",
                 "traveller", "traveler", "i'm", "i am", "its for", "it's for")

_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")


# A captured span ends at the first of these: "to Chicago for Friday" is the
# place "Chicago", not the phrase "Chicago for Friday". Trailing-only trimming
# is not enough, because the junk sits in the middle.
_SPAN_BOUNDARY = frozenset("""
for on at in to from with and or but please next this last by of about
tomorrow today tonight monday tuesday wednesday thursday friday saturday sunday
right now just currently again soon later asap immediately quickly actually
maybe perhaps then also too instead rather already still
""".split())


def _clean_value(raw: str) -> str:
    """Trim a captured span down to the value itself."""
    text = re.sub(r"[\s,.;:!?]+$", "", str(raw or "").strip())
    text = re.sub(r"^[\s,.;:]+", "", text)
    tokens = text.split()
    # Drop leading grammar words: "it New York" -> "New York". Except a
    # capitalised "The" that opens a capitalised name - "at The Olive Room",
    # "to The Hague" - which is part of the name, not grammar.
    while tokens and contract.norm(tokens[0]) in _NON_VALUE:
        if (tokens[0] == "The" and len(tokens) > 1 and tokens[1][:1].isupper()):
            break
        tokens.pop(0)
    # Cut at the first internal boundary word.
    cut = len(tokens)
    for i, token in enumerate(tokens):
        if contract.norm(token).strip(",.;:") in _SPAN_BOUNDARY:
            cut = i
            break
    tokens = tokens[:cut]
    # Drop trailing grammar words: "Boston for" -> "Boston".
    while tokens and contract.norm(tokens[-1]) in _NON_VALUE:
        tokens.pop()
    # When casing survived, a proper name ends at its last capitalised word:
    # "a hotel in Porto cost ..." is Porto, not "Porto cost". Interior
    # lowercase words stay ("Rio de Janeiro"); an all-lowercase span - raw
    # ASR - is left alone, because there casing is no evidence at all.
    if any(t[:1].isupper() for t in tokens):
        while tokens and not tokens[-1][:1].isupper() and not tokens[-1][:1].isdigit():
            tokens.pop()
    return " ".join(tokens[:4]).strip()


def _is_date_like(value: str) -> bool:
    """Guard against a weekday being read as a person: 'for Friday'."""
    return bool(_DATE_RE.fullmatch(str(value or "").strip()))


def _title(value: str) -> str:
    """Normalise casing for a proper noun arriving from lowercase ASR.

    Tool arguments are matched case-insensitively by the scorer, so this is
    about what we say aloud rather than what we send.
    """
    if not value:
        return value
    if any(c.isupper() for c in value):
        return value
    return " ".join(w.capitalize() for w in value.split())


# Verbs that follow "to" as an infinitive, not as a destination: "I need TO
# GET to Tucson". Grammar vocabulary, like _NON_VALUE - it names no place.
_INFINITIVES = frozenset("""
get go fly travel head book find see check have make take know do be visit
reach leave come move buy pay ask try look search reserve cancel open order
change switch help hear speak talk call ring send stay spend arrive return
""".split())


def _after_preposition(text: str, preps: Sequence[str]) -> List[Tuple[str, int]]:
    """Capture up to three tokens following each preposition.

    Matches may overlap. With plain finditer, "to get to Tucson" is one
    match - "to" followed by "get to Tucson" - which swallows the second "to"
    and the real destination with it, leaving "Get" as the place.
    """
    found: List[Tuple[str, int]] = []
    for prep in preps:
        pattern = r"(?=\b" + re.escape(prep) + r"\s+([\w'\-]+(?:\s+[\w'\-]+){0,2}))"
        for match in re.finditer(pattern, text, re.I):
            head = match.group(1).split()[0].lower()
            if head in _INFINITIVES:
                continue
            value = _clean_value(match.group(1))
            if value and not _TIME_RE.fullmatch(value):
                found.append((value, match.start(1)))
    return found


def extract_ids(text: str) -> List[Candidate]:
    out: List[Candidate] = []
    for match in _ID_RE.finditer(text):
        out.append(Candidate(match.group(1), ROLE_ID, 0.95,
                             match.span(1), "identifier shape"))
    if not out:
        for match in _ID_RE_LOWER.finditer(text):
            token = match.group(1)
            if "-" not in token:
                continue
            out.append(Candidate(token.upper(), ROLE_ID, 0.8,
                                 match.span(1), "identifier shape (lowercase)"))
    return out


def extract_dates(text: str) -> List[Candidate]:
    """Date expressions.

    Deliberately does NOT use _clean_value: that strips tokens in _NON_VALUE,
    which contains "tomorrow", "today" and the weekdays on purpose so that
    "to Recife tomorrow" yields the place "Recife" rather than the phrase
    "Recife tomorrow". Correct for places, fatal for dates - the two need
    different vocabularies, so dates get a light trim only.
    """
    out: List[Candidate] = []
    for match in _DATE_RE.finditer(text):
        value = re.sub(r"[\s,.;:!?]+$", "", match.group(1).strip())
        if value:
            out.append(Candidate(value, ROLE_DATE, 0.8, match.span(1), "date expression"))
    return out


def extract_times(text: str) -> List[Candidate]:
    out: List[Candidate] = []
    for match in _TIME_RE.finditer(text):
        raw = re.sub(r"\s+", " ", match.group(1).strip())
        out.append(Candidate(raw.upper().replace(" ", ""), ROLE_TEXT, 0.85,
                             match.span(1), "clock time"))
    return out


def extract_places(text: str) -> List[Candidate]:
    out: List[Candidate] = []
    for value, pos in _after_preposition(text, ("to", "in", "at", "from", "into",
                                                "toward", "towards")):
        if _looks_like_value(value):
            out.append(Candidate(_title(value), ROLE_PLACE, 0.8, (pos, pos + len(value)),
                                 "after spatial preposition"))
    return out


def extract_people(text: str) -> List[Candidate]:
    out: List[Candidate] = []
    # Patterns whose cue names a person explicitly work in any case; patterns
    # relying on "for X" must NOT be case-insensitive, or [A-Z] matches
    # lowercase and "for friday" becomes a passenger called Friday.
    cased_patterns = (
        r"\bfor\s+([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*)?)",
        r"\b(?:I'm|I am)\s+([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*)?)",
        r"\bunder\s+([A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*)?)",
    )
    explicit_patterns = (
        r"\bname(?:'s| is)\s+([\w'\-]+(?:\s+[\w'\-]+)?)",
        r"\bpassenger(?:\s+is)?\s+([\w'\-]+(?:\s+[\w'\-]+)?)",
        r"\btravell?er(?:\s+is)?\s+([\w'\-]+(?:\s+[\w'\-]+)?)",
    )
    for pattern in cased_patterns:
        for match in re.finditer(pattern, text):
            _offer_person(out, match, "benefactive phrase", 0.75)
    for pattern in explicit_patterns:
        for match in re.finditer(pattern, text, re.I):
            # An explicit cue ("name's Alice", "passenger Alice") is not
            # ambiguous with a place, so it is tagged differently and the
            # reassignment in extract_values must never touch it.
            _offer_person(out, match, "explicit person cue", 0.9)
    return out


def _offer_person(out: List[Candidate], match, evidence: str,
                  confidence: float) -> None:
    value = _clean_value(match.group(1))
    if not _looks_like_value(value) or _is_date_like(value):
        return
    out.append(Candidate(_title(value), ROLE_PERSON, confidence,
                         match.span(1), evidence))


def extract_capitalised(text: str) -> List[Candidate]:
    """Proper-noun spans, as corroboration only.

    Absent from lowercase ASR output, so this can never be the sole route to a
    value - but when punctuation and casing survive, it disambiguates.
    """
    out: List[Candidate] = []
    for match in re.finditer(r"\b([A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+)*)\b", text):
        value = match.group(1)
        if contract.norm(value) in _NON_VALUE:
            continue
        if len(value) < 3:
            continue
        if _sentence_initial_word(text, match):
            continue
        out.append(Candidate(value, ROLE_TEXT, 0.5, match.span(1), "capitalised span"))
    return out


def _sentence_initial_word(text: str, match) -> bool:
    """A single capitalised word that merely starts a sentence.

    "Could you get us a table", "Scratch the trip": capitalised because they
    begin a sentence, not because they are names - one became a passenger
    called Could, the other a destination called Scratch. A lone capitalised
    word at a sentence start, followed by a lowercase word, is treated as
    ordinary. "Recife. Friday." keeps Recife: nothing lowercase follows it.
    """
    value = match.group(1)
    if " " in value:
        return False
    before = text[:match.start(1)].rstrip()
    at_start = not before or before[-1] in ".!?-"
    if not at_start:
        return False
    after = text[match.end(1):]
    return bool(re.match(r"\s+[a-z]", after))


def _looks_like_value(value: str) -> bool:
    if not value or len(value) < 2:
        return False
    low = contract.norm(value)
    if low in _NON_VALUE or low in _FILLER_WORDS:
        return False
    return bool(re.search(r"[A-Za-z]", value))


def extract_values(text: str, *, expect: Optional[Sequence[str]] = None
                   ) -> Dict[str, Candidate]:
    """Extract a role -> value map from an utterance.

    `expect` lists roles the caller is hoping for (usually the required
    argument roles of the tool it is considering). It resolves the genuine
    ambiguity between a place and a person: "for Alice" and "for Denver" have
    identical grammar, and the schema is the only thing that can break the tie.
    """
    text = str(text or "")
    wanted: Set[str] = set(expect or ())
    found: Dict[str, Candidate] = {}
    rejected = _rejected_spans(text)

    def offer(cand: Candidate) -> None:
        if _inside(cand.span, rejected):
            return                      # "not Alice" is what the user rejects
        existing = found.get(cand.role)
        # Later mentions win: in a self-repair the corrected value comes last.
        if existing is None or cand.confidence >= existing.confidence:
            found[cand.role] = cand

    for cand in extract_ids(text):
        offer(cand)
    for cand in extract_dates(text):
        offer(cand)
    for cand in extract_places(text):
        offer(cand)
    for cand in extract_people(text):
        offer(cand)

    # NOTE: clock times are deliberately NOT offered as values. A time is a
    # selector over results ("the 8 AM one"), not a slot. Treating "8AM" as a
    # free-text value made a manual-search tool look fully satisfiable for
    # "find a flight to Denver and book the 8 AM one", which flipped the
    # ranking away from flight_search. extract_selector() handles times.

    # Disambiguate place vs person when both matched the same span.
    place = found.get(ROLE_PLACE)
    person = found.get(ROLE_PERSON)
    if place and person and contract.norm(place.value) == contract.norm(person.value):
        if ROLE_PLACE in wanted and ROLE_PERSON not in wanted:
            found.pop(ROLE_PERSON, None)
        elif ROLE_PERSON in wanted and ROLE_PLACE not in wanted:
            found.pop(ROLE_PLACE, None)
        else:
            found.pop(ROLE_PERSON, None)

    # "for X" is genuinely ambiguous between a benefactive person and a
    # destination: "book it for Alice" and "find something for Recife" have
    # identical grammar. Only the schema can break the tie, so when the caller
    # wants one role and we produced the other from that pattern, reassign it.
    for wanted_role, other in ((ROLE_PLACE, ROLE_PERSON), (ROLE_PERSON, ROLE_PLACE)):
        if wanted_role in wanted and wanted_role not in found and other not in wanted:
            spare = found.get(other)
            if spare is not None and spare.evidence == "benefactive phrase":
                found.pop(other)
                found[wanted_role] = Candidate(
                    spare.value, wanted_role, spare.confidence * 0.9, spare.span,
                    "reassigned from ambiguous 'for'")

    # Capitalised spans fill a wanted role nothing else reached. A span
    # already claimed by another role is skipped: "book a flight to Boston"
    # must not also yield a passenger called Boston just because a person is
    # wanted and none was found.
    claimed = {contract.norm(c.value) for c in found.values()}
    for cand in extract_capitalised(text):
        if contract.norm(cand.value) in claimed or _inside(cand.span, rejected):
            continue
        for role in (ROLE_PLACE, ROLE_PERSON):
            if role in wanted and role not in found:
                found[role] = Candidate(cand.value, role, 0.45, cand.span,
                                        "capitalised fallback")
                claimed.add(contract.norm(cand.value))
                break

    for cand in _numbers(text):
        if ROLE_NUMBER in wanted and ROLE_NUMBER not in found:
            found[ROLE_NUMBER] = cand

    return found


# Spoken numbers. People say "for three nights" and "a party of four", and
# ASR writes what it hears; a number argument that only accepts digits makes
# the agent ask "which nights?" of a user who already said.
_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "a couple of": 2, "a pair of": 2,
    "a dozen": 12, "a single": 1, "just me": 1, "both of us": 2,
}
_NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, _NUMBER_WORDS), key=len, reverse=True)) + r")\b",
    re.I)
# "one" is also a pronoun - "the 8 AM one", "the cheapest one" - so it counts
# only as a quantity before a noun ("one night") and not after a determiner.
_ONE_RE = re.compile(
    r"(?<!\bthe )(?<!\bthis )(?<!\bthat )(?<!\bwhich )(?<!\bany )(?<!\beach )"
    r"\b(one)\s+(?!(?:of|more|moment|second|sec|please|thanks|thank|is|was|"
    r"for|to|and|or|with|that|which|i|you|we|it|at|on|in|then|too|as|if)\b)[a-z]",
    re.I)


def _numbers(text: str) -> List[Candidate]:
    """Numerals and spoken numbers, in order of appearance.

    Clock times are excluded: "at 7 PM" is when, not how many.
    """
    times = [m.span(1) for m in _TIME_RE.finditer(text)]

    def in_time(span: Tuple[int, int]) -> bool:
        return any(a <= span[0] < b for a, b in times)

    out = []
    for match in _NUMBER_RE.finditer(text):
        if not in_time(match.span(1)):
            out.append(Candidate(match.group(1), ROLE_NUMBER, 0.6, match.span(1), "numeral"))
    for match in _NUMBER_WORD_RE.finditer(text):
        value = _NUMBER_WORDS[match.group(1).lower()]
        out.append(Candidate(str(value), ROLE_NUMBER, 0.6, match.span(1), "number word"))
    for match in _ONE_RE.finditer(text):
        out.append(Candidate("1", ROLE_NUMBER, 0.6, match.span(1), "number word"))
    out.sort(key=lambda c: c.span[0] if c.span else 0)
    return out


# ---------------------------------------------------------------------------
# Result selectors
# ---------------------------------------------------------------------------
@dataclass
class Selector:
    """How the user picked among results: "the 8 AM one", "the cheapest"."""

    time: Optional[str] = None
    superlative: Optional[str] = None
    ordinal: Optional[int] = None
    # (from_hour, to_hour) for "the afternoon flight", "a morning one".
    daypart: Optional[Tuple[float, float]] = None

    @property
    def is_empty(self) -> bool:
        return (self.time is None and self.superlative is None
                and self.ordinal is None and self.daypart is None)


_SUPERLATIVES = {
    "cheapest": "min_price", "least expensive": "min_price",
    "lowest": "min_price", "best price": "min_price", "cheaper": "min_price",
    "most expensive": "max_price", "priciest": "max_price",
    "earliest": "min_time", "first": "min_time", "soonest": "min_time",
    "latest": "max_time", "last": "max_time",
}

_ORDINALS = {"first": 0, "second": 1, "third": 2, "1st": 0, "2nd": 1, "3rd": 2}


def extract_selector(text: str) -> Selector:
    """Which of several results the user meant.

    conf_06 turns on this: "the cheapest one" is the $99 2 PM flight, not
    flights[0]. Taking the first row is the easy wrong answer.
    """
    low = contract.norm(text)
    selector = Selector()

    times = extract_times(text)
    if times:
        selector.time = times[-1].value

    for phrase, kind in _SUPERLATIVES.items():
        if phrase in low:
            selector.superlative = kind
            break

    for word, index in _ORDINALS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            if selector.superlative is None:
                selector.ordinal = index
            break

    for word, hours in _DAYPARTS.items():
        if re.search(r"\b" + word + r"\b", low):
            selector.daypart = hours
            break

    return selector


# Parts of the day, as hour ranges matched against a result's own time field.
_DAYPARTS = {
    "afternoon": (12.0, 17.0), "morning": (5.0, 12.0), "evening": (17.0, 21.0),
    "night": (19.0, 24.0), "noon": (11.0, 14.0), "midday": (11.0, 14.0),
    "lunchtime": (11.0, 14.0), "late": (18.0, 24.0), "early": (0.0, 9.0),
}


# Generic English verbs of commitment. Not domain vocabulary: these are how
# a speaker of any language-domain signals "do the irreversible thing now",
# and they are what lets "put me on the cheapest one" read as a booking
# despite containing no booking noun at all.
_COMMIT_CUES = (
    "book", "reserve", "buy", "purchase", "order", "schedule", "confirm",
    "put me on", "get me on", "sign me up", "take it", "go ahead", "do it",
    "make it happen", "proceed", "submit", "file", "open a", "raise a",
    "cancel", "delete", "remove", "send", "apply",
)


def wants_commit(text: str) -> bool:
    """Does the user want an irreversible action taken now?

    Used only to break ties among tools we can ALREADY satisfy - never to
    promote an unsatisfiable one, or "find a flight and book the 8 AM"
    would try to book before any flight id exists.
    """
    low = contract.norm(text)
    return any(cue in low for cue in _COMMIT_CUES)


def looks_like_capability_question(text: str) -> bool:
    """Is the user asking what we can do, rather than asking us to do it?

    pub_04 makes this a scored routing decision: calling any tool there loses
    half the task score. The test is narrow on purpose - it must not swallow
    real requests.
    """
    low = contract.norm(text).rstrip("?.! ")
    if not low:
        return False
    patterns = (
        "what can you", "what do you do", "what are you able",
        "what else can you", "how can you help", "what can i ask",
        "who are you", "what are you", "can you help me with",
        "what kind of things", "what sort of things", "what all can you",
    )
    return any(p in low for p in patterns)


_AFFIRMATIONS = ("yes", "yeah", "yep", "yup", "correct", "right", "exactly",
                 "that's right", "that is right", "that's it", "sure",
                 "affirmative", "uh huh", "mm hmm", "you got it")
_NEGATIONS = ("no", "nope", "nah", "wrong", "that's wrong", "that is wrong",
              "not quite", "no it's not", "no it is not", "negative")


def _reply_words(text: str) -> str:
    return re.sub(r"[^a-z' ]+", " ", contract.norm(text)).strip()


def looks_like_affirmation(text: str) -> bool:
    """Does this reply agree with what we just asked about?

    "Yes", "yeah, that's right", "correct" - or an affirmation leading into
    more words ("yes, Austin"), whose extra words the caller still plans
    with. Checked only when a confirmation question is actually pending.
    """
    low = _reply_words(text)
    return any(low == a or low.startswith(a + " ") for a in _AFFIRMATIONS)


def looks_like_bare_negation(text: str) -> bool:
    """A "no" that carries no replacement value ("no", "nope, that's wrong")."""
    low = _reply_words(text)
    if not low:
        return False
    return any(low == n for n in _NEGATIONS) or (
        low.split()[0] in ("no", "nope", "nah")
        and all(w in ("no", "nope", "nah", "that's", "it's", "not", "it", "is",
                      "wrong", "right", "that", "quite")
                for w in low.split()))


def looks_like_greeting(text: str) -> bool:
    low = contract.norm(text).rstrip("?.! ")
    return low in ("hi", "hello", "hey", "yo", "good morning", "good evening",
                   "good afternoon", "thanks", "thank you", "cheers")
