"""A stand-in for the Gemini API, for checking the live pipeline without a key.

    python tests/fake_gemini.py --port 8765 &
    GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:8765 GOOGLE_API_KEY=fake \\
        python bench/run_live.py --only <items> --no-judge --provider duet_fake

It is a plumbing test double, not a model, and it knows nothing about the
benchmark: its tool choices are deliberately naive, so the scores of such a run
mean nothing. What the run does show is that the model-driven path works end
to end with real audio and timing:

- the talker's line is spoken before the answer;
- the thinker's calls pass the commit gate and reach the mock backend;
- calls land in /tmp/agent_tool_calls.log, and the benchmark's scripts read them;
- keep_listening defers the reply;
- the first thinker request fails with a 503, so the retry path runs too;
- token usage reaches the traces and the cost report.

Script: a request without tools (the talker) gets "Sure, I'm on it." A thinker
request whose user text trails off ("...", ",", "and", "um") gets keep_listening
when that tool is offered. Otherwise the thinker calls the one offered tool whose
name shares a word with the user's text, with placeholder arguments, and after
the tool result it answers with one sentence.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TRAILING = re.compile(r"(\.\.\.|,|\b(and|um|uh|so)\b)\s*$", re.I)
_lock = threading.Lock()
_state = {"thinker_requests": 0, "fail_first": True}


def _text_of(content: dict) -> str:
    return " ".join(p.get("text", "") for p in content.get("parts", []) if "text" in p).strip()


def _placeholder(schema: dict) -> dict:
    args = {}
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        kind = props.get(name, {}).get("type", "string")
        args[name] = {"integer": 1, "number": 1.0, "boolean": True}.get(kind, "test")
    return args


def respond(body: dict) -> tuple[int, dict]:
    decls = [d for t in body.get("tools", []) for d in t.get("functionDeclarations", [])]
    contents = body.get("contents", [])
    last = contents[-1] if contents else {}
    usage = {"promptTokenCount": 900 + 40 * len(contents), "candidatesTokenCount": 20, "thoughtsTokenCount": 0,
             "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 900 + 40 * len(contents)}]}

    def reply(*parts):
        return 200, {"candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": "STOP"}],
                     "usageMetadata": usage}

    if not decls:  # the talker
        return reply({"text": "Sure, I'm on it."})
    with _lock:
        _state["thinker_requests"] += 1
        if _state["fail_first"]:
            _state["fail_first"] = False
            return 503, {"error": {"code": 503, "message": "fake overload", "status": "UNAVAILABLE"}}
    if any("functionResponse" in p for p in last.get("parts", [])):
        return reply({"text": "All done, I have taken care of that for you."})
    text = _text_of(last)
    user_words = set(re.findall(r"[a-z]+", text.lower()))
    if TRAILING.search(text) and any(d["name"] == "keep_listening" for d in decls):
        return reply({"functionCall": {"name": "keep_listening", "args": {"reason": "the sentence trails off"}}})
    for d in decls:
        if d["name"] != "keep_listening" and set(d["name"].split("_")) & user_words:
            # the SDK sends proto field names (snake_case); the API accepts both spellings
            schema = (d.get("parameters_json_schema") or d.get("parametersJsonSchema")
                      or d.get("parameters") or {})
            return reply({"functionCall": {"name": d["name"], "args": _placeholder(schema)}})
    return reply({"text": "Okay, could you tell me a bit more?"})


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        code, payload = respond(body)
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print("fake-gemini:", self.path.split("?")[0], args[1] if len(args) > 1 else "", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    port = ap.parse_args().port
    print("fake Gemini API on http://127.0.0.1:%d" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
