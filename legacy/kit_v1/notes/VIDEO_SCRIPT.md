# Demo video script (<= 5:00)

FAQ Q17 requires a demo video of at most five minutes. This script uses only
things that exist and run in this repository. Every on-screen number comes
from a command shown on screen or listed in README.md.

**Setup before recording**
- Terminal at the repo root, large font; browser ready for the timeline pages.
- Pre-generate the timelines (they run the real agent at official speed):
  ```bash
  python tools/timeline.py scenarios/pub_02_text_interrupt.json
  python tools/timeline.py tests/conformance/conf_23_correction_inside_commit_hold.json
  python tools/timeline.py tests/conformance/conf_21_retract_during_booking.json
  python tools/timeline.py scenarios/pub_05_audio_asr_ambiguity.json
  ```
  Output lands in `generated/timeline_<scenario>.html`.

---

## 0:00-0:30 The problem, shown not told

**Screen:** title card "DUET - making voice agents safe to interrupt", then
`scenarios/pub_02_text_interrupt.json` open, events highlighted.

**Say:** "Full-duplex speech is solved - assistants can hear you talk over them.
Full-duplex *action* isn't. Ask one to book a flight to Boston, then say
'wait, New York', and the Boston search is still running. Its result can still
be spoken, and a booking can still be made. Samsung's harness measures exactly
this: stale work, state after the interruption, latency and double-commits."

## 0:30-1:30 An interruption, live

**Screen:** run it in the terminal:
```bash
python run_local.py --scenario scenarios/pub_02_text_interrupt.json --agent agent.agent:ParticipantAgent
```
Then open `generated/timeline_pub_02_text_interrupt.html`.

**Say:** "The user finishes the turn at 800 milliseconds, and DUET is speaking 30
milliseconds later - naming what it's doing, not 'one moment'. At 1.9 seconds
the user barges in. Look at the tools lane: the Boston search is *cut* - the
dashed bar is cancelled the instant the interruption lands. The acknowledgement
names the correction, 'Boston to New York', and it carries the new state, so
the scorer sees the right destination 30 milliseconds after the interruption.
The New York search runs and the answer is grounded in its result. 100 out of
100."

## 1:30-2:30 What M1 does underneath: epochs

**Screen:** `generated/timeline_conf_23_correction_inside_commit_hold.html`.

**Say:** "The hard case is when the work in flight is irreversible. Here the
agent is booking the 8 AM flight for Alice. The user says 'make it the 2 PM',
and then, 150 milliseconds later, 'and it's for Priya, not Alice'. Every
computation in DUET carries the *epoch* it was conceived under, and an
interruption starts a new epoch. The 8 AM booking belongs to epoch zero, so it's
cancelled. The 2 PM booking for Alice was being held for 250 milliseconds,
because irreversible work waits a moment after a correction. The second
correction lands inside that hold, so it's dropped before it's ever sent. One
booking goes out: 2 PM, for Priya. Nothing was double-booked, and nothing stale
was committed. And note what it heard: 'not Alice' is a *rejected* value, so
Alice never enters the state."

## 2:30-3:15 Schema-driven tools and honesty

**Screen:** `tests/conformance/conf_18_unseen_commit.json` (the tool manifest),
then run `python tools/quality.py --show --scenarios tests/conformance/conf_18_unseen_commit.json`.

**Say:** "About ten hidden tools arrive only as a schema at the start of a
scenario. This one - reserve_table - DUET has never seen. It picks the tool from
the schema, binds 'Casa Lume' as the restaurant and 'four' as the party size,
and treats it as irreversible because the schema says so. It only reports
success after the tool confirms it. And when a booking *times out*, it says it
doesn't know whether it went through - it never retries a booking blindly."

## 3:15-4:15 Hearing and seeing

**Screen:** `generated/timeline_pub_05_audio_asr_ambiguity.html`, then
`scenarios/pub_07_visual_port_lookup.json` with the frame image.

**Say:** "Audio arrives as raw recordings. DUET judges confidence per *word*,
not per sentence. Here it heard 'Austin' at 47% - so it asks, by name: 'Sorry,
did you say Austin?' - and searches Boston when the user answers.

For vision, we're honest about a limit. We measured a vision-language model on
this photo: it called the HDMI port 'USB-C' three times out of four, and
invented a label to match. A wrong answer here fails two checks instead of one,
so DUET says it can't tell which port that is, points to the candidate pages,
and asks. We ship what we measured."

## 4:15-5:00 Results and thesis

**Screen:** README.md "Results" table; `python tools/killswitch.py` output.

**Say:** "97.4 weighted on the official evaluator, against about 57 for the
reference agent. Beyond the public set, we test on 23 scenarios the kit doesn't
cover, and each interruption scenario is proven to fail an agent that doesn't
cancel. We also run randomized scenarios, and lint every transcript, because
the score can't hear the agent. With every model switched off it still averages
89 and never goes silent. Full-duplex speech is solved. DUET makes full-duplex
*action* safe."

---

**Recording checklist**
- [ ] 1080p, <= 5:00, spoken audio clear.
- [ ] The link to the video is in README.md, in the tagged commit (FAQ Q17-Q20).
- [ ] Numbers on screen match README.md at the tagged commit.
