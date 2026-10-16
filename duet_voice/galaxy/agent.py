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
for _name, _value in (("DUET_ENDPOINT_MIN", "0.5"), ("DUET_ACK_MIN_QUIET", "0.4"),
                      ("DUET_PROGRESS_AFTER", "4")):
    os.environ.setdefault(_name, _value)

from livekit import agents, rtc  # noqa: E402
from livekit.agents import AgentServer, AgentSession, JobContext, JobExecutorType  # noqa: E402

from .. import agent as base  # noqa: E402  (the benchmark agent's building blocks)
from .. import speech_models  # noqa: E402
from ..config import CONFIG  # noqa: E402
from ..coordinator import Coordinator  # noqa: E402
from ..plugins import KokoroTTS, WhisperSTT  # noqa: E402
from ..thinker import make_thinker  # noqa: E402
from . import fast  # noqa: E402
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

# what the phone's timeline shows, and which fields of each event it needs
SHOWN = {
    "user_state": ("state",), "heard": ("text",), "think_start": ("text",),
    "thinker_listen": ("reason",), "listen_done": (), "thinker_decided": ("tools", "after_s"),
    "talker": ("text", "reason"), "thinker_say": ("text", "after_s"), "thinker_error": ("text",),
    "thinker_review": ("text",), "confirmed": ("text",), "route": ("where", "why"),
    "superseded": (), "agent_said": ("text", "interrupted"), "agent_state": ("state",),
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
        if fast.enabled():
            # two speeds: the small Gemma on our GPU looks first (duet_voice/galaxy/fast.py)
            thinker = fast.HybridThinker(fast.make_fast_thinker(mode["tools"], mode["title"]), thinker,
                                         on_route=lambda where, why: trace("route", where=where, why=why))
        super().__init__(trace, coord, toolbox, thinker=thinker,
                         instructions=instructions, talker_instructions=mode["talker"])
        self.cut_off: Optional[str] = None
        self._seen = 0  # coordinator history already covered by an answered request

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

    def error_line(self, confirmed) -> str:
        if confirmed:
            return ""   # what was asked is done and was said; nothing is missing
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
        return "\n".join(lines)

    def reply_delivered(self) -> None:
        closed = self._pending_consumed is not None
        super().reply_delivered()
        if closed:
            self._seen = len(self._coord.history)
            self._toolbox.new_request()


def _load() -> float:
    try:
        return min(1.0, len(server.active_jobs) * 0.25)
    except Exception:
        return 0.0


server = AgentServer(
    setup_fnc=base.prewarm,
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
    phone = await ctx.wait_for_participant()
    trace = RoomTrace(room, publish)
    coord = Coordinator(commit_hold_s=CONFIG.commit_hold_s, revising_hold_s=CONFIG.revising_hold_s,
                        dangling_hold_s=CONFIG.dangling_hold_s)
    said = {"last": ""}  # what DUET last said, for the consent gate

    async def consent(tool: str, args: Dict[str, Any]) -> bool:
        ok = await user_agreed(tool, args, coord.open_utterance, said["last"])
        trace("consent", tool=tool, args=args, allowed=ok)
        return ok

    names = {t["name"] for t in mode["tools"]}
    toolbox = PhoneToolbox(coord, mode["tools"], rpc_sender(ctx.room, lambda: phone.identity), emit=publish,
                           consent=consent if os.environ.get("DUET_CONSENT", "1") == "1" else None,
                           server={k: fn for k, fn in SERVER_TOOLS.items() if k in names})
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
        tts=KokoroTTS(),
        turn_handling={
            "turn_detection": base.build_turn_detection(),
            "endpointing": {"min_delay": CONFIG.endpoint_min_s, "max_delay": CONFIG.endpoint_max_s},
            "preemptive_generation": {"enabled": CONFIG.preemptive,
                                      "max_speech_duration": CONFIG.preempt_max_speech_s,
                                      "max_retries": CONFIG.preempt_max_retries},
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
        text = str(msg.get("text", "")).strip()
        if not text or coord.user_speaking:
            return
        trace("phone_event", event=msg.get("kind"), text=text)
        user_input = text if msg.get("kind") == "text" else "[Phone event] " + text
        session.generate_reply(user_input=user_input)

    trace("session_start", room=room, mode=mode["title"], backend=CONFIG.llm_backend,
          thinker=agent._thinker.model, asr=CONFIG.asr_model, tts=CONFIG.tts_backend)
    publish({"kind": "session_start", "mode": mode["title"], "model": agent._thinker.model})
    await session.start(room=ctx.room, agent=agent)
    session.say(mode["greeting"].format(name=name or "there").replace("Hello there", "Hello"))


if __name__ == "__main__":
    agents.cli.run_app(server)
