"""One line per user turn of DUET's conversation traces: what was heard, which mind answered, what ran, and the
latencies from the end of the user's speech (first DUET audio, the action done, the answer ready).

    python -I trace_turns.py <traces dir> <from HH:MM> <to HH:MM>      (local time)
"""
import glob
import json
import os
import sys
import time

folder, t_from, t_to = sys.argv[1], sys.argv[2], sys.argv[3]
day = time.strftime("%Y-%m-%d")
lo = time.mktime(time.strptime(day + " " + t_from, "%Y-%m-%d %H:%M"))
hi = time.mktime(time.strptime(day + " " + t_to, "%Y-%m-%d %H:%M"))


def hm(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


out = []
for f in glob.glob(os.path.join(folder, "*.jsonl")):
    rows = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
    if not rows or not (lo <= rows[0].get("t", 0) <= hi) or not any(r.get("kind") == "heard" for r in rows):
        continue
    out.append((rows[0]["t"], os.path.basename(f), rows))

for t0, name, rows in sorted(out):
    mode = next((r.get("mode") for r in rows if r.get("kind") == "session_start"), "?")
    print("\n##### %s  %s  start %s  (%.0f s)" % (name, mode, hm(t0), rows[-1]["t"] - t0))
    last_quiet = None          # the user's speech ended here
    turn = None
    for r in rows:
        k, t = r.get("kind"), r.get("t", t0)
        if k == "user_state" and r.get("state") == "listening":
            last_quiet = t
        elif k == "agent_said" and turn is None and r.get("text", "").startswith("Hi"):
            print("  %6.1f  DUET greets: %s" % (t - t0, r["text"]))
        elif k == "user_turn":
            if turn:
                print(turn["line"]())
            end = last_quiet or t
            turn = {"heard": r.get("text", ""), "end": end, "at": t - t0, "route": "", "why": "", "voice": None,
                    "tools": [], "said": [], "answer": None, "model": "", "calls": None, "tokens": None}

            def line(turn=turn):
                v = "%.2f" % (turn["voice"] - turn["end"]) if turn["voice"] else "-"
                a = "%.2f" % (turn["answer"] - turn["end"]) if turn["answer"] else "-"
                tools = "; ".join("%s%s %s @%.2f" % (n, json.dumps(a_, ensure_ascii=True), o, d - turn["end"])
                                  for n, a_, o, d in turn["tools"])
                return ("  %6.1f  YOU: %s\n          route=%s (%s) model=%s calls=%s in_tokens=%s\n"
                        "          first audio +%s s | answer ready +%s s | tools: %s\n          DUET: %s"
                        % (turn["at"], turn["heard"], turn["route"], turn["why"], turn["model"], turn["calls"],
                           turn["tokens"], v, a, tools or "-", " / ".join(turn["said"])))
            turn["line"] = line
        elif turn is None:
            continue
        elif k == "route":
            turn["route"], turn["why"] = r.get("where", ""), r.get("why", "")
        elif k == "agent_state" and r.get("state") == "speaking" and turn["voice"] is None:
            turn["voice"] = t
        elif k == "tool_done" or k == "tool_failed":
            turn["tools"].append((r.get("name"), r.get("args"), r.get("outcome", k), t))
        elif k == "thinker_say":
            turn["answer"] = turn["answer"] or t
            turn["model"] = r.get("model", "")
            u = r.get("usage") or {}
            turn["calls"], turn["tokens"] = u.get("calls"), u.get("input")
        elif k == "agent_said":
            turn["said"].append(("%s%s" % (r.get("text", ""), " [cut off]" if r.get("interrupted") else "")).strip())
        elif k in ("tool_asked", "superseded", "tone", "thinker_error"):
            turn["said"].append("<%s: %s>" % (k, (r.get("why") or r.get("label") or r.get("text") or "")[:90]))
    if turn:
        print(turn["line"]())
