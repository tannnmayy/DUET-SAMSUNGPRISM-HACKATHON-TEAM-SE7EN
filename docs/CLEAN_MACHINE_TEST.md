# Testing the reproduction on a clean machine

> For a remote GPU machine you reach over SSH (such as a DGX), follow
> [DGX_RUNBOOK.md](../DGX_RUNBOOK.md): it covers the same test, step by step. No API key is needed any
> more; the commands below that set keys apply only to the Gemini alternative.

The guide: *"Only our re-run counts … test the script on a clean machine before
submitting."* This is the procedure, on a Google Cloud GPU VM that has never
seen the project. It takes about 30 minutes plus the benchmark's run time. The
VM costs roughly US$1 per hour; delete it afterwards.

## 1. Create the VM (Google Cloud)

DUET needs one GPU with about 34 GiB free: the language model takes a fixed
22 GiB, the speech models and the scoring recognizer the rest (about 31 GiB in
all). An L4 (24 GB) is too small. The closest match to Samsung's machine is a
48 GB card (L40S or RTX 6000 Ada, offered by RunPod, Lambda or Vast); on Google
Cloud use an A100 40 GB (`a2-highgpu-1g`) or 80 GB (`a2-ultragpu-1g`).
Use a Deep Learning VM image, which comes with the NVIDIA driver: list the current
CUDA 12 image families, then pick an Ubuntu 22.04 one.

```bash
gcloud compute images list --project deeplearning-platform-release \
  --filter="family~cu12 AND family~ubuntu-2204" --format="value(family)" | sort -u

gcloud compute instances create duet-repro \
  --zone=us-central1-a --machine-type=a2-ultragpu-1g \
  --accelerator=type=nvidia-a100-80gb,count=1 --maintenance-policy=TERMINATE \
  --image-project=deeplearning-platform-release --image-family=<family from the list> \
  --boot-disk-size=200GB --metadata=install-nvidia-driver=True

gcloud compute ssh duet-repro --zone=us-central1-a
```

## 2. On the VM: exactly what Samsung will do

```bash
nvidia-smi                                   # driver and CUDA version (12.x or 13.x)
sudo apt-get update && sudo apt-get install -y ffmpeg unzip git curl
git clone <repository URL> duet && cd duet   # or copy the submission folder
export OPENAI_API_KEY=...                    # optional: the official gpt-4o judge
bash reproduce.sh --only travel_19,ecommerce_01_65e8cf8f4c7424fa062e54a3,housing_04
```

Do not install `python3-venv` or anything else by hand. The script must cope on
its own, as it will have to on Samsung's machine.

## 3. What to check

| Check | Where | Pass |
|---|---|---|
| Both environments install | script output | no pip error; prints the Python versions |
| The GPU is used by the agent | `results/live/<run>/agent.log` | `whisper large-v3-turbo on cuda` |
| The GPU is used by the scoring ASR | `nvidia-smi` during the run | two Python processes on the GPU |
| Every item gets a reply | `results/live/<run>/summary.json` | `turn_take_rate` 1.0 |
| Tool calls are logged | `results/live/<run>/agent_tool_calls.log` | one line per call |
| The scores match ours | `summary.json` against our reported run | within run-to-run noise |

Then run all 100 items (`bash reproduce.sh`, about 2 hours) and compare the
result with our reported run. Record the machine (GPU, driver, image, Python
version) and the outcome in the "Clean-machine test" row of
[SAMSUNG_REQUIREMENTS.md](SAMSUNG_REQUIREMENTS.md).

## 4. Clean up

```bash
gcloud compute instances delete duet-repro --zone=us-central1-a
```
