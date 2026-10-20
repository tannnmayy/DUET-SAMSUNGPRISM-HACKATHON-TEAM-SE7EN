# DUET Round 2: from "One moment." to a real conversation

**Written:** 20 Oct 2026, night, after the first runs on the real Galaxy S24.
**For:** the 21 Oct jury demo.
**Branch:** `round2` (E:\duet_fdb). Nothing in this plan is built yet; it needs your go-ahead.
**Evidence used:** every file in `duet_voice/`, `app/www/`, the Round 2 docs; the four videos from tonight
(three S24 runs and one ChatGPT Live reference), timed word by word with Whisper; 996 requests in the 18 Oct
emulator traces; and probes run tonight on this laptop against the real fast mind (Gemma 4 E2B) and the real
cloud mind (Gemma 4 26B). Probe scripts: the session scratchpad, `tools/probe_fast*.py`, `probe_cloud.py`.

- [1. The goal](#1-the-goal)
- [2. What the S24 videos show](#2-what-the-s24-videos-show)
- [3. How a turn works today, with measured times](#3-how-a-turn-works-today-with-measured-times)
- [4. Problems and solutions](#4-problems-and-solutions)
- [5. Your idea: two API calls instead of a local fast mind](#5-your-idea-two-api-calls-instead-of-a-local-fast-mind)
- [6. The new turn](#6-the-new-turn)
- [7. Implementation plan](#7-implementation-plan)
- [8. Risks](#8-risks)
- [9. Naman's issues and recommendations](#9-namans-issues-and-recommendations)
- [10. What I need from you](#10-what-i-need-from-you)
- [11. Compared with ChatGPT Live: what is missing](#11-compared-with-chatgpt-live-what-is-missing)

---

## 1. The goal

**DUET talks like a person and acts like a careful machine.** Every turn gets a real reply, fast; small talk is
answered at once; anything that needs the cloud is bridged with a line that shows DUET understood; open
conversation gets a relevant follow-up question, the way ChatGPT Live does. Everything that already works stays
as it is: interruptions, self-corrections, never acting on words taken back, never acting twice, never claiming
an action that did not happen.

Gates, measured on the demo laptop with the S24, from the end of the user's last word:

| # | Gate | Today (S24, 20 Oct) | Target |
|---|---|---|---|
| C1 | Turns that get a real reply (not "One moment." alone, not silence) | 2 of 9 in the small-talk video | **every turn** in a 30-turn conversation script |
| C2 | Small talk: first words | "One moment." at 1.9–4.6 s, then nothing | **≤ 2.0 s p50** at the phone |
| C3 | Cloud turns: first words | fixed "One moment." at 2.8–4.6 s | a line in DUET's own words **≤ 2.0 s p50**; the fixed line on at most 1 in 5 cloud turns |
| C4 | Web answers | 15.6 s (both web videos) | **≤ 7 s p50** |
| C5 | Health / advice answers | 6.5–7.0 s | **≤ 4.5 s p50** |
| C6 | Follow-up question in open conversation (feelings, plans, advice) | none | **≥ 7 in 10** such replies; never after a confirmed action |
| C7 | Safety, unchanged | fast-mind eval 0 wrong actions; suite 38/39; 180 unit tests | the same, plus the new tests |
| C8 | Interruptions and corrections, unchanged | work (videos 2 and 4) | the same |

For reference, ChatGPT Live in tonight's video answers in about 1 to 2.5 s, in short turns, and almost every reply
ends with a question that keeps the conversation going ("A rest day is important too. So, a movie marathon, or
just sleep?").

## 2. What the S24 videos show

Times are from the end of the user's last word to DUET's first audio, read off the video's sound.

| Video | User | DUET | Gap |
|---|---|---|---|
| 1 (21:11) | "Can you check when is the next Apple event?" | "One moment." / "Still working on it." / correct answer (Pro and Tell, 28 Oct) | 3.6 s / 9.7 s / **15.6 s** |
| 2 (21:21) | "When was the latest Samsung phone launched?" … "No, check for the latest iPhone launch." | "One moment." (cut off by the correction), "One moment.", "Still working on it.", correct iPhone answer | 2.8 s; then 3.6 / 9.6 / **15.6 s** after the correction |
| 2 | "Okay." | "One moment.", then "Waiting for you to finish", then **silence** (15 s, until the video ends) | 2.0 s |
| 4 (21:28) | "Good night." | "One moment.", then **nothing**. Timeline: the fast mind planned `set_alarm`; the safety check refused it ("the words do not name set_alarm"); the cloud mind said nothing | 4.6 s |
| 4 | "I said good night." · "Good morning." · "How are you?" · "Thank you." | "One moment." each time, then **nothing** | 2.2 / 4.1 / 1.9 s |
| 4 | "I am not good." | **nothing** for 11 s | — |
| 4 | "What do I do? I have a stomach pain." | "One moment.", then a correct but generic answer | 3.8 s / 6.5 s |
| 4 | Cut in: "But I also have a headache." | **stopped at once** (barge-in works), "One moment.", a fitting answer | 3.7 s / 7.0 s |
| 4 | "Okay, good night." | "One moment.", then nothing | — |

Also visible in every video: the top bar stays on "Connected · waiting for DUET…" for the whole conversation.

**What works, and must keep working:** barge-in (DUET stops mid-sentence), self-correction (Samsung → iPhone was
answered for the iPhone), web answers are correct, and, from your testing, the phone actions (torch, calls, alarms,
texts, permissions), Google Maps, SmartThings.

**The first-word delay on the S24 is about 2 s longer than on this laptop's emulator runs.** Emulator, 996
requests: the first line came 1.1 s after the agent saw the speech end (about 1.5 s from the last word). S24
tonight: 3.6 s p50 (10 turns). Tonight's stack ran on Naman's laptop, and its logs are not on this machine, so the
cause is not known yet. The likely ones: that laptop's GPU (Whisper or the fast mind running slower or on the
CPU; the fast mind gets 3 s before it is skipped), or the network path. Section 7, Phase 0 settles it.

## 3. How a turn works today, with measured times

Medians from the 18 Oct emulator traces (996 requests), measured from the moment the agent saw the user stop:

```
user stops ─► VAD notices (0.35 s of quiet) ─► Whisper transcript 0.59 s ─► turn closed 0.62 s
           ─► FAST MIND (Gemma 4 E2B, this GPU) 0.3 s: may only act, keep listening, or hand over.
              It is not allowed to say anything itself (fast.py, FAST_INSTRUCTIONS:
              "Never answer a question yourself ... never reply without calling a tool").
              ├─ simple action ─► "Sure." 1.0 s ─► gate holds 1.4 s of quiet ─► phone ─► "Alarm set for 7." ~2.4 s
              ├─ plain reading ─► template answer, no model, 0.5–0.8 s
              └─ anything else, including "Good morning" ─► CLOUD MIND
                    "One moment." at once (1.0 s), before the cloud has decided anything
                    Gemma 4 26B call #1, ~3,800 input tokens (22 tools + instructions): 1.5–2.0 s
                    ├─ no tool: its answer ─► spoken ~2.8 s. For greetings that answer is "<silent>" (see P1)
                    └─ a tool: the tool, then call #2 to word the answer ─► ~5.3 s
                          web_search is itself a third Gemma call (Google Search grounding, 3.6–6.4 s)
                          ─► 8.5 s on the emulator, 15.6 s on the S24
```

Measured tonight on this laptop:

| What | Time | Notes |
|---|---|---|
| Fast mind (E2B), small-talk reply, prompt cached | **0.25–0.45 s** | 16 cases from the videos, 2 rounds; first call after start-up 1.2–2.4 s (cache cold) |
| Fast mind, hand-over with a bridge line and a self-contained web question | **0.55–0.65 s** | "Let me check when the next Apple event is for you." / question "When was the latest iPhone launched?" from the Samsung → iPhone correction |
| Fast mind, actions | 0.18–0.24 s | `set_alarm 07:00` for "6:30, no, make it 7"; `keep_listening` for "Call my" |
| Cloud mind, full phone prompt, "Good morning." | 1.45–1.59 s, 3,775 input tokens | **`<silent>` 3 times out of 3** |
| Cloud mind, light prompt (no tools), health question with history | 2.0 s (3.2 s first), **100 input tokens** | "I'm sorry you're feeling that way, Tanmay. For mild pain, resting and staying hydrated often helps, but you should see a doctor if…" |
| One grounded web search, called directly | 3.6–6.4 s | correct answer, already worded to be spoken |

## 4. Problems and solutions

Ordered by what the jury will notice first.

### P1. Small talk gets "One moment." and then silence
- **Seen:** video 4, five turns in a row; in the emulator traces too: "Hi DUET, how are you doing today?" got no
  answer **6 times out of 6**.
- **Cause, two parts:**
  1. The fast mind is not allowed to talk (`galaxy/fast.py`, `FAST_INSTRUCTIONS`; `HybridThinker.run` hands every
     reply without a tool to the cloud: "nothing for the fast mind to do").
  2. The cloud mind is told *"A separate fast voice has already acknowledged the user, so do not greet them"* and
     *"If the words are not meant for you … reply exactly <silent>"* (`galaxy/modes.py`, ASSISTANT/CARE/DRIVE and
     ACTING). Gemma reads a greeting as one of those: `<silent>` 3 of 3 tonight.
- **Fix:**
  - The fast mind gets a `chat_reply` tool and answers small talk itself (greetings, goodbyes, thanks, "how are
    you", "okay", "what can you do", "I'm not good", "nothing much, I want to rest"). Measured: 0.25–0.45 s, warm
    and natural, with a question back.
  - A deterministic small-talk check decides whether its reply may be spoken: the user's words are short, name no
    action, ask nothing about the world, and carry no health or emergency words (pain, fell, dizzy, medicine…);
    the reply has no numbers, claims no action, and is at most 35 words. Anything else goes to the cloud mind,
    with the fast mind's first sentence as the bridge line (P3). Tonight's probes show why the check is needed:
    for the battery question the small model once planned `open_settings` (refused by the existing safety check),
    and for "Call my" it once planned `call_contact("my")` (refused by the existing cut-off guard).
  - Cloud instructions: greetings, thanks and small talk *are* meant for DUET; answer them warmly in one sentence.
    `<silent>` only for background talk and noise.

### P2. After a silent reply the words pile up
- **Seen:** video 4: "Good night. I said good night. Good morning. How are you. Thank you. I am not good." became
  one open request, finally answered by the stomach-pain reply.
- **Cause:** `GalaxyAgent.silent_answers` (`galaxy/agent.py`) keeps the request open after any `<silent>` with no
  action, a fix made for "Call my" on 17 Oct.
- **Fix:** keep it open only when the words are cut off ("Call my") or a guard held an action back; otherwise a
  silent reply closes the request.

### P3. "One moment." is fixed, context-free, and comes before the cloud has decided anything
- **Seen:** every cloud turn; "Okay." got "One moment." and then "Waiting for you to finish" (the cloud called
  `keep_listening` after the line was already spoken).
- **Cause:** the `route` event to the cloud sets `cover_now` (`duet_voice/agent.py`, `llm_node`) and
  `GalaxyAgent.ack_line` says "One moment." for any cloud route.
- **Fix:**
  - The fast mind's hand-over (`ask_expert`) gets a `say` field: one short sentence in its own words, written in the
    same call at no extra cost ("Let me check when the next Apple event is.", "I'm sorry you're in pain."). Rule 3 of
    the Round 2 plan still holds: no time, number or value of an action, and never a claim.
  - Code cleans it: drops a question (the model wrote "What kind of phone are you using?" once), drops digits, caps
    the length. If nothing usable is left, a varied fixed line by kind: "Let me check." / "Let me look that up." /
    "Let me think about that." (synthesized once at start-up, like "Sure." today).
  - A `bridge` event in the turn loop speaks it at once. The benchmark thinker never sends that event, so the
    benchmark path is unchanged.
  - No line at all when the fast mind says `keep_listening`.

### P4. Web answers take three Gemma calls in a row (15.6 s on the S24)
- **Cause:** cloud call #1 decides `web_search` → the gate → `web_search` (itself a grounded Gemma call, 3.6–6.4 s) →
  cloud call #2 words the answer → "Still working on it." in between.
- **Fix:** the fast mind already writes a self-contained question when it hands over a web question (measured
  tonight: "When was the latest iPhone launched?" from the Samsung → iPhone correction). That question goes
  straight to `web_search` through the toolbox (the same coordinator: gate, ledger, timeline), and its answer is
  spoken as it comes (the search is already told to answer in two spoken sentences). One call instead of three.
  If the search fails or finds nothing, the request goes to the cloud mind as today.
  Expected: bridge line at ~1.7 s, answer at ~5–7 s (from 15.6 s).

### P5. Every cloud turn sends the whole phone prompt
- **Cause:** one cloud mind with all 22 tools: about 3,800 input tokens a call. Three free keys allow about 45,000
  tokens a minute in all, so about 11 cloud calls a minute; a web question costs three.
- **Fix:** health, feelings and advice that name no phone action and no phone reading go to a **light cloud call**:
  no tools, short instructions per mode (Care keeps its medicine rules: anything about a dose still goes to the full
  cloud mind, which reads the schedule), the recent conversation, the time. Measured: 100 input tokens, about 2 s,
  a good answer. Same speed as the full call, about 38 times less of the per-minute budget, so the jury's free play
  does not run the keys dry.

### P6. Answers sound mechanical
- **Seen:** no follow-up questions; generic medical boilerplate; "Hi, I'm DUET. What can I do on your phone?" every
  time; the name never used.
- **Fix (instructions, both minds):**
  - In open conversation (feelings, plans, advice), end with one short, relevant follow-up question. Never after an
    action that was just confirmed.
  - Use the user's name now and then, not every turn.
  - A HEALTH section for Assistant mode, like Care's: no diagnosis or dose; what usually helps; when to see a doctor;
    one follow-up question ("Where exactly is the pain, and how long have you had it?"); offer to call someone.
  - The greeting uses the name: "Hi Tanmay, I'm DUET. What can I do for you?"

### P7. The fast mind does not know the conversation
- **Cause:** `self.fast.history = []` in `HybridThinker.run`: each request is judged on its words alone.
- **Fix:** it sees the last six exchanges as real chat turns. Measured tonight: as chat turns the prompt cache holds
  (0.24–0.44 s); put inside the system message instead, every turn re-reads the tool list (0.84–0.96 s). Acting on an
  old request stays impossible: `vet_plan` requires the *current* words to name the action and its last value, and
  the ledger never runs an action twice. The cloud mind gets the same recent exchanges in its note (today it only
  hears about the actions the fast mind did).

### P8. The S24 run was about 2 s slower to its first word than the emulator runs
- **Cause:** unknown (section 2). Naman's laptop ran the stack; its logs are not here.
- **Fix:** Phase 0: get its `agent.log`, `fast-server.log` and traces, or run tonight's stack on this laptop with
  the S24. A turn-stage script (from tonight's analysis) prints, per turn: transcript, route, first line, decision,
  answer. The demo preflight fails if the fast mind takes over 0.8 s or Whisper is on the CPU. One warm-up call to
  the fast mind at start-up fills its prompt cache (the first call was 1.2–2.4 s tonight).

### P9. The ears take about 1 s before any thinking starts
- **Cause:** 0.35 s of quiet for the VAD, then Whisper large-v3-turbo with beam search 5 (`speech_models.py`), then
  the end-of-turn delay (0.5 s minimum in phone mode).
- **Fix, only if measured safe:** greedy decoding (beam 1) in phone mode through a new `DUET_ASR_BEAM` setting (the
  benchmark keeps 5); end-of-turn minimum 0.35 s. A word-error check on the suite's voices decides. Streaming
  transcripts are out of scope for tomorrow.

### P10. The tests guard safety, not conversation
- **Cause:** the suite's `smalltalk` scenario checks only that no action ran (`do=[], only=True`): it passed while
  DUET said nothing.
- **Fix:** new scenarios with `say=` checks (the suite already supports them): greetings, thanks, "okay", feelings,
  goodbye, a health question with a follow-up, a web question with a correction, a barge-in. Each also checks that
  the first spoken line is not "One moment." alone and that the answer came within its budget. The fast-mind eval
  gets the small-talk cases, and its existing action cases must still show 0 wrong actions.

### P11. Small UI faults the jury will see
- "Connected · waiting for DUET…" never changes: the agent's `session_start` message is published before the phone
  is listening. Fix: send it again once the greeting has started.
- The bridge line and the answer merge into one bubble ("One moment. I'm sorry you're feeling even worse…"). Fix: the
  bridge gets its own bubble (the `talker` event already does this for "Sure.").

### Out of scope for tomorrow, say so if asked
- Hindi and Hinglish. The ChatGPT reference is in Hindi; DUET's recogniser is set to English. The demo stays in
  English.
- Streaming speech recognition, and the phone-native engine (Track B in `ROUND2_PLAN.md`).

## 5. Your idea: two API calls instead of a local fast mind

You asked: why run Gemma E2B on the laptop GPU at all; why not two API calls, a fast one and a slow one, with the
fast one given context so it answers better?

**The part of the idea that is right, and is in this plan:** the fast voice must know the conversation and must be
allowed to answer like a person (P1, P7). That, not where the model runs, is what makes DUET sound mechanical today.

**Where it runs, measured:**

| | First words after you stop | Extra cloud calls per chat turn | Free-tier budget | Works with the cloud cut | Verdict |
|---|---|---|---|---|---|
| **A. Local E2B that talks** (recommended) | **~1.4–1.6 s** (model 0.25–0.45 s, measured tonight) | 0 | not used | yes: chat and actions | Do it |
| B. Fast mind = Gemma 26B through the API | ~2.7–3.2 s (one call is 1.5–2.0 s p50, 2.7 s p90, on top of ~1 s of ears) | 1 more on every turn | each call ~3,800 tokens; 3 keys ≈ 11 calls a minute for everything | no | About twice as slow, and the jury's free play would hit the per-minute limit |
| C. Fast mind = Gemini Flash-Lite through the API | about 1–1.5 s (estimate; not measured here) | 1 more | needs a paid key (free tier: a few requests a minute) | no | Possible after the demo; a model switch tonight is a risk |

The local E2B is also the Round 2 story: it is the phone-sized Gemma that the plan moves onto the S24 itself.

**Where two cloud calls do help, and are in this plan:** not two models on every turn, but the *right single call*
for each kind of turn: one grounded search for web questions (P4), one light call for talk and health (P5), and
the full tool-using cloud mind only when the phone or the home is involved.

## 6. The new turn

```
the user's words ─► ears (unchanged) ─► FAST MIND, Gemma 4 E2B, with the last six exchanges (~0.3–0.6 s)
   ├─ small talk ─────────► chat_reply ─► small-talk check ─► spoken (~1.5 s)            no cloud call
   ├─ simple action ──────► as today: vet_plan ─► "Sure." ─► gate ─► phone ─► "Alarm set for 7."
   ├─ plain reading ──────► as today: template answer, no model
   ├─ cut off ("Call my") ► keep_listening ─► nothing said, or "Who should I call?"
   └─ hand over (ask_expert: reason, kind, say, question)
        bridge line spoken at once (its own words, cleaned; else a fixed line by kind)
        ├─ kind web ──────► web_search(question) through the toolbox ─► answer spoken    1 cloud call
        ├─ health / talk ─► light cloud call (no tools, recent conversation)              1 cloud call
        └─ phone / other ─► full cloud mind with tools, as today                          1–2 cloud calls
every action, whichever path: consent check ─► commit gate ─► exactly-once ledger ─► the phone ─► read back
```

Expected times from the last word, at the phone (estimates from the measurements above, to be confirmed in Phase 6):
small talk ~1.6–2.0 s; bridge ~1.8–2.2 s; health/advice answer ~4 s; web answer ~5.5–7 s; simple actions unchanged.

## 7. Implementation plan

Each phase ends with a check that must pass before the next starts. Times are working estimates. Every change goes
to `duet_voice/galaxy/` or behind a hook whose default is the benchmark's, so the Round 1 path stays as submitted.
No commits without your OK; no AI attribution in commits.

**Phase 0 · 15 min · Facts from tonight's run**
- Naman's laptop: `results/galaxy/agent.log`, `results/galaxy/fast-server.log`, `results/galaxy/traces/` (zip),
  and `nvidia-smi`. Or: run the stack on this laptop with the S24 and one short conversation.
- Decide which laptop runs the demo, and whether the S24 is with you tomorrow morning for the final checks.
- Add the turn-stage script (`scripts/turn_stages.py`) and run it on those traces.
- **Check:** we know where the extra ~2 s went, and the demo machine is fixed.

**Phase 1 · 2 h · The fast mind talks (P1, P7)**
- `galaxy/fast.py`: `chat_reply` tool; `ask_expert` gains `say`, `kind`, `question`; new instructions; the last
  six exchanges as chat turns; the small-talk check; a plain-text reply treated as a chat candidate.
- `galaxy/agent.py`: keep the recent exchanges (delivered user and DUET lines); pass them to both minds; warm-up call
  at start-up.
- `llm_local.py`: the escalate event carries the hand-over's arguments.
- Tests: the small-talk check, history building, warm-up.
- **Check:** `galaxy_fast_eval.py` on the old cases: **0 wrong actions, no new misses**; 20 small-talk cases answered
  by the fast mind, p50 ≤ 0.5 s model time; all unit tests pass.
- **Done 20 Oct, night (not committed):**
  - fast-mind eval, two rounds with the user's name: old cases 124 decisions, 0 wrong actions, the same 2 misses as
    before; small-talk set 66 decisions, 66 right, 0 wrong chats; a small-talk reply in 0.29 s p50 (max 0.47 s);
  - 227 unit tests pass (47 new, `tests/test_galaxy_smalltalk.py`);
  - spoken suite with the new `chat` scenario and a `no_bare_ack` check: 7 of 7 (chat, smalltalk, alarm, torch,
    cut_off, battery, web); 0 stale, 0 duplicates; every small-talk reply ready 0.8–1.0 s after the user stopped.
  - Found and fixed on the way: "Skip this song" came as `control_media(action="skip")` (now "next", and any value a
    tool does not take hands the request over); "Turn on the" came back as the text "keep_listening" (cut-off words
    are never small talk, and a reply that names a tool is never spoken); the model sometimes writes "Chat reply:"
    before its words (dropped).

**Phase 2 · 45 min · The cloud mind answers people (P1 part 2, P2, P6)**
- `galaxy/modes.py`: greetings are meant for DUET; narrow `<silent>`; follow-up questions; HEALTH section; the
  "fast voice may already have said a line" wording; greeting with the name.
- `galaxy/agent.py`: `silent_answers` closes the request unless the words are cut off or an action was held back.
- **Check:** the full prompt with "Good morning." answers instead of `<silent>`, 5 of 5; the "Call my" scenarios still
  ask "Who should I call?"; unit tests pass.
- **Done 21 Oct (Phase 1 committed as bb519fc):**
  - the real Assistant prompt (instructions, user context, 22 tools, turn note): "Good morning." answered 5 of 5
    ("Good morning, Tanmay. How can I help you today?"), against `<silent>` 3 of 3 before; background talk ("Rahul,
    did you take the car keys from the table?") still `<silent>` 2 of 2; health answers in two sentences and a
    follow-up question ("Where exactly is the pain, and since when have you been feeling it?"), no "I am an AI";
  - 230 unit tests pass (the old silent-reply test rewritten to the new rule: cut-off words, held-back and
    refused actions stay open, anything else closes);
  - nine full-prompt calls in a minute spent the three free keys' per-minute budget: Phase 4's light calls matter;
  - spoken suite 12 of 12 (cut_off, waver_call, dropped, smalltalk, chat, s24_smalltalk, question_only, battery,
    care, care_fall, drill, alarm), 0 stale, 0 duplicates; greetings with the name ("Hi Tanmay, I'm DUET. What can
    I do for you?");
  - found: a dropped request ("Set an alarm for 6 tomorrow morning. Actually, never mind, forget it.") answered "I've
    cancelled that." 3 of 3, instructions or not. Fixed in code (`confirm.drop_false_undo`): when nothing ran in the
    request, a sentence saying something was cancelled is dropped, and a dropped request is answered "Okay, I'll
    leave it." Re-run: dropped 2 of 2 "Okay, I'll leave it."; alarm_late keeps its real "Cancelled the 3:30 in the
    morning alarm."; home 2 of 2. 231 unit tests.
  - Seen, older than tonight, for Phase 3: after an action the cloud mind repeats the confirmation ("The air
    conditioner is now on. I've turned on the AC.").

**Phase 3 · 45 min · Bridge lines (P3)**
- `duet_voice/agent.py`: a `bridge` event spoken at once if nothing was said yet (no-op for the benchmark).
- `galaxy/fast.py`: bridge cleaning, fixed fallbacks by kind; nothing on `keep_listening`.
- `galaxy/agent.py`: the fallback lines in `FIXED_LINES`.
- **Check:** in a suite run, no cloud turn starts with the bare "One moment." unless the fast mind gave no usable line;
  the UI shows the bridge in its own bubble.
- **Done 21 Oct, with Phase 4 (one commit):**
  - bridge lines: "Let me check when the next Apple event is." (+1.3 s), "I'm sorry to hear you have a stomach
    pain." (+1.1 s), after a correction "Let me check when the latest iPhone was launched."; only lines that open as a
    bridge ("Let me…", "I'm sorry…", "Sure…") and promise nothing; "Sure." for an action; nothing for small talk;
    the cloud mind is told the line, and an opening apology is not said twice;
  - conversation controls: "Wait." says nothing; "Go on." finishes the cut answer (+0.67 s, no model); "What?" says
    it again; found on the way: a reply that says nothing never closed its request ("Wait. Go on." was one request);
  - a sentence that only restates a confirmed action is dropped ("The air conditioner is now on." is no longer
    followed by "I've turned on the AC.");
  - alarm readings say their time as people do ("4:30 in the morning", whatever the phone sends).

**Phase 4 · 1.5 h · One cloud call where one is enough (P4, P5)**
- `galaxy/fast.py`: the web route (search through the toolbox, answer spoken, failure falls back to the cloud mind);
  the light cloud call for health and talk, with a deterministic override (the words name a phone reading or an
  action, or Care's medicines: full cloud mind).
- Optional if time allows: battery questions pre-read the battery report into the cloud's note, saving one call.
- **Check:** web p50 ≤ 7 s and health p50 ≤ 4.5 s on this laptop; the Samsung → iPhone correction answers for the
  iPhone; the cloud-cut drill still says "I can't reach my cloud mind" at once.
- **Done 21 Oct:** web questions in one grounded search: the Apple event 5.6 s (10.5 s in Phase 3, 15.6 s on the
  S24), the iPhone correction 5.3 s, the T20 question 5.5 s; health in one call with no tools: 3.8 s; the weather,
  the phone, the home and Care keep the full cloud mind (the Care fall still alerts the family). Spoken suite 15 of
  15 (controls twice, s24_apple, s24_correction, web, s24_smalltalk, care_fall, care, weather, question_only, drill,
  battery, alarm, cut_off, chat), 0 stale, 0 duplicates, 0 model errors; 259 unit tests. Battery pre-read and
  sentence-by-sentence streaming not built (the one-call routes took most of the wait).

**Phase 5 · 30 min · Ears and UI (P9, P11)**
- `DUET_ASR_BEAM` (phone 1, benchmark 5) and end-of-turn 0.35 s, each kept only if the suite's word check holds.
- `session_start` sent again; bridge bubble.
- **Check:** the top bar shows the brain; first-word time down by the measured amount, no new mishearings.
- **Done 21 Oct:**
  - **Backchannels:** "Mm-hm, okay." said over an answer cut DUET off 2 of 2 (LiveKit's VAD mode). LiveKit's
    adaptive interruption (LiveKit Cloud inference, works with our keys) did not tell the test voice's "Mm-hm" from an
    interruption either, but it stops DUET faster: **0.21 s p50 after the speech began, against 0.40 s** (5 against 48
    overlaps). It is now the default (`DUET_INTERRUPT_MODE=vad` turns it off; it falls back to VAD by itself). And a
    backchannel over an answer ("right, I see", "mm-hmm", "okay") now lets the answer go on from the sentence it was
    cut in: 2 of 2 in the spoken suite.
  - **Greedy Whisper: measured, not changed.** On the S24 videos' real speech, beam 1 saves about 0.03 s a segment
    (0.27 s against 0.30 s) and the user's words came out the same. Not worth the risk; the ears' wait is the
    speech detector and the end of turn. The 0.35 s end-of-turn minimum was left at 0.5 s for the same reason (a
    shorter wait answers people in their pauses).
  - **The top bar:** on the S24, DUET's `session_start` came while the app was still setting up the microphone and
    audio route, and the app then wrote "Connected · waiting for DUET…" over it for good. Fixed in `app/www/app.js`;
    the web app loads with no console errors; a new APK is built (`app/android/app/build/outputs/apk/debug/app-debug.apk`,
    copied to `E:\duet_local\DUET-galaxy-debug.apk`). **Not yet installed on the S24**: `python scripts/phone_check.py`.

**Phase 6 · 1.5–2 h · Verify, on this laptop, then on the S24**
- Unit tests; `galaxy_fast_eval.py --repeat 3`; `galaxy_suite.py` with the new conversation scenarios;
  `verify_all.py --device s24 --no-suite` for the phone paths.
- A 30-turn spoken conversation on the S24 (small talk, a web question with a correction, health with a barge-in, an
  alarm correction, the torch, SmartThings), filmed like tonight's videos and timed the same way, next to the
  ChatGPT video.
- **Check:** gates C1–C8.

**Phase 7 · 30 min · Demo kit**
- `ROUND2_DEMO.md`: the script opens with a short natural exchange ("Good morning, DUET" → it greets you by name and
  asks back) before the correction hook; Q&A answer 6 ("Why is 'One moment.' a fixed line?") becomes "the fast mind
  talks; it never says a value an action could use and never claims a result"; the numbers from Phase 6.

**Cut line.** If time runs short, Phases 1–3 alone fix what the jury notices first (silence, "One moment.", no
small talk); Phase 4's web route is the next most visible. Phases 5 and the battery pre-read can wait.

## 8. Risks

| Risk | Guard |
|---|---|
| The small model says something wrong in small talk | It speaks only after the small-talk check (no facts, numbers, claims, health); everything else goes to the cloud |
| New instructions change how the fast mind routes actions | The fast-mind eval's old cases must show 0 wrong actions and no new misses before anything else moves |
| The fast mind acts on an earlier request it now sees in the history | `vet_plan` checks the current words only; the ledger never repeats an action |
| A bridge line names a topic the user then corrects | Nothing ran; the corrected turn gets its own line. Lines for actions stay "Sure." |
| A web answer is wrong or empty | The search says when it found nothing; a failed search falls back to the cloud mind |
| Free-tier quota during free play | Fewer calls per turn (chat 0, web and health 1); the key pool still waits for room ("Still working on it.") |
| Overnight changes break the demo | Branch tag before Phase 1; every phase has its check; `verify_all.py` before the demo; tonight's build stays one checkout away |

## 9. Naman's issues and recommendations

To add when you send them. Each becomes a row in section 4 with its cause and fix, and joins the phase it fits.

## 10. What I need from you

1. **Naman's issues and recommendations** (you said you would send them).
2. **From the laptop that ran tonight:** `results/galaxy/agent.log`, `results/galaxy/fast-server.log`, and
   `results/galaxy/traces/` (zipped), plus which GPU it has. Or tell me the demo runs on this laptop.
3. **The demo time tomorrow**, and whether the S24 is with you tonight or tomorrow morning for Phase 6.
4. **Your go-ahead** to start Phase 1.

## 11. Compared with ChatGPT Live: what is missing

Written 21 Oct after Phases 1 and 2, from the code, LiveKit 1.8.3's source, 996 emulator requests, 78 measured
barge-ins, the suite runs and your ChatGPT Live video.

### The verdict

**The architecture is not what made DUET feel mechanical.** DUET is already full duplex where it counts:
- the audio runs both ways at once over WebRTC;
- DUET listens while it speaks and while it thinks;
- talking over it stops it: 78 of 78 overlaps in the traces, **0.40 s p50 (0.45 s p90) after the speech detector
  heard the user**;
- it knows how much of a cut-off answer the user heard and answers from there (ChatGPT does the same, with
  "truncation");
- a correction made while it is thinking voids the old plan, and the new turn re-reads everything.

What made it feel mechanical were **behaviour rules**: the fast mind was forbidden to talk, the cloud mind was told
not to greet, a fixed "One moment." covered every cloud turn before anything was decided, and web questions took
three cloud calls in a row. Phases 1 and 2 fixed the first two; Phases 3 and 4 fix the rest.

**There are real architectural differences**, and they are what remains between DUET and ChatGPT Live. Ranked by how
much a user feels them:

| # | | ChatGPT Live | DUET today | Kind of gap | What closes it |
|---|---|---|---|---|---|
| A1 | **Hearing** | One model hears the audio itself, while you speak | A cascade: the speech detector waits 0.35 s of quiet, then Whisper transcribes the whole segment (no words until then), then the end-of-turn rule waits at least 0.5 s. About **1 s after your last word before any thinking starts** | Architecture: no streaming recogniser | Today: greedy Whisper and a 0.35 s end-of-turn minimum, if the word checks hold (Phase 5). After: a streaming recogniser with partial words (sherpa-onnx streaming Zipformer, on the laptop or the phone) |
| A2 | **Cloud answers** | One model, audio streamed out within about half a second | Gemma 4 26B on the free tier, 1.5–2 s a call, the whole answer before the first word; a web question was three calls (10–11 s) | Design (fixable) plus compute | Phase 4: one call per turn; then stream the cloud answer sentence by sentence; a paid, low-latency tier after the demo |
| A3 | **Barge-in judgement** | Tells a real interruption from "mm-hm" | Any sound over 0.5 s stops DUET: a cough or "mm-hm" cuts it off, and it resumes 2 s later only if no words came. LiveKit 1.8.3 has an **adaptive** (ML) interruption mode with backchannel detection, but turns it off in production mode: the agent log says "adaptive interruption is disabled by default in production mode" | Configuration | Turn on adaptive interruption (it runs on LiveKit Cloud's inference with our project's keys) and measure; else a 0.3 s minimum with Galaxy Buds |
| A4 | **Conversation control** | "Wait" or "hold on": it stops and waits. "Go on": it carries on. "What?": it says it again | Nothing special for any of these: "hold on" can get a reply; "go on" after a cut-off answer does not continue it, though the cut-off text is known | Missing behaviour | Deterministic controls: wait (stop, say nothing), go on (finish the cut-off answer from where the user stopped hearing it), repeat (say the last answer again) |
| A5 | **Voice and language** | Expressive, and in your video it spoke Hindi with you | Kokoro, one English voice; Whisper forced to English | Model choice | After the demo: language detection plus an Indian-English or Hindi voice. For the demo: English |
| A6 | **Where it runs** | Phone to OpenAI | Phone → LiveKit Cloud → laptop GPU → Google's free tier: two network hops and three machines | Deployment | Track B (the engine on the phone, Gemma E2B on-device) and a paid cloud key, after the demo |
| A7 | **Measured at the phone** | — | Every number is the agent's own clock; nothing measures when the phone actually plays the first word. The S24 was about 2 s slower than the emulator, cause still unknown | Missing measurement | Naman's logs (section 10); a phone-side timestamp on the first audio frame |

**Where DUET is ahead of ChatGPT Live**, and should say so to the jury:
- **Corrections:** it never acts on words you took back. On Full-Duplex-Bench v3 the best published system
  (GPT-Realtime) has Pass@1 0.60; DUET's Round 1 run had 0.81 (judged by Gemma 4 in place of gpt-4o, as disclosed).
- **Actions:** it never acts twice, and never claims what the phone did not confirm.
- **The phone:** it really acts on it (alarms, calls, Maps, SmartThings), and speaks first with offers.

### What this changes in the plan

- **Phase 3:** the bridge lines; also the cloud mind's repeated confirmations ("The air conditioner is now on. I've
  turned on the AC."), and the conversation controls of A4.
- **Phase 4:** one cloud call where one is enough; then sentence-by-sentence streaming of the cloud answer (A2).
- **Phase 5:** greedy Whisper and the shorter end-of-turn wait (A1); adaptive interruption tested (A3).
- **After the demo, the roadmap to say to the jury:** streaming recogniser (A1), the engine on the phone (A6), Hindi
  and Hinglish (A5).
