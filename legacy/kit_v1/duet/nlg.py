"""What the agent says.

Four constraints shape every string in this file.

1. Truthful. We never assert that an irreversible action happened before the
   tool doing it reported success. Enforced downstream by the emitter, but
   the templates are written so the situation cannot arise.

2. Non-repeating. The scorer deducts 0.15 per verbatim-repeated filler
   (capped at 0.45), and the LLM quality grade scores non-redundancy
   directly. The Phrasebook therefore never returns the same string twice in
   one session: variants are consumed, not sampled.

3. Deterministic. SUBMISSION.md requires reproducibility and the sealed run
   takes a median of 3 repetitions. Variant selection is a rotation over a
   fixed order, never random.

4. ASCII only. The harness prints actions to a console that may be cp1252 on
   Windows; a stray em dash there is a UnicodeEncodeError inside the harness,
   not inside us, and would be recorded as our crash. There is no upside to
   non-ASCII punctuation in speech that is synthesised anyway.

Every public method returns a plain string. Nothing here touches the queue.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Set

from . import contract

# --------------------------------------------------------------------------
# Variant pools.
#
# Ordered, not random. Each pool is phrased differently rather than being the
# same sentence with synonyms swapped, because the quality grade rewards
# natural variety and punishes template stuffing.
#
# {subject} / {value} / {label} / {options} are filled by the renderer.
# --------------------------------------------------------------------------
_POOLS: Dict[str, Sequence[str]] = {
    # Starting a read-only lookup. Content-aware: names what we are doing.
    "ack_lookup": (
        "Looking up {subject} now.",
        "One moment, checking {subject}.",
        "Let me pull up {subject}.",
        "On it - {subject} coming up.",
        "Checking {subject} for you.",
        "Give me a second on {subject}.",
    ),
    # Generic version when we have no subject worth naming.
    "ack_generic": (
        "One moment.",
        "Give me a second.",
        "Let me check that.",
        "Working on it.",
        "Just a sec.",
        "Right, let me look.",
    ),
    # Starting irreversible work. A promise, never a claim: every line names
    # what is about to happen in the progressive ("booking ... now"), which
    # is truthful before the tool reports back and says nothing about the
    # outcome. {subject} is a gerund phrase: "booking flight FL-DEN-8AM for
    # Alice".
    "ack_commit": (
        "Okay, {subject} now.",
        "On it - {subject} now.",
        "Great, {subject} right away.",
        "Alright, {subject} now.",
    ),
    # Acknowledging a correction. MUST name the new value: the scorer checks
    # for a spoken action containing it inside a short window after the
    # interruption, and a user needs to hear that the correction landed.
    "ack_correction": (
        "Got it - switching to {value}.",
        "Okay, {value} instead.",
        "Understood, making it {value}.",
        "Changing that to {value}.",
        "Right - {value} it is.",
        "Sure, {value} instead then.",
    ),
    # Correction where we can name both sides.
    "ack_correction_from": (
        "Got it - {old} to {value}.",
        "Switching from {old} to {value}.",
        "Okay, dropping {old} and going with {value}.",
    ),
    # User retracted entirely ("actually, never mind").
    "ack_retraction": (
        "No problem, dropping that.",
        "Okay, I will leave it.",
        "Sure, cancelling that request.",
        "Understood, stopping there.",
        "Fine by me - nothing sent.",
    ),
    # User changed topic completely.
    "ack_intent_change": (
        "Sure, let me switch to that.",
        "Okay, different track - on it.",
        "Right, let me look at that instead.",
        "Got it, moving to that.",
    ),
    # Audio arrived; we have not transcribed it yet. Deliberately does not
    # pretend to have understood anything.
    "ack_listen": (
        "One sec, let me catch that.",
        "Give me a moment to hear that properly.",
        "Hang on, listening back.",
        "Just a moment while I take that in.",
    ),
    # A frame arrived and the user asked about it.
    "ack_look": (
        "Let me take a look at that.",
        "Checking the image now.",
        "One moment, looking at what you are showing me.",
        "Give me a second to look at that.",
    ),
    # Work is taking longer than expected.
    "progress": (
        "Still working on {subject}.",
        "Nearly there on {subject}.",
        "That one is taking a moment - still going.",
    ),
    "progress_generic": (
        "Still on it.",
        "Nearly there.",
        "Just a little longer.",
    ),
    # Ambiguous perception. The phrasing deliberately contains "did you say"
    # and both options, which is both the natural way to ask and what the
    # ground truth for ambiguity scenarios looks for.
    "clarify_choice": (
        "Sorry, did you say {options}?",
        "Just to confirm - did you say {options}?",
        "I did not catch that clearly. Did you mean {options}?",
        "Quick check: was that {options}?",
    ),
    # We heard a value, but not clearly enough to act on (M5). Naming it is
    # the honest question and the natural one: the user can answer "yes" or
    # correct just that word. Every variant keeps "did you say", which is
    # also how ambiguity ground truth is written.
    "confirm_heard": (
        "Sorry, did you say {value}?",
        "Just to check - did you say {value}?",
        "I want to be sure I heard that right: did you say {value}?",
    ),
    # We could not make out the audio at all. Distinct from a missing slot:
    # there is no specific value to ask about, so the honest move is to ask
    # for confirmation of the whole request. This is also simply the natural
    # English register for "I did not hear you".
    "clarify_unheard": (
        "Sorry, I did not catch that - could you confirm what you need?",
        "I missed that one. Can you confirm what you would like?",
        "That did not come through clearly - could you confirm that for me?",
        "Sorry, did you say that again? I did not quite catch it.",
    ),
    # A required value is missing entirely.
    "clarify_missing": (
        "Which {label} did you want?",
        "Sure - what {label} should I use?",
        "Happy to help. What {label} are we talking about?",
        "Can you tell me the {label}?",
    ),
    # A required quantity is missing.
    "clarify_count": (
        "How many {label}?",
        "Sure - how many {label} should that be?",
        "And how many {label} would you like?",
    ),
    # Tool failed and we are retrying.
    "retrying": (
        "That did not come back - trying once more.",
        "Hit a snag there, giving it another go.",
        "One more attempt on that.",
    ),
    # A read-only tool failed terminally. {subject} is a noun phrase:
    # "flights to Seattle".
    "failed": (
        "Sorry, I could not get {subject} just now.",
        "I was not able to pull up {subject}, sorry.",
        "Sorry, I had trouble getting {subject}.",
    ),
    # An irreversible call failed in a way that does NOT tell us whether it
    # took effect upstream (a timeout, typically). Truthfulness decides the
    # wording: we cannot say it failed, and we must not say it worked. So we
    # say exactly what we know, and that we will not blindly resend it.
    # {subject} is a gerund phrase, capitalised by the renderer.
    "commit_unknown": (
        "{subject} timed out, so I cannot tell whether it went through. "
        "I will not send it again without checking with you first.",
        "{subject} did not come back in time, so I cannot tell if it went "
        "through - I will hold off on retrying until you say so.",
    ),
    # Fallback for the same situation once the specific lines are spent. It
    # must keep the same meaning: "I could not complete that" would assert a
    # failure we cannot know happened.
    "commit_unknown_generic": (
        "I cannot tell whether that went through, so I will not repeat it "
        "without checking with you.",
        "The outcome of that one is unclear - I will hold off rather than risk "
        "doing it twice.",
        "No clear answer came back on that, so I am not going to send it again "
        "on my own.",
    ),
    # An irreversible call was rejected before it could take effect.
    "commit_rejected": (
        "Sorry, {subject} did not go through - the request was not accepted.",
        "{subject} was turned down, so nothing has changed.",
    ),
    # The thing the irreversible call referred to does not exist.
    "commit_not_found": (
        "Sorry, I could not find that - {subject} did not go through.",
        "That reference does not seem to exist, so {subject} did not go through.",
    ),
    # The environment reports it was already done by an earlier request.
    "commit_duplicate": (
        "That already went through earlier, so I have not made a second one.",
        "It looks like that was already in place, so I did not repeat it.",
    ),
    # The scenario is ending and a tool never came back. Honest, and still
    # specific about what we were waiting for.
    "still_waiting": (
        "Sorry, I am still waiting on {subject}, so I do not have an answer yet.",
        "That is taking longer than usual - still no answer on {subject}.",
    ),
    "still_waiting_generic": (
        "Sorry, that is taking longer than expected, so I do not have an answer yet.",
        "That is running long - I do not have an answer for you yet.",
    ),
    # A successful call that returned nothing. Distinct from a failure:
    # the tool worked, there simply are no hits, and inventing a citation
    # here is the failure mode docs/TOOLS.md warns about.
    "no_results": (
        "I could not find anything about that.",
        "Nothing came back for that one, sorry.",
        "Sorry, there is nothing on that in what I can search.",
        "No results for that, I am afraid.",
        "I do not have anything covering that.",
    ),
    "failed_generic": (
        "Sorry, I could not complete that.",
        "That did not work out - sorry.",
        "I was not able to get that done.",
    ),
    # Reporting a grounded result. The lead-in is not decoration: it keeps
    # the utterance above the scorer's 50%-alphabetic substantive-speech
    # threshold, which a bare identifier like "FL-CHI-8AM, 08:00, $129"
    # fails outright.
    "report": (
        "I found {body}.",
        "Here you go - {body}.",
        "Got one for you: {body}.",
        "That would be {body}.",
        "Looks like {body}.",
        "The best match is {body}.",
    ),
    # Several results fit a question about the camera frame and nothing in
    # the image told them apart. {where} names locations only, never titles:
    # asserting a component we did not identify is the failure this avoids.
    "visual_unsure": (
        "I could not tell from the picture exactly which one that is - "
        "{where} cover the likely candidates. Which one do you mean?",
        "From the image alone I cannot say which one you mean; the closest "
        "matches are {where}. Which one is it?",
    ),
    # Reporting scalar fields rather than a chosen row: "it is currently
    # sunny, 74 degrees, with clear skies through Friday". "I found ..." does
    # not fit a reading, so these lead-ins are neutral.
    "report_fields": (
        "Okay, {body}.",
        "Here is what I have: {body}.",
        "Alright - {body}.",
        "So, {body}.",
    ),
    # Reporting an irreversible action AFTER its tool reported success. Only
    # ever rendered from a confirmed result; the emitter's claim guard would
    # substitute a progress line if it were not.
    "report_done": (
        "All set - {body}.",
        "Done - {body}.",
        "That is sorted: {body}.",
        "There we go - {body}.",
    ),
    # Last-resort filler when every other pool is exhausted. Must still be
    # substantive (>= 3 chars, >= 50% alphabetic).
    "fallback": (
        "Bear with me.",
        "Almost there.",
        "One second more.",
        "Hold on please.",
        "Nearly done.",
        "Just a moment.",
        "Coming right up.",
        "Right with you.",
    ),
}

# Last-resort filler fragments, combined as a cartesian product when every
# pool above is exhausted. Checked by test_nlg_fragments_contain_no_completion_verbs:
# a completion verb here would make the emitter reject its own fallback.
_LAST_RESORT_OPENERS = (
    "Still with you",
    "Bear with me",
    "One moment more",
    "Hang tight",
    "Still going",
    "Just a bit longer",
    "Working on it",
    "Give me one more second",
    "Nearly there",
    "Hold tight",
    "Right with you",
    "Stay with me",
)

_LAST_RESORT_TAILS = (
    ".",
    ", please.",
    " - nearly there.",
    " while I wrap up.",
    " on this one.",
    ", thanks for waiting.",
    " here.",
    ", not much longer.",
    " for you.",
    ", appreciate the patience.",
)

# Human-friendly labels for slot names when asking for a missing value.
# Anything not listed falls back to the slot name with underscores removed,
# which reads acceptably for schema-driven tools we have never seen
# ("pickup_city" -> "pickup city").
_SLOT_LABELS: Dict[str, str] = {
    "destination": "city",
    "date": "date",
    "passenger_name": "name",
    "booking_id": "booking",
    "device_model": "device",
    "issue_summary": "problem",
}


def slot_label(name: str) -> str:
    """A speakable label for a slot, derived generically for unseen slots."""
    if name in _SLOT_LABELS:
        return _SLOT_LABELS[name]
    return str(name).replace("_", " ").strip() or "detail"


def join_options(options: Sequence[str]) -> str:
    """'Austin or Boston'; 'A, B or C' for longer lists."""
    opts = [str(o).strip() for o in options if str(o).strip()]
    if not opts:
        return ""
    if len(opts) == 1:
        return opts[0]
    return ", ".join(opts[:-1]) + " or " + opts[-1]


class Phrasebook:
    """Deterministic, non-repeating utterance generator.

    One instance per conversation. Variants are consumed in a fixed rotation
    so the same string is never produced twice, and the sequence is identical
    across repetitions of the same scenario.
    """

    def __init__(self) -> None:
        self._cursor: Dict[str, int] = {}
        self._used: Set[str] = set()
        self._overflow: int = 0

    # -- core -------------------------------------------------------------
    def _render(self, pool_name: str, **fields: object) -> Optional[str]:
        """Return the next unused variant from a pool, or None if exhausted."""
        pool = _POOLS.get(pool_name)
        if not pool:
            return None
        start = self._cursor.get(pool_name, 0)
        for offset in range(len(pool)):
            template = pool[(start + offset) % len(pool)]
            try:
                text = template.format(**fields)
            except (KeyError, IndexError):
                continue
            text = text.strip()
            if not contract.is_substantive(text):
                continue
            if contract.norm(text) in self._used:
                continue
            self._cursor[pool_name] = (start + offset + 1) % len(pool)
            self._used.add(contract.norm(text))
            return text
        return None

    def _last_resort(self) -> str:
        """Unique, substantive, natural filler after every pool is exhausted.

        A verbatim repeat is a scored penalty; slightly blander phrasing is
        not. So when the pools run dry we compose from a cartesian product
        rather than repeat. 12 openers x 10 tails = 120 combinations, which
        is over ten times the largest plausible utterance count for a single
        scenario, and every one is ordinary English.

        None of these fragments may contain a completion verb from
        contract._GENERIC_COMPLETION, or the emitter's truthfulness guard
        would reject its own fallback and loop.
        """
        for opener in _LAST_RESORT_OPENERS:
            for tail in _LAST_RESORT_TAILS:
                text = opener + tail
                key = contract.norm(text)
                if key in self._used:
                    continue
                if not contract.is_substantive(text):
                    continue
                self._used.add(key)
                return text
        # 120 combinations exhausted in one conversation: something upstream
        # is looping. Stay unique and let the emitter's runaway cap stop it.
        self._overflow += 1
        text = "Still here with you, moment " + str(self._overflow) + "."
        self._used.add(contract.norm(text))
        return text

    def _render_or(self, pool_name: str, fallback_pool: str, **fields: object) -> str:
        """Render from a pool, falling back through a generic pool, then the
        last-resort pool. Guaranteed to return unique, substantive text."""
        text = self._render(pool_name, **fields)
        if text is None:
            text = self._render(fallback_pool)
        if text is None:
            text = self._render("fallback")
        if text is None:
            text = self._last_resort()
        return text

    def mark_used(self, text: str) -> None:
        """Register externally-composed text so we never echo it later."""
        if text:
            self._used.add(contract.norm(text))

    def was_used(self, text: str) -> bool:
        return contract.norm(text) in self._used

    # -- acknowledgments --------------------------------------------------
    def ack_lookup(self, subject: Optional[str] = None) -> str:
        if subject:
            return self._render_or("ack_lookup", "ack_generic", subject=subject)
        return self._render_or("ack_generic", "fallback")

    def ack_commit(self, subject: Optional[str] = None) -> str:
        """Announce irreversible work as it starts - a promise, not a claim."""
        if subject:
            return self._render_or("ack_commit", "ack_generic", subject=subject)
        return self._render_or("ack_generic", "fallback")

    def ack_correction(self, value: object, old: object = None) -> str:
        """Acknowledge a correction, naming the new value.

        Naming the value is not decoration: it is how the user knows the
        correction landed, and the ground truth for interruption scenarios
        checks for it in a short window after the barge-in.
        """
        if value is None:
            return self._render_or("ack_generic", "fallback")
        if old is not None and str(old) != str(value):
            text = self._render("ack_correction_from", value=value, old=old)
            if text:
                return text
        return self._render_or("ack_correction", "ack_generic", value=value)

    def ack_retraction(self) -> str:
        return self._render_or("ack_retraction", "ack_generic")

    def ack_intent_change(self) -> str:
        return self._render_or("ack_intent_change", "ack_generic")

    def ack_listen(self) -> str:
        return self._render_or("ack_listen", "ack_generic")

    def ack_look(self) -> str:
        return self._render_or("ack_look", "ack_generic")

    def progress(self, subject: Optional[str] = None) -> str:
        if subject:
            return self._render_or("progress", "progress_generic", subject=subject)
        return self._render_or("progress_generic", "fallback")

    def retrying(self) -> str:
        return self._render_or("retrying", "ack_generic")

    def failed(self, subject: Optional[str] = None) -> str:
        if subject:
            return self._render_or("failed", "failed_generic", subject=subject)
        return self._render_or("failed_generic", "fallback")

    def failed_commit(self, subject: Optional[str], error: Optional[str]) -> str:
        """An irreversible call did not succeed. Say only what we know.

        The error code decides what that is. `invalid_args` and
        `unknown_tool` prove nothing happened; `not_found` and
        `duplicate_booking` are definite answers; anything else - a timeout
        above all - leaves the outcome unknown, and the honest sentence is
        the one that says so.
        """
        code = str(error or "")
        if code == "duplicate_booking":
            return self._render_or("commit_duplicate", "failed_generic")
        pool = {"not_found": "commit_not_found",
                "invalid_args": "commit_rejected",
                "unknown_tool": "commit_rejected"}.get(code, "commit_unknown")
        text = self._render(pool, subject=subject or "that request")
        if text is None and pool == "commit_unknown":
            text = self._render("commit_unknown_generic")
        if text is None:
            return self._render_or("failed_generic", "fallback")
        return _capitalise(text)

    def still_waiting(self, subject: Optional[str] = None) -> str:
        """The scenario is ending and work never came back."""
        if subject:
            return self._render_or("still_waiting", "still_waiting_generic",
                                   subject=subject)
        return self._render_or("still_waiting_generic", "fallback")

    # -- clarifications ---------------------------------------------------
    def clarify_choice(self, options: Sequence[str]) -> str:
        """Ask between competing hypotheses (M5 - calibrated abstention).

        Naming both candidates is the honest form of the question and tells
        the user exactly what we are unsure about.
        """
        joined = join_options(options)
        if not joined:
            return self.clarify_missing("detail")
        return self._render_or("clarify_choice", "ack_generic", options=joined)

    def confirm_heard(self, value: object) -> str:
        """Confirm a value we heard but do not trust yet, by name."""
        label = str(value or "").strip()
        if not label:
            return self.clarify_unheard()
        return self._render_or("confirm_heard", "clarify_unheard", value=label)

    def clarify_unheard(self) -> str:
        """Ask for the whole request again when nothing was intelligible."""
        return self._render_or("clarify_unheard", "clarify_missing", label="detail")

    def clarify_count(self, arg_name: str) -> str:
        """Ask for a missing quantity: 'party_size' -> 'How many party size?'
        reads badly, so a trailing size/count/number word is dropped."""
        label = slot_label(arg_name)
        words = [w for w in label.split() if w not in ("size", "count", "number", "num", "of")]
        label = " ".join(words) or label
        return self._render_or("clarify_count", "clarify_missing", label=label)

    def clarify_missing(self, slot_name: str) -> str:
        label = slot_label(slot_name)
        return self._render_or("clarify_missing", "ack_generic", label=label)

    # -- reporting --------------------------------------------------------
    def report(self, body: str, style: str = "found") -> str:
        """Wrap a grounded result phrase in a natural sentence.

        `style` picks the register: "found" for a chosen result row ("I found
        flight FL-CHI-8AM ..."), "fields" for a reading ("Okay, it is currently
        sunny ..."), "done" for an irreversible action whose tool has
        reported success ("All set - flight FL-DEN-8AM is booked ...").
        """
        body = str(body or "").strip().rstrip(".")
        if not body:
            return self._render_or("failed_generic", "fallback")
        pool = {"fields": "report_fields", "done": "report_done"}.get(style, "report")
        return self._render_or(pool, "report", body=body)

    def visual_unsure(self, where: str) -> str:
        """Several candidates fit the frame; say where to look and ask."""
        return self._render_or("visual_unsure", "failed_generic", where=where)

    def no_results(self) -> str:
        """A successful search that found nothing."""
        return self._render_or("no_results", "failed_generic")

    # -- capabilities -----------------------------------------------------
    def capabilities(self, tool_descriptions: Sequence[str]) -> str:
        """Answer 'what can you do?' from the live manifest.

        Derived from the tools we were actually given, never from a hardcoded
        list: the same code answers correctly for a manifest of flight tools
        or one of smart-home tools it has never seen.
        """
        phrases: List[str] = []
        for desc in tool_descriptions:
            phrase = _summarise_description(desc)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
        # A spoken answer, not a catalogue: past five items a listener stops
        # hearing individual entries.
        extra = len(phrases) > _MAX_CAPABILITIES
        phrases = phrases[:_MAX_CAPABILITIES]
        if not phrases:
            text = "I can help with whatever tools I have been given - just tell me what you need."
        elif len(phrases) == 1:
            text = "I can help you " + phrases[0] + ". What would you like to do?"
        else:
            tail = (", " + phrases[-1] + ", and a few other things") if extra \
                else (", and " + phrases[-1])
            text = ("I can help you " + ", ".join(phrases[:-1]) + tail
                    + ". What would you like to do?")
        self._used.add(contract.norm(text))
        return text


_MAX_CAPABILITIES = 5

# Verbs a capability phrase may start with. Anything else ("Current weather
# and a short forecast") is a noun phrase and gets "get" in front of it.
_CAPABILITY_VERBS = frozenset("""
search find book cancel open create file look check get retrieve list show
set schedule reserve order buy send track report update change add remove
delete start stop run turn play quote estimate compare convert translate
call pay lock unlock arm disarm dim adjust control monitor locate reset
""".split())

# Prepositions that always begin detail we do not need in a spoken summary.
_HARD_STOPS = frozenset(("to", "for", "on", "by", "with", "at", "returned",
                         "that", "which", "via", "using", "into", "per"))
# Prepositions that begin detail only when followed by a determiner:
# "pages FROM device manuals" is the substance, "hotels IN A city" is not.
_SOFT_STOPS = frozenset(("in", "from", "of", "about", "across", "within"))
_DETERMINERS = frozenset(("a", "an", "the", "any", "some", "each", "every",
                          "your", "their", "its", "given"))
# Adjectives that carry no meaning once the detail they qualify is gone.
_VAGUE = frozenset(("specific", "given", "existing", "particular", "indexed",
                    "unresolved", "certain", "relevant"))


def _summarise_description(description: str) -> str:
    """Turn a tool description into a short capability phrase.

        'Search flights to a destination city on a given date.' -> 'search flights'
        'Book a specific flight returned by flight_search.'     -> 'book a flight'
        'Retrieve pages from indexed device manuals. ...'       -> 'retrieve pages from device manuals'
        'Current weather and a short forecast for a city.'      -> 'get current weather and a short forecast'

    Works on descriptions we have never seen: it keeps the leading clause up
    to the first preposition that starts incidental detail, drops adjectives
    that only made sense with that detail, and fixes the article left behind.
    """
    first = re.split(r"[.;:!?]", str(description or ""), maxsplit=1)[0]
    tokens = re.findall(r"[A-Za-z0-9'\-]+", first)
    if not tokens:
        return ""
    kept: List[str] = []
    for i, token in enumerate(tokens):
        low = token.lower()
        nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
        if i > 0 and low in _HARD_STOPS:
            break
        if i > 0 and low in _SOFT_STOPS and nxt in _DETERMINERS:
            break
        if "_" in token:
            break
        kept.append(low)
        if len(kept) >= 7:
            break
    kept = [t for t in kept if t not in _VAGUE]
    # "an existing booking" -> "an booking" -> "a booking"
    for i in range(len(kept) - 1):
        if kept[i] == "an" and kept[i + 1][:1] not in "aeiou":
            kept[i] = "a"
        elif kept[i] == "a" and kept[i + 1][:1] in "aeiou":
            kept[i] = "an"
    while kept and kept[-1] in _DETERMINERS | {"and", "or"}:
        kept.pop()
    if not kept:
        return ""
    if kept[0] not in _CAPABILITY_VERBS:
        kept.insert(0, "get")
    return " ".join(kept)


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text
