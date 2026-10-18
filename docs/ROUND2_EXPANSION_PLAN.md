# Round 2, main branch: expanding and solidifying DUET (plan for approval, 18 Oct 2026)

Scope: the `round2` branch only, for the 21 October demo. The fully on-device version is a separate branch, later. A
module moves onto the phone now **only if that lowers latency** (section 3: none of the AI modules qualify).

## 0. Your thoughts, organised

1. **Worry: we are not differentiated.** Other teams use the same models and the same coding agents, so they can build
   the same thing.
2. **Question: is "phone + laptop + cloud API" acceptable to Samsung's jury?**
3. **What Samsung said it judges:**
   - understanding of the problem, and caveats identified;
   - the core architecture and its technology modules;
   - extension of the solution and relevance to Samsung;
   - scalability, and how easily new use cases are covered;
   - a demo of all of these, besides full duplex (done) and a real use case.
4. **Idea: on-device.** Now only where it lowers latency; fully on-device later, on its own branch.
5. **Idea: an assistant that watches the screen** (WhatsApp and so on). You parked it yourself. Section 5 explains why
   that is the right call.
6. **Idea: proactive suggestions across Samsung's ecosystem.** "Home in 10 minutes: turn on the AC?"
7. **What you want:** things to implement, not marketing.
8. **Testing:** everything tested and verified many times over, and ready for the S24 night.

## 1. Is the current setup acceptable? Where we stand against each criterion

For a hackathon final, a phone app driven by an agent on a laptop is a normal prototype shape. Samsung's engineers will
not reject it. **But it is not what makes us stand out:** most finalists will have "a LiveKit voice agent on a server
with a cloud LLM". What only we can show is the **coordinator**, which makes an acting agent safe to interrupt, and real
Galaxy and SmartThings actions with proof. The plan below puts those in front of the jury, and closes the gaps on the
criteria where we are weak.

| Samsung's criterion | Where we stand | Gap | What closes it (section 4) |
|---|---|---|---|
| Problem understood, caveats identified | Strong in the code: stale intent, duplicates, false claims, echo, background limits, rate limits, recovery | Mostly invisible in a demo; the cloud drill is the only live failure | P6 live recovery drills; a caveats → mitigations slide |
| Core architecture, technology modules | Clear: ears, coordinator, two minds, toolbox, mouth, timeline | "Why a laptop?" needs an answer with numbers | Section 3 (placement chosen by measured latency); a module slide |
| Extension, relevance to Samsung | SmartThings, Galaxy actions | **New in One UI 8.5 (on our S24):** Bixby is now a conversational *device agent* that changes settings in natural language. The jury will compare us with it. We are not yet an assistant on the phone, and we never speak first | P1 proactive offers (Samsung's Car-to-Home direction, done conversationally); P2 DUET as the phone's assistant (side key) |
| Scalability, new use cases | True in the code: any new tool inherits the coordinator's guarantees | Not demonstrated; adding a tool means editing 3 files | P3 any SmartThings device works with no code (live); skills from a manifest shaped like Android 16 AppFunctions |
| Demo of all of this | 7-minute script covers full duplex, corrections, home, Drive, drill | Nothing for scalability, Samsung integration, proactivity | Section 6 demo changes |

**Our positioning against the new Bixby:** Samsung just made Bixby an agent that acts. The moment an assistant acts,
interruptions and corrections become dangerous:
- a setting changed on words the user took back;
- a message sent twice;
- "done" said before anything happened.

DUET is the layer that makes an acting agent safe to interrupt: the coordinator, with its epochs, commit gate, ledger
and guards, and confirmed-only-when-done. That is the PRISM-worklet story: the coordinator under Bixby's device agent,
in front of every AppFunction call.

## 2. What others will have, and what only we have

| Most finalists (likely) | Only DUET (today) | Only DUET (after this plan) |
|---|---|---|
| A voice agent on a server, a cloud LLM, barge-in from LiveKit | A coordinator that never acts on retracted words, never repeats, never claims falsely, measured on FDB-v3 (Pass@1 0.81) | The agent **speaks first** with context, and the offer can be accepted, corrected, declined or interrupted |
| Simulated or few tools | Real Galaxy actions, a real SmartThings home with read-back confirmation, behind Google Maps | DUET **is** the phone's assistant (side key / assist gesture) |
| A happy-path demo | Live recovery (cloud cut), resilience tested (drops, restarts) | Live recovery drills, and **a new device or skill added on stage with no code** |
| "It's fast" | Two speeds, measured: fast mind 0.24 s | Simple *questions* answered in about 1.5 s instead of 4 s; placement justified by measurements |

## 3. On-device on the main branch: latency per module

Measured today (emulator, 18 Oct runs) and from published benchmarks:
- Phone → LiveKit Cloud → laptop → phone round trip: **p50 90 ms**, p90 199 ms.
- An action sent to the phone and its reply: **p50 253 ms**.
- The fast mind's decision on the laptop GPU: **0.24 s**, with real voice.
- Gemma E2B on S24-class phones (LiteRT-LM, published): Gemma 3n E2B on the **S24 Ultra GPU, 816 tokens/s prefill and
  16 tokens/s decode**. Gemma 4 E2B on the S26 Ultra GPU: 3,808 and 52. Our S24 is the Exynos 2400, with no published
  numbers. Gemini Nano (ML Kit) is not offered on the S24.

| Module | Today | On the S24 | Latency effect | Decision |
|---|---|---|---|---|
| Fast mind (Gemma 4 E2B) | 0.24 s on the laptop GPU | A 1,400-token prompt at ~816 tokens/s is ~1.7 s, plus ~25 tokens decoded at ~16 tokens/s, ~1.6 s: **≈ 3.3 s** (≈ 1.6 s even with the prompt cached, which the Kotlin API cannot do yet) | **~10× slower** | Stays on the laptop |
| Speech recognition (Whisper large-v3-turbo) | GPU, after the turn ends | Streaming Zipformer or Moonshine (sherpa-onnx), real-time factor ~0.05 | At most 0.1–0.3 s faster; **much less accurate** on Indian English and noise; ASR is not the bottleneck | Stays |
| Voice (Kokoro, fixed lines cached) | GPU, streamed (+45 ms) | Android TTS (~0.1–0.3 s first audio); Kokoro on Android ~2.9 s first audio | No gain; would break the agent's record of what the user heard (needed for barge-in) | Stays |
| Media path | LiveKit Cloud (90 ms round trip) | Local LiveKit server over the USB cable | Saves < 0.1 s; adds a fragile setup | Not now |
| SmartThings calls | Laptop → Samsung's cloud | Phone → Samsung's cloud | None: both go over the internet to the same cloud; from the phone it would add a hop when the laptop decides | Stays |
| Cloud mind (Gemma 4 26B) | Laptop → Google | Phone → Google | None: the same internet call | Stays (moves with the on-device branch) |
| Stopping DUET's voice when you interrupt | The laptop hears you, then stops the stream (network + its 0.5 s of speech) | The phone silences DUET locally the instant you speak | **Could be faster**, but echo may fire it falsely | **Measure on the S24 first** (P10); build only if barge-in is slow there |
| Phone actions, DUET alarms, microphone service | Already on the phone | — | — | Already there |

**Conclusion:** moving the AI modules onto the S24 would make DUET slower, so on the main branch they stay where they
are. Our latency is spent elsewhere:
- the deliberate 1.4 s safety hold before an action, which is why a correction a second later still wins;
- **two cloud calls for every question** (answer p50 4.1 s);
- the answer being generated whole before it is spoken.

P4 and P5 attack the last two. This table is also the answer to "why a laptop?": placement is chosen by measured
latency, and the on-device branch comes when phones are fast enough (the S26 Ultra's Gemma 4 E2B numbers suggest they
are about to be).

## 4. The work, in priority order

Every item ships behind a setting (it can be switched off on demo day), with new suite and e2e scenarios, and passes
`verify_all` (P7) before it is committed.

### P1 · DUET speaks first: proactive offers you can negotiate (Samsung relevance, full duplex both ways) · ~5 h · risk low–medium
- **Arriving home (Drive):**
  - **Trigger:** the trip's arrival time to the destination "home" crosses 10 minutes. This happens automatically from
    the trip, not only from the demo button that exists today.
  - **Context:** the outside temperature (weather for the city) and the home's real state (SmartThings).
  - **DUET's offer:** "You'll be home in about 10 minutes and it's 33 degrees out. Shall I start the living room AC
    at 24?"
  - **The user can:**
    - "Yes, make it 22, and turn on the porch light": done, corrected;
    - "No thanks": nothing;
    - stay silent: the offer expires;
    - talk over the offer: it stops.
- **The coordinator records an offer:**
  - The user's "yes" counts as asking for exactly the offered action. A correction changes its values. Silence never
    acts.
  - **On the timeline:**
    - "DUET offered: AC 24 °C (why: 10 min from home, 33 °C outside)";
    - "Accepted with a change: 22";
    - "Done".
- **Two more triggers with real data:**
  - Low battery on a long trip (the real battery reading against the minutes left): offer to lower the brightness.
  - The washer finished (a SmartThings state change): offer a reminder.
  - The Care medicine reminder fires at its time instead of by a button.
- **Why it matters:** Samsung's Car-to-Home (with Hyundai and Kia) already turns on the AC as the car nears home, as a
  silent routine. DUET does it as a conversation: it explains why, it asks, it can be corrected, and it is safe to
  interrupt. That is the direction of Bixby's new "agentic" mode, done safely.

### P2 · DUET as the phone's assistant (Samsung relevance) · ~1.5 h · risk low
- **Mechanism:** DUET answers Android's assist intent. Set as the default digital assistant app, it opens straight into
  a listening conversation from the assist gesture, or from the side key where One UI offers it.
- **Caveat:** the role resets when the app is reinstalled, so `phone_check.py` sets it after installing.
- **Tested on the emulator** with the assist intent. The S24 night confirms the side key.
- **Ask Naman first:** this changes his phone's assistant setting, and it can be reverted.

### P3 · New use cases without new code (scalability, demonstrated live) · ~8 h · risk medium (additive only)
- **P3a · Any SmartThings device works:**
  - **Generic capabilities:** switch, level, colour temperature, fan speed, thermostat, media volume, and sensors
    (contact, motion, temperature) as readings, plus **scenes** ("run Good night").
  - **New devices:** an unknown name refreshes the device list, so a device added in the SmartThings app works within
    seconds.
  - **Demo:** add a "Coffee machine" plug in the SmartThings app on stage, say "turn on the coffee machine", and it is
    done, confirmed and on the timeline.
- **P3b · Skills from a manifest:**
  - **Where:** `skills/*.json`.
  - **What a skill declares:**
    - its parameters;
    - read, write or screen;
    - whether it needs consent;
    - its exactly-once rule;
    - its spoken confirmation;
    - an executor: an Android intent, an Android setting, a SmartThings command, or a web read.
  - **What it inherits:** the coordinator, ledger, guards, confirmations and timeline apply automatically.
  - **Examples:**
    - Do Not Disturb on/off;
    - open the camera;
    - a note in Samsung Notes;
    - the Wi-Fi panel.
  - **Why this shape:** it is the shape of Android 16's **AppFunctions** (self-describing functions for agents). On a
    Samsung device, a privileged agent could run Samsung Notes', Calendar's and Reminder's AppFunctions through the
    same coordinator. We cannot call them ourselves: that permission is for privileged or allowlisted agents only.

### P4 · Simple questions in ~1.5 s instead of ~4 s (latency) · ~4 h · risk low–medium
- **Today:** every question goes to the cloud mind (two calls).
- **Plain readings get a fast path:**
  - battery level and charging;
  - the next alarm;
  - one home device's state;
  - the trip's arrival time.

  The fast mind picks the reading, and the answer is spoken from a template, like today's confirmations.
- **What still goes to the cloud:** anything with judgement ("is that enough for the drive?", "why…?").
- **Gate:** a deterministic rule decides what counts as a plain reading.
- **Measured** before and after in the suite.

### P5 · Speak the cloud mind's answer as it is written (latency, phone mode only) · ~3 h · risk medium
The first sentence is spoken while the rest is being generated: an estimated 0.3–0.8 s earlier on longer answers. The
benchmark path stays untouched (frozen); the false-claim filter runs per sentence. **Lowest priority:** do it only if
P1–P4 and P6–P7 are done and green.

### P6 · Recovery drills, live on stage (caveats identified) · ~2.5 h · risk low
A drills panel in the app:
- cut the cloud (exists);
- **slow cloud** (+6 s: "Still working on it", while the fast mind keeps working);
- **a phone action refused** (permission off: DUET says what to allow);
- **SmartThings unreachable** (honest line, nothing claimed);
- **drop the connection** (rejoins, still listening).

The mentors asked to see variable latency and failing tasks recovered; this shows it on demand.

### P7 · Always tested: one command for everything · ~2 h, then runs · risk none
- **`scripts/verify_all.py`, in order:**
  1. unit tests;
  2. the voice suite (all 36 scenarios plus the new ones);
  3. on the emulator: self-test, e2e Assistant and Drive, resilience;
  4. preflight.
- **Output:** one dated report with pass/fail and a latency table, kept under `results/`.
- **Rules:**
  - after every feature;
  - a full run twice before the 20th;
  - the same script against the S24 on the 20th night.
- **Freeze:** nothing new after 6 pm on the 20th, only fixes found on the S24.

### P9 · Show the problem itself: the coordinator switched off (understanding of the problem, core module) · ~2.5 h · risk low
- A hidden **"Coordinator off"** switch makes DUET act like a plain voice agent:
  - it acts at the first pause;
  - no epochs, no ledger, no guards;
  - the timeline says so in red.
- **On stage:** "Set an alarm for 6:30." (a one-second pause) "No, make it 7."
  - Coordinator off: both alarms are set, and DUET says "done" twice.
  - Coordinator on: only 7.
- This shows the problem, why it is hard, and that our core module solves it, in 30 seconds.
- It is off by default, resets with each conversation, and is never on in the Q&A.

### P10 · Measure on the S24 night what only the phone can tell (feeds the on-device branch) · ~30 min that night · no code risk
- **Barge-in:** how fast DUET stops when interrupted, on the loudspeaker. If it is slow, a phone-side instant stop becomes
  worth building (section 3).
- **Gemma 4 E2B on this S24 (Exynos 2400):**
  - the measures: prefill and decode speed, memory, heat;
  - the tool: Google's AI Edge Gallery app or a small benchmark of ours;
  - it needs Naman's OK and a ~2.6 GB model download onto the phone.

  These are your "how good and how bad is it on the phone" numbers.

### P8 · Show all five criteria (deck and demo script) · ~3 h, last
- **New slides:**
  - caveats → mitigations;
  - the modules and where each runs, with the latency table from section 3;
  - Samsung relevance (Bixby device agent, Car-to-Home, the assistant role, AppFunctions);
  - scalability (the manifest; the new device live).
- **Demo script** (section 6).
- **Q&A additions:** "why not on-device?", "how is this different from the new Bixby?", "how do you add a use case?".

### Notes on your ideas, item by item
- **Several suggestions at once** ("Do you want this done? And this?"): P1's offer can bundle 2–3 things ("the AC at 24
  and the hall light?"). You can accept some of them ("just the AC").
- **More of Samsung's apps:**
  - Samsung Notes and similar apps, through Android's share and open intents, are P3b skills.
  - SmartThings scenes are in P3a.
  - Galaxy Buds are the audio fallback.
  - Calling Samsung apps' own functions (AppFunctions) needs a privileged permission. It is the pitch for a worklet,
    not something we can do as a sideloaded app.

## 5. Not doing on the main branch, and why

- **On-device AI modules:** slower (section 3); they go to the on-device branch later.
- **Local LiveKit over USB:** saves under 0.1 s and adds risk.
- **A screen-reading assistant:**
  - it needs an accessibility service or screen capture;
  - accessibility for sideloaded apps is a restricted setting on Android 13+, and Play Protect's India pilot blocks
    installs that ask for it;
  - privacy;
  - Google and Samsung already ship screen context (Circle to Search, Gemini).

  You were right to park it.
- **Calling other apps' AppFunctions:** needs a privileged permission. P3b mirrors their shape instead.
- **Gemini Nano:** not offered on the S24.

## 6. Demo script changes (about 8 minutes)

| Beat | Shows | Change |
|---|---|---|
| 0 · Hold the side key: DUET opens and listens | Samsung integration | **New** (P2) |
| 1 · "6:30… no, make it 7" | Never ran, fast mind | — |
| 2 · Late correction → Undone | Recovery after commit | — |
| 3 · Battery: interrupt with your own theory | Full duplex | Shorter |
| 4 · Home: "AC to 22, actually 21"; "is the door locked?" (now ~1.5 s, P4) | Real home, fast readings | Faster |
| 5 · Drive: office → airport → Maps; **"home in 10 minutes" offer, accepted with a change** | Proactive, negotiable, behind Maps | **New** (P1) |
| 6 · A drill: slow cloud or cut cloud | Caveats, recovery | Choice of drill (P6) |
| 7 · **Add a coffee machine in SmartThings, use it at once** | Scalability, no code | **New** (P3a) |
| 8 · **Problem first:** the coordinator off, then on, with the same words | Understanding of the problem; the core module | **New** (P9); could open the demo instead |
| 9 · Under the hood: the timeline and the module slide | Core architecture | Kept from today's script |
| 10 · The jury's turn | — | — |

## 7. Order of work and freeze

| When | Work | Then |
|---|---|---|
| 18 Oct | P7 (harness), P1, P2, P9 | verify_all |
| 19 Oct | P3a, P4, P6; P3b if time | verify_all ×2 |
| 20 Oct, day | P5 (optional), P8; **freeze at 6 pm** | verify_all ×2 |
| 20 Oct, night | The S24 (checklist + verify_all on the phone); P10 measurements if time allows | Fixes only |

## 8. Decisions needed from you

1. Approve P1–P10, or pick which.
2. P3a: may I add devices (a coffee-machine plug, a fan) and one scene ("Good night") to your SmartThings home?
3. P2: may DUET become the default digital assistant on Naman's S24 for the demo (reversible)? Please ask him.
4. On the S24 night, test the new Bixby with the same corrections ("6:30, no, 7")? We'd use it only as an
   internal reference, unless it clearly fails *and* we decide it is tactful to show.
5. The demo-script changes in section 6.

## Sources

- **On-device models:**
  - [LiteRT-LM overview and benchmarks](https://developers.google.com/edge/litert-lm/overview)
  - [Gemma 4 E2B LiteRT-LM model card](https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm)
  - [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)
  - [LiteRT-LM prefix-caching request (Kotlin)](https://github.com/google-ai-edge/LiteRT-LM/issues/3949)
  - [ML Kit GenAI supported devices](https://developers.google.com/ml-kit/genai)
- **On-device speech:**
  - [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
  - [Kokoro on Android](https://soniqo.audio/ko/guides/kokoro/android)
- **Agents on Android and Samsung:**
  - [AppFunctions overview](https://developer.android.com/ai/appfunctions)
  - [AppFunctions with Gemini on One UI 8.5 (9to5Google)](https://9to5google.com/2026/02/25/android-appfunctions-gemini/)
  - [The new Bixby in One UI 8.5 (Samsung Newsroom India)](https://news.samsung.com/in/samsung-introduces-the-new-bixby-in-one-ui-8-5)
  - [SmartThings Car-to-Home (SamMobile)](https://www.sammobile.com/news/smartthings-lets-control-home-appliances-hyundai-kia-cars/)
  - [Assistant role (RoleManager)](https://developer.android.com/reference/android/app/role/RoleManager)
- **Restrictions:**
  - [Play Protect India pilot (9to5Google)](https://9to5google.com/2024/10/03/google-blocking-sideloaded-apps-india/)
