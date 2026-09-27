"""The cost report prices every traced model call at the published rates."""

import json

from bench import cost_report


def test_cost_is_priced_per_model_with_audio_and_thinking(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    rows = [
        {"kind": "thinker_listen", "model": "gemini-2.5-flash",
         "usage": {"input": 1000, "input_audio": 0, "output": 10, "thinking": 100, "calls": 1}},
        {"kind": "thinker_say", "model": "gemini-2.5-flash",
         "usage": {"input": 3000, "input_audio": 400, "output": 60, "thinking": 300, "calls": 3}},
        {"kind": "talker_usage", "model": "gemini-2.5-flash-lite",
         "usage": {"input": 250, "output": 12, "thinking": 0, "calls": 1}},
        {"kind": "tool_done", "name": "track_order"},
    ]
    (traces / "eval-1.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    r = cost_report.report(tmp_path)
    flash = (3600 * 0.30 + 400 * 1.00 + (70 + 400) * 2.50) / 1e6   # audio input and thinking priced
    lite = (250 * 0.10 + 12 * 0.40) / 1e6
    assert r["conversations"] == 1
    assert r["by_model"]["gemini-2.5-flash (thinker)"]["calls"] == 4
    assert abs(r["total_usd"] - round(flash + lite, 4)) < 1e-9
