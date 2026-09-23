# DUET final sprint: 23 → 27 September 2026

**Submission: 27 September.** The PPT and the video are owned by the user and
are out of scope here. Everything else is in scope: the graded agent (the
harness Samsung runs on ~60 unseen scenarios), the live application, a Python
SDK, and an Android APK.

This file is the single source of truth for every agent working on the repo.
Read it fully before touching code, and re-read §2 (ground rules) before every
commit.

---

## 0. Where we start (measured 23 Sep, commit 4d08c2b)

| Measurement | Result | Meaning |
|---|---|---|
| `pytest` | 373 passed | the engine is well tested **on what it was built for** |
| `eval_submission.py . --time-scale 1` | 97.4 weighted (text 100, audio 100, visual 81.5) | the public nine are solved except naming the port in a photo |
| `tools/chaos.py --templates all --n 48 --seed 8675309` | 48/48 at 100.0 | our own templates are **saturated**: they can no longer find bugs |
| `tools/probes.py` on `tests/probes_dev/` (24 mixed) | **mean 74.6** | phrasing, tools and audio nobody tuned against |
| `tools/probes.py --pattern "probe_neg_*"` (8 re-skins of pub_04) | **mean 50.0 — 7/8 called a tool** | the negative-routing case is badly broken |
| Qwen3-VL-2B on `frames/pub_07_f017.png` | reads **HDMI** (full frame, 2 prompts agree) | the vision gap is closable with a newer model |
| `app/` | empty | there is nothing a person can talk to |

**Held-out set #1** (`E:\duet_holdout\set1`, 60 scenarios written blind by the
red-team agent, 30 text / 18 audio / 12 visual, 12 unseen tools, 9 proven-
discriminating interruption scenarios) on `main` 84e0c93, speech on CPU:
**mean 67.3** (text 66.4, audio 65.0, visual 73.0), 36/60 below 75. The largest
cluster is unseen tools never called correctly - meaning, not keywords ("three
hundred fifty dollars in euros", "I need someone out here urgently"). H6 is
therefore raised to **P0**.

Expected hidden-set score today: roughly 80-88, not 97 - and the held-out
number says the lower end or below. The work below is
ordered by how many hidden-set points it recovers per hour, then by what the
jury sees.

---

## 1. Deliverables

| # | Deliverable | Owner | Done when |
|---|---|---|---|
| D1 | Graded agent, hardened (§3) | agents H-A, H-B, H-C, V, FZ | all gates in §5 green, held-out probes up ≥ 12 points, zero regressions |
| D2 | Live application "DUET Live" - web client + server (§4) | agent APP | a person can talk, barge in, see tools cut, on one laptop, for 20 minutes without a crash |
| D3 | Python SDK: `pip install` the coordination layer (§4.6) | agent APP | wheel installs in a clean venv; `examples/console_chat.py` runs |
| D4 | Android APK: phone as the mic/speaker/camera of DUET Live (§4.7) | agent AND | APK installs on a real phone and completes a spoken barge-in |
| D5 | README sections for app, SDK and APK; Docker verified on Linux | orchestrator | a clean clone reproduces every number |
| D6 | Tag `PRISM_GENAI_HACKATHON_Y2026` on the final commit | user (orchestrator prepares) | tagged commit contains everything referenced |

Not ours: PPT, video. Also the user's: filling Samsung's `LangAI3.0_AI_Disclosure.docx`
(from the organisers' zip) - AI coding agents were used, so it must say so.

---

## 2. Ground rules for every agent (non-negotiable)

1. **Never modify** `harness/`, `scenarios/`, `docs/`, `run_local.py`, `eval_submission.py`,
   `audio/`, `frames/`, `KIT_README.md`, `WALKTHROUGH.md`.
2. `duet/` never imports `app/` or `harness/`. No tool name, city list, person
   list or scenario id inside `duet/` (grep-enforced by `tests/test_invariants.py`).
3. **Fix categories, never phrases.** Adding the failing sentence to a trigger
   list is a hardcode. Vocabularies are allowed only if they are ordinary,
   domain-free English (conversational acts, intensity words, communication
   verbs) and are documented as such.
4. **The dev set is for diagnosis; the held-out set decides.** `tests/probes_dev/`
   is visible to everyone. The held-out set lives outside the repo and is run
   only by the orchestrator. A change that helps dev but not held-out is reverted.
5. **Invariant 1 stays:** no event handler blocks the loop > 20 ms. Anything slow
   (models, LLM calls) is a task with a hard timeout and a rules fallback.
6. **Every new model** is pinned by repo + commit in `duet/perception/checkpoints.py`,
   added to `tools/prefetch.py`, loaded once per process in `setup()`, has a null
   backend, and is verified downloadable **anonymously** (no HF token: gated repos
   fail on the grading box).
7. **Speaking before doing:** never speak a commit acknowledgement ("booking ... now")
   for a call the coordinator has not accepted.
8. Keep `requirements.txt` and `submission.yaml` identical (`tests/test_packaging.py`).
9. **Commits:** small, one concern each, on your branch, message in the repo's
   existing style (see `git log`). **No AI attribution of any kind** (no
   Co-Authored-By line) - a hard project rule.
10. **Machine etiquette** (one laptop, one 6 GB GPU, several agents):
    - develop with `DUET_NO_ASR=1 DUET_NO_EMBED=1` unless your work needs a model;
      if you need speech, prefer `DUET_ASR_DEVICE=cpu`;
    - the GPU belongs to agent V unless the orchestrator says otherwise;
    - do NOT run the full `pytest` suite or `tools/gate.py` (the orchestrator
      runs gates; they are timing-sensitive and serialised by a lock);
    - run the specific test files you touched, `tools/probes.py --pattern ...`,
      and single scenarios with `run_local.py --time-scale 4` while iterating;
      scale 1 only for a final check of a latency-relevant change.
11. Report back with: what changed (files, functions), why (root cause),
    before/after numbers on the relevant probes/tests, and anything you found
    but did not fix.

---

## 3. The graded agent - work items

Each item: root cause (with file:line on commit 4d08c2b), the fix, acceptance,
and the failure points we must design around.

### H1. "Does this need a tool at all?" - negative routing (agent H-A) · P0

**Symptoms.** "What kinds of things can I ask you about?", "Thanks, that's
perfect", "Who built you?", "Can you hear me okay?" → `lookup_manual`.
"Hmm, let me think" → flight search to "Hmm". "I'm just talking to my brother"
(audio) → flight search to "Brother". "Honestly I'm bored" → flights to "Honestly I'm".

**Root causes.**
- `rules.py:492-500`: when no tool ranks above zero, `text_sink()` sends ANY
  utterance to the read-only free-text tool.
- `tools.py:441`: the "supplied" admissibility route makes a tool a candidate
  whenever its required roles are filled - and a place is "filled" by any
  `to X` phrase or by the capitalised-span fallback.
- `fastpath.py:739`: capability questions are a 12-phrase list.

**Fix.**
1. New `duet/router.py`: classify each complete turn as TASK / META (about the
   assistant: what can you do, who are you, can you hear me) / SOCIAL (greeting,
   thanks, goodbye) / HOLD (hold on, one sec, thinking aloud, talking to someone
   else). Two signals, combined:
   - structural (always available): request shape (imperative verb, question
     about a thing, explicit object), second-person-about-the-agent, strong typed
     values (identifier shape, model code, date + place);
   - semantic: sentence-embedding similarity (`sentence-transformers/all-MiniLM-L6-v2`,
     pinned, ~90 MB, ~5 ms on CPU) against (a) prototypes for each non-task act,
     written as generic English, and (b) each live tool's name + description.
   Degrades to structural-only when the model is missing.
2. A tool may be chosen only with task evidence: lexical score > 0, OR semantic
   similarity to its description above threshold, OR "supplied" values that came
   from a preposition phrase / identifier / model code - **never** from the
   capitalised fallback alone.
3. `text_sink` only when the router says TASK and the utterance describes a
   problem or asks about a thing (semantic similarity to the sink tool's
   description, or generic problem cues: broken, not working, error, won't,
   keeps, blinking, noise, stuck).
4. Non-task replies: META → capabilities (existing); SOCIAL-thanks/closing → a
   short courteous close ("You're welcome - anything else I can do?"); greeting
   → greeting + one-line capabilities; HOLD → "Sure, take your time." and no tool.
   All are `final_response` (latency + participation), none calls a tool.
5. Semantic tie-break in `rank()`: add a weighted embedding-similarity feature so
   "Is it going to rain in Chicago tomorrow?" reaches `weather_lookup` instead of
   `flight_search` (no shared word, but "rain" ~ "weather forecast").

**Acceptance.** All 8 `probe_neg_*` ≥ 95; `probe_chitchat_no_tool`,
`probe_thanks_no_tool`, `probe_audio_distractor`, `probe_weather_no_keyword` ≥ 95;
pub_04 still 100; **pub_09, conf_03, conf_07 (real requests with weak
vocabulary) still 100**; chaos unchanged.

**Failure points.**
- *False negative* (a real request classified as chat → no tool → task score 0):
  asymmetric thresholds - suppress a tool only when task evidence is weak AND a
  non-task prototype wins by a margin; measure on every suite, not just negatives.
- Embedding model missing on the grading box → structural fallback; covered by
  the kill switch run.
- Prototype list turning into a phrase list → prototypes are 5-10 generic
  sentences per act, never copied from a probe; reviewed at merge.
- Non-determinism → embeddings are deterministic on CPU; run the router on CPU.

### H2. Never promise before the gate; always follow a refusal (agent H-A) · P0

**Symptoms.** "Text Priya that I'm running late" → "Okay, sending the message now."
then nothing (gate refused: `low_confidence:0.45`). Spoken "…book the morning one
for Sarah Miller" → booking refused (`low_confidence:0.59`), agent reports only
the search result; the booking request silently vanishes.

**Root cause.** `runtime.py:931-938` speaks the commit acknowledgement, then
`_issue()`; on refusal nothing else happens. Same in the chained follow-up at
`runtime.py:755-762`.

**Fix.** Ask `coord.may_issue()` first. Accepted → acknowledge + issue.
Refused for `low_confidence` → a confirmation question naming exactly what would
be committed ("Just to confirm - book the 8 AM flight to Phoenix for Sarah
Miller?"), with a pending-confirm state: "yes" re-plans at confidence 1.0, a
correction re-values the slot. Refused for `invalid_args` / missing → ask for the
missing value by a human label (never "flight id"). `previous_outcome_unknown` /
`already_committed` → say so truthfully. Add a lint rule to `tools/quality.py`:
every commit acknowledgement must be followed by a state-modifying `tool_call`
(or be superseded by an interruption).

**Acceptance.** `probe_array_arg`, `probe_audio_chain_name` no longer silent;
new lint rule clean on all suites; conf_04/08/21/23 still 100.

**Failure points.** A confirmation question where the scenario expected action
costs task points → only when the gate would refuse anyway (never newly asks
when today's agent acts); scenario_end arriving while a confirmation is pending
→ the question itself is the final spoken action (no extra closing line).

### H3. Argument hygiene (agent H-B) · P0

**Symptoms.** `create_support_ticket.device.model` and `.serial` = the whole
sentence; `schedule_technician.device_model` = the whole sentence; boolean
`eco_mode` = a sentence; severity "medium" for "it's urgent"; "What's the
temperature in Paris in Celsius?" → city "Celsius"; "Text Priya …" → recipient
"Text Priya".

**Root causes.** `tools.py:626-629` - any ROLE_TEXT argument (the default role
when no hint matches) falls back to the full utterance, including optional ones
and non-text types; `tools.py:651-654` - required enums default to the middle
member; no extractor for model codes; sentence-initial verbs survive inside a
multi-word capitalised span (`fastpath.py:488`); the last "in X" wins even when
X is a unit or enum word.

**Fix.**
1. Utterance fallback only for genuinely free-text arguments (name/description
   says query, question, text, message, body, summary, description, details,
   note, comment, issue, problem, request, search) and only for type `string`.
   Optional arguments are omitted unless a real value was extracted.
2. Booleans: from cues on the argument's own words ("turn on / enable / with /
   switch on X" → true; "turn off / disable / without / no X" → false); else
   omitted (optional) or asked (required).
3. Model codes: shape-based extraction (a token mixing letters and digits, 2-10
   chars: QN90, WF45, S24, QN90A; also "Galaxy S24" style) → a MODEL role bound
   to arguments whose name/description mentions model (incl. nested
   `device.model`) and used for enum matching.
4. Ordinal enums (severity/priority/urgency-like): generic intensity words
   (urgent, asap, critical, emergency, immediately → top; minor, whenever, not
   urgent, low priority → bottom). Anything else still defaults but is marked
   *guessed* so H6 can override it.
5. Communication verbs (text, message, email, call, tell, ask, remind, ping) are
   not part of a name when they open the sentence.
6. A place candidate that equals an enum member or a unit word of the chosen
   tool is not a place ("in Celsius").
7. Arrays: a person → `[person]`; "Priya and Omar" → two items.

**Acceptance.** `probe_ticket_severity`, `probe_tech_nested`,
`probe_thermostat_bool`, `probe_weather_units`, `probe_array_arg` ≥ 90; every
tool_call in every suite validates against its schema with no fallback text in
non-text arguments (FZ checks this).

**Failure points.** pub_07 / conf_07 rely on the utterance filling `query` -
keep that path; conf_17/18 rely on number words - keep; unseen tools whose free
text argument has an unusual name (e.g. `prompt`, `content`) → include those
generic names; never drop a *required* argument silently (ask instead).

### H4. Multi-turn selection and producer chaining (agent H-B) · P0

**Symptoms.** Turn 2 "Book the cheaper one for Maria Lopez" books the $129
flight (the first row) instead of the $99 one. "Denver, Friday, book it." →
"Which flight id did you want?" and no search.

**Root causes.** New-turn selectors are never applied to held results
(`reselect()` runs only on interruptions, `runtime.py:691`). With no "flight"
word, `book_flight` wins on "book"; PLACE is not a wanted role, so "Denver" is
never extracted as a destination; the missing machine id is asked of the user.

**Fix.**
1. In `plan_turn`, when the turn carries a selector and results are held, apply
   it (reselect) before binding.
2. Producer chaining: if the chosen tool's missing required argument is an
   identifier (`<noun>_id`) and a read-only tool in the manifest produces that
   noun (same noun in its name + a search/find/list/lookup verb, or the id field
   in its `default_result`), plan the producer first and include its roles in the
   wanted roles. Generic, no tool names.
3. Never ask a user for a machine identifier; ask for what they know (which
   flight / which destination), or run the producer.

**Acceptance.** `probe_two_turn_book` and `probe_walkthrough_denver_book_it` ≥ 90;
pub_03, conf_06, conf_23 still 100.

### H5. Audio robustness (agent H-B) · P1

1. **Multi-clip turns:** re-transcribe the concatenated audio of all clips at end
   of turn (decode each clip with `faster_whisper.decode_audio`, concatenate,
   transcribe once), falling back to the joined per-clip texts. Fixes "Phoenix
   End Book". Latency cost lands on the tool call, not on first speech.
2. **Deferred generic acknowledgement:** speak "One sec, let me catch that" only
   if transcription has not produced something better within ~450 ms. Saves a
   filler per audio turn (budget 4, −0.25 each beyond) and sounds less canned.
3. **Calibration beyond four clips:** generate a calibration set (2 TTS voices ×
   20 utterances × clean/10/5/0 dB) and measure act-vs-ask rates of the M5 gate;
   retune thresholds only if the data says so.
4. Distractor speech is H1 (router → HOLD, no tool).

**Acceptance.** `probe_audio_*` ≥ 95; pub_05/pub_06 still 100 with both the GPU
and the CPU speech model; filler count on audio scenarios ≤ 3.

### H6. Semantic resolver - a small LLM where the rules must guess (agent H-C) · **P0** (raised 23 Sep after the held-out baseline), flag-gated

**Why.** Unseen tools need meaning, not keywords: "rupees" → `INR`, "Celsius" →
`metric`, "urgent" → `high`, "Is it going to rain?" → the weather tool. Tool
calls do not stop the latency clock, so a 0.3-0.9 s call costs only tail time.

**Design.** `duet/planner/resolver.py`, off by default (`DUET_RESOLVER=1`), called
only when the rules plan is uncertain: an enum marked *guessed*, a required
argument filled by fallback, no tool with lexical evidence while the router says
TASK, or a near-tie between tools. Input: the live manifest (compact), the
utterance, current slots, the rules plan. Output: strict JSON `{tool|none, args}`,
validated against the schema; rules values with strong evidence always win.
Greedy decoding, hard timeout 900 ms → rules plan. Candidates (all ungated,
Apache-2.0): `Qwen/Qwen3-4B-Instruct-2507`, `Qwen/Qwen3-1.7B` (already in the
local cache); `google/gemma-4-E4B-it` needs `transformers>=5.5` (we pin 5.4.0) -
evaluate in a separate venv only.

**Ship rule.** On by default only if held-out probes improve ≥ 3 points with zero
regressions anywhere and cold setup grows < 60 s on the grading GPU. Otherwise it
ships off and is documented as an optional mode.

**Failure points.** Hallucinated tool names/args → schema validation and "rules
win on strong evidence"; latency spikes → timeout; VRAM → sized for 48 GB with all
models; non-determinism across the 3 graded repetitions → greedy decoding only.

### H7. Vision that can name what it sees (agent V) · P1

**Evidence.** Qwen3-VL-2B (transformers 5.4.0 supports `qwen3_vl`) on pub_07:
DUET's prompt → "HDMI port, TEXT: HDMI" on the full frame and lower half (manual
returns the HDMI page first); a direct "which port?" prompt → hdmi on 3/4
variants. Requiring the two prompts to agree: 2 right, 2 abstain, 0 wrong.

**Fix.**
1. `vision.py`: processor has no chat template for Qwen3-VL → fall back to
   `processor.tokenizer.apply_chat_template`.
2. Agreement gate: identified only if two independent prompts name the same
   component (normalised); otherwise the existing honest "I could not tell"
   path. Carry printed text (OCR) into the query for error-code questions.
3. Model: bench `Qwen/Qwen3-VL-2B-Instruct` locally (already in
   `E:\hf_probe_cache`) and `Qwen/Qwen3-VL-4B-Instruct` on the GPU host; pin the
   winner. Enable by default only after the bench passes.
4. Bench (`tools/vision_bench.py`, extended): the pub_07 variants plus ≥ 20 real
   photos the team takes (laptop ports, TV back panels, phone ports, washer
   display codes, TV status LEDs) with expected labels in a JSON sidecar.
   **Pass:** confidently-wrong ≤ 5 %, correct ≥ 60 %, the rest abstain.
5. Timing: frame→question gap is 500 ms in pub_07; `FRAME_WAIT_MS` = 1500. Measure
   two prompts on the A6000-class GPU; keep total ≤ 1.5 s.

**Acceptance.** pub_07 and conf_05 = 100; bench passes; kill switch unchanged.

**Failure points.** A wrong label fails two checkpoints (grounding and
not-hedging) → agreement gate; model download size/time → prefetch + pinned
revision; GPU contention on the laptop → V owns the GPU.

### H8. What the agent says - quality multiplier (agent H-C) · P2

- Times spoken as people say them ("8 AM", not "08:00").
- Corrections without reading ids aloud ("Got it - the 2 PM flight instead").
- Offer alternatives when several rows came back and none was chosen ("8 AM for
  $129, or 2 PM for $99 - want me to book one?"), keeping the destination and
  the id in the sentence (checkpoints match `fl-xxx` / city names).
- No field-name leakage ("status text", "eta", "the track for OR-5521").
- An LLM-judge pass over every transcript of every suite (four dimensions,
  0-5): target ≥ 4.3 average, zero absurd lines.

### H9. Fuzzing (agent FZ) · P1

- Schema fuzzer: random valid manifests (strings, numbers, booleans, enums,
  arrays, nested objects, missing descriptions, odd names) × generated
  utterances → no crash, no protocol error, every `tool_call` validates against
  its schema, no fallback text in non-text arguments, no state-modifying call on
  a guessed value.
- Event fuzzer: malformed payloads, missing/corrupt media, duplicate events,
  empty interruptions, results for unknown call ids, `scenario_end` early, frames
  with no question.
- Found bugs are reported to the orchestrator (not fixed in engine files FZ does
  not own).

### H10. Grading-platform validation (orchestrator + user's GPU host) · P0, Day 3

On a Linux GPU host, fresh Python 3.12 venv: `pip install -r requirements.txt` →
`pytest` → `eval_submission.py . --reps 3` → `tools/chaos.py` → `tools/killswitch.py`
→ `tools/clockcheck.py` → vision bench → cold `setup()` time with **all** models
and peak VRAM → `docker build` + `docker run --gpus all`. Script:
`tools/gpu_validate.sh` (to be written), output one JSON the user sends back.

---

## 4. The application - "DUET Live"

The jury rubric (launch deck): *Does the prototype actually work? Is the
approach sound? Would a real user want this? Can it be taken further as a
worklet?* The theme box asks for **multimodal inputs and multimodal outputs**.
The app is where both show.

```
Browser or Android APK (app/static)                     Server (app/server.py)
  mic -> Silero VAD (vad-web, vendored)                   one LiveSession per socket (duet/live.py)
    speech start -> stop TTS locally (barge-in) --ws-->     busy? (tools in flight or agent speaking)
    speech end   -> WAV ------------------------ws-->       not busy -> user_audio_chunk (graded path, M5 live)
                                                           busy     -> transcribe -> interruption{text}
  text box / BARGE-IN / camera / manifest editor --ws-->  user_speech_chunk / interruption / video_frame
  speech out + cards + timeline + slots <--------ws---   actions + tool lifecycle + epochs + latency
                                                          tools: MockEnvironment + demo manifests (app/tools_env.py)
```

### 4.1 `duet/live.py` - LiveSession (the SDK core; must not import harness/app)

- `LiveSession(manifest, executor, on_output, *, reset_idle_s=None)`;
  `user_text(text)`, `user_audio(path)`, `barge_in(text)`, `frame(path, hint)`,
  `set_agent_speaking(bool)`, `reset()`, `close()`.
- Drives a `DuetAgent` through its two queues exactly like the harness does:
  stamps events with ms since session start, executes `tool_call` via an
  injectable async `executor`, cancels on `cancel_tool`, feeds `tool_result` back,
  forwards every spoken action and tool lifecycle change to `on_output`.
- Turn vs interruption: a new utterance while the agent is busy (pending or
  deferred calls, or speech still playing) is an `interruption`; otherwise a turn.
- Live-mode realities (found in the audit): `config.FILLER_ABSOLUTE_CAP = 8` per
  agent would silence a long session → raised for live sessions; the phrasebook
  never repeats, so long sessions drift into fallback lines → fresh agent per
  conversation (reset button + idle reset); there is no `scenario_end` → send one
  on reset/close.

### 4.2 `app/tools_env.py` - tool executors and demo manifests

MockEnvironment-backed executor (flights, bookings, manuals, tickets) plus
`app/manifests/*.json`: **smart home** (lights, thermostat, washer with state you
can see change), **device support** (manual + ticket + technician), **travel**
(the kit's tools + hotels). Every manifest follows docs/TOOLS.md conventions, so
the unmodified agent handles them zero-shot.

### 4.3 `app/server.py`

FastAPI + uvicorn (websocket). Loads models once at startup through the same
`duet.perception.load_*` functions. Endpoints: `/` (client), `/ws`, `/api/manifests`,
`/api/replay` (run a scenario file through the real harness and stream its trace -
"judge mode"), `/health`. Transcribes barge-in audio with the shared ASR model.

### 4.4 `app/static` - the client (no build step)

- Input: hands-free mic (VAD) and push-to-talk, text box, BARGE-IN button, camera
  snapshot / image upload, manifest picker + JSON editor.
- Output (multimodal): speech (Web Speech API; Kokoro optional), result cards
  (flight options, booking confirmation, manual page, device state), a live
  timeline (user / agent speech / tool bars cut at the cancel, epoch markers), a
  slot panel with provenance (source, confidence, epoch), latency meters
  (end-of-speech → first audio, barge-in → speech stopped, interruption → cancel).
- Judge mode: pick any scenario (public, conformance, probes) and watch the real
  agent run it on the timeline with its score.

### 4.5 Latency and duplex targets

End of user speech → agent audio starts: p50 ≤ 1.2 s. Speech onset → TTS stopped:
≤ 300 ms. Barge-in → stale tool cancelled on screen: ≤ 1.0 s after the user stops.
20-minute session: zero crashes, zero stuck states.

### 4.6 Python SDK (agent APP)

`pyproject.toml` (distribution `duet-runtime`, package `duet`), public API =
`duet.live.LiveSession` + `DuetAgent`; `docs/SDK.md`; `examples/console_chat.py`
(type to talk, live mock tools, barge-in with Ctrl-C/`!`), `examples/custom_tools.py`
(bring your own manifest + executor). Build a wheel and test it in a clean venv.
Story: DUET drops between any speech stack (Bixby, Pipecat, LiveKit) and any
action layer (SmartThings) - the PRISM-worklet path.

### 4.7 Android APK (agent AND, starts Day 2 once the client exists)

Capacitor wrapper around the same `app/static` client (one codebase):
server URL set in-app (settings + QR code), RECORD_AUDIO / CAMERA / INTERNET
permissions, WebView media permission grant, cleartext to a LAN server
(`network_security_config`), plus PWA manifest + service worker as the no-build
fallback. Build path A: local (JDK 21 + Android command-line tools on E:,
Java 24 present is too new for some Gradle/AGP combos). Build path B: GitHub
Actions (ubuntu runners ship the Android SDK) - needs the user's OK to push.

---

## 5. Gates and measurement

| Gate | Command | Must hold |
|---|---|---|
| quick (agents) | `python tools/gate.py --quick` | fast unit tests pass; dev probes not worse |
| full (orchestrator only, serialised) | `python tools/gate.py --holdout <dir>` | pytest all pass · eval weighted ≥ 97.4 · quality lint clean · chaos mean 100 on a new seed · dev probes up · held-out up |
| platform (Day 3) | `tools/gpu_validate.sh` on Linux GPU | same numbers on Linux + py3.12, reps 3 within 5 points, setup < 240 s cold |

Targets by 27 Sep: dev probes 74.6 → ≥ 92, negatives 50 → ≥ 95, held-out
baseline + ≥ 12, pub_07 100, zero regressions.

---

## 6. Agents, ownership, schedule

| Agent | Work | Owns (may edit) | Start |
|---|---|---|---|
| RT red team | held-out probe set #1 (~60 scenarios, 50/30/20 text/audio/visual) from the kit docs only; set #2 on Day 3 by a fresh agent | `E:\duet_holdout\` (outside repo) | now |
| H-A routing | H1, H2 | `duet/router.py` (new), `duet/tools.py` rank/text_sink/best, `duet/planner/rules.py` plan_turn/_plan_from_state routing, `duet/fastpath.py` capability/greeting, `duet/runtime.py` _execute/_issue/follow-up, `duet/nlg.py` new pools, `tools/quality.py` lint | now |
| H-B binding | H3, H4, H5 | `duet/tools.py` build_args/_value_for/_pick_enum/_coerce, `duet/fastpath.py` extract_*/selectors, `duet/planner/rules.py` reselect/producer chaining/harvest, `duet/runtime.py` audio assembly, `duet/perception/asr.py` | now |
| V vision | H7 | `duet/perception/vision.py`, `checkpoints.py` (VLM row), `config.py` (vision), `tools/vision_bench.py`, `tests/vision_bench/` | now |
| FZ fuzz | H9 | `tests/test_fuzz_*.py` (new) | now |
| APP | §4.1-4.6 | `duet/live.py` (new), `app/**`, `examples/**`, `docs/SDK.md`, `pyproject.toml`, `requirements-app.txt` | now |
| H-C | H6, H8 | resolver (new), NLG phrasing | Day 2, after H-A/H-B merge |
| AND | §4.7 | `app/android/**`, `.github/workflows/android.yml` | Day 2 |

Overlaps (H-A/H-B in `tools.py`, `rules.py`, `runtime.py`, `fastpath.py`) are by
function; the orchestrator merges and resolves.

| Day | Orchestrator milestones |
|---|---|
| Wed 23 | agents launched; held-out baseline measured; first H-A/H-B/FZ reports |
| Thu 24 | H-A, H-B merged through the full gate; H-C and AND launched; vision decision (2B locally); app talks end-to-end |
| Fri 25 | GPU-host validation (H10) incl. Qwen3-VL-4B bench and Docker; resolver decision; APK builds; SDK wheel |
| Sat 26 | held-out set #2; NLG judge pass; app 20-minute soak; README for app/SDK/APK; **engine freeze 20:00** |
| Sun 27 | final gate on Linux GPU; tag prepared; user pushes, tags, submits |

**Cut order if late** (first cut first): Kokoro voice → resolver on-by-default →
APK local build (keep PWA) → judge-mode replay → 4B vision (keep 2B or keep off).
Never cut: H1-H4, H10, the app's talk-and-barge-in loop, gates.

---

## 7. Failure-point register

| # | Failure point | Mitigation |
|---|---|---|
| F1 | Parallel agents break each other's work | ownership table §6, worktrees, orchestrator-only merges, small commits |
| F2 | Parallel timing-sensitive runs create phantom latency failures | agents never run full suites; `tools/gate.py` holds a global lock |
| F3 | Overfitting to the dev probes | held-out sets outside the repo written by agents that never read `duet/`; revert rule |
| F4 | A fix regresses the public nine or conformance | full gate on every merge; weighted ≥ 97.4 is a hard floor |
| F5 | Real requests misread as chat (router false negatives) | asymmetric thresholds; every suite must stay at 100 |
| F6 | A new model is gated/renamed/unavailable on the grading box | pin repo + commit; anonymous download check; null backend; kill switch |
| F7 | `setup()` exceeds 300 s with more models | load models concurrently; per-model timeout inside setup → null backend; measure cold on GPU host |
| F8 | Non-determinism across the 3 graded runs | greedy decoding, CPU embeddings, no sampling anywhere |
| F9 | Model latency pushes the final answer past the 6 s tail | hard timeouts with rules fallback; frame wait bounded |
| F10 | A vision model names the wrong thing | two-prompt agreement or abstain; bench before enabling |
| F11 | Filler budget blown on multi-turn audio | deferred generic acknowledgement (H5.2) |
| F12 | Linux / Python 3.12 / CUDA 12 never exercised | H10 on the GPU host; Docker built there |
| F13 | Disk space (C: ~12 GB free, models there) | new model downloads go to `E:\hf_probe_cache` (`HF_HOME`) during development |
| F14 | 6 GB laptop GPU contention (Whisper + VLM + app) | V owns the GPU; others on CPU; the app demo uses the 2B model |
| F15 | Echo: the agent's own voice triggers barge-in | headset for recording; TTS through an `<audio>` element (AEC reference); VAD threshold raised while speaking; push-to-talk fallback |
| F16 | Mic needs a secure context on a phone | laptop uses `localhost`; phone uses the APK (Capacitor local scheme) or HTTPS |
| F17 | CDN unavailable where the demo runs | vendor vad-web and onnxruntime-web into `app/static/vendor` |
| F18 | Long live sessions degrade (filler cap, phrase exhaustion) | per-conversation agent, idle reset, live-mode cap |
| F19 | Unscripted speech misroutes live | typed fallback, verified demo lines, judge-mode replay |
| F20 | Phone and laptop cannot reach each other (campus Wi-Fi isolation, firewall) | laptop hotspot; firewall rule for the port; configurable URL; tunnel as last resort |
| F21 | Android toolchain missing (no SDK/Gradle; Java 24 too new) | JDK 21 + cmdline-tools on E:, or GitHub Actions build; PWA fallback |
| F22 | Tagged commit misses a referenced file | pre-tag script checks every README link and artefact exists at the tag |
| F23 | Large files in git (models, recordings) | `.gitignore`; size check before commit; APK < 20 MB is fine |
| F24 | Hardcoding review | no names/cities/tools in `duet/`; router prototypes and cue words are generic English and documented |
| F25 | Android WebView has no Web Speech API: the agent would be silent inside the APK | one speech abstraction in the client; native TTS via a Capacitor plugin in the APK, Web Speech in browsers |
| F26 | Downloads fail on Windows certificate-revocation checks, and the link is slow (~100 KB/s) | `curl --ssl-no-revoke`; resumable downloads; toolchain and caches on E:; start long downloads early |
| F27 | The session usage limit stops every agent at once | small commits after each working piece; agents resumed from their transcripts after the reset |

---

## 8. Open items needing the user

1. GPU host access for H10 (SSH to a Linux box, or a notebook we can run
   `tools/gpu_validate.sh` in).
2. OK to push branches / use GitHub Actions for the APK build.
3. ~20 photos of real device ports/panels/displays for the vision bench (phone
   camera is fine), with what each shows.
4. An Android phone for the APK test on Day 3.
5. The checklist that mentions the SDK/APK option (not found in the organisers'
   zip), to match its exact wording.
