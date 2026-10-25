# Round 2: the deck and the demo film

What Team SE7EN presents in Round 2 of the Samsung PRISM GenAI Hackathon 3.0 (Theme 05).

| File | What it is |
|---|---|
| [`deck/DUET_Round2_Deck.html`](deck/DUET_Round2_Deck.html) | The presentation: 8 animated slides and an appendix (A1–A6). Its last slide plays the demo film. |
| [`deck/DUET_Round2_Deck.pdf`](deck/DUET_Round2_Deck.pdf) | The same deck as a PDF, every slide at its last step (15 pages). |
| [`deck/duet_demo.mp4`](deck/duet_demo.mp4) | The live demo film, 2:57.6, 1080p: five scenarios on a Galaxy S24, from three views kept in sync. |
| [`DEMO_FILM_NOTES.md`](DEMO_FILM_NOTES.md) | The film's running order, every fast-forward with its real length, and how it was edited. |

## Presenting

1. Open `deck/DUET_Round2_Deck.html` in Chrome. Keep `duet_demo.mp4` and `duet_demo_poster.jpg` in the same
   folder, because the last slide plays the film from there.
2. **F** gives full screen. **→** or a click moves to the next build, and **←** moves back.
3. **N** shows the speaker notes, with a timer for each slide.
4. On the film slide, the film starts with sound. **Space** pauses it, and **→** moves on to the appendix.

The slides:
1. Title
2. The problem
3. One conversation through DUET
4. The coordinator
5. Semantics and acoustics
6. On a Galaxy today
7. Vision and roadmap
8. The demo film

The appendix covers latency, the fast mind, tone, testing, the benchmark and the landscape.

## The film

The film shows five scenarios: navigation, a SmartThings home, a text and an alarm, web search, and battery and
torch.
- **Views:** the S24's own screen recording sits in the centre, with a front camera and a top-down camera kept in
  sync by their audio.
- **Labels:** every response shows which mind answered and its latency from DUET's logs.
- **Fast-forwards:** every one is labelled with its real length.
- **No zooms:** every view stays at its place and size the whole time.

The scripts that edited it are in [`tools/demo_video`](../../tools/demo_video).
