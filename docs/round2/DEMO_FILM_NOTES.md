# DUET live demo film, second cut: edit notes

**File:** [`deck/duet_demo.mp4`](deck/duet_demo.mp4) · 1920×1080 · 30 fps · H.264 + AAC · **2:57.6**

## What changed from the first cut
- **Look:** a clean white, Apple-like design.
  - Soft white background; the S24 in a graphite phone frame; soft shadows.
  - White rounded chips and cards; dark rounded caption pills with yellow text.
  - Dips to **white** between scenarios.
  - The Team SE7EN watermark is now faint grey.
- **No zooms anywhere.** Every view stays at its place and size, all the time.
- **Natural fast-forwards:**
  - About 0.45 s of real time is kept after you stop speaking and before DUET speaks, measured on the actual
    sound, not on transcript times.
  - The speed **ramps** smoothly from 1× up and back down, instead of jumping.
  - Each one is still labelled with its real length.
- **Scenario 2:** one fast-forward where there were two, so DUET's "Sure." plays at its real time. It also ends on
  "now unlocked" (the extra "thank you" is cut).
- **Scenario 3:** after DUET's answer, one continuous, gentle 2× stretch (the Clock, and the text arriving on the
  other phone). No breaks, no zooms.
- **Order:** web search is now scenario 4, and battery & torch scenario 5.
- **Web search:** a "What DUET is doing" card explains every step, tagged YOU / FAST MIND / CLOUD MIND.
- **Captions** start on the actual sound. Whisper had put some of DUET's lines 0.2–0.5 s early.
- **A correction to the first cut's notes:** the dark stretch in scenario 2 is not a hidden secure screen. Naman
  taps the air conditioner tile: the phone goes dark while its page opens, shows the AC's own page (On, Cool, 24 °C),
  then goes back. It plays as it happened, labelled with a chip.

## Running order

| Timecode | Scenario |
|---|---|
| 0:00 | Title, over DUET's greeting |
| 0:00 – 0:47.7 | **1 · Navigation:** live GPS location; railway station → airport; car → walking |
| 0:47.7 – 1:16.8 | **2 · SmartThings home:** light and AC on, confirmed by SmartThings; the AC's page; front door unlocked by the fast mind |
| 1:16.8 – 1:40.4 | **3 · Messages & alarms:** one sentence, two actions, 8:50 never ran; the text on the other phone; 8:30 PM in Clock |
| 1:40.4 – 2:09.0 | **4 · Web search:** the question changes mid-answer and the old answer is dropped; one grounded search; every step explained |
| 2:09.0 – 2:54.6 | **5 · Battery & torch:** the cloud mind reads the phone and answers; you talk over it; 20% held at the gate and voided; 55% and the torch on the fast mind |
| 2:54.6 – 2:57.6 | Closing card |

## Fast-forwards

| Timecode | Real time | On screen | Peak speed |
|---|---|---|---|
| 0:21.4 | 2.8 s | 1.0 s | 3.5× |
| 0:36.5 | 2.8 s | 1.0 s | 3.5× |
| 1:00.5 | 7.2 s | 1.5 s | 6× |
| 1:11.8 | 3.6 s | 1.1 s | 4× |
| 1:36.9 | 6.4 s | 3.5 s | 2× (scenario 3: the phones being checked) |
| 1:44.1 | 3.3 s | 1.2 s | 3.5× |
| 1:50.9 | 4.5 s | 1.3 s | 4× |
| 1:55.7 | 8.9 s | 1.7 s | 6× |
| 2:15.3 | 2.9 s | 1.1 s | 3.5× |
| 2:17.8 | 4.5 s | 1.4 s | 4× |
| 2:46.5 | 3.7 s | 1.2 s | 4× |
| 2:52.1 | 2.9 s | 1.0 s | 3.5× |

Every fast-forward eases in and out. Speech is never sped up: each one sits in silence, apart from DUET's short
"Still working on it." in the web search's longest wait.

## Minds and latencies on screen
All from DUET's logs, in seconds after you stopped speaking. They're unchanged from the first cut: see that cut's notes.

## Rebuilding
The edit lives in [`tools/demo_video/edl2.py`](../../tools/demo_video/edl2.py) and
[`render2.py`](../../tools/demo_video/render2.py) renders it (about 8 minutes on the laptop). See
[`tools/demo_video/README.md`](../../tools/demo_video/README.md) for the folder layout it expects.
