# Guided troubleshooting in DUET: what the materials ask for, feasibility on the S24, value, scope

Internal analysis (18 Oct 2026) of the troubleshooting materials shared on 18 Oct: the problem PDF (9 pages),
`schema.py`, `input.txt`, `sample_output.json`, `siis_responses.json` and `deeplinks.json`. Outside this file it is
simply a DUET capability, with no reference to where the idea came from. Nothing is built yet: this is for your
decision.

## 1. What the materials ask for (in short)

**An engine that turns a vague complaint into a structured, ordered, one-tap troubleshooting plan:**
- **The complaint:** "screen flickers and the battery dies fast".
- **The plan:**
  - a goal and a title;
  - **actions**, each with UI steps;
  - one **actionable deeplink** per settings screen ("one action = one screen");
  - a **validation key** that reads the setting back;
  - a **category**: auto (a settings change), manual (physical, a service centre) or critical (restart, reset; always
    last).
- **Ordering:** least disruptive first, critical last.

**Rules:**
- **Steps come only from the reference support text** (no invented steps). If it has nothing, return an empty plan
  with `"fallback": "no_match"`.
- **Deeplinks come only from the catalog**, matched on their descriptions.
- **No web links.**
- **Strict schema:**
  - goal: "Follow these steps to perform this … Troubleshooting";
  - title: 2–3 words;
  - description: 5–7 words, starting "It will".

**Speed:**
- complaints seen before: P95 ≤ 300 ms, from a semantic cache with 8–10 paraphrases per complaint;
- new complaints: P95 ≤ 8 s;
- served as a REST API (`POST /v1/troubleshoot`, `GET /health`).

**Their motivation:** human support agents take about **15 minutes per scenario**, across millions of interactions.

## 2. What is in the files, and what is usable

| File | What it is | Usable for DUET? |
|---|---|---|
| PDF | The pipeline, rules, ordering, evaluation template | **Yes, as design rules:** ordering, read-back validation, no invented steps, fast path for known complaints, schema conformance |
| `schema.py` | Pydantic schema of the plan (Goal → Action → StepGroup, deeplinks, validation key/condition/value, category) | **Yes:** a ready format for DUET's plan and its report |
| `deeplinks.json` | 578 Galaxy settings entries (switch on, switch off, set a value, open a screen), each with a read-back key such as "Adaptive battery", "Adaptive power saving" or "Show charging information" | **Not as actions:** every URI is a masked token (`voiceassist://masked/act/…`) that opens nothing on a real phone. **Useful as a map** of which Galaxy settings matter, by topic: display 211, sound and notifications 141, security 70, apps 60, battery 57, accessibility 53, network 47, performance 36, camera 9 |
| `siis_responses.json` | 20 support articles, anonymised ("TechCorp", "Nexa X1"). Mostly display problems (black screen ×6, cracked screen, touch, rotation, flicker with the camera), plus email, Data Transfer, Multi window, screen mirroring | **Partly:** shows how steps are grounded in text. Anonymised and mostly hardware or manual issues, so not shippable as DUET's knowledge as-is |
| `input.txt` | 20 complaints, all about displays, with anonymised device names | As test inputs for a DUET evaluation set, adapted |
| `sample_output.json` | Cracked screen → back up first (auto), then the service centre (manual) | The pattern "protect data first, hardware last" |

## 3. Your idea against theirs

| | The materials | Your idea (DUET) |
|---|---|---|
| Input | The complaint text (plus a support article) | The spoken complaint **and the phone's live readings** |
| Output | A plan for a person to follow | A conversation: what it found, one fix at a time, **"shall I?"**, done, **verified**, next |
| Who acts | The user taps | **DUET does it** (or opens the exact screen when Android does not allow apps to act) |
| A "no" | — | "Okay. Here's what else I can do…" |
| End | — | **A report:** found, done (verified), skipped, still for you |
| Interruptions | — | Full duplex: "wait, isn't it my games?" mid-explanation, and DUET checks that theory against the readings |

**Yours is the bigger and more useful version.** The materials add the rigour it needs:
- a fixed plan format;
- least disruptive first, critical last;
- every action verified by reading the setting back;
- steps only from known sources;
- a fast path for common complaints.

## 4. Feasibility on Naman's S24 (DUET sideloaded, permissions granted over USB)

Checked on the Android 16 emulator today, with adb only:
- **Granted over USB:** DUET can be given write access to secure settings, Do Not Disturb access, and battery-stats and
  dump access. These are development permissions, so `phone_check.py` can grant them.
- **Power saving does not work through a direct setting write.** Writing the setting left Battery Saver **OFF** on
  Android 16. So power saving is a one-tap screen, not a silent change.
- **Readable:** auto-rotate, navigation mode (gestures vs buttons), location mode, uptime, the security patch date,
  storage.

### 4a. What DUET can read (the diagnosis), on the phone

| Area | Readings | Already in DUET? |
|---|---|---|
| Battery | Level, charging, temperature, health, **current drain**, charge cycles, power saving, thermal state | Yes (`get_battery_report`) |
| Screen | Brightness, adaptive brightness, timeout, refresh rate, dark mode, auto-rotate, navigation mode (gestures or buttons) | Mostly; rotation and navigation mode are new |
| Use | **Screen time per app** (24 h), installed apps with categories, **recently installed apps** ("slow since I installed…") | Screen time yes; recent installs new |
| Performance | **Days since the last restart**, free memory, free storage, thermal state, **security patch age** | New |
| Connections | Wi-Fi, Bluetooth, location, airplane mode, mobile data, internet validated | Partly |
| Sound | Do Not Disturb, ringer, volumes | Partly |

**Not readable by an app that isn't part of the system:**
- battery % per app: Android keeps it system-only, so DUET uses screen time and current drain instead, and opens
  Samsung's battery-usage screen for the exact numbers;
- other apps' background activity.

### 4b. What DUET can fix, in their three categories

| Category | Fix | How |
|---|---|---|
| **auto, done directly and verified by reading back** | Brightness, adaptive brightness, screen timeout, auto-rotate, volumes, Do Not Disturb (after the USB grant), the torch | Android settings and APIs; DUET reads the value back before saying "done" |
| **auto, Samsung-specific (check on the S24 night)** | Refresh rate (Motion smoothness), Always On Display | Samsung's own setting keys: listed on the S24 with `adb shell settings list`. If one does not work, DUET opens its screen instead |
| **auto, one tap on the exact screen DUET opens** | Power saving (measured: a direct write is ignored), an app's page (restrict its background use, force stop, clear cache), battery usage per app, storage, the Wi-Fi / internet panel, navigation bar, Device care, software update, location | Android's settings intents. DUET says exactly what to tap, and checks the result where Android lets it |
| **critical, always last, never automatic** | Restart (DUET can tell how many days since the last one), safe mode, reset network settings; factory reset only with "back up first" | Spoken steps, plus the screen where one exists |
| **manual** | Cracked or black screen, battery swelling, water: the service centre | Spoken guidance |

### 4c. Latency

**Reading the phone** (measured on the emulator):
- battery report 0.5 s;
- screen time 0.1 s;
- installed apps 0.1–0.25 s.

**Planning:**
- a known complaint (battery drain, slow or hot phone, screen won't rotate, gestures wrong, Wi-Fi) comes from a cached
  plan plus the live readings, **about 1 s**;
- a new complaint goes to the cloud mind, **about 4–6 s**, with "Let me check your phone" said at once.

**Each fix:** about 0.3 s plus the read-back.

**Placement:** readings and fixes run on the phone (they are phone APIs); reasoning stays on the laptop and cloud.
That follows the latency rule.

## 5. Is it a value addition?

| Question | Answer |
|---|---|
| Real-user value | **High.** Battery drain, a slow phone and screen glitches are the most common reasons people need help. Support takes about 15 minutes per case; DUET does it in about a minute, by voice, and actually applies the fixes |
| Relevance to Samsung | **High.** It sits where Samsung Members, Device care and the new Bixby device agent are going. The plan format follows Samsung's own troubleshooting schema |
| Fit with Theme 05 (our theme) | **Strong.** Interrupting a diagnosis with your own theory, saying "no, not that", correcting mid-step: full duplex makes the plan a conversation. Every fix goes through the coordinator: asked before doing, never twice, verified before "done", critical steps last, and **"undo everything you changed"** |
| Differentiation | Other finalists of our theme won't have it |
| Builds on what exists | Yes: the battery report, settings changes, consent, confirmations and the timeline are all there. It overlaps P3b (the catalog of actions) and P4 (fast readings), so it can absorb them |
| Risks | Scope creep before the 21st. Samsung-specific keys can only be checked on the S24. Some fixes can only open a screen. Being honest about each of these is part of the design |

## 6. Proposed design: the "check-up"

```
"My battery dies so fast these days."
   → DUET: "Let me check your phone." (readings, ~0.5 s)
   → findings (facts from readings, deterministic): brightness 100 % with adaptive off; BGMI 3 h of screen time today;
     120 Hz always on; 19 days since a restart; battery 41 °C
   → plan: the findings mapped to fixes from DUET's verified catalog, ordered auto → screen → critical
   → one at a time:
       DUET: "Your brightness is at 100 % and adaptive brightness is off. Turn adaptive brightness on?"
       You:  "Yes."                → done, read back: "Adaptive brightness is on."
       DUET: "BGMI used 3 hours of screen time today. Open its battery settings so you can limit it?"
       You:  "No, I need it."      → "Okay." (skipped, never asked again in this check-up)
       You:  "Wait, is it the heat?" → DUET checks: "Your battery is at 41 degrees, warm; charging while gaming adds to it…"
       DUET: "Last one: your phone hasn't restarted in 19 days. Restart it when you can."  (critical: advice, last)
   → report, spoken and on screen:
       found 4 · done 1 (verified) · skipped 1 · still for you 1 · "undo" restores everything changed
```

**Who decides what:**
- Findings are computed from the readings by code, so they can't be invented.
- The model chooses and phrases from DUET's catalog: it can't invent a fix or a screen.
- The order is enforced by code.
- Each fix is one coordinator action.
- The report can also be exported in the materials' plan format, with DUET's real deeplinks and outcomes.

## 7. Scope options

| Option | What | Effort | Demo |
|---|---|---|---|
| **S · battery and performance check-up** | Readings (battery, screen, use, uptime, storage, patch), ~10 real fixes in the three categories, the yes/no loop with skip and undo, read-back, report card | ~7 h | Replaces today's battery beat (no extra demo time) |
| **M · + screen and connection problems** | Rotation, gestures vs buttons ("swipes go the wrong way"), touch sensitivity, Wi-Fi / internet; cached plans for the common complaints (~1 s); the report in the materials' format | +5 h | One more complaint, or used in the jury's free play |
| **L · + the engine as a service** | `POST /v1/troubleshoot` on the token server (voice is one client; Samsung Members-style apps could be others); an evaluation set from the 20 complaints (format, order, no links, latency) | +4 h | Q&A material ("it's an engine, not a script") |

**Recommendation:** S + M (~12 h) as a new item, **P11**. It absorbs P3b (its catalog is the skills manifest) and P4
(fast readings). L only if time allows.

## 8. Caveats (we say them first)

- **The masked deeplinks and anonymised articles can't be used on a real phone.** DUET's catalog is its own: real
  Android settings and screens, each checked on the S24.
- **Per-app battery use is system-only.** DUET uses screen time and current drain, and opens Samsung's own screen for
  per-app numbers.
- **Some fixes Android lets only the user do:** power saving (measured), force stop, clear another app's cache,
  restart. DUET opens the exact screen and says so; it never claims to have done them.
- **Samsung-specific setting keys** (refresh rate, Always On Display) are verified on the S24 night, or replaced by
  their screens.
- **Never automatic:** anything critical or destructive is advice only, last, and a reset is never offered without
  "back up first".

## 9. Decisions for you

1. Add guided troubleshooting as **P11**, and at which scope (S, S+M, or S+M+L)?
2. Should it replace the battery beat in the demo (my suggestion), or be its own beat?
3. On the S24 night, may I list the phone's settings keys (`adb shell settings list`, read-only) and grant DUET the
   extra USB permissions (secure settings, Do Not Disturb)? Please ask Naman.
