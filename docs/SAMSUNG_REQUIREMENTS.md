# Theme 05 guide: where each requirement is met

Each line of Samsung's updated participant guide, mapped to where the submission
meets it. Status: ✅ done · 🟡 in progress · ⬜ not started.

## The challenge: three capabilities

| Requirement | Where | Status |
|---|---|---|
| Stay responsive: spoken feedback fast, no dead air | "One moment." as soon as the user has clearly finished or the thinker decides there is work, progress lines while it works, a spoken fallback if it fails. `duet_voice/agent.py` | ✅ 4.38 s first response on the live pilot, as the benchmark measures it (its floor with no model is 4.09 s) · 🟡 all 100 from the full run |
| No false "done!" claims | The fast voice never states a result; the thinker speaks only after tool results. `duet_voice/prompts.py` | ✅ |
| Work asynchronously | ASR, TTS, model calls and tools run off the audio loop | ✅ |
| Recover cleanly: discard stale intent | Epochs (new speech, or words arriving after a turn closed), every call bound to its plan's epoch; commit gate waits for a closed turn plus a quiet hold; `keep_listening`. `duet_voice/coordinator.py` | ✅ unit-tested and seen live |
| Update tool arguments after a change of mind | The open request is re-read whole, and the thinker uses the final value. `DuetAgent._open_request`, `prompts.py` | ✅ Gemma 4's preflight test is a correction ("K 7, no wait, K 4 Q 2" gives `K4Q2`) · 🟡 self-correction Pass@1 over all 100 comes from the full run |
| Never perform the same state-changing action twice | Idempotency ledger; committed calls survive a barge-in. `coordinator.py`, `tests/test_voice_coordinator.py` | ✅ |

## What to do

| Step | Where | Status |
|---|---|---|
| Build a LiveKit voice agent (custom allowed, following the template patterns) | `duet_voice/agent.py`: same tool names, arguments and `/tmp/agent_tool_calls.log` format as the templates | ✅ |
| Clone FDB-v3, LiveKit, download data, run it | `reproduce.sh`, `bench/run_live.py`. Local LiveKit server verified end to end on all 100 items (`results/reported/listening_dry_run`); LiveKit Cloud (`LIVEKIT_*`) verified | ✅ |
| Iterate on self-corrections and multi-step chains | `bench/offline_eval.py` (the thinker alone on all 100, officially scored), `bench/failures.py`, `bench/compare_runs.py`, `docs/FINDINGS.md` | ✅ Gemma 4 on the exact scripts: 71/100 by exact match; most misses are formats the judge accepts (`docs/FINDINGS.md`) |
| One extension use case, end to end, in the video | DUET for Galaxy (`app/`, `duet_voice/galaxy/`, `app/README.md`); recorded film `results/galaxy/video/` | ✅ |

## What to submit

| Deliverable | Where | Status |
|---|---|---|
| README: architecture (one diagram), exact setup and run steps, extension marked | `README.md` | ✅ · results table filled from the full run |
| One-command reproduction (install, configure, evaluate) | `reproduce.sh`: a Google API key for Gemma 4; two environments from lock files (uv, Python 3.11); pinned benchmark, data, LiveKit and speech-model revisions; a preflight of every key and of tool calling before anything runs | ✅ fresh Linux clone (WSL2 Ubuntu): install, preflight, runner, scoring and evaluations end to end |
| Declaration of model provider / custom agent | `README.md`, "Models and providers": custom LiveKit agent; Gemma 4 26B-A4B-it (open weights, Apache 2.0) through Google's API | ✅ |
| Results and run logs (scores, seeds, configuration) from our best run | `results/live/<run>/`, published to `results/reported/<run>/`: `run_config.json`, `summary.json`, official reports, per-item JSON, traces, logs | 🟡 full run next |
| API keys documented, not included | `README.md`, "API keys": `GOOGLE_API_KEY` (a free-tier project) or `GOOGLE_API_KEYS`; optional OpenAI and LiveKit keys. `.env.local` is git-ignored | ✅ |
| Demo video, 3-5 min | team (the extension's film is recorded) | 🟡 |
| Slides, at most 8 | team | ⬜ |

## Scoring and policy

| Point | How we handle it | Status |
|---|---|---|
| Samsung re-runs our script on one 48 GB GPU or declared hosted APIs; only the re-run counts | The declared hosted API is Google's (Gemma 4); the GPU runs speech only (about 8 GiB). The preflight stops a run whose keys cannot use Gemma, with the fix (a key from a free-tier project), instead of scoring 100 failures. `GOOGLE_API_KEYS` adds up the limits of several projects | ✅ · 🟡 full run next |
| LLM judge enabled, single pinned judge | The official scripts run unmodified with `--use-llm`; Samsung's gpt-4o is used whenever a working OpenAI key is present. Our own numbers otherwise use Gemma 4 26B-A4B as a proxy judge, labelled as such; `--rescore` re-judges saved results | ✅ |
| Ties break on strict pass rate | Pass@1 is the metric we optimise first | ✅ |

## Dos and don'ts

| Rule | How we comply | Status |
|---|---|---|
| Cite public checkpoints and hosted APIs | `README.md`, "Models and providers" and "References" | ✅ |
| Pin seeds and versions | Every package, transitive included (`requirements*.lock`); Hugging Face revisions of Whisper and Kokoro; the Gemma model name, its sampling and seed 7 on every call; benchmark commit, data SHA-256 and LiveKit checksum in `reproduce.sh`; greedy ASR; effective settings recorded per run | ✅ |
| Test the reproduction on a machine that is not ours | A fresh Linux clone (WSL2 Ubuntu 26.04, RTX 3060) ran `reproduce.sh` from scratch: install, preflight, runner, Parakeet scoring and the three evaluations. It exposed a 16 s start-up on the agent's first room, now paid by a warm-up conversation. Earlier, a shared DGX A100 at SRM ran the install and a live recording end to end (28 Sep) | ✅ |
| Keep the extension honest | Marked in the README; the film states that phone actions are simulated in the web build | ✅ |
| Don't hardcode or memorise test items | `tests/test_voice_integrity.py` fails on any benchmark value in the agent's strings; no training or tuning on the benchmark | ✅ |
| Don't call your own servers | The only remote services are Google's API (the model) and, with a Cloud project, LiveKit | ✅ |
| Don't cache across scenarios | Per-room coordinator, toolbox and thinker state | ✅ |
