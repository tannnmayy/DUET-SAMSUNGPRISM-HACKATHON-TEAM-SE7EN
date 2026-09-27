#!/usr/bin/env python3
"""What a run cost in API calls, from the agent's own traces (the local model: $0).

    python bench/cost_report.py results/live/<run>

Every thinker call (including keep-listening looks) and every talker call
records its token counts in the trace. Calls cut short by a barge-in are not
counted, so this is a slight underestimate. Speech recognition, TTS, VAD and
end-of-turn detection run on the local GPU and cost nothing per call.

Prices: Gemini API paid tier, standard, USD per million tokens, as published
at https://ai.google.dev/gemini-api/docs/pricing (checked 27 Sep 2026).
Thinking tokens are billed as output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict

PRICES = {
    # audio input is priced separately where the page lists it; otherwise as text
    "gemini-3.5-flash": {"input": 1.50, "input_audio": 1.50, "output": 9.00},
    "gemini-3.5-flash-lite": {"input": 0.30, "input_audio": 0.30, "output": 2.50},
    "gemini-3.7-flash": {"input": 0.75, "input_audio": 0.75, "output": 3.75},   # to 31 Dec 2026
    "gemini-3.6-flash": {"input": 0.75, "input_audio": 0.75, "output": 3.75},   # to 31 Dec 2026
    "gemini-3.1-flash-lite": {"input": 0.25, "input_audio": 0.50, "output": 1.50},
    "gemini-2.5-flash": {"input": 0.30, "input_audio": 1.00, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "input_audio": 0.30, "output": 0.40},
    "gemini-2.5-pro": {"input": 1.25, "input_audio": 1.25, "output": 10.00},
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
        # an open-weights model on our own GPU: no API bill (only GPU time)
        return 0.0 if not name.startswith("gemini") else float("nan")
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
            "prices": "Gemini API paid tier, standard (ai.google.dev/gemini-api/docs/pricing, 27 Sep 2026)"}


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
