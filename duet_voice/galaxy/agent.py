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
from typing import Any, Dict, Optional

from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_ROOT, ".env.livekit"))

from livekit import agents, rtc  # noqa: E402
from livekit.agents import AgentServer, AgentSession, JobContext, JobExecutorType  # noqa: E402

from .. import agent as base  # noqa: E402  (the benchmark agent's building blocks)
from ..config import CONFIG  # noqa: E402
from ..coordinator import Coordinator  # noqa: E402
from ..plugins import KokoroTTS, WhisperSTT  # noqa: E402
from ..thinker import make_thinker  # noqa: E402
from .modes import llm_specs, mode as get_mode  # noqa: E402
from .tools import PhoneToolbox, rpc_sender  # noqa: E402

log = logging.getLogger("duet.galaxy")

AGENT_NAME = os.environ.get("DUET_GALAXY_AGENT", "duet-galaxy")

# the second look before an answer is spoken (DUET_REVIEW=0 turns it off)
REVIEW = ("Second look before your answer is spoken: did the user ask, in their latest words, for an "
          "action that has no successful tool result above yet? If so, call that tool now. If not, "
          "reply with exactly: OK")

CONSENT = ("You check one action a phone assistant is about to take. Answer yes only if the user's "
           "words ask for this action, or say yes to the assistant offering it. A question about a "
           "problem is not a request to change anything. Answer with one word: yes or no.")


async def user_agreed(tool: str, args: Dict[str, Any], asked: str, offered: str) -> bool:
    """The consent gate: one short model call, only for actions that change the user's
    settings, call or text someone, or control the home."""
    if CONFIG.llm_backend != "local":
        return True
    from ..llm_local import client
    prompt = ('The assistant last said: "%s"\nThe user then said: "%s"\nAction: %s(%s)\n'
              "Did the user ask for this action or agree to it?"
              % (offered or "(nothing yet)", asked, tool, json.dumps(args)))
    resp = await client().chat.completions.create(
        model=CONFIG.talker_model, max_tokens=3, temperature=0, seed=CONFIG.seed,
        messages=[{"role": "system", "content": CONSENT}, {"role": "user", "content": prompt}])
    answer = (resp.choices[0].message.content or "").strip().lower()
    return answer.startswith("yes")


# what the phone's timeline shows, and which fields of each event it needs
SHOWN = {
    "user_state": ("state",), "heard": ("text",), "think_start": ("text",),
    "thinker_listen": ("reason",), "listen_done": (), "thinker_decided": ("tools", "after_s"),
    "talker": ("text", "reason"), "thinker_say": ("text", "after_s"), "thinker_error": ("text",),
    "thinker_review": ("text",),
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
        super().__init__(trace, coord, toolbox,
                         thinker=make_thinker(specs=llm_specs(mode["tools"]), instructions=instructions,
                                              review=REVIEW if os.environ.get("DUET_REVIEW", "1") == "1" else None),
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

    def turn_note(self) -> str:
        now = dt.datetime.now()
        lines = ["Now: %s, %s." % (now.strftime("%A %d %B %Y"), now.strftime("%H:%M"))]
        if self.cut_off:
            lines.append('Your last answer was cut off. The user heard only: "%s"' % self.cut_off)
            self.cut_off = None
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

    toolbox = PhoneToolbox(coord, mode["tools"], rpc_sender(ctx.room, lambda: phone.identity), emit=publish,
                           consent=consent if os.environ.get("DUET_CONSENT", "1") == "1" else None)
    context = "; ".join(x for x in (
        ("Their name is %s." % name) if name else "",
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
            "preemptive_generation": {"enabled": True,
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
        trace("phone_event", kind=msg.get("kind"), text=text)
        user_input = text if msg.get("kind") == "text" else "[Phone event] " + text
        session.generate_reply(user_input=user_input)

    trace("session_start", room=room, mode=mode["title"], backend=CONFIG.llm_backend,
          thinker=agent._thinker.model, asr=CONFIG.asr_model, tts=CONFIG.tts_backend)
    publish({"kind": "session_start", "mode": mode["title"], "model": agent._thinker.model})
    await session.start(room=ctx.room, agent=agent)
    session.say(mode["greeting"].format(name=name or "there").replace("Hello there", "Hello"))


if __name__ == "__main__":
    agents.cli.run_app(server)
