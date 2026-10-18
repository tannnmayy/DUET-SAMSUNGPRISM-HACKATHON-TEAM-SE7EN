# DUET for Galaxy: every capability, and what it is worth for Theme 05

Team SE7EN (SRM) · Samsung PRISM GenAI Hackathon 3.0 · Theme 05, Interruptible Real-Time Agents · state on 18 Oct 2026,
branch `round2`. Every "verified" below names the test that showed it. "S24: pending" means only the night of the 20th can
settle it.

## 0. What the app is, plainly

**DUET for Galaxy is an Android app (one APK) plus a brain that runs on our laptop.** It is not a standalone phone app.

| Runs on the Galaxy S24 (the APK) | Runs on the laptop (`bash scripts/galaxy_demo.sh`) | Runs in the cloud |
|---|---|---|
| The screen: three modes, the live timeline, the home panel, settings | The agent: ears (Silero VAD + Whisper large-v3-turbo), mouth (Kokoro), turn detector | Gemma 4 26B, the cloud mind (Google's API, 3 free-tier keys) |
| The microphone and loudspeaker (WebRTC in the app's WebView) | The coordinator: epochs, commit gate, ledger, guards | LiveKit Cloud: the audio and data link between phone and laptop |
| Every phone action (the DuetPhone native plugin: 29 methods) | The fast mind: Gemma 4 E2B on the laptop's GPU (llama.cpp) | SmartThings: our home's 7 devices (REST API) |
| DUET's own alarms; the "DUET is listening" microphone service | The token server (:8787), reached by the phone through the USB cable | Open-Meteo (weather), Google Search grounding (web) |

So on the 20th and 21st, "everything runs" means: **the S24 plugged into the laptop by USB, the laptop running the stack
with its GPU, and internet on both.** Without the laptop the app opens but cannot talk. The fully on-device design
(Track B in `ROUND2_PLAN.md`) was the plan's target and is **not built**: say so if the jury asks, and present it as the
next step (section 4).

## 1. The capability ladder: bare minimum to maximum

Each level adds to the one below and names what it needs. When something fails on the day, DUET drops to a lower level,
says so, and keeps working.

| Level | What works | Needs | If it fails |
|---|---|---|---|
| **0. Last resort** | The backup recording; the same app in the laptop browser (`localhost:8787`), the agent real and the phone simulated | The laptop | — |
| **1. A conversation** | Talk, interrupt, correct yourself; DUET answers questions from real readings (battery, apps, screen time, weather, the web) | Laptop stack, internet, the phone on USB | Level 0 |
| **2. The phone acts** | Alarms, timers, brightness, timeout, torch, media, volume, calendar, apps, settings, Maps, calls, texts, contacts | Level 1, permissions (`phone_check.py`) | Settings › *Simulate phone actions*: the app simulates them, and says so |
| **3. The real home** | SmartThings devices change for real, and DUET says so only once a device reports the change | Level 1, a SmartThings token from the last 24 h | The app's simulated home, labelled on screen |
| **4. Behind Google Maps** | DUET keeps listening and acting while Maps is in front (Drive mode) | Level 2, "Appear on top" and the microphone service | DUET says what to allow, or asks you to come back to it; it never pretends |
| **5. Two speeds** | Simple, clear actions in about a second, without the cloud | The fast mind on the laptop's GPU | The cloud mind does everything (2.5–4 s) |
| **6. Today's maximum** | All of the above, plus direct calls and texts confirmed by the phone's radio, DUET's alarms on the lock screen, recovery from drops and restarts while still listening | Level 5, "Call and text directly" on | The dialer or Messages opens; you tap |
| **7. Potential, not built** | DUET on the phone alone, inside Bixby, with real devices and more senses (section 4) | Engineering after the hackathon | — |

## 2. Every capability

Each block gives what it does, how it works, whether it is real or simulated, where it is verified, and its value for
the problem statement, scored out of 10. Theme 05 asks an agent to **stay responsive**, **never claim "done!" falsely**,
**work asynchronously**, and **recover cleanly**: discard stale intent, update arguments after a change of mind, and
never repeat a state-changing action. The Round 2 jury asks four questions: does it work, is the approach sound, would a
real user want it, and can it become a PRISM worklet. The mentors also asked to see variable latency and failing tasks
recovered (retry, human in the loop), and called a Bixby-like use case "wonderful".

### A. The core: interruptible, self-correcting, honest (Theme 05 itself)

**A1. Full-duplex conversation with barge-in · 10/10**
- **What:** DUET listens while it talks. Speak over it and it stops mid-sentence; it knows what you heard of its answer
  ("Your last answer was cut off. The user heard only: …").
- **How:** LiveKit agent with Silero VAD and the LiveKit turn detector; echo cancellation in the phone's WebRTC.
- **Real.**
- **Verified:** the FDB-v3 benchmark in Round 1 (Pass@1 0.81 on all 100 real recordings; the published best is
  GPT-Realtime at 0.600), and the 36-scenario Galaxy suite with interruptions.
- **S24: pending:** loudspeaker echo, i.e. whether DUET's own voice through the speaker falsely interrupts it. Fallback:
  Galaxy Buds.
- **Value:** this *is* the theme.

**A2. A corrected value never runs ("Never ran") · 10/10**
- **What:** "Set an alarm for 6:30… no, make it 7" sets 7:00 only. The timeline shows the 6:30 plan as **Never ran**.
- **How:** the coordinator gives every user utterance an epoch. A plan made before your later words is void. No action
  runs while you speak, nor until you have been quiet for the hold: 1.4 s on the phone, 1.8 s after a correction word,
  2.2 s after a dangling phrase.
- **Real.**
- **Verified:** unit tests, suite final_r2 (0 stale actions in 36 scenarios), phone_e2e "destination"
  (office → airport, only the airport set).
- **Value:** Theme 05's "discard stale intent" and "update tool arguments", the heart of Pass@1.

**A3. A late correction is undone ("Undone") · 9/10**
- **What:** "Set an alarm for 3:30." Done. "No, cancel that, make it 4:30." DUET cancels 3:30 and sets 4:30.
- **How:** the thinker is told what already ran ("this really happened… undo it with the matching tool").
- **Real** (DUET's alarms).
- **Verified:** suite scenarios `alarm` and `alarm_late`.
- **Value:** recovery after commit; few agents handle this.

**A4. Nothing runs twice ("Not repeated") · 9/10**
- **What:** if the same plan comes again (a model retry, a re-read turn), the action is answered from the ledger rather
  than repeated.
- **How:** an idempotency ledger keyed on tool and arguments, with one spelling per value (7:00 = 07:00). An unknown
  outcome stays unknown and is never "already done".
- **Real.**
- **Verified:** unit tests; 0 duplicates in suite final_r2.
- **Value:** Theme 05's "never perform the same state-changing action twice".

**A5. Guards that hold whatever the model decides · 9/10**
- **What:**
  - **Cut-off:** "Call my…" does nothing and asks for the rest.
  - **Undecided:** "Call Rahul, or maybe Priya" asks which.
  - **Dropped:** "…never mind, forget it" does nothing.
  - **Next occurrence:** "6:30" at 11:50 pm means tomorrow morning.
  - **Leave it:** "leave the light on" leaves it alone.
  - **Misheard value:** "three thirds" is asked about.
  - **Unasked changes:** a change nobody asked for is offered, not made (consent gate).
  - **Refusal loop:** if the model keeps calling an action it was told not to, the loop ends in a question.
- **How:** deterministic code in the toolbox and coordinator, plus instructions for the cloud mind.
- **Real.**
- **Verified:** unit tests; suite scenarios `cut_off`, `waver`, `waver_call`, `dropped`, `home`.
- **Value:** "approach is sound": safety does not depend on the model behaving.

**A6. Confirmed only when done; never a false "done!" · 9/10**
- **What:** DUET says an action is done the moment the device reports it ("Alarm set for 7 in the morning"), from the
  device's own reply, with no model call. What is not confirmed is said as such:
  - "Sent, not confirmed" for a home device that has not reported back;
  - "It may not have gone through, please check" when the phone never answered;
  - "Your message is ready; tap send" when Messages is opened for you;
  - "with the phone, but it hasn't confirmed it went out" for a text the radio has not confirmed.
- **New 18 Oct:**
  - Direct texts wait for the radio's own result: no SIM, no signal or airplane mode come back as errors. On the
    emulator, a text sent while the radio was still starting came back "radio off"; the old code would have said
    "sent".
  - A cloud-mind sentence that claims more than the device reported is removed. It was caught live: "I've sent that to
    Priya" right after "ready; tap send".
- **Verified:** unit tests (144); phone_e2e Drive `d_text`.
- **Value:** Theme 05's "no false done! claims", word for word.

**A7. Never dead air · 8/10**
- **What:**
  - "Sure." when the fast mind is acting (its confirmation follows in about a second);
  - "One moment." when the cloud mind is needed;
  - "Still working on it." once at most on a long wait;
  - an honest line instead of silence when something breaks.
- **How:** the turn loop in `duet_voice/agent.py`. The fixed lines are pre-synthesised, so they cost no speech time.
- **Measured** (suite final_r2, real speech): first sound p50 0.87 s after you stop. Action done p50 2.54 s; on the fast
  path about 1–2 s. Answer p50 4.09 s.
- **Value:** "stay responsive".

### B. Two speeds: a fast mind on our own GPU, a cloud mind for the rest · 8/10

- **What:** Gemma 4 E2B on the laptop's GPU looks first. It acts alone only on simple, clear actions, and decides in
  about 0.24 s. Everything else goes to Gemma 4 26B: questions, readings, anything unclear, care, a second look after
  you paused.
- **How:** deterministic code (`vet_plan`) lets the fast mind act only when all of these hold:
  - your words name the action;
  - its values are in your words;
  - the last value you said is in its plan;
  - it is not a question;
  - nothing was dropped.

  Otherwise it hands over.
- **Real** (both models).
- **Verified:** fast-mind evaluation, 61 of 62 right, **0 wrong actions**, 14 of 14 held out, p50 0.23 s. The timeline
  labels each step "Fast mind" or "Cloud mind".
- **Value:** the mentors offered extra credit for latency and cost savings. It shows the "dual mind" design, and keeps
  simple actions working when the cloud is gone (G1).

### C. The phone's own actions (Assistant mode, 22 tools)

All real, through Android's public APIs and intents only: no root, no hidden APIs. The self-test passes 14 of 14 on the
Android 16 emulator; S24: pending.

| Capability | What it does | Notes | Score |
|---|---|---|---|
| **Alarms (DUET's own)** | Set, cancel and list, exactly. They ring as an insistent alarm with a Dismiss button and show as the phone's next alarm on the lock screen | Chosen because Google's Clock ignored cancel requests and jumped to the front (measured 17 Oct). The Clock app stays as an option in Settings | 8 (the best vehicle for showing corrections) |
| **Battery diagnosis** | Charge, charging, temperature, health, current drain, brightness, refresh rate, screen timeout, Wi-Fi, Bluetooth, location, dark mode, screen time per app, app categories | Interrupt the explanation with your own theory; DUET checks it against the readings | 8 (real-user value, and interruption in action) |
| **Brightness, screen timeout** | Set, or adaptive on or off | Asked first if you didn't request it (consent) | 6 |
| **Timers** | Clock app timer | Opens the Clock (needs "Appear on top" behind Maps) | 6 |
| **Calls** | The dialer opens with the number; with "Call and text directly", the call is placed through Android's telephony | Contacts typed in DUET's settings first ("my daughter"), then the address book | 7 |
| **Texts** | Messages opens with the text; directly, sent and confirmed by the radio | Times in a text go out in digits (18 Oct) | 7 |
| **Navigation** | Google Maps turn-by-turn (driving, two-wheeler, walking, transit) | Then DUET keeps listening behind it | 7 |
| **Open an app, a settings page** | By name (installed apps are listed), or battery, display, Wi-Fi and so on | Samsung's Device care when present | 5 |
| **Torch** | On or off | Works while the cloud is cut (fast mind) | 5 (good for the drill) |
| **Media** | Play, pause, next, previous | Says "Pressed play on your music app", because a key press is all DUET knows | 5 |
| **Volume** | Media volume to a percent | DUET's own voice may follow it | 5 |
| **Calendar** | Adds an event to the phone's calendar | Needs a writable calendar (Samsung or Google account) | 6 |
| **Weather** | Now and up to a week ahead, for a city | Open-Meteo; falls back to web search when it is down | 6 |
| **Web answers** | News, facts, results, opening hours | Gemma 4 with Google Search grounding; sources kept | 6 |

### D. The real SmartThings home · 8/10

- **What:** real devices in our SmartThings account:
  - living room light and bedroom light (dimmers);
  - air conditioner (temperature, mode);
  - TV;
  - front door lock;
  - robot vacuum;
  - washer.

  DUET reads them, changes them, and reads the change back before it says "done". The changes show live in Samsung's
  SmartThings app and at my.smartthings.com.
- **How:** `duet_voice/galaxy/smartthings.py`:
  - a device name must fit most of your words, so "garage door" never matches the front door lock;
  - an ambiguous name ("the light") gets a question;
  - values are clamped to each device's range;
  - an expired token is said plainly, and the app falls back to its labelled simulated home.
- **Real:** real SmartThings cloud, virtual devices.
- **Verified:** phone_e2e `home_ac` and `home_read`, and every resilience case.
- **Value:** Samsung's own ecosystem: the clearest path to a PRISM worklet and a Bixby-like use case.

### E. Drive mode: hands busy, Maps in front · 8/10

- **What:**
  - a destination changed mid-sentence: only the final one is set;
  - the route handed to Google Maps;
  - then, **with Maps in front**: the home, music, the arrival time, a text to Priya with that time;
  - the car's climate.
- **How:**
  - a microphone foreground service ("DUET is listening") keeps capture alive behind Maps;
  - "Appear on top" lets DUET open the dialer or Clock from behind Maps;
  - without it, DUET says what to allow instead of pretending.
- **Real:** the Maps hand-off and everything on the phone and home.
- **Simulated (labelled):** the trip's progress and arrival time, and the car's climate. There is no car connection.
- **Verified:** phone_e2e Drive 6 of 6, and 19 of 19 Assistant cases including 4 behind Maps.
- **New 18 Oct, phone_resilience** (4 of 4):
  - 5 minutes behind Maps, the microphone still recording and not silenced;
  - a dropped conversation rejoined in about 5 s, still behind Maps, still listening;
  - 20 s without network, then recovered;
  - the whole laptop stack restarted: the phone rejoined by itself in 110 s.
- **Value:** a real "eyes busy" use case. Shows asynchronous work across apps.

### F. Care mode: an older person living alone · 6/10

- **What:**
  - medicines with a **double-dose guard**: the phone refuses a second dose of the same slot;
  - reminders;
  - calls and texts to family;
  - a daily check-in the family sees;
  - a family alert, which never claims to have called emergency services.
- **Real:** calls, texts, reminders (DUET alarms), the home.
- **Simulated (labelled):** the medicine schedule, the check-ins, the family app.
- **Changed 18 Oct:** a direct family alert now counts only the people whose text actually went out.
- **Verified:** suite scenarios `care`, `care_call`, `care_fall`, `reminder_fix`.
- **Value:** strong for "would a real user want it", and the dose guard is a state-safety story. It is not in the
  7-minute script.

### G. Failing well and recovering · 9/10

The mentors asked to see variable latency and failing tasks recovered.

| Failure | What DUET does | Verified |
|---|---|---|
| Cloud mind unreachable (or the **drill** button) | Within 2 s: "I can't reach my cloud mind right now… alarms, timers and the torch still work". Simple actions keep working on the fast mind | phone_e2e drill cases |
| Phone never answers an action | "It may not have gone through, please check"; it is never retried blindly | suite `phone_timeout` |
| Gemma rate limit (free tier) | The key pool waits for room; "Still working on it." once; no failure | Earlier runs |
| Weather service down | Answered through web search | Unit test; seen live 17 Oct |
| SmartThings token expired | "The home is simulated now", said on screen; a new token and a restart fix it | Unit tests |
| Conversation dropped (network, LiveKit) | The app rejoins on its own for 3 minutes, **with the same microphone** (new 18 Oct: before, a rejoin behind Maps left DUET connected but deaf) | phone_resilience `drop`, `network` |
| The laptop stack restarted | The phone rejoins by itself when it is back; a room DUET never joins is left after 30 s | phone_resilience `restart` |
| Android blocks a screen behind Maps | DUET says what to allow ("Appear on top"); nothing is claimed | Self-test, `--no-overlay` runs |
| An unexpected error in a phone action | Reported to DUET and said, never a crash (new 18 Oct: Capacitor would otherwise close the app) | Code; self-test |
| Any one phone action misbehaves on the S24 | Settings › *Simulate phone actions* | Built in |

### H. Seeing the invisible: the live timeline · 9/10 for the demo

- **What:** every step on screen:
  - heard;
  - **Fast mind** or **Cloud mind**;
  - planned;
  - **Never ran**;
  - **Done**;
  - **Undone**;
  - **Not repeated**;
  - **Asked first**;
  - **Sent, not confirmed**;
  - **Unconfirmed**;
  - what DUET said.

  The home panel flashes the device that changed. The orb follows who is speaking.
- **Value:** the jury can *see* that the stale plan never ran. That turns the theme's invisible guarantees into
  something you can show.

### I. Making "does it work?" provable · 7/10

| Tool | What it checks |
|---|---|
| `scripts/phone_check.py` | Installs the app, grants every permission over USB (no restricted-settings prompts), sets the USB route, runs the in-app self-test (14 checks), checks the demo contacts, takes a screenshot |
| `scripts/phone_e2e.py` | The real app, agent, both minds, phone and home: 19 Assistant cases plus 6 Drive cases |
| `scripts/phone_resilience.py` (new) | Drops, 5 minutes behind Maps, network loss, stack restart. A case passes only if DUET is *really* listening afterwards: Android's recording events and the bytes the app sends |
| `scripts/galaxy_suite.py` | 36 spoken scenarios with a scripted voice that interrupts, corrects itself and pauses like a person. Checks for stale and duplicate actions and false claims |
| `scripts/demo_preflight.py` | Keys, LiveKit, SmartThings, the stack, the phone, permissions, battery, Do Not Disturb, media and call volume, the demo contacts → **READY** |
| The app's home screen | "Ready · ✓ Cloud mind · ✓ Fast mind · ✓ SmartThings · 7 devices" |

### J. Small conveniences · 4/10

QR pairing without USB (`pair.html`), a server address over Wi-Fi, typed contacts with relations ("Priya (daughter)"),
the alarm kind (DUET's or the Clock's), "Call and text directly", "Simulate phone actions", and the self-test button.

## 3. The whole app against the problem statement

| Theme 05 / jury question | Where DUET answers it | Strength |
|---|---|---|
| Stay responsive, no dead air | A7, B | Strong: first sound p50 0.87 s; simple actions about 1–2 s |
| No false "done!" claims | A6, G | Strong, and tightened today |
| Work asynchronously | Agent architecture; E (behind Maps); G | Strong |
| Discard stale intent; update arguments | A2, A3, A5 | The core, verified at benchmark scale |
| Never repeat a state-changing action | A4 | Strong |
| Does it actually work? | Emulator: self-test 14/14, e2e 25/25, resilience 4/4, suite 34/36 (both misses fixed and re-run 10/10) | **The S24 itself is untested until the 20th** |
| Is the approach sound? | One coordinator with deterministic guards; models are configuration | Strong; easy to defend in Q&A |
| Would a real user want it? | Battery help, alarms you can correct, the real home, Drive behind Maps, Care | Good; Care and Drive partly simulated |
| Can it become a PRISM worklet? | SmartThings, Android-only public APIs, Bixby-style intents | Plausible. Weakness: it needs our laptop today |

**Overall value against the problem statement: 8 / 10.**
- What earns it:
  - every Theme 05 requirement has a working, tested mechanism;
  - the demo makes the invisible visible;
  - the actions and the home are real;
  - failures are handled honestly.
- What holds it back:
  - the laptop dependency (not on-device);
  - the S24 has not been tested yet (echo, Samsung's background rules);
  - the car and medicine data are simulated;
  - the free-tier cloud quota.
- If the S24 night passes clean, the demo-day value is about **8.5**. A 9+ needs the on-device engine or physical
  devices.

## 4. Maximum potential (not built; say "next step", never "we have")

1. **DUET on the phone alone (Track B):**
   - a Kotlin engine in the microphone service;
   - on-device speech recognition and Gemma 4 E2B (LiteRT-LM) as the fast mind;
   - the coordinator ported to Kotlin;
   - the cloud mind called from the phone.

   The UI stays as it is. This removes the laptop, and the fast path then works with no network at all.
2. **Bixby / worklet:** the coordinator as a layer under Bixby's device agents, where every action gets epochs, the
   commit gate and the ledger.
3. **Physical SmartThings devices** in place of virtual ones; scenes and routines.
4. **Galaxy Buds** as the default audio (no loudspeaker echo); Galaxy Watch for Care alerts.
5. **More languages:** Hindi, Tamil, and code-switching ("alarm 7 baje ka").
6. **Tone:** stress or confusion detected in the voice, kept advisory only (it never triggers an action).
7. **Vision:** "what's this error on my screen?" from a screenshot or the camera.

## 5. Known limits (say them before the jury finds them)

- **Needs the laptop and internet;** the cloud mind is on Google's free tier (about 6 cloud questions a minute across
  3 keys; past that, DUET waits).
- **A question repeated within minutes** may be answered from the earlier reading.
- **The fast mind is on our GPU, not the phone's.**
- **Care's medicines and Drive's car are simulated,** and the app labels them so.
- **"Pressed play"** is all DUET knows about media; Android tells other apps nothing more.
- **On the emulator, typed requests sometimes got "One moment." where "Sure." was due:** the emulator slows the fast
  mind past the 0.5 s grace. With real speech it decided in 0.24 s and said "Sure.".
- **Only the S24 can answer:**
  - loudspeaker echo (fallback: Buds);
  - Samsung's background rules with Maps in front (the resilience run checks it);
  - whether DUET's voice plays as media or as call audio (both volumes up; the preflight checks them);
  - Play Protect at install (tap "Install anyway");
  - the preferred SIM for direct calls and texts.
