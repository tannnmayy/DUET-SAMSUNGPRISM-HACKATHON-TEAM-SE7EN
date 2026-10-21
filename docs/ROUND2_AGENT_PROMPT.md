# Paste this whole file to your AI coding agent

You are setting up and testing **DUET for Galaxy** (Team SE7EN, Samsung PRISM GenAI Hackathon, Round 2) on this
Windows laptop and a **Samsung Galaxy S24** connected by USB. DUET is a full-duplex voice assistant: the phone app
records and plays audio, and this laptop runs the brains (speech recognition, two language models, a coordinator that
decides when an action may run, speech synthesis). Your job: get it running on the phone, run the checks, run the
test conversations below, and collect the logs for Tanmay. Work through the steps in order. **Every step ends with a
check; do not go on until it passes.** If a check fails, fix only what the step is about, or stop and report.

The code is on GitHub, branch **`round2-tone`** (its latest commit; it contains this file, `docs/ROUND2_AGENT_PROMPT.md`):
`https://github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN`

## Rules (read first, follow always)

1. **Never print, log, commit or paste secrets**: the contents of `.env.local` and `.env.livekit` (Google API keys,
   LiveKit secret, SmartThings token). Both files are git-ignored; keep it that way. When you check them, check that
   a variable is *set*, never show its value.
2. **Never push anything to GitHub** and never commit. You are testing, not developing. The only local edits allowed
   are the two Android path files in step 7 (do not commit them).
3. **Never force-stop or clear the Clock app** on the phone: it deletes the owner's alarms. Delete test alarms and
   timers by hand, or ask the owner.
4. **Ask the phone's owner before** running `scripts/phone_check.py --assistant` (it makes DUET the default digital
   assistant; reversible) and before changing any phone setting yourself.
5. Run every shell command in **Git Bash** (not PowerShell, not cmd). The repo's `.sh` scripts need it.
6. Do not edit DUET's code to make a test pass. If something fails, collect the logs (step 12) and report it.

## 0. What runs where

```
Galaxy S24 (DUET app: microphone, speaker, phone actions)
   |  LiveKit Cloud (audio + data)        USB cable: adb reverse tcp:8787 (the phone reaches the laptop)
   v
This laptop (NVIDIA GPU, 6 GB or more)
   |- DUET agent: Silero VAD, Whisper large-v3-turbo, Kokoro voice, the coordinator, the tone channel
   |- fast mind: Gemma 4 E2B on llama.cpp, port 18002
   |- token server and web app, port 8787
Cloud: Gemma 4 26B through Google's API (the cloud mind); SmartThings (the home)
```
The phone needs the laptop running, and the laptop needs the internet.

## 1. Check the machine

| Need | Check |
|---|---|
| Windows 10/11, NVIDIA GPU with 6 GB+ and a CUDA 12 driver | `nvidia-smi` shows the GPU |
| Git for Windows (Git Bash) | `git --version` |
| Python 3.11 (3.10-3.12 work) | `py -3.11 --version` |
| About 15 GB free disk | |
| The S24 and a USB-C **data** cable | |
| From Tanmay, privately: `.env.local` and `.env.livekit` (or their values), a **fresh** SmartThings token (they last 24 hours), and optionally the APK file `DUET-galaxy-tone-debug.apk` | ask the owner |

Only to build the app yourself (step 7, option B): Node.js 20+, JDK 21, Android SDK 36.

## 2. Get the code

New clone:
```bash
git clone -b round2-tone https://github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN.git duet
cd duet
```
An existing clone of this repo (for example on `round2`): first check `git status` shows no local changes you need,
then:
```bash
git fetch origin
git switch round2-tone
git pull
```
**Check:** `git branch --show-current` prints `round2-tone`; `git status -sb` says it is up to date with
`origin/round2-tone`; `ls docs/ROUND2_AGENT_PROMPT.md docs/ROUND2_SETUP.md duet_voice/galaxy/tone.py` lists all three
(an older checkout without `ROUND2_AGENT_PROMPT.md` cannot start the stack as step 6 does).

## 3. Python environment

From the repo root (skip the `venv` line if `.venv` already exists; run the rest anyway, the requirements changed):
```bash
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install pytest
```
**Check:**
```bash
.venv/Scripts/python.exe -c "import torch; print(torch.cuda.is_available())"
.venv/Scripts/python.exe -m pytest -q tests
```
The first prints `True`. The tests: **345 passed** and at most 1 skipped (the benchmark checkout is not in git).

## 4. Keys

Put the two files from Tanmay in the repo root, named exactly `.env.local` and `.env.livekit`. They must contain
these variables (values from Tanmay; do not show them):

- `.env.livekit`: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `.env.local`: `GOOGLE_API_KEYS` (comma-separated, several keys), `SMARTTHINGS_TOKEN` (the fresh one)

**Check** (prints only which variables are set):
```bash
for f in .env.livekit .env.local; do echo "$f:"; grep -oE "^[A-Z_]+=" "$f"; done
.venv/Scripts/python.exe -m duet_voice.gemma_api > /dev/null && echo "Google keys OK"
```
Then download the tone channel's emotion model (10 MB, checked against its SHA-256):
```bash
.venv/Scripts/python.exe -c "from duet_voice.galaxy import tone; tone.prewarm(); print(tone._session() is not None)"
```
It prints `True`. (`False` means it could not be downloaded; DUET still works, without the emotion cue.)

Without a valid SmartThings token everything still works with the app's simulated home.

## 5. The fast mind (llama.cpp and Gemma 4 E2B)

Skip what already exists: check `ls ../duet_local/llama/llama-server.exe ../duet_local/models/gemma-4-E2B-it-Q4_0.gguf`.
```bash
mkdir -p ../duet_local/llama ../duet_local/models && cd ../duet_local
B=b11249
curl -L -o llama.zip  https://github.com/ggml-org/llama.cpp/releases/download/$B/llama-$B-bin-win-cuda-12.4-x64.zip
curl -L -o cudart.zip https://github.com/ggml-org/llama.cpp/releases/download/$B/cudart-llama-bin-win-cuda-12.4-x64.zip
unzip -oq llama.zip -d llama && unzip -oq cudart.zip -d llama
curl -L -C - -o models/gemma-4-E2B-it-Q4_0.gguf \
  https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_0.gguf
cd ../duet
```
(If `curl` fails on certificate revocation, add `--ssl-no-revoke`.)
**Check:** `../duet_local/llama/llama-server.exe --version` prints `build 11249`; the `.gguf` is about 2.8 GB.

## 6. Start the laptop side

Keep this running in its own Git Bash window for the whole session:
```bash
export LLAMA_SERVER="$(cd ../duet_local/llama && pwd)/llama-server.exe"
DUET_APP_HOST=127.0.0.1 FAST_MODEL="$(cd ../duet_local/models && pwd)/gemma-4-E2B-it-Q4_0.gguf" bash scripts/galaxy_demo.sh
```
Wait for `Ready.` (1 to 3 minutes the first time: it downloads Whisper, Kokoro and the turn detector, a few GB, once).
`DUET_APP_HOST=127.0.0.1` keeps the laptop from answering anyone on the Wi-Fi; the phone reaches it over the USB cable.

**Check**, in another Git Bash window:
```bash
curl -s localhost:8787/api/health
```
Expected: `fast_mind` ok, `cloud_mind` ok with "N of N work", `livekit` ok, `tone` ok. `smartthings` ok if the token
is fresh (otherwise the app uses its simulated home: fine). Logs: `results/galaxy/agent.log`,
`results/galaxy/fast-server.log`. Optional, with a valid SmartThings token: `.venv/Scripts/python.exe
scripts/smartthings_setup.py --reset` puts the demo home's devices in their starting state.

## 7. The phone app (APK)

**Option A (fastest): Tanmay's APK.** Copy the file to the path the install script uses:
```bash
mkdir -p app/android/app/build/outputs/apk/debug
cp /path/to/DUET-galaxy-tone-debug.apk app/android/app/build/outputs/apk/debug/app-debug.apk
```
(Its SHA-256 starts with `4197b88571ec9249`; it is built from this branch's app code.)

**Option B: build it.** Needs Node 20+, JDK 21 (`JAVA_HOME`), Android SDK 36:
```bash
cd app && npm install && npm run sync && cd android
```
Edit two files for this machine (**do not commit these edits**):
- `app/android/local.properties`: `sdk.dir=C:/Users/<you>/AppData/Local/Android/Sdk` (forward slashes)
- `app/android/gradle/wrapper/gradle-wrapper.properties`:
  `distributionUrl=https\://services.gradle.org/distributions/gradle-8.14.3-bin.zip`
```bash
./gradlew assembleDebug
cd ../..
```
If Gradle says "Unable to establish loopback connection": set
`JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=C:/tmp -Djdk.net.unixdomain.tmpdir=C:/tmp"` and retry.

**Check:** `app/android/app/build/outputs/apk/debug/app-debug.apk` exists (about 7 MB).

## 8. Prepare the S24 (the owner does this by hand, once)

1. Settings › About phone › Software information: tap **Build number** 7 times.
2. Settings › Developer options: **USB debugging** on.
3. Settings › Security and privacy: **Auto Blocker** off (it blocks USB commands).
4. Settings › Connections › SIM manager: a **preferred SIM** for calls and messages (not "Ask every time").
5. Plug in the cable; on the phone tap **Allow** on "Allow USB debugging?" (tick "Always allow").
6. During the install, if **Google Play Protect** asks: *Install anyway* / *install without scanning*.

## 9. Install and self-test on the phone

```bash
export ADB="C:/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe"     # where adb.exe is on this machine
"$ADB" devices                                                                     # one device, "device" (not "unauthorized")
.venv/Scripts/python.exe scripts/phone_check.py
```
It installs the APK, grants DUET's permissions over USB, sets `adb reverse tcp:8787`, runs the app's self-test and
saves a screenshot.
**Check:** every line `OK`, and "self-test - 14 of 14". After re-plugging the cable, run `"$ADB" reverse tcp:8787
tcp:8787` again.

In DUET's Settings on the phone, the server must be `localhost:8787` (phone_check sets it). Put the teammates' real
phone numbers in DUET's demo contacts (Settings), and on the phone turn **Do Not Disturb on** and **media and call
volume up** for the demo.

## 10. The automatic checks

```bash
.venv/Scripts/python.exe scripts/verify_all.py --quick --device s24
```
About 15 minutes: unit tests, the stack, 10 spoken scenarios, end to end on the phone. The report is in
`results/verify/verify_<time>.md`. If there is time, the full run (about 70 minutes):
`.venv/Scripts/python.exe scripts/verify_all.py --device s24`.
A miss saying "no key has room" or "no answer within 15 s" is the free Google tier, not a bug: note it, go on.
Afterwards, delete the test alarms and timers on the phone **by hand** (rule 3).

## 11. Talk to it (the real test)

Open DUET, pick the mode, and say these, one at a time, waiting for each answer. Note for each: what DUET said, what
happened on the phone, and roughly how long it took. The timeline under the conversation shows each step.

**Assistant mode**
| Say | Expected |
|---|---|
| "Good morning." then "I'm a bit tired today." | a greeting by name, then a caring reply, each in about a second |
| "Set an alarm for 6:30, no, make it 7." | only 7 is set ("Sure. Alarm set for 7…"); the timeline shows 6:30 **Never ran** |
| "Set an alarm for 3:30." then, after it confirms, "No, cancel that, make it 4:30." | 3:30 **Undone**, 4:30 set |
| "Turn on the torch." then "Turn the torch off." then "Set the volume to 40 percent." | each done in about a second, by the fast mind |
| "My battery drains super fast these days. What's going on?" and while it explains, say "right, I see" | it carries on from where it was |
| the same question, and while it explains, shout **"No, no, no!"** | it stops and stays quiet; timeline: "Heard in your voice: urgent" |
| the same question, then "Wait." then "Go on." then "What?" | stops; finishes the answer; says it again |
| "Set an alarm for 4:30, I think." then "Yes." | "4:30 …, right?" first; set only after "Yes" |
| "Set a timer for 15 minutes, maybe." then "No, make it 20." | asks, then sets 20 (15 never runs) |
| "Wake me up at quarter to eight in the evening." | an alarm at 7:45 pm |
| "Call Bob." | says there is no Bob in your contacts; calls nobody |
| "What's the weather in Chennai tomorrow?" | an answer in about 5 seconds |
| "Turn on the AC at home and set it to 22. Actually, make it 21." | the AC at 21 (SmartThings, or the simulated home) |

**Drills** (the app's Drills panel): cut the cloud mind, then "What's the weather?" (it says at once it cannot reach
its cloud mind) and "Turn on the torch." (done anyway); restore it.

**Drive mode**: "Take me to the office. No wait, the airport." (only the airport); "Hand it to Google Maps."; with
Maps in front, "Turn on the AC at home."; "Take me home", then **Skip ahead: 10 min**: DUET offers the AC, answer
"Yes, make it 22".

**Care mode**: "Did I take my morning medicine?"; "I fell down in the bathroom and my hip hurts." (it must never claim
it called an ambulance).

Say a few lines in a normal voice before the shouted ones: DUET learns the speaker's usual voice from them.

## 12. Collect the results for Tanmay

```bash
STAMP=$(date +%m%d_%H%M)
mkdir -p "../duet_results_$STAMP"
cp results/galaxy/agent.log results/galaxy/fast-server.log "../duet_results_$STAMP/" 2>/dev/null
cp -r results/galaxy/traces "../duet_results_$STAMP/" 2>/dev/null
cp -r results/verify "../duet_results_$STAMP/" 2>/dev/null
cp -r results/galaxy/phone "../duet_results_$STAMP/" 2>/dev/null
ls "../duet_results_$STAMP"
```
Zip that folder and send it to Tanmay, together with the notes from step 11 (what was said, what happened, how
long, anything that felt wrong) and any screen recordings. **Never include `.env.local` or `.env.livekit`.**
The traces hold each conversation step by step, including the tone channel's measurements (`tone_segment`), which is
how the thresholds get checked on real voices.

## 13. Stop everything

Ctrl-C in the window from step 6. If anything is left running: `bash scripts/galaxy_stop.sh`.

## If something goes wrong

| Symptom | Fix |
|---|---|
| Step 6 stops with "set LLAMA_MODEL" | you are on an old commit: `git pull` (fixed in the latest `round2-tone`) |
| `fast_mind` not ok | `LLAMA_SERVER` must point to `llama-server.exe` (step 6); see `results/galaxy/fast-server.log` |
| "No usable Google API key" | `.env.local` missing, or every key refused |
| `cloud_mind` "refused: key N" | that key is not valid or has expired; DUET skips it; tell Tanmay |
| SmartThings 401, "token was refused" | the token is older than 24 hours: get a fresh one, restart step 6 |
| "Sorry, I couldn't get that done just now" on questions | the free Google tier (about 6 cloud questions a minute): wait a minute |
| The phone cannot reach the laptop | `"$ADB" reverse tcp:8787 tcp:8787`; the stack says `Ready.`; the cable carries data |
| DUET silent behind Google Maps | the "DUET is listening" notification must be there; restart the conversation from DUET's screen |
| DUET interrupts itself on the loudspeaker | use earbuds, or restart step 6 with `DUET_INTERRUPT_MODE=vad` in front of the command |
| "Connected · waiting for DUET…" never changes | an old APK: install this branch's APK (steps 7 and 9) |
| DUET asks "…, right?" when not wanted | a hedge was said next to the value ("I think"); answer "yes"; or restart step 6 with `DUET_TONE=0` |
| torch "paging file is too small" | close other GPU programs and Android emulators |

More detail: `docs/ROUND2_SETUP.md` (setup), `docs/ROUND2_DEMO.md` (the demo script), `docs/ROUND2_TEST_REPORT.md`
(what was tested on 21 Oct and what is known to be weak), `docs/ROUND2_TONE_PLAN.md` (the tone channel).
