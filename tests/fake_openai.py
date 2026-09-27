"""A stand-in for the local model server (vLLM's OpenAI-compatible API), for
checking the whole local-model pipeline on a machine that cannot run vLLM.

    python tests/fake_openai.py --port 8766 &
    DUET_LLM_BASE_URL=http://127.0.0.1:8766/v1 python bench/run_live.py --only <items>

It is a plumbing test double, not a model, and knows nothing about the benchmark;
its choices are deliberately naive, so the scores of such a run mean nothing.
Endpoints: GET /health, GET /v1/models, POST /v1/chat/completions (tools, tool
calls, tool results), with the same naive script as tests/fake_gemini.py:

- no tools offered (the talker, or the judge): a short line, or a JSON verdict
  when the prompt asks for one;
- the user's text trails off ("...", ",", "and", "um") and keep_listening is
  offered: keep_listening;
- otherwise: the one offered tool whose name shares a word with the user's
  text, with placeholder arguments; after a tool result, one closing sentence.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "qwen3-30b-a3b-instruct-2507"
TRAILING = re.compile(r"(\.\.\.|,|\b(and|um|uh|so)\b)\s*$", re.I)


def _placeholder(schema: dict) -> dict:
    props = schema.get("properties", {})
    return {n: {"integer": 1, "number": 1.0, "boolean": True}.get(props.get(n, {}).get("type"), "test")
            for n in schema.get("required", [])}


def completion(body: dict) -> dict:
    messages = body.get("messages", [])
    tools = [t["function"] for t in body.get("tools") or []]
    last = messages[-1] if messages else {}
    message = {"role": "assistant", "content": None}
    if not tools:
        prompt = " ".join(str(m.get("content", "")) for m in messages)
        message["content"] = ('{"correct": true, "explanation": "stand-in judge"}' if "JSON" in prompt
                              else "Sure, I'm on it.")
    elif last.get("role") == "tool":
        message["content"] = "All done, I have taken care of that for you."
    else:
        text = str(last.get("content", "")).split("\n\n")[0]
        words = set(re.findall(r"[a-z]+", text.lower()))
        call = None
        if TRAILING.search(text) and any(t["name"] == "keep_listening" for t in tools):
            call = ("keep_listening", {"reason": "the sentence trails off"})
        else:
            for t in tools:
                if t["name"] != "keep_listening" and set(t["name"].split("_")) & words:
                    call = (t["name"], _placeholder(t.get("parameters") or {}))
                    break
        if call:
            message["tool_calls"] = [{"id": "chatcmpl-tool-" + uuid.uuid4().hex[:8], "type": "function",
                                      "function": {"name": call[0], "arguments": json.dumps(call[1])}}]
        else:
            message["content"] = "Okay, could you tell me a bit more?"
    return {"id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", MODEL),
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 900 + 40 * len(messages), "completion_tokens": 20,
                      "total_tokens": 920 + 40 * len(messages)}}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send(200, {})
        elif self.path.startswith("/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path.startswith("/v1/chat/completions"):
            self._send(200, completion(body))
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        print("fake-llm:", self.path, args[1] if len(args) > 1 else "", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    port = ap.parse_args().port
    print("fake local model server on http://127.0.0.1:%d/v1" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
