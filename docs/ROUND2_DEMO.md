# DUET for Galaxy: the Round 2 demo kit

**Demo:** Wednesday 21 October 2026, online: presentation, live demo, Q&A.
**Phone:** Galaxy S24 (SM-S921E, One UI 8.5, Android 16), on the laptop over USB.
**Everything below was rehearsed on the Android 16 emulator first.** The S24 itself is available only on the night of
the 20th; its checklist is in [section 2](#2-the-night-of-the-20th-the-s24).

- [1. What runs where](#1-what-runs-where)
- [2. The night of the 20th: the S24](#2-the-night-of-the-20th-the-s24)
- [3. The morning of the 21st](#3-the-morning-of-the-21st)
- [4. The online set-up](#4-the-online-set-up)
- [5. The demo script](#5-the-demo-script)
- [6. If something goes wrong](#6-if-something-goes-wrong)
- [7. The talk track](#7-the-talk-track)
- [8. Questions the jury may ask](#8-questions-the-jury-may-ask)

---

## 1. What runs where

```
 Galaxy S24 · DUET app                 Laptop (RTX 3060)                            Cloud
 microphone + speaker   ◄──WebRTC──►   LiveKit agent: ears (Silero VAD, Whisper),   Gemma 4 26B (Google API):
 live timeline                         mouth (Kokoro), the coordinator,             questions, explanations,
 real Android actions   ◄───RPC─────   FAST MIND: Gemma 4 E2B (llama.cpp, GPU)      the weather, the web
 (DUET alarms, Clock,                  token server :8787 (USB: adb reverse)        SmartThings REST: the home
  Maps, calls, SMS, torch,             scrcpy: the phone's screen on the laptop     LiveKit Cloud (India South)
  volume, media, calendar)
```

- Simple, clear phone actions are done by the **fast mind** on the laptop's GPU; everything else goes to the
  **cloud mind**. Both go through the same **coordinator**, so a corrected value never acts and nothing runs twice.
- The phone talks to the agent through LiveKit Cloud; the token server is reached through the USB cable.
- **The SmartThings home is real:** 7 virtual devices in our SmartThings account. Changes show live in Samsung's
  SmartThings app and at my.smartthings.com.

## 2. The night of the 20th: the S24

About three hours. Do the steps in order; stop and fix at the first failure.

**On the phone (Naman):**
1. Settings › About phone › Software information: tap **Build number** 7 times (Developer options).
2. Settings › Developer options: **USB debugging** on.
3. Settings › Security and privacy › **Auto Blocker: off** (it blocks USB commands).
4. Charge to 100%. Wi-Fi or mobile data on. Plug into the laptop with a USB-C data cable; tap **Allow** on the
   "Allow USB debugging?" prompt (tick "Always allow").
5. Settings › Connections › SIM manager: a **preferred SIM for calls and for messages** (not "Ask every time"), or
   a direct call or text waits for someone to pick a SIM.
6. Watch the phone while `phone_check.py` installs DUET: if **Google Play Protect** asks about the app ("Scan app" /
   "Unsafe app blocked"), tap *Install without scanning* or *More details › Install anyway*. (DUET asks for none of
   the permissions Play Protect's India pilot blocks: no SMS reading, no notification or accessibility access.)
   If Android ever says **"Restricted setting"** when a permission is allowed by hand: App info › ⋮ › *Allow
   restricted settings*. `phone_check.py` grants everything over USB, so this should not come up.

**On the laptop:**
```bash
bash scripts/galaxy_demo.sh             # with FAST_MODEL set (see 3); wait for "Ready."
python scripts/phone_check.py            # install, permissions, Appear on top, USB route, self-test, screenshot
python scripts/smartthings_setup.py --reset
python scripts/phone_e2e.py              # 19 end-to-end cases on the S24, also behind Google Maps
python scripts/phone_e2e.py --mode drive # 6 Drive cases
python scripts/phone_resilience.py       # behind Maps: a dropped conversation, 5 minutes, 20 s without network;
                                         # each must end "listening" with the request done
```

**Then test by hand, and write down what happens:**

| # | Test | Pass when |
|---|---|---|
| 1 | Self-test (Settings › Run the phone self-test) | All steps green |
| 2 | "Set an alarm for 6:30, no, make it 7" | One DUET alarm at 7; the lock screen shows it as the next alarm |
| 3 | Samsung Clock: Settings › Alarms › "The phone's Clock app"; "Set an alarm for 6:45", then "cancel the 6:45 alarm" | **Decide the alarm kind:** if Samsung Clock really removes it without opening, Clock is an option; otherwise keep DUET's own (the default) |
| 4 | Loudspeaker: let DUET give a long answer in a quiet room, then in a noisy one. **While it speaks, press volume up to the top**: that raises whichever volume DUET's voice uses (media volume on the emulator; call volume if the S24 switches to call audio). Don't ask DUET to lower the media volume during the demo: its own voice may get quieter too | Loud and clear from the loudspeaker (not the earpiece); it never stops itself (no false interruption); if it does, switch to Galaxy Buds |
| 5 | Interrupt a long answer | DUET stops within half a second |
| 6 | Drive mode: "Take me to the airport", "hand it to Google Maps"; with Maps in front, "Turn on the AC at home" and "Tell Priya when I'll get there" | Both done; the notification "DUET is listening" stays |
| 7 | The drill button, then "What's the weather?" and "Turn on the torch" | Honest line at once; the torch works |
| 8 | Contacts: "Call Priya" | The dialer opens with the right number (the teammates' numbers saved as demo contacts) |
| 9 | 10 live trials of the demo script (section 5), by two different speakers | 0 wrong actions |
| 10 | 5 full rehearsals in the real online set-up (section 4) | Clean |

**Known unknowns only the S24 answers:** Samsung Clock's behaviour (3), loudspeaker echo (4), Samsung's background
limits with Maps in front (6). Each has a fallback that is already built: DUET's own alarms, Galaxy Buds, and
"come back to DUET and ask again" (DUET says so itself when Android blocks an action).

## 3. The morning of the 21st

1. **A fresh SmartThings token** (they last 24 hours): account.smartthings.com/tokens › Generate new token › tick
   every box under Devices, Locations and Scenes › copy it into `E:\duet_fdb\.env.local` as
   `SMARTTHINGS_TOKEN=...` (replace the old line).
2. Start the stack:
   ```bash
   FAST_MODEL=/e/duet_local/models/gemma-4-E2B-it-Q4_0.gguf DUET_THINKER_THINKING=minimal bash scripts/galaxy_demo.sh
   ```
   To stop it later: `bash scripts/galaxy_stop.sh`.
3. `python scripts/smartthings_setup.py --reset` (the home's starting state).
4. `python scripts/demo_preflight.py`: one line per check (keys, SmartThings, the stack, the phone, its
   permissions, battery, Do Not Disturb). It must end with **READY**.
5. On the phone, the home screen of DUET shows **"Ready · ✓ Cloud mind · ✓ Fast mind · ✓ SmartThings · 7 devices"**.
   Anything red: fix it before the call.
6. **Pace:** the free Gemma API allows about 4 cloud-mind calls per key per minute (3 keys: about 6 cloud questions
   a minute, each needs two calls). Simple phone actions use the fast mind and no quota. If the jury fires many
   questions at once, DUET waits for room ("Still working on it.") rather than failing.
7. One run of the demo script. Phone: **Do Not Disturb on**, notifications off, brightness up, 100% charged.
8. Close everything else on the laptop (the GPU is shared with the speech models and the fast mind).

## 4. The online set-up

| What | How |
|---|---|
| The phone's screen in the meeting | `set ADB=E:\android-toolchain\sdk\platform-tools\adb.exe` then `E:\android-toolchain\scrcpy-win64-v5.0.1\scrcpy.exe --no-audio --stay-awake --window-title "DUET on Galaxy S24"` (the `ADB` line stops scrcpy's own adb from fighting ours); share that window |
| What the jury hears | The presenter's voice and DUET's, from the phone's loudspeaker, through the laptop microphone. Test the level once: DUET must be clearly audible. (Alternative: scrcpy without `--no-audio` forwards the phone's sound to the laptop, and by default **silences the phone's own speaker**, so DUET then speaks only from the laptop; share system audio. `--audio-source=playback --audio-dup` keeps both, but apps may opt out of capture and DUET's voice is call audio: test before relying on it. Choose by testing.) |
| The SmartThings home | my.smartthings.com in a laptop browser, signed in with the account that owns the home: the devices change live. Share it next to the phone window |
| If the jury wants to interrupt DUET themselves | Their voice comes out of the laptop speaker: put the phone next to it. Rehearse once. Fallback: the presenter says what they ask |
| **When you are not demoing** | DUET hears the meeting too. During the talk and the Q&A, tap the **microphone button** in DUET's top bar (muted), or end the conversation, so it does not answer the jury's questions to you. Unmute for the jury's free play |
| Backup | A recorded run of the whole script (made on the 20th); the laptop browser at http://localhost:8787 runs the same app with the phone simulated |

## 5. The demo script

About 7 minutes, then the jury's turn. **Bold** is what the presenter says; *italics* is what to point at.

**Beat 1 · The hook: a correction (Assistant mode, 45 s)**
- **"Set an alarm for 6:30… no, make it 7."**
- *The timeline:* the 6:30 plan is **Never ran**; "Fast mind: Gemma 4 E2B on our own GPU"; 7:00 **Done**; DUET
  says "Sure. Alarm set for 7 in the morning." about 1.5 s after you stop.
- Say: "It acted on what I meant, not on what I said first, and it never touched the cloud."

**Beat 2 · A late correction: undo (30 s)**
- **"Set an alarm for 3:30."** Wait for "Alarm set…". **"No, cancel that, make it 4:30."**
- *The timeline:* 3:30 **Undone** (it had really been set, so DUET reversed it), 4:30 **Done**.

**Beat 3 · Interrupt it: real troubleshooting (90 s)**
- **"My battery drains super fast these days. What's going on?"**
- While DUET explains, cut in: **"Wait, I think it's my brightness and gaming. Is that it?"**
- DUET stops at once and checks your theory against the phone's real readings (*the panel: battery, temperature,
  brightness, most-used apps*).
- **"Okay, bring the brightness down to 40."** *The screen dims.*

**Beat 4 · The real home (SmartThings, 60 s)**
- *Show my.smartthings.com next to the phone.*
- **"Turn on the AC at home and set it to 22. Actually, make it 21."**
- *SmartThings shows the air conditioner on at 21.* 22 never reached it.
- **"Is the front door locked?"** (a question: the cloud mind reads the home, nothing changes)

**Beat 5 · Drive: while Maps is in front (75 s)** *(switch to Drive mode)*
- **"Take me to the office. No wait, the airport, I have a flight."** Only the airport is set.
- **"Hand it to Google Maps."** Maps opens; the notification says DUET is still listening.
- With Maps in front: **"Turn on the AC at home."** (SmartThings shows it) and **"Tell Priya I'm on my way and
  when I'll get there."** (the arrival time comes from the trip, never a guess). With "Call and text directly" on,
  the text is sent without leaving Maps; otherwise Messages opens with it, ready to send.

**Beat 6 · The cloud goes away (45 s)** *(back to DUET, Assistant mode)*
- Tap **"Drill: cut the cloud mind"**.
- **"What's the weather in Chennai?"**: at once, "I can't reach my cloud mind right now… simple things on the
  phone still work."
- **"Turn on the torch."**: done, by the fast mind. *Restore the cloud mind.*

**Beat 7 · The jury's turn**
- "Please interrupt it however you like: change your mind, cut it off, ask two things at once."

**Beat 8 · Under the hood (30 s)**
- The timeline: every step, where it ran (fast mind or cloud), what was held back and why.
- The architecture slide: two minds, one coordinator; models are configuration, the coordinator is the product.

## 6. If something goes wrong

| What happens | Say | Do |
|---|---|---|
| DUET mishears | "The recogniser heard X; watch, it acts only on what it heard and the timeline shows it." | Repeat more clearly |
| A long pause before an answer (the cloud mind is slow) | "That's the cloud mind; simple actions don't wait for it." | Show the fast mind with an alarm |
| The connection drops | "It rejoins by itself." | The app keeps rejoining for 3 minutes, also behind Maps, with the same microphone (tested: about 5 s after a drop, 20 s after the network returns); otherwise tap End and the mode again |
| The agent hangs or crashes | "Let me restart its brain; the phone reconnects on its own." | `bash scripts/galaxy_stop.sh`, then the start command (section 3): about 2 minutes, and the phone rejoins by itself (tested: 110 s) |
| An action is blocked behind Maps | DUET says what to allow: that is the honest behaviour | Come back to DUET, ask again |
| SmartThings token expired | "The home is simulated in the app now; the screen says so." | New token (section 3, step 1), restart the stack |
| Everything fails | "Here is the recording from last night." | Play the backup recording |

## 7. The talk track

1. **The problem** (30 s): people interrupt, hesitate and correct themselves; voice agents act on the first
   version ("Rome, no wait, Milan" books Rome). The Full-Duplex-Bench v3 paper: the best published system passes 60%.
2. **Our answer** (45 s): two minds and a coordinator. A fast mind that keeps the conversation alive and does simple
   things in about a second; a cloud mind that reasons; one coordinator that decides *when an action may run*:
   never while you speak, never on words you took back, never twice, never claimed before it happened.
3. **Round 1** (20 s): 0.81 Pass@1 on all 100 recordings of FDB-v3 (GPT-Realtime 0.60), 0% interruptions.
4. **Round 2: the phone** (30 s): the same coordinator on a Galaxy, with real actions, the real SmartThings home,
   and two speeds: Gemma 4 E2B on our own GPU for simple actions, Gemma 4 26B in the cloud for the rest.
5. **The demo** (section 5).
6. **What we measured** (30 s): the numbers in the box below.
7. **The worklet** (30 s): the same split moves onto the phone itself. Google's on-device runtime runs Gemma 4 E2B on
   Galaxy phones; the coordinator is 250 lines of deterministic code that ports to Kotlin; Samsung could bind it to
   Bixby's device agents and SmartThings directly.

**The numbers** (17 Oct 2026; refresh with the night-of-the-20th runs):
- Round 1: FDB-v3 Pass@1 0.81 (judge: Gemma 4 standing in for gpt-4o, disclosed); 75/100 by exact match.
- Galaxy Correction Suite, 36 spoken scenarios, two speeds (`results/galaxy/suite/final_r2`): 34/36 passed,
  **0 stale and 0 duplicate actions**, 0 model errors; the two misses (a misheard time, an extra no-op) were fixed and
  re-run 10/10. From the end of speech (p50): "Sure."/"One moment." 0.9 s, action done 2.5 s, answer ready 4.1 s.
- End to end on Android 16 (the emulator), the real app: Assistant 19 cases (also behind Google Maps and with the cloud
  cut) and Drive 6 cases; the last failure was a test expecting "07:00" where the model wrote "7:00" (now one spelling).
- The fast mind alone: 0 wrong actions in 62 decisions, 14/14 held out, 0.23 s to decide (p50).
- 139 unit tests.

## 8. Questions the jury may ask

**The design**

1. *Why two minds instead of one fast model?* A small model is fast but makes mistakes on anything unclear; a large
   one is careful but takes seconds. The small one handles what it can do safely and hands over everything else; the
   user hears "Sure." or "One moment." either way, never silence.
2. *How does the fast mind know when to hand over?* It may act only when the user's own words name the action, the
   values come from those words, the last number said is in its plan, and it is not a question. Deterministic code
   checks this before anything runs; anything else goes to the cloud mind. Measured: 0 wrong actions in 124 cases.
3. *What exactly does the coordinator do?* Epochs (new words void older plans), a commit gate (an action waits until
   you have been quiet 1.4 s on the phone (1.1 s in the benchmark), 1.8 s after a correction, 2.2 s after an open sentence), an idempotency ledger (the same
   action never twice), and a failure policy (a failed read is retried once; a failed or unconfirmed write is never
   re-sent, and the user is told).
4. *Doesn't the hold make it slow?* Thinking starts at once; only acting waits, and the hold mostly overlaps the
   model's thinking. A simple action is confirmed in about 1.5 s.
5. *What if the user corrects after the action ran?* The coordinator tells the model what really happened; it undoes
   it with the matching tool (the alarm example: **Undone**).
6. *Why is "One moment." a fixed line?* Anything that names a value could be wrong a second later ("setting 6:30" while
   the user says "no, 7"). The fixed lines never claim a value or a result.
7. *Why not end-to-end speech-to-speech (Gemini Live, GPT-Realtime)?* They act on partial words; FDB-v3 measures it:
   Gemini Live 3.1 passes 35% of self-corrections. A cascade lets a coordinator sit between understanding and acting.
   It costs latency, which the fast mind wins back.
8. *How do you avoid acting on background speech?* Only words void a plan; noise only delays it. The recogniser drops
   segments it thinks are not speech; the model replies `<silent>` to talk not meant for it.
9. *Tone of voice?* Out of scope for this demo; the design keeps tone advisory: it may change wording or ask for
   confirmation, never act on its own.

**Speed**

10. *Your latency?* From the end of speech (p50, 36 spoken scenarios): "Sure."/"One moment." 0.9 s; a simple action done 2.5 s
    (fast mind, including the 1.4 s safety hold); a cloud answer ready in 4.1 s.
11. *Where does the time go?* End-of-turn detection (~0.5 s), the gate's hold for actions (1.4 s), and the cloud
    model (~3 s a call). Simple actions skip the cloud.
12. *Why is the cloud answer 4–5 s?* Two model calls (decide, then phrase the answer from the tool's result) at ~1.5–3 s
    each through a free-tier API. Minimal thinking halved it.

**Safety and honesty**

13. *How do you know an action really happened?* Only the device's own report counts: an alarm is "done" when Android
    accepted it; a SmartThings device when it reports its new state; anything else is said as "sent, not confirmed".
14. *What if the phone does not answer?* The action is marked unknown, never re-sent; DUET says it may not have gone
    through and asks the user to check (tested: a phone that never answers).
15. *What about Samsung's Clock app?* Android gives other apps no way to read or reliably cancel the Clock's alarms;
    on Google's Clock a cancel request was ignored. So DUET keeps its own exact alarms (shown on the lock screen). A
    Samsung-internal version would call the Clock's own API.
16. *What can it never do?* Payments, acting inside other apps (WhatsApp), calling emergency services. It says so.
17. *Privacy?* Speech recognition, the voice and the fast mind run on our own machine; only requests that need the
    cloud mind go to Google. The SmartThings token stays on the laptop, never on the phone.

**The models**

18. *Why Gemma?* Open weights (Apache 2.0), native tool calling, a family from 2B to 31B: the same model family on
    the phone and in the cloud.
19. *Which exact models?* Gemma 4 E2B (Q4_0, llama.cpp) as the fast mind; Gemma 4 26B-A4B through Google's API as the
    cloud mind; Whisper large-v3-turbo; Kokoro-82M; Silero VAD; LiveKit's turn detector.
20. *Fine-tuning?* None. Instructions, deterministic checks and tests. Fine-tuning E2B on self-corrected phone
    requests is on the roadmap.
21. *What if Gemma's API is down?* Simple actions keep working on the fast mind; DUET says plainly it cannot reach its
    cloud mind (the drill in the demo).

**Testing**

22. *How did you test it?* Unit tests for every rule (139), the Galaxy Correction Suite (36 spoken scenarios with what
    DUET must do, must never do and must never claim, run repeatedly), an end-to-end test of the real app on Android,
    and the fast mind's own evaluation with held-out cases.
23. *Who wrote the test sentences?* We did, from the FDB-v3 failure categories; none are benchmark items.
24. *Real users?* Team members only so far. Next: recorded trials with older users for Care mode.

**Samsung and the worklet**

25. *How does this become a PRISM worklet?* The coordinator plus the two-speed router as a library: Kotlin on the phone,
    the fast mind on-device (Gemma 4 E2B on LiteRT-LM), the cloud mind as today. Inputs: Bixby's callable device agents
    or AppFunctions; outputs: the same exactly-once, never-stale guarantees.
26. *Can it work with Bixby?* Not through public APIs today; with Samsung-internal access the coordinator wraps
    Bixby's agents unchanged. That is the point: it is a layer, not another assistant.
27. *On-device only?* The plan's Track B: all of it on the phone, the cloud only for depth. Gemma 4 E2B runs on Galaxy
    phones through Google's LiteRT-LM.
28. *Cost?* The fast mind is free on the device; the cloud mind is called only when needed, about one to two calls per
    such request.

**Honest limits**

29. *What doesn't work yet?* The phone app runs its brain on a laptop today; the phone is the ears, mouth and hands. The
    car is simulated. English only.
30. *What surprised you?* Most failures were not the model's understanding but timing: acting a second too early.
