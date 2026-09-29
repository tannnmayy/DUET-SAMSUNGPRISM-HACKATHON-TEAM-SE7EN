/* DUET for Galaxy: demo mode, for recording the app without a phone or a person.
 *
 *   index.html?demo=<scene>      (scenes: demo/scenes.json)
 *
 * A scripted user speaks recorded lines (demo/audio/*.wav, made by
 * scripts/galaxy_demo_voices.py) into the room instead of the microphone. The
 * script is reactive: it waits for DUET to finish a reply, or cuts in while DUET
 * is speaking, exactly as a person would. Everything else is the real app and the
 * real agent. Both voices are recorded (MediaRecorder) for the video's soundtrack.
 */
(function () {
  "use strict";
  const q = new URLSearchParams(location.search);
  const name = q.get("demo");
  if (!name) return;
  if (q.get("embed")) document.body.classList.add("embed");
  const App = window.DuetApp, P = window.DuetPhone, LK = window.LivekitClient;
  const toParent = (msg) => { if (window.parent !== window) window.parent.postMessage(msg, "*"); };
  const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000));
  const now = () => performance.timeOrigin + performance.now();

  // ---- what DUET is doing, for the script's waits
  const st = { agent: "initializing", lineEnd: 0, saidAt: [], thinkerAt: [] };
  App.hooks.events.push((ev) => {
    if (ev.kind === "agent_state") st.agent = ev.state;
    if (ev.kind === "agent_said") st.saidAt.push(now());
    if (ev.kind === "thinker_say") st.thinkerAt.push(now());
  });
  async function until(test, limit) {
    const end = now() + (limit || 60) * 1000;
    while (!test() && now() < end) await sleep(0.05);
  }
  const after = (list) => list.some((t) => t > st.lineEnd);
  async function waitReply() {           // DUET answered and is listening again
    await until(() => after(st.saidAt) && st.agent === "listening", 75);
    await sleep(0.9);
  }
  async function waitBarge(into) {       // DUET is in the middle of its answer
    await until(() => after(st.thinkerAt) && st.agent === "speaking", 75);
    await sleep(into || 2);
  }

  // ---- audio: the scripted voice goes to the room and to the recording; DUET's voice to the recording
  const ctx = new AudioContext({ sampleRate: 48000 });
  const mic = ctx.createMediaStreamDestination();
  const mix = ctx.createMediaStreamDestination();
  let recorder = null, chunks = [], recStart = 0;

  App.hooks.mic = async (room) => {
    await ctx.resume();
    await room.localParticipant.publishTrack(mic.stream.getAudioTracks()[0],
      { source: LK.Track.Source.Microphone, name: "microphone" });
    recorder = new MediaRecorder(mix.stream, { mimeType: "audio/webm;codecs=opus", audioBitsPerSecond: 128000 });
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.start(1000);
    recStart = now();
  };
  App.hooks.remoteAudio = (track) => {
    const src = ctx.createMediaStreamSource(new MediaStream([track.mediaStreamTrack]));
    src.connect(mix);
  };

  async function speak(file) {
    const buf = await ctx.decodeAudioData(await (await fetch(file)).arrayBuffer());
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(mic); src.connect(mix);
    await new Promise((resolve) => { src.onended = resolve; src.start(); });
  }

  async function finish(status) {
    let audio = "";
    if (recorder && recorder.state !== "inactive") {
      await new Promise((resolve) => { recorder.onstop = resolve; recorder.stop(); });
      const blob = new Blob(chunks, { type: "audio/webm" });
      audio = await new Promise((resolve) => {
        const r = new FileReader(); r.onload = () => resolve(String(r.result).split(",")[1]); r.readAsDataURL(blob);
      });
    }
    const result = { status, scene: name, audio, audioStart: recStart, room: App.room ? App.room.name : "" };
    window.__demoResult = result;
    toParent({ type: "done", result });
  }

  async function run() {
    const all = await (await fetch("demo/scenes.json")).json();
    const scene = all.scenes[name];
    if (!scene) { finish("no such scene: " + name); return; }
    // a clean phone for every scene, and the scene's person
    P.reset();
    // the morning's doses were taken at 9:02, whatever time the scene is recorded
    P.state.meds.forEach((m) => { if (m.time === "09:00") m.taken = "09:02"; });
    App.settings.name = scene.name;
    App.settings.contacts = "Priya (daughter), +910000000001\nRahul (son), +910000000002";
    App.settings.direct = false;
    try { localStorage.setItem("duet.settings", JSON.stringify(App.settings)); } catch (e) { /* ignore */ }
    toParent({ type: "scene", scene: name, info: scene });
    await App.start(scene.mode);
    try {
      for (let i = 0; i < scene.lines.length; i++) {
        const line = scene.lines[i];
        if (line.wait === "reply") await waitReply();
        if (line.wait === "barge") await waitBarge(line.into);
        if (line.event) {
          App.simulate(line.event);
          toParent({ type: "caption", who: "phone", text: line.event === "near" ? "The car is 10 minutes from home" : line.event });
          st.lineEnd = now();
          continue;
        }
        toParent({ type: "caption", who: "you", text: line.say });
        await speak("demo/audio/" + name + "_" + i + ".wav");
        st.lineEnd = now();
        await sleep(line.after != null ? line.after : 0.6);
      }
      await waitReply();
      await sleep(1.2);
      finish("ok");
    } catch (e) {
      console.error(e);
      finish("error: " + (e.message || e));
    }
  }
  run();
})();
