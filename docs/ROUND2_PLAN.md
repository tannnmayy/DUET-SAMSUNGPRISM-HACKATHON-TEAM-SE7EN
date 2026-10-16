# DUET for Galaxy, Round 2: goals, scope and implementation plan

**Status:** v2, locked 16 Oct 2026.
- **Demo:** Tuesday **21 October**, online: presentation, live demo, then Q&A.
- **Phone:** Galaxy S24 (SM-S921E, Exynos 2400, 8 GB RAM, One UI 8.5 / Android 16).
- **Language:** English only.
- **Plan:** [section 0](#0-the-21-october-plan-what-we-actually-build-in-five-days) is what we build for the demo. Sections 5–9 describe the full phone-first target, which continues after 21 Oct as the worklet roadmap.
**Context:** Team SE7EN (SRM) is in the top 12 of 250+ teams. Round 2 is a live jury demo: "your agent,
interrupted live, plus questions on your design". The jury asks four things: does it actually work, is the
approach sound, would a real user want it, and can it become a PRISM worklet. The benchmark round is over; the
tagged Round 1 commit (`22ca240`, tag `PRISM_GENAI_HACKATHON_Y2026`) stays frozen.

---

## 0. The 21 October plan: what we actually build in five days

### 0.1 Why the scope changes

There are four and a half working days to the demo. The full phone-native engine (Track B: Kotlin rewrite,
on-device speech recognition, on-device Gemma, echo cancellation, tools) is two to three weeks of careful work and
device testing. Rushing it would put an unproven engine in front of the jury. The plan's own cut line applies: for
21 October we ship **Track A+**, the proven stack made fast, broad and robust on the real S24. We add the one idea
that matters most from the phone-first design: **two speeds**. A local Gemma fast mind runs on our own hardware
next to a cloud Gemma slow mind, both behind the same coordinator.

```
 Galaxy S24 · DUET app                       Laptop (our edge machine)                       Cloud
 ┌───────────────────────────┐   WebRTC   ┌────────────────────────────────────┐  HTTPS  ┌──────────────┐
 │ mic + speaker (echo cancel)│◄──LiveKit─►│ ears: Silero VAD, Whisper, turn    │◄──────►│ Gemma 4 26B  │
 │ live timeline UI           │    Cloud   │ FAST MIND: Gemma 4 E4B (llama.cpp, │         │ slow mind:   │
 │ hands: real Android actions│◄───RPC─────│   local GPU): simple actions ~1-2 s│         │ hard requests│
 │  (Clock, Maps, calls, SMS, │            │ COORDINATOR: gate, epochs, ledger  │         │ web answers  │
 │   settings, battery, apps, │            │ tone: SenseVoice tags (stretch)    │         └──────────────┘
 │   calendar, torch, media)  │            │ mouth: Kokoro                      │
 └───────────────────────────┘            └────────────────────────────────────┘
```

**What we say to the jury, honestly:**
- Today the brain runs on our own edge machine plus Google's cloud.
- The same split (small Gemma local, big Gemma in the cloud, one coordinator) moves onto the phone with Google's on-device runtime: Gemma 4 E2B runs at 52 tokens/s on a Galaxy S26 Ultra.
- That move is the worklet. If the stretch item lands, we show Gemma E2B running on our S24 itself.

### 0.2 Gates for 21 October (Track A+ on the S24)

| # | Gate |
|---|---|
| G1 | At least 12 real capabilities work on the S24 (section 6 list) |
| G2 | Correction suite (at least 25 scripted trials) plus at least 10 live trials on the S24: **0 stale actions, 0 duplicates**, final intent correct in at least 95% |
| G3 | Acknowledgement p50 ≤ 1.2 s · simple action confirmed p50 ≤ 2.5 s (local fast mind) · cloud answer, first words p50 ≤ 6 s · barge-in stop ≤ 0.5 s |
| G4 | At most 1 false interruption per 10 minutes, on loudspeaker or Buds |
| G5 | Cloud lost mid-request: an honest spoken fallback within 3 s, and simple actions keep working on the local fast mind |
| G6 | (stretch) Tone tags advisory; Care distress check-in |
| G7 | No false claim anywhere (app, deck, talk). The timeline shows edge or cloud for every step |
| G8 | 5 clean dry runs in the real online setup, plus backups |

### 0.3 Day by day

**Day 0 · Thu 16 Oct (today)**
- [x] Plan locked; branch `round2` created; plan committed.
- [x] Laptop stack verified: 3 of 3 Gemma keys reach Gemma 4 26B, tool calling OK; local llama.cpp supports Gemma 4.
- [ ] Baseline on the laptop: the 8 fake-phone scenarios, with per-stage timings.
- [ ] Per-stage latency in the timeline events: end of speech, turn closed, ack, plan, action, answer.

**Day 1 · Fri 17 Oct: the S24, and the must-fixes**

S24 bring-up:
- [ ] USB debugging, install, permissions, all three modes.
- [ ] Baseline on the device: latency, loudspeaker echo, barge-in, alarm set and cancel in Samsung Clock.
- [ ] Alarm path decision (D10).

Fixes:
- [ ] Remove the false footer.
- [ ] Consent check and second look working with the Gemma backend.
- [ ] Template confirmation for simple phone actions (no second model call).
- [ ] Phone-mode turn timing (shorter end-of-turn delay; ack after 0.4 s of quiet; `keep_listening` still guards pauses).
- [ ] Per-mode recogniser vocabulary.
- [ ] Any S24 audio-route or echo bug.

**Gate:** the 8 scenarios work live on the S24; before and after numbers recorded.

**Day 2 · Sat 18 Oct: two speeds, and reach**

Two speeds:
- [ ] **Local fast mind:** Gemma 4 E4B (E2B if the GPU is tight) on llama.cpp.
- [ ] **Router:** simple, unambiguous phone actions stay local; anything else goes to Gemma 26B; uncertain means escalate.
- [ ] One coordinator gates both minds; the timeline shows "edge" or "cloud".

New capabilities:
- [ ] Real contacts.
- [ ] Calendar event.
- [ ] Weather (Open-Meteo).
- [ ] Web answers (Gemma grounding, if the probe passes).
- [ ] Torch.
- [ ] Media keys.
- [ ] Keep listening while Maps is open.
- [ ] SmartThings, after the account and an explained step list.

**Gate:** G1; simple local action p50 ≤ 2.5 s.

**Day 3 · Sun 19 Oct: robustness, then freeze**

Failure drills:
- [ ] Cloud lost mid-request.
- [ ] Tool timeout: unknown outcome, offer a human.
- [ ] Key exhausted: local fallback.
- [ ] Incoming call.
- [ ] App sent to the background.

Testing and polish:
- [ ] Correction suite: extend the fake-phone scenarios to at least 25; run them; 10 live trials on the S24.
- [ ] Timeline polish: per-step latency, edge/cloud badge, **Undone** state; slot view if time.
- [ ] Stretch: tone (SenseVoice tags, advisory) and the Care distress check-in.
- [ ] **Feature freeze at night.**

**Gate:** G2, G5.

**Day 4 · Mon 20 Oct: rehearse**

Online set-up test:
- [ ] Meeting app, phone mirrored with scrcpy, audio path (what the jury hears).
- [ ] How the jury's voice reaches the phone, if they want to interrupt it themselves.

Rehearse and prepare:
- [ ] 5 full dry runs; only bug fixes from here.
- [ ] Round 2 deck and talk track; Q&A bank (about 50 likely questions on design decisions and trade-offs, with answers).
- [ ] Backups: recorded run, web build in a laptop browser, second network.

**Gate:** G7, G8.

**Day 5 · Tue 21 Oct: demo day**
- [ ] Morning smoke test: keys and quota, agent warm, phone charged, Do Not Disturb on, notifications off.

**Stretch, only if Days 1–3 are green early:**
- [ ] Gemma 4 E2B on the S24 itself (LiteRT-LM; the Exynos 2400 GPU or CPU backend), running one offline flow ("set an alarm for 6:30… no, 7" with airplane mode on), as the proof of the phone-first roadmap.

---

## 1. The product in one paragraph

**DUET for Galaxy** is a full-duplex voice assistant that runs on a Samsung Galaxy phone. You open it (or press
the side button) and talk the way people talk: interrupt it, hesitate, correct yourself, change your mind. It acts
on what you finally meant, using the phone's real functions:
- **Speed:** simple things in about a second, harder things in a few seconds with no dead air.
- **Offline:** core actions work without internet, because the fast mind runs on the phone.
- **Safety:** never on a word you took back, never twice, and never claiming something is done before it is.
- **Tone:** it hears *how* you say things as well as *what* you say.

## 2. Goals: what "done" means, with numbers

Every goal has a gate measured on the real phone. Nothing counts as done until its gate passes.

| # | Goal | Gate (measured on the Galaxy) |
|---|---|---|
| **G1** | **Real actions on a real Galaxy** | At least 12 capabilities from [section 6](#6-capabilities) work end to end on the device, each with a recorded clip as evidence |
| **G2** | **Correction safety** | Galaxy Correction Suite (at least 40 scripted trials) plus 10 live human trials: **0 stale actions, 0 duplicate state-changing actions**, final intent correct in at least 95% |
| **G3** | **Latency** (from the end of the user's speech) | Acknowledgement p50 ≤ 0.5 s, p90 ≤ 0.8 s · simple action done and confirmed p50 ≤ 1.5 s, p90 ≤ 2.5 s · cloud-reasoned answer, first words p50 ≤ 4 s, p90 ≤ 7 s · barge-in: DUET stops talking within 0.3 s |
| **G4** | **Full-duplex feel** | User can interrupt any reply · a cough or "mm-hm" never cancels a plan · DUET's own voice on the loudspeaker causes at most 1 false interruption per 10 minutes |
| **G5** | **Works offline and degrades honestly** | Core actions pass with airplane mode on · losing the network mid-request gives a spoken, honest fallback within 3 s, never silence |
| **G6** | **Hears how you say it** | Tone tags reach both minds · the Care distress check-in works in demo trials · **tone alone never triggers or cancels an action** (tested) |
| **G7** | **Honest and transparent** | The timeline shows every step and *where it ran* (phone or cloud) · every simulated part is labelled · zero false claims in the app, deck or talk |
| **G8** | **Jury-proof demo** | 5 consecutive clean dry runs of the demo script · failure drills pass · backups ready ([section 10](#10-demo-plan-draft)) |
| G9 | Stretch | Side-button default assistant · real SmartThings device · Hindi/Hinglish basics · fine-tuned on-device Gemma for phone intents |

## 3. Scope

**In scope:**
- An Android app for Galaxy phones (Android 14+, arm64), in three modes:
  - **Assistant:** everyday phone use and troubleshooting.
  - **Care:** an older person living alone, and their family.
  - **Drive:** hands-free, eyes on the road.
- An **on-device engine**: ears, fast mind, coordinator, hands, mouth.
- A **cloud slow mind**: Gemma 4 26B-A4B through Google's API.
- The tone channel, the live timeline UI, offline mode, an on-device test suite, and the demo.

**Out of scope:**
- **Platform limits:**
  - The Android Auto screen, which allows no third-party voice assistants.
  - Acting inside other apps (sending WhatsApp messages, booking an Uber), which would need an accessibility service and runs into Play policy.
  - Payments.
  - Bixby agents and AppFunctions, which need privileged permission. These are a worklet item.
  - An always-on wake word.
- **Not needed for the demo:** iOS and voice ID.
- **Frozen:** any change to the Round 1 benchmark path.

**Labelled as simulated:** the car (climate, trip progress) and, unless we get real devices, the smart home
(SmartThings virtual devices are a real Samsung API, so "virtual" rather than "simulated").

## 4. Rules for every design choice

1. **The model proposes; deterministic code disposes.** Models suggest actions. The coordinator decides whether and when they run.
2. **Plan early, commit late, act once.** Thinking may start on partial words. Acting waits for the gate. Nothing runs twice.
3. **The fast voice never says a value** a correction could change, and never claims a result.
4. **Words change plans; noise only delays them.**
5. **Tone is advisory.** It may change wording, speed, the hold length, or trigger a confirmation. It never acts or cancels on its own.
6. **Local first** for speed, privacy and offline use; **cloud for depth**. Never silent.
7. **Every step is visible** on the timeline and in the trace, with where it ran.
8. **Models are configuration; the coordinator is the product.** Any model can be swapped; the guarantees stay.
9. **Decide by measurement.** Every open choice in [section 5](#5-architecture) is settled by a test on the phone, not by opinion.
10. **Always shippable.** At every point there is a build we could demo tomorrow (Track A until Track B is ready).

## 5. Architecture

### 5.1 Target: phone-first DUET (Track B)

```
 GALAXY PHONE · DUET app (Android, Kotlin engine + existing web UI)                CLOUD
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ Foreground service (type microphone): keeps listening while Maps is open   │
 │                                                                            │
 │ EARS   mic (VOICE_COMMUNICATION, echo cancel) ─► Silero VAD ─► ASR         │
 │        streaming partials + segment final ─► tone: prosody + SenseVoice tags│
 │                                                                            │
 │ FAST MIND  phrase bank (instant acks, value-free, tone-aware)              │
 │            Gemma 4 E2B (LiteRT-LM, GPU): keep_listening, routing,          │──► SLOW MIND
 │            simple tool calls, read-backs                                   │    Gemma 4 26B-A4B (REST)
 │                                                                            │◄── multi-step plans,
 │ COORDINATOR (Kotlin port of coordinator.py): epochs · settle · gate holds ·│    web answers (grounding),
 │            ledger · failure policy · undo · slot view                      │    diagnosis, Care talk
 │                                                                            │
 │ HANDS  tool manifests (JSON) ─► native tools: Clock, calendar, Maps,       │
 │        contacts/calls/SMS, settings, battery, apps, media, torch,          │──► SmartThings REST
 │        weather, SmartThings, Care DB                                       │    Open-Meteo (weather)
 │                                                                            │
 │ MOUTH  Android TextToSpeech (Samsung engine), Kokoro optional, instant stop│
 │                                                                            │
 │ UI     Capacitor web UI (modes, timeline, panels) ◄── engine events (v1)   │
 └────────────────────────────────────────────────────────────────────────────┘
```

**One turn, simple action:**
1. User: "Set an alarm for 6:30, no, 7."
2. The ears produce partial text while the user speaks.
3. The fast mind routes it as simple and drafts `set_alarm(07:00)` from the full words.
4. At the end of speech (+0.3 s), the phrase bank says "Sure."
5. The gate holds 1.8 s (the user was revising), then the action runs on the device.
6. A template confirms: "Alarm set for 7 in the morning."
7. The timeline shows the 6:30 plan as **Never ran** and the 7:00 alarm as **Done**.

**One turn, hard request:**
1. User: "My battery dies so fast, what's going on?"
2. The ack plays at 0.3 s.
3. The router sends the request to the cloud. The slow mind calls `get_battery_report`, which the phone runs locally.
4. The slow mind answers with the facts.
5. The user cuts in, and the coordinator handles it exactly as in the benchmark.

### 5.2 Fallback: today's stack on the phone (Track A)

The current app: a LiveKit room to the laptop agent (`duet_voice/galaxy`), with Gemma through the API and Whisper
and Kokoro on the laptop.
- **Fixed in Phase 0** and kept as an **engine switch** in the same APK (Settings → Engine: *On-device* | *Server*).
- The web UI reads the same event schema from either engine, so the switch costs nothing.
- If Track B is not ready by demo day, Track A is the demo.

### 5.3 Decisions, and how each is settled

| # | Decision | Default | Settled by |
|---|---|---|---|
| D1 | UI | Keep the Capacitor web UI; the engine is native Kotlin inside a foreground service; events go through the plugin | Locked (reuses the finished UI) |
| D2 | Speech recognition | Candidates, run on the device: ML Kit GenAI Speech (basic, streaming, on-device) · sherpa-onnx streaming Zipformer (en) · sherpa-onnx Whisper base/small (segment) · SenseVoice-Small (segment, plus emotion) · Gemma 4 E2B audio (segment, up to 30 s) | **P0/P1 shoot-out** on our own recorded test set: accuracy on codes and numbers, latency, memory. Likely: a streaming recogniser for partials plus a segment-final one |
| D3 | Fast mind | Gemma 4 E2B (LiteRT-LM, GPU): S26 Ultra 52 tok/s decode, 0.3 s to first token, about 0.8 GB working memory, 2.6 GB model file. E4B if the phone allows | P0 probe on our phone; P2 intent eval (local vs cloud accuracy per intent sets the routing threshold) |
| D4 | Slow mind | Gemma 4 26B-A4B via REST; 31B as fallback. Google Search grounding for web answers and "minimal" thinking, **if they work for Gemma** (the docs say so; on 30 Sep the API refused a thinking setting) | P0 probe |
| D5 | Coordinator | 1:1 Kotlin port of `coordinator.py` plus: compensations (undo), slot view, tone-informed hold, read-back for spoken codes | JUnit port of the Python tests must pass |
| D6 | Text-to-speech | Android TextToSpeech (no download, instant, rate and pitch per tone); Kokoro (sherpa-onnx) if it is fast enough | P1 latency and quality check |
| D7 | Echo and barge-in | AudioRecord `VOICE_COMMUNICATION` with platform echo cancellation; text-to-speech on the communication stream; "only words interrupt" (a barge-in needs recognised words, not just sound) | P1 loudspeaker test (G4) |
| D8 | Process model | Foreground service, type `microphone`, started from the app in the foreground (Android forbids starting it from the background), so DUET keeps listening while Maps is in front | Locked |
| D9 | Tools | JSON manifests in assets: name, description, schema, `kind` (read/write/ui), `repeatable`, `consent`, `undo`, `group/target`, `runs` (local/cloud-needed) | Locked |
| D10 | Alarms | Samsung Clock intents if `ACTION_DISMISS_ALARM` works on our phone; otherwise DUET-owned alarms (`AlarmManager.setAlarmClock`, shown on the lock screen) so set, list and cancel are exact | **P0 probe** |
| D11 | Tone | Prosody computed in Kotlin (pitch slope, loudness, speaking rate, pauses) plus SenseVoice emotion and sound-event tags | P3 trials |
| D12 | Privacy | "On-device only" switch, Samsung's own "Process data only on device" idea: cloud off, honest "I need the internet for that" | Locked |
| D13 | Keys | Entered in Settings and stored encrypted, never in the APK or repo. Per-turn cloud calls kept low so **one key is enough** for a demo. No dependence on pooling many free projects (Google APIs Terms §2d) | Locked; ask Samsung for a demo key |
| D14 | Model delivery | The E2B model (2.6 GB) is not in the APK: download on first run, or `adb push` for the demo | Locked |

### 5.4 Event schema v1 (engine → UI → trace)

Every step is one JSON event, `{"v":1, "t": <unix ms>, "kind": ..., "where": "phone"|"cloud", ...}`. The kinds are
those the UI already understands (`heard`, `thinker_listen`, `talker`, `thinker_say`, `superseded`,
`tool_planned`, `tool_done`, `tool_dropped`, `tool_cached`, `tool_failed`, `tool_unknown`, `tool_asked`,
`agent_said`), plus new ones:
- `partial`: live words;
- `tone`: tags;
- `route`: local or cloud, and why;
- `undo`: a compensating action;
- `slot`: a value, its source words, and what it replaced;
- `latency`: per-stage timings.

The engine writes the same events to a per-conversation trace file on the phone (pulled with `adb`).

## 6. Capabilities

| # | Capability | How | Real / virtual / simulated | Mode | Notes |
|---|---|---|---|---|---|
| 1 | Alarms: set, change, cancel, list | D10 | Real | All | The correction demo |
| 2 | Timers | Clock `ACTION_SET_TIMER` | Real | All | |
| 3 | Reminders and calendar events | `CalendarContract` (insert) or a labelled alarm | Real | Assistant, Care | |
| 4 | Navigation | Google Maps intent; DUET keeps listening (D8) | Real | Drive, Assistant | Arrival time: a free routing service (OSRM) if its terms allow, otherwise a labelled simulated trip |
| 5 | Calls and texts | Real contacts (`ContactsContract`), dialer or direct with permission | Real | All | Replaces the typed contact list |
| 6 | Settings | Brightness, screen timeout, settings screens, Wi-Fi/Bluetooth panels | Real | Assistant | |
| 7 | Phone diagnosis | Battery, heat, current, screen, refresh rate, app usage | Real | Assistant | Already built |
| 8 | Apps | List, count, open | Real | Assistant | Already built |
| 9 | Web answers | Slow mind with Google Search grounding (D4) | Real | All | Needs the cloud |
| 10 | Weather | Open-Meteo (no key) | Real | All | |
| 11 | Media | Play, pause, next (media key events) | Real | Drive | |
| 12 | Torch | `CameraManager.setTorchMode` | Real | Assistant | A tiny but vivid demo |
| 13 | Smart home | SmartThings REST with a personal access token; real or virtual devices that appear in Samsung's SmartThings app | Real API | All | Needs a SmartThings account |
| 14 | Medicines | A schedule stored on the phone, with the double-dose guard | Real (local data) | Care | |
| 15 | Family alert and check-in | SMS to family contacts, check-in log | Real | Care | |
| 16 | Human hand-off | "Let me get a person": call family or a support number | Real | All | The mentors asked for human-in-the-loop recovery |
| 17 | Car climate | Simulated, labelled | Simulated | Drive | No public car API |

## 7. Reuse map: what moves from Python and JS to Kotlin

| Today | Becomes | Size |
|---|---|---|
| `duet_voice/coordinator.py` + `tests/test_voice_coordinator.py` | `engine/Coordinator.kt` + JUnit | 245 + 13 tests |
| `duet_voice/agent.py` (`llm_node`, `reply_delivered`, timing constants) | `engine/TurnLoop.kt` | ~300 |
| `duet_voice/thinker.py` + `gemma_api.py` (pool, budget, backups, deadline) | `engine/CloudThinker.kt`, `engine/GemmaClient.kt` | ~450 |
| `duet_voice/galaxy/modes.py` (instructions, tool specs) | `assets/modes/*.json` + `assets/tools/*.json` | text |
| `duet_voice/galaxy/tools.py` (ledger scope, groups, held-back) | `engine/PhoneToolbox.kt` | ~160 |
| `app/www/phone.js` + `DuetPhonePlugin.java` | `engine/tools/*.kt` | ~900 |
| `duet_voice/galaxy/agent.py` (`turn_note`, `done_note`, `polish`) | `engine/GalaxyContext.kt` | ~100 |
| `scripts/galaxy_fake_phone.py` + `galaxy_check_scenarios.py` | the Galaxy Correction Suite (section 8) | reuse |

The benchmark code (`duet_voice/agent.py`, `bench/`, `reproduce.sh`) is **not modified**. Shared changes go
only to `duet_voice/galaxy/` (Track A), and all 75 Python tests must keep passing.

## 8. How we test: the Galaxy Correction Suite

- **Scripted trials on the device:** a debug intent feeds WAV files into the engine instead of the microphone, so tests are repeatable and run over `adb`. The lines are voiced by **our own team members** (Indian English) plus synthetic voices.
- **Each trial has expectations:** the tool sequence that must run, the calls that must never run, and the words the answer must contain. The checker is a Python script reading the pulled trace files (as `galaxy_check_scenarios.py` does today).
- **Categories:**
  - self-correction (immediate and late);
  - wavering (must ask);
  - dropped request;
  - multi-step;
  - barge-in during the answer;
  - noise and cough;
  - spelled codes (must read back before a write);
  - tone (advisory only);
  - offline;
  - network loss;
  - tool failure (must offer a human).
- **Live human trials:** 10 per category over the project, each logged with its trace.
- **Metrics per run:** stale actions, duplicates, final-intent accuracy, per-stage latency (p50/p90), false barge-ins per minute, cloud calls per turn, memory and thermal state.

## 9. Track B phases (the phone-native engine: after 21 October, the worklet roadmap)

These phases are the full phone-first build. For the 21 October demo, [section 0](#0-the-21-october-plan-what-we-actually-build-in-five-days)
applies. The Phase 0 probes and the on-phone Gemma stretch are pulled forward from here.
**Cut lines (as originally planned):**
- Demo 5 days away or less: Track A plus demo prep only. This is the case for 21 Oct.
- 6–10 days: Phases 0–2, with fewer capabilities.
- 11 days or more: Phases 0–4.

### Phase 0: device bring-up, fallback and probes (days 1–2)

**Set-up:**
- [ ] Branch `round2` from `main`; this plan committed.
- [ ] Phone connected to the laptop over USB with debugging on; `adb` and `scrcpy` working.

**Track A on the phone:**
- [ ] Install the current APK. Run the current stack on the phone. Record **baseline numbers** (latency, echo, barge-in, alarm behaviour).
- [ ] Track A fixes:
  - [ ] Remove the false footer ("not a cloud AI").
  - [ ] Make the consent check and second look work with the Gemma backend.
  - [ ] Confirm simple phone actions with a template, not a second model call.
  - [ ] Acknowledge after 0.4 s of quiet in phone mode.
  - [ ] Use a per-mode vocabulary in the speech recogniser's prompt.
  - [ ] The benchmark path stays untouched.

**Probes on the phone:**
- [ ] Samsung Clock dismiss (D10).
- [ ] Gemma E2B load time, speed, memory and heat (D3).
- [ ] Gemma 26B grounding and minimal thinking (D4).
- [ ] Loudspeaker echo with communication-mode recording (D7).
- [ ] ML Kit GenAI Speech availability (D2).

**Test data:**
- [ ] Each team member records the 60-line test script (supplied).

**Gate P0:** the APK runs on the phone; baseline recorded; every probe answered with data; decisions D2, D3, D4, D6, D7 and D10 locked.

### Phase 1: ears and mouth on the phone (days 3–5)
- [ ] Kotlin engine module, foreground service, audio capture with echo cancellation, Silero VAD, audio ring buffer, event bus, debug audio injection.
- [ ] ASR shoot-out on the recorded set; integrate the winner(s): partials plus finals.
- [ ] Text-to-speech with instant stop; barge-in only on recognised words; guard against DUET hearing itself.
- [ ] End of turn: silence, plus the coordinator's "sentence left open" and "revising" signals.
- [ ] UI bridge: engine events into the existing timeline; engine switch (On-device | Server).
- [ ] Debug "echo bot" mode (repeats what it heard) for timing tests.

**Gate P1:**
- End of speech to first audio p50 ≤ 0.6 s.
- Barge-in stop ≤ 0.3 s.
- At most 1 false interruption per 10 minutes on the loudspeaker.
- Code and number accuracy at least as good as the laptop's Whisper baseline on the same recordings.

### Phase 2: brain and coordinator (days 5–9)
- [ ] `Coordinator.kt` plus a JUnit port of the coordinator and Galaxy toolbox tests. **All pass.**
- [ ] Tool manifests; Kotlin tools for capabilities 1–8, 12 and 14–16; compensations (undo); slot view.
- [ ] Cloud slow mind: REST client (key pool, token budget, backups, 28 s deadline, rate-limit rest), mode instructions, a tool loop that keeps the model's own turns (thought signatures), `keep_listening`.
- [ ] Turn loop port: open request, settle, listen wait, acknowledgement timing, progress line, reply-delivered rule, cut-off, done and never-ran notes.
- [ ] Fast mind on the phone: Gemma E2B router plus simple tool calls (constrained to the manifest schema); escalate to the cloud when unsure; template confirmations.
- [ ] Read-back: a spelled-out code or number used in a write is read back once before it runs.
- [ ] Galaxy Correction Suite v1 (40 or more trials) plus checker.

**Gate P2:**
- G2 passes on the suite.
- G3 passes for local actions; cloud answers p50 ≤ 4 s.
- All JUnit and Python tests pass.

### Phase 3: reach (days 8–11)
- [ ] Capabilities 3, 4, 5 (real contacts), 9, 10, 11 and 13 (SmartThings); navigation while still listening; Drive arrival time.
- [ ] Care: medicine database, distress flow (tone plus words), family alert, check-in.
- [ ] Tone channel (D11) with the advisory rules; tone tests.
- [ ] Offline mode and the "On-device only" switch; network-loss handling.
- [ ] Stretch: side-button default assistant (`VoiceInteractionService`).

**Gate P3:** G1, G5 and G6.

### Phase 4: robustness, polish, jury-proofing (days 11–13)
- [ ] **Failure drills:**
  - [ ] airplane mode mid-request;
  - [ ] tool timeout (unknown outcome, offer a human);
  - [ ] key exhausted (local fallback);
  - [ ] incoming call;
  - [ ] app sent to the background;
  - [ ] low battery.
- [ ] **UI:** phone/cloud badges, per-step latency, slot view, **Never ran / Not repeated / Undone** states.
- [ ] **Performance:** memory, heat and battery over a 20-minute session.
- [ ] **Demo:** script, 5 dry runs, backup video, backup phone and hotspot. Round 2 deck and talk track; bank of design Q&A.

**Gate P4:** G7 and G8.

### Phase 5: stretch (only after every gate passes)
- Hindi/Hinglish: Gemma audio, which handles many languages.
- Fine-tune FunctionGemma 270M or Gemma 4 E2B on synthetic, self-corrected phone requests (the DuplexCascade recipe).
- "Mm-hm" backchannels in phone mode only.
- Camera: "what's this error on my washer?" sent to Gemma.
- Package the engine as a library: the worklet seed.

## 10. Demo plan (draft)

**The online set-up (21 Oct):**
- **The laptop** runs everything: the agent (speech models and the local fast mind on its GPU), the token server, scrcpy (the S24's screen, and optionally its audio, mirrored on the laptop), the slides and the meeting app.
  - A 6 GB GPU shared with video encoding is a risk. Test it on Day 4, with E2B and Kokoro on the CPU as fallbacks.
- **The phone:** on mobile data (not the laptop's Wi-Fi). The presenter speaks to it directly.
- **Audio the jury hears:** either the laptop microphone picking up both the presenter and the phone's speaker, or scrcpy's forwarded phone audio shared as system sound. Choose by test on Day 4.
- **If the jury wants to interrupt the agent themselves:** their voice plays from the laptop speaker placed next to the phone, so the phone hears it like a person in the room. Rehearse it. Fallback: the presenter says what they ask.

**Track A+ demo (21 Oct):** the same flow as below, with the hook online (not in airplane mode). Losing the cloud in step 6 shows the local fast mind still acting.

About 7 minutes, then open play for the jury.
1. **Hook (offline, airplane mode on):** "Set an alarm for 6:30… no, make it 7." About 1.5 s. The timeline shows 6:30 as **Never ran** and 7:00 as **Done**.
2. **Real troubleshooting:** "Why is my battery dying?" The user interrupts with their own theory; DUET checks it against the real readings.
3. **Drive:** a destination changed mid-sentence; Maps opens and DUET keeps listening. "Tell Priya when I'll arrive." "Turn on the AC at home" (SmartThings).
4. **Care:** a shaky "I'm fine" leads to a gentle check-in; the double-dose guard.
5. **Jury free play:** "Interrupt it however you like."
6. **Live failure:** Wi-Fi off mid-request gives an honest answer and the local fallback.
7. **Under the hood:** timeline and trace, architecture, why the coordinator is the product, the worklet.

**Backups:** a second phone with the same build, a phone hotspot, a recorded run, and the Track A server engine.

## 11. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The on-device model is too slow, hot or large on our phone | Fast mind unusable | P0 probe; E2B (not E4B); FunctionGemma 270M as the router; cloud-only fallback |
| Echo on the loudspeaker causes false interruptions | Broken full-duplex feel | Communication-mode echo cancellation; words-only barge-in; Galaxy Buds for the demo |
| Speech recognition misses Indian-English codes | Wrong actions | Shoot-out on our own voices; read-back before writes |
| Gemma API stalls, or quota runs out on demo day | Slow or failed answers | Local routing (few cloud calls), backups, a demo key from Samsung, honest fallback |
| Samsung Clock ignores dismiss intents | Cancel fails | DUET-owned alarms (D10) |
| LiteRT-LM or ML Kit APIs are alpha and change | Build breaks | Pin versions; the Track A engine switch |
| Time | Unfinished features | Cut lines, always-shippable Track A, gates before features |
| Demo room (noise, Wi-Fi) | Flaky live run | Offline-first design, Buds, hotspot, rehearse in a noisy room |

## 12. What we need from the team now

**Answered 16 Oct:**
- **Demo:** 21 Oct, online, presentation, demo and Q&A.
- **Phone:** Galaxy S24, 8 GB, One UI 8.5.
- **Keys:** 3 Gemma keys already work.
- **Hugging Face:** later, when needed.
- **SmartThings:** account to come. Explain the steps before implementing.
- **People:** Tanmay takes the people side; the agent prepares everything else.
- **Approved:** Track A+ / Track B plan, scope, `round2` branch, English only.

**Still open:**
- Can the S24 stay connected to the build laptop by USB, 17–21 Oct?
- Meeting platform, and how the slot splits between presentation, demo and Q&A.
- Approval for two downloads: scrcpy (official GitHub release) and a Gemma 4 E4B GGUF (about 3–5 GB).

**Original list:**

1. **Round 2 date, format and length:** in person or online? If online, how will the jury's voice reach the phone?
2. **The phone(s):**
   - The exact model, One UI/Android version and RAM.
   - Whether it can be connected to the build laptop by USB with Developer options and USB debugging on. Then the agent can install builds, run the suite and read logs directly.
   - A second Android phone, if possible, as a backup.
3. **Gemma API key(s):**
   - One or two of our own.
   - Ask prism@samsung.com for a demo key (the mentors said Samsung has Gemini/Gemma access). A draft email is ready on request.
4. **Accounts only a team member can create:**
   - A Hugging Face account that has accepted the Gemma licence (to download the on-device model).
   - A SmartThings account with a personal access token (for real smart-home control).
5. **People:**
   - Who can record the 60-line voice test set (each member).
   - Who knows Android or Kotlin.
   - Who owns the deck and the Q&A bank.
6. **Decisions:**
   - Approve Track B (phone-first) with Track A as the fallback.
   - Approve the scope and capabilities.
   - Hindi as a stretch goal: yes or no.
7. **Anything Samsung has sent about Round 2:** extra use cases, extra input types, rules, time slot.
