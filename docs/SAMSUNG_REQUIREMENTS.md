# Theme 05 guide: where each requirement is met

Each line of Samsung's updated participant guide, mapped to where the submission
meets it. Status: ✅ done · 🟡 in progress · ⬜ not started.

## The challenge: three capabilities

| Requirement | Where | Status |
|---|---|---|
| Stay responsive: spoken feedback fast, no dead air | Talker acknowledgement while the thinker works; progress line on slow tools; spoken fallback if the thinker fails. `duet_voice/agent.py`, `duet_voice/talker.py` | 🟡 designed and tested with fakes; latency tuning pending model runs |
| No false "done!" claims | The talker never states results; the thinker speaks only after tool results. `duet_voice/prompts.py` | 🟡 pending model runs |
| Work asynchronously | ASR, TTS and tools run off the audio loop (`asyncio.to_thread`); tools run while the acknowledgement plays | ✅ |
| Recover cleanly: discard stale intent | Epochs; commit gate waits for a closed turn plus a quiet hold; `keep_listening`. `duet_voice/coordinator.py` | ✅ unit-tested · 🟡 benchmark proof pending |
| Update tool arguments after a change of mind | The open request is re-read whole, and the thinker uses the final value. `DuetAgent._open_request`, `prompts.py` | 🟡 pending model runs |
| Never perform the same state-changing action twice | Idempotency ledger; committed calls survive a barge-in. `coordinator.py`, `tests/test_voice_coordinator.py` | ✅ |

## What to do

| Step | Where | Status |
|---|---|---|
| Build a LiveKit voice agent (custom allowed, following the template patterns) | `duet_voice/agent.py`: same tool names, arguments and `/tmp/agent_tool_calls.log` format as the templates | ✅ |
| Clone FDB-v3, LiveKit, download data, run it | `reproduce.sh`, `bench/run_live.py`. Local LiveKit server verified end to end on 100 items. LiveKit Cloud supported via `LIVEKIT_*` | ✅ local · ⬜ LiveKit Cloud run |
| Iterate on self-corrections and multi-step chains | `bench/offline_eval.py`, `docs/FINDINGS.md` | 🟡 listening done; model iteration needs the Gemini key |
| One extension use case, end to end, in the video | `docs/USE_CASE_RESEARCH.md` | ⬜ deferred by decision |

## What to submit

| Deliverable | Where | Status |
|---|---|---|
| README: architecture (one diagram), exact setup and run steps, extension marked | `README.md` | 🟡 results and extension sections pending |
| One-command reproduction (install, configure, evaluate) | `reproduce.sh` | 🟡 written; clean-Linux test pending |
| Declaration of model provider / custom agent | `README.md`, section "Models and providers" | ✅ |
| Results and run logs (scores, seeds, configuration) from our best run | `results/live/<run>/` (`run_config.json`, `summary.json`, official reports, per-item JSON, traces, logs) | 🟡 pipeline ready, best run pending |
| API keys documented, not included | `README.md`, section "API keys" | ✅ |
| Demo video, 3-5 min | team | ⬜ |
| Slides, at most 8 | team | ⬜ |

## Scoring and policy

| Point | How we handle it | Status |
|---|---|---|
| Samsung re-runs our script on one 48 GB GPU or declared hosted APIs; only the re-run counts | Local models need about 4 GB; hosted: Gemini API only | 🟡 clean-machine test pending |
| LLM judge enabled, single pinned judge | The official scripts run unmodified with `--use-llm`; our own numbers use gpt-4o when `OPENAI_API_KEY` is set, and a proxy judge is labelled as such | ✅ |
| Ties break on strict pass rate | Pass@1 is the metric we optimise first | ✅ |

## Dos and don'ts

| Rule | How we comply | Status |
|---|---|---|
| Cite public checkpoints and hosted APIs | `README.md`, sections "Models and providers" and "References" | ✅ |
| Pin seeds and versions | `requirements*.txt`; benchmark commit, data SHA-256 and LiveKit checksum in `reproduce.sh`; temperature 0 and seed 7; greedy ASR | ✅ |
| Test the reproduction on a machine that is not ours | Google Cloud GPU VM | ⬜ |
| Keep the extension honest | The extension will be marked and scoped | ⬜ |
| Don't hardcode or memorise test items | `tests/test_voice_integrity.py` fails on any benchmark value in the agent's strings | ✅ |
| Don't call your own servers | Only the Gemini API and LiveKit | ✅ |
| Don't cache across scenarios | Per-room coordinator, toolbox and thinker state; only model weights are shared | ✅ |
