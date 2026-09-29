# DUET for Galaxy: the use-case extension

DUET is a full-duplex voice agent: you can interrupt it, correct yourself mid-sentence and
change your mind, and it never acts on words you took back. The benchmark submission shows
this on Full-Duplex-Bench v3. This app puts the same agent on a Samsung Galaxy phone.

| Mode | For whom | What it shows |
|---|---|---|
| **Assistant** | Every Galaxy owner | Alarms corrected mid-sentence, battery troubleshooting with real phone data that you can interrupt, installed apps, brightness and settings, Google Maps, smart home |
| **Care** | An older person living alone, and their family | Medicines with a double-dose guard, calls and texts to family, reminders, a daily check-in, a family alert |
| **Drive** | A driver, eyes on the road | Destination changed mid-sentence, arrival-time messages, car climate, the home from the car |

## How it works

```
Galaxy phone: DUET app (Capacitor: web UI + DuetPhone native plugin)
   │  full-duplex audio (WebRTC)              ▲  phone actions (LiveKit RPC "duet.tool")
   ▼                                          │  live timeline (data topic "duet")
LiveKit Cloud room ───────────────────────────┤
   ▼                                          │
DUET agent (duet_voice/galaxy/agent.py) ─────┘
   ears: Silero VAD, Whisper large-v3-turbo, turn detector · mouth: Kokoro
   talker (fast acknowledgement) + thinker (tools) through the DUET coordinator:
   commit gate · epochs · exactly-once ledger · failure policy · second look before speaking
   ▼
language model: any OpenAI-compatible server
   (laptop: Qwen3-4B-Instruct-2507 on llama.cpp; GPU server: Qwen3-30B-A3B on vLLM)
```

Every action DUET takes on the phone passes the same coordinator as in the benchmark:
- nothing runs while you are speaking;
- nothing runs before you have been quiet for the commit hold;
- a plan your later words overtook is never carried out;
- no action runs twice.

The app's timeline shows each of these steps live: "Planned", "Never ran", "Done" and
"Not repeated".

**What is real and what is simulated:**
- **Real, through Android's public interfaces:**
  - alarms and timers in the Clock app;
  - battery, temperature, current drain, brightness, refresh rate, timeout, Wi-Fi,
    Bluetooth and location;
  - screen time per app, and the installed apps;
  - brightness and screen timeout changes;
  - settings screens, opening apps, and Google Maps navigation;
  - calls and texts.
- **Simulated inside the app:** the SmartThings-style home, the medicine schedule and the
  car's trip.

## Run it

**1. The laptop.** Needs `.env.livekit` with your LiveKit Cloud project's URL, key and secret.

```bash
LLAMA_SERVER=/e/duet_local/llama/llama-server.exe \
LLAMA_MODEL=/e/duet_local/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
bash scripts/galaxy_demo.sh
```

On a machine with a 24 GB+ GPU and the benchmark installed, leave out the two `LLAMA_`
variables; the benchmark's vLLM server is used instead.

The script prints the laptop's address for the phone, for example `192.168.1.20:8787`. The
first time, Windows asks whether Python may accept connections on private networks. Allow
it, or the phone cannot reach the laptop.

**2. The phone.**
1. Install `app/android/app/build/outputs/apk/debug/app-debug.apk`. Copy it over and open it,
   allowing "install unknown apps" once, or use `adb install -r app-debug.apk`.
2. Put the phone on the same Wi-Fi as the laptop, or run the laptop on the phone's hotspot.
3. Open DUET, then Settings:
   - enter the server address, your name and the contacts DUET may call;
   - under Phone access, allow **Screen time per app** and **Change brightness and timeout**.
4. Choose a mode and allow the microphone. Galaxy Buds give the cleanest audio: no echo, and
   no false interruptions.

**In a browser instead:** open `http://localhost:8787` on the laptop. Phone actions are then
simulated.

## Try these

| Mode | Say | What to watch |
|---|---|---|
| Assistant | "Set an alarm for 3:30. No no, cancel that, set it for 4:30." | Timeline: 3:30 **Never ran**, 4:30 **Done**; one alarm in Clock |
| Assistant | Say "Set an alarm for 3:30", wait, then "No, make it 4:30" | DUET had set 3:30, so it cancels it and sets 4:30 |
| Assistant | "Wake me at 6:30, or maybe 7, hmm." | DUET asks "6:30 or 7?" instead of guessing |
| Assistant | "My battery drains super fast. What's going on?" Then, while it explains: "Oh wait, I think it's my brightness and games." | Real readings; DUET stops, checks your theory against the numbers, and offers a fix without making it |
| Assistant | "How many shopping apps do I have?" | Counts from the phone's real app list |
| Care | "Did I take my blood pressure tablet? I'll just take it now." | The double-dose guard: it was taken at 9:02, so don't |
| Care | Tap "Simulate: evening medicine time" | DUET speaks first with the reminder |
| Drive | "Take me to the office. No wait, the airport." | Only the airport is set |
| Drive | Tap "Simulate: 10 min from destination" | DUET offers one useful thing, such as the AC at home |

## Test without a phone

`scripts/galaxy_fake_phone.py` joins a room like the app does. It speaks scripted lines, answers
DUET's tool calls with a simulated phone, and saves every timeline event to
`results/galaxy/scenario_*.json`:

```bash
python scripts/galaxy_fake_phone.py --list
python scripts/galaxy_fake_phone.py alarm_late
```

## Build the APK

JDK 21, Android SDK 36 and Node 20+ are needed:

```bash
cd app
npm install
npm run sync
cd android
./gradlew assembleDebug
```
