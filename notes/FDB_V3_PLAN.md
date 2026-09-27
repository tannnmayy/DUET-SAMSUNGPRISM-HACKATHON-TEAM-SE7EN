# DUET on Full-Duplex-Bench v3: plan (26 Sep 2026)

Samsung replaced the Theme 05 participant kit with the public **Full-Duplex-Bench v3**
(FDB-v3). The theme and goals did not change. What changed is how we are measured.

## 1. What is scored

| Part | Weight | What Samsung does |
|---|---|---|
| FDB-v3 benchmark | 60% | Re-runs our one-command reproduction script on one 48 GB NVIDIA GPU (CUDA 12/13), or our declared hosted APIs. Only their re-run counts. Ties break on strict pass rate. |
| Use-case extension | 20% | One real use case beyond the benchmark, running end to end and shown in the video. "Extend the dual mind: one mind talks, one mind thinks." |
| Docs, architecture, video | 20% | README (architecture diagram, exact setup, extension marked), an honest architecture explanation, run logs, and at most 8 slides. |

Mentor emphasis (meeting transcript): highly logical tasks without breaking conversational
fluency; responsive throughout; tasks fail and latency varies, so recover cleanly (retry,
close, human-in-the-loop); no static rule-based system; Gemini preferred (Samsung has its own
Gemini and Gemma keys); latency and cost savings earn extra credit; show run logs, not only
numbers. Rules: no hardcoding or memorising benchmark items, no calls to our own servers,
nothing cached across scenarios, and seeds and versions pinned.

## 2. How FDB-v3 actually measures (read from the code, not the README)

- **Input.** 100 real recordings (79 scenarios, 12 speakers, 36-59 s each). The spoken
  request comes first, with real disfluency: fillers, pauses up to about 1.5 s,
  hesitations, false starts and self-corrections. About 30 s of the speaker's real room
  noise follows. 83 files are mono 48 kHz int32, 10 are stereo, and 7 are 16 kHz int16.
- **Transport.** `livekit_inference.py` joins a LiveKit room as the "user", streams the
  WAV in real time, then 1.5 s of silence. It records the agent's audio for exactly
  the input's duration, so the answer must be spoken inside that window.
- **Tool calls.** The agent writes each call to `/tmp/agent_tool_calls.log` as
  `{"room", "call": {"function", "args", "timestamp_start", "timestamp_end"}}`. The
  runner matches rows by room name.
- **Scoring.** Parakeet-TDT-0.6B-v2 transcribes both the input (user end = end of the
  last word before the first gap longer than 2 s) and the agent's output. Four metrics
  are computed from that:
  - Tool-selection F1.
  - Argument accuracy, judged by gpt-4o semantically (dates, aliases, ±5 %, `$RESULT_n`
    references allowed).
  - Response quality, judged by gpt-4o against the expected assistant turn. Partial
    multi-step delivery scores 0, and so does a refusal.
  - **Pass@1**, which needs exactly the expected tools with correct arguments. One
    extra call fails the item, so a stale call made before a self-correction fails it.
- **Latency.** Three measures: first word, first tool call, and the key-information
  sentence (found by gpt-4o). Any agent speech before the user's end counts as an
  **interruption**.
- **Traps found in the data.**
  - Whisper hallucinates sentences in the noisy 30 s tail, so an agent that acts on
    them makes an extra call.
  - Several expected calls omit arguments the template schema makes required
    (`search_apartments` without a budget), or use optional filters the template lacks
    (`pets_allowed`).
  - The mock `search_apartments` returns no address, so the commute origin must be a
    plausible value from the previous result.

**Published baselines (paper, Table 2):**

| System | Pass@1 | Tool F1 | Arg | Resp | Task-completion latency | Interrupt |
|---|---|---|---|---|---|---|
| GPT-Realtime | 0.600 | 0.876 | 0.680 | 0.792 | 6.89 s | 13.5 % |
| Gemini Live 3.1 | 0.540 | 0.817 | 0.588 | 0.718 | 4.25 s | 19.2 % |
| Cascaded (Whisper→GPT-4o→TTS) | 0.450 | 0.803 | 0.562 | 0.600 | 10.12 s | 33.0 % |

Self-correction Pass@1 is the weakest area: 0.588 for the best system, 0.176 for the cascade.

The paper's own conclusion is our thesis: *"models commit intermediate parameters before
the correction arrives, and reliable rollback requires distinguishing provisionally set
values from explicitly confirmed ones."* That is DUET's M1/M2/M3 (epochs,
reversibility-gated commit, slot provenance), which we built for the old kit.

## 3. Architecture: DUET dual-mind agent (LiveKit custom agent)

```
LiveKit room audio ─► Silero VAD ─► ASR (local, GPU) ─► Turn manager ─┬─► Talker (fast mind): acknowledges in ~0.5 s,
                                    hallucination guard   (EOU model +  │     progress while tools run, final answer
                                                           disfluency    │
                                                           cues)         └─► Thinker (slow mind): Gemini, function calling,
                                                                             speculative plan while the user pauses,
                                                                             commit only when the turn is complete
                                                     DUET coordinator ◄──┘   (epochs, commit gate, idempotency ledger,
                                                                             retry/escalate on failure) ─► 12 tools (logged
                                                                             exactly like the benchmark) ─► TTS (Kokoro, local)
```

| Known failure mode (paper or data) | Our mechanism |
|---|---|
| Stale values committed before a self-correction | Tools run only after the turn is complete (LiveKit authorisation) plus a commit hold. The thinker re-plans over the whole open utterance ("latest value wins"). An epoch bump discards any plan made before new speech. |
| Pauses read as end of turn (weakest category) | Semantic end of turn: LiveKit's EOU model plus disfluency cues ("um", "and", "let me think", dangling clauses) with an adaptive wait of up to about 3 s. |
| Cascaded ASR drops the correction | Segments are appended to one open utterance and never finalised early. The thinker always sees the full turn text. |
| Hallucinated speech in ambient noise | VAD gate, and segments with high no-speech probability or low log-probability are dropped. The thinker never calls a tool on unintelligible or unrelated fragments. |
| Silent worker (tools ran, nothing said) | A final spoken answer is guaranteed after every tool chain, with a truthful fallback line. |
| Filler talking over the user | The talker speaks only after end of turn is confirmed, and never claims a result before the tool returns. |
| Multi-step and conditional chains | The thinker runs up to 6 tool steps, passes previous results (`$RESULT` ids) and applies conditions to actual results ("if the commute is over 15 minutes…"). |
| Tool failures and slow tools | Retry read-only calls once, never blindly re-send a state-changing call (idempotency ledger), say what failed, offer a human handoff, and give progress speech while a tool is slow. |

Models:
- Thinker: Gemini Flash through the Gemini API (the default, which Samsung can run
  without our key). Any OpenAI-compatible endpoint also works, including a local vLLM
  on the 48 GB GPU for a fully offline run.
- ASR: faster-whisper large-v3-turbo on the GPU.
- TTS: Kokoro-82M, local.
- VAD and end of turn: Silero and the LiveKit turn detector, both on CPU.

The whole stack stays far below 48 GB.

## 4. Evaluation method

1. **Offline harness** (minutes, not hours). It runs our perception, turn manager,
   thinker and tools over the 100 WAVs at accelerated time, and scores with the
   official functions. We use it for iteration.
2. **Official pipeline.** A local `livekit-server --dev` (or LiveKit Cloud) runs
   `run_tool_benchmark_all_released.py --provider duet` and the three official
   evaluation scripts. Its results and run logs are what we report.
3. **Integrity.** Prompts and schemas are written from general principles and the
   paper's failure taxonomy, never from individual items. We track a development split
   and report all 100. Any rule that names a benchmark entity is forbidden, and a grep
   test enforces it.

## 5. Deliverables and timeline

| When | Deliverable |
|---|---|
| 26 Sep night | Branch `fdb-v3`: agent core (tools, thinker, talker, turn manager, coordinator, ASR, TTS), offline harness, first numbers |
| 27 Sep | Official pipeline end to end on a local LiveKit server; latency and turn tuning; full 100-item run with logs; `reproduce.sh` |
| 28 Sep | Extension use case (after the user approves which one); README, architecture doc, results vs baselines, cost and latency analysis |
| 29 Sep | Clean-machine test (Linux, fresh venv); final docs; tag |

## 6. Risks

- **No Gemini key yet, so the thinker cannot be tested.** Build everything else now and
  run plumbing tests with stubs.
- **Rate limits on a free key.** 100 items × about 3 LLM calls: use a paid key or
  throttle.
- **The judge needs an OpenAI key (gpt-4o).** Without one, we report exact-match
  metrics plus a clearly labelled Gemini-judge estimate. Samsung runs its own pinned
  judge anyway.
- **NeMo (Parakeet) on Windows.** Local runs use a faster-whisper ASR shim for scoring
  only. The final validation runs on Linux.
- **The recording window closes at the input's length.** Keep task-completion latency
  well under the roughly 20-30 s tail.
