# Engineering findings (FDB-v3)

What we measured, what broke, and what we changed because of it. Every number
here comes from a run whose folder is named in the text.

## 1. How FDB-v3 actually scores (read from the benchmark code)

- **Pass@1 fails on any extra tool call.** Precision must be 1.0, so a call made
  with a stale, pre-correction value fails the item even if the corrected call
  follows. This is why DUET's coordinator gates *every* tool call, read-only ones
  included, not just state-changing ones.
- **The "end of the user's turn"** is the end of the last word before the first
  silence longer than 2 s, as heard by Parakeet on the input. Pauses shorter than
  that are, by the benchmark's definition, inside the turn. Any agent speech
  before that point counts as an **interruption**.
- **Every recording is the request followed by about 30 s of the speaker's real
  room noise.** The shortest tail is 29.7 s, so an answer always has time to land.
  The same noise is where Whisper, run without voice-activity detection, invents
  sentences ("You want breakfast?"). An agent that acted on them would make an
  extra, failing call.
- **The benchmark's recorder adds a near-constant ~1.9 s to every system's
  measured latency.** Agent audio frames queued during the 2 s before streaming
  starts are written first. This affects every published baseline the same way,
  and we do not try to game it.
- **Tool schemas matter.** In several items the expected call omits an argument
  that the reference agents' schema makes required (an apartment search with no
  budget), or uses a common optional filter the reference schema lacks
  (`pets_allowed`). A model forced to fill a required field invents a value.
  DUET's schemas keep the benchmark's names but make optional what a real API
  would treat as optional (`duet_voice/fdb_tools.py`).
- **Two items cannot be passed by any agent.** In one the expected city is never
  spoken; in the other the expected document number differs from the recording.
  The attainable ceiling is below 100%.

## 2. The ears (offline, `bench/offline_asr.py`)

We ran faster-whisper large-v3-turbo with Silero VAD over all 100 inputs:

- **Every expected argument value appears in the transcript for 67 of 100 items.**
  - Most misses are naming, not hearing: "British pounds" for GBP, "driver's
    license" for `driver_license`, "Vegas" for Las Vegas. The thinker resolves
    these.
  - About 8 are real mishearings. Examples: "in euros" heard as "nearest", "I"
    heard as "bye" inside a spelled id, "1800" heard as "$800".
  - This is why the thinker can optionally hear the turn's audio as well
    (`DUET_THINKER_AUDIO=1`).
- **No phantom input from the noise tails.** With VAD gating, zero segments were
  kept from the ambient tails.

## 3. Listening, with no model at all (dry run, `results/live/20260926_234008`)

`bench/run_live.py --dry-run` runs the whole official pipeline with the agent
answering "Okay." to every closed turn. It measures our ears and turn-taking
alone.

| Metric (official scripts) | Dry run |
|---|---|
| Turn-take rate | 81% |
| Interruption rate | 11.1% |
| First-response latency (benchmark-measured) | 4.14 s (median 4.12) |

**Where the 4.1 s goes** (medians, from our traces aligned to the benchmark
clock):

| Stage | Seconds |
|---|---|
| VAD notices the user stopped (after the real last word) | 0.71 |
| ASR of the final segment (laptop RTX 3060; faster on the target GPU) | 0.62 |
| End-of-turn decision | 0.79 |
| TTS to first audio | 0.18 |
| Benchmark recorder offset (constant, not ours) | 1.87 |

**Three defects this run exposed, and the fixes:**

1. **17 silent conversations.** LiveKit drafts a reply *preemptively* during a
   pause. A draft that was later discarded still marked the request as answered,
   so the real turn found nothing to answer. *Fix:* a request is closed only when
   a reply that finished thinking actually reaches the conversation
   (`DuetAgent.reply_delivered`). A test covers the discarded-draft case.
2. **2 conversations never joined.** LiveKit's default worker load is CPU usage,
   with new rooms refused above 70%. Between items the benchmark's scoring ASR
   loads the CPU, so the worker refused rooms. *Fix:* load is counted in
   conversations.
3. **The end of turn fires after about 0.8 s of silence**, including after
   "Like, you know." and "Could you track it for me?" (the order id came next).
   That is where the 11% interruptions came from, and where a model would make a
   premature, failing call. *Fix:* the thinker decides whether the user has
   finished. It can call `keep_listening`, in which case nothing is done or said.
   Once the user has been quiet for 2.5 s, it looks again with that option
   removed, and acts or asks for exactly what is missing. The talker covers a
   slow thinker only after 1.6 s of quiet.

**Recheck** (`results/live/20260927_014233`): the 19 conversations that failed
(17 silent, 2 never joined) were re-run with fixes 1 and 2. All 19 replied
(turn-take 100%), with no interruptions and 3.95 s benchmark-measured latency.
Fix 3 acts only when the thinker runs, so it is measured in the model runs.

**Does the noise tail trigger false "user is speaking" events?** It matters: DUET
treats new speech as a possible correction and discards the plan in flight. A
noise burst with no words would then leave the request unanswered. Across all
117 conversations of the two runs above, LiveKit's VAD fired after the user's
last transcribed words exactly once: a 0.55 s burst, 23.7 s later, long after
the reply. In a 1-6 s thinking window that is a risk of about 0.1% per
conversation, so we did not add machinery for it.

## 4. Correctness fixes found by reading the code

- **A call interrupted mid-flight.** If the user barges in while a tool call is
  executing, cancelling the reply could leave a call that the backend already
  performed unrecorded. A re-plan could then perform it twice. *Fix:* past the
  gate a call is shielded, runs to completion, and is recorded and logged (test
  added).
- **Re-run determinism.** Whisper's temperature fallback samples, and sampled
  decoding makes re-runs differ. It is now greedy/beam only. All Gemini calls use
  temperature 0 and a fixed seed (`DUET_SEED=7`).

## 5. Will Samsung's re-run install and run? (resolved for Linux from Windows)

Only Samsung's re-run of `reproduce.sh` is scored, and a script that does not run
scores zero on 60% of the grade. We have no Linux GPU machine of our own yet, so
we resolved both environments for Linux x86_64 with `pip --platform` and
`uv pip compile --universal`, for Python 3.10, 3.11 and 3.12. This found two
defects that would have broken the re-run:

1. **The benchmark runner would have lost the GPU on a CUDA 12 machine.** Its
   environment left torch unpinned, and NVIDIA NeMo accepts any torch >= 2.6. A
   clean install today pulls torch 2.14, which is built for CUDA 13 and cannot use
   a CUDA 12.x driver. The guide allows either. *Fix:* the runner uses the
   agent's torch 2.10 (CUDA 12.8 build, cuDNN 9.10), which runs on 12.x and 13.x
   drivers.
2. **The agent would not install on Python 3.10**, the default Python of Ubuntu
   22.04, a common GPU-server image. `numpy==2.4.6` requires Python 3.11 or
   newer. *Fix:* numpy 2.2.6 on Python 3.10, and 2.4.6 on 3.11 and newer.

And three hardening steps:

- **Lock files.** Only the 13 top-level packages of the agent were pinned. About
  160 transitive ones in the agent's environment alone, and more under NeMo, would
  float to whatever is newest on the day of the re-run. `requirements.lock` and `requirements-bench.lock` now pin every
  package. On Python 3.11 the lock matches the environment of our runs exactly,
  so `reproduce.sh` prefers 3.11.
- **No `python3-venv` or no suitable Python.** Stock Ubuntu lacks the `venv`
  module, and some images ship only Python 3.13. `reproduce.sh` now picks a
  Python in 3.10-3.12. If there is none, or `venv` is missing, it falls back to
  a pinned `uv`, which also supplies Python 3.11 when needed.
- **Model snapshots.** Whisper and Kokoro download the exact Hugging Face
  revisions of our runs (verified to load offline from the cache). The spaCy
  model that Kokoro's phonemizer fetches on first use is pinned in the lock.

Still open: a real run on a clean Linux GPU machine (see
[CLEAN_MACHINE_TEST.md](CLEAN_MACHINE_TEST.md)).

## 6. Failures a hosted model adds, and how they are absorbed

- **Rate limits and brief outages.** A 429 or 5xx from the Gemini API used to
  end the turn with an apology, which fails the item. Calls are now retried with
  backoff (1, 2, 4 s; verified against a local server that answers 503 twice),
  and a hung request is cut off after 20 s.
- **Empty or malformed replies.** Gemini 2.5 occasionally returns an empty
  candidate, or stops on `MALFORMED_FUNCTION_CALL`. The user would hear nothing.
  The thinker now asks once more before giving up. `tests/test_voice_thinker.py`
  covers this, and the rest of the thinker's tool loop.

## 7. Which Gemini models a re-run can actually reach (27 Sep 2026)

With a newly created key:

- `gemini-2.5-flash-lite` and `gemini-2.5-pro` are refused: *"no longer available
  to new users. Please update your code to use models/gemini-3.5-flash-lite"*
  (for Pro: `gemini-3.1-pro-preview`). `gemini-2.5-flash` still answers, but it
  is the same generation.
- Samsung's re-run uses a key of its own, possibly a new one. A declared model it
  cannot call would make the re-run fail, and that part of the grade scores zero.
  DUET therefore declares current-generation models, `gemini-3.7-flash`
  (thinker) and `gemini-3.5-flash-lite` (talker), which is Google's named
  replacement. The switch needed two changes for Gemini 3:
  - **Thought signatures.** Gemini 3 attaches them to function-call parts and
    rejects a follow-up request that lost one. The thinker used to delete a
    `keep_listening` call that came next to real work. It now keeps the model's
    turn exactly as returned and answers every call.
  - **Temperature.** Google advises keeping Gemini 3 at its default of 1.0,
    because lower values can cause looping. The seed stays fixed.
- **The free tier is not enough to evaluate on.** It allows 5 requests per minute
  per model and serves them slowly: 1-16 s for a one-sentence talker reply and
  28 s for a thinker step. Paid-tier latency is not measured yet. The benchmark
  needs a billing-enabled key.
- **First real-model check.** On "…the ID is X-K-4-2-Q-7, no wait, X-K-4-2-Q-8",
  `gemini-3.5-flash` made exactly one call, `track_order("XK42Q8")`, with the
  corrected, joined id. The talker named the corrected id too.
- **First sample (20 items, our ASR transcripts, exact-match scoring).** The free
  tier's daily cap (20 requests per model) stopped both runs part-way:
  - `gemini-3.7-flash` passed 7 of the 7 items it completed;
  - `gemini-3.5-flash` passed 5 of 6. On the two-step chain both attempted, 3.5
    got the second call's arguments wrong and 3.7 got them right.

  The thinker default is `gemini-3.7-flash`: newer (August 2026), half the price
  today, and at least as good on this sample. The full 100-item comparison runs
  once billing is on.

## 8. Gaps in today's systems that DUET is built against

- **FDB-v3 paper.**
  - The best published system, GPT-Realtime, passes fewer than 59% of
    self-correction items.
  - Gemini Live 3.1 gives no spoken reply in 22% of items, although in most of
    those it had already called its tools. Its pre-emptive calls "lock in the
    stale destination".
  - The cascaded Whisper→GPT-4o pipeline scores 0.176 on self-corrections,
    because it finalises the uncorrected transcript.
- **Reports from users of Gemini on Android Auto (2026).** It "won't stop
  talking", keeps speaking after the driver has used the touchscreen, and
  misreports failures. Sources are in `docs/USE_CASE_RESEARCH.md`.
