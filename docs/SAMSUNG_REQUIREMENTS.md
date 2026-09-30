# Theme 05 guide: where each requirement is met

Each line of Samsung's participant guide for Theme 05 (Interruptible Real-Time Agents),
mapped to where this submission meets it.

## The challenge: three capabilities

| Requirement | How DUET meets it | Evidence |
|---|---|---|
| **Stay responsive:** spoken feedback fast, no dead air | "One moment." as soon as the user has clearly finished and the thinker is still working; "Still working on it." on long chains; a spoken reply even when the model is unreachable (`duet_voice/agent.py`) | {{turn_take}} of conversations answered; first response {{first_response}} as the benchmark measures it ([RESULTS.md](RESULTS.md)) |
| **No false "done!" claims** | The fast voice never states a result or names a value; the thinker speaks only after the tool results, and only what they say (`duet_voice/prompts.py`) | Response quality {{resp_qual}} |
| **Work asynchronously** | Speech recognition, synthesis, model calls and tools all run off the audio loop; the backend runs in a worker thread | Architecture, section 2 |
| **Recover cleanly: discard stale intent** | Epochs: a plan made on words the user has since changed is never carried out; the commit gate waits for a closed turn and a quiet hold; `keep_listening` for unfinished sentences (`duet_voice/coordinator.py`) | 75 unit tests; self-correction items in [RESULTS.md](RESULTS.md) |
| **Update tool arguments after a change of mind** | The open request is re-read whole on every turn, and the thinker acts only on the final value | The preflight itself checks a correction ("K 7, no wait, K 4 Q 2" must give `K4Q2`) |
| **Never perform the same state-changing action twice** | Idempotency ledger; a committed call survives a barge-in and is recorded | `tests/test_voice_coordinator.py` |

## What to do

| Step | Where |
|---|---|
| Build a LiveKit voice agent (custom allowed, following the template patterns) | `duet_voice/agent.py`: the templates' tool names, arguments and `/tmp/agent_tool_calls.log` format |
| Clone FDB-v3 and LiveKit, download the data, run it | `reproduce.sh` and `bench/run_live.py`: all 100 recordings through the official runner, on a local LiveKit server or LiveKit Cloud |
| Iterate on self-corrections and multi-step chains | `bench/offline_eval.py` (the thinker alone on all 100 requests, officially scored), `bench/failures.py`, `bench/compare_runs.py`, [ENGINEERING_NOTES.md](ENGINEERING_NOTES.md) |
| One extension use case, end to end, in the video | DUET for Galaxy: `app/`, `duet_voice/galaxy/`, [app/README.md](../app/README.md); recorded demo in `results/galaxy/video/` |

## What to submit

| Deliverable | Where |
|---|---|
| README: architecture with a diagram, exact setup and run steps, the extension clearly marked | [README.md](../README.md), [ARCHITECTURE.md](ARCHITECTURE.md) |
| One-command reproduction: install, configure, evaluate | `reproduce.sh`: environments from lock files (uv, Python 3.11); pinned benchmark, data, LiveKit and speech-model revisions; a preflight of every key and of tool calling before anything runs. Verified on a fresh Linux clone |
| Declaration of model provider / custom agent | README, "Models and providers": a custom LiveKit agent; Gemma 4 26B-A4B-it (open weights, Apache 2.0) through Google's API |
| Results and run logs (scores, seeds, configuration) from our best run | [`results/reported/{{run_name}}`](../results/reported/{{run_name}}): `run_config.json`, `summary.json`, the official reports, per-recording results, traces and logs; analysed in [RESULTS.md](RESULTS.md) |
| API keys documented, not included | README, "API keys": `GOOGLE_API_KEY` or `GOOGLE_API_KEYS`; optional OpenAI and LiveKit keys. `.env.local` is git-ignored |
| Demo video, 3-5 minutes | The team's video; the extension's live demo is recorded from real runs |
| Slides, at most 8 | The team's deck |

## Scoring and policy

| Point | How we handle it |
|---|---|
| Samsung re-runs our script on one 48 GB GPU or declared hosted APIs; only the re-run counts | The declared hosted API is Google's (Gemma 4); the GPU runs only the speech models. The preflight stops a run whose keys cannot reach Gemma, naming the fix, rather than scoring 100 failures. Several keys add up their limits, and every model call survives rate limits, transient errors and stalls |
| LLM judge enabled, single pinned judge | The official scripts run unmodified with `--use-llm`; the official gpt-4o judge is used whenever `OPENAI_API_KEY` is set |
| Ties break on strict pass rate | Pass@1 is the metric DUET is built around: every tool call is gated, so a stale value never becomes an extra, failing call |

## Dos and don'ts

| Rule | How we comply |
|---|---|
| Cite public checkpoints and hosted APIs | README, "Models and providers" and "References" |
| Pin seeds and versions | Every package, transitive ones included (`requirements*.lock`); the Hugging Face revisions of Whisper and Kokoro; the Gemma model name, its sampling and seed 7 on every call; the benchmark commit, data SHA-256 and LiveKit checksum; deterministic speech recognition; effective settings recorded in every run |
| Test the reproduction on a machine that is not ours | A fresh clone on a clean Linux system ran `reproduce.sh` end to end: installation, preflight, the official runner with the Parakeet scoring recognizer, and the three evaluations |
| Keep the extension honest | Marked in the README; the demo states which phone actions are real and which are simulated |
| Don't hardcode or memorise test items | `tests/test_voice_integrity.py` fails if any benchmark value appears in the agent's strings; nothing is trained or tuned on the benchmark |
| Don't call your own servers | The only remote services are Google's API and, with a Cloud project, LiveKit |
| Don't cache across scenarios | A fresh coordinator, toolbox and conversation for every room |
