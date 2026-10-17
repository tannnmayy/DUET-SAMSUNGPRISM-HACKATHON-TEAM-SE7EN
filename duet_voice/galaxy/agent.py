"""DUET for Galaxy: the agent worker the phone app talks to.

    python -m duet_voice.galaxy.agent dev      # serve the app's rooms (LiveKit Cloud, .env.livekit)

The phone app asks the token server (duet_voice/galaxy/server.py) to join a new
room; the room's configuration dispatches this worker (agent name `duet-galaxy`)
with the mode (assistant, care or drive) and a little about the phone. From
there it is the benchmark's agent with the mode's tools and instructions:
the same ears and mouth, the same talker and thinker, the same coordinator. The
benchmark's worker (duet_voice/agent.py) is a separate program and is unchanged.

Two things are new here, both for a person holding a phone rather than a
benchmark recording:
- the thinker knows the time, and, when the user cut an answer short, how much of
  it they actually heard;
- every step is shown live on the phone (the DUET timeline, topic "duet").
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
from typing import Any, Dict, Optional

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
from . import fast, smartthings  # noqa: E402
from .confirm import adds_nothing  # noqa: E402
from .modes import llm_specs, mode as get_mode  # noqa: E402
from .server_tools import SERVER_TOOLS  # noqa: E402
from .tools import PhoneToolbox, rpc_sender  # noqa: E402

log = logging.getLogger("duet.galaxy")

# The recogniser's vocabulary hint: the phone's words, not the benchmark's flights and passports.
# This process serves only the phone app, so the benchmark's own hint is untouched.
speech_models.ASR_PROMPT = (
    "Alarms, timers and reminders, battery, brightness, screen timeout, Wi-Fi, Bluetooth, settings, "
    "apps, Google Maps navigation, calls and text messages, SmartThings, air conditioner, lights, "
    "medicines and tablets, check-in, destination, arrival time.")

AGENT_NAME = os.environ.get("DUET_GALAXY_AGENT", "duet-galaxy")

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
    "drill": ("cloud_off",),
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
    def __init__(self, trace, coord, toolbox: PhoneToolbox, mode: Dict[str, Any], context: str) -> None:
        instructions = mode["thinker"] + ("\nTHE USER AND THEIR PHONE\n" + context if context else "")
        thinker = make_thinker(specs=llm_specs(mode["tools"]), instructions=instructions,
                               review=REVIEW if os.environ.get("DUET_REVIEW", "1") == "1" else None)
        self._route_now: Optional[str] = None   # where this request is being handled: edge or cloud

        def on_route(where: str, why: str) -> None:
            self._route_now = where
            trace("route", where=where, why=why)
        if fast.enabled():
            # two speeds: the small Gemma on our GPU looks first (duet_voice/galaxy/fast.py)
            thinker = fast.HybridThinker(fast.make_fast_thinker(mode["tools"], mode["title"]), thinker,
                                         on_route=on_route)
        super().__init__(trace, coord, toolbox, thinker=thinker,
                         instructions=instructions, talker_instructions=mode["talker"])
        self.cut_off: Optional[str] = None
        self._seen = 0  # coordinator history already covered by an answered request

    async def llm_node(self, chat_ctx, tools, model_settings):
        self._route_now = None
        async for chunk in super().llm_node(chat_ctx, tools, model_settings):
            yield chunk

    def ack_line(self) -> str:
        """"Sure." when the fast mind is doing a simple action (its confirmation follows in about a
        second); "One moment." when the request needs the cloud mind, or nobody knows yet."""
        return "Sure." if self._route_now == "edge" else "One moment."

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
        (duet_voice/galaxy/confirm.py); the slow mind's answer adds the rest."""
        done = self._toolbox.last_done
        if not done or done[0] != getattr(ev, "name", None):
            return ""
        return done[3] or ""

    def progress_allowed(self, lines_so_far: int, confirmed) -> bool:
        """One "Still working on it." at most, and none once an action has been confirmed: the
        rest of the wait is only the wording of the answer."""
        return lines_so_far < 1 and not confirmed

    def silent_answers(self, confirmed) -> bool:
        """A silent reply closes the request only when something was done in it (the fast mind's
        confirmations said it all). Otherwise the words stay open for what the user says next: 17 Oct
        2026, "Call my" got a silent reply, and the "My daughter" that followed lost its "call"."""
        return bool(confirmed) or any(r.outcome == "ok" for r in self._coord.history[self._seen:])

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
        - drop an answer that only repeats what was already confirmed aloud in this reply."""
        text = re.sub(r"^\s*(ok(ay)?|alright)[.,!]\s*", "", " ".join((words or "").split()), flags=re.I)
        parts = [p for p in re.split(r"(?<=[.!?])\s+", text) if p]
        while len(parts) > 1 and FILLER.match(parts[-1]):
            parts.pop()
        text = " ".join(parts)
        if adds_nothing(text, self._confirmed_now):
            return ""
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
        return "\n".join(lines)

    def reply_delivered(self) -> None:
        closed = self._pending_consumed is not None
        super().reply_delivered()
        if closed:
            self._seen = len(self._coord.history)
            self._toolbox.new_request()
            stale_readings(self._thinker.history)


# --- the voice: the fixed lines are synthesized once ------------------------------------------------

FIXED_LINES = ("Sure.", "One moment.", "Still working on it.")
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
    async def _run(self, output_emitter) -> None:
        import numpy as np
        audio = fixed_audio(self.input_text)
        if audio is None:
            audio = await asyncio.to_thread(speech_models.synthesize, self.input_text)
        pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        from livekit.agents import utils
        output_emitter.initialize(request_id=utils.shortuuid(), sample_rate=KokoroTTS.SAMPLE_RATE,
                                  num_channels=1, mime_type="audio/pcm")
        output_emitter.push(pcm16)
        output_emitter.flush()


class GalaxyTTS(KokoroTTS):
    """Kokoro, with "Sure.", "One moment." and "Still working on it." ready before they are needed."""

    def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS) -> "FixedLineStream":
        return FixedLineStream(tts=self, input_text=text, conn_options=conn_options)


def prewarm(proc) -> None:
    base.prewarm(proc)
    for line in FIXED_LINES:
        fixed_audio(line)


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

    async def publish_home() -> None:
        """The home's devices as they are now, for the phone's home panel."""
        try:
            publish({"kind": "home", "source": "SmartThings", "devices": await smartthings.snapshot()})
        except Exception as exc:
            publish({"kind": "home", "source": "SmartThings", "error": str(exc)[:160]})

    def emit(msg: Dict[str, Any]) -> None:
        publish(msg)
        if home["live"] and msg.get("kind") in ("tool_done", "tool_failed", "tool_unknown") \
                and msg.get("tool") in smartthings.TOOLS:
            asyncio.ensure_future(publish_home())
    coord = Coordinator(commit_hold_s=CONFIG.commit_hold_s, revising_hold_s=CONFIG.revising_hold_s,
                        dangling_hold_s=CONFIG.dangling_hold_s)
    said = {"last": ""}  # what DUET last said, for the consent gate

    async def consent(tool: str, args: Dict[str, Any]) -> bool:
        words = coord.open_utterance
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
    toolbox = PhoneToolbox(coord, mode["tools"], rpc_sender(ctx.room, lambda: phone.identity), emit=emit,
                           consent=consent if os.environ.get("DUET_CONSENT", "1") == "1" else None,
                           server={k: fn for k, fn in run_here.items() if k in names})
    context = "; ".join(x for x in (
        ("Their name is %s." % name) if name else "",
        ("They live in %s." % meta["city"]) if meta.get("city") else "",
        ("Phone: %s." % meta["device"]) if meta.get("device") else "",
        ("Their contacts: %s." % meta["contacts"]) if meta.get("contacts") else "",
    ) if x)
    agent = GalaxyAgent(trace, coord, toolbox, mode, context)

    session = AgentSession(
        vad=ctx.proc.userdata.get("vad") or base.build_vad(),
        stt=WhisperSTT(),
        llm=base.build_placeholder_llm(),
        tts=GalaxyTTS(),
        turn_handling={
            "turn_detection": base.build_turn_detection(),
            "endpointing": {"min_delay": CONFIG.endpoint_min_s, "max_delay": CONFIG.endpoint_max_s},
            "preemptive_generation": {"enabled": CONFIG.preemptive,
                                      "max_speech_duration": CONFIG.preempt_max_speech_s,
                                      "max_retries": CONFIG.preempt_max_retries},
            # how much speech cuts DUET off (LiveKit's default 0.5 s): the lever for a loudspeaker whose
            # echo interrupts DUET (DUET_INTERRUPT_MIN_S=0.8); speech with no words within 2 s resumes it
            **({"interruption": {"min_duration": float(os.environ["DUET_INTERRUPT_MIN_S"])}}
               if os.environ.get("DUET_INTERRUPT_MIN_S") else {}),
        },
        user_away_timeout=None,
    )

    @session.on("user_state_changed")
    def _user_state(ev) -> None:
        if ev.new_state == "speaking":
            coord.user_started_speaking()
            agent.mark_speech_start()
        elif ev.old_state == "speaking":
            coord.user_stopped_speaking()
        trace("user_state", state=ev.new_state)

    @session.on("user_input_transcribed")
    def _heard(ev) -> None:
        if ev.is_final:
            coord.heard(ev.transcript)
            trace("heard", text=ev.transcript)

    @session.on("agent_state_changed")
    def _agent_state(ev) -> None:
        trace("agent_state", state=ev.new_state)

    @session.on("conversation_item_added")
    def _item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) == "assistant":
            interrupted = bool(getattr(item, "interrupted", False))
            trace("agent_said", text=item.text_content, interrupted=interrupted)
            said["last"] = (item.text_content or "").strip()
            if interrupted and item.text_content:
                agent.cut_off = item.text_content.strip()
            agent.reply_delivered()

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
            # the offline drill: the cloud mind is cut (or restored); the fast mind keeps going
            off = bool(msg.get("cloud_off"))
            if isinstance(agent._thinker, fast.HybridThinker):
                agent._thinker.cloud_off = off
                trace("drill", cloud_off=off)
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


if __name__ == "__main__":
    agents.cli.run_app(server)
