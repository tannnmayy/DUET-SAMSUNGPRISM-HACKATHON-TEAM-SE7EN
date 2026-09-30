"""The usage report counts every traced model call; Gemma through Google's API is free."""

import json

from bench import cost_report


def test_calls_and_tokens_are_counted_per_model_and_role(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    rows = [
        {"kind": "thinker_listen", "model": "gemma-4-26b-a4b-it",
         "usage": {"input": 2300, "input_audio": 0, "output": 10, "thinking": 200, "calls": 1}},
        {"kind": "thinker_say", "model": "gemma-4-26b-a4b-it",
         "usage": {"input": 5200, "input_audio": 0, "output": 60, "thinking": 300, "calls": 2}},
        {"kind": "tool_done", "name": "track_order"},
    ]
    (traces / "eval-1.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    r = cost_report.report(tmp_path)
    t = r["by_model"]["gemma-4-26b-a4b-it (thinker)"]
    assert r["conversations"] == 1 and t["calls"] == 3 and t["input"] == 7500 and t["thinking"] == 500
    assert r["total_usd"] == 0.0
