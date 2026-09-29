"""The local model: Qwen3-30B-A3B-Instruct-2507, served by vLLM on the same GPU.

DUET reaches it through the OpenAI-compatible chat API that vLLM exposes; vLLM's
`hermes` tool parser turns Qwen3's tool-call format into standard tool calls.
The thinker's loop is the same as with Gemini (thinker.py): every tool call
passes the coordinator, the first look at a turn may decide to keep listening,
and a plan the user's words have overtaken never acts. Only the wire format
differs, so the agent does not know which backend it is talking to.

    python -m duet_voice.llm_local     # preflight: is the server up, does tool calling work?
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .config import CONFIG
from .coordinator import Superseded
from .fdb_tools import TOOL_SPECS
from .prompts import TALKER_INSTRUCTIONS, THINKER_INSTRUCTIONS
from .thinker import KEEP_LISTENING, KEEP_LISTENING_DESCRIPTION, ThinkEvent

log = logging.getLogger("duet.local")

# One client per event loop: a connection pool must not be shared between the
# event loops of different conversations (LiveKit runs each job in its own thread).
_clients: Dict[int, Any] = {}
_clients_lock = threading.Lock()


def client():
    from openai import AsyncOpenAI
    key = id(asyncio.get_running_loop())
    with _clients_lock:
        if key not in _clients:
            _clients[key] = AsyncOpenAI(
                base_url=CONFIG.llm_base_url,
                api_key=os.environ.get("DUET_LLM_API_KEY", "local"),
                timeout=float(os.environ.get("DUET_LLM_TIMEOUT_S", "60")),
                max_retries=2)
        return _clients[key]


def sampling() -> Dict[str, Any]:
    """The Qwen3-2507 model card's recommendation (temperature 0.7, top-p 0.8,
    top-k 20, min-p 0), with a fixed seed; DUET_TEMPERATURE overrides."""
    temperature = float(CONFIG.temperature) if CONFIG.temperature != "" else 0.7
    return {"temperature": temperature, "top_p": 0.8, "seed": CONFIG.seed,
            "extra_body": {"top_k": 20, "min_p": 0.0}}


def _tool(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


TOOLS = [_tool(s["name"], s["description"], s["parameters"]) for s in TOOL_SPECS]
LISTEN_TOOL = _tool(KEEP_LISTENING, KEEP_LISTENING_DESCRIPTION, {
    "type": "object", "properties": {"reason": {"type": "string", "description": "What the user has not finished saying."}},
    "required": ["reason"]})

_THINK = re.compile(r"<think>.*?</think>", re.S)
_OK_TAIL = re.compile(r"(?:(?<=[.!?])|\n)\s*OK\.?\s*$")  # a second look's "OK" after the answer
_RAW_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def clean(text: str) -> str:
    """Model text without reasoning blocks (the Instruct model should not emit any)."""
    return _THINK.sub("", text or "").strip()


def salvage_calls(content: str) -> Tuple[List[Dict[str, str]], str]:
    """Qwen3 writes tool calls as <tool_call>{"name": ..., "arguments": {...}}</tool_call>.
    If the server's parser left one in the text (malformed JSON around it, for
    instance), recover it here rather than lose the call."""
    calls = []
    for i, m in enumerate(_RAW_CALL.finditer(content or "")):
        try:
            obj = json.loads(m.group(1))
            calls.append({"id": "call_salvaged_%d" % i, "name": str(obj["name"]),
                          "arguments": json.dumps(obj.get("arguments") or {})})
        except (ValueError, KeyError, TypeError):
            continue
    return calls, _RAW_CALL.sub("", content or "").strip()


class LocalThinker:
    """The thinker on the local model. Same interface and events as thinker.Thinker."""

    def __init__(self, model: Optional[str] = None, thinking: Optional[str] = None,
                 specs: Optional[List[Dict[str, Any]]] = None, instructions: Optional[str] = None,
                 review: Optional[str] = None) -> None:
        self.model = model or CONFIG.thinker_model
        self.thinking = "none (non-thinking model)"
        # the benchmark's twelve tools and instructions unless a use case brings its own
        self.tools = TOOLS if specs is None else [_tool(s["name"], s["description"], s["parameters"])
                                                  for s in specs]
        self.instructions = instructions or THINKER_INSTRUCTIONS
        # A second look before an answer is spoken (the phone app; off for the benchmark):
        # the model checks its draft against the tool results and calls what is missing.
        # Safe because the ledger never runs an action twice.
        self.review = review
        # completed exchanges only, as chat messages; an interrupted turn never lands here
        self.history: List[Dict[str, Any]] = []

    async def _create(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]):
        return await client().chat.completions.create(
            model=self.model, messages=messages, tools=tools, tool_choice="auto",
            max_tokens=1024, **sampling())

    async def run(self, text: str, toolbox, audio=None, note: str = "",
                  allow_listen: bool = True) -> AsyncIterator[ThinkEvent]:
        # (audio is ignored: this model reads text only)
        start_epoch = toolbox.coord.epoch
        user = {"role": "user", "content": text + ("\n\n" + note if note else "")}
        prefix = [{"role": "system", "content": self.instructions}] + list(self.history) + [user]
        messages = list(prefix)
        final_text = ""
        draft, review_at = "", None
        usage = {"input": 0, "input_audio": 0, "output": 0, "thinking": 0, "calls": 0}
        for step in range(1, CONFIG.max_tool_steps + 1):
            tools = self.tools + [LISTEN_TOOL] if (allow_listen and step == 1) else self.tools
            calls: List[Dict[str, str]] = []
            content = ""
            for attempt in (1, 2):
                try:
                    resp = await self._create(messages, tools)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("thinker call failed: %s", exc)
                    yield ThinkEvent("error", text=str(exc))
                    return
                u = getattr(resp, "usage", None)
                if u is not None:
                    usage["input"] += int(getattr(u, "prompt_tokens", 0) or 0)
                    usage["output"] += int(getattr(u, "completion_tokens", 0) or 0)
                usage["calls"] += 1
                msg = resp.choices[0].message if resp.choices else None
                calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}"}
                         for c in (getattr(msg, "tool_calls", None) or [])]
                content = clean(getattr(msg, "content", None) or "")
                if not calls and "<tool_call>" in content:
                    calls, content = salvage_calls(content)
                # an empty reply is asked once more, like a malformed one on Gemini
                if calls or content or attempt == 2:
                    break
                log.warning("thinker step %d: empty reply, asking again", step)
            listens = [c for c in calls if c["name"] == KEEP_LISTENING]
            real = [c for c in calls if c["name"] != KEEP_LISTENING]
            if listens and not real and step == 1:
                # The user has not finished: nothing is acted on and nothing is
                # remembered; the caller waits and asks again once they go quiet.
                try:
                    reason = json.loads(listens[0]["arguments"] or "{}").get("reason", "")
                except (ValueError, AttributeError):
                    reason = ""
                yield ThinkEvent("listen", text=str(reason), usage=usage)
                return
            assistant: Dict[str, Any] = {"role": "assistant", "content": content or None}
            if calls:
                assistant["tool_calls"] = [{"id": c["id"], "type": "function",
                                            "function": {"name": c["name"], "arguments": c["arguments"]}}
                                           for c in calls]
            messages.append(assistant)
            if step == 1:
                yield ThinkEvent("decided", tools=bool(real))
            if not real:
                if (self.review and review_at is None and step < CONFIG.max_tool_steps
                        and content and "<silent>" not in content):
                    draft, review_at = content, len(messages)
                    messages.append({"role": "user", "content": self.review})
                    yield ThinkEvent("review", text=draft)
                    continue
                if review_at is not None and len(messages) == review_at + 2:
                    # The second look found nothing missing: the draft stands (whatever else the
                    # model wrote), and the history is kept as if it had not been asked.
                    final_text = draft
                    del messages[review_at:]
                else:
                    final_text = _OK_TAIL.sub("", content).strip() if review_at is not None else content
                break
            for c in calls:  # every call gets a response, in order
                if c["name"] == KEEP_LISTENING:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(
                        {"result": "not needed: the request is being acted on"})})
                    continue
                try:
                    args = json.loads(c["arguments"] or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments are not an object")
                except ValueError:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(
                        {"status": "error", "error": "the arguments were not a valid JSON object; call the tool again"})})
                    continue
                yield ThinkEvent("tool_start", name=c["name"], args=args)
                out = await toolbox.call(c["name"], args, epoch=start_epoch)
                if toolbox.coord.epoch != start_epoch:
                    raise Superseded()  # the user's words changed: this plan is void
                parsed = json.loads(out)
                yield ThinkEvent("tool_done", name=c["name"], args=args,
                                 outcome=str(parsed.get("status", "ok")) if isinstance(parsed, dict) else "ok")
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
        # A reply planned during a pause only counts once the turn really closed.
        await toolbox.coord.wait_committed(start_epoch)
        self.history = list(self.history) + [user] + messages[len(prefix):]
        yield ThinkEvent("say", text=final_text, usage=usage)


async def acknowledge(user_text: str, usage: Optional[dict] = None,
                      instructions: Optional[str] = None) -> Optional[str]:
    """The talker on the local model: one short sentence, or None (<silent>)."""
    resp = await client().chat.completions.create(
        model=CONFIG.talker_model, max_tokens=40,
        messages=[{"role": "system", "content": instructions or TALKER_INSTRUCTIONS},
                  {"role": "user", "content": "User: " + user_text}],
        **sampling())
    u = getattr(resp, "usage", None)
    if u is not None and usage is not None:
        usage.update(input=int(u.prompt_tokens or 0), output=int(u.completion_tokens or 0), thinking=0, calls=1)
    text = clean(resp.choices[0].message.content or "").strip().strip('"')
    return text or None


# --- preflight ------------------------------------------------------------------------------

def check() -> Dict[str, Any]:
    """Is the server up, does it serve our model, and does tool calling work?"""
    from openai import OpenAI
    out: Dict[str, Any] = {"backend": "local", "base_url": CONFIG.llm_base_url,
                           "thinker": CONFIG.thinker_model, "talker": CONFIG.talker_model, "notes": []}
    api = OpenAI(base_url=CONFIG.llm_base_url, api_key=os.environ.get("DUET_LLM_API_KEY", "local"),
                 timeout=120, max_retries=1)
    try:
        served = [m.id for m in api.models.list().data]
    except Exception as exc:
        out["error"] = "the model server at %s does not answer (%s)" % (CONFIG.llm_base_url, str(exc)[:160])
        return out
    out["served"] = served
    if CONFIG.thinker_model not in served:
        out["error"] = "the server serves %s, not %s" % (served, CONFIG.thinker_model)
        return out
    try:
        resp = api.chat.completions.create(
            model=CONFIG.thinker_model, tools=TOOLS, tool_choice="auto", max_tokens=200, **sampling(),
            messages=[{"role": "system", "content": THINKER_INSTRUCTIONS},
                      {"role": "user", "content": "Could you track my order? The order id is K-7-Q-2."}])
        calls = resp.choices[0].message.tool_calls or []
        got = [(c.function.name, c.function.arguments) for c in calls]
        out["tool_calling"] = "ok" if got and got[0][0] == "track_order" else "unexpected: %s" % (
            got or (resp.choices[0].message.content or "")[:160])
        if out["tool_calling"] != "ok":
            out["notes"].append("tool calling did not return track_order; check that the server runs with "
                                "--enable-auto-tool-choice --tool-call-parser hermes")
    except Exception as exc:
        out["error"] = "tool-calling test failed (%s)" % str(exc)[:200]
    return out


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"))
    result = check()
    print(json.dumps(result))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
