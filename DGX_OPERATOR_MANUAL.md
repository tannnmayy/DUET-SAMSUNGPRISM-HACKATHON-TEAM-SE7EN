# DUET operator manual: running it on a DGX, and what to send back

This manual is for the person running DUET on a DGX (or any Linux GPU server), and
for the AI assistant helping them. It is self-contained. It explains:
- the project;
- the rules;
- every command and what it should print;
- what to save;
- what to send back to Tanmay.

It works on a DGX whose GPUs are free (for example an H100 box) and on a shared one
whose GPUs are partly used by other people (for example SRM's DGX A100).

**Deadline: 1 Oct 2026.** Tanmay needs results by the evening of 30 Sep to write
them up.

---

## How to use this manual with your AI assistant

At the start of every session, give your assistant this whole file (attach it or
paste it), with this message:

```
I am running the DUET project on a DGX. The attached manual is the source of truth.
Read "Part 1. Instructions for the AI assistant" first and follow it strictly.
I will paste the exact output of each command. Tell me the next step from the
manual, check the output against its "You should see" sections, and use its
troubleshooting table. Do not suggest anything the manual forbids.
```

Then:
- **Paste exact output.** Copy the terminal text; screenshots work, but text is
  better.
- **Say which part and step you're on,** for example "Part 7.3, the full live run,
  step 2".
- **If the assistant suggests something not in this manual,** especially editing
  code, starting vLLM with your own flags, running speech on the CPU, or changing
  the scoring, don't do it. Stop and ask Tanmay.

---

## Part 1. Instructions for the AI assistant

### 1.1 Your role

You are helping a teammate run a prepared, tested pipeline on a GPU server and store
the results. You can't see the machine; you only see what the operator pastes. Your
job:
- give the next command from this manual;
- compare the output with the manual's expected output;
- diagnose problems with Part 10 (troubleshooting);
- make sure every result is saved and pushed (Part 8).

The code is finished and tested. Nothing needs designing, tuning or editing here.
When a problem is not covered by this manual, the right move is to collect the
logs and send them to Tanmay (Part 8.6), not to improvise.

### 1.2 Never suggest these

Each of these has caused real problems or would break competition rules:

1. **Editing any file in the repository.** That covers code, scripts, configs and
   lock files. The only file the operator writes is `results/journal.md`. Fixes come
   from Tanmay through `git pull`.
2. **Starting vLLM (the model server) by hand** with `python -m
   vllm.entrypoints...` and flags of your own. Use `bench/llm_server.py serve`,
   `reproduce.sh` or `scripts/experiments.sh`: they pick the right weights and
   memory, record the settings, and write logs. On 28 Sep a hand-typed command
   worked but left no record of what ran.
3. **Running the agent's speech recognition on the CPU for a scored run**
   (`DUET_ASR_DEVICE=cpu`). On the CPU the agent switches to a much smaller model
   (`small.en`), which misheard "June 3" as "June 1" on 28 Sep. For runs we report,
   speech stays on a GPU. Use `bench/place_gpus.py` to find one.
4. **Changing the benchmark's scoring** (`--scoring-asr whisper`, or anything that
   replaces NVIDIA Parakeet). Reported runs must use the benchmark's own scorer.
5. **Changing model settings to "make it fit".** That includes
   `--gpu-memory-utilization`, `DUET_LLM_GPU_GIB`, `DUET_LLM_MAX_LEN`, the model,
   the variant, the temperature, the seed and the prompts. The memory plan is
   designed for Samsung's 48 GB GPU. On a crowded machine, place the pieces on
   other GPUs (`place_gpus.py`) or wait.
6. **Training or fine-tuning anything.** Samsung disqualifies teams that train on
   the benchmark's test items, and there's no other training data. Every "run" here
   is an evaluation.
7. **Modifying anything under `third_party/`** (the benchmark's code). Samsung runs
   it unmodified.
8. **Deleting results.** Never delete `results/live/*`, `results/offline/*` or a
   failed run. A failed run with its logs is evidence.
9. **Running two live benchmark runs at the same time on one machine.** They share
   `/tmp/agent_tool_calls.log`, and a full run clears it at the start.
10. **Killing other users' processes,** or `pkill` without `-u $USER`.
11. **`git push --force`, `git reset --hard`, `git stash`, `git checkout -- .`,
    or committing to `main`.** Results go to their own branches through
    `scripts/push_results.sh`.
12. **Pasting API keys** into commands, files or chat. None are needed.

### 1.3 How to diagnose

1. Find the step in Part 7 and compare the output with "You should see".
2. If it differs, look the message up in Part 10.
3. If Part 10 doesn't cover it, ask for these and nothing more:
   - the command;
   - the last 40 lines of output;
   - for model-server problems: `tail -80` of the relevant `llm_server.log`;
   - for run problems: `tail -80` of the run folder's `runner.log` and `agent.log`;
   - `nvidia-smi` (the full table).
4. Still unclear: tell the operator to push what exists
   (`bash scripts/push_results.sh <name>_failed`) and send Tanmay the handoff
   message (Part 8.6). Don't propose code changes.

### 1.4 What happened on 28 Sep (so you don't repeat it)

Machine: SRM's DGX A100, 8 × 40 GB, shared.

| What happened | Why | The rule or fix now |
|---|---|---|
| The model server refused to start: "Free memory on device (14.9/39.49 GiB) … less than desired (21.72 GiB)" | Other users' jobs filled every GPU halfway | `bench/place_gpus.py` places the model (split over two GPUs if needed) and the other pieces on GPUs with room |
| An assistant hand-launched vLLM split over two GPUs | No launcher support for that then | `DUET_LLM_GPUS` does it through the launcher now (rule 2) |
| Speech was put on the CPU to save GPU memory; the agent misheard a date | CPU speech uses a small model | Rule 3 |
| A "full run" ended after one minute with `total: 1` | The benchmark's runner crashed loading its scorer onto a full GPU, and old results were scored | The run now stops if the runner fails; the scorer can be placed on its own GPU (`DUET_SCORING_GPUS`) |
| `.venv-bench/bin/python` not found | A relative path, typed by hand | Fixed in the code; and rule 2: use the scripts |
| GPU peak "31.9 GiB" and "39.5 GiB" reported | Those counted other users' jobs too | Use `ours_peak_gib` in `summary.json` (our own processes only) |

What worked that day: the install, the model server, tool calling, one live
recording end to end, and an offline evaluation. Pass@1 was 0.63 on the exact
scripts, graded by the model itself (the proxy judge).

### 1.5 Normal things that look like errors

- **`scripts/doctor.sh` prints `FAIL` for GPU memory on a shared machine.** Expected
  when other jobs use the GPUs; `place_gpus.py` decides what's possible.
- **Hugging Face `HTTP Request:` lines, and deprecation warnings** (for example
  about `HF_HUB_ENABLE_HF_TRANSFER` or torch APIs).
- **Long silences.** A live run streams each recording in real time, about 70 s
  each, and prints little.
- **Stray characters like `^[[A`** from arrow keys.
- **`Continuing: N recordings were recorded but never scored; they run again.`**
  This is normal when a crashed live run continues.
- **`WARNING: ... thinker errors`** is *not* normal if the count is more than a
  handful (see Part 10).
- **In offline evaluations, `FAIL <item> ...` lines** list the items that missed.
  That's expected; about 30-40 of 100 fail.

---

## Part 2. The project in brief

### 2.1 The hackathon

Samsung PRISM GenAI Hackathon 3.0, Theme 05: Interruptible Real-Time Agents. Team
SE7EN (SRM). The aim is a voice agent that:
- stays responsive;
- works in the background while talking;
- recovers cleanly when the user changes their mind mid-sentence, without
  repeating actions.

**Score = 0.6 × benchmark + 0.2 × use-case extension + 0.2 × documentation and video.**
- The benchmark part is scored by **Samsung re-running our `reproduce.sh`** on their
  own machine: one NVIDIA 48 GB GPU, CUDA 12.x or 13.x. If it doesn't run there,
  that 60% is zero.
- We must also submit our own results and **run logs** ("scores, seeds,
  configuration"). That's what your runs produce.
- Rules: no hardcoding or training on test items, no calls to our own servers
  during the evaluation, nothing cached between conversations, seeds and versions
  pinned, and the reproduction tested on a machine that isn't ours (yours).

### 2.2 The benchmark: Full-Duplex-Bench v3 (FDB-v3)

- **The recordings.** 100 real recordings of people asking for help (orders,
  cards, apartments, flights) with natural disfluency: "um", pauses, false starts,
  self-corrections ("order 1234, no wait, 1243"). Each is 36-59 s: the request,
  then about 30 s of room noise.
- **The tools.** 12 mock tools (`track_order`, `search_flights`, `book_flight`, …).
  An item passes (**Pass@1**) only if the agent called exactly the expected tools
  with the right arguments: no extra call, none missing.
- **How a run works.** The benchmark's runner plays each recording into a LiveKit
  room in real time. Our agent listens, calls tools and speaks. The runner records
  the reply, and NVIDIA's Parakeet recognizer transcribes both sides. Three
  scripts score tool choice, arguments, answer quality (with an LLM judge), Pass@1,
  latency and interruptions.
- **Published results to beat:** GPT-Realtime Pass@1 0.600, Gemini Live 0.540, a
  Whisper→GPT-4o cascade 0.450.

### 2.3 DUET, the agent

A custom LiveKit voice agent with two "minds" and a coordinator:
- **Ears and mouth:** Silero voice detection, faster-whisper large-v3-turbo speech
  recognition (GPU), LiveKit's end-of-turn model, Kokoro-82M text-to-speech (GPU).
- **Talker:** says one truthful acknowledgement while work happens ("Let me check
  that order").
- **Thinker:** reads the whole request and calls tools. It can decide the user
  hasn't finished (`keep_listening`).
- **Coordinator:**
  - no tool runs until the user has finished and paused;
  - plans made on words the user has since corrected are discarded;
  - the same action is never done twice.
- **Language model:** both minds use **Qwen3-30B-A3B-Instruct-2507** (open
  weights), running locally on the same GPU via **vLLM 0.19.1**, in Red Hat's
  **4-bit build** (`RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16`). No API
  key, no cost.

### 2.4 Memory: why 48 GB is enough

| Piece | GPU memory |
|---|---|
| Model server (vLLM, 4-bit Qwen, fixed budget) | 22 GiB |
| Agent's speech models (Whisper, Kokoro) | ~3.5 GiB |
| The benchmark's scorer (Parakeet) | ~4-5 GiB |
| **All together** | **~31 GiB** (a 48 GB card has ~44.7 GiB usable) |

Every live run measures `ours_peak_gib`: the memory of our own processes, summed
over all GPUs they used. **At or below 43 means it fits Samsung's card.**

### 2.5 What your runs are for

| Run | Gives us | Part |
|---|---|---|
| Offline experiments | How good the model's decisions are, how much scores vary by chance, whether temperature 0 is better, what goes wrong | 7.2 |
| A full live run | The real benchmark numbers and logs for all 100 recordings | 7.3 |
| A clean single-GPU run | Samsung's exact layout: the run we report | 7.3, path F |
| A final fresh-clone check | Proof the final code installs and runs on a machine that isn't ours | 7.6 |

---

## Part 3. What runs where

**Processes during a live run** (all started by `reproduce.sh`, which calls
`bench/run_live.py`):

| Process | What it is | GPU | Where it logs |
|---|---|---|---|
| Model server | vLLM serving Qwen on `127.0.0.1:18000` | yes, 22 GiB (or 12.5 GiB on each of two GPUs) | `results/live/<time>/llm_server.log` |
| LiveKit server | Local audio rooms on free ports (7880 or higher) | no | `livekit_server.log` (large, never published) |
| Agent | `python -m duet_voice.agent start`: listens, thinks, calls tools, speaks | yes, ~3.5 GiB | `agent.log`, `traces/*.jsonl` |
| Benchmark runner | The benchmark's own script: plays recordings, records replies, runs Parakeet | yes, ~4-5 GiB | `runner.log` |
| Evaluations | The benchmark's three scoring scripts, then our summary | no | `eval_*.log`, `summary.json` |

**Python environments** (created by `reproduce.sh` from lock files):
- `.venv`: the agent and our tools;
- `.venv-bench`: the benchmark runner and NeMo/Parakeet;
- `.venv-llm`: vLLM.

**Downloads** go into `third_party/` inside the repository (about 60 GB with the
environments).

**Important paths:**

| Path | What |
|---|---|
| `results/live/<YYYYmmdd_HHMMSS>/` | One live run: everything about it |
| `results/offline/eval_<text>_<time>_<tag>.json` | One offline evaluation |
| `results/offline/failures/*.md` | Each evaluation's failures, grouped |
| `results/offline/EXPERIMENTS.md`, `experiments.log` | The table of all results; one line per experiment |
| `results/llm_server/llm_server.log` | The log of a server started with `llm_server.py serve` |
| `results/journal.md` | Your journal (the only file you write) |
| `third_party/Full-Duplex-Bench/v3/fdb_v3_data_released/<item>/` | The recordings; after a run also `output_duet.wav` (the agent's voice) and `result_duet.json` |
| `/tmp/agent_tool_calls.log` | Where the benchmark reads tool calls (fixed by the benchmark) |

---

## Part 4. One-time setup on your DGX

Skip what's already done. On SRM's DGX (`srmist1@172.16.0.32`), everything up to
4.7 was done on 28 Sep; continue at Part 5.

### 4.1 Does the machine qualify?

```bash
uname -m; cat /etc/os-release | head -2
nvidia-smi
which git curl gcc tar tmux
df -h ~
```

**You need:**
- `x86_64` Linux;
- an NVIDIA driver showing CUDA 12.x or 13.x;
- `git`, `curl`, `gcc`, `tar` and `tmux` present;
- about **80 GB free** on the disk you'll use.

A GPU with **34 GiB (34,000 MiB) free** runs everything on one card. With less, the
shared-machine path splits it up (Part 6).

### 4.2 Where to put the repository

Use the big disk your account can write to. On a DGX that is often `/raid/<you>`.
If `/raid` isn't writable, your home directory is fine if it has space: on SRM's
DGX, home had 8 TB free and `/raid` was root-only, so the repository is `~/duet`.
Below, "the repository folder" means wherever you cloned it.

### 4.3 GitHub access

The repository is private: `github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN`.
1. Tell Tanmay your GitHub username. He adds you with **Write** access, and you
   accept the email invitation.
2. On the DGX, set your identity and make an SSH key:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@users.noreply.github.com"
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

3. On GitHub: Settings → SSH and GPG keys → New SSH key. Paste that line and save.
4. Test it: `ssh -T git@github.com` should say `Hi <you>! You've successfully authenticated`.

### 4.4 Clone

```bash
cd /raid/$USER 2>/dev/null || cd ~
git clone git@github.com:tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet
cd duet
git log --oneline -1
```

### 4.5 tmux (keeps runs alive when your connection drops)

```bash
tmux new -s duet          # later: tmux attach -t duet
```

| Key | Does |
|---|---|
| `Ctrl-b` then `d` | Detach: the work continues; you can disconnect |
| `Ctrl-b` then `c` | New window |
| `Ctrl-b` then `0` / `1` / `2` | Switch windows |
| `Ctrl-b` then `[` | Scroll mode (arrows); `q` leaves it |
| `Ctrl-q`, then `q`, then `Esc` | Unfreeze a stuck screen |

**Use three windows:**
- **0** for the model server;
- **1** for your commands;
- **2** for watching (`watch -n 10 nvidia-smi`).

Everything below happens inside tmux.

### 4.6 Machine check

```bash
cd <the repository folder>
bash scripts/doctor.sh
```

**You should see** `OK` lines and `Ready.` at the end. It writes
`doctor_report.txt`, which is published with your results.
- A GPU `FAIL` on a shared machine is expected (Part 1.5).
- Any other `FAIL` line says how to fix it. If you can't, send `doctor_report.txt`.

### 4.7 First install, and one recording (about 45-60 minutes)

```bash
eval "$(python3 bench/place_gpus.py)" && bash reproduce.sh --only travel_19_695bd157114f0d2317f88617
```

`place_gpus.py` chooses the GPUs, and the `&&` means nothing starts if there's no
room. **You should see** progress lines starting with `==`:
- `uv`, then the three Python environments (~15 min);
- the benchmark, its audio (736 MB), ffmpeg, LiveKit (~3 min);
- speech models, the language model weights (~17 GB), Parakeet (~5-15 min).

Then, near the end:

```
Model server: RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16 (w4a16, 22.0 GiB budget on NVIDIA ..., GPU N); log: ...
Model server: ready at http://127.0.0.1:18000/v1
Models: qwen3-30b-a3b-instruct-2507 (thinker), qwen3-30b-a3b-instruct-2507 (talker)
Tool calling: ok
LiveKit: local server up on port 7880 (pid ...)
Agent: registered and warm (pid ...)
Runner: ...
Eval: tool_calls
Eval: pass_rate
Eval: latency
{ ... "turn_take_rate": 1.0, ... "total": 1 ... }
Run folder: .../results/live/<time>
GPU ...: peak xx.x GiB used of xx.x GiB (the whole card, other users' jobs included)
This run's own processes: peak xx.x GiB over all GPUs used (fits a 48 GB card at 43 GiB or less)
```

**Healthy:** `Tool calling: ok` and `"turn_take_rate": 1.0`. One recording can pass
or fail (Pass@1 1 or 0); either is fine here. This step proves the install. Then
push it: `bash scripts/push_results.sh setup_check` (Part 8).

Re-running is safe: finished install steps are skipped.

---

## Part 5. Every session

### 5.1 Start (5 minutes)

```bash
ssh <you>@<dgx-host>
tmux attach -t duet || tmux new -s duet
cd <the repository folder>
git status --short
git pull
source scripts/env.sh
python3 bench/place_gpus.py --show
```

- **`git status --short`** should print nothing, or only `??` lines (new files like
  your journal). An `M` line means a tracked file was edited on this machine:
  stop, and send it to Tanmay.
- **`git pull`** brings Tanmay's latest fixes. Always do it before starting a run.
- **`place_gpus.py --show`** prints the GPUs and where DUET would run right now
  (Part 6).

Add a journal entry (Part 8.5).

### 5.2 End

1. `bash scripts/push_results.sh <session-name>`: stores everything (Part 8).
2. Finish the journal entry, then push again.
3. Stop the model server if it's running (window 0, `Ctrl-c`), so you're not holding
   GPUs others need.
4. Copy the backup archive off the machine (Part 8.4).
5. Detach: `Ctrl-b d`.

---

## Part 6. Choose your path: free GPU or shared GPUs

`python3 bench/place_gpus.py --show` ends with one of these:

**`Everything on GPU N: the same layout as Samsung's re-run.`** One GPU has at
least 34,000 MiB free. Use **path F** (free) in Part 7. Your live runs are
Samsung's layout, and the clean run is the one we report.

**`No single GPU is free enough; model server on GPU a,b (split over 2), agent's speech on GPU c, scoring recognizer on GPU d.`**
Use **path S** (shared). The pieces go where memory is free:

| Piece | Needs free |
|---|---|
| Model on one GPU | 23,100 MiB |
| Model split over two GPUs | 13,100 MiB each |
| Agent's speech | 5,000 MiB |
| Scorer | 6,000 MiB |

Results are still valid and comparable: `ours_peak_gib` adds the pieces up. But
latency is slower on busy GPUs, so the run is labelled as shared.

**`No room for the model ...`** Nothing fits right now. Wait 15-30 minutes, check
again, or ask the machine's admin or other users for one GPU for 3 hours
(`nvidia-smi` shows who uses what). Shared machines are often quieter late at
night.

On an H100 machine with free GPUs you will almost always get path F.

---

## Part 7. The runs, in order

Run them in this order. Push after each one (Part 8).

### 7.1 Start the model server, for offline work (window 0, about 5 minutes)

```bash
cd <the repository folder> && git pull && source scripts/env.sh \
  && eval "$(python3 bench/place_gpus.py --offline)" \
  && .venv/bin/python bench/llm_server.py plan \
  && .venv/bin/python bench/llm_server.py serve
```

**You should see:**
- **The plan:** JSON with `"variant": "w4a16"`, `"budget_gib": 22.0`, the GPUs, and
  on a split `"tensor_parallel": 2`.
- **Then:** `Model server: ... ; log: .../results/llm_server/llm_server.log`, and
  after 1-5 minutes `Model server: ready at http://127.0.0.1:18000/v1` and
  `Serving; Ctrl-C stops it.`

Leave window 0 alone. If it fails, see Part 10 (model server rows).

### 7.2 Offline experiments (window 1, about 1 hour)

The model's decisions on all 100 recordings, without the real-time streaming, scored
by the benchmark's own scorers. They use the server from 7.1.

```bash
cd <the repository folder> && source scripts/env.sh && bash scripts/experiments.sh baseline
```

**What runs:**

| Tag | Test | About |
|---|---|---|
| `noise_s7`, `noise_s8`, `noise_s9` | The same settings, three seeds, exact scripts | 18 min: the chance spread |
| `greedy` | Temperature 0 instead of 0.7 | 6 min: which default is better |
| `asr_t07`, `asr_t0` | On what the agent's own recognizer hears (transcribed once first, 5-15 min) | 25 min: the realistic number |

**For each one you should see:**

```
== noise_s7: --text script (temperature model card, seed 7)
 "pass@1": 0.6x,
 "tool_f1": 0.9x,
 "arg_acc": 0.8x,
 "resp_qual": 0.8x,
 "errors": 0,
wrote results/offline/eval_script_<time>_noise_s7.json
   failures, grouped: results/offline/failures/eval_script_<time>_noise_s7.md
```

It ends with `Done. Every result so far: results/offline/EXPERIMENTS.md`.

**Healthy:**
- `"errors": 0`. Anything above 0 means the model server had problems; see Part 10.
- `pass@1` around 0.6 on scripts (28 Sep: 0.63), and lower on `asr`.

Then:

```bash
cat results/offline/EXPERIMENTS.md
bash scripts/push_results.sh offline_baseline
```

Write the six Pass@1 values in your journal.

**Only on H100, L40S or other Ada/Hopper GPUs** (not A100): the FP8 comparison. The
first time, it downloads the FP8 weights (31 GB) while the server starts.
1. Stop the server in window 0 (`Ctrl-c`).
2. Then in window 1:

```bash
bash scripts/experiments.sh fp8
bash scripts/push_results.sh offline_fp8
```

### 7.3 The full live run: all 100 recordings (window 1, about 2.5 hours)

**First stop the model server in window 0** (`Ctrl-c`): the live run starts its own
and records its log.

```bash
cd <the repository folder> && git pull && source scripts/env.sh \
  && eval "$(python3 bench/place_gpus.py)" \
  && env | grep -E "CUDA_VISIBLE_DEVICES|DUET_(LLM|AGENT|SCORING)_GPUS" \
  && bash reproduce.sh --force
```

- **Path F:** the `env` line shows only `CUDA_VISIBLE_DEVICES=N`. This is the
  **clean run**, Samsung's layout.
- **Path S:** it shows where each piece goes (`DUET_LLM_GPUS=…`, `DUET_AGENT_GPUS=…`,
  `DUET_SCORING_GPUS=…`). Copy that line into your journal.
- **`--force`** makes every recording run again, so the scores come only from this
  run.

**In the first 5-10 minutes you should see** the same lines as in 4.7, up to
`Runner: ... run_tool_benchmark_all_released.py --provider duet ... --force`.
Then it's quiet for about 2 hours. Detach (`Ctrl-b d`) if you like.

**Watch it** (window 2):

```bash
cd <the repository folder>
R=$(ls -dt results/live/*/ | head -1); echo $R
tail -f $R/runner.log      # Ctrl-c stops watching only
ls third_party/Full-Duplex-Bench/v3/fdb_v3_data_released/*/result_duet.json | wc -l    # done, of 100
```

**At the end you should see** the summary JSON, `Run folder: ...` and the two GPU
lines. Print the key numbers:

```bash
.venv/bin/python - <<'PY'
import glob, json, os
run = max(glob.glob("results/live/*/"), key=os.path.getmtime)
s = json.load(open(run + "summary.json"))
print("run:", run)
for k in ("total", "pass_at_1", "tool_selection_f1", "argument_acc", "response_qual",
          "turn_take_rate", "interruption_rate", "first_response_latency_mean_s",
          "task_completion_latency_mean_s"):
    print(" ", k, s.get(k))
a = s["agent"]
print("  thinker errors:", a["thinker_errors"], "| tool calls:", a["tool_calls"],
      "| keep-listening:", a["keep_listening"], "| fallback lines:", a.get("fallback_acks"))
print("  our GPU peak GiB:", s["gpu"].get("ours_peak_gib"), "| self-correction pass:",
      s.get("pass_by_disfluency", {}).get("SELF_CORRECTION"))
PY
```

**Healthy:**

| Value | Expected |
|---|---|
| `total` | 100 |
| `turn_take_rate` | 0.95-1.0 |
| `thinker errors` | 0, or a handful at most |
| `tool calls` | roughly 150-250 |
| `our GPU peak GiB` | ~31 on one GPU, ~31-35 when the model is split. At or below 43 fits Samsung's card |
| `pass_at_1` | unknown until now. Report it whatever it is; the offline numbers suggest roughly 0.4-0.6 |

Then:

```bash
bash scripts/push_results.sh live_full_<F-or-S>      # e.g. live_full_F
```

Send Tanmay the printed numbers and the branch name straight away (Part 8.6).

**If it stops early:**
- **`The benchmark's runner failed ... so nothing is scored`,** followed by the end
  of `runner.log`. Usually a GPU filled up (`CUDA out of memory`).
  1. Run the same block again, but with `bash reproduce.sh` **without `--force`**.
     - Finished recordings are kept, and it continues with the rest.
     - A recording whose reply was recorded but not yet scored when it crashed runs
       again. It prints `Continuing: N recordings were recorded but never scored;
       they run again.`
     - Do this only if `git pull` brought no new code in between.
  2. Note it in the journal.
- **Before `Agent: registered and warm`:** push
  (`bash scripts/push_results.sh live_failed`) and send `agent.log`.

### 7.4 A second clean run (optional, about 2.5 hours, path F only)

If a free GPU is still available and there's time before 30 Sep evening, repeat 7.3
unchanged. Two runs show how much the score moves between runs of the same
system, which makes the reported number credible. Push it as `live_full_F2`.

### 7.5 After Tanmay pushes an improvement (about 15 minutes each)

Tanmay reads your failure files and pushes general fixes. He'll say which name to
use (for example `prompt_v2`). With the model server from 7.1 running:

```bash
cd <the repository folder> && git pull && source scripts/env.sh
bash scripts/experiments.sh one prompt_v2_script --text script
bash scripts/experiments.sh one prompt_v2_asr --text asr
bash scripts/push_results.sh prompt_v2
```

To see which recordings the change fixed and which it broke:

```bash
.venv/bin/python bench/failures.py <new eval json> --compare <old eval json> | head -30
```

If Tanmay asks for a new full live run afterwards, repeat 7.3.

### 7.6 The final fresh-clone check (about 1 hour, when Tanmay says the code is frozen)

This tests exactly what Samsung downloads, in a new folder, with nothing reused:

```bash
cd /raid/$USER 2>/dev/null || cd ~
git clone git@github.com:tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet_final_check
cd duet_final_check
bash scripts/doctor.sh || true
eval "$(python3 bench/place_gpus.py)" && bash reproduce.sh --only travel_19_695bd157114f0d2317f88617
bash scripts/push_results.sh final_check
```

It needs about 50 GB more disk and downloads everything again. Delete the folder
afterwards (`rm -rf duet_final_check`), after the push succeeded.

---

## Part 8. What to save, store and give back

### 8.1 The rule

**After every run, and at the end of every session:**

```bash
bash scripts/push_results.sh <session-name>
```

Names can use letters, digits, `_`, `-` and `.`, for example `offline_baseline`,
`live_full_F`, `h100_0930_am`. Push as often as you like: each push holds
everything so far.

### 8.2 What it stores

It copies into `results/reported/dgx/`, commits that on a new branch
`results-<session>-<time>`, pushes it to GitHub, and returns to your branch. Your
working copy is unchanged.

| Stored as | Contents |
|---|---|
| `live_<time>/` | Each live run: `summary.json` (scores, agent statistics, GPU peaks, git commit), `run_config.json` (every setting; code version, host, GPUs, driver, GPU placement), the benchmark's three reports, `items/` (per recording: transcripts, tool calls, timings), `traces/` (per conversation: everything the agent heard, decided and did), `agent.log`, `runner.log`, `eval_*.log`, `llm_server.log`, `llm_plan.json` |
| `offline_<time>_<tag>/` | Each offline evaluation: the summary (scores, settings, the weights actually served, code version, host) and one row per recording |
| `failures/` | Each offline evaluation's failures, grouped by kind of mistake |
| `EXPERIMENTS.md` | One table of every result, with code version and machine |
| `experiments.log` | One line per experiment |
| `JOURNAL.md` | Your journal |
| `doctor_report.txt`, `machine_<session>.txt` | The machine check; a GPU and disk snapshot |
| `llm_server_latest.log.gz`, `llm_plan_latest.json` | The last `serve` model server's log and plan |

**What it leaves out:**
- audio;
- the LiveKit server's debug log (about 100 MB).

**It refuses to push** anything that looks like an API key.

**Checkpoints.** There are no model checkpoints: nothing is trained. The result
folders are our checkpoints. Each one records the exact git commit it came from,
so any number can be traced to the code that produced it.

### 8.3 Check the push

```bash
git branch -r | grep results- | tail -3
```

Your newest branch should be there. Tanmay sees the same list.

### 8.4 Backup copies

Each push also writes `duet_results_<session>.tar.gz` in the repository folder.
Keep a copy off the machine. On your laptop:

```bash
scp <you>@<dgx-host>:<the repository folder>/duet_results_<session>.tar.gz .
```

Then upload it to the team's Google Drive folder. If a push to GitHub fails (no
access, network), the archive is how the results reach Tanmay.

### 8.5 The journal

Create it once:

```bash
cd <the repository folder>
printf '# DUET on %s: journal\n\n' "$(hostname)" >> results/journal.md
```

Edit it with `nano results/journal.md`: type, then `Ctrl-o`, `Enter` to save,
`Ctrl-x` to leave. One entry per session:

```
## 30 Sep, 09:10-12:40, session h100_0930_am
- Machine and GPUs: 8x H100 80GB, driver 570.xx (CUDA 12.8); GPU 2 free -> path F
- Ran: 7.1 server, 7.2 baseline (pushed offline_baseline), 7.3 live full F (pushed live_full_F)
- Numbers: noise 0.62/0.63/0.61, greedy 0.64, asr 0.55/0.57; live Pass@1 0.52, turn-take 0.99, ours 30.8 GiB
- Problems: none / (what happened, what you did)
- Next: waiting for Tanmay's prompt_v2
```

It's published with the results. It is the story of how the numbers were made:
Samsung asks for run logs, and Tanmay uses it to write the documentation.

### 8.6 What to send Tanmay (a message, after each important run and at the end of each day)

```
DUET update from <machine>, <date time>
Branch: results-<session>-<time>   (backup archive: <Drive link>)
Machine: <GPU model x count>, driver <version> (CUDA <x.y>), path F/S
Done: <parts>
Offline Pass@1: noise <a>/<b>/<c>, greedy <d>, asr t0.7 <e>, asr t0 <f>
Live (if done): total <n>, Pass@1 <p>, tool F1 <t>, turn-take <r>, interrupt <i>, task latency <s> s, thinker errors <e>, our GPU peak <g> GiB, self-correction <sc>
Problems: <none / what happened + which branch has the logs>
Next: <what you will run next / what you need>
```

**If something failed,** also say:
- which step;
- the error line;
- the branch you pushed with `_failed` in its name.

### 8.7 Final deliverables checklist

- [ ] Setup check pushed (4.7), or done earlier
- [ ] Offline baseline pushed (7.2); on Ada/Hopper also `offline_fp8`
- [ ] At least one full live run pushed (7.3); a clean one (path F) if at all possible
- [ ] Optional: a second clean run (7.4)
- [ ] Each improvement Tanmay asked for tested and pushed (7.5)
- [ ] Final fresh-clone check pushed (7.6), when asked
- [ ] Journal up to date and pushed
- [ ] Backup archives on Drive
- [ ] Update message sent (8.6)
- [ ] Model server stopped; nothing of yours left running
  (`ps -u $USER -o pid,cmd | grep -E "vllm|duet_voice|livekit" | grep -v grep` prints nothing)

---

## Part 9. Reading results

### 9.1 A live run's `summary.json`

| Field | Meaning | Healthy |
|---|---|---|
| `pass_at_1` | Share of the 100 items with exactly the right tools and arguments (the headline, and the tie-breaker) | the higher the better; published best 0.600 |
| `tool_selection_f1` | Right tools, partly right counts | ≥ 0.80 |
| `argument_acc` | Right argument values (judge) | ≥ 0.56 |
| `response_qual` | Quality of the spoken answer (judge) | ≥ 0.60 |
| `turn_take_rate` | Share of items where the agent spoke | ~1.0 |
| `interruption_rate` | Share of items where it spoke before the user finished | ≤ 0.15 |
| `first_response_latency_mean_s`, `task_completion_latency_mean_s` | Seconds, as the benchmark measures them (including its ~1.9 s recorder offset) | lower is better; slower on busy GPUs |
| `pass_by_disfluency.SELF_CORRECTION` | Pass@1 on self-corrections, DUET's focus | the paper: 0.588 best, 0.176 cascade |
| `agent.thinker_errors` | Failed model calls | 0 |
| `agent.keep_listening`, `agent.superseded` | Times DUET waited for the user, or dropped a plan the user changed | some: evidence the mechanisms work |
| `gpu.ours_peak_gib` | Peak memory of our own processes over all GPUs | ≤ 43 fits 48 GB |
| `gpu.peak_used_gib` | The whole first card, others' jobs included | ignore on a shared machine |
| `git_commit` | The code version | |

**The judge.** Unless an OpenAI key is set (it isn't), argument accuracy, response
quality and Pass@1 are graded by our own model as a **proxy judge**. Samsung uses
gpt-4o, so our numbers are estimates. The label is recorded in the files.

### 9.2 Offline results

- **`results/offline/EXPERIMENTS.md`** shows one row per evaluation: when, tag,
  `script` or `asr`, Pass@1, tool F1, arguments, response, self-correction, errors,
  temperature, seed, weights, judge, commit and host.
- **`script`** is the model on the exact words: the ceiling for its decisions.
- **`asr`** is the model on what the agent heard: closer to the live run.
- **The noise rows** show the chance spread. A change counts only if it beats that.

### 9.3 Failure files

`results/offline/failures/<eval>.md` groups the failures:

| Kind | Meaning |
|---|---|
| no call | Nothing was called |
| wrong tool | A different tool than expected |
| extra call | All expected tools, plus more |
| missing call | A chain stopped early |
| wrong arguments | The right tools with a wrong value |
| model error | A server problem, not a mistake |

Each item shows what was heard, what was expected, what was called, the verdict and
what the agent said. Tanmay uses these to fix general mistakes. They must never be
used to write item-specific rules; that would disqualify us.

---

## Part 10. Troubleshooting

| What you see | Cause | What to do |
|---|---|---|
| `No room for the model: it needs one GPU with 23100 MiB free, or two with 13100 MiB each` | GPUs too full now | Wait 15-30 min and retry. Ask the admin or users for a GPU. Try late at night |
| `The model fits, but no GPU has 5000 MiB left for the agent's speech models` | Nearly full machine | Wait. Don't move speech to the CPU (rule 3) |
| `No GPU has 6000 MiB left for the benchmark's scoring recognizer` | Same | Wait |
| `GPU N has only X GiB free and the model needs Y GiB there` | Memory changed after placement | Re-run the same block; `place_gpus.py` looks again |
| `The model server exited while starting` plus a log tail | Out of memory (another job grew), or a driver or compiler problem | Out of memory: re-run the block. Otherwise send the log tail and `llm_server.log` |
| `The model server did not become ready in time` | The first start compiles kernels (3-5 min); a slow download | Look at `llm_server.log`. If it shows progress, run again (downloads resume) |
| `Preflight failed` or `Tool calling: unexpected ...` | The model answers, but not with a tool call | Send `llm_server.log` and the screen |
| `No NVIDIA GPU visible` | Driver missing, or no GPU in this session | `nvidia-smi`. You may be on a login node, not the GPU node; ask the admin |
| `no C compiler (gcc)` (doctor) | vLLM compiles small kernels | Ask the admin for `build-essential` |
| `Could not download the benchmark data` | Google Drive throttling | Download `fdb_v3_data_released.zip` in a browser from the v3 README of `github.com/DanielLin94144/Full-Duplex-Bench`, copy it to `third_party/downloads/`, re-run |
| A download stops half-way | Network | Re-run the same command; it resumes |
| `Agent: still warming after 10 min; continuing anyway`, or it stops before `registered and warm` | The agent couldn't load its models (GPU full, CUDA libraries) | Push as `..._failed`; send `agent.log` |
| `The benchmark's runner failed (exit code N), so nothing is scored` | The runner crashed; usually the scorer out of memory | Re-run the block without `--force` to continue (7.3). If it repeats, push `_failed` and send `runner.log` |
| `WARNING: N recordings already have results filed under 'duet'` | You ran without `--force` | Intended only when continuing a crashed run. Otherwise `Ctrl-c` and add `--force` |
| `WARNING: N thinker errors in this run` | Model calls failed (server crashed or overloaded) | Check window 0 or `llm_server.log`; send it. The run's numbers are unreliable |
| `Cannot write /tmp/agent_tool_calls.log` | Another user owns that file | Ask them to delete it; the benchmark needs that exact path |
| An offline evaluation shows `"errors"` above 0 | The model server died or was overloaded | Check window 0; restart it (7.1); re-run that set |
| `A model server is running with ..., but this set needs the ... build` | A server with other weights is up | Stop it (window 0, `Ctrl-c`), run again |
| `Not installed yet` | Environments missing | Part 4.7 |
| `git pull`: "Your local changes would be overwritten" | A tracked file was edited | Don't force anything. Send `git status` to Tanmay |
| `Permission denied (publickey)` or `Repository not found` | GitHub access | Part 4.3; ask Tanmay to (re)add you |
| `Push failed (network or GitHub access)` | The results are committed locally, not pushed | Retry later with the printed `git push` line. Meanwhile copy the archive (8.4) |
| `git commit failed: set git config user.name / user.email` | Git identity missing | Part 4.3, step 2 |
| `STOP: something that looks like an API key` | A key got into a file | Nothing was pushed. Tell Tanmay. Don't push by hand |
| The terminal ignores keys | tmux scroll mode or a frozen SSH | `Ctrl-q`, `q`, `Esc`. Then reconnect and `tmux attach -t duet` |
| Window 0 closed by accident | The model server stopped | 7.1 again |
| No new `result_duet.json` for 15 minutes during a live run | Stuck | Send the last 80 lines of `runner.log` and `agent.log`. Don't kill it yet |
| Disk full | Environments and models need ~80 GB | `df -h`; free space; never delete `results/` |

**Stopping everything of yours** (only when told to, or at the end):

```bash
pkill -u $USER -f vllm.entrypoints; pkill -u $USER -f duet_voice.agent; pkill -u $USER -f livekit-server
```

---

## Part 11. Reference

### 11.1 Commands

```bash
python3 bench/place_gpus.py --show                  # where DUET would run now
eval "$(python3 bench/place_gpus.py --offline)"     # set GPUs for the model server only
eval "$(python3 bench/place_gpus.py)"               # set GPUs for a live run
.venv/bin/python bench/llm_server.py plan           # the model server's plan for these GPUs
.venv/bin/python bench/llm_server.py serve          # run the model server (window 0)
.venv/bin/python bench/llm_server.py health         # is it up?
bash scripts/experiments.sh baseline                # noise + greedy + hearing
bash scripts/experiments.sh fp8                     # FP8 comparison (Ada/Hopper only)
bash scripts/experiments.sh one <tag> --text script # one evaluation
bash reproduce.sh --only travel_19_695bd157114f0d2317f88617   # install + one recording
bash reproduce.sh --force                           # the full live run
bash reproduce.sh                                   # continue a crashed live run
.venv/bin/python bench/compare_runs.py              # the table of every result
.venv/bin/python bench/failures.py <eval.json> [--compare <old.json>]
bash scripts/push_results.sh <session>              # store everything on GitHub
bash scripts/doctor.sh                              # machine check
```

### 11.2 Settings (set only by the scripts, except `CUDA_VISIBLE_DEVICES` in special cases)

| Variable | Meaning |
|---|---|
| `CUDA_VISIBLE_DEVICES` | The main GPU (set by `place_gpus.py`) |
| `DUET_LLM_GPUS` | The model server's GPUs; two means the model is split |
| `DUET_AGENT_GPUS` | The GPU for the agent's speech models |
| `DUET_SCORING_GPUS` | The GPU for the benchmark's scorer |
| `DUET_LLM_VARIANT` | `w4a16` (default); `fp8` only via `experiments.sh fp8` |
| `DUET_TEMPERATURE`, `DUET_SEED` | Set per experiment by `experiments.sh`; don't set by hand |

### 11.3 Where everything is

See Part 3. The published copies are in `results/reported/dgx/` on your results
branches.

### 11.4 Glossary

| Term | Meaning |
|---|---|
| **FDB-v3** | Full-Duplex-Bench v3, the benchmark (60% of the score) |
| **Pass@1** | Share of recordings where every tool call was exactly right |
| **Live run** | The whole agent with real-time audio through the benchmark's pipeline, about 2 h for 100 |
| **Offline evaluation** | The model's decisions on the recordings' words, without audio streaming, about 6 min for 100 |
| **`script` / `asr`** | The exact words / what the agent's recognizer heard |
| **Proxy judge** | Our own model grading answers when gpt-4o isn't available (labelled) |
| **Model server / vLLM** | The program that runs the language model on the GPU at `127.0.0.1:18000` |
| **4-bit / w4a16, FP8** | Compressed weight formats. 4-bit is our default; FP8 is for comparison on newer GPUs |
| **Split / tensor parallel** | One model across two GPUs, when neither has room alone |
| **Path F / path S** | A free GPU (Samsung's layout) / pieces spread over a shared machine |
| **`ours_peak_gib`** | The memory our own processes used at peak, over all GPUs |
| **Results branch** | A GitHub branch `results-<session>-<time>` made by `push_results.sh` |
| **tmux** | Keeps terminal sessions running after you disconnect |
