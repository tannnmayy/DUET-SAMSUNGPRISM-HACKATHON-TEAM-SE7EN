# DUET: submission video script

**Length:** 4:45 (limit 5:00) · **Cast:** Tanmay (host, live use cases), Pranjal and Naman
(the benchmark, the harness and the results) · **Format:** 1920×1080, 30 fps

Every file named below is in the repository or in `results/galaxy/video/`.

## At a glance

| # | Time | Who | Segment | On screen |
|---|---|---|---|---|
| 1 | 0:00-0:30 | (no one) | Opening | `intro.mp4`, ready to use |
| 2 | 0:30-0:42 | All three | Who we are | Camera, name titles |
| 3 | 0:42-1:05 | Naman | The benchmark | Benchmark card, recording waveform |
| 4 | 1:05-1:35 | Pranjal | How DUET works | Diagrams: system, one turn |
| 5 | 1:35-1:58 | Naman | How we worked, and the harness | Pipeline diagram, terminal |
| 6 | 1:58-2:18 | Pranjal | Results | `results_card.png` |
| 7 | 2:18-2:30 | Tanmay | From benchmark to phone | Film opening card |
| 8 | 2:30-4:15 | Tanmay | Four live use cases | The four scene clips |
| 9 | 4:15-4:45 | Tanmay, then all | Close | Film closing card, repository |

Speaking pace: about 150 words a minute. Each spoken block below fits its slot at that pace.

---

## 1 · Opening (0:00-0:30)

**On screen:** `results/galaxy/video/intro.mp4`, 30 seconds, with its own sound. No one
speaks over it; add a quiet music bed if you like.

What it shows:
- 0-9 s: "Book me a flight to Rome... no wait, Milan." A typical agent books Rome; DUET holds, then books Milan.
- 9-25 s: "People pause, hesitate and correct themselves mid-sentence." / "Voice agents act before the correction arrives." / "The best published system on Full-Duplex-Bench v3 passes 60%." / "So we built an agent that waits for what you mean: 81%."
- 25-30 s: the DUET title.

## 2 · Who we are (0:30-0:42) · all three

**On screen:** the three of you, or three quick shots; name titles:
"Tanmay Singh", "Pranjal", "Naman", "Team SE7EN · SRM".

> **Tanmay:** Hi, we're Team SE7EN from SRM. I'm Tanmay.
>
> **Pranjal:** I'm Pranjal.
>
> **Naman:** And I'm Naman. This is DUET.

## 3 · The benchmark (0:42-1:05) · Naman

**On screen:** a title card "Full-Duplex-Bench v3: 100 real recordings · 12 tools · strict
Pass@1", then a waveform of a real request with its pauses.

> **Naman:** Full-Duplex-Bench v3 plays a hundred real recordings to a voice agent: people
> booking flights, paying bills, renting apartments and tracking orders, and hesitating,
> pausing and correcting themselves all the way. The agent has twelve tools, and the score
> is strict: one wrong or extra tool call, and the whole request fails.

## 4 · How DUET works (1:05-1:35) · Pranjal

**On screen:** `docs/diagrams/README-1.png` (the system), then `docs/diagrams/README-2.png`
(one turn with a correction). Highlight each box as it is named.

> **Pranjal:** So we split the agent into two minds and a referee. A fast voice answers the
> moment you finish, so there's never dead air. A thinker, Google's Gemma 4 26B, reads
> everything you've said since its last answer and plans the tool calls. And every call goes
> through the coordinator: nothing runs while you're still talking, a plan your correction
> overtook is dropped, and no action ever runs twice.

## 5 · How we worked, and the harness (1:35-1:58) · Naman

**On screen:** `docs/diagrams/README-5.png` (the reproduction pipeline); then a terminal
running `bash reproduce.sh --force`; then the run folder `results/reported/gemma4_final_run`
with its reports, traces and logs.

> **Naman:** We started by reading the benchmark's own scorer, measured our listening with no
> model at all, and iterated on all hundred requests before every live run. And it's one
> command: reproduce.sh installs everything, streams all hundred recordings in real time
> through the official runner, and scores them with the official scripts, with every
> conversation traced and logged.

## 6 · Results (1:58-2:18) · Pranjal

**On screen:** `results/galaxy/video/results_card.png`.

> **Pranjal:** On all hundred recordings, DUET passes eighty-one percent, against sixty for
> the best published system. It answered every conversation and never interrupted once.
> What's left is mostly spelled-out codes the recogniser mishears, and items whose expected
> answer contradicts the benchmark's own data.

## 7 · From benchmark to phone (2:18-2:30) · Tanmay

**On screen:** `results/galaxy/video/card_intro.mp4` (the film's opening card).

> **Tanmay:** A benchmark is one thing. Here is the same agent, the same coordinator and the
> same Gemma model on a Samsung Galaxy: DUET for Galaxy, in three modes.

## 8 · Four live use cases (2:30-4:15) · Tanmay

**On screen:** the four scene clips from `results/galaxy/video/`, cut to about 25 seconds
each (cut only the waiting between turns, and mark the cuts "trimmed for time"). The user's
voice in the clips is scripted; the agent, the model and every action are live. Talk over
the moments listed.

**Assistant: changing your mind (2:30-2:55)** · `correction.mp4`

> **Tanmay:** In Assistant mode, I ask for a 3:30 alarm, pause, and change my mind. Watch the
> timeline: the plan for 3:30 is dropped before it can act. Only 4:30 is set, exactly once.

**Assistant: troubleshooting you can interrupt (2:55-3:25)** · `battery.mp4`

> **Tanmay:** Next, a real problem: my battery drains fast. DUET reads the phone's battery,
> screen and app data. I cut in with my own theory; it stops, checks it against the numbers,
> and changes the brightness only when I ask.

**Care: never a double dose (3:25-3:50)** · `care.mp4`

> **Tanmay:** Care mode is for someone living alone. Ramesh can't remember his blood-pressure
> tablet. DUET checks the record, sees he took it this morning, and gently stops a second
> dose. Then it calls his daughter.

**Drive: route changes and the home from the car (3:50-4:15)** · `drive.mp4`

> **Tanmay:** And on the road, I change my destination mid-sentence and only home is set. The
> text to Priya carries the real arrival time, and ten minutes out, DUET speaks first and
> offers to cool the house.

## 9 · Close (4:15-4:45) · Tanmay, then all three

**On screen:** `results/galaxy/video/card_outro.mp4`, then the repository address.

> **Tanmay:** Interrupt it, correct it, change your mind: DUET acts on what you mean. It
> reproduces with one command, runs on Gemma 4, and every run is fully logged.
>
> **All three:** Thank you!

---

## Production notes

- **Assets:**
  - `results/galaxy/video/intro.mp4`: the opening
  - `results/galaxy/video/results_card.png`: the results
  - `docs/diagrams/*.png`: every architecture diagram
  - `results/galaxy/video/<scene>.mp4`: the four live scenes
  - `card_intro.mp4` and `card_outro.mp4`: the film's cards
  - `results/galaxy/video/DUET_for_Galaxy_demo.mp4`: the full, uncut film
- **Honesty on screen:** say or show once that the demo runs the app's web build, with
  phone actions simulated in the browser; on a Galaxy the same app does them through Android.
  Mark any cut inside a scene "trimmed for time".
- **Recording voices:** a quiet room, the microphone 15-20 cm away, one take per block.
  Record the voice-over first and cut the pictures to it.
- **Captions:** burn in subtitles for all spoken lines; judges often watch muted.
- **Export:** H.264, 1080p, 30 fps, under 5:00.
