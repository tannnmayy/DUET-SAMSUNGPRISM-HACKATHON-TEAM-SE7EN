# Organizer clarifications

Answers from the Samsung PRISM organizers to Team SE7EN (SRM), received
before 22 September 2026. docs/SUBMISSION.md says clarifications are announced
to all teams and become binding. Where these conflict with the kit docs,
**these win**.

| # | Question | Answer | Consequence for DUET |
|---|---|---|---|
| 1 | Practice drop (TOOLS.md)? | None expected. Any future release will be announced to teams. | Our own unseen-tool scenarios are the only zero-shot signal. |
| 2 | Is the process reused across scenarios? | Yes. Only the first scenario pays model load against the 300 s `setup()` cap; later scenarios see instant setup. | Module-level model caches are correct. Repetitions share one process, so no state may leak between scenarios (tested). |
| 3 | Hugging Face reachable? Do downloads count against `setup()`? Vendoring? | pypi.org and huggingface.co are allowed. Downloads are **not** counted against `setup()`. No repository size limit. | Load time is no longer what limits model size; validation hardware and inference latency are. Pin model revisions, since scoring happens after the deadline. Do not vendor weights (GitHub's 100 MB per-file limit and LFS quotas still apply). |
| 4 | Firewall allowlist deadline? | No deadline; request early. | Nothing to request: we use only PyPI and Hugging Face. |
| 5 | CUDA / driver versions? | CUDA 12.x (12.4/12.6) or 13.2; can be arranged on request if it is a hard constraint. | CTranslate2 4.x (faster-whisper) is built for CUDA 12 + cuDNN 9. We request CUDA 12.x with cuDNN 9. |
| 6 | Per-scenario wall clock: 120 s or 300 s? | **300 s** (the FAQ supersedes the kit). | Not a constraint: our scenarios run in about 10 s. |
| 7 | One submission or daily? | A single final submission. No leaderboard, no feedback. | As assumed: local dry runs are the only signal. |
| 8 | Hidden audio: WAV or MP3? | Expect MP3; supporting both is advised. | The PyAV decoder reads both; a WAV path is tested. |
| 9 | Quality multiplier range? | x0.90-1.10 (the kit's value, not the guide's 0.80-1.20). | As assumed. |

## Follow-up sent (awaiting answer)

1. Provision CUDA 12.x (12.4 or 12.6) with cuDNN 9 on the library path.
2. How is download time excluded: a prefetch command we name, or excluded
   automatically inside `setup()`?
3. Confirm Linux x86_64, and that `python: "3.12"` in submission.yaml is honoured.
4. May the 15 October live demo use UI/microphone adapter code added after
   the tagged submission, if the graded agent is unchanged?
