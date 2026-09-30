#!/usr/bin/env python3
"""What a run used in model calls and tokens, from the agent's own traces.

    python bench/cost_report.py results/live/<run>

Every thinker call (including keep-listening looks) and every talker call
records its token counts in the trace. Calls cut short by a barge-in are not
counted, so this is a slight underestimate. Speech recognition, TTS, VAD and
end-of-turn detection run on the local GPU and cost nothing per call.

Gemma 4 through Google's API is free of charge (free-tier projects; see
https://ai.google.dev/gemini-api/docs/pricing), so the bill is $0 and what matters
is the token count against the per-minute limit. Prices below stay at 0; a model
without a listed price is reported as free (an open model on our own hardware).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict

PRICES = {
    # USD per million tokens (0: free of charge on Google's API)
    "gemma-4-26b-a4b-it": {"input": 0.0, "input_audio": 0.0, "output": 0.0},
    "gemma-4-31b-it": {"input": 0.0, "input_audio": 0.0, "output": 0.0},
}
KEYS = ("input", "input_audio", "output", "thinking", "calls")


def tokens_by_model(run_dir: Path) -> Dict[str, Dict[str, int]]:
    """Token totals per model and role, summed over every conversation's trace."""
    out: Dict[str, Dict[str, int]] = {}
    for path in sorted((run_dir / "traces").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            role = {"thinker_say": "thinker", "thinker_listen": "thinker", "talker_usage": "talker"}.get(ev.get("kind"))
            if role is None or not ev.get("usage"):
                continue
            key = "%s (%s)" % (ev.get("model", "?"), role)
            acc = out.setdefault(key, dict.fromkeys(KEYS, 0))
            for k in KEYS:
                acc[k] += int(ev["usage"].get(k, 0) or 0)
    return out


def dollars(model: str, t: Dict[str, int]) -> float:
    name = model.split(" ")[0]
    p = PRICES.get(name)
    if p is None:
        # an open-weights model on our own hardware: no API bill
        return 0.0
    text_in = max(0, t["input"] - t["input_audio"])
    return (text_in * p["input"] + t["input_audio"] * p["input_audio"]
            + (t["output"] + t["thinking"]) * p["output"]) / 1e6


def report(run_dir: Path) -> dict:
    conversations = len(list((run_dir / "traces").glob("*.jsonl")))
    rows = {}
    total = 0.0
    for key, t in tokens_by_model(run_dir).items():
        usd = dollars(key, t)
        total += usd
        rows[key] = {**t, "usd": round(usd, 4)}
    return {"conversations": conversations, "by_model": rows, "total_usd": round(total, 4),
            "usd_per_conversation": round(total / conversations, 5) if conversations else None,
            "usd_per_1000_conversations": round(1000 * total / conversations, 2) if conversations else None,
            "prices": "Gemma 4 on Google's API: free of charge (ai.google.dev/gemini-api/docs/pricing)"}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    r = report(Path(sys.argv[1]))
    print("%-36s %8s %8s %8s %8s %6s %9s" % ("model (role)", "input", "of audio", "output", "thinking", "calls", "USD"))
    for key, t in r["by_model"].items():
        print("%-36s %8d %8d %8d %8d %6d %9.4f" % (key, t["input"], t["input_audio"], t["output"],
                                                   t["thinking"], t["calls"], t["usd"]))
    print("total $%.4f over %d conversations: $%s per conversation, $%s per 1,000"
          % (r["total_usd"], r["conversations"], r["usd_per_conversation"], r["usd_per_1000_conversations"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
