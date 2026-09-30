# Results

The reported run: **all 100 recordings of Full-Duplex-Bench v3**, streamed in real time
through the benchmark's own runner and scored by its own evaluation scripts, on the code
in this repository ({{commit}}). The complete record, with every report, per-recording
result, conversation trace and log, is
[`results/reported/{{run_name}}`](../results/reported/{{run_name}}).

## Headline

| Metric | DUET | GPT-Realtime (paper) |
|---|---|---|
| **Pass@1**, strict: every expected tool, no extra call, every argument right | **{{pass_at_1}}** ({{passed}} of 100) | 0.600 |
| Tool selection | {{tool_f1}} | 0.876 |
| Argument accuracy | {{arg_acc}} | 0.680 |
| Response quality | {{resp_qual}} | 0.792 |
| Conversations answered (turn-take) | {{turn_take}} | 96.0% |
| Interruptions | {{interruptions}} | 13.5% |
| First response, as the benchmark measures it | {{first_response}} | |
| First tool call | {{tool_latency}} | |
| Key information spoken (task completion) | {{task_latency}} | 6.89 s |

{{judged_note}}

## How it was measured

- **The benchmark's pipeline, unmodified.** `bash reproduce.sh --force` runs
  `run_tool_benchmark_all_released.py --provider duet` and then `evaluate_tool_calls.py`,
  `evaluate_pass_rate.py` and `analyze_tool_latency.py` with `--use-llm`.
- **Tool metrics** come from the agent's call log exactly as the benchmark reads it
  (`/tmp/agent_tool_calls.log`): every call DUET actually made, with its arguments.
- **The machine:** a laptop with an RTX 3060 (6 GB) on Windows, a local LiveKit server, and
  Gemma 4 26B-A4B through Google's API with three keys.
- **Transcripts of DUET's own speech**, which the response-quality and latency scores read,
  come from faster-whisper here: the benchmark's Parakeet recognizer needs NVIDIA NeMo, which
  runs on Linux. The tool metrics, including Pass@1, do not depend on this.
- **The LLM judge.** The official judge is gpt-4o. This run was judged by Gemma 4 26B-A4B
  answering the official prompts unchanged (`bench/judge.py`). Samsung's re-run uses gpt-4o
  whenever an `OPENAI_API_KEY` is set.

## Pass@1 by kind of item

{{breakdown}}

## Where the remaining points are

Every recording that did not pass by exact match, read from its trace and classified:

| Kind | Recordings | What happened |
|---|---|---|
| **Format only** | 8 | The right tool and value in another form: "mechanical keyboard" for "keyboards", "the gym" for "Gym", "Vegas" for "Las Vegas". The benchmark's judge rules accept such differences |
| **Heard wrong** | 7 | The recognizer misheard a spelled-out code or amount ("D, E, L, bye, V" for DELIV; "B0, B1, B2" for BOB12; "$800" for 1800) or missed a word ("desk" heard as "yes"). DUET acted on what it heard, or asked for what it could not |
| **Provider stalls** | 3 | Every request for one model call went unanswered for 28 s (Google returned 504 Deadline Exceeded), despite backups on every key |
| **Conditional requests** | 3 | The expected calls contradict the benchmark's own mock data ("book it if a flight is under $300" expects the booking and the alternative, while the only flight costs $450); or the condition is the user's own judgement ("if the rate looks good to me") |
| **Not passable as scored** | 2 | The expected city is never spoken; the expected document number differs from the recording |
| **Judgement** | 2 | DUET asked a question instead of acting: once for a gift idea described only as "something in the electronics section", once waiting for a city the user never gave |

The first two rows are the ceiling of any agent that listens through a recognizer and is
scored by exact match; the judge recovers the format row. The conditional and unpassable
rows are properties of the benchmark items. What is left for DUET itself is small: provider
stalls and two judgement calls.

## Model requests

Every request to Google's API is logged in the run's `agent.log`:

{{requests}}

Transient errors are retried within about 1.5 s, and a request with no answer after 6 s gets
a backup on the key with the most room, so a burst of server errors costs seconds, not the
recording.

## Reproduce this page

```bash
bash reproduce.sh --force                               # a new run: results/live/<time>/
python bench/results_tables.py results/live/<time>      # the tables above, from its reports
```
