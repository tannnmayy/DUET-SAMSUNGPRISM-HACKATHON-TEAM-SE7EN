# listening_dry_run

All 100 FDB-v3 recordings through the official runner and evaluation scripts, with the agent answering 'Okay.' to every closed turn and no language model: it measures listening alone (turn-taking, interruptions, first-response latency). Scoring ASR: faster-whisper on this Windows machine (NeMo/Parakeet is Linux-only). Tool metrics are zero by construction.

Source: `results/live/20260927_045953` (live run through the official FDB-v3 runner).

| Metric | Value |
|---|---|
| pass_at_1 | 0.0 |
| tool_selection_f1 | 0.0 |
| argument_acc | 0.0 |
| turn_take_rate | 1.0 |
| interruption_rate | 0.12 |
| avg_response_latency_s | 4.09 |
| first_response_latency_mean_s | 4.09 |
| task_completion_latency_mean_s | 4.24 |

Settings: thinker `gemini-3.7-flash` (thinking low, sampling {'temperature': 1.0, 'seed': 7}), talker `gemini-3.5-flash-lite`, seed 7, endpointing 0.8-2.5 s, commit hold 1.1 s.

Full settings: `run_config.json`. Headline numbers: `summary.json`.
