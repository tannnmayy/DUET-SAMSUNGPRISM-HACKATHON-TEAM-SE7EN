# Theme 05 guide: where each requirement is met

Each line of Samsung's updated participant guide, mapped to where the submission
meets it. Status: ✅ done · 🟡 in progress · ⬜ not started.

## The challenge: three capabilities

| Requirement | Where | Status |
|---|---|---|
| Stay responsive: spoken feedback fast, no dead air | Talker acknowledgement while the thinker works; progress line on slow tools; spoken fallback if the thinker fails. `duet_voice/agent.py`, `duet_voice/talker.py` | 🟡 live with a scripted stand-in: acknowledgement 0.24 s after the turn closes. Real-model latency needs a billing-enabled key (free tier: 1-38 s per request) |
| No false "done!" claims | The talker never states results; the thinker speaks only after tool results. `duet_voice/prompts.py` | 🟡 pending model runs |
| Work asynchronously | ASR, TTS and tools run off the audio loop (`asyncio.to_thread`); tools run while the acknowledgement plays | ✅ |
| Recover cleanly: discard stale intent | Epochs (new speech, or words arriving after a turn closed), every call bound to its plan's epoch; commit gate waits for a closed turn plus a quiet hold; `keep_listening`. `duet_voice/coordinator.py` | ✅ unit-tested and seen live (stand-in run) · 🟡 benchmark proof pending |
| Update tool arguments after a change of mind | The open request is re-read whole, and the thinker uses the final value. `DuetAgent._open_request`, `prompts.py` | 🟡 first real-model check correct ("X-K-4-2-Q-7, no wait, …Q-8" gave one call with XK42Q8); full run needs billing |
| Never perform the same state-changing action twice | Idempotency ledger; committed calls survive a barge-in. `coordinator.py`, `tests/test_voice_coordinator.py` | ✅ |

## What to do

| Step | Where | Status |
|---|---|---|
| Build a LiveKit voice agent (custom allowed, following the template patterns) | `duet_voice/agent.py`: same tool names, arguments and `/tmp/agent_tool_calls.log` format as the templates | ✅ |
| Clone FDB-v3, LiveKit, download data, run it | `reproduce.sh`, `bench/run_live.py`. Local LiveKit server verified end to end on all 100 items with the final code (100% answered; `results/reported/listening_dry_run`); LiveKit Cloud (`LIVEKIT_*`) verified on 3 items | ✅ |
| Iterate on self-corrections and multi-step chains | `bench/offline_eval.py`, `docs/FINDINGS.md` | 🟡 listening done; first 20-item sample (12 of 13 completed items passed) before the free tier's 20-requests-a-day cap; full iteration needs billing |
| One extension use case, end to end, in the video | `docs/USE_CASE_RESEARCH.md` | ⬜ deferred by decision |

## What to submit

| Deliverable | Where | Status |
|---|---|---|
| README: architecture (one diagram), exact setup and run steps, extension marked | `README.md` | 🟡 results and extension sections pending |
| One-command reproduction (install, configure, evaluate) | `reproduce.sh`: lock-file installs, Python 3.10-3.12 auto-picked (uv fallback), pinned benchmark, data and LiveKit. Both environments resolve for Linux on Python 3.10-3.12 (`docs/FINDINGS.md` §5) | 🟡 resolved for Linux; clean-machine run pending (`docs/CLEAN_MACHINE_TEST.md`) |
| Declaration of model provider / custom agent | `README.md`, section "Models and providers": custom LiveKit agent; Gemini `gemini-3.7-flash` (thinker) and `gemini-3.5-flash-lite` (talker), which a new key can reach (2.5 Flash-Lite and 2.5 Pro are closed to new users) | ✅ |
| Results and run logs (scores, seeds, configuration) from our best run | `results/live/<run>/` (`run_config.json` with the effective models, sampling and seed; `summary.json` with scores, agent statistics and cost; official reports, per-item JSON, traces, logs). The reported run is copied to a committed folder | 🟡 pipeline ready, best run pending billing |
| API keys documented, not included | `README.md`, section "API keys" | ✅ |
| Demo video, 3-5 min | team | ⬜ |
| Slides, at most 8 | team | ⬜ |

## Scoring and policy

| Point | How we handle it | Status |
|---|---|---|
| Samsung re-runs our script on one 48 GB GPU or declared hosted APIs; only the re-run counts | Local models need about 4 GB; hosted: Gemini API only, with models a new key can reach. Both environments use the CUDA 12.8 torch build, which runs on CUDA 12.x and 13.x drivers. Gemini calls retry rate limits and server errors | 🟡 clean-machine test pending |
| LLM judge enabled, single pinned judge | The official scripts run unmodified with `--use-llm`. Our numbers use gpt-4o when a probe call succeeds; otherwise a Gemini proxy judge, labelled as such. `--rescore` re-judges saved results later | ✅ · 🟡 our OpenAI account needs credits |
| Ties break on strict pass rate | Pass@1 is the metric we optimise first | ✅ |

## Dos and don'ts

| Rule | How we comply | Status |
|---|---|---|
| Cite public checkpoints and hosted APIs | `README.md`, sections "Models and providers" and "References" | ✅ |
| Pin seeds and versions | Every package, transitive included (`requirements*.lock`); Hugging Face snapshots of Whisper and Kokoro; benchmark commit, data SHA-256 and LiveKit checksum in `reproduce.sh`; seed 7 on every model call (temperature at the Gemini 3 default, as Google advises); greedy ASR; effective settings recorded per run | ✅ |
| Test the reproduction on a machine that is not ours | Google Cloud GPU VM, procedure in `docs/CLEAN_MACHINE_TEST.md` | ⬜ needs the team's Google Cloud account |
| Keep the extension honest | The extension will be marked and scoped | ⬜ |
| Don't hardcode or memorise test items | `tests/test_voice_integrity.py` fails on any benchmark value in the agent's strings | ✅ |
| Don't call your own servers | Only the Gemini API and LiveKit | ✅ |
| Don't cache across scenarios | Per-room coordinator, toolbox and thinker state; only model weights are shared | ✅ |
