"""The slow mind: Gemini with the tools, run as DUET's own tool loop.

LiveKit supplies the ears and the mouth. The thinking is ours, for three
reasons that matter on real disfluent speech:

1. **Every call passes the coordinator** (commit gate, ledger, failure policy),
   not a framework loop that fires tools as soon as the model emits them.
2. **The thinker can hear the turn.** Besides the transcript, the model gets
   the turn's audio (`DUET_THINKER_AUDIO=1`), so a mis-heard word ("nearest"
   for "in euros") or a spelled id can be recovered from the sound itself.
   The paper attributes the cascade's lower pass rate to ASR errors
   propagating downstream; this is the fix.
3. **It keeps its own native conversation state** (function calls, results and
   Gemini's thought signatures), which a text-only chat history would lose.

`run()` is an async generator of events, so the agent can decide what to say
while the thinker is still working.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .config import CONFIG
from .coordinator import Superseded
from .fdb_tools import TOOL_SPECS
from .prompts import THINKER_INSTRUCTIONS

log = logging.getLogger("duet.thinker")

from .gemini import client  # one client per process, API key or Vertex AI


def encode_audio(pcm16k) -> Tuple[bytes, str]:
    """16 kHz float mono -> compact bytes for the request (OGG/Vorbis, else WAV)."""
    import numpy as np
    import soundfile as sf
    buf = io.BytesIO()
    try:
        sf.write(buf, np.asarray(pcm16k, dtype=np.float32), 16000, format="OGG", subtype="VORBIS")
        return buf.getvalue(), "audio/ogg"
    except Exception:
        buf = io.BytesIO()
        sf.write(buf, np.asarray(pcm16k, dtype=np.float32), 16000, format="WAV", subtype="PCM_16")
        return buf.getvalue(), "audio/wav"


@dataclass
class ThinkEvent:
    kind: str                     # decided | tool_start | tool_done | say | error
    text: str = ""
    tools: bool = False
    name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    outcome: str = ""
    usage: Dict[str, int] = field(default_factory=dict)


def usage_of(resp) -> Dict[str, int]:
    """Token counts of one response, for the cost analysis. Audio input is
    counted separately because it is billed at a higher rate than text."""
    u = getattr(resp, "usage_metadata", None)
    if u is None:
        return {}
    audio = sum(int(d.token_count or 0) for d in (getattr(u, "prompt_tokens_details", None) or [])
                if str(getattr(d.modality, "value", d.modality)).upper().endswith("AUDIO"))
    return {"input": int(getattr(u, "prompt_token_count", 0) or 0),
            "input_audio": audio,
            "output": int(getattr(u, "candidates_token_count", 0) or 0),
            "thinking": int(getattr(u, "thoughts_token_count", 0) or 0)}


KEEP_LISTENING = "keep_listening"
KEEP_LISTENING_DESCRIPTION = (
    "Call this INSTEAD of acting or answering when the user has clearly not finished "
    "speaking: their last words are cut off mid-sentence or mid-list, or they announced a "
    "detail they have not said yet ('the order number is...', 'and also...'), or the "
    "request lacks something they are evidently about to give. Nothing is done and "
    "nothing is said; you will be asked again once they go quiet. Never call it for a "
    "request that can be carried out as it stands.")


def make_thinker(model: Optional[str] = None, thinking: Optional[str] = None,
                 specs: Optional[List[Dict[str, Any]]] = None, instructions: Optional[str] = None,
                 review: Optional[str] = None):
    """The thinker for the configured backend: the local model (default) or Gemini.
    Both have the same interface and yield the same events. `specs` and
    `instructions` default to the benchmark's twelve tools and instructions; the
    phone app (duet_voice/galaxy) passes its own, and a second-look `review`
    question (local model only)."""
    if CONFIG.llm_backend == "local":
        from .llm_local import LocalThinker
        return LocalThinker(model=model, thinking=thinking, specs=specs, instructions=instructions,
                            review=review)
    return Thinker(model=model, thinking=thinking, specs=specs, instructions=instructions)


# The second look, once a user the thinker was waiting for has gone quiet.
RESPOND_NOW_NOTE = ("The user has stopped talking. Respond now: act if the request can be "
                    "carried out, otherwise ask briefly for exactly what is missing.")


class Thinker:
    def __init__(self, model: Optional[str] = None, thinking: Optional[str] = None,
                 specs: Optional[List[Dict[str, Any]]] = None, instructions: Optional[str] = None) -> None:
        from google.genai import types
        from .gemini import resolved
        self.model = model or resolved("thinker", CONFIG.thinker_model)
        self.thinking = thinking or CONFIG.thinker_thinking
        decls = [types.FunctionDeclaration(name=s["name"], description=s["description"],
                                           parameters_json_schema=s["parameters"])
                 for s in (TOOL_SPECS if specs is None else specs)]
        listen = types.FunctionDeclaration(
            name=KEEP_LISTENING, description=KEEP_LISTENING_DESCRIPTION,
            parameters_json_schema={"type": "object", "properties": {
                "reason": {"type": "string", "description": "What the user has not finished saying."}},
                "required": ["reason"]})
        from .gemini import sampling, thinking_config
        extra: Dict[str, Any] = {}
        tc = thinking_config(self.model, self.thinking)
        if tc is not None:
            extra["thinking_config"] = tc

        def config(with_listen: bool):
            return types.GenerateContentConfig(
                system_instruction=instructions or THINKER_INSTRUCTIONS,
                tools=[types.Tool(function_declarations=decls + ([listen] if with_listen else []))],
                **sampling(self.model),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                **extra,
            )
        self.config = config(True)          # the first look at a turn may decide to keep listening
        self.config_final = config(False)   # after the user has gone quiet, it must respond
        # completed exchanges only; an interrupted turn never lands here
        self.history: List[Any] = []

    def user_content(self, text: str, audio: Optional[Tuple[bytes, str]] = None, note: str = ""):
        from google.genai import types
        parts = []
        if audio is not None:
            parts.append(types.Part.from_bytes(data=audio[0], mime_type=audio[1]))
            parts.append(types.Part(text=(
                "The audio above is the user's turn. The automatic transcript below may contain "
                "mis-heard words; where they disagree, trust what you hear.\nTranscript: " + text)))
        else:
            parts.append(types.Part(text=text))
        if note:
            parts.append(types.Part(text=note))
        return types.Content(role="user", parts=parts)

    async def _generate(self, contents: List[Any], config):
        return await client().aio.models.generate_content(model=self.model, contents=contents, config=config)

    async def run(self, text: str, toolbox, audio: Optional[Tuple[bytes, str]] = None,
                  note: str = "", allow_listen: bool = True) -> AsyncIterator[ThinkEvent]:
        from google.genai import types
        base = len(self.history)
        start_epoch = toolbox.coord.epoch
        contents = list(self.history) + [self.user_content(text, audio, note)]
        final_text = ""
        usage = {"input": 0, "input_audio": 0, "output": 0, "thinking": 0, "calls": 0}
        for step in range(1, CONFIG.max_tool_steps + 1):
            t = time.time()
            config = self.config if (allow_listen and step == 1) else self.config_final
            for attempt in (1, 2):
                try:
                    resp = await self._generate(contents, config)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("thinker call failed: %s", exc)
                    yield ThinkEvent("error", text=str(exc))
                    return
                for k, v in usage_of(resp).items():
                    usage[k] += v
                usage["calls"] += 1
                content = resp.candidates[0].content if resp.candidates else None
                parts = list(content.parts or []) if content is not None else []
                # Gemini 2.5 now and then returns an empty candidate, or stops on a
                # malformed function call; asking once more usually gets a clean one.
                if any(p.function_call or (getattr(p, "text", None) and not getattr(p, "thought", False))
                       for p in parts) or attempt == 2:
                    break
                log.warning("thinker step %d: empty reply (%s), asking again", step,
                            getattr(resp.candidates[0], "finish_reason", None) if resp.candidates else "no candidate")
            log.debug("thinker step %d in %.2f s", step, time.time() - t)
            every_call = [p.function_call for p in parts if p.function_call]
            words = " ".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)).strip()
            listens = [c for c in every_call if c.name == KEEP_LISTENING]
            calls = [c for c in every_call if c.name != KEEP_LISTENING]
            if listens and not calls and step == 1:
                # The user has not finished: nothing is acted on and nothing is
                # remembered; the caller waits and asks again once they go quiet.
                yield ThinkEvent("listen", text=str((listens[0].args or {}).get("reason", "")), usage=usage)
                return
            # The model's turn is kept exactly as returned: Gemini 3 attaches thought
            # signatures to function-call parts and rejects a follow-up request that
            # lost one. A keep_listening next to real work is answered, not deleted.
            if content is not None:
                contents.append(content)
            if step == 1:
                yield ThinkEvent("decided", tools=bool(calls))
            if not calls:
                final_text = words
                break
            responses = []
            for fc in every_call:
                if fc.name == KEEP_LISTENING:
                    responses.append(types.Part.from_function_response(
                        name=KEEP_LISTENING, response={"result": "not needed: the request is being acted on"}))
                    continue
                args = dict(fc.args or {})
                yield ThinkEvent("tool_start", name=fc.name, args=args)
                out = await toolbox.call(fc.name, args, epoch=start_epoch)
                if toolbox.coord.epoch != start_epoch:
                    # the user spoke again, or late words arrived: this plan is void,
                    # and asking the model to continue it would only waste a call
                    raise Superseded()
                parsed = json.loads(out)
                yield ThinkEvent("tool_done", name=fc.name, args=args,
                                 outcome=str(parsed.get("status", "ok")) if isinstance(parsed, dict) else "ok")
                responses.append(types.Part.from_function_response(name=fc.name, response={"result": parsed}))
            contents.append(types.Content(role="user", parts=responses))
        # A reply planned during a pause only counts once the turn really closed.
        await toolbox.coord.wait_committed(start_epoch)
        # The exchange completed: keep it, with a text-only copy of the user turn
        # (the audio has done its job and would be re-sent on every later call).
        self.history = list(self.history) + [self.user_content(text, None, note)] + contents[base + 1:]
        yield ThinkEvent("say", text=final_text, usage=usage)
