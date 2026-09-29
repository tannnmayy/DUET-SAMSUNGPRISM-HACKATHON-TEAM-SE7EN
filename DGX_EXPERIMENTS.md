# DUET on the shared DGX: experiments, the full run, and a complete record

This is the step-by-step guide for running DUET on SRM's DGX A100
(`srmist1@172.16.0.32`, the repository in `~/duet`). It picks up where the 28 Sep
session stopped. Follow it in order, and copy the commands exactly. Each step says
what you should see, how long it takes, and what to do if something goes wrong.

**Deadline: 1 Oct.** Everything here fits in about a day of machine time, most
of it unattended.

---

## What you are producing, and why it matters

Samsung scores the benchmark part (60% of the total) by re-running our
`reproduce.sh` on their own single 48 GB GPU. Our job before that is to prove it
works, and to show the numbers and the logs. From you we need three things, all
stored on GitHub:

| # | Result | Why | Machine time |
|---|---|---|---|
| 1 | **Offline experiments**: the language model on all 100 recordings under different settings | Tells us which settings score best, how much results vary between runs, and what the model gets wrong, so Tanmay can fix general mistakes | about 1 hour |
| 2 | **A full live run**: all 100 recordings streamed through the whole agent, with the benchmark's own scoring | Our "results and run logs" deliverable, and proof the whole pipeline runs with the real model | about 2.5 hours |
| 3 | **A clean single-GPU run**, whenever one GPU is free | The closest copy of Samsung's re-run. It becomes the run we report | about 2.5 hours |

**No training.** Every "run" in this guide *evaluates* the system. Nothing is
trained or fine-tuned. Samsung disqualifies teams that train on the benchmark's
test items, and we have no other training data. There are no model checkpoints
to save. Our "checkpoints" are the result folders: each one records the exact code
version (git commit) it came from, and every one goes to GitHub.

**Documentation counts as much as the numbers.** Samsung asks for "run logs, not
only numbers". So:
- keep the journal (section 4);
- push after every part (section 12);
- never delete a result folder, including failed runs. A failure with its logs is
  evidence too.

---

## 1. Rules for every session

1. **Work inside tmux** (section 2), so a dropped connection doesn't kill a run.
2. **`git pull` at the start of every session.** Tanmay may have pushed fixes.
3. **Don't edit code on the DGX.** If something breaks, stop and send the error
   (section 13). The only file you write is your journal.
4. **Don't start vLLM or the benchmark by hand.** Use the scripts here: they choose
   the GPUs, record every setting, and check for mistakes.
5. **Scored runs keep the agent's speech on a GPU and use the benchmark's own
   scoring.** Never use `DUET_ASR_DEVICE=cpu` or `--scoring-asr whisper` for a run
   we report. On the CPU the agent hears with a much smaller model; that's why
   "June 3" became "June 1" on 28 Sep.
6. **Push after every part:** `bash scripts/push_results.sh <session-name>`.
7. **It's a shared machine.**
   - Only stop your own processes.
   - Never run two DUET runs on this machine at the same time: the benchmark
     writes tool calls to one fixed file, `/tmp/agent_tool_calls.log`.
   - Stop the model server when you finish for the day.
8. **When in doubt, stop and ask.** Guessing costs more time than waiting.

---

## 2. tmux in one minute

tmux keeps your terminal alive on the DGX when your laptop disconnects. Our session
is called `duet`, and we use three windows:

| Window | What runs there |
|---|---|
| **0** | The model server (leave it alone while it runs) |
| **1** | Your work: experiments, runs, pushes |
| **2** | GPU watch: `watch -n 10 nvidia-smi` |

| To | Press / type |
|---|---|
| Enter the session | `tmux attach -t duet`, or the first time `tmux new -s duet` |
| Leave it running and disconnect | `Ctrl-b`, release, then `d` |
| Make a new window | `Ctrl-b`, then `c` |
| Go to window 0, 1 or 2 | `Ctrl-b`, then `0`, `1` or `2` |
| Scroll back through output | `Ctrl-b`, then `[`, then the arrow keys; `q` to stop |
| Unfreeze a stuck screen | `Ctrl-q`, then `q`, then `Esc`. Only if still stuck: `Ctrl-c` (this stops what runs in that window) |

The green bar at the bottom shows the windows. `*` marks the one you're looking at.

---

## 3. Start of every session (5 minutes)

On your laptop:

```bash
ssh srmist1@172.16.0.32
```

On the DGX:

```bash
tmux attach -t duet || tmux new -s duet
```

Then, in window 1 (`Ctrl-b 1`; if there's no window 1, `Ctrl-b c`):

```bash
cd ~/duet
git status --short
git pull
source scripts/env.sh
.venv/bin/python bench/place_gpus.py --show
```

**You should see:**
- `git status --short` prints nothing, or only lines starting with `??` (new files
  such as your journal). If it lists `M something`, a tracked file was changed on
  this machine: send Tanmay the output and don't continue.
- `git pull` prints `Updating ...` with a list of files, or `Already up to date.`
- `place_gpus.py --show` prints a table of GPUs and a sentence saying where things
  would run, like this:

```
GPU  free MiB  busy  name
  0     15700   98%  NVIDIA A100-SXM4-40GB
  ...
  7     19900   75%  NVIDIA A100-SXM4-40GB

No single GPU is free enough; model server on GPU 4,7 (split over 2), agent's speech on GPU 6, scoring recognizer on GPU 5.
```

or `Everything on GPU 3: the same layout as Samsung's re-run.` when one GPU is free.

**What the numbers mean.**
- An A100 here has 40,960 MiB in total.
- DUET needs about **31 GiB** when everything is on one GPU: the model 22 GiB,
  speech 3.5, scoring 4-5.
- `place_gpus.py` needs these free amounts:

| What runs there | Free memory needed |
|---|---|
| Everything on one GPU | 34,000 MiB |
| The model alone on one GPU | 23,100 MiB |
| Half of the model on each of two GPUs | 13,100 MiB each |
| The agent's speech models | 5,000 MiB |
| The scoring recognizer | 6,000 MiB |

---

## 4. Your journal (set up once, add to it every session)

Create it once:

```bash
cd ~/duet
cat >> results/journal.md <<'EOF'
# DUET on the SRM DGX A100: journal

EOF
```

At the start and end of every session, add a short entry. `nano results/journal.md`
opens it: type, then `Ctrl-o`, `Enter` to save, `Ctrl-x` to leave. Use this shape:

```
## 29 Sep, 10:15-13:40 IST, session a100_0929_am
- GPUs at start: all busy, 15-20 GB free each; used 4,7 (model) 6 (speech) 5 (scoring)
- Ran: Part C baseline (all 7 evaluations), pushed
- Results: noise Pass@1 0.61 / 0.63 / 0.62, greedy 0.64 ...
- Problems: GPU 5 filled up at 12:10, scoring crashed; re-ran without --force, it continued
- Next: Part D when the lab is quieter
```

`push_results.sh` publishes it with the results. It is the story of how our
numbers were made. Samsung asks for exactly that, and Tanmay needs it to write
the docs.

---

## 5. Part A: restart the model server the new way (10 minutes)

The server from 28 Sep was started with a hand-typed command. From now on it is
started by our launcher, which records what it ran and writes a log.

**1. Stop the old server.** Go to window 0 (`Ctrl-b 0`). If logs are scrolling
there, press `Ctrl-c` once and wait for the prompt.

**2. Check that nothing of yours is still holding a GPU:**

```bash
nvidia-smi --query-compute-apps=pid,used_memory --format=csv
ps -u $USER -o pid,cmd | grep -E "vllm|duet_voice|livekit" | grep -v grep
```

The second command should print nothing. If it lists a process, stop only that
one: `kill <pid>` (use the number in the first column).

**3. Start the server** (still in window 0):

```bash
cd ~/duet && git pull && source scripts/env.sh \
  && eval "$(.venv/bin/python bench/place_gpus.py --offline)" \
  && .venv/bin/python bench/llm_server.py plan \
  && .venv/bin/python bench/llm_server.py serve
```

(The `&&` chain stops at the first step that fails, so nothing starts on the wrong
GPUs.)

**You should see:**
- **`plan`:** JSON with `"variant": "w4a16"` and the GPUs chosen. On busy GPUs it
  shows `"tensor_parallel": 2` and `"per_gpu_gib": 12.5`.
- **After 1-5 minutes:** `Model server: ready at http://127.0.0.1:18000/v1`, then
  `Serving; Ctrl-C stops it.`

Leave window 0 alone from here on.

**If instead you see:**
- **`GPU N has only X GiB free and the model needs Y GiB there`:** memory moved
  since you looked. Run the `eval` line and `serve` again.
- **`No room for the model`:** every GPU is too full right now. Wait 15-30 minutes
  and try again. Section 13 has more options.
- **`The model server exited while starting`:** the last lines of its log are
  printed; the full log is `results/llm_server/llm_server.log`. Send both to Tanmay.

---

## 6. Part B: publish the 28 Sep results (5 minutes)

The files from 28 Sep are still only on this machine. In window 1:

```bash
cd ~/duet
source scripts/env.sh
bash scripts/push_results.sh a100_0928
```

**You should see:**

```
Published N results into results/reported/dgx
Backup: /home/faculty/srmist1/duet/duet_results_a100_0928.tar.gz (x.xM) - copy it off the machine too
Pushed to GitHub: branch results-a100_0928-2026...
```

This includes the offline evaluation (Pass@1 0.63), the one-item live run, and the
failed "full" run. Keep that last one; it documents the bug that has since been
fixed.

Copy the backup to your laptop too (on your laptop, not in the SSH window):

```bash
scp srmist1@172.16.0.32:~/duet/duet_results_a100_0928.tar.gz .
```

The folders you copied by hand on 28 Sep (`results/reported/dgx_tp2_script`,
`results/reported/dgx_live_travel19`) are now duplicates. You can leave them.

---

## 7. Part C: offline experiments (about 1 hour)

These test the model alone on the words of all 100 recordings, scored by the
benchmark's own scorers. Each evaluation takes about 6 minutes, and they need only
the model server from Part A. In window 1:

```bash
cd ~/duet
source scripts/env.sh
bash scripts/experiments.sh baseline
```

**What runs, in order:**

| Tag | What it tests | Why |
|---|---|---|
| `noise_s7`, `noise_s8`, `noise_s9` | The same settings three times with different random seeds, on the exact scripts | How much Pass@1 moves by chance. A later change only counts if it beats this spread |
| `greedy` | Temperature 0 (always the most likely word) instead of the model card's 0.7 | Whether more deterministic decoding scores better. That decides our default |
| `asr_t07`, `asr_t0` | The same, on what the agent's own recognizer (large-v3-turbo) heard | The realistic number: hearing errors included. The recordings are transcribed once first (5-15 minutes) |

**For each evaluation you should see** lines like:

```
== noise_s7: --text script (temperature model card, seed 7)
 "pass@1": 0.63,
 "tool_f1": 0.932,
 ...
wrote results/offline/eval_script_20260929_101500_noise_s7.json
   failures, grouped: results/offline/failures/eval_script_20260929_101500_noise_s7.md
```

It ends with `Done. Every result so far: results/offline/EXPERIMENTS.md`.

**Look at the table:**

```bash
cat results/offline/EXPERIMENTS.md
```

**Push it:**

```bash
bash scripts/push_results.sh a100_0929_offline
```

Add the Pass@1 numbers to your journal.

**If an evaluation stops with an error:**
- The script prints the last lines. The most common cause is the model server
  stopping; check window 0.
- Restart the server (Part A), then run just the missing parts, for example
  `bash scripts/experiments.sh hearing`.

**Optional, only if a free Ada or Hopper GPU is available (an H100 somewhere):**
`bash scripts/experiments.sh fp8` compares Qwen's FP8 build with our 4-bit one. It
needs its own server, so stop the one in window 0 first. The A100s here can't run it.

---

## 8. Part D: the full live run on shared GPUs (about 2.5 hours)

This streams all 100 recordings through the whole agent, as Samsung will. It runs
the benchmark's own runner and scoring, unmodified. On this machine the pieces sit
on different GPUs, wherever memory is free.

**1. Stop the model server in window 0** (`Ctrl-b 0`, then `Ctrl-c`). The run
starts its own server and records its log.

**2. In window 1:**

```bash
cd ~/duet && git pull && source scripts/env.sh \
  && eval "$(.venv/bin/python bench/place_gpus.py)" \
  && env | grep -E "CUDA_VISIBLE_DEVICES|DUET_(LLM|AGENT|SCORING)_GPUS" \
  && bash reproduce.sh --force
```

`place_gpus.py` without `--offline` places all three pieces: the model, the agent's
speech and the scoring recognizer. The `env` line shows where each one goes; copy
it into your journal. If placement finds no room, the chain stops there and
nothing starts.

**You should see, in the first 5-10 minutes:**

```
Model server: RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16 (w4a16, 22.0 GiB budget on NVIDIA A100-SXM4-40GB, GPU 4,7, split over 2 GPUs); log: ...
Model server: ready at http://127.0.0.1:18000/v1
Models: qwen3-30b-a3b-instruct-2507 (thinker), qwen3-30b-a3b-instruct-2507 (talker)
Tool calling: ok
LiveKit: local server up on port 7880 (pid ...)
Agent: registered and warm (pid ...)
Runner: .../.venv-bench/bin/python .../run_tool_benchmark_all_released.py --provider duet ... --force
```

Then it goes quiet for about 2 hours; each recording plays in real time. Detach
with `Ctrl-b d` and come back later.

**Watching it** (window 2, `Ctrl-b c` the first time):

```bash
cd ~/duet
R=$(ls -dt results/live/*/ | head -1); echo $R
tail -f $R/runner.log                    # which recording; Ctrl-c stops watching, not the run
ls third_party/Full-Duplex-Bench/v3/fdb_v3_data_released/*/result_duet.json | wc -l   # recordings done (of 100)
```

**At the end you should see** a JSON summary, then:

```
Run folder: /home/faculty/srmist1/duet/results/live/2026...
GPU NVIDIA A100-SXM4-40GB: peak 3x.x GiB used of 40.0 GiB (the whole card, other users' jobs included)
This run's own processes: peak 3x.x GiB over all GPUs used (fits a 48 GB card at 43 GiB or less)
```

**Check the summary** (the numbers Tanmay needs):

```bash
.venv/bin/python - <<'PY'
import glob, json, os
run = max(glob.glob("results/live/*/"), key=os.path.getmtime)
s = json.load(open(run + "summary.json"))
for k in ("total", "pass_at_1", "tool_selection_f1", "argument_acc", "response_qual",
          "turn_take_rate", "interruption_rate", "task_completion_latency_mean_s"):
    print(k, s.get(k))
print("thinker errors:", s["agent"]["thinker_errors"], " tool calls:", s["agent"]["tool_calls"])
print("our GPU peak GiB:", s["gpu"].get("ours_peak_gib"))
PY
```

**Healthy:**
- `total` is 100 and `turn_take_rate` close to 1.0.
- `thinker errors` is 0 or close to it.
- `tool calls` is somewhere around 150-250.
- `our GPU peak GiB` is about 31. Anything at or below 43 fits Samsung's card.

**Push:**

```bash
bash scripts/push_results.sh a100_0929_live
```

**If the run stops early:**
- **`The benchmark's runner failed ... so nothing is scored`,** with the last lines of
  `runner.log` printed. Usually a GPU filled up (`CUDA out of memory`).
  1. Run the `eval "$(.venv/bin/python bench/place_gpus.py)"` line again.
  2. Then run `bash reproduce.sh` **without `--force`**. Finished recordings are
     kept, and it continues with the rest. Only do this if no `git pull` brought
     new code in between.
  3. Note it in the journal.
- **It stops before `Agent: registered and warm`:** send `results/live/<time>/agent.log`.
- **Anything else:** push anyway (`bash scripts/push_results.sh a100_0929_live_failed`)
  and send the last screen of output.

**Why these numbers are not quite Samsung's:**
- The GPUs are shared, so the model answers more slowly than on a free card. That
  inflates latency and can make the agent miss some answers.
- The layout (pieces spread over GPUs) is recorded, and Tanmay labels the run
  accordingly.

---

## 9. Part E: the clean single-GPU run (about 2.5 hours, whenever a GPU is free)

This is the most valuable run: exactly Samsung's layout, everything on one GPU, no
special settings. Shared machines are often quieter late at night. At the start of
each session, and now and then (window 2), check:

```bash
cd ~/duet && .venv/bin/python bench/place_gpus.py --show
```

When it says **`Everything on GPU N`**:
1. Stop any DUET model server (window 0, `Ctrl-c`).
2. In window 1:

```bash
cd ~/duet && git pull && source scripts/env.sh \
  && eval "$(.venv/bin/python bench/place_gpus.py)" \
  && env | grep -E "CUDA_VISIBLE_DEVICES|DUET_(LLM|AGENT|SCORING)_GPUS" \
  && bash reproduce.sh --force
```

The `env` line should show only `CUDA_VISIBLE_DEVICES=N`: `place_gpus.py` has
cleared the placement settings. Everything else is as in Part D. At the end:

```bash
bash scripts/push_results.sh a100_clean
```

Tell Tanmay straight away: this becomes the reported run if it's healthy.

If another user's job lands on "your" GPU during the run, it may run out of
memory. Then follow "If the run stops early" in Part D, with a new `place_gpus`
line.

---

## 10. Part F: after Tanmay pushes an improvement (15 minutes each)

Tanmay will read the failure breakdowns in `results/reported/dgx/failures/` and
push general fixes (prompts, settings). Each time, in window 1:

```bash
cd ~/duet
git pull
source scripts/env.sh
bash scripts/experiments.sh one <name>_script --text script
bash scripts/experiments.sh one <name>_asr --text asr
bash scripts/push_results.sh a100_<name>
```

Tanmay tells you `<name>` (for example `prompt_v2`). The model server from Part A
must be running. A `git pull` doesn't require restarting it unless Tanmay says so.

To compare with the baseline yourself:

```bash
.venv/bin/python bench/failures.py results/offline/eval_script_<new>.json --compare results/offline/eval_script_<old>.json | head -30
```

It lists which recordings the change fixed and which it broke.

---

## 11. Part G: the final check, after the last code change (about 1 hour)

When Tanmay says the code is frozen, test what Samsung will download: a fresh
clone, in a new folder, with nothing reused.

```bash
cd ~
git clone git@github.com:tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet_final_check
cd duet_final_check
bash scripts/doctor.sh || true
eval "$(python3 bench/place_gpus.py)" && bash reproduce.sh --only travel_19_695bd157114f0d2317f88617
```

This installs everything again from zero: about 45 minutes and 50 GB. It shows
the final code installs and runs on a machine that is not ours. Then:

```bash
bash scripts/push_results.sh a100_final_check
```

Afterwards the folder can be deleted: `rm -rf ~/duet_final_check`.

---

## 12. What gets stored, and where

`bash scripts/push_results.sh <session>` copies everything below into
`results/reported/dgx/` and pushes it to GitHub on a new branch
`results-<session>-<time>`. Each push holds everything so far, so the newest
branch is always complete. It also leaves a backup archive,
`duet_results_<session>.tar.gz`, in `~/duet`; copy that to your laptop or Drive.

| In `results/reported/dgx/` | What it is |
|---|---|
| `live_<time>/` | One live run: `summary.json` (scores, GPU peak), `run_config.json` (every setting, the git commit, host, GPUs, placement), the benchmark's three reports, `items/` (per recording), `traces/` (everything the agent heard, decided and did, per conversation), `agent.log`, `runner.log`, `eval_*.log`, `llm_server.log`, `llm_plan.json` |
| `offline_<time>_<tag>/` | One offline evaluation: the JSON with the summary (scores, settings, weights, git commit, host) and one row per recording |
| `failures/` | For each offline evaluation: its failures grouped by kind of mistake |
| `EXPERIMENTS.md` | One table of every result, with code version and machine |
| `experiments.log` | One line per experiment: when, what, which file |
| `JOURNAL.md` | Your journal |
| `doctor_report.txt`, `machine_<session>.txt` | The machine check and a GPU and disk snapshot |
| `llm_server_latest.log.gz`, `llm_plan_latest.json` | The last model server's log and plan (started from Part A) |

Audio and the LiveKit server's debug log (about 100 MB) are left out. Everything
else is kept.

On GitHub, Tanmay sees the branches with `git branch -r | grep results-`.

---

## 13. Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| `No room for the model` (place_gpus) | Every GPU is too full right now | Wait 15-30 minutes and check again. If it stays like this for hours, ask whoever runs the lab for one A100 for 3 hours (`nvidia-smi` shows who uses each GPU, at the bottom) |
| `GPU N has only X GiB free ...` (launcher) | Memory moved since placement | Run the `eval "$(... place_gpus.py ...)"` line again, then the same command |
| `The model server exited while starting` | Usually out of memory | Place again. If it repeats, send `results/llm_server/llm_server.log` (or the run folder's `llm_server.log`) |
| `The benchmark's runner failed ... nothing is scored` | The runner crashed, usually out of memory for the scoring recognizer | Place again, then `bash reproduce.sh` without `--force` to continue (Part D) |
| `WARNING: N recordings already have results ...` | You re-ran without `--force` | Intended only when continuing a crashed run. Otherwise stop (`Ctrl-c`) and add `--force` |
| `Cannot write /tmp/agent_tool_calls.log` | Another user owns that file | The benchmark needs that exact path. Ask them to delete it, or wait |
| `Not installed yet` | The environments are missing | `bash reproduce.sh --only travel_19_695bd157114f0d2317f88617` once |
| `git pull` says local changes would be overwritten | A tracked file was edited here | Don't force anything. Send `git status` to Tanmay |
| `Push failed` | Network or GitHub access | The commit is saved on a local branch, and the backup archive exists. Retry with the printed `git push` line later |
| `STOP: something that looks like an API key` | A key got into a log | Nothing was pushed. Tell Tanmay; don't push by hand |
| The terminal ignores your keys | tmux copy mode or a frozen SSH | `Ctrl-q`, `q`, `Esc`. Still stuck: close the terminal tab, `ssh` again, `tmux attach -t duet` |
| You closed window 0 by accident | The model server stopped | Part A, step 3 |
| The run looks stuck (no new lines for 10 minutes) | Often it is between recordings; each takes about 70 s | Check `ls .../result_duet.json \| wc -l` in window 2. If the count doesn't grow for 15 minutes, send `runner.log` and `agent.log` |

**When you ask for help, send:**
- the command;
- the last 30 lines of the screen;
- the run folder's `agent.log`, `runner.log` and `llm_server.log`;
- which GPUs you used (the `env` line).

The quickest way is to push (`bash scripts/push_results.sh <name>_failed`) and
say so.

---

## 14. End of every session

1. **Push:** `bash scripts/push_results.sh <session>`.
2. **Journal:** write the end of today's entry, then push again. (Or write it
   before step 1, then you only push once.)
3. **Stop the model server** if nothing else will use it tonight: window 0,
   `Ctrl-c`. It holds GPU memory other people need.
4. **Leave tmux running:** `Ctrl-b d`. Then close the terminal.

---

## 15. Quick reference

```bash
# every session
ssh srmist1@172.16.0.32
tmux attach -t duet || tmux new -s duet
cd ~/duet && git pull && source scripts/env.sh
.venv/bin/python bench/place_gpus.py --show

# the model server, for offline experiments (window 0)
eval "$(.venv/bin/python bench/place_gpus.py --offline)" && .venv/bin/python bench/llm_server.py serve

# offline experiments (window 1)
bash scripts/experiments.sh baseline                  # noise + greedy + hearing
bash scripts/experiments.sh one <tag> --text script   # one evaluation after a change
cat results/offline/EXPERIMENTS.md

# live runs (window 1; stop the window 0 server first)
eval "$(.venv/bin/python bench/place_gpus.py)" && bash reproduce.sh --force
bash reproduce.sh                                     # continue a crashed run (no --force)

# store everything
bash scripts/push_results.sh <session>
```
