# DUET handover: everything you need to take over

This is for the SE7EN teammate taking over DUET from Tanmay on 28 Sep 2026. It
assumes you have not followed the project so far. It explains:
- what the hackathon asks;
- what DUET is and how the repository is laid out;
- how to run the benchmark and how to read the results;
- how to publish the results;
- what is still left (the extension, the video, the slides);
- how to submit.

Read section 0 first; it is the whole plan on one page. The other sections
explain each step in detail. Keep this file open while you work.

> **Related files.**
> - [DGX_EXPERIMENTS.md](DGX_EXPERIMENTS.md): the guide for the shared SRM DGX:
>   experiments, the full live run, the clean single-GPU run, and pushing every
>   result and log to GitHub.
> - [DGX_RUNBOOK.md](DGX_RUNBOOK.md): the short, copy-paste version of the
>   benchmark run, for whoever types the commands on the GPU machine.
> - [README.md](README.md): what Samsung reads.
> - [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/FINDINGS.md](docs/FINDINGS.md),
>   [docs/SAMSUNG_REQUIREMENTS.md](docs/SAMSUNG_REQUIREMENTS.md): the details.

---

## Contents

0. [The short version](#0-the-short-version)
1. [What the hackathon asks](#1-what-the-hackathon-asks)
2. [The benchmark, FDB-v3, in five minutes](#2-the-benchmark-fdb-v3-in-five-minutes)
3. [What DUET is and how it works](#3-what-duet-is-and-how-it-works)
4. [The repository](#4-the-repository)
5. [Access and accounts you need](#5-access-and-accounts-you-need)
6. [Running the benchmark, step by step](#6-running-the-benchmark-step-by-step)
7. [Reading the results](#7-reading-the-results)
8. [Improving the score (only if there is time)](#8-improving-the-score-only-if-there-is-time)
9. [Publishing the results into the submission](#9-publishing-the-results-into-the-submission)
10. [The use-case extension (20%)](#10-the-use-case-extension-20)
11. [The demo video and the slides](#11-the-demo-video-and-the-slides)
12. [Submitting](#12-submitting)
13. [The fallback: the Gemini version](#13-the-fallback-the-gemini-version)
14. [Troubleshooting](#14-troubleshooting)
15. [Reference: commands, settings, files](#15-reference-commands-settings-files)
16. [Glossary](#16-glossary)

---

## 0. The short version

### Where things stand (28 Sep 2026)

| Part of the score | Weight | State | What is left |
|---|---|---|---|
| **Benchmark** (Samsung re-runs our `reproduce.sh`) | **60%** | Code complete. One command installs and runs everything, with no API key. Unit tests pass (44). The whole pipeline has run end to end with a stand-in for the language model. | **The real language model (Qwen3-30B-A3B) has never run yet: this laptop cannot hold it.** The first run on a large GPU (the DGX) is the real test. Then the full 100-recording run gives the numbers we report. |
| **Use-case extension** | 20% | Research done ([docs/USE_CASE_RESEARCH.md](docs/USE_CASE_RESEARCH.md)); no code. | Pick the use case with Tanmay, build a small version that works end to end, and show it in the video. |
| **Docs, architecture, video** | 20% | README, architecture doc, findings and requirements map are written. | Fill the README results table from the run. Record the 3-5 minute video. Make the slides (at most 8). |

### What you do, in order

| # | Step | Time | Section |
|---|---|---|---|
| 1 | Read sections 0-3 of this file | 30 min | here |
| 2 | Accept the GitHub invitation, clone, get onto a GPU machine (or line up the DGX owner) | 15-30 min | [5](#5-access-and-accounts-you-need) |
| 3 | `bash scripts/doctor.sh`, then the one-recording run | ~1 h, mostly downloads | [6](#6-running-the-benchmark-step-by-step) |
| 4 | Offline accuracy on all 100 recordings: the first real Pass@1 | ~15 min | [6](#6-running-the-benchmark-step-by-step), [7](#7-reading-the-results) |
| 5 | The full benchmark run (`bash reproduce.sh --force`) | ~2 h, unattended | [6](#6-running-the-benchmark-step-by-step) |
| 6 | Send back and publish the results, then fill the README table | 30-45 min | [9](#9-publishing-the-results-into-the-submission) |
| 7 | The extension (while step 5 runs, start on the decision and design) | the rest of the day | [10](#10-the-use-case-extension-20) |
| 8 | Video and slides | 3-4 h | [11](#11-the-demo-video-and-the-slides) |
| 9 | Final checks and submission on the Google Form | 30 min | [12](#12-submitting) |

The benchmark comes first because if Samsung's re-run fails, 60% of the score
is zero. The extension comes before the video because the video has to show it.

### The rules you must never break

1. **Never commit or paste a secret.** That means API keys, the LiveKit secret,
   tokens. `.env.local` and `.env.livekit` are git-ignored; keep secrets there or
   in your shell. Samsung's rule: document which keys, never include them.
2. **Never write anything from the benchmark's test items into the agent.** No
   values, names, phrasings or item ids in prompts, code or rules. Samsung
   disqualifies submissions that pattern-match test items, "and we check".
   General behaviour fixes are fine; item-specific ones are not.
   `tests/test_voice_integrity.py` catches some of this; your judgement has to
   catch the rest.
3. **Never modify the benchmark's code.** That is anything under
   `third_party/Full-Duplex-Bench`. Samsung runs it unmodified; our code adapts to
   it, never the other way round.
4. **A setting only counts if it is the default in our code.** Samsung runs
   `bash reproduce.sh` with no environment variables. If `DUET_TEMPERATURE=0`
   scores better, change the default in the code (section 8); exporting it in
   your shell changes nothing for them.
5. **Don't change pinned versions** unless you then re-test the whole
   installation on a clean machine. The pins cover the lock files, vLLM 0.19.1,
   torch 2.10 and transformers 5.5.4. They exist so the install works on CUDA 12
   and 13 drivers.
6. **Don't raise GPU memory use.** Samsung's card has 48 GB, about 44.7 GiB
   usable, and our peak must stay under it. Leave the model's 22 GiB budget and
   12288-token context alone.
7. **Git hygiene.** Never force-push `main` or rewrite history. Make small commits
   with plain descriptive messages. Team convention: no AI-tool attribution lines
   in commit messages.
8. **The DGX is borrowed.** Use one GPU and never touch other people's jobs.
   Clean up when finished (section 6.6).

---

## 1. What the hackathon asks

Samsung PRISM GenAI Hackathon 3.0, **Theme 05: Interruptible Real-Time Agents**.
The team is SE7EN (SRM). Below is a condensed version of the updated participant
guide, which replaced the old kit on 26 Sep. The old kit is kept in
`legacy/kit_v1/` and no longer applies.

### The challenge

Build a voice agent that does three things at once:
- **Stay responsive:** spoken feedback within a few hundred milliseconds; no
  dead air and no false "done!" claims.
- **Work asynchronously:** tool calls, perception and reasoning run in the
  background and never block the conversation.
- **Recover cleanly:** when the user changes their mind mid-sentence, drop the
  stale intent, update the tool arguments, and never perform the same
  state-changing action twice.

### What to submit

- A code repository whose README covers:
  - the architecture (one diagram);
  - exact setup and run steps;
  - the extension, clearly marked.
- A **one-command reproduction script** that runs FDB-v3 against our agent
  (install, configure, evaluate), plus a declaration of the model or provider it
  uses.
- **Our results and run logs** (scores, seeds, configuration) from our best run.
- A **demo video, 3-5 minutes**: a real interruption handled on the benchmark,
  then the extension. Unedited single takes are preferred over polish.
- A **slide deck, at most 8 slides**: problem, architecture, benchmark results,
  what we would do next.

It goes in through a **Google Form**. There is one final submission per team,
and **the last upload counts**. Keys are documented but never included.

### How it is scored

**Round 1 score = 0.6 × benchmark + 0.2 × extension + 0.2 × documentation.**

- **Benchmark (60%).** Samsung re-runs FDB-v3 with *our* reproduction script, on
  a standard machine (one NVIDIA 48 GB GPU, CUDA 12.x/13.x) or our declared hosted
  APIs.
  - Only their re-run counts; our own numbers only guide them.
  - If the script fails, they contact us once. If it still fails, **this part
    scores zero**.
  - The judge is the benchmark's LLM judge, a single pinned model (gpt-4o in the
    benchmark code), the same for every team.
  - Ties break on the strict pass rate (Pass@1).
- **Extension (20%).** Judged on how relevant the use case is, whether it runs end
  to end, and whether the video shows it working. "One use case done well beats
  three half-built ones."
- **Documentation, architecture, video (20%).** "Can we understand and trust your
  system from the submission alone": a clear README, an honest architecture
  explanation, and a video of real behaviour, not a slideshow.
- **Round 2** (shortlisted teams): a live jury demo where the agent is interrupted
  live, plus questions on the design.

### Dos and don'ts (from the guide)

| Do | Don't |
|---|---|
| use public checkpoints and hosted APIs, and cite them | hardcode, memorise or fine-tune on benchmark items |
| pin seeds and versions so the re-run matches our logs | call our own servers at evaluation time |
| test the reproduction on a machine that is not ours | cache anything across scenarios |
| keep the extension honest (working beats ambitious) | |

### What the mentors stressed

- Hard, logical tasks without breaking conversational flow.
- A "dual mind": one mind talks, one thinks.
- No static rule-based systems.
- Clean recovery from failures (retry, human in the loop).
- Latency and cost savings earn extra credit.
- Show run logs, not only numbers.

### Deadline

**29 Sep 2026.** The mentors mentioned the 30th; don't count on it. Check the
exact closing time on the Google Form or with Tanmay.

---

## 2. The benchmark, FDB-v3, in five minutes

Full-Duplex-Bench v3 comes from NTU (paper arXiv 2604.04847; code at
github.com/DanielLin94144/Full-Duplex-Bench, `v3/` folder). We pin its commit
`3e799c4`.

**The data.** 100 real recordings (79 scenarios, 12 speakers), 36-59 s each. The
spoken request comes first, full of real disfluency:
- fillers ("um");
- pauses of up to about 1.5 s;
- hesitations;
- false starts;
- self-corrections ("order 1234, no wait, 1243").

About 30 s of the speaker's real room noise follows. The requests use **12 mock
tools** across four domains:

| Domain | Tools |
|---|---|
| e-commerce support | `track_order`, `search_products`, `add_to_cart` |
| finance and billing | `get_card_benefits`, `modify_autopay`, `get_exchange_rate` |
| housing and location | `search_apartments`, `calculate_commute`, `update_search_filter` |
| travel and identity | `search_flights`, `book_flight`, `update_identity_doc` |

Items need 1, 2 or 3 chained calls (easy, medium, hard).

**How one item runs:**
1. The benchmark's runner joins a LiveKit room as the "user" and streams the
   recording in real time.
2. Our agent joins the same room, listens, calls tools and speaks.
3. The runner records the agent's audio for **exactly the recording's length**.
   Anything said after that window is lost.
4. Every tool call the agent makes is appended to `/tmp/agent_tool_calls.log`
   (a fixed path) with the room name, and the runner picks it up.
5. Parakeet, NVIDIA's speech recognizer, transcribes both sides.
6. Three evaluation scripts score the item.

**The metrics:**

| Metric | What it measures | Good |
|---|---|---|
| **Pass@1** (strict pass rate) | The share of items where the agent called **exactly** the expected tools, no extras and none missing, with correct arguments. One stale call made before a self-correction fails the item. This is the tie-breaker. | higher |
| Tool-selection F1 | Right tools, partly right counts | higher |
| Argument accuracy | Right argument values; the LLM judge allows "next Friday" for a date, aliases, ±5% on numbers | higher |
| Response quality | The LLM judge compares the final spoken answer with the expected one; a partial answer to a multi-step request scores 0 | higher |
| Turn-take rate | Share of items where the agent spoke at all | 100% |
| Latency | First response, first tool call, and "key information" (the sentence carrying the answer) | lower |
| Interruption rate | Agent speech before the user finished; talking over the user counts against us | lower |

**The published baselines** (the paper's Table 2), which the README compares
us against:

| System | Pass@1 | Tool F1 | Arg | Resp | Task latency | Interrupt |
|---|---|---|---|---|---|---|
| GPT-Realtime | 0.600 | 0.876 | 0.680 | 0.792 | 6.89 s | 13.5% |
| Gemini Live 3.1 | 0.540 | 0.817 | 0.588 | 0.718 | 4.25 s | 19.2% |
| Cascade Whisper → GPT-4o → TTS | 0.450 | 0.803 | 0.562 | 0.600 | 10.12 s | 33.0% |

Self-correction is where everyone loses the most (best system 0.588, the
cascade 0.176). DUET is built around exactly that failure.

**Traps we found in the data** ([docs/FINDINGS.md](docs/FINDINGS.md) §1-3):
- Speech recognizers invent sentences in the noisy 30 s tail, and acting on them
  adds a call and fails the item.
- Some expected calls leave out arguments the tool template marks as required.
- The runner connects and then waits a fixed 2 s before streaming. Our agent is
  ready well within that.

---

## 3. What DUET is and how it works

DUET is a **custom LiveKit voice agent**. It is not one of the benchmark's preset
providers. It pairs two "minds" with a coordinator that decides *when acting is
allowed*.

### One turn, as a story

The user says: *"Can you track my order, it's 1234, no wait, 1243."*

1. **Ears.** Silero VAD notices speech. faster-whisper (large-v3-turbo, on the GPU)
   transcribes it, and a filter drops non-speech hallucinations. LiveKit's turn
   detector judges whether the user has finished.
2. **Epochs.** Every new stretch of speech advances an "epoch". Any plan made on
   earlier words is now stale and gets discarded, so "1234" never reaches a tool.
3. **The commit gate.** No tool runs while the user is speaking, or before the
   turn has closed and the user has been quiet for a short hold (1.1 s). The hold
   is longer while they are revising ("no wait…", 1.8 s) or have left a sentence
   open ("…and", 2.2 s).
4. **The thinker.** The language model, with the 12 tools, reads the whole open
   request. It can decide the user is not finished yet (`keep_listening`); DUET
   then waits and takes a second look after 2.5 s of quiet. Otherwise it calls
   `track_order` with the final value, 1243.
5. **The talker.** While the thinker works, the talker (the same model, short
   instructions) says one truthful acknowledgement, such as "Let me check that
   order." It never states a result it doesn't have, and never repeats a
   specific value.
6. **The ledger.** An identical state-changing call is never performed twice,
   even if the user barges in mid-call. A failed read is retried once; a failed
   or uncertain write is never blindly re-sent.
7. **Mouth.** The thinker's answer, with the key facts, is spoken by Kokoro-82M
   (text-to-speech, on the GPU). If the thinker fails, a fallback line ("One
   moment.") keeps the conversation alive.

### The pieces and where they live

| Piece | What it does | File |
|---|---|---|
| LiveKit entrypoint | Joins each room, wires VAD/STT/TTS, the turn handling and traces | `duet_voice/agent.py` |
| Coordinator | Epochs, commit gate and holds, idempotency ledger, failure policy | `duet_voice/coordinator.py` |
| Thinker (local model) | Tool loop against the vLLM server, `keep_listening`, retries | `duet_voice/llm_local.py` |
| Thinker (Gemini) | The same loop for the Gemini API (the fallback) | `duet_voice/thinker.py`, `duet_voice/gemini.py` |
| Talker | The one-line acknowledgement | `duet_voice/talker.py` |
| Prompts | The thinker's and talker's instructions, written from general principles | `duet_voice/prompts.py` |
| Tools | The 12 tool schemas, the adapter to the benchmark's `mock_apis.py`, and the call log | `duet_voice/fdb_tools.py` |
| Speech models | faster-whisper STT, Kokoro TTS, 16 kHz resampling | `duet_voice/speech_models.py`, `duet_voice/plugins.py` |
| Settings | Every setting, with its environment variable | `duet_voice/config.py` |

### The model: Qwen3-30B-A3B-Instruct-2507, local, on the same GPU

- **Why local.** We have no paid API credits:
  - The Gemini key's prepaid balance is used up, and its free tier is 20
    requests per model per day.
  - The OpenAI key has no credit.
  - Two Gemini 2.5 models stopped accepting new keys this month.

  A local open-weights model needs no key and costs nothing, and Samsung's re-run
  cannot fail on a quota.
- **Why this model.** It is a mixture-of-experts model: 30.5B parameters, about
  3.3B active per token, so it answers fast. It is good at tool calling and
  Apache-2.0 licensed.
- **How it fits in 48 GB:**
  - **Weights.** Red Hat's 4-bit build (16.7 GB) runs under vLLM 0.19.1 in a
    fixed 22 GiB budget, on every GPU generation. It is the build that ran on the
    DGX A100 on 28 Sep, so Samsung's re-run uses what we measured.
  - **Everything else.** The speech models take about 3.5 GiB and the
    benchmark's scoring recognizer about 4-5 GiB: about 31 GiB in all.
  - **FP8.** Qwen's own FP8 build (31.2 GB, 33 GiB budget, Ada or later) is kept
    for comparison runs only (`DUET_LLM_VARIANT=fp8`).
- **Sampling.** The model card's values (temperature 0.7, top-p 0.8, top-k 20),
  with seed 7.
- **Where it runs.** The server listens on `127.0.0.1:18000`: part of the
  submission, running on the evaluation machine, not a server of ours.

### The judge

The benchmark's scoring uses gpt-4o as its judge, which needs an OpenAI key with
credit. We have none. Our scripts therefore use the best judge available:
1. gpt-4o, if a working `OPENAI_API_KEY` is set;
2. otherwise the local Qwen model, as a **proxy judge, labelled as such**;
3. otherwise Gemini.

Samsung uses its own gpt-4o, so our argument accuracy, response quality and
Pass@1 are estimates. Say so wherever you report them.

---

## 4. The repository

**Repository:** `github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN` (private).

### Branches

| Branch | What it is |
|---|---|
| `main` | **The submission.** Everything you change goes here in the end. |
| `gemini-ready` | A snapshot of the working Gemini version before the switch to the local model. It is the fallback (section 13); leave it alone. |
| `results-<name>-<time>` | Created by `scripts/collect_results.sh --push`: one run's results sent back from a GPU machine. Merge the one we report into `main` (section 9). |

### Files

| Path | What | When you touch it |
|---|---|---|
| `README.md` | What Samsung reads: overview, results table, architecture diagram, model declaration, keys, reproduction, run logs, integrity, extension | Results table (section 9), extension section (section 10) |
| `HANDOVER.md` | This file | Delete it (or move it into `notes/`) before submitting |
| `DGX_RUNBOOK.md` | Copy-paste steps for running on a remote GPU machine | Send it to whoever runs the DGX |
| `reproduce.sh` | The one command Samsung runs | Only for install/run fixes |
| `scripts/env.sh` | Shared settings: cache folders under `third_party/`, GPU choice, interpreter paths | Rarely |
| `scripts/doctor.sh` | Checks a machine and writes `doctor_report.txt` | Never |
| `scripts/offline_eval.sh` | Fast accuracy on all 100 recordings | Never; pass options to it |
| `scripts/collect_results.sh` | Packs a run (`.tar.gz`) and optionally pushes it as a `results-*` branch | Never |
| `duet_voice/` | The agent (table in section 3) | Prompts and defaults when improving (section 8); extension hooks (section 10) |
| `bench/run_live.py` | Drives a real run: model server, LiveKit, agent, the benchmark's runner, the three evaluations, `summary.json` | Rarely |
| `bench/llm_server.py` | Starts vLLM with the right weights and memory for this GPU (`plan`, `prefetch`, `serve`, `health`) | Never |
| `bench/offline_eval.py`, `bench/offline_asr.py` | The fast loop: transcribe once, then run the thinker on the transcripts and score officially | Never |
| `bench/judge.py`, `bench/official_eval.py` | Pick the judge and run the benchmark's evaluation scripts unmodified | Never |
| `bench/publish_run.py` | Copies a run (no audio) to `results/reported/<name>/` | Section 9 |
| `bench/cost_report.py` | Tokens and cost from the traces | Optional |
| `bench/fdb_runner.py` | The runner for `--only` subsets; full runs use the benchmark's own script | Never |
| `requirements*.txt`, `requirements*.lock` | Direct and fully pinned packages for the three environments | Never (rule 5) |
| `tests/` | Coordinator, speaking flow, local model loop, integrity, cost. `fake_openai.py` and `fake_gemini.py` are stand-in model servers | Run after any code change |
| `docs/ARCHITECTURE.md` | The honest architecture explanation | If the design changes |
| `docs/FINDINGS.md` | Engineering findings, with evidence | Add the DGX findings |
| `docs/SAMSUNG_REQUIREMENTS.md` | Every guide line mapped to where we meet it, with a status | Update the statuses (section 9) |
| `docs/USE_CASE_RESEARCH.md` | The extension research | Read before section 10 |
| `docs/CLEAN_MACHINE_TEST.md` | How to test on a fresh cloud VM | Only if no DGX |
| `results/reported/` | The committed run records. `listening_dry_run/` is the no-model check of all 100 recordings | Section 9 adds the real run |
| `notes/FDB_V3_PLAN.md` | The original plan after the benchmark change | Background only |
| `legacy/kit_v1/` | The previous kit and DUET v1 (research record) | Never |

### What is *not* in git

These are created on the machine that runs things:
- `third_party/`: uv, Python 3.11, package caches, model weights, the benchmark
  clone and its audio, ffmpeg, LiveKit. About 60 GB.
- `.venv/`, `.venv-bench/`, `.venv-llm/`: the three Python environments.
- `results/live/`, `results/offline/`: run outputs.
- `.env.local`, `.env.livekit`: secrets.

`reproduce.sh` creates all of these.

---

## 5. Access and accounts you need

### GitHub

1. Accept the invitation to the repository (email, or github.com → Notifications).
   If you don't have one, ask Tanmay to add you: Settings → Collaborators →
   **Write** access.
2. Clone it. The repository is private, so authenticate with a personal access
   token (github.com → Settings → Developer settings → Personal access tokens) or
   an SSH key:
   ```bash
   git clone https://github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet
   # or: git clone git@github.com:tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet
   ```
3. Set your identity once per machine; commits and the results push need it:
   ```bash
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```

### A GPU machine

| Need | Why |
|---|---|
| Linux x86_64 | vLLM and NeMo run on Linux only |
| One NVIDIA GPU with **34 GiB free** (on a shared machine: see [DGX_EXPERIMENTS.md](DGX_EXPERIMENTS.md)) | Everything runs on one GPU, as on Samsung's 48 GB machine |
| NVIDIA driver for CUDA 12.x or 13.x | All GPU packages are CUDA 12.8 builds |
| ~80 GB free disk | Environments ~25 GB, models ~22 GB, caches |
| `git`, `curl`, `tar`, `gcc` | vLLM's kernels compile small helpers with gcc |
| Internet to pypi.org, huggingface.co, github.com, astral.sh, drive.google.com | Packages, weights, the benchmark, its audio |

No sudo, Docker, system Python or API key is needed.

**Options:**
- **Tanmay's friend's DGX (H100 80 GB).** Either the friend runs
  [DGX_RUNBOOK.md](DGX_RUNBOOK.md) and pushes the results, or they give you SSH
  access and you run it yourself. Agree with them beforehand which GPU index is
  free and when.
- **Any university or lab server** with a free 48 GB or 80 GB GPU.
- **A cloud VM** (A100 80 GB or H100): [docs/CLEAN_MACHINE_TEST.md](docs/CLEAN_MACHINE_TEST.md).
  This costs money (about US$1-4 an hour); delete it afterwards.

Your own laptop cannot run the model: it needs about 31 GiB of GPU memory. You
can still read and edit code there, and run the unit tests (section 15).

### Optional accounts

| Account | For | Notes |
|---|---|---|
| LiveKit Cloud (free) | The **live demo** in the video: you talk to the agent from a browser (section 11) | Create your own project at cloud.livekit.io. Keep its URL, API key and secret in your shell or `.env.livekit`; **never commit them**. The benchmark run does not need it: `reproduce.sh` runs a local LiveKit server. |
| OpenAI with credit | The official gpt-4o judge for our own numbers | We have none. Without it, results are labelled "proxy judge". |
| Google Gemini with billing | Only for the fallback in section 13 | Not needed otherwise |

Keys that were pasted into chats earlier should be treated as burned: don't
reuse them, and Tanmay will rotate them.

---

## 6. Running the benchmark, step by step

If the DGX owner is running it, send them [DGX_RUNBOOK.md](DGX_RUNBOOK.md); it
has the same steps in short form. This section explains *why* each step exists
and what to look at.

### 6.1 What `reproduce.sh` does

Every stage is skipped if it has already been done, so re-running is safe and an
interrupted install resumes.

1. **Checks the machine:** `git`, `curl`, `tar`, `sha256sum`, the GPU. If
   `LIVEKIT_URL` is set, the key and secret must be too.
2. **Installs uv 0.12.19** (a fast Python package manager) into `third_party/uv`.
   uv downloads its own **Python 3.11**.
3. **Creates three environments**, each installed from a lock file that pins every
   package, including transitive ones:
   - `.venv`: the agent (LiveKit agents 1.8.3, faster-whisper, Kokoro), from
     `requirements.lock`.
   - `.venv-bench`: the benchmark's runner and scorers (NVIDIA NeMo for Parakeet,
     torch 2.10), from `requirements-bench.lock`.
   - `.venv-llm`: the model server (vLLM 0.19.1, transformers 5.5.4), from
     `requirements-llm.lock`.

   Each gets a `.duet-installed` marker when complete.
4. **Fetches ffmpeg** (a static build, checksum-verified) if the machine has none.
5. **Clones Full-Duplex-Bench** into `third_party/` at commit `3e799c4`.
6. **Downloads the audio** (736 MB zip from the Google Drive link in the v3
   README). It retries three times, verifies the SHA-256 and extracts to
   `third_party/Full-Duplex-Bench/v3/fdb_v3_data_released/`.
7. **Fetches the LiveKit server** v1.13.7 (checksum-verified), unless LiveKit
   Cloud variables are set.
8. **Pre-downloads every model**, so the timed run is not a download:
   - Whisper, Kokoro and the turn detector;
   - the Qwen weights (the 4-bit build, 17 GB);
   - Parakeet.
9. **Runs `bench/run_live.py`**, which:
   - starts the model server (`bench/llm_server.py`) and waits until it answers;
   - runs a **preflight**: the model must list itself and make a real
     `track_order` tool call, or the run stops here instead of producing 100
     failures;
   - starts a local LiveKit server on free ports;
   - starts the agent (`python -m duet_voice.agent start`) and waits until it is
     registered and warm;
   - runs the benchmark's **unmodified** runner, which streams each recording
     into a fresh room;
   - copies the per-item results and the tool-call log into the run folder;
   - runs the three evaluation scripts with the LLM judge;
   - writes `summary.json`, including the GPU's peak memory;
   - stops everything it started.

Everything lands in `results/live/<YYYYmmdd_HHMMSS>/`.

### 6.2 The steps

Do everything inside **tmux** so a dropped SSH connection doesn't kill the run:

```bash
cd /raid/$USER            # a disk with ~90 GB free; on a DGX, /raid is the big one
git clone https://github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet
cd duet
tmux new -s duet          # later: tmux attach -t duet ; detach: Ctrl-b then d
```

**Step 0: check the machine (1 minute).**

```bash
nvidia-smi                                  # which GPUs are idle?
export CUDA_VISIBLE_DEVICES=3               # optional: pin an idle GPU (default: most free memory)
bash scripts/doctor.sh
```

You want `OK` lines and `Ready.` at the end. Each `FAIL` says how to fix it. The
report is saved as `doctor_report.txt`.

**Step 1: install everything and run one recording (~45-60 min the first time).**

```bash
bash reproduce.sh --only travel_19_695bd157114f0d2317f88617
```

Near the end you should see:

```
Model server: ready at http://127.0.0.1:18000/v1
Models: qwen3-30b-a3b-instruct-2507 (thinker), qwen3-30b-a3b-instruct-2507 (talker)
Tool calling: ok
...
Run folder: .../duet/results/live/<time>
GPU NVIDIA H100 80GB HBM3: peak 4x.x GiB used of 79.6 GiB
```

The summary above that should show `"turn_take_rate": 1.0`. This is the moment
of truth for the real model. If it fails, go to section 14 and send the log to
Tanmay.

**Step 2: accuracy on all 100 recordings, fast (~15 min).**

```bash
bash scripts/offline_eval.sh
```

This measures the thinker on its own:
1. It transcribes all 100 recordings once with the agent's own recognizer.
2. It gives each transcript to the model with the 12 tools.
3. It scores the calls with the benchmark's own scorers.

There is no real-time streaming, so it takes minutes, not hours. It is the best
early estimate of Pass@1. Two comparisons worth running while you are there (~5
minutes each):

```bash
bash scripts/offline_eval.sh --text script --tag script     # the exact scripts: the ceiling without hearing errors
DUET_TEMPERATURE=0 bash scripts/offline_eval.sh --tag greedy  # greedy decoding vs the model card's 0.7
```

**Step 3: the full benchmark (~2 hours, unattended).**

```bash
bash reproduce.sh --force
```

**Always use `--force` here.** The benchmark's runner skips every recording that
already has a result under the same name (from step 1, or from any earlier run)
and silently reuses it in the scores. `--force` makes every score come from this
run. Without it, the run prints a warning with the count. Detach with `Ctrl-b d`
and come back later.

**Step 4: send the results back (~1 min).**

```bash
bash scripts/collect_results.sh --push dgx_full_run
```

This does three things:
- packs the latest run, recent offline evaluations and the doctor report into
  `duet_results_<time>.tar.gz` (no audio);
- copies the run into `results/reported/dgx_full_run/`;
- commits that copy on a new branch `results-dgx_full_run-<time>`, pushes it, and
  switches back.

It needs GitHub write access and `git config user.name/user.email`. Without
access, run it without `--push` and send the `.tar.gz` any other way (scp, Drive).

### 6.3 Watching a run

In a second tmux window (`Ctrl-b c`; switch with `Ctrl-b n`):

```bash
cd /raid/$USER/duet
R=$(ls -dt results/live/*/ | head -1); echo "$R"
tail -f "$R/runner.log"          # which recording is being streamed
tail -f "$R/agent.log"           # the agent: turns, tool calls, errors
tail -f "$R/llm_server.log"      # the model server
ls third_party/Full-Duplex-Bench/v3/fdb_v3_data_released/*/result_duet.json | wc -l   # items finished (of 100)
watch -n 5 nvidia-smi            # memory on the GPU
```

Each recording takes about 70 s including scoring, so 100 take about 2 hours.

### 6.4 Re-running and experiments

| You want to | Command |
|---|---|
| Re-run everything after a change | `bash reproduce.sh --force` |
| Re-run a few recordings | `bash reproduce.sh --only travel_19,housing_04 --force` |
| Keep an experiment's results apart from the main run's | Add `--provider duet_<name>`: results are filed under that name, and the scores cover only those items |
| Re-score saved results without re-running | `.venv/bin/python bench/run_live.py --eval-only --bench-python .venv-bench/bin/python` |
| Check listening and plumbing with no model | `bash reproduce.sh --dry-run --only travel_19` (the agent answers "Okay." to every turn) |

With `--only`, the scores also include every other recording that still has a
result under the same name; the run warns you. Use a fresh `--provider` name for
subsets you want scored alone.

### 6.5 If the DGX owner runs it

1. Make sure they have Write access to the repository and have accepted the
   invitation.
2. Send them [DGX_RUNBOOK.md](DGX_RUNBOOK.md). They copy the commands in order;
   nothing needs watching.
3. When they are done, fetch the results:
   ```bash
   git fetch origin
   git branch -r | grep results-
   git checkout origin/results-dgx_full_run-<time> -- results/reported/dgx_full_run
   ```
   The last command copies the folder into your working tree without switching
   branches. Section 9 then publishes it.
4. If something failed, ask them for:
   - the last screen of output;
   - `doctor_report.txt`;
   - the `.tar.gz` from `bash scripts/collect_results.sh` (it includes
     `llm_server.log` and `agent.log`).

### 6.6 Cleaning up the machine

Everything is inside the repository folder (~90 GB):

```bash
pkill -u $USER -f vllm.entrypoints; pkill -u $USER -f duet_voice.agent; pkill -u $USER -f livekit-server
rm -rf third_party .venv .venv-bench .venv-llm results/live results/offline
```

Don't clean up until the submission is in: you may need to re-run.

---

## 7. Reading the results

### 7.1 `results/live/<time>/summary.json`

| Field | Meaning | Healthy value |
|---|---|---|
| `pass_at_1` | Strict pass rate, 0-1 (the headline) | Beat the cascade (0.45); GPT-Realtime is 0.60 |
| `passed` / `total` | Items passed / scored | `total` = 100 on a full run |
| `tool_selection_f1` | Right tools | ≥ 0.80 |
| `argument_acc` | Right arguments (judge) | ≥ 0.56 |
| `response_qual` | Final answer quality (judge) | ≥ 0.60 |
| `turn_take_rate` | Share of items where the agent spoke | 1.0 (the dry run had 1.0) |
| `interruption_rate` | Share of items where the agent spoke before the user finished | ≤ 0.15 (the dry run had 0.12) |
| `first_response_latency_mean_s`, `task_completion_latency_mean_s` | Seconds; includes the benchmark's constant ~1.9 s recorder offset | as low as possible; the baselines are 4-10 s |
| `pass_by_difficulty`, `pass_by_disfluency`, `pass_by_domain` | Pass@1 per slice | Self-correction is the one to show |
| `agent.thinker_errors` | Model calls that failed | **0**; any number above a handful means something is broken (see `first_thinker_error`) |
| `agent.fallback_acks` | Times the talker was too slow and "One moment." was said instead | low |
| `agent.keep_listening` | Times the thinker decided the user wasn't finished | some; proof the mechanism works |
| `agent.superseded` | Plans discarded because the user kept talking | some; proof the epochs work |
| `agent.tool_calls` | Tool calls made | roughly 150-250 for 100 items (1-3 each) |
| `gpu` | `ours_peak_gib`: the peak memory of this run's own processes, summed over every GPU they used. `peak_used_gib`: the whole first card, other users' jobs included | `ours_peak_gib` **≤ 43 GiB** fits Samsung's 48 GB card (expected: about 31) |

Next to it:
- `run_config.json`: every effective setting, the models in use and any
  overrides;
- `llm_plan.json`: which weights and memory budget the server used;
- the benchmark's three reports: `duet_evaluation_report.json`,
  `duet_pass_rate_report.json`, `duet_latency_report.json`.

### 7.2 The offline evaluation: `results/offline/eval_<text>_<time>[_<tag>].json`

`summary` holds `pass@1`, `tool_f1`, `arg_acc`, `resp_qual` and `errors` (should
be 0), plus `pass_by` slices. `rows` holds one entry per recording:
- what was heard (`input`) and what was called (`calls`);
- what was expected (`expected`);
- `passed`, and `why` it failed.

To list the failures of the latest evaluation:

```bash
.venv/bin/python - <<'EOF'
import glob, json, os
f = max(glob.glob("results/offline/eval_*.json"), key=os.path.getmtime)
d = json.load(open(f, encoding="utf-8"))
print(f, "pass@1 =", d["summary"]["pass@1"], "errors =", d["summary"]["errors"])
for r in d["rows"]:
    if not r["passed"]:
        print("-", r["example_id"], "|", r["disfluency"], "|", r["why"])
EOF
```

The `--text script` run tells you *why* items fail:
- items that fail on our transcripts but pass on the exact scripts are hearing
  problems;
- items that fail on both are reasoning or prompt problems.

### 7.3 Following one conversation

For an item `<folder>` in a run:
1. `results/live/<time>/items/<folder>.json` has what the benchmark saw: the
   transcripts, `actual_tool_calls`, the timings and the `room_name`.
2. `results/live/<time>/traces/<room_name>.jsonl` is DUET's side, one event per
   line: `heard` segments, turn decisions, `talker` lines, thinker steps,
   `thinker_listen` (keep listening), `superseded`, tool calls with arguments and
   outcomes, and `agent_said`.
3. The audio is in `third_party/Full-Duplex-Bench/v3/fdb_v3_data_released/<folder>/`:
   `input.wav` is the user and `output_duet.wav` the agent, the same length and
   time-aligned.

### 7.4 Reporting honestly

Without an OpenAI key, the judge-based numbers (argument accuracy, response
quality, Pass@1) come from the local proxy judge. Wherever you write them,
label them:

> judge: local Qwen3-30B-A3B proxy; Samsung's pinned gpt-4o judge is
> authoritative.

Tool F1, turn-take, latency and interruption don't depend on the judge.

---

## 8. Improving the score (only if there is time)

The order of priorities is fixed: a clean, reproducible run first, then the
extension, the video and the slides. Tune only after those are safe. Every
change needs a new full run (2 h) before it can be reported.

**The loop:**
1. Change one thing.
2. Run `bash scripts/offline_eval.sh --tag <what-you-changed>` (15 min).
3. Compare its `pass@1` with the previous one.
4. Keep the change only if it clearly helps. Remember the run-to-run noise: a
   difference of 1-2 items out of 100 is noise.

**What you may change**: general behaviour, never item-specific.

| Knob | Default | Where the default lives | Notes |
|---|---|---|---|
| Temperature | 0.7 | `duet_voice/llm_local.py`, `sampling()` | If `DUET_TEMPERATURE=0` wins clearly offline, change the `0.7` there |
| Thinker instructions | | `duet_voice/prompts.py`, `THINKER_INSTRUCTIONS` | Fix *kinds* of mistakes: acting on a value the user later corrected, extra calls, missing a chained step. Never mention a benchmark value or scenario. |
| Talker instructions | | `duet_voice/prompts.py`, `TALKER_INSTRUCTIONS` | Keep it short and truthful |
| Commit holds | 1.1 / 1.8 / 2.2 s | `duet_voice/config.py` (`DUET_COMMIT_HOLD`, `DUET_REVISING_HOLD`, `DUET_DANGLING_HOLD`) | Timing only shows in a full live run. Don't touch it without evidence from traces. |
| End of turn | 0.8-2.5 s | `duet_voice/config.py` (`DUET_ENDPOINT_MIN/MAX`) | Same |

**Never change:** the tool names, argument names or log format (the benchmark
reads them), anything under `third_party/`, the pins, or the memory budget.

**After a change:**

```bash
source scripts/env.sh                               # puts the project's uv on PATH
uv pip install --python .venv/bin/python pytest     # once
.venv/bin/python -m pytest tests -q                 # all must pass, including the integrity test
git add -p && git commit -m "Thinker: <what and why>"
git push origin main
```

Then do a full run with `--force` and publish it (section 9).

---

## 9. Publishing the results into the submission

Do this once you have the full run you want to report.

1. **Get the run into `main`.**
   - The DGX owner pushed a `results-*` branch: follow section 6.5 step 3.
   - You ran it yourself: `.venv/bin/python bench/publish_run.py results/live/<time> --name dgx_full_run --note "Qwen3-30B-A3B, H100 80GB, <date>"`.

   Either way you get `results/reported/dgx_full_run/` with:
   - `summary.json` and `run_config.json`;
   - the benchmark's reports, `items/` and `traces/`;
   - the logs, including `llm_server.log` and `llm_plan.json`;
   - a `README.md` index.
2. **Fill the README results table** (`README.md`, section "Results"). In the
   `DUET (ours)` row, replace *pending* with:

   | Column | From `summary.json` |
   |---|---|
   | Pass@1 | `pass_at_1` |
   | Tool F1 | `tool_selection_f1` |
   | Arg acc | `argument_acc` |
   | Resp qual | `response_qual` |
   | Turn-take | `turn_take_rate` (as %) |
   | Latency (task) | `task_completion_latency_mean_s` |
   | Interrupt | `interruption_rate` (as %) |

   Under the table:
   - link the folder, `results/reported/dgx_full_run`;
   - name the judge label from section 7.4;
   - give the GPU and its peak memory (`gpu`);
   - give the self-correction Pass@1 (`pass_by_disfluency.SELF_CORRECTION`) next
     to the paper's 0.588 / 0.176.
3. **Update [docs/SAMSUNG_REQUIREMENTS.md](docs/SAMSUNG_REQUIREMENTS.md).** Rows
   still describe the Gemini period ("needs a billing-enabled key", "free
   tier…"). Rewrite them with the DGX facts:
   - "Stay responsive": the measured latency;
   - "Iterate on self-corrections": the offline and live Pass@1;
   - "Results and run logs": ✅ with the folder;
   - "Test the reproduction on a machine that is not ours": ✅ with GPU, driver,
     OS and date;
   - "Samsung re-runs…": the measured peak memory.
4. **Add a short section to [docs/FINDINGS.md](docs/FINDINGS.md)** with what the
   first real run showed: numbers, anything that broke and its fix.
5. **Commit and push:**
   ```bash
   git add results/reported/dgx_full_run README.md docs/
   git commit -m "Reported run: Qwen3-30B-A3B on an H100, all 100 items"
   git push origin main
   ```

---

## 10. The use-case extension (20%)

### What the guide requires

- One real use case beyond the benchmark's domains.
- It **runs end to end**, shows up working in the video, and is clearly marked in
  the README.
- It is judged on relevance, on working end to end, and on the video showing it
  work.
- "A sketch on a slide does not count." "One use case done well beats three
  half-built ones."
- The mentors want it to extend the dual mind (one talks, one thinks).

### Where it stands

Research only. [docs/USE_CASE_RESEARCH.md](docs/USE_CASE_RESEARCH.md) compares
eight directions. Its recommendation is **A: DUET Co-Driver**, an in-car
assistant. The guide itself names "an in-car destination change" as an example.
Its shape:
- The driver can only use voice, and plans change mid-drive: stops, destinations,
  who to tell, what to do at home.
- The task spans the car, the phone and the home, through SmartThings.

**Decide the direction with Tanmay before building.** Tanmay wants to approve it first.

### A scope that fits one day

Keep it small and honest:
- one simulated drive;
- 4-6 tools with deterministic, local mock results;
- a single scripted-but-real conversation you can perform live in one take.

Show each DUET property once:
- **Change of mind mid-sentence:** "Take me to the Starbucks on 5th, no, the one
  by the office." One `set_destination`, with the final value.
- **Barge-in:** the driver interrupts the agent's reply; it stops and handles the
  new request.
- **Asynchronous work:** a slow tool (a charger search) runs while the agent
  keeps talking.
- **Exactly once:** "Send Priya my ETA", then "did you send it?". It is never
  sent twice.
- **Honest failure:** a tool times out ("dead zone"). The agent says so and
  offers a retry, never a false "done".

### How it plugs into the code

The agent is built around the benchmark's tools today. An extension needs its
own toolset and instructions, selected by a setting, so the benchmark path stays
untouched:
- **Tools.** A new module, for example `duet_voice/codriver.py`, holds the tool
  specs (same shape as `TOOL_SPECS` in `duet_voice/fdb_tools.py`) and a toolbox
  class with the same `call(tool, raw_args, epoch=None)` method as `FdbToolbox`,
  going through the same `Coordinator` so the gate and ledger apply.
- **Instructions.** Thinker and talker instructions for the car, in the style of
  `duet_voice/prompts.py`.
- **The switch.** A setting such as `DUET_USE_CASE=codriver` (default:
  benchmark) that `agent.py` (where `FdbToolbox` is created) and `llm_local.py`
  (where `TOOLS` and the instructions are read) use to pick the set.
- **Checks.** Tests for the new tools in `tests/`. Run the full test suite, then
  a benchmark `--only` run, to prove the benchmark path is unchanged.
- **Running it.** It runs like the live demo in section 11.2, with the setting
  exported. A small web page with a map is a nice extra, not a requirement.

The README section "Use-case extension" then says:
- what it is and why it matters (a few lines from the research);
- how to run it;
- what is real and what is mocked (be explicit: judges reward honesty);
- where the code is.

---

## 11. The demo video and the slides

### 11.1 The video (3-5 minutes)

Required: **a real interruption handled on the benchmark, then the extension in
action.** Unedited single takes are preferred; screen recording with voice-over
is fine.

A shot list that fits about 4 minutes:

| Time | Show | How |
|---|---|---|
| 0:00-0:20 | Team, the problem in one sentence ("assistants act on words the user has already taken back") | Face or title card |
| 0:20-1:40 | **A benchmark self-correction, handled.** Pick a `SELF_CORRECTION` item that passed in the reported run. | Open `input.wav` and `output_duet.wav` together in Audacity (free): File → Import → Audio, both at once. They line up, and the waveforms show the agent waiting through the correction and answering after. Next to it, the trace (`traces/<room>.jsonl`): the heard segments, `thinker_listen` or `superseded`, the one tool call with the corrected value. |
| 1:40-2:10 | Optionally, the same live: talk to the agent yourself with the benchmark's tools ("track my order 1234, no wait, 1243"), interrupt it mid-reply | Section 11.2 |
| 2:10-3:40 | **The extension, live, one take:** change of mind, barge-in, a slow tool, exactly-once, an honest failure | Section 10 |
| 3:40-4:20 | Architecture diagram, the results table vs the baselines, what's next | The README's mermaid diagram, rendered on GitHub |

Record on a quiet machine. Say what is real and what is mocked.

### 11.2 Talking to the agent live (LiveKit Cloud + browser)

The agent runs on the GPU machine. You talk to it from your laptop's browser
through a LiveKit Cloud project. We have not tried this on the DGX yet, so do a
trial run before recording.

On the GPU machine, after `reproduce.sh` has installed everything, open two tmux
windows. **Stop any benchmark run first**: the agent joins every new room in the
project.

```bash
# window 1: the model server (foreground; Ctrl-C stops it)
cd /raid/$USER/duet
export CUDA_VISIBLE_DEVICES=3            # the same idle GPU in both windows
source scripts/env.sh
"$PY_LLM" bench/llm_server.py serve      # ready when it prints "Model server: ready"
```

```bash
# window 2: the agent
cd /raid/$USER/duet
export CUDA_VISIBLE_DEVICES=3
source scripts/env.sh
export LIVEKIT_URL=wss://<your-project>.livekit.cloud
export LIVEKIT_API_KEY=<key> LIVEKIT_API_SECRET=<secret>        # typed here, never committed
export LD_LIBRARY_PATH="$(ls -d "$PWD"/.venv/lib/python3*/site-packages/nvidia/*/lib | paste -sd: -)${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export DUET_TRACE_DIR="$PWD/results/demo_traces"                # traces to show in the video
"$PY_AGENT" -m duet_voice.agent start                           # ready at "registered worker"
```

The `LD_LIBRARY_PATH` line lets the speech recognizer find its CUDA libraries.
Without it, it falls back to the CPU and gets slow.

Then, on your laptop, open LiveKit's **Agents Playground**
(agents-playground.livekit.io), or a sandbox app from the LiveKit Cloud
dashboard. Connect to your project, allow the microphone, and talk. The agent
joins the room within a second or two.

For reference, a 3-item dry run over LiveKit Cloud measured 3.55 s to the
agent's first response, as the benchmark counts it (including its ~1.9 s
recorder offset).

### 11.3 The slides (at most 8)

The guide asks for the problem, the architecture, the benchmark results and
what we would do next. A plan:

1. **The problem.** Voice agents act on half-said, corrected words; the paper's
   finding; best system under 60% Pass@1.
2. **What FDB-v3 measures** and why self-correction is the hardest part
   (baselines table).
3. **DUET architecture.** The diagram: ears, talker, thinker, coordinator,
   mouth, one GPU.
4. **The coordinator.** A timeline of one self-correction: epoch, commit gate,
   keep listening, one call. Plus the ledger (exactly once).
5. **Built to reproduce.** One command, no API key, one 48 GB GPU, pinned
   everything, peak memory measured, logs.
6. **Results.** Our row vs the three baselines; Pass@1 by disfluency;
   latency and interruptions. Label the judge.
7. **The extension.** What it is, why it needs full duplex, a screenshot.
8. **Next.** What we'd improve (from the failures in section 7), limitations,
   Round 2 plans.

---

## 12. Submitting

### The repository must be readable by Samsung

The repository is **private**. Before submitting, confirm with Tanmay (or the
organisers) how Samsung gets access:
- make it public;
- add their GitHub account;
- or upload an archive.

A link the judges cannot open is as bad as a script that doesn't run.

### Final checklist

- [ ] `main` has the final code; `git status` is clean and pushed.
- [ ] `.venv/bin/python -m pytest tests -q` passes.
- [ ] The **latest `main`** has run end to end on a machine that is not ours with
      `bash reproduce.sh` (at least `--only travel_19_695bd157114f0d2317f88617`
      on a fresh clone after the last code change).
- [ ] `results/reported/<run>/` holds the reported run; the README table matches
      its `summary.json`.
- [ ] README: architecture diagram, setup and run steps, model declaration, keys
      section ("none required"), results, **extension clearly marked**.
- [ ] `docs/SAMSUNG_REQUIREMENTS.md` statuses are current.
- [ ] No secrets anywhere in the history. This prints `0` (it did on 28 Sep):
      `git log -p --all --no-textconv | grep -cE "sk-(proj-)?[A-Za-z0-9_-]{20}|AIza[0-9A-Za-z_-]{30}|AQ\.[A-Za-z0-9_-]{20}"`
- [ ] `HANDOVER.md` deleted or moved to `notes/` (it is internal).
- [ ] Video: 3-5 min, benchmark interruption then extension; uploaded, and the
      link opens without signing in.
- [ ] Slides: at most 8, as PDF.
- [ ] Google Form submitted before the deadline. The last upload counts, so
      re-submit if anything changes.

---

## 13. The fallback: the Gemini version

**Use it only if the local model cannot be made to work on the GPU machine in
time.** One example: vLLM won't start and the logs don't lead to a fix.

- The branch `gemini-ready` (commit `12349c9`) is the complete Gemini version,
  and `main` still supports it: `DUET_LLM_BACKEND=gemini`.
- It needs a **billing-enabled** Google key. The free tier (20 requests per model
  per day) cannot finish a run: 100 items need several hundred requests.
- Models: `gemini-3.7-flash` (thinker) and `gemini-3.5-flash-lite` (talker).
- Run it with:
  ```bash
  export GOOGLE_API_KEY=<key> DUET_LLM_BACKEND=gemini
  bash reproduce.sh --force
  ```
  The preflight checks the key first. A `no_credit` verdict means the prepaid
  balance is empty.
- For the submission, Samsung's re-run must also use Gemini without our key. The
  README would then declare a hosted API, and the default in
  `duet_voice/config.py` would switch to `gemini`. That is a bigger change:
  **decide it with Tanmay.** Samsung has said they have their own Gemini keys; an
  email asking whether they can supply one was drafted.

---

## 14. Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| Doctor: `GPU N has only … MiB free` | Someone else is using that GPU | `export CUDA_VISIBLE_DEVICES=<idle index>` and re-run |
| Doctor: `no C compiler (gcc)` | vLLM needs gcc to build its kernels | Ask the admin for `build-essential`, or point `CC` at a gcc |
| `Could not download the benchmark data` | Google Drive is throttling the file | Download it in a browser from the link in the FDB-v3 README, copy it to `third_party/downloads/fdb_v3_data_released.zip`, re-run |
| `The model server exited while starting` | Usually out of memory (a busy GPU); sometimes a driver or compiler issue | Read the printed log tail; the full log is `results/live/<time>/llm_server.log`. Free GPU → re-run. Otherwise send the log. |
| Model server not ready after a long time | The first start compiles kernels (3-5 min); downloads resume | Wait up to 30 min (`DUET_LLM_START_TIMEOUT`, in seconds); check `llm_server.log` |
| `Preflight failed` / `Tool calling: unexpected` | The model answers but not with a proper tool call | Send `llm_server.log` and the screen output to Tanmay |
| `Cannot write /tmp/agent_tool_calls.log` | Another user on the machine owns that file | The benchmark needs that exact path: ask them to remove it, or use another machine |
| Another team member runs FDB-v3 on the same machine at the same time | Both write `/tmp/agent_tool_calls.log` | Don't: one run per machine at a time |
| `WARNING: N recordings already have results…` | You re-ran without `--force` | Re-run with `--force` (section 6.4) |
| `turn_take_rate` well below 1.0 | The agent didn't speak in some items | Check `agent.log` for tracebacks, and `agent.fallback_acks` and `thinker_errors` in `summary.json` |
| `thinker_errors` > 0 | Model calls failed | `first_thinker_error` in `summary.json`, then `llm_server.log` |
| Agent log: whisper `on cpu` | The recognizer didn't find CUDA | In `reproduce.sh` runs this is handled; in the live demo, set the `LD_LIBRARY_PATH` line (section 11.2) |
| `ours_peak_gib` above 43 | Would not fit Samsung's 48 GB card | Tell Tanmay before anything else. (`peak_used_gib` counts other users' jobs too; ignore it on a shared machine.) |
| Port 18000 taken | Another server on the machine | `export DUET_LLM_BASE_URL=http://127.0.0.1:18011/v1` (any free port) |
| A download stops half-way | Network | Re-run the same command; it resumes |
| Need to stop everything | | `Ctrl-C` in tmux, then the `pkill` line in section 6.6 |
| `git push` rejected | Someone pushed first | `git pull --no-rebase origin main`, resolve if needed, push again. Never `--force`. |

When you ask Tanmay for help, send:
- the command you ran;
- the last 30 lines of output;
- the run folder's `llm_server.log` and `agent.log` (or the `.tar.gz` from
  `collect_results.sh`);
- `doctor_report.txt`.

---

## 15. Reference: commands, settings, files

### Commands

```bash
bash scripts/doctor.sh                                   # is this machine ready?
bash reproduce.sh --only travel_19_695bd157114f0d2317f88617   # install + one recording
bash scripts/offline_eval.sh [--text script] [--tag X]   # fast accuracy, all 100
bash reproduce.sh --force                                # full run, fresh
bash reproduce.sh --dry-run --only travel_19             # no model: listening and plumbing
bash scripts/collect_results.sh [--push <name>]          # pack (and push) the latest run
.venv/bin/python bench/publish_run.py results/live/<time> --name <name>   # publish a run
.venv-llm/bin/python bench/llm_server.py plan            # which weights/budget this GPU gets
.venv-llm/bin/python bench/llm_server.py health          # is the model server up?
.venv/bin/python bench/cost_report.py results/live/<time>                 # tokens used
.venv/bin/python -m pytest tests -q                      # tests (install pytest once, section 8)
```

**Without a GPU** (your laptop, for code changes): create an environment and run
the tests.

```bash
uv venv --python 3.11 .venv
uv pip install -r requirements.lock pytest       # uv installs into ./.venv
.venv/bin/python -m pytest tests -q        # Windows: .venv\Scripts\python -m pytest tests -q
```

`tests/fake_openai.py` is a stand-in model server that speaks the same protocol
as vLLM. With it, the model-driven path can run on a laptop (see "Develop" in the
README), but that also needs the benchmark data and a LiveKit server locally.

### Settings (environment variables)

Samsung's re-run uses the defaults. Environment variables are for experiments;
change defaults in code (rule 4).

| Variable | Default | Meaning |
|---|---|---|
| `CUDA_VISIBLE_DEVICES` | the GPU with the most free memory | Which GPU (nvidia-smi numbering) |
| `DUET_LLM_BACKEND` | `local` | `local` (vLLM + Qwen) or `gemini` |
| `DUET_LLM_BASE_URL` | `http://127.0.0.1:18000/v1` | Model server address (change the port if 18000 is taken) |
| `DUET_LLM_MODEL` | `qwen3-30b-a3b-instruct-2507` | Served model name |
| `DUET_LLM_VARIANT` | `w4a16` | `fp8` only for comparison runs (Ada/Hopper GPUs) |
| `DUET_LLM_GPU_GIB` | 22 (w4a16) / 33 (fp8) | Model server memory budget (don't raise it: rule 6) |
| `DUET_LLM_GPUS` | unset (the same GPU as everything else) | Shared machines: the model server's GPUs; two GPUs split the model |
| `DUET_AGENT_GPUS`, `DUET_SCORING_GPUS` | unset | Shared machines: the GPU for the agent's speech models, and for the benchmark's scoring recognizer |
| `DUET_LLM_MAX_LEN` | 12288 | Context length (don't raise it) |
| `DUET_LLM_START_TIMEOUT` | 1800 | Seconds to wait for the model server |
| `DUET_TEMPERATURE` | 0.7 (local) | Sampling temperature |
| `DUET_SEED` | 7 | Seed on every model call |
| `DUET_JUDGE` | auto | `openai`, `local`, `gemini` or `none` |
| `DUET_COMMIT_HOLD` / `DUET_REVISING_HOLD` / `DUET_DANGLING_HOLD` | 1.1 / 1.8 / 2.2 s | Quiet time before a tool may run |
| `DUET_ENDPOINT_MIN` / `DUET_ENDPOINT_MAX` | 0.8 / 2.5 s | End-of-turn delays |
| `DUET_TALKER_TIMEOUT` | 1.2 s | After this, "One moment." instead of the talker's line |
| `DUET_TRACE_DIR` | set per run | Where conversation traces go |
| `FDB_V3_DIR`, `FDB_DATA_DIR` | under `third_party/` | The benchmark and its audio |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | unset (local server) | LiveKit Cloud instead |
| `OPENAI_API_KEY` | unset | The official gpt-4o judge |
| `GOOGLE_API_KEY` | unset | Only with `DUET_LLM_BACKEND=gemini` |

### Pinned facts

| Thing | Pin |
|---|---|
| Benchmark | Full-Duplex-Bench commit `3e799c45a045256f47d5f1c9cda90157e2d2ec9e` |
| Benchmark audio | SHA-256 `37545bd8…` (checked by `reproduce.sh`) |
| LiveKit server | v1.13.7 |
| uv / Python | 0.12.19 / 3.11 |
| Model weights (FP8, comparison runs only) | `Qwen/Qwen3-30B-A3B-Instruct-2507-FP8` @ `5a5a776` |
| Model weights (4-bit) | `RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16` @ `e9c59cd` |
| Model server | vLLM 0.19.1, torch 2.10 (CUDA 12.8 build), transformers 5.5.4 |
| Agent | livekit-agents 1.8.3; faster-whisper large-v3-turbo @ `0a363e9`; Kokoro-82M @ `f3ff357` |

---

## 16. Glossary

| Term | Meaning |
|---|---|
| **FDB-v3** | Full-Duplex-Bench v3, the benchmark that is 60% of the score |
| **Full duplex** | Listening and speaking at the same time, as people do, instead of strict turns |
| **Pass@1** | Share of items where every tool call was exactly right; the tie-breaker |
| **LiveKit** | The real-time audio framework the benchmark uses. A *room* is one conversation; our *agent* (a *worker*) joins each room |
| **VAD** | Voice activity detection: is someone speaking (Silero) |
| **STT / ASR** | Speech to text (faster-whisper for the agent, Parakeet for scoring) |
| **TTS** | Text to speech (Kokoro-82M) |
| **End of turn** | Deciding the user has finished; LiveKit's turn detector plus our holds |
| **Talker / thinker** | DUET's two minds: the talker acknowledges quickly, the thinker reasons and calls tools |
| **Coordinator** | Decides when acting is allowed: epochs, commit gate, ledger |
| **Epoch** | A counter that advances when the user says more. Plans from an older epoch are discarded |
| **Commit gate / hold** | No tool call until the turn is closed and the user has been quiet for the hold |
| **Idempotency ledger** | Remembers performed actions, so an identical one is never done twice |
| **keep_listening** | A pseudo-tool the thinker calls when the user hasn't finished |
| **vLLM** | The server that runs the language model on the GPU with an OpenAI-compatible API |
| **MoE** | Mixture of experts: a big model where only a small part runs per token (fast) |
| **FP8 / w4a16** | 8-bit and 4-bit weight formats; smaller and faster than 16-bit |
| **GiB vs GB** | 1 GiB = 1.074 GB. A "48 GB" card has about 44.7 GiB |
| **Judge / proxy judge** | An LLM that grades arguments and answers. Official: gpt-4o. Ours without a key: local Qwen, labelled "proxy" |
| **uv / lock file** | uv installs Python packages fast; a lock file pins every package version exactly |
| **tmux** | Keeps terminal sessions running after SSH disconnects |
| **Dry run** | A run with no language model (the agent says "Okay."), to test listening and plumbing |
| **Offline eval** | The thinker alone on transcripts, officially scored: minutes instead of hours |

---

*Questions: ask Tanmay first (Tanmay is in a different time zone, so send logs, not
descriptions). For anything about the machine, ask the DGX owner. For rules, the
deadline or the form, ask the organisers.*
