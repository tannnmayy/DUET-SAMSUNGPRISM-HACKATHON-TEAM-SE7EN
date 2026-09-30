# gemma4_final_run

Final submission run: all 100 recordings, Gemma 4 26B-A4B through Google's API

Source: `results/live/20260930_170052` (live run through the official FDB-v3 runner).

| Metric | Value |
|---|---|
| pass_at_1 | 0.81 |
| tool_selection_f1 | 0.915 |
| argument_acc | 0.85 |
| response_qual | 0.73 |
| turn_take_rate | 1.0 |
| interruption_rate | 0.0 |
| avg_response_latency_s | 4.8 |
| first_response_latency_mean_s | 4.8 |
| task_completion_latency_mean_s | 14.438 |

Settings: thinker `gemma-4-26b-a4b-it` (sampling {'temperature': 1.0, 'top_p': 0.95, 'top_k': 64, 'seed': 7}), fast voice: fixed lines, seed 7, endpointing 0.8-2.5 s, commit hold 1.1 s.

Full settings: `run_config.json`. Headline numbers: `summary.json`.
