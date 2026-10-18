# DUET Round 2: work report, 18 October 2026

Branch `round2` (local, not pushed). Demo: Wednesday 21 October; the S24 only on the night of the 20th.
Every number below comes from a run on this machine today; the phone runs are on the Android 16 emulator
(the S24 is not here yet).

## 1. Checklist (0 to 10, 10 = fully done)

| # | Item | Score | Where it stands |
|---|---|---|---|
| 1 | P1 · DUET speaks first (nearly home: the AC and, after dark, a light; the washer finished: a laundry timer) | 9 | Built and tested end to end, with the live SmartThings home too (arrival 5/5, washer 2/2, home scenarios 16/16). Tonight's tests found five bugs around it, all fixed (section 3). Not yet on the S24 |
| 2 | P2 · DUET as the phone's assistant (side key / assist gesture) | 8 | 5 s from a cold start into a conversation on Android 16. Needs Naman's OK, then the side-key setting on the S24 |
| 3 | P3b · Skills from a file (`app/www/skills.json`) | 9 | Five skills ship; a new one added while the app runs works with no new APK and no restart |
| 4 | P4 · Plain readings answered from the reading | 9 | Battery, alarms, trip, home: 0.21–0.83 s, against 3.7–4.1 s through the cloud mind |
| 5 | P5 · Speak the cloud answer while it is written | measured, not built | The gain is at most 0.1 s (section 2) |
| 6 | P6 · Recovery drills panel | 10 | Cut the cloud, slow it by 6 s, phone refuses, SmartThings down, drop the connection: 9/9 with the live home |
| 7 | P7 · One command verifies everything (`scripts/verify_all.py`) | 9 | Four full runs today, the last two with the live home (section 4) |
| 8 | Project page (`docs/DUET_Project.html`) | 10 | Problem, what we built, caveats, architecture, Samsung, scalability, every use case, proof; screenshots and a screen recording |
| 9 | Readiness on the real S24 | 6 | Scripts and checklist ready (`docs/ROUND2_DEMO.md` §2); nothing has run on that phone yet |
| 10 | Live SmartThings | 9 | Your new token works (7 devices). DUET now stays within SmartThings' ~10 requests a minute. Tokens last 24 h: make a new one on the 20th evening and the 21st morning |
| 11 | Push `round2` | 0 (needs you) | All committed locally |
| 12 | P8 deck and script | 0 | Deferred, as agreed |
| 13 | Guided troubleshooting (Theme 2) | 2 | On hold; scoped in `docs/TROUBLESHOOTING_SCOPE.md` |

## 2. P5, and the architecture of latency

**What P5 would change.** Today the cloud mind (Gemma 4 26B through Google's API) writes its whole answer, and
then the voice (Kokoro) speaks it. P5 would hand the voice the first sentence as soon as it exists, while the model
writes the rest.

**What we measured** (live API, phone-mode prompt, three runs each):

| Answer | Whole answer | First sentence, streamed |
|---|---|---|
| 15 words ("…I'll need to check your battery report. May I?") | 1.70–1.88 s | 1.59–1.91 s (the whole answer came in one piece) |
| 34 words (battery advice, two sentences) | 1.92–2.11 s | 1.92–2.00 s |

So the gain is 0 to 0.1 s. Nearly all of the wait comes before the first word: the request crossing the network
and the model reading its prompt and deciding. Writing two short sentences takes almost no time. Streaming cannot
shorten that wait; only not asking a model can. That is what we built instead:

```
the user's words
   │
   ├─ an answer to DUET's own offer ("yes, make it 22")  → settled from the offer, no model      0.28 s to both actions
   ├─ a plain reading ("how much battery?")              → read it, say it from a template        0.2–0.8 s
   ├─ a simple action ("torch on")                       → fast mind, Gemma 4 E2B on our GPU      0.24 s to decide
   └─ anything else                                      → cloud mind, Gemma 4 26B                about 2 s a call
every action, whichever path: consent check → commit gate → exactly-once ledger → the phone → read back from its reply
```

The two lower paths cost no model at all, and every path still goes through the same coordinator, so the
safety rules never depend on which path answered.

## 3. Bugs found and fixed today

Each was found by a test, fixed, and is now covered by a test.

| Found by | What went wrong | Fix |
|---|---|---|
| Screen recording for the project page | "Shall I start the AC at 24 and switch on the bedroom light?" "Yes, make it 22." Only the AC ran | A yes that changes one part takes up the rest of the offer as offered |
| New spoken scenario `offer_part` | In Drive, "Yes, just the AC, make it 22" set the **car's** AC (the fast mind's guess) | Answers to DUET's offer are settled from the offer itself: no model, no guess |
| The same scenario | With two actions in a row, only the second was read back | Each read-back travels with its own action |
| Run 3, with the live home | SmartThings allows a token about 10 requests a minute. DUET re-read all 7 devices after every action and every 10 s, so commands were refused (429) and the arrival offer could not read the home | The panel is updated from each command's own reply; the washer watch reads only the washer; read-backs back off; the device list is kept 10 minutes; a short limit is waited out once. Home scenarios: 12/16, then 16/16 |
| Run 3, with the live home | After the refused AC command, the cloud mind said "The AC is on, but I can't reach your smart home devices" | A clause that says a failed action happened is dropped before it is spoken |
| Run 2 | One spoken scenario met the free Gemma tier's per-minute token budget (the suite runs ~40 scenarios back to back); DUET answered honestly ("Sorry, I couldn't get that done just now") | Product unchanged; the suite runs such a scenario again after a minute and says so |
| Skills e2e | Changing the text size restarted the app and ended the conversation | The app handles font changes itself |
| Readings | A battery reading was cached across requests (stale answers) | Readings are always fresh |
| Cut-off words ("Call my") | Silence when no call was planned | DUET asks for the rest itself |
| Refusal drill | Silence after the phone refused an action | DUET says what to allow, and claims nothing |
| ETA texts | The fast mind paraphrased away the arrival time | Texts that need a lookup go to the cloud mind |
| "Leave the light on" | Treated as an action | Recognised as leaving things as they are |
| Follow-ups | A follow-up stated a different time from the one just confirmed | Dropped before it is spoken |
| Resilience behind Maps | The microphone service lost a race with Maps opened 2 s after the start; Android silenced DUET | The microphone service starts first, while DUET is on screen |
| The checker itself | Counted an earlier run's microphone as this run's | Fixed |
| A unit test | Expected 06:30 for "6:30" between 18:00 and 18:30, when the next 6:30 is 18:30 | The test computes the next 6:30 |

## 4. Verification (`scripts/verify_all.py`, reports in `results/verify/`)

| Step | Run 2 · 18:39 · simulated home | Run 3 · 19:22 · live SmartThings | Run 4 · 20:42 · live SmartThings, final code |
|---|---|---|---|
| Unit tests | 174 pass | 174 pass | 179 pass (180 after the last fix) |
| Stack: fast mind, cloud mind, LiveKit, SmartThings | pass (no token) | pass, 7 devices | pass, 7 devices |
| Spoken suite | 38/39 (cloud budget) | 35/39 (SmartThings limit) | 38/39 (a misheard question) |
| Phone end to end: Assistant · Drive · arrival · washer · skills · new skill · drills | 19 · 6 · 5 · skipped · 7 · 2 · 7 | 19 · 6 · 5 · 2 · 7 · 2 · 9 | 19 · 6 · 5 · 2 · 7 · 2 · 9 (all pass) |
| Resilience behind Maps | 3/3 | 3/3 | 3/3 |
| Preflight (counts only with `--device s24`) | emulator reds only | emulator reds only | emulator reds only |

Each miss is in section 3, with its fix. After run 4's fix, rechecked on its own: the home scenarios 16/16 with the
live home, the brightness scenarios 6/6, the misheard sentence typed into the app (nothing changed), and the
Assistant end to end 19/19 with the live home.

**Latency** (run 4, spoken suite, from the end of speech, p50): first sound 1.16 s, action done 2.42 s,
answer ready 3.08 s. From the phone runs: an answer to DUET's offer 0.28 s to both actions; a simple action
0.8–1.3 s; a plain reading 0.5–0.8 s; a cloud question 2.5–4.5 s (weather 9 s with "Still working on it").

## 5. What the team needs to do

**Before the 20th**
1. **SmartThings token**: today's expires 24 hours after you made it, around 19:15 on the 19th. Make a new one at
   account.smartthings.com/tokens on the 20th evening and again on the 21st morning, and send it to me (or put it in
   `.env.local` as `SMARTTHINGS_TOKEN`).
2. **Push**: say if `round2` should go to GitHub.

**The night of the 20th, with Naman** (`docs/ROUND2_DEMO.md` §2, in order)
1. Developer options and USB debugging on; Auto Blocker off; a preferred SIM for calls and texts; watch for Play
   Protect when DUET installs.
2. `python scripts/verify_all.py --device s24`, then the 12 hand tests.
3. **Ask Naman** before making DUET the default assistant (`phone_check.py --assistant`, reversible), then set the
   side key.
4. After the run, **stop the test timers in the Clock app by hand. Never force-stop the Clock**: on his phone it
   would cancel his own alarms.
5. Volumes up; the teammates' numbers saved as demo contacts; Galaxy Buds charged as the fallback for echo.

## 6. Score

Overall readiness: **8.7/10**. The code, tests and documents are done, verified in four full runs today (the last
two with the live SmartThings home), and every miss is fixed and rechecked. What remains is what only the S24 can
answer (loudspeaker echo, Samsung's background rules, the side key) and a fresh SmartThings token on the day.
