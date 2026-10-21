"""DUET for Galaxy: the agent worker the phone app talks to.

    python -m duet_voice.galaxy.agent dev      # serve the app's rooms (LiveKit Cloud, .env.livekit)

The phone app asks the token server (duet_voice/galaxy/server.py) to join a new
room; the room's configuration dispatches this worker (agent name `duet-galaxy`)
with the mode (assistant, care or drive) and a little about the phone. From
there it is the benchmark's agent with the mode's tools and instructions:
the same ears and mouth, the same talker and thinker, the same coordinator. The
benchmark's worker (duet_voice/agent.py) is a separate program and is unchanged.

Three things are new here, all for a person holding a phone rather than a
benchmark recording:
- the thinker knows the time, and, when the user cut an answer short, how much of
  it they actually heard;
- DUET hears how the user said it (duet_voice/galaxy/tone.py): every speech segment
  is measured as it ends and read with the request's words, for the coordinator's
  hold, the phone's actions, both minds and DUET's voice (DUET_TONE=0 turns it off);
- every step is shown live on the phone (the DUET timeline, topic "duet").
"""

from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import logging
import os
import re
import time
from typing import Any, Deque, Dict, Optional, Tuple

import numpy as np
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_ROOT, ".env.livekit"))

# Phone-mode timing. The benchmark's values are tuned to its rule that a silence over 2 s ends
# the user's turn. On a phone, close a turn sooner and say "One moment." after 0.4 s of quiet
# instead of 1.6 s. The thinker's keep_listening still holds back an answer to an unfinished
# sentence, and the commit gate still holds every action. Set before the config is read.
# With two speeds, the fast mind's route is usually known within half a second: the acknowledgement waits
# that long for it ("Sure." for a simple action, "One moment." for the cloud mind, said the moment the
# request goes to the cloud). "Still working on it." only after 6 s: cloud answers mostly take 4 to 5.
# A person holding a phone does not wait 28 s (the benchmark's answer window) for the cloud mind: 15 s at
# most, and 2 s when Google cannot be reached at all (then DUET says so; simple actions stay on the fast mind).
for _name, _value in (("DUET_ENDPOINT_MIN", "0.5"), ("DUET_ACK_MIN_QUIET", "0.4"),
                      ("DUET_PROGRESS_AFTER", "6"),
                      ("DUET_ACK_GRACE", "0.5" if os.environ.get("DUET_FAST", "0") == "1" else "0.3"),
                      ("DUET_GEMMA_DEADLINE_S", "15"), ("DUET_GEMMA_OFFLINE_AFTER_S", "2"),
                      # the fast mind decides in 0.3 s, so the gate's hold alone stands between a pause and
                      # an action: 1.4 s, not the benchmark's 1.1 s (17 Oct 2026: "3:30." then "no, no"
                      # after a 1.1 s pause set 3:30 first, then undid it)
                      ("DUET_COMMIT_HOLD", "1.4"),
                      # the cloud mind answers without a long reasoning step: about half the wait, and every
                      # phone test since 16 Oct ran this way (the benchmark leaves Gemma's default)
                      ("DUET_THINKER_THINKING", "minimal"),
                      # a phone mode's call is about 3,600 input tokens (its tools and instructions):
                      # booked as such from the first call, a fresh agent does not draw 429s
                      ("DUET_GEMMA_EST_TOKENS", "3800")):
    os.environ.setdefault(_name, _value)

from livekit import agents, rtc  # noqa: E402
from livekit.agents import AgentServer, AgentSession, JobContext, JobExecutorType  # noqa: E402
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS  # noqa: E402

from .. import agent as base  # noqa: E402  (the benchmark agent's building blocks)
from .. import speech_models  # noqa: E402
from ..config import CONFIG  # noqa: E402
from ..coordinator import Coordinator  # noqa: E402
from ..plugins import KokoroStream, KokoroTTS, WhisperSTT  # noqa: E402
from ..thinker import make_thinker  # noqa: E402
from . import fast, proactive, smartthings, tone  # noqa: E402
from .confirm import adds_nothing, drop_failed_claims, drop_false_undo, drop_overclaims, drop_restated  # noqa: E402
from .modes import light_instructions, llm_specs, mode as get_mode  # noqa: E402
from .server_tools import SERVER_TOOLS  # noqa: E402
from .tools import PhoneToolbox, cut_off, rpc_sender  # noqa: E402

log = logging.getLogger("duet.galaxy")

# The recogniser's vocabulary hint: the phone's words, not the benchmark's flights and passports.
# This process serves only the phone app, so the benchmark's own hint is untouched.
speech_models.ASR_PROMPT = (
    "Alarms, timers and reminders, battery, brightness, screen timeout, Wi-Fi, Bluetooth, settings, "
    "apps, Google Maps navigation, calls and text messages, SmartThings, air conditioner, lights, "
    "medicines and tablets, check-in, destination, arrival time.")

AGENT_NAME = os.environ.get("DUET_GALAXY_AGENT", "duet-galaxy")
HOME_POLL_S = float(os.environ.get("DUET_HOME_POLL_S", "10"))   # how often the home is asked what changed
# how long "One moment." waits for the fast mind to decide (it answers small talk itself in about half a second)
FAST_WAIT_S = float(os.environ.get("DUET_FAST_WAIT_S", "1.5"))
# the tone channel: after a raised voice, actions wait this long for its words (the words decide, never the tone);
# DUET speaks at this rate to a user who sounds distressed
RAISED_WAIT_S = 4.0
DISTRESSED_SPEED = float(os.environ.get("DUET_TONE_SLOW", "0.9"))

# the second look before an answer is spoken (DUET_REVIEW=0 turns it off)
REVIEW = ("Second look before your answer is spoken. Actions carried out in this turn: {done}. "
          "Did the user ask, in their latest words, for an action that is not in that list? If so, call "
          "that tool now. If not, reply with exactly: OK")

# chosen on 11 real cases from recorded conversations (11/11 on the laptop's 4B model)
CONSENT = ("You check one action a phone assistant is about to take. First, is the user's last sentence "
           "a question? A user who asks a question wants an answer, not this action: then answer no, "
           "unless they are saying yes to the assistant's offer. Otherwise answer yes only if the user's "
           "words ask for this action or accept the assistant's offer of it. Answer with one word: yes or no.")


def consent_model() -> Optional[tuple]:
    """(server, model) answering the consent question: the laptop's local model, or the phone
    app's fast mind. None: no local model (the cloud mind's instructions carry the rule alone)."""
    if CONFIG.llm_backend == "local":
        return None, CONFIG.talker_model
    if fast.enabled() and os.environ.get("DUET_FAST_CONSENT", "1") == "1":
        return (os.environ.get("DUET_FAST_BASE_URL", "http://127.0.0.1:18002/v1"),
                os.environ.get("DUET_FAST_MODEL", "gemma-4-e2b"))
    return None


def asked_for(tool: str, words: str) -> bool:
    """The user's own words plainly ask for this action: they name it, and they are not a question
    ("set the AC to 22" yes; "is it my brightness?" no). Then no model is asked."""
    return fast.user_named(tool, words) and not fast.asks_question(words)


async def user_agreed(tool: str, args: Dict[str, Any], asked: str, offered: str) -> bool:
    """The consent gate: one short model call, only for actions that change the user's
    settings, call or text someone, or control the home."""
    where = consent_model()
    if where is None:
        return True
    from ..llm_local import client
    prompt = ('The assistant last said: "%s"\nThe user then said: "%s"\nAction: %s(%s)\n'
              "Did the user ask for this action or agree to it?"
              % (offered or "(nothing yet)", asked, tool, json.dumps(args)))
    resp = await asyncio.wait_for(client(where[0]).chat.completions.create(
        model=where[1], max_tokens=3, temperature=0, seed=CONFIG.seed,
        messages=[{"role": "system", "content": CONSENT}, {"role": "user", "content": prompt}]), 3.0)
    answer = (resp.choices[0].message.content or "").strip().lower()
    return answer.startswith("yes")


# The question for a request cut off mid-phrase, when the model asks nothing itself
ASK_REST = {"call_contact": "Who should I call?", "send_message": "Who should I text?",
            "set_alarm": "For what time?", "set_reminder": "For what time?", "set_timer": "For how long?",
            "navigate_to": "Where to?", "set_destination": "Where to?", "add_stop": "Where should I add the stop?",
            "control_home_device": "Which device, and what should I do with it?"}

FILLER = re.compile(r"(just )?(let me know|feel free|is there anything else|anything else)", re.I)

# Readings that change: once a reply is delivered, the thinker's memory keeps that it read them, not
# what they said, so a later question is answered from a fresh reading (17 Oct 2026: asked again two
# minutes later, the battery was answered from the old reading, whatever the instructions said).
VOLATILE_READS = {"get_battery_report", "get_home_status", "list_alarms", "get_trip_status", "get_weather",
                  "get_app_usage", "get_medication_schedule"}
STALE = {"status": "out_of_date", "note": "an earlier reading, now out of date: read it again with the tool if needed"}


def stale_readings(history) -> int:
    """Replace the results of volatile readings in a thinker's history (Gemma's contents, or a local
    model's chat messages) with an out-of-date note. Returns how many were replaced."""
    n = 0
    names = {c.get("id"): (c.get("function") or {}).get("name") for item in history or [] if isinstance(item, dict)
             for c in item.get("tool_calls") or []}
    for item in history or []:
        if isinstance(item, dict):                     # a local model's chat message
            if item.get("role") == "tool" and names.get(item.get("tool_call_id")) in VOLATILE_READS \
                    and item.get("content") != json.dumps(STALE):
                item["content"] = json.dumps(STALE)
                n += 1
            continue
        for part in getattr(item, "parts", None) or []:
            fr = getattr(part, "function_response", None)
            if fr is not None and fr.name in VOLATILE_READS and fr.response != {"result": STALE}:
                fr.response = {"result": STALE}
                n += 1
    return n

# The cloud mind cannot be reached (no internet, Google down, or the offline drill): said at once, and
# true, because simple phone actions run on the fast mind on our own machine.
OFFLINE_LINE = ("I can't reach my cloud mind right now, so I can't do that one. Simple things on the phone, "
                "like alarms, timers and the torch, still work.")

# what the phone's timeline shows, and which fields of each event it needs
SHOWN = {
    "user_state": ("state",), "heard": ("text",), "think_start": ("text",),
    "thinker_listen": ("reason",), "listen_done": (), "thinker_decided": ("tools", "after_s"),
    "talker": ("text", "reason"), "thinker_say": ("text", "after_s"), "thinker_error": ("text",),
    "thinker_review": ("text",), "confirmed": ("text",), "route": ("where", "why"),
    "superseded": (), "agent_said": ("text", "interrupted"), "agent_state": ("state",),
    "drill": ("cloud_off", "what", "on", "seconds"),
    "offer": ("text", "why", "items", "source"), "offer_accepted": ("text",), "offer_declined": ("text",),
    "offer_expired": ("text",), "tone": ("label", "cues"), "tone_ask": ("text", "cues"),
}


class RoomTrace(base.Trace):
    """The run log, plus a live copy of the steps that matter on the phone's screen."""

    def __init__(self, room: str, publish) -> None:
        super().__init__(room)
        self.publish = publish

    def __call__(self, kind: str, **data: Any) -> None:
        super().__call__(kind, **data)
        if kind == "talker" and not data.get("text"):
            return  # the fast mind had nothing in time (logged, not shown)
        if kind in SHOWN:
            self.publish({"kind": kind, **{k: data[k] for k in SHOWN[kind] if k in data}})


class GalaxyAgent(base.DuetAgent):
    def __init__(self, trace, coord, toolbox: PhoneToolbox, mode: Dict[str, Any], context: str,
                 user_name: str = "") -> None:
        instructions = mode["thinker"] + ("\nTHE USER AND THEIR PHONE\n" + context if context else "")
        thinker = make_thinker(specs=llm_specs(mode["tools"]), instructions=instructions,
                               review=REVIEW if os.environ.get("DUET_REVIEW", "1") == "1" else None)
        self._route_now: Optional[str] = None   # where this request is being handled: edge or cloud

        def on_route(where: str, why: str) -> None:
            self._route_now = where
            trace("route", where=where, why=why)
        if fast.enabled():
            # two speeds: the small Gemma on our GPU looks first (duet_voice/galaxy/fast.py)
            thinker = fast.HybridThinker(fast.make_fast_thinker(mode["tools"], mode["title"], user_name), thinker,
                                         on_route=on_route)
        super().__init__(trace, coord, toolbox, thinker=thinker,
                         instructions=instructions, talker_instructions=mode["talker"])
        self.cut_off: Optional[str] = None
        self._seen = 0  # coordinator history already covered by an answered request
        # what DUET offered by itself (proactive.py): its note goes to both minds, and the consent gate
        # and the fast mind's check accept an answer that takes it up
        self.offers = proactive.Offers(on_status=lambda o: trace("offer_" + o.status, text=o.text))
        # the conversation as it was really heard and said, [(role, text), ...]: the fast mind answers small
        # talk in context, and the cloud mind hears what the fast mind said
        self.recent: Deque[Tuple[str, str]] = collections.deque(maxlen=2 * fast.RECENT_TURNS)
        self._request_text = ""         # the open request the current reply answers
        self._asked_at = 0.0            # when its reply began
        self._answer_now = ""           # the answer of the current reply, as spoken (polish)
        self._last_reply = ""           # DUET's last reply in full: its confirmations and its answer
        self._cut_heard = ""            # what the user heard of that reply, if they cut it off
        if isinstance(self._thinker, fast.HybridThinker):
            self._thinker.offers = self.offers
            self._thinker.recent = self.recent
            # talk and health with nothing on the phone in them: one cloud call with no tools (Care: never)
            self._thinker.light_instructions = light_instructions(str(mode["title"]).lower(), context)
        # how the user sounds (tone.py): each speech segment measured as it ends, read with the request's words
        self.listener = tone.Listener() if tone.enabled() else None
        self._tone: Optional[tone.Reading] = None      # the reading of the request being answered
        self._tone_jobs: set = set()                   # segments being measured
        self._speech_from = 0.0                        # when the current speech segment began (wall clock)
        self._speech_floor = 0.0                       # where the previous segment's audio ended
        self.voice = None                              # the GalaxyTTS: its rate follows the user's tone
        if self.listener is not None:
            coord.extra_hold = self.tone_hold
            toolbox.tone = lambda: self.listener.reading(self._coord.open_utterance)
            toolbox.on_unsure = self._unsure_offer

    async def llm_node(self, chat_ctx, tools, model_settings):
        self._route_now = None
        self._request_text = self._open_request(chat_ctx)
        self._asked_at = time.time()
        if isinstance(self._thinker, fast.HybridThinker):
            # "say that again" and "go on" are answered from DUET's last reply (fast.control)
            self._thinker.last_answer = self._last_reply
            # (what was cut stays known through a "Wait.", which says nothing, until DUET next speaks)
            self._thinker.resume_text = fast.remainder(self._last_reply, self._cut_heard)
        self._answer_now = ""
        self._tone = None
        if self.listener is not None:
            if self._tone_jobs:
                # the last segment is measured in about 20 ms, long before the turn closes; never wait long
                await asyncio.wait(list(self._tone_jobs), timeout=0.3)
            self._tone = self.listener.reading(self._request_text)
            if self._tone.label:
                self._trace("tone", **self._tone.event())
        if isinstance(self._thinker, fast.HybridThinker):
            self._thinker.tone = self._tone
        if self.voice is not None:
            self.voice.speed = DISTRESSED_SPEED if self._tone is not None and self._tone.upset == "distressed" else 1.0
        spoke = False
        async for chunk in super().llm_node(chat_ctx, tools, model_settings):
            spoke = spoke or (isinstance(chunk, str) and bool(chunk.strip()))
            # kept as it is made: a reply the user cuts off is closed before it ends
            full = " ".join(self._confirmed_now + ([self._answer_now] if self._answer_now else []))
            if full.strip():
                self._last_reply = full.strip()
            yield chunk
        if not spoke and self._pending_consumed is not None:
            # A reply that says nothing ("Wait.", background talk) answers its request, but no message reaches the
            # conversation, so nothing would close it: it is closed here. 21 Oct 2026: "Wait." then "Go on." was
            # read as the one request "Wait. Go on.", so "go on" was not recognised.
            self.reply_delivered("")

    def ack_line(self) -> str:
        """"Sure." when the fast mind is doing a simple action (its confirmation follows in about a
        second); "One moment." when the request needs the cloud mind. Nothing while the fast mind is still
        deciding (half a second, as a rule): it may answer by itself, and "One moment." before "Good morning!"
        would be wrong. If it is slow, the line comes after FAST_WAIT_S."""
        if self._tone is not None and self._tone.unsure:
            return ""      # the phone asks about the value itself in a moment ("4:30, right?"): "Sure." would be wrong
        if self._route_now == "edge":
            return "Sure."
        if self._route_now is None and fast.enabled() and time.time() - self._asked_at < FAST_WAIT_S:
            return ""
        return "One moment."

    def done_note(self, done_before) -> str:
        """What a plan the user overtook had already done on the phone. It happened, so the
        thinker must know it: to not repeat it, or to undo it if the user took it back."""
        fresh = [r for r in self._coord.history[self._seen:] if r.outcome == "ok"]
        if not fresh:
            return ""
        return ("Already carried out on the phone for this request, before the user's latest words. This "
                "really happened. Do not repeat it; if the user's words cancel or change it, undo it with "
                "the matching tool first, then do what they want now: " + "; ".join(
                    "%s(%s)" % (r.tool, json.dumps(r.args)) for r in fresh))

    def confirm(self, ev) -> str:
        """The action that just ran, confirmed at once from the phone's own reply
        (duet_voice/galaxy/confirm.py); the slow mind's answer adds the rest. The confirmation travels on
        the event itself: by the time an event is spoken, the thinker may have run the next action."""
        if getattr(ev, "text", ""):
            return ev.text
        if getattr(ev, "outcome", "ok") not in ("ok", ""):
            return ""
        done = self._toolbox.last_done
        if not done or done[0] != getattr(ev, "name", None):
            return ""
        return done[3] or ""

    def progress_allowed(self, lines_so_far: int, confirmed) -> bool:
        """One "Still working on it." at most, and none once an action has been confirmed: the
        rest of the wait is only the wording of the answer."""
        return lines_so_far < 1 and not confirmed

    def silent_fallback(self, confirmed) -> str:
        """The request stops mid-phrase ("Call my"), or the guards held it back as undecided, and the model
        then said nothing: DUET asks for the missing part itself. 18 Oct 2026: after "Call my", the cloud
        mind replied <silent> although told never to, and the user heard only "One moment." (once with the
        call held back by the guard, once with no call planned at all)."""
        if confirmed:
            return ""
        failed = getattr(self._toolbox, "failed", [])
        if failed:
            # the phone refused or failed an action and the model said nothing (18 Oct 2026, the refusal drill:
            # "One moment." and then silence): never leave the user guessing whether it happened
            self._toolbox.failed = []
            if any(why == "needs_permission" for _, why in failed):
                return "The phone didn't let me do that. You can allow it in DUET's settings."
            return "Sorry, that didn't work on the phone."
        held = self._toolbox.unfinished
        if not held:
            words = self._coord.open_utterance
            if not cut_off(words):
                return ""
            held = (next((t for t in ASK_REST if fast.user_named(t, words)), ""), "cut_off")
        self._toolbox.unfinished = None
        tool, why = held
        if why == "undecided":
            return "Which one do you want?"
        if why == "unsure":
            return (self._toolbox.confirming or ("", {}, "Did I get that right?"))[2]
        return ASK_REST.get(tool, "Sorry, I didn't catch the end of that. Could you finish it?")

    def silent_answers(self, confirmed) -> bool:
        """Does a silent reply close the request? Yes, unless something in it is still unfinished: words cut
        off mid-phrase (17 Oct 2026: "Call my" got a silent reply, and the "My daughter" that followed lost its
        "call"), an action a guard held back, or one the phone refused. Those stay open for what the user says
        next. Before 21 Oct 2026 every silent reply with nothing done stayed open, and on the S24 "Good night.
        I said good night. Good morning. How are you. Thank you. I am not good." piled up into one request."""
        if confirmed or any(r.outcome == "ok" for r in self._coord.history[self._seen:]):
            return True
        unfinished = cut_off(self._coord.open_utterance) or self._toolbox.unfinished or getattr(
            self._toolbox, "failed", [])
        return not unfinished

    def error_answers(self, why: str) -> bool:
        """"I can't reach my cloud mind" answers the request: it is not kept open for the next words
        (measured 17 Oct 2026: a weather question asked during the drill was answered, from the cloud,
        in place of the battery question asked after it)."""
        return fast.cloud_unreachable(why)

    def error_line(self, confirmed, why: str = "") -> str:
        if confirmed:
            return ""   # what was asked is done and was said; nothing is missing
        if fast.cloud_unreachable(why):
            return OFFLINE_LINE if fast.enabled() else OFFLINE_LINE.split(". ")[0] + "."
        return "Sorry, I couldn't get that done just now. Could you say it again?"

    def polish(self, words: str) -> str:
        """Spoken, not read:
        - drop a leading "OK." and a closing filler sentence ("Let me know if you need anything
          else."), which models add however often they are told not to;
        - drop a sentence that claims more than the phone reported ("I've sent it" after "ready; tap send");
        - drop a clause that says a failed action happened ("The AC is on, but I can't reach your home");
        - drop an answer that only repeats what was already confirmed aloud in this reply."""
        text = re.sub(r"^\s*(ok(ay)?|alright)[.,!]\s*", "", " ".join((words or "").split()), flags=re.I)
        text = text[:1].upper() + text[1:]     # "Okay, what can I do?" is shown as "What can I do?"
        parts = [p for p in re.split(r"(?<=[.!?])\s+", text) if p]
        while len(parts) > 1 and FILLER.match(parts[-1]):
            parts.pop()
        text = drop_failed_claims(" ".join(parts), getattr(self._toolbox, "failed_calls", []), self._confirmed_now)
        text = drop_overclaims(text, self._confirmed_now)
        text = drop_restated(text, self._confirmed_now)
        text = fast.drop_second_apology(text, getattr(self._thinker, "bridge_said", ""))
        if not any(r.outcome == "ok" for r in self._coord.history[self._seen:]):
            # nothing ran in this request, so nothing was cancelled ("never mind" is not an undo)
            text = drop_false_undo(text, fast.drops_something(self._coord.open_utterance))
        if adds_nothing(text, self._confirmed_now):
            return ""
        if text and "<silent>" not in text:
            self._answer_now = text
        return text

    def turn_note(self) -> str:
        now = dt.datetime.now()
        lines = ["Now: %s, %s." % (now.strftime("%A %d %B %Y"), now.strftime("%H:%M"))]
        if self.cut_off:
            lines.append('Your last answer was cut off. The user heard only: "%s"' % self.cut_off)
            self.cut_off = None
        ran = {(r.tool, json.dumps(r.args, sort_keys=True)) for r in self._coord.history if r.outcome == "ok"}
        never = ["%s(%s)" % (c["tool"], json.dumps(c["args"])) for c in self._toolbox.held_back
                 if (c["tool"], json.dumps(c["args"], sort_keys=True)) not in ran]
        if never:
            lines.append("Planned but never carried out, because the user kept talking (it did not happen, "
                         "so there is nothing to undo): " + "; ".join(never))
        read = sorted({r.tool for r in self._coord.history[:self._seen] if r.tool in VOLATILE_READS and r.outcome == "ok"})
        if read:
            lines.append("Earlier in this conversation you read: %s. Those readings, and your answers from them, "
                         "are out of date now: if the user asks about any of it, call the tool again before you "
                         "answer." % ", ".join(read))
        offer = self.offers.active()
        if offer is not None:
            lines.append(offer.note())
        if self._tone is not None and self._tone.note():
            lines.append(self._tone.note())
        return "\n".join(lines)

    def reply_delivered(self, said: str = "") -> None:
        """`said`: what DUET's message said (as far as the user heard it)."""
        closed = self._pending_consumed is not None
        super().reply_delivered()
        self.offers.answered()
        if closed:
            self._seen = len(self._coord.history)
            self._toolbox.new_request()
            stale_readings(self._thinker.history)
            if self._request_text:
                self.recent.append(("user", self._request_text))
            self._request_text = ""
            if self.listener is not None:
                self.listener.close(self._asked_at)
        # the fixed lines say nothing about the conversation
        words = re.sub(r"^\s*((%s)\s*)+" % "|".join(re.escape(x) for x in FIXED_LINES), "", " ".join(
            (said or "").split()))
        if words:
            self.recent.append(("assistant", words))


    # -- the tone channel ----------------------------------------------------------------------------------
    def speech_started(self) -> None:
        self._speech_from = time.time()

    def speech_ended(self) -> None:
        """A user speech segment ended: it is measured from the tape in a worker thread, while Whisper transcribes it."""
        if self.listener is None:
            return
        now = time.time()
        t0 = max(self._speech_from - 0.5, self._speech_floor)
        self._speech_floor = now
        chunks = [(t, rate, pcm) for t, rate, pcm in self._tape.frames if t >= t0]
        if not chunks:
            return
        slot = self.listener.expect(now)        # its place: its words find it even before it is measured
        rate = chunks[-1][1]
        same = [(t, pcm) for t, r, pcm in chunks if r == rate]
        audio = np.concatenate([pcm for _, pcm in same])
        start = same[0][0] - len(same[0][1]) / rate       # a frame's time is when it arrived, at its end
        job = asyncio.ensure_future(self._measure(audio, rate, start, slot))
        self._tone_jobs.add(job)
        job.add_done_callback(self._tone_jobs.discard)

    async def _measure(self, audio, rate: int, start: float, slot) -> None:
        try:
            f = await asyncio.to_thread(lambda: tone.analyse(speech_models.to_16k(audio, rate), start))
        except Exception as exc:  # the voice is a second channel; the words still work without it
            log.warning("tone: a segment could not be measured: %s", exc)
            f = None
        f = self.listener.add(f, into=slot)
        if f is not None:
            self._trace("tone_segment", **f.summary())

    def heard_words(self, text: str) -> None:
        if self.listener is not None and (text or "").strip():
            self.listener.heard(text, time.time())

    def tone_hold(self) -> float:
        """The coordinator's extra hold (never shorter than the words' own): after a raised voice whose words have
        not arrived, RAISED_WAIT_S of quiet, so a shouted "no" is heard before anything runs; while the user sounds
        hesitant about a value, the revising hold, the time to correct it."""
        if self.listener.awaiting_words():
            return RAISED_WAIT_S
        return self._coord.revising_hold_s if self.listener.reading(self._coord.open_utterance).unsure else 0.0

    def _unsure_offer(self, tool: str, args: Dict[str, Any], question: str, action: str, reading) -> None:
        """The phone asks back a value the user sounded unsure of: the question is DUET's offer, so "yes" runs it as
        asked, "no, 5:30" changes it, "no" leaves it (proactive.py, fast.HybridThinker)."""
        self.offers.make(proactive.Offer(text=question, why="you sounded unsure: " + "; ".join(reading.cues),
                                         items=[proactive.OfferItem(tool, dict(args), action)], source="tone"))
        self._trace("tone_ask", text=question, tool=tool, args=args, cues="; ".join(reading.cues))


# --- the voice: the fixed lines are synthesized once ------------------------------------------------

FIXED_LINES = ("Sure.", "One moment.", "Still working on it.") + tuple(sorted(set(fast.BRIDGE_LINES.values())))
_fixed_audio: Dict[str, Any] = {}


def fixed_audio(text: str):
    """The cached audio of a fixed line, or None for any other text."""
    key = " ".join((text or "").split())
    if key not in FIXED_LINES:
        return None
    if key not in _fixed_audio:
        _fixed_audio[key] = speech_models.synthesize(key)
    return _fixed_audio[key]


class FixedLineStream(KokoroStream):
    speed = 1.0

    async def _run(self, output_emitter) -> None:
        audio = fixed_audio(self.input_text) if self.speed == 1.0 else None
        if audio is None:
            audio = await asyncio.to_thread(speech_models.synthesize, self.input_text, self.speed)
        pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        from livekit.agents import utils
        output_emitter.initialize(request_id=utils.shortuuid(), sample_rate=KokoroTTS.SAMPLE_RATE,
                                  num_channels=1, mime_type="audio/pcm")
        output_emitter.push(pcm16)
        output_emitter.flush()


class GalaxyTTS(KokoroTTS):
    """Kokoro, with "Sure.", "One moment." and "Still working on it." ready before they are needed. `speed` is set
    by the agent for each reply (a little slower for a user who sounds distressed)."""
    speed = 1.0

    def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS) -> "FixedLineStream":
        stream = FixedLineStream(tts=self, input_text=text, conn_options=conn_options)
        stream.speed = self.speed
        return stream


def prewarm(proc) -> None:
    base.prewarm(proc)
    for line in FIXED_LINES:
        fixed_audio(line)
    try:
        tone.prewarm()          # the emotion model (a 10 MB download the first time) and Praat
    except Exception as exc:  # tone without its models still reads the words and the timing
        log.warning("tone: prewarm failed: %s", exc)


def _load() -> float:
    try:
        return min(1.0, len(server.active_jobs) * 0.25)
    except Exception:
        return 0.0


server = AgentServer(
    setup_fnc=prewarm,
    load_fnc=_load,
    load_threshold=0.99,
    job_executor_type=JobExecutorType.THREAD,
    num_idle_processes=int(os.environ.get("DUET_IDLE_PROCESSES", "1")),
    initialize_process_timeout=240.0,
)


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except ValueError:
        meta = {}
    mode = get_mode(meta.get("mode", "assistant"))
    name = (meta.get("user_name") or "").strip()
    room = ctx.room.name

    def publish(msg: Dict[str, Any]) -> None:
        try:
            data = json.dumps(msg, default=str)
            asyncio.ensure_future(ctx.room.local_participant.publish_data(data, reliable=True, topic="duet"))
        except Exception as exc:  # the screen is a view; never let it break the conversation
            log.debug("could not publish to the phone: %s", exc)

    await ctx.connect()
    smartthings.DRILL["down"] = False   # a drill never outlives its conversation
    # the SmartThings home, checked while the phone joins (a token lasts 24 hours)
    home_check = asyncio.ensure_future(smartthings.check()) if smartthings.enabled() else None
    phone = await ctx.wait_for_participant()
    trace = RoomTrace(room, publish)
    home = {"live": False, "why": "no SmartThings token: the home is simulated in the app"}
    if home_check is not None:
        try:
            out = await asyncio.wait_for(home_check, 8.0)
        except Exception as exc:  # the home never stops the conversation
            out = {"ok": False, "why": "SmartThings did not answer (%s)" % (str(exc)[:80] or type(exc).__name__)}
        home = {"live": bool(out.get("ok")), "why": out.get("why", "")}
        if not home["live"]:
            log.warning("SmartThings unavailable, using the app's simulated home: %s", home["why"])
    trace("home", source="SmartThings" if home["live"] else "simulated", why=home["why"])

    home_view: Dict[str, Dict[str, Any]] = {}   # the phone's home panel: device name -> its state

    async def publish_home() -> None:
        """The home's devices as they are now, for the phone's home panel (read once, at the start)."""
        try:
            for d in await smartthings.snapshot():
                home_view[d["name"]] = d
            publish({"kind": "home", "source": "SmartThings", "devices": list(home_view.values())})
        except Exception as exc:
            publish({"kind": "home", "source": "SmartThings", "error": str(exc)[:160]})

    def home_changed(result: Any) -> None:
        """A command's own reply says the device's new state ("on, 22°C cool"): the panel is updated from
        it, with no request. 18 Oct 2026: re-reading the whole home after every action (seven requests)
        ran out SmartThings' ~10 requests a minute."""
        if not isinstance(result, dict) or result.get("device") not in home_view or not result.get("now"):
            return
        now = str(result["now"])
        home_view[result["device"]].update(state=now, on=now.startswith(("on", "unlocked", "cleaning", "running")))
        publish({"kind": "home", "source": "SmartThings", "devices": list(home_view.values())})

    def emit(msg: Dict[str, Any]) -> None:
        publish(msg)
        if home["live"] and msg.get("kind") == "tool_done" and msg.get("tool") == "control_home_device":
            home_changed(msg.get("result"))
        if msg.get("kind") == "tool_done":
            taken = agent.offers.done(str(msg.get("tool")), msg.get("args") or {})
            if taken is not None:
                trace("offer_accepted", text=taken.text, tool=msg.get("tool"), args=msg.get("args"))
    coord = Coordinator(commit_hold_s=CONFIG.commit_hold_s, revising_hold_s=CONFIG.revising_hold_s,
                        dangling_hold_s=CONFIG.dangling_hold_s)
    said = {"last": ""}  # what DUET last said, for the consent gate

    async def consent(tool: str, args: Dict[str, Any]) -> bool:
        words = coord.open_utterance
        if agent.offers.consent(tool, args, words):
            # the user's answer takes up DUET's own offer ("yes", "make it 22"): they agreed to exactly this
            trace("consent", tool=tool, args=args, allowed=True, by="offer")
            return True
        if asked_for(tool, words):
            # the user's own words ask for it ("set the AC to 22, no 21"): no model to second-guess
            # them (17 Oct 2026: the small model said no to exactly that). A question that mentions it
            # ("is it my brightness?") is still checked.
            trace("consent", tool=tool, args=args, allowed=True, by="named")
            return True
        ok = await user_agreed(tool, args, words, said["last"])
        trace("consent", tool=tool, args=args, allowed=ok, by="model")
        return ok

    names = {t["name"] for t in mode["tools"]}
    run_here = dict(SERVER_TOOLS, **(smartthings.TOOLS if home["live"] else {}))
    rpc_send = rpc_sender(ctx.room, lambda: phone.identity)
    toolbox = PhoneToolbox(coord, mode["tools"], rpc_send, emit=emit,
                           consent=consent if os.environ.get("DUET_CONSENT", "1") == "1" else None,
                           server={k: fn for k, fn in run_here.items() if k in names})
    context = "; ".join(x for x in (
        ("Their name is %s." % name) if name else "",
        ("They live in %s." % meta["city"]) if meta.get("city") else "",
        ("Phone: %s." % meta["device"]) if meta.get("device") else "",
        ("Their contacts: %s." % meta["contacts"]) if meta.get("contacts") else "",
    ) if x)
    agent = GalaxyAgent(trace, coord, toolbox, mode, context, user_name=name)
    voice = GalaxyTTS()
    agent.voice = voice

    interruption: Dict[str, Any] = {}
    if os.environ.get("DUET_INTERRUPT_MIN_S"):
        interruption["min_duration"] = float(os.environ["DUET_INTERRUPT_MIN_S"])
    if os.environ.get("DUET_INTERRUPT_MODE") in ("adaptive", "vad"):
        interruption["mode"] = os.environ["DUET_INTERRUPT_MODE"]
    session = AgentSession(
        vad=ctx.proc.userdata.get("vad") or base.build_vad(),
        stt=WhisperSTT(),
        llm=base.build_placeholder_llm(),
        tts=voice,
        turn_handling={
            "turn_detection": base.build_turn_detection(),
            "endpointing": {"min_delay": CONFIG.endpoint_min_s, "max_delay": CONFIG.endpoint_max_s},
            "preemptive_generation": {"enabled": CONFIG.preemptive,
                                      "max_speech_duration": CONFIG.preempt_max_speech_s,
                                      "max_retries": CONFIG.preempt_max_retries},
            # how much speech cuts DUET off (LiveKit's default 0.5 s): the lever for a loudspeaker whose
            # echo interrupts DUET (DUET_INTERRUPT_MIN_S=0.8); speech with no words within 2 s resumes it.
            # DUET_INTERRUPT_MODE=adaptive: LiveKit's own model tells a real interruption from "mm-hm" (it runs
            # on LiveKit Cloud's inference; LiveKit leaves it off by default in production mode)
            **({"interruption": interruption} if interruption else {}),
        },
        user_away_timeout=None,
    )

    @session.on("user_state_changed")
    def _user_state(ev) -> None:
        if ev.new_state == "speaking":
            coord.user_started_speaking()
            agent.mark_speech_start()
            agent.speech_started()
        elif ev.old_state == "speaking":
            coord.user_stopped_speaking()
            agent.speech_ended()
        trace("user_state", state=ev.new_state)

    @session.on("user_input_transcribed")
    def _heard(ev) -> None:
        if ev.is_final:
            coord.heard(ev.transcript)
            agent.heard_words(ev.transcript)
            trace("heard", text=ev.transcript)

    agent_now = {"state": "initializing"}

    @session.on("agent_state_changed")
    def _agent_state(ev) -> None:
        agent_now["state"] = ev.new_state
        trace("agent_state", state=ev.new_state)

    async def speak_first(offer: proactive.Offer) -> None:
        """DUET speaks without being asked, once nobody is talking and it is not busy (it waits up to
        20 s). The user can talk over it like any other line. Nothing runs from the offer itself."""
        for _ in range(80):
            if agent_now["state"] == "listening" and not coord.user_speaking:
                break
            await asyncio.sleep(0.25)
        else:
            trace("offer_skipped", text=offer.text, why="the conversation stayed busy for 20 s")
            return
        if offer.items:
            agent.offers.make(offer)
        trace("offer", **{k: v for k, v in offer.event().items() if k != "kind"})
        session.say(offer.text)
        if offer.items:
            async def lapse() -> None:
                await asyncio.sleep(proactive.OFFER_TTL_S + 0.5)
                if not offer.is_open() and offer.status == "expired":
                    trace("offer_expired", text=offer.text)
            asyncio.ensure_future(lapse())

    async def arrive(msg: Dict[str, Any]) -> None:
        """The car is nearly at its destination (the app's trip). Nearly home: offer to get the home ready,
        from the home's real state and the outside temperature. Anywhere else: say how far it is."""
        place = str(msg.get("destination") or "").strip() or "your destination"
        try:
            minutes = max(1, int(round(float(msg.get("minutes") or 10))))
        except (TypeError, ValueError):
            minutes = 10
        if not re.search(r"\bhome\b", place, re.I):
            await speak_first(proactive.Offer(text="About %d minutes to %s now." % (minutes, place),
                                              why="%d min left on the trip" % minutes, items=[], source="arrival"))
            return

        async def home_now() -> list:
            if home["live"]:
                # only what an arrival offer can offer: every read counts against SmartThings' limit
                return await smartthings.snapshot(kinds={"ac", "light"})
            reply = json.loads(await rpc_send("get_home_status", {}, 8.0))
            return proactive.devices_from_phone(reply.get("devices") or [])

        async def outside_now() -> Optional[float]:
            city = (meta.get("city") or "").strip()
            if not city:
                return None
            weather = await SERVER_TOOLS["get_weather"]({"place": city})
            return ((weather or {}).get("now") or {}).get("temperature_c")

        got = await asyncio.gather(asyncio.wait_for(home_now(), 8.0), asyncio.wait_for(outside_now(), 5.0),
                                   return_exceptions=True)
        devices = got[0] if isinstance(got[0], list) else []
        outside = got[1] if isinstance(got[1], (int, float)) else None
        if not isinstance(got[0], list):
            log.warning("arrival offer without the home's state: %s", got[0])
        await speak_first(proactive.arrival_offer(minutes, devices, outside, dt.datetime.now().hour))

    async def watch_home() -> None:
        """What the home reports by itself: a washer that has finished gets an offer (Assistant mode). Only
        the washer is asked: SmartThings allows about 10 requests a minute, and a whole-home check every
        10 s (18 Oct 2026) left too few for the user's own commands and their read-backs."""
        last: Dict[str, str] = {}
        while ctx.room.isconnected():
            try:
                for d in [d for d in await smartthings.devices() if d["kind"] == "washer"]:
                    state = smartthings.describe("washer", await smartthings.status(d["id"]))
                    was, last[d["label"]] = last.get(d["label"]), state
                    if was and was.startswith("running") and state == "finished":
                        asyncio.ensure_future(speak_first(proactive.washer_offer()))
            except Exception as exc:  # the home's news is a bonus; never let it break the conversation
                log.debug("home check: %s", exc)
            await asyncio.sleep(HOME_POLL_S)

    @session.on("conversation_item_added")
    def _item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) == "assistant":
            interrupted = bool(getattr(item, "interrupted", False))
            trace("agent_said", text=item.text_content, interrupted=interrupted)
            said["last"] = (item.text_content or "").strip()
            if interrupted and item.text_content:
                agent.cut_off = item.text_content.strip()
            agent.reply_delivered(said=item.text_content or "")
            agent._cut_heard = (item.text_content or "") if interrupted else ""   # for "go on"

    @ctx.room.on("data_received")
    def _from_phone(packet: rtc.DataPacket) -> None:
        """A phone or car event ("10 minutes from home") or typed text."""
        if packet.topic != "duet.phone":
            return
        try:
            msg = json.loads(packet.data.decode("utf-8"))
        except ValueError:
            return
        if msg.get("kind") == "drill":
            # the app's drills: a failure shown live, and how DUET recovers (the fast mind keeps going)
            hybrid = agent._thinker if isinstance(agent._thinker, fast.HybridThinker) else None
            if "cloud_off" in msg and hybrid is not None:
                hybrid.cloud_off = bool(msg["cloud_off"])
                trace("drill", what="cloud_off", on=hybrid.cloud_off, cloud_off=hybrid.cloud_off)
            if "slow_cloud_s" in msg and hybrid is not None:
                hybrid.cloud_delay_s = max(0.0, min(20.0, float(msg.get("slow_cloud_s") or 0)))
                trace("drill", what="slow_cloud", on=hybrid.cloud_delay_s > 0, seconds=hybrid.cloud_delay_s)
            if "home_down" in msg and home["live"]:
                smartthings.DRILL["down"] = bool(msg["home_down"])
                trace("drill", what="home_down", on=smartthings.DRILL["down"])
            return
        if msg.get("kind") == "event" and msg.get("event") == "near_destination":
            # the trip itself says the car is nearly there: DUET builds an offer from facts and says it first
            trace("phone_event", event="near_destination", text=str(msg.get("text", "")))
            asyncio.ensure_future(arrive(msg))
            return
        text = str(msg.get("text", "")).strip()
        if not text or coord.user_speaking:
            return
        trace("phone_event", event=msg.get("kind"), text=text)
        typed = msg.get("kind") == "text"
        # Typed words and phone events never pass the ears, so the coordinator is told here: typed words are
        # words the user said, and either way the turn is complete. Without this, an action they lead to
        # waited at the gate for a spoken turn to close (forever, before the user had spoken at all).
        if typed:
            coord.heard(text)
        coord.turn_committed()
        session.generate_reply(user_input=text if typed else "[Phone event] " + text)

    trace("session_start", room=room, mode=mode["title"], backend=CONFIG.llm_backend,
          thinker=agent._thinker.model, asr=CONFIG.asr_model, tts=CONFIG.tts_backend)
    publish({"kind": "session_start", "mode": mode["title"], "model": agent._thinker.model,
             "home": "SmartThings" if home["live"] else "simulated", "home_why": home["why"]})
    if home["live"]:
        asyncio.ensure_future(publish_home())
    await session.start(room=ctx.room, agent=agent)
    session.say(mode["greeting"].format(name=name or "there").replace("Hello there", "Hello"))
    if isinstance(agent._thinker, fast.HybridThinker):
        asyncio.ensure_future(fast.warm_up(agent._thinker.fast))   # while the greeting plays
    if home["live"] and "set_timer" in names:
        watcher = asyncio.ensure_future(watch_home())

        async def _stop_watch() -> None:
            watcher.cancel()
        ctx.add_shutdown_callback(_stop_watch)


if __name__ == "__main__":
    agents.cli.run_app(server)
