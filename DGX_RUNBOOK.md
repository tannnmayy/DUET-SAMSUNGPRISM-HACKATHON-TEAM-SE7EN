# Running DUET on a remote GPU machine (DGX)

This page is for whoever runs DUET on the DGX over SSH. It needs no API key, no
sudo and no machine-learning background: copy the commands in order. Each step
says what you should see, and the troubleshooting table at the end covers what
can go wrong.

**What you will do:** check the machine, install everything and run one
recording (about 45 minutes, mostly downloads), measure accuracy on all 100
recordings (about 15 minutes), then run the full benchmark (about 2 hours) and
send the results back. Nothing needs watching while it runs.

**What runs on the machine:** one GPU holds everything, exactly as on Samsung's
48 GB evaluation machine:
- the language model, Qwen3-30B-A3B-Instruct-2507 in its 4-bit build, served by
  vLLM: a fixed 22 GiB;
- the agent's speech models: about 3.5 GiB;
- the benchmark's scoring recognizer: about 4-5 GiB.

That is about 31 GiB in all. The run records the peak memory of its own processes,
which shows whether everything fits in 48 GB.

> **Shared machine, no GPU free?** If every GPU is partly used by other people's
> jobs (as on the SRM DGX on 28 Sep), follow [DGX_EXPERIMENTS.md](DGX_EXPERIMENTS.md)
> instead: it spreads the pieces over the free memory of several GPUs. The full
> manual, including experiments, what to store and send back, and rules for an AI
> assistant, is [DGX_OPERATOR_MANUAL.md](DGX_OPERATOR_MANUAL.md).

---

## 0. What the machine needs

| Need | Why | Check |
|---|---|---|
| Linux x86_64 with an NVIDIA GPU that has **34 GiB free** (an idle 40 GB A100 or anything bigger) | The whole stack runs on one GPU | `nvidia-smi` |
| An NVIDIA driver for CUDA 12.x or 13.x | Every GPU package here is the CUDA 12.8 build | `nvidia-smi`, top right |
| About **80 GB** of free disk where you clone the repository | Three Python environments (~25 GB), models (~22 GB), caches | `df -h .` |
| `git`, `curl`, `tar`, a C compiler (`gcc`) | Downloads; vLLM's Triton kernels compile small helpers | `which git curl gcc` |
| Internet access to pypi.org, huggingface.co, github.com, astral.sh and drive.google.com | Packages, model weights, the benchmark and its audio | `bash scripts/doctor.sh` checks all of them |

You do **not** need: sudo, Docker, a system Python, `python3-venv`, `ffmpeg`, or
any API key. The scripts fetch what is missing into `third_party/`.

On a DGX, put the repository on the big data disk (often `/raid`), not in a small
home directory. Everything the project downloads stays inside the repository
folder.

## 1. Get the code

Accept the GitHub invitation to the repository (email, or github.com →
Notifications). Then, on the DGX:

```bash
cd /raid/$USER            # or any folder on a disk with ~90 GB free
git clone https://github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet
cd duet
```

If git asks for a password, GitHub needs a personal access token instead
(github.com → Settings → Developer settings → Tokens), or use SSH:
`git clone git@github.com:tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet`.

## 2. Work inside tmux

SSH connections drop; tmux keeps the run alive if yours does.

```bash
tmux new -s duet          # start a session (later: tmux attach -t duet)
```

Detach with `Ctrl-b` then `d`. Everything below runs inside this session.

## 3. Check the machine

```bash
bash scripts/doctor.sh
```

**You should see** a list of `OK` lines ending in `Ready.` It also writes
`doctor_report.txt`. If a line says `FAIL`, it says how to fix it. If you cannot,
send `doctor_report.txt` back.

**Choosing the GPU.** The doctor lists every GPU with its free memory. By default
the one with the most free memory is used. To pick one yourself (for example,
GPU 3 is idle), set it once in this tmux session:

```bash
export CUDA_VISIBLE_DEVICES=3
```

## 4. Install everything and run one recording

```bash
bash reproduce.sh --only travel_19_695bd157114f0d2317f88617
```

The first time, this does everything, and the progress lines start with `==`:

| Step | Time (first run) |
|---|---|
| uv and Python 3.11; three environments | ~15 min |
| The benchmark, its audio (736 MB), ffmpeg, the LiveKit server | ~3 min |
| Speech models, the language model's weights (17 GB), the scoring recognizer | ~5-15 min (bandwidth) |
| The model server starts (compiles its kernels once) | ~3-5 min |
| One recording, then scoring | ~2 min |

**You should see**, near the end:

```
Model server: ready at http://127.0.0.1:18000/v1
Models: qwen3-30b-a3b-instruct-2507 (thinker), qwen3-30b-a3b-instruct-2507 (talker)
Tool calling: ok
...
Run folder: /raid/.../duet/results/live/<time>
GPU NVIDIA A100-SXM4-40GB: peak 3x.x GiB used of 40.0 GiB (the whole card, other users' jobs included)
This run's own processes: peak 3x.x GiB over all GPUs used (fits a 48 GB card at 43 GiB or less)
```

The summary printed just above should show `"turn_take_rate": 1.0` (the agent
answered). If anything stops with an error, see Troubleshooting, and send back
the packed results (step 7) with the last screen of output.

Re-running is safe: finished steps are skipped, and an interrupted install
resumes where it stopped.

## 5. Measure accuracy on all 100 recordings (fast)

```bash
bash scripts/offline_eval.sh
```

This takes about 15 minutes:
1. It transcribes the 100 recordings once with the agent's own speech recognizer.
2. It gives each transcript to the language model with the benchmark's 12 tools.
3. It scores the calls with the benchmark's own scorers.

**You should see** a summary with `"pass@1"`, `"tool_f1"` and `"arg_acc"`. Pass@1
is the share of recordings where every tool call was exactly right. For
reference, the best published system scores 0.600.

Two extra comparisons worth running while you are there (about 5 minutes each):

```bash
bash scripts/offline_eval.sh --text script --tag script                       # exact scripts: the ceiling without hearing errors
DUET_TEMPERATURE=0 bash scripts/offline_eval.sh --tag greedy                  # greedy decoding instead of the model card's 0.7
```

## 6. Run the full benchmark

```bash
bash reproduce.sh --force
```

It takes about 2 hours: each of the 100 recordings is streamed in real time,
then scored. Detach from tmux (`Ctrl-b d`) and come back later with
`tmux attach -t duet`. It ends with the summary, the run folder and the GPU's
peak memory.

`--force` matters: the benchmark's runner skips any recording that already has
a result (here, the one from step 4), and would otherwise reuse it. Use
`--force` on every run after the first, or after any change, so every score
comes from this run. Without it, the run prints a warning with the count.

## 7. Send the results back

```bash
bash scripts/collect_results.sh --push dgx_full_run
```

This packs the latest run and the offline evaluations into
`duet_results_<time>.tar.gz`, and pushes them to a new branch on GitHub
(`results-dgx_full_run-<time>`), where Tanmay can pull them. If the push fails
(no GitHub write access), run `bash scripts/collect_results.sh` without
`--push` and send the `.tar.gz` file any other way (for example
`scp` or a Drive link).

---

## Troubleshooting

| What you see | What to do |
|---|---|
| `GPU N has only ... MiB free` (doctor), or the model server runs out of memory | Another job is using that GPU. Pick a free one: `export CUDA_VISIBLE_DEVICES=<index>` (see `nvidia-smi`), then re-run. |
| `no C compiler (gcc)` | Ask the machine's admin for `build-essential` (Ubuntu), or point `CC` at any gcc. |
| `Could not download the benchmark data` | Google Drive is refusing the file for now. Download it in a browser from the link in the FDB-v3 README, copy it to `third_party/downloads/fdb_v3_data_released.zip`, and re-run. |
| `The model server exited while starting` | The last lines of its log are printed. The full log is in the run folder, `llm_server.log`. Out of memory: pick another GPU. Anything else: send the log. |
| `Preflight failed` or `Tool calling: unexpected` | Send `llm_server.log` and the screen output. |
| `Cannot write /tmp/agent_tool_calls.log` | Another user's file. The benchmark reads exactly that path; ask them to remove it. |
| A download stops half-way (Hugging Face, pip) | Re-run the same command; downloads resume. |
| Port 18000 is taken by something else | `export DUET_LLM_BASE_URL=http://127.0.0.1:18011/v1` and re-run (any free port). |
| You need to stop everything | `Ctrl-C` in tmux. If anything is left: `pkill -u $USER -f vllm.entrypoints`, `pkill -u $USER -f duet_voice.agent`, `pkill -u $USER -f livekit-server`. |

## Where things are

| Path | What |
|---|---|
| `results/live/<time>/` | One benchmark run: `summary.json` (scores, GPU peak), `run_config.json` (every setting), the benchmark's reports, per-item results, traces, logs |
| `results/offline/eval_*.json` | Offline evaluations, with per-recording rows |
| `doctor_report.txt` | The machine check |
| `third_party/` | Everything downloaded: Python, packages cache, model weights, the benchmark, ffmpeg, LiveKit |
| `.venv`, `.venv-bench`, `.venv-llm` | The agent's, the benchmark runner's and the model server's Python environments |

## Cleaning up afterwards

About 90 GB, all inside the repository folder:

```bash
rm -rf third_party .venv .venv-bench .venv-llm results
```

---

## For Tanmay: before and after

**Before:** add the friend as a collaborator:
1. On GitHub, open the repository, then Settings → Collaborators → Add people.
2. Enter their GitHub username and choose Write access, so they can push the
   results branch.
3. They accept the invitation.

**After:** fetch the results branch.

```bash
git fetch origin && git branch -r | grep results-
```
