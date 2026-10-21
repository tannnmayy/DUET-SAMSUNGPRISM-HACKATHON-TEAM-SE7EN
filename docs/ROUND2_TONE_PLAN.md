# DUET hears how you say it: semantics and acoustics together

**Researched, measured and built on 21 Oct 2026** (demo day), on branch `round2-tone`. The code is
`duet_voice/galaxy/tone.py` and the hooks listed in section 6. `DUET_TONE=0` turns it off. The benchmark agent
(`duet_voice/agent.py`) never imports it, and its coordinator has no extra hold.

- [1. What it does](#1-what-it-does)
- [2. Semantics and acoustics together: the rules](#2-semantics-and-acoustics-together-the-rules)
- [3. Who already does it](#3-who-already-does-it)
- [4. Is it novel?](#4-is-it-novel)
- [5. What we measured before building it](#5-what-we-measured-before-building-it)
- [6. The design as built](#6-the-design-as-built)
- [7. How it was tested](#7-how-it-was-tested)
- [8. Limits, and the impact, honestly](#8-limits-and-the-impact-honestly)
- [9. Next](#9-next)
- [Sources](#sources)

## 1. What it does

Before this branch DUET understood *what* you said and nothing about *how*: its only acoustic signals decided
*whether* and *when* someone was talking (Silero VAD, LiveKit's end-of-turn model, LiveKit's adaptive interruption,
Whisper's no-speech probability). Now every speech segment is also measured for vocal effort, emotion and timing,
and read together with the words.

| You say | Without tone | With tone | Heard from |
|---|---|---|---|
| "Set an alarm for 4:30, I think." | Sets 4:30 | "4:30 in the afternoon, right?" Then "yes" sets it (no model call); "no, 5:30" sets 5:30; "no" sets nothing | the hedge next to the value |
| "Set an alarm for… (long pause) 4:30." | Sets 4:30 | Asks the same | the pause in the middle of the phrase (the voice's timing) |
| "Set an alarm for 4:30." | Sets 4:30 | Sets 4:30: no needless question | |
| **"No, no, no!"** shouted over a long answer | Stops its voice, then answers "no" as a request | Stops, and stays quiet | a raised voice **and** a bare "no" |
| "No." said calmly | An answer, or the start of a correction | The same | |
| **"Okay, okay!"** shouted over an answer | Treated as "mm-hm": the answer carries on | Stays stopped | a raised voice over DUET |
| "Right, I see." said calmly over an answer | The answer carries on | The same | |
| "It's *still* not working!" (annoyed) | An ordinary request | Both minds are told the user sounds frustrated: a few words of acknowledgement, then help, no cheerfulness | a raised or negative voice **and** words of trouble |
| "I have a stomach pain." (in a low, sad voice) | A correct, slightly formal answer | The cloud mind answers (never the fast mind's small talk), told to be gentle and short and to ask how they are; Kokoro speaks 10% slower | the emotion model **and** distress words |
| "Good morning!" (excited) | Answered | Answered: a raised voice alone changes nothing but the timeline | |

Every reading is shown live on the phone's timeline ("Heard in your voice: unsure of the value · "I think"") and
kept in the run log with its numbers (`tone_segment`, `tone`, `tone_ask` events).

**The rule that keeps it safe** (rule 5 of `ROUND2_PLAN.md`): tone is advisory. It may hold an action longer, ask
before one, change how DUET speaks, or stop DUET's own voice. **It never runs an action and never cancels one on
its own**; an action still needs words.

## 2. Semantics and acoustics together: the rules

Each label needs a cue from the voice **and** a cue from the words (or, for unsure, the words alone or the voice's
timing), each measured against the speaker's own earlier segments in the conversation (their baseline):

| Label | The voice (acoustics) | The words (semantics) | Effect |
|---|---|---|---|
| **urgent** | a raised voice: two of brighter (+6 dB), higher (+4 semitones), louder (+6 dB) than the speaker's usual, or louder by 8 dB alone | "no", "stop", "wait", "don't", "cancel", "enough" | a bare shouted "no" is a stop; both minds keep the reply very short |
| **distressed** | emotion model: angry + fearful + sad ≥ 0.6, or ≥ 0.4 with a raised voice | pain, hurt, fell, scared, sick, dizzy, "not okay", "please help"… | the cloud mind answers, gently; DUET speaks 10% slower |
| **frustrated** | a raised voice, or the emotion model as above | "still not working", "doesn't work", "useless", "come on"… | both minds: acknowledge in a few words, then help |
| **unsure** | a pause of 1 s or more in the middle of the phrase, or 0.6 s after a filler, before the value; optionally (`DUET_TONE_PITCH=1`) a terminal pitch rise of 4 semitones over the speaker's usual ending | a hedge within three words of the value: "I think", "maybe", "probably", "around", "or so", "-ish" | the phone asks the value back before it runs; the gate's hold is the revising hold |
| **raised voice** | as for urgent | none of the above | shown on the timeline only (excitement sounds the same) |

Two more uses of the voice, for the coordinator:
- After a raised-voice segment whose words have not arrived yet, every pending action waits for 4 s of quiet, so a
  shouted "no" is always *heard* before anything runs (and the words then decide).
- While the user sounds unsure of a value, the gate's hold is the revising hold (1.8 s): the time to correct it.

## 3. Who already does it

| System | Open? | What it reads in the voice | What it does with it |
|---|---|---|---|
| **Hume EVI** (EVI 3, EVI 4-mini) | No (API) | A prosody model: tune, rhythm, timbre; many vocal expressions | End of turn from tone; replies in a matching tone |
| **ChatGPT voice / GPT-Realtime** | No | Hears the audio itself (speech-to-speech) | Tone shapes the reply implicitly; its semantic VAD waits longer after "ummm..." |
| **Gemini Live API, native audio** | No | "Affective dialog": the user's tone and emotional expression | Replies according to it (`enable_affective_dialog`) |
| **Kyutai Moshi / Unmute** | Yes | Moshi hears audio natively; Unmute's streaming recogniser has a semantic VAD | Turn-taking; replies |
| **LiveKit turn detector and adaptive interruption** (we use both) | Model weights local / cloud | Audio cues of a finished turn; backchannel vs interruption | Turn-taking only |
| **Pipecat Smart Turn v3.2** | Yes (BSD, 8 M parameters, ONNX) | Raw audio: prosody, pace, intonation | Turn-taking only |
| **SenseVoice-Small** (FunAudioLLM) | Yes | Speech emotion and sound events next to the words | A tagger (on English we measured it useless, section 5) |
| **emotion2vec+** and its distillations | Yes | Speech emotion (9 classes) | A tagger: what you do with it is up to you |
| **OSUM-EChat**, **ParaS2S** (ICLR 2026), Step-Audio 2, Qwen-Omni | Yes (research) | Paralinguistics: emotion, sarcasm, age, gender | Empathetic replies; ParaS2S finds most speech models still fail to adapt to how users speak |
| **Recognizing uncertainty in speech** (Pon-Barry and Shieber, Harvard) | Research | Prosody around the uncertain phrase | Uncertain against neutral: about 75% accuracy from prosody |

## 4. Is it novel?

- **Reading tone is not novel.** Hume, OpenAI and Google all do it, and open models tag emotion well.
- **Using tone for turn-taking is not novel.** LiveKit, Pipecat and Hume all do it; DUET already uses LiveKit's.
- **Feeding tone into the decision to act is, as far as we can find, new.** An unsure-sounding value is asked back
  before the action runs; a shouted "no" is heard before anything commits; tone may delay or ask but never act.
  Voice agents today confirm values when the *recogniser's confidence* is low, not when the *speaker* sounds unsure.
  We found no public system, open or closed, that gates tool calls on prosody. It fits DUET's own story: the
  coordinator decides when acting is allowed, and now it also hears how sure you sounded.

## 5. What we measured before building it

All on 21 Oct 2026, with scratch scripts, on three sets: **CREMA-D** (10 actors, the same sentence said angry,
fearful, sad, happy and neutral: real, labelled emotion), **our own voices** (13 utterances cut from the S24
recordings of 20 Oct: plain speech, Indian English, phone audio) and **Kokoro** (4 voices × 14 lines: certain,
unsure, shouted).

**Emotion models.**

| Model | Result | Verdict |
|---|---|---|
| SenseVoice-Small (sherpa-onnx, int8) | "unknown" emotion on 49 of 50 English clips | rejected |
| emotion2vec-web-distill (9.7 MB ONNX; a distillation of emotion2vec+ large, under emotion2vec's FunASR model licence: free to use, modify and share, with the source and model name kept) | angry, fearful or sad caught at a negative score of 0.6 on 19 of 30 CREMA-D clips, 1 false alarm in 20 calm ones; a few ms a segment on the CPU | **used, only with distress words** |
| the same, level-sensitive | brought to one level the result fell (0.05 RMS: 17 of 30, 3 false); quiet audio reads as sad and loud as angry; one Kokoro voice's plain lines read sad 0.8 | fed DUET's audio as it arrives; never decides alone |

**Vocal effort** (what survives the phone's gain control). CREMA-D, every clip brought to one level as automatic
gain control does, each emotion against the same actor's neutral take:

| | brightness (energy 1–4 kHz against 80 Hz–1 kHz) | median pitch | loudness after levelling | marked raised by `tone.py` |
|---|---|---|---|---|
| angry | **+18.9 dB** (min +8.9) | **+14.0 semitones** | +2.3 dB | **9 of 10** |
| fearful | +13.3 dB | +12.3 semitones | +2.5 dB | 7 of 10 |
| happy | +12.2 dB | +9.8 semitones | +1.3 dB | 8 of 10 (excitement: timeline only) |
| sad | −1.0 dB | +1.4 semitones | −1.0 dB | 0 of 10 |
| neutral | 0 | 0 | 0 | 0 of 10 |

Plain speech marked raised: **0 of 13** of our S24 utterances, **0 of 40** plain Kokoro lines. Before levelling,
loudness rose by 21.7 dB, which a phone flattens: brightness and pitch carry the cue. A first pitch tracker of our
own (numpy autocorrelation) made octave errors on deep voices and on shouts (angry marked raised 6 of 10); Praat's
tracker (`praat-parselmouth`) replaced it.

**Pitch at the end of an utterance** (the classic "unsure" cue). Against each speaker's usual ending, a rise of
4 semitones flagged **6 of 29** plain statements and found only **8 of 29** rises of 4 semitones added to the
same statements with Praat. On the S24 voices, 2 of 5 plain statements ended rising ("Uh, good morning." +3.4,
"I have a stomach pain." +10.6), as research on Indian English expects. Kokoro says "4:30?" with the same falling
pitch as "4:30.", and Whisper's "?" follows the syntax, not the voice. **So pitch is off by default
(`DUET_TONE_PITCH=1` turns it on)**, and unsure rests on a hedge next to the value or a long pause before it.

**Cost.** Measuring a segment (brightness, Praat pitch, the emotion model): 12 ms at the median, 31 ms at most, in
a worker thread while Whisper transcribes the same segment: nothing is added to the time to answer.

## 6. The design as built

```
user audio (AudioTape) ─► tone.analyse(), per segment, as it ends, in a worker thread
                            brightness · pitch (Praat) · level · pauses · emotion (emotion2vec distill)
                            each against the speaker's baseline (their plain segments in this conversation)
                                        │
words (Whisper) ────────────────────────┤  tone.read(): the segments of the open request with its words
                                        ▼
                         a Reading: urgent · unsure · distressed · frustrated · raised voice, with its cues
     ┌───────────────┬────────────────┬────────────────┬────────────────┬──────────────┬──────────────┐
 COORDINATOR      TOOLBOX          FAST MIND        CLOUD MIND        MOUTH          TIMELINE
 extra_hold:      unsure value →   a shouted "no"   the turn note:    distressed:    "Heard in your
 4 s after a      "4:30…, right?"  or a raised      how they          Kokoro at      voice: …" ·
 raised voice     (DUET's offer:   "okay!" over     sounded, and      0.9 speed      "Checking first:
 until its words; "yes" runs it,   DUET = stop;     what to do in                    4:30…, right?"
 revising hold    no model)        upset → cloud    the reply
 when unsure
```

| Where | What changed |
|---|---|
| `duet_voice/galaxy/tone.py` (new) | `analyse` (one segment), `Baseline` (the speaker's usual), `read` (segments + words → `Reading`), `Listener` (one conversation: each segment's place is made the moment it ends, its words fill it in order, its measurements a few ms later), `ask_back` (the question for a value), the pinned, checksummed emotion model |
| `duet_voice/coordinator.py` | `extra_hold`: an optional callable that can only lengthen the hold (None for the benchmark) |
| `duet_voice/galaxy/tools.py` | `PhoneToolbox._unsure`: a write with a value is read once the gate's hold has passed; an unsure one is not sent, and its question becomes DUET's offer |
| `duet_voice/galaxy/fast.py` | the stop for a shouted "no" or a raised backchannel; upset small talk goes to the cloud mind; the question ends the reply, whichever mind planned the action |
| `duet_voice/galaxy/proactive.py` | an offer of source "tone" has its own note for both minds |
| `duet_voice/galaxy/agent.py` | segments measured from the tape at each speech end; the reading of each request; the coordinator's extra hold; the offer; no "Sure." before the question; the slower voice; the timeline events |
| `duet_voice/speech_models.py` | `synthesize(text, speed=1.0)` (the benchmark keeps 1) |
| `app/www/app.js`, `style.css` | the tone steps on the timeline (an APK rebuild shows them on the phone) |
| `requirements.txt` | `praat-parselmouth==0.4.7` (onnxruntime and huggingface_hub were already there) |

## 7. How it was tested

- **Unit tests** (`tests/test_galaxy_tone.py`, 42): measuring synthetic voices (pitch within half a semitone;
  brighter, higher at the same level is raised; nothing before a baseline exists); every label needs both channels;
  hedges count only next to the value ("I think I want an alarm at 7" is not unsure); pauses mid-phrase or after a
  filler; words find their segment in order and skip a noise burst; the question for each kind of value; the hold
  only lengthens; the toolbox never sends an unsure value and reads it after the hold; "yes" runs it from the offer;
  a raised "okay" and a shouted "no" stop DUET, a calm one does not; upset small talk goes to the cloud; the
  question ends the reply from either mind; the agent measures a segment from the tape; the voice slows; the
  benchmark never hears it. All 317 unit tests pass.
- **The spoken suite** (`scripts/galaxy_suite.py`): a new `^` prefix says a line in a raised voice (Kokoro, then
  Praat pitch +7 semitones, a high shelf of +18 dB from 0.8 to 3 kHz, +3 dB): on the suite's voice that measures
  brighter by 11–14 dB and higher by 7–9 semitones against its plain lines. Six new scenarios (`tone_unsure`,
  `tone_unsure_change`, `tone_certain`, `tone_shout`, `tone_raised_ok`, `tone_frustrated`) and two new checks (`tone`
  labels heard, `no_tone_ask`). Results: section 7.1.
- **Regression**: the 45 earlier scenarios again, with tone on. Results: section 7.1.

### 7.1 Results

(filled in from the runs below)

## 8. Limits, and the impact, honestly

- **Measured on actors, synthetic voices and 13 of our own utterances**, not yet on the S24 with a real raised
  voice. The thresholds are the measured ones; a rehearsal should check them on Naman's and Tanmay's voices (the
  run log keeps every segment's numbers for that).
- **Sad sounds like calm** to the effort cues (CREMA-D: 0 of 10 raised); only the emotion model hears it, and only
  with distress words.
- **Excitement sounds like anger** to the effort cues (happy: 8 of 10 raised). That is why a raised voice alone
  changes nothing but the timeline.
- **Unsure from the voice alone is weak**: the pitch cue misfired on 6 of 29 plain statements, so unsure needs a
  hedge or a long pause. A value said plainly but meant unsurely is still taken as said.
- **The emotion model is level-sensitive** and reads some synthetic voices as sad. It never decides alone.
- **Impact.** For the jury: it answers "does it hear how I say it?" with something concrete and, as far as we know,
  new: tone that may hold or ask before an action but never act. For users: modest, because most intent is in the
  words; it catches a shouted stop, an unsure value and an upset user. For the benchmark: none (it is off there:
  FDB-v3 has no second turn to answer "4:30, right?").

## 9. Next

- The same channel on the phone, in the Kotlin engine (round 2 plan, Track B).
- Gemma 4 E2B hearing the audio itself (llama.cpp's audio input), so meaning and tone come from one fast mind.
- Calibrate the thresholds on recorded team voices (each segment's numbers are already logged).
- SenseVoice or a newer tagger for laughter, crying and coughs, if one handles English.

## Sources

- Hume EVI: [overview](https://dev.hume.ai/docs/empathic-voice-interface-evi/overview), [EVI API](https://hume.ai/blog/introducing-hume-evi-api)
- OpenAI Realtime voice activity detection (semantic VAD): [docs](https://developers.openai.com/api/docs/guides/realtime-vad)
- Gemini Live API, affective dialog: [docs](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api/configure-gemini-capabilities)
- Kyutai Unmute: [post](https://kyutai.org/2025/05/22/unmute.html)
- Pipecat Smart Turn: [github](https://github.com/pipecat-ai/smart-turn)
- SenseVoice: [github](https://github.com/FunAudioLLM/SenseVoice)
- emotion2vec: [github](https://github.com/ddlBoJack/emotion2vec); the distillation used: [Hugging Face, thomashallock/emotion2vec-web-distill](https://huggingface.co/thomashallock/emotion2vec-web-distill)
- CREMA-D: [github](https://github.com/CheyneyComputerScience/CREMA-D) (Cao et al., IEEE Transactions on Affective Computing, 2014)
- Praat through Parselmouth: [github](https://github.com/YannickJadoul/Parselmouth)
- OSUM-EChat: [arXiv 2508.09600](https://arxiv.org/html/2508.09600v1); ParaS2S: [ICLR 2026](https://mlanthology.org/iclr/2026/yang2026iclr-paras2s/); Resurfacing paralinguistic awareness: [arXiv 2603.11947](https://arxiv.org/pdf/2603.11947)
- Recognizing uncertainty in speech (Pon-Barry, Shieber): [arXiv 1103.1898](https://arxiv.com/abs/1103.1898)
- Indian English intonation: [Cambridge, Intonation in varieties of English](https://www.cambridge.org/highereducation/books/the-sounds-of-english-around-the-world/FA1D8F8E7849D5A9CFDC8D9E98655F7A/intonation-in-varieties-of-english/C5DC9080CEA4E53C37D7A37A96681FA7); [pitch accents in Indian English](https://findanexpert.unimelb.edu.au/scholarlywork/1320287-pitch-accent-types-and-tonal-alignment-of-the-accentual-rise-in-indian-english%28es%29)
- Gemma 4 E2B audio in llama.cpp: [Unsloth guide](https://unsloth.ai/docs/models/gemma-4); [llama.cpp multimodal docs](https://gitcode.com/greenhills555/llama.cpp/blob/master/docs/multimodal.md)
- Tool-call confirmations in voice agents: [partitech](https://www.partitech.com/en/technical-blog-article/real-time-voice-agents-in-2026-architecture-latency-consent-and-quality-of-service)
