# DUET for Galaxy: the failure hunt of 21 Oct 2026

**What this is.** Everything in the project was run, attacked and checked on one day, the way a phone maker's test
team would before handing a build to field testing: the unit tests, the spoken suite, the Android emulator end to end,
a review of the code paths no test reached, and failure injection (parts of the system switched off on purpose). Every
bug found was fixed on branch `round2-tone`, with a test, and re-run. This report lists what was tested, what broke,
what was fixed, and an honest verdict: the good, the doubtful, the outstanding and the bad.

- [1. What was tested, and how](#1-what-was-tested-and-how)
- [2. The bugs found and fixed](#2-the-bugs-found-and-fixed)
- [3. Failure injection](#3-failure-injection)
- [4. Final results](#4-final-results)
- [5. The good](#5-the-good)
- [6. The outstanding](#6-the-outstanding)
- [7. The doubtful](#7-the-doubtful)
- [8. The bad](#8-the-bad)
- [9. Not tested](#9-not-tested)
- [10. Before the S24](#10-before-the-s24)
- [11. The S24 run of 22 Oct](#11-the-s24-run-of-22-oct)

## 1. What was tested, and how

| Layer | How | Real or simulated |
|---|---|---|
| The code | 345 unit tests (`pytest tests`), pyflakes, a read-through of the failure paths | real code |
| The conversation | the spoken suite (`scripts/galaxy_suite.py`): 66 scenarios. A scripted voice (Kokoro, a raised voice made with Praat) joins a LiveKit Cloud room as the phone does, and the whole laptop stack answers: VAD, Whisper, the turn detector, adaptive interruption, the coordinator, both minds, the tone channel, Kokoro | real stack and network; the user's voice and the phone's replies simulated |
| The fast mind alone | `scripts/galaxy_fast_eval.py --repeat 2`: 190 decisions | real model |
| The phone | the Android 16 emulator (`duet_a16`) with the real app (APK built today): install and self-test, end to end in Assistant, Drive, arrival offer, skills, a skill added live and drills (`scripts/phone_e2e.py`); resilience behind Google Maps: dropped room, 5 minutes in the background, network cut (`scripts/phone_resilience.py`); the demo preflight | real app, real Android, real Maps; requests typed into the live conversation (the ears are covered by the suite) |
| Failures on purpose | the fast mind's server killed mid-session; one Google key invalid; every key invalid; the tone channel off; the emotion model off | real stack |

## 2. The bugs found and fixed

Ordered by how much they would have hurt on the S24 or in front of the jury.

| # | Found by | What happened | Why | Fix |
|---|---|---|---|---|
| 1 | emulator, Assistant | After the first action of a conversation, the fast mind answered almost every later simple action **in words** instead of doing it ("Turn the torch off." → a reply, no action). A guard caught each one and sent it to the cloud mind: 2–19 s instead of 1 s, the free tier's per-minute budget ran out ("no key has room"), and **with the cloud cut off (demo beat 6) the torch stayed on** | the fast mind saw earlier exchanges as plain words ("Turn on the torch." / "Torch on.") and imitated them: 0 of 5 actions with such a history, 5 of 5 without | exchanges in which a tool ran are left out of the fast mind's history (the cloud mind keeps all of it). Emulator: fast mind 7 → 15 of 19 requests, action p50 2.59 → 0.94 s, failures 2 → 0 |
| 2 | edge scenario | **"Wait, make the alarm 7"** said over a reply: the 7 was set for 7 am (the 6:30 before it was 6:30 pm) | delivering the reply that was cut off emptied the coordinator's record of the user's words, *after* the correction had been heard: every guard of the correction (next occurrence, cut off, undecided, unsure, consent, the correction hold) read nothing | the coordinator keeps when each phrase was heard; the phone agent keeps what was said after its reply began |
| 3 | edge scenario | "Set an alarm for **quarter to eight** in the evening" was set for **20:45** (a wrong action) | the fast mind's arithmetic | a time said in words wins over a model time that matches none of them; after a correction the last one counts; two times for two things are left alone |
| 4 | the round2 re-test | "Set an alarm for 3:30. No, no, cancel that…" sent a **cancel of 03:30 to the phone** for an alarm never set (the plan had been 15:30); "430" from the recogniser kept 04:30 at 9 am | the never-set check compared times exactly; "430" was not read as 4:30 | one alarm = the same clock time half a day apart when no half was said; "430" is 4:30 (`alarm` 1/3 → 6/6) |
| 5 | review | the consent check **let an action through when its model failed** (fast mind down, or slower than 3 s): an unasked call, text or home change could run | "a failed check never blocks the user" | without its model only a plain yes to DUET's question counts; a broken check asks first |
| 6 | review, then a real call | one **invalid or expired Google key failed every request** that landed on it, whatever the other keys; the health check only tested the first key | a 400 "API key not valid" was a permanent error | that key rests 30 min and the request moves on; `/api/health` checks every key ("1 of 2 work; refused: key 1") |
| 7 | failure injection | with the fast mind's server stopped, every request first waited its whole 3 s deadline | its client retried a closed port | a refused connection is seen in a millisecond: first word p50 2.09 → 1.45 s, action 5.79 → 3.61 s |
| 8 | review | the token server's CORS was `*`: any web page that could reach the laptop could read a room token and type requests, **including to the SmartThings home** | the permissive default | only the app's WebView origin (`https://localhost`, read from the emulator) and `DUET_APP_ORIGINS` |
| 9 | tone review | "an alarm for 4:30, I think, **and a timer for 10 minutes**": the question stopped the plan, so the timer never ran | the reply ended at the question | the other actions run, only the unsure value is asked; a slow reply still ends with the question |
| 10 | edge scenario | a bare "Yes." with nothing pending got "Let me check." and then silence | a fixed bridge line for anything unclear | no bridge for a bare yes or no |
| 11 | emulator, review | doubled confirmations: "Camera's open. I've opened the camera."; "Alarm set for 4:30. I've set your alarm for half past four."; "…7 in the morning. I've changed that to 7:00…" | the restatement check missed possessives, verb forms, times in words, ":00" | all four now dropped; a sentence that says the opposite ("unlocked") is kept |
| 12 | the final regression | after "4:30, I think" was asked back, **"Yes." asked the same question again** (and again) | Windows' monotonic clock moves in 15 ms steps: the request's words and its reply's start shared a tick, so the old words ("I think") were kept for the next request | the coordinator numbers what it hears instead of timing it |
| 13 | the final regression | "Wait, make the alarm 7" (after a 6:30 was set) **added a second alarm** and left the 6:30 | since fix 1 the fast mind no longer sees earlier actions, so a change to one looked like a new one | a change to an earlier action ("make the alarm 7", "change it to 22", "set it for 4:30 instead") goes to the cloud mind, which has the whole history; a correction inside the same words stays fast |
| 14 | the final regression | after holding the unsure value, the fast mind handed the request to the cloud mind, which said the same question 5.8 s later | anything not simply done was handed over | when the unsure value is all that was held, the question is said at once |
| 15 | the tests themselves | the suite never checked the skills (Do Not Disturb, auto-rotate…) as actions; the refusal drill read "I couldn't turn the torch on" as a claim; one tone test depended on the time of day | test bugs | fixed |

## 3. Failure injection

| What was switched off | What DUET did | Verdict |
|---|---|---|
| The fast mind's server, killed mid-session | `/api/health` said so; every request went to the cloud mind; 4 of 4 scenarios passed (alarm, torch, small talk, quarter to eight) | **survives**, now without the 3 s wait |
| The cloud mind (the app's drill) | "I can't reach my cloud mind right now…" at once; simple actions stayed on the fast mind (the torch on and off) | **survives** |
| One of two Google keys invalid | health "1 of 2 work"; every request answered on the good key | **survives** (it failed a third of requests before the fix) |
| Every Google key invalid | the stack refuses to start: "No usable Google API key" | fails fast with a clear message; mid-session it now says the cloud mind cannot be reached |
| The tone channel (`DUET_TONE=0`) | no tone events; round2 behaviour exactly | the switch works |
| The emotion model (`DUET_TONE_EMOTION=0`) | urgent, unsure and frustrated still heard | degrades as designed |
| The phone refuses an action (drill) | "I couldn't turn the torch on, but you can allow this in the DUET settings." | honest |
| The phone does not answer (13 s) | "it may not have gone through, check the phone"; never claims the timer was set | honest |
| The LiveKit room dropped, behind Google Maps | rejoined in 3.7 s without restarting the microphone service, then acted | **survives** |
| 5 minutes behind Google Maps | still connected, still recording | **survives** |
| The phone's network cut for 20 s | recovered, in a new conversation, 18.9 s after the network returned | **survives**, slowly |
| The free tier's budget spent | "Sorry, I couldn't get that done just now. Could you say it again?"; the request stays open and is done with the next words | survives, but slow (section 8) |

## 4. Final results

| Run | Result |
|---|---|
| Unit tests | **345 pass** |
| The spoken suite, all 66 scenarios | **64 of 66**, 0 stale actions, 0 duplicates, 0 model errors; first word p50 1.5 s, action 1.87 s, answer 1.85 s. The 2 misses were real bugs (13 and 12 above), fixed; then those two twice and 16 correction scenarios: 19 of 20 (the one miss: the free tier's per-minute budget, its action done) |
| The tone scenarios (earlier today, three passes) | 24 of 24 |
| The fast mind's decisions | 188 of 190 right, **0 wrong**, 2 handed over |
| Emulator, end to end, on the final code | **46 of 46** (Assistant 19, Drive 6, arrival 5, skills 7, a skill added live 2, drills 7); resilience behind Maps 3 of 3; in Assistant 15 of 19 requests on the fast mind, actions about 1 s |
| Emulator, install and self-test | 14 of 14 |

Across every spoken run today (well over 200 conversations): **0 stale actions** (an action with a value the user had
corrected) and **0 duplicate actions**.

## 5. The good

- **The coordinator is the product, and it holds.** Epochs, the commit gate and the exactly-once ledger: in every run
  today no corrected value ever reached the phone and no action ran twice. "6:30, no, make it 7" never sets 6:30.
- **Graceful degradation everywhere.** Cloud gone: the fast mind keeps simple things working and DUET says so. Fast
  mind gone: the cloud mind takes everything. A bad key: the others carry on. A slow phone: DUET says it may not have
  gone through. Nothing fails silently.
- **It never claims what did not happen.** Confirmations come from the phone's own reply; overclaims, failed claims,
  false undos and restatements are removed before DUET speaks.
- **Deterministic guards around a small model.** The fast mind (2 B parameters on a 6 GB laptop GPU) is fast because
  it is small, and safe because code checks every plan it makes: 0 wrong actions in 190 decisions.
- **Observability.** The timeline on the phone shows every step and why; every conversation is traced; `/api/health`
  names what is missing; one command (`verify_all.py`) runs the whole check.
- **Real phone actions.** 34 Android actions, the SmartThings home, Google Maps, plus skills declared in a JSON file
  with no code (the shape of Android 16's AppFunctions).
- **Testing culture.** 345 unit tests, 66 spoken scenarios, an emulator harness, resilience and preflight scripts; and
  docs that record measurements, not hopes.

## 6. The outstanding

- **Correction-safe full duplex.** Interrupt it and it stops in 0.21 s (LiveKit's adaptive interruption); say
  "mm-hm" and it carries on from the sentence it was in; correct yourself while it thinks and the old plan is void.
  Few voice agents, open or commercial, refuse to act on words you took back; DUET is built around it.
- **It hears how you say it, and that can hold an action.** An unsure value is asked back before it runs; a shouted
  "no" stops it. As far as we found, no other voice agent gates its actions on how sure the user sounds, and here tone
  can never act on its own.
- **It speaks first, from facts.** Nearly home, it offers the AC from SmartThings' real state and the outside
  temperature; the answer ("yes, make it 22", "just the AC") is settled with no model call.
- **It heals itself behind Google Maps.** Dropped rooms rejoin in seconds without breaking Android 14+'s background
  rules; drills show the recovery live.
- **The benchmark.** On FDB-v3 (main), Pass@1 0.81 on all 100 recordings against GPT-Realtime's 0.600 (with our proxy
  judge: see the caveat in section 7).

## 7. The doubtful

- **The free Gemma tier is the weakest link.** Three 15-second stalls today (Google not answering on any key) and the
  per-minute token budget ran out under fast use. Each costs a "Sorry, I couldn't get that done" in front of the jury.
  Mitigation: more keys from separate projects; the fixes above put far fewer requests on the cloud.
- **The token server answers anyone on the same Wi-Fi.** CORS is fixed, but a device on the venue's network can still
  ask for a room token directly and, through it, reach the SmartThings home. At the venue: use the USB cable (adb
  reverse) and start the stack with `DUET_APP_HOST=127.0.0.1`. A pairing secret in the QR code is the proper fix.
- **The tone thresholds come from actors and synthetic voices.** No real shout through the S24's microphone and gain
  control has been measured yet; the run log keeps every segment's numbers to check it.
- **The spoken suite has one synthetic voice** (Kokoro `am_michael`), clean audio, and a simulated phone. Indian
  English accents, noise, a loudspeaker's echo and real Whisper errors are only covered by the S24 recordings of 20 Oct.
- **The benchmark number uses a Gemma proxy judge**, not the official judge; 75 of 100 by exact match. The shared code
  changed today (key pool, coordinator hook, Kokoro speed): its integrity tests pass, but FDB-v3 was not re-run.
- **Recovery after a network cut is slow.** 20 s without network, then 18.9 s more before DUET was listening again (a new conversation: what was said before is not carried over).
- **Many English regular expressions.** The guards are deterministic and tested, but they are English-specific and a
  new phrasing can slip past one ("That is wrong" is not frustration; "Book me a flight" was sent to a web search
  before DUET said it cannot).

## 8. The bad

- **It is not standalone.** The phone needs the laptop (a 6 GB NVIDIA GPU) and the internet; LiveKit Cloud and Google
  must both be up. The phone-native engine (Track B) is not built.
- **Cloud answers are slow next to ChatGPT Live.** Weather and web 4–7 s, an answer p50 about 2 s (the cascade:
  non-streaming Whisper, a free-tier cloud, Kokoro). Simple actions are now fast (about 1 s); questions are not.
- **English only.** "Kal subah saat baje ka alarm laga do" was heard as "Kalsubasat Baj Kalarm Lagadu"; DUET did
  nothing wrong (it looked it up and said it found nothing) but nothing useful either.
- **SmartThings tokens last 24 hours** and are renewed by hand (expired all day today: the simulated home was used).
- **The S24's extra first-word delay** (about +2 s on 20 Oct) is still unexplained; it has not been seen on this laptop
  or the emulator.
- **A long answer can still repeat itself** across clauses ("…battery left, and I've turned on Do Not Disturb" after
  "Do Not Disturb is on."): sentence-level clean-up only.

## 9. Not tested

- The real S24 with this build (the APK with today's changes is built, not installed).
- Real human voices through the app's microphone (tone thresholds, Whisper on accents, echo on the loudspeaker).
- Two phones in two conversations at once.
- The live SmartThings home (token expired); the washer offer (it needs the live home).
- An FDB-v3 re-run after today's shared-code changes.

## 10. Before the S24

1. `git switch round2-tone && git pull`, `pip install -r requirements.txt` (adds `praat-parselmouth`).
2. A fresh SmartThings token in `.env.local`; check `/api/health` shows `cloud_mind` "3 of 3 work" and `tone` ok.
3. Build and install the APK (ROUND2_SETUP.md sections 7 and 9); use the USB cable; at a venue start the stack with
   `DUET_APP_HOST=127.0.0.1`.
4. Run `scripts/verify_all.py --device s24`.
5. Talk to it for five minutes, including a shouted "no" and "4:30, I think", then read the `tone_segment` numbers
   in `results/galaxy/traces/` to check the thresholds on your own voices.

## 11. The S24 run of 22 Oct

**Set-up.** The stack ran on this laptop and the phone ran Naman's build of the app. That build is not ours: it
was updated at 01:27 that night and adds a `get_location` method to the native plugin and the web UI. The phone was
reached by **wireless debugging**: this laptop has no Android USB driver for the S24, so adb saw nothing over the cable.
Over about 25 conversations everything held: calls, texts, the torch, Maps, the SmartThings home and corrections
("set it to 27, oh wait, 23"). The traces showed these failures, all fixed with tests:

| What the user heard | Why | Fix |
|---|---|---|
| "Can you fetch my location?" read the **battery**, and "how far is the airport from me?" could not say | the agent had no location tool at all (Naman's app can read the GPS, but nothing asked it) | `get_location` (the phone's GPS, named with OpenStreetMap: "Potheri, Chennai, Tamil Nadu"), `get_distance` (from the GPS, by road with OSRM: the airport 26 km, 32 minutes) and `get_time`, run by DUET itself (`server_tools.py`); "where am I?" is a plain reading said from a template |
| "Isn't it afternoon now?" → "5:48 AM"; "time in Singapore?" → "the results do not provide it" | time questions went to a web search, which knew neither the user's time zone nor the clock | "what time is it" (here, or in a named place) is a plain reading from the clock and the place's time zone, said from a template; time and distance questions never go to a web search |
| London "half past seven", then "the exact time in London" → **6:25** (it was 7:25) | the cloud mind rounded the tool's 7:25; "exact time" slipped past the routing and a search gave UTC as London's time | "exact/current time in <place>" is a reading too; the cloud mind is told to say times exactly; searches are told the time in UTC |
| "11:05" sounded like **"ELEVENFIVE"** | Kokoro reads "11:05" as "eleven-zero five" and "10:00" as "ten zero zero" | `confirm.speakable`: times in words before the voice ("eleven oh five", "ten o'clock", "8 AM"); the screen keeps digits |
| "Restaurants near me" → "I don't have access to your location" | the search had no place in it | a near-me question is searched with the place ("near Potheri, Chennai") and carries the date and time |
| "Set an **8am** alarm" → 8 in the **evening** | the AM/PM check needed a space ("8 am"); it also read "I **am**" as AM | AM/PM counts right after a time, glued or not |
| "Add the exams to my calendar and set an 8am alarm" → only the alarm | the fast mind has no calendar tool and did the part it could | words the fast mind has no tool for (calendar, weather, search, how far, where am I) send the whole request to the cloud mind |
| Exams "on the 24th, 29 and 31st" → one on **today's** date, one missing, both at a 9 AM nobody said | the cloud mind's dates and times | a calendar event needs a time the user said, on a day they said (also a turn earlier); otherwise DUET asks |
| a room the phone left before joining was logged as an unhandled exception | — | a normal exit |

**Verified on 22 Oct (this laptop, live stack, LiveKit Cloud, the free Gemma tier):** unit tests **365 pass** (20 new,
`tests/test_galaxy_place_time.py`); the fast mind's 190 decisions: 188 right, **0 wrong**, actions decided in 0.23 s (the
same as before); eight new spoken scenarios (`where_am_i`, `how_far`, `time_here`, `time_london`, `alarm_8am`,
`calendar_and_alarm`, `calendar_said_day`, `web_near_me`): **8 of 8**, 0 stale actions, 0 duplicates. The full suite was
started and cut short by a session restart after 4 scenarios (all passed); it was not re-run.

**Still open:**
- Naman's app changes (`get_location` in `DuetPhonePlugin.java`, `phone.js`, `app.js`, `index.html`) are not in this
  repository: an APK rebuilt from here has no GPS, and `get_location` then says so plainly. Commit them.
- Install Samsung's Android USB driver on the demo laptop, or keep the phone's Developer options › Stay awake on:
  wireless debugging drops (and changes port) when the screen locks.
- The cloud mind added an exam to the calendar when it was only mentioned ("I have an exam on the 29th"), before it
  was asked; calendar events are not under the consent check.
