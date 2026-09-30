# Engineering notes

What we measured while building DUET for Full-Duplex-Bench v3, and the design decision
each measurement led to. Every number here comes from a run of the official pipeline or
of our offline tools, on the code in this repository.

- [1. How FDB-v3 actually scores](#1-how-fdb-v3-actually-scores)
- [2. What the ears hear](#2-what-the-ears-hear)
- [3. Listening with no model at all](#3-listening-with-no-model-at-all)
- [4. Turn-taking decisions](#4-turn-taking-decisions)
- [5. Making a hosted model dependable](#5-making-a-hosted-model-dependable)
- [6. Reproducible on someone else's machine](#6-reproducible-on-someone-elses-machine)
- [7. A critical review of the benchmark path](#7-a-critical-review-of-the-benchmark-path)
- [8. Where today's systems fall short](#8-where-todays-systems-fall-short)

## 1. How FDB-v3 actually scores

We read the benchmark's runner and evaluation scripts before writing any agent code.

- **Pass@1 fails on any extra tool call.** Precision must be 1.0: a call made with a
  stale, pre-correction value fails the item even if the corrected call follows. This is
  why DUET gates *every* tool call, read-only ones included.
- **Arguments are compared per tool.** Each expected call is matched with the first actual
  call of the same tool; with `--use-llm` a judge decides whether the values mean the same
  ("August 20" and "2026-08-20"), otherwise the match is exact after lower-casing.
- **The end of the user's turn** is the end of the last word before the first silence
  longer than 2 s, as heard by the scoring recognizer. Pauses shorter than that are inside
  the turn, and agent speech before that point counts as an **interruption**.
- **Every recording is the request followed by about 30 s of the speaker's real room
  noise** (the shortest tail is 29.7 s). The output recording is exactly as long as the
  input, so every tool call and every word of the answer must happen inside that window.
- **The recorder adds a near-constant ~1.9 s** to every system's measured latency: agent
  audio queued during the 2 s before streaming starts is written first. It affects every
  published baseline the same way.
- **Tool schemas matter.** Some expected calls omit an argument that the reference agents'
  schema makes required (an apartment search with no budget), or use a filter the reference
  schema lacks (`pets_allowed`). A model forced to fill a required field invents a value, so
  DUET keeps the benchmark's names but makes optional what a real API treats as optional.
- **A few items cannot be passed as scored.** In one the expected city is never spoken; in
  another the expected document number differs from the recording. Three conditional
  requests expect a branch that the benchmark's own mock data contradicts: *"if there is a
  flight under $300, book it; otherwise update my licence"* expects both actions, while the
  mock's only flight costs $450. DUET follows the data, as a real agent must; we do not
  special-case items.

## 2. What the ears hear

`bench/offline_asr.py` runs the agent's recognizer (faster-whisper large-v3-turbo with
Silero VAD) over all 100 inputs.

- **Every expected argument value appears in the transcript for 67 of 100 items.** Most
  misses are naming, not hearing: "British pounds" for GBP, "driver's license" for
  `driver_license`, "Vegas" for Las Vegas. The thinker resolves these.
- **About 8 are real mishearings**, mostly in spelled-out codes: a letter "I" heard as
  "bye", "1800" heard as "$800", "B-O-B-1-2" heard as "B0, B1, B2". These set the ceiling for
  any agent that listens through a recognizer.
- **No phantom input from the noise tails.** With VAD gating and the non-speech filter, zero
  segments were kept from the 30-second ambient tails.

## 3. Listening with no model at all

`bench/run_live.py --dry-run` runs the whole official pipeline with the agent answering
"Okay." to every closed turn. It measures the ears and the turn-taking alone.

| Metric (official scripts, all 100 recordings) | Result |
|---|---|
| Conversations answered (turn-take) | 100% |
| Interruption rate | 12.0% |
| First-response latency, as the benchmark measures it | 4.09 s (median 4.07, p90 4.77) |

Where the 4.1 s goes (medians):

| Stage | Seconds |
|---|---|
| VAD notices the user stopped | 0.71 |
| ASR of the final segment (laptop RTX 3060) | 0.62 |
| End-of-turn decision | 0.79 |
| TTS to first audio | 0.18 |
| Benchmark recorder offset (constant) | 1.87 |

This run is the floor for DUET's own response time, and it is where the turn-taking
defects in the next section were found. The interruptions are the end-of-turn model closing
a turn inside a pause; with the model, the thinker's `keep_listening` decides such turns
instead, so 12% is an upper bound. The complete record is in
[`results/reported/listening_dry_run`](../results/reported/listening_dry_run).

## 4. Turn-taking decisions

Each rule below exists because a run showed the failure it prevents.

| What we saw | Consequence | Decision |
|---|---|---|
| LiveKit drafts a reply during a pause; a draft that was then thrown away still marked the request as answered | The real turn found nothing to answer | A request is closed only when a reply that finished thinking is actually spoken (`DuetAgent.reply_delivered`) |
| The worker's default load is CPU usage, and the benchmark's scoring recognizer loads the CPU between recordings | The worker refused rooms | Load is counted in conversations |
| The end of turn fires after about 0.8 s of silence, including after "Like, you know." and "Could you track it for me?" (the order id came next) | A premature, failing call | The thinker decides whether the user has finished (`keep_listening`); once they have been quiet for 2.5 s it must act or ask |
| A third of all turns closed before their last words were transcribed | A plan made on the shorter text could act on a stale value | Words that arrive after a turn closed advance the epoch; every call carries its plan's epoch |
| The same late transcripts started model calls that the late words then voided | Wasted calls against the per-minute budget | Before the first call, the thinker waits up to 1.5 s for a transcript that is still due |
| A burst of room noise after a request advanced the epoch and voided a correct plan; the noise had no words, so no new turn followed | An unanswered request | Speech makes a call wait, but only new *words* void a plan; 2.5 s after wordless speech, the plan stands |
| The first room a worker joins starts WebRTC, which took up to 16 s on one machine, while the runner streams 2 s after joining | The first recording's request was half gone | A throwaway warm-up conversation before the first recording |
| With a hosted model the thinker needs seconds, and the fixed acknowledgement waited for its decision | Up to 8 s of silence after the user finished | "One moment." once the user has been quiet for 1.6 s. The benchmark ends a turn at 2 s of silence, so this cannot land inside it |
| The user barges in while a booking is executing | The cancelled reply could leave a performed action unrecorded; a re-plan could repeat it | Past the gate, a call is shielded, runs to completion and is recorded |

The effect on the live pipeline: on a three-recording pilot with the model, first response
4.38 s as the benchmark measures it (against the 4.09 s floor above), with no interruptions.

## 5. Making a hosted model dependable

Gemma 4 26B-A4B through Google's API answers a thinker call in 3.3 s at the median and
6.5 s at the 90th percentile (226 answers in one full run). Two properties of a hosted
model decide whole recordings, because a recording's room closes about 30 s after the
request:

- **Per-minute limits.** A project may send 16,000 input tokens per minute per model, and
  one thinker call is 2,300-2,600 tokens (the instructions and 13 tool definitions), so a
  single key allows about six calls a minute. Across a full run DUET used about 7,200 input
  tokens a minute on average, and a busy minute reached about 19,600. *Decisions:* a token
  budget per key that books every call before it is sent, several keys whose limits add up,
  and no calls spent on plans that are already void (no preemptive planning; the transcript
  wait above).
- **Transient errors and stalls.** Some requests fail with a 500 or 503 and come back
  within about 1.5 s; retries absorb them. A few simply stall with no answer. Waiting for a
  stalled request, then starting over, could use up a whole deadline. *Decision:* backup
  requests. A request unanswered after 6 s stays in flight and a second one goes to the key
  with the most room; the first answer wins. In a full run, 58 backups were sent, and in 29
  calls an answer arrived while backups were pending.

Every request is logged in `agent.log` (key, time, tokens, and every retry, backup and
cancellation), so a slow day at the provider can be told apart from a wrong answer.

## 6. Reproducible on someone else's machine

Only Samsung's re-run of `reproduce.sh` is scored, so the script must work on a machine we
have never seen.

- **Lock files for every package**, transitive ones included (`requirements.lock`,
  `requirements-bench.lock`), resolved for Linux x86_64. `reproduce.sh` uses a pinned `uv`
  to create Python 3.11 environments, so neither a system Python nor `python3-venv` is
  needed.
- **One torch for both environments** (2.10, CUDA 12.8 build), which runs on CUDA 12.x and
  13.x drivers. Left unpinned, the benchmark's NeMo dependency would pull a CUDA-13-only
  torch and lose the GPU on a CUDA 12 machine.
- **Pinned models:** the exact Hugging Face revisions of Whisper and Kokoro, verified to load
  offline from the cache, and the spaCy model Kokoro's phonemizer fetches on first use.
- **Pinned benchmark:** the FDB-v3 commit, the data archive's SHA-256, the LiveKit server
  version and its checksum.
- **A fresh clone on a clean Linux system** (WSL2 Ubuntu) ran `reproduce.sh` end to end:
  installation, preflight, the official runner with the Parakeet scoring recognizer, and the
  three evaluations.

## 7. A critical review of the benchmark path

We read every file the benchmark exercises and assumed each could be wrong.

**Checked and sound:**

- **The scorer's rules** match our reading: the exact multiset of tool names, then each
  expected call against the first actual call of the same tool. No item expects an
  identical call twice, so the idempotency ledger can never block a required call.
- **The agent joins in time.** Across a full run the agent was listening 1.7 s before
  streaming began; no request audio was missed.
- **Spoken facts survive the voice.** Typical answers went through Kokoro and back through a
  recognizer with every id, amount, name and date intact ("XK42Q8", "$1,850").
- **Late transcripts never strand a plan.** In all 145 cases across two full runs, a new
  closed turn (121) or new speech (24) followed.
- **The mock backend is stateless**, so one instance shared across conversations caches
  nothing, and our tool latencies use the same `instant` profile as the reference agents.

**Found and fixed:**

| Finding | Risk | Fix |
|---|---|---|
| A model can be withdrawn from a key | Every turn an apology | A fallback model, and a preflight that checks every key and a real tool call before the run |
| A GPU whose libraries fail to load killed the worker at start | No run at all | Whisper and Kokoro fall back to the CPU, same models |
| Audio was resampled without a low-pass filter | Aliased input to Whisper | LiveKit's band-limited resampler, live and offline |
| The acknowledgement named values ("checking flights to Paris") | A stale value in the transcript the judge reads, if the user then corrects it | The fast voice never names a value |
| A tool name the model invents raised an exception | An apology instead of an answer | An error result the model can recover from |
| An interrupted tool chain kept calling the model | Wasted calls on a void plan | The chain stops at once |
| The benchmark's fixed `/tmp/agent_tool_calls.log` may belong to another user on a shared machine | Every tool call becomes an apology | Checked before the run; a logging failure is logged, not fatal |
| A pre-set data folder, half-finished installs, missing LiveKit credentials | A failed re-run | `reproduce.sh` reuses existing data, retries downloads, marks finished installs and validates credentials |
| Markdown or snake_case in an answer | Symbols read aloud | Stripped before speech; underscores spoken as spaces |

## 8. Where today's systems fall short

- **The FDB-v3 paper.** The best published system, GPT-Realtime, passes fewer than 59% of
  self-correction items. Gemini Live 3.1 gives no spoken reply in 22% of items, although in
  most of those it had already called its tools, and its pre-emptive calls "lock in the stale
  destination". The cascaded Whisper → GPT-4o pipeline scores 0.176 on self-corrections,
  because it finalises the uncorrected transcript.
- **Voice assistants on the road.** User reports of in-car assistants (2026) describe
  assistants that keep talking after the driver has already chosen on the touchscreen and
  misreport failures. Sources are in [USE_CASE_RESEARCH.md](USE_CASE_RESEARCH.md).
