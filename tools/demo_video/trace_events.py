"""The timed events of one DUET trace, relative to its first event, for mapping onto a screen recording.

    python -I trace_events.py <trace.jsonl>
"""
import json
import sys

rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
t0 = rows[0]["t"]
for r in rows:
    k = r.get("kind")
    if k in ("agent_state", "user_state"):
        s = r.get("state")
    elif k in ("tool_start", "tool_done", "tool_failed"):
        s = "%s %s %s" % (r.get("name"), json.dumps(r.get("args"), ensure_ascii=True), r.get("outcome", ""))
    elif k in ("heard", "agent_said", "talker", "confirmed", "thinker_say", "user_turn"):
        s = (r.get("text") or "")[:90]
    elif k == "route":
        s = "%s: %s" % (r.get("where"), r.get("why"))
    else:
        continue
    print("%7.2f %-12s %s" % (r["t"] - t0, k, s.encode("ascii", "replace").decode()))
