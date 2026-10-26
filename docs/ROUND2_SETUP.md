# DUET Round 2: set it up from a fresh clone (laptop + Galaxy S24)

For Naman, and for an AI coding agent working on his machine. Follow the steps in order; each ends with a
check. Do not skip a check: if one fails, fix it before going on.

> **Agent rules (read first)**
> - Never print, commit or paste the keys in `.env.local` / `.env.livekit`. Both files are git-ignored.
> - Never force-stop the Clock app on the phone (it cancels the owner's alarms). Stop test timers by hand.
> - Ask the phone's owner before `phone_check.py --assistant` (makes DUET the default assistant, reversible).
> - Do not push to `main` (it holds the submitted Round 1 version). Work on `round2-tone` (`round2` plus the tone
>   channel, `docs/ROUND2_TONE_PLAN.md`); `round2` is the same without it.
> - On Windows, run the `.sh` scripts and the commands below in **Git Bash**, not PowerShell or cmd.

## 0. What runs where

```
Galaxy S24 (DUET app: mic, speaker, phone actions)
   │  LiveKit Cloud (audio + data)          USB: adb reverse tcp:8787 (token server)
   ▼
Laptop (NVIDIA GPU)
   ├─ DUET agent: Silero VAD, Whisper large-v3-turbo (ears), Kokoro (voice), the coordinator
   ├─ fast mind: Gemma 4 E2B on llama.cpp, port 18002
   ├─ token server + web app, port 8787
   └─ SmartThings REST (the home)
Cloud: Gemma 4 26B through Google's API (the cloud mind)
```

It is not standalone: the phone needs the laptop running, and the laptop needs internet.

## 1. What you need

| Thing | Version / note |
|---|---|
| Windows 10/11 laptop with an NVIDIA GPU | 6 GB+ VRAM, CUDA 12 driver (`nvidia-smi` works) |
| Git for Windows (Git Bash) | any recent |
| Python | **3.11** (3.10–3.12 work; we use 3.11.0) |
| Node.js | 20+ (only to build the app) |
| JDK 21 + Android SDK 36 (platform-tools, platforms;android-36, build-tools;36.0.0) | only to build the app; Android Studio installs both |
| Disk | ~15 GB (Python packages + models + Gradle) |
| Galaxy S24 + a USB-C **data** cable | Developer options on (section 6) |
| Keys (from Tanmay, privately) | Google API key(s) for Gemma, LiveKit Cloud URL/key/secret, a SmartThings token |

## 2. Clone

```bash
git clone -b round2-tone https://github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet
cd duet
```
Already have a clone? `git fetch origin && git switch round2-tone && git pull`, then run the `pip install -r
requirements.txt` line of section 3 again (the tone channel adds one package, `praat-parselmouth`).

Check: `git log --oneline -1` shows the latest round2-tone commit; `ls docs/ROUND2_DEMO.md docs/ROUND2_TONE_PLAN.md`
lists both.

## 3. Python environment

```bash
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
# torch with CUDA first (the default Windows wheel is CPU-only):
.venv/Scripts/python.exe -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install pytest
```

Check:
```bash
.venv/Scripts/python.exe -c "import torch; print(torch.cuda.is_available())"   # True
.venv/Scripts/python.exe -m pytest -q tests                                       # all pass (343 on 21 Oct)
.venv/Scripts/python.exe -c "from duet_voice.galaxy import tone; tone.prewarm(); print(tone._session() is not None)"   # True
```
The first real run downloads Whisper large-v3-turbo, Kokoro and the turn detector from Hugging Face (a few GB,
once). The last check downloads the tone channel's emotion model (emotion2vec-web-distill, 10 MB, pinned and
checked against its SHA-256); `False` means it could not be downloaded, and tone then works without emotion (the
voice's effort and timing, and the words, are still read).

## 4. Keys

Create two files in the repo root (both git-ignored; get the values from Tanmay privately):

`.env.livekit`
```
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=<key>
LIVEKIT_API_SECRET=<secret>
```

`.env.local`
```
GOOGLE_API_KEYS=<key1>,<key2>,<key3>     # free-tier Gemma keys; more keys = more cloud questions a minute
SMARTTHINGS_TOKEN=<token>                 # account.smartthings.com/tokens, tick Devices + Locations; lasts 24 h
```

Check: `.venv/Scripts/python.exe -m duet_voice.gemma_api` prints a usable key (exit code 0).

Without a SmartThings token everything still works with the app's **simulated home**; the home panel says so.

## 5. The fast mind (llama.cpp + Gemma 4 E2B)

```bash
mkdir -p ../duet_local/llama ../duet_local/models && cd ../duet_local
B=b11249
curl -L -o llama.zip  https://github.com/ggml-org/llama.cpp/releases/download/$B/llama-$B-bin-win-cuda-12.4-x64.zip
curl -L -o cudart.zip https://github.com/ggml-org/llama.cpp/releases/download/$B/cudart-llama-bin-win-cuda-12.4-x64.zip
unzip -oq llama.zip -d llama && unzip -oq cudart.zip -d llama
curl -L -C - -o models/gemma-4-E2B-it-Q4_0.gguf \
  https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_0.gguf    # ~2.8 GB
cd ../duet
```
(If `curl` fails on certificate revocation, add `--ssl-no-revoke`.)

Check: `../duet_local/llama/llama-server.exe --version` prints `build 11249`.

## 6. Start the laptop side

```bash
export LLAMA_SERVER="$(cd ../duet_local/llama && pwd)/llama-server.exe"
FAST_MODEL="$(cd ../duet_local/models && pwd)/gemma-4-E2B-it-Q4_0.gguf" bash scripts/galaxy_demo.sh
```
`LLAMA_SERVER` must be set: the script's default path is Tanmay's (`/e/duet_local/...`). Wait for `Ready.`
(1–3 minutes the first time). Ctrl-C stops everything.

Check (another Git Bash window):
```bash
curl -s localhost:8787/api/health
# {"fast_mind": {"ok": true}, "cloud_mind": {"ok": true}, "smartthings": {"ok": true, "devices": 7}, "livekit": {"ok": true},
#  "tone": {"ok": true}}      (tone false: its "why" says what is missing)
```
Logs: `results/galaxy/agent.log`, `results/galaxy/fast-server.log`.

Optional, with a SmartThings token: create the demo home's 7 virtual devices once, then reset before rehearsals:
```bash
.venv/Scripts/python.exe scripts/smartthings_setup.py           # create + reset
.venv/Scripts/python.exe scripts/smartthings_setup.py --reset   # before each rehearsal
```

## 7. Build the phone app (APK)

```bash
cd app
npm install
npm run sync                     # copies the web app and its libraries into the Android project
cd android
```
Two files hold Tanmay's local paths; change them on your machine (do **not** commit these two changes):
- `app/android/local.properties` → `sdk.dir=C:/Users/<you>/AppData/Local/Android/Sdk` (forward slashes)
- `app/android/gradle/wrapper/gradle-wrapper.properties` →
  `distributionUrl=https\://services.gradle.org/distributions/gradle-8.14.3-bin.zip`

Then, with `JAVA_HOME` pointing at JDK 21:
```bash
./gradlew assembleDebug
cd ../..
```
Check: `app/android/app/build/outputs/apk/debug/app-debug.apk` exists (~7 MB).
If Gradle says "Unable to establish loopback connection", set
`JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=C:/tmp -Djdk.net.unixdomain.tmpdir=C:/tmp"` and retry.

## 8. Prepare the S24 (by hand, once)

1. Settings › About phone › Software information: tap **Build number** 7 times.
2. Settings › Developer options: **USB debugging** on.
3. Settings › Security and privacy: **Auto Blocker off** (it blocks USB commands).
4. Settings › Connections › SIM manager: a **preferred SIM** for calls and messages (not "Ask every time").
5. Plug in; tap **Allow** on "Allow USB debugging?" (tick "Always allow").
6. While the app installs, watch for **Google Play Protect**: choose *Install anyway / without scanning*.

## 9. Install and check the app on the phone

`scripts/phone_check.py` uses Tanmay's adb path unless `ADB` is set:
```bash
export ADB="C:/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe"
.venv/Scripts/python.exe scripts/phone_check.py
```
It installs the APK, grants DUET's permissions over USB, sets `adb reverse tcp:8787` (the phone reaches the laptop
through the cable; no Wi-Fi setup), runs the in-app self-test and saves a screenshot.

Check: every line `OK`, and "self-test … of … on this phone" all green.
Run `adb reverse tcp:8787 tcp:8787` again whenever the cable is re-plugged.

Optional, **only with the phone owner's OK**: DUET as the default digital assistant (side key / assist gesture):
```bash
.venv/Scripts/python.exe scripts/phone_check.py --no-install --assistant
```

## 10. Use it

Open DUET on the phone. In Settings the server is `localhost:8787` (set by phone_check). Pick a mode and talk:

| Mode | Try |
|---|---|
| Assistant | "Set an alarm for 6:30, no, make it 7." · "Turn on the torch." · "Is the front door locked?" · "How much battery do I have?" · "Turn on the AC at home and set it to 22." |
| Drive | "Take me to the office, no wait, the airport." · "Hand it to Google Maps." · "Take me home", then **Skip ahead: 10 min**: DUET offers the AC (and a light after 6 pm); answer "Yes, make it 22", "Just the AC" or "No thanks". |
| Care | "Did I take my morning medicine?" · "Call my daughter." |
| Tone (any mode) | Say two or three ordinary things first (DUET learns your usual voice from them). "Set an alarm for 4:30, I think." → "4:30 in the afternoon, right?" → "Yes." sets it; "Set an alarm for 4:30." sets it at once. Ask something long, then shout **"No, no, no!"** over the answer: DUET stops and stays quiet. "It's *still* not working!" said annoyed: the timeline shows *Heard in your voice: frustrated*. |

Interrupt DUET any time; correct yourself mid-sentence. The timeline under the conversation shows what it heard,
held back, did and confirmed. The **Drills** panel shows recovery: cut/slow the cloud, phone refuses, SmartThings
down, drop the connection.

## 11. Verify everything (one command)

```bash
.venv/Scripts/python.exe scripts/verify_all.py --device s24     # ~70 min; report in results/verify/
.venv/Scripts/python.exe scripts/verify_all.py --device s24 --no-suite   # phone parts only, ~40 min
```
Runs: unit tests, stack health, the spoken suite (39 scenarios), end-to-end on the phone (Assistant, Drive,
arrival offer, washer offer, skills, a skill added live, drills), resilience behind Google Maps, the demo preflight.
After it: open the Clock app and **delete the test timers by hand**; delete test alarms at 6:30 / 7:00 in DUET.

Expected on 18 Oct (emulator, live SmartThings): every phone step passes; the spoken suite 38–39/39. A miss marked
"run again after the free tier's per-minute budget ran out" is the free Gemma quota, not a bug.

## 12. Known limits and fixes

| Symptom | Cause / fix |
|---|---|
| `fast_mind` not ok | llama-server not found: set `LLAMA_SERVER`; see `results/galaxy/fast-server.log` |
| "No usable Google API key" | `.env.local` missing or key invalid |
| SmartThings `401` / "token was refused" | token expired (24 h): make a new one, restart the stack |
| SmartThings `429` "limiting requests" | ~10 requests/min per token; wait a minute (DUET waits out short limits itself) |
| "Sorry, I couldn't get that done just now" on cloud questions | free Gemma tier: about 6 cloud questions a minute; add keys to `GOOGLE_API_KEYS` |
| Phone can't reach the laptop | re-run `adb reverse tcp:8787 tcp:8787`; check the stack says `Ready.` |
| DUET silent behind Google Maps | the "DUET is listening" notification must be present; restart the conversation from DUET's screen |
| Echo / DUET interrupts itself on loudspeaker | use Galaxy Buds; or start the stack with `DUET_INTERRUPT_MODE=vad` (the default since 21 Oct is LiveKit's adaptive interruption, which stops DUET faster) |
| "Connected · waiting for DUET…" never changes | an APK from before 21 Oct: rebuild it (section 7) and install it (section 9) |
| torch error "paging file is too small" | close the Android emulator / other GPU apps; free RAM |
| At a venue, on shared Wi-Fi | the token server answers anyone on the same network (anyone could join and reach the SmartThings home): use the USB cable and start the stack with `DUET_APP_HOST=127.0.0.1 bash scripts/galaxy_demo.sh` |
| `/api/health` says a key is refused | that key is not valid or has expired; DUET rests it and uses the others. Replace it in `GOOGLE_API_KEYS` |
| DUET asks "…, right?" when you did not want it | you said a hedge next to the value ("4:30, I think", "maybe 15 minutes"): say it plainly, or answer "yes". Turn the tone channel off with `DUET_TONE=0 bash scripts/galaxy_demo.sh` |
| Tone never shows on the timeline | an APK from before the tone channel: rebuild it (section 7). Each measured segment is in the run log (`results/galaxy/traces/<room>.jsonl`, kind `tone_segment`); `agent.log` says "tone: no emotion model" if the model could not be loaded |

## 13. Where things are

| Path | What |
|---|---|
| `duet_voice/galaxy/` | the phone agent: coordinator use, fast mind, offers (`proactive.py`), readings, skills, SmartThings, how you said it (`tone.py`) |
| `duet_voice/coordinator.py` | epochs, commit gate, exactly-once ledger |
| `app/www/` | the app (UI + `phone.js`); `app/www/skills.json` = new phone actions without code |
| `app/android/.../DuetPhonePlugin.java` | the native plugin (34 phone actions) |
| `scripts/` | `galaxy_demo.sh` (stack), `phone_check.py`, `verify_all.py`, `smartthings_setup.py`, `demo_preflight.py` |
| `docs/ROUND2_DEMO.md` | the demo night checklist and script |
| `docs/ROUND2_TONE_PLAN.md` | the tone channel: research, measurements, design, how it was tested |
| `docs/ROUND2_TEST_REPORT.md` | the failure hunt of 21 Oct: what was tested, what broke and was fixed, the verdict |
| `docs/round2/` | the Round 2 deck (HTML and PDF) and the demo film |
