// Records the DUET for Galaxy demo: one video per scene of app/www/demo/scenes.json, then
// all scenes joined into one film.
//
//   node app/tools/record_demo.js correction        # one scene
//   node app/tools/record_demo.js all               # every scene, then demo_full.mp4
//
// Needs the token server (python -m duet_voice.galaxy.server), the agent and its model
// running, Google Chrome, and ffmpeg (FFMPEG, default E:/ffmpeg/...). Headless Chrome opens
// demo.html (the stage: the app in a phone frame, DUET's steps live, captions); the page's
// frames go to ffmpeg at 25 fps as they happen, and the page records both voices itself
// (demo-driver.js). The two are joined on the wall clock.
"use strict";
const { chromium } = require("playwright-core");
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const FFMPEG = process.env.FFMPEG || "E:/ffmpeg/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe";
const BASE = process.env.DEMO_URL || "http://localhost:8787";
const OUT = path.resolve(__dirname, "../../results/galaxy/video");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function record(scene) {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({
    channel: "chrome", headless: true,
    args: ["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream", "--hide-scrollbars"],
  });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: "dark" });
  const page = await context.newPage();
  page.on("console", (m) => { if (m.type() === "error") console.log("  [page]", m.text().slice(0, 200)); });
  const cdp = await context.newCDPSession(page);
  let latest = null;
  cdp.on("Page.screencastFrame", async (f) => {
    latest = Buffer.from(f.data, "base64");
    try { await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }); } catch (e) { /* closing */ }
  });

  const video = path.join(OUT, scene + "_video.mp4");
  const ff = spawn(FFMPEG, ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", "25", "-c:v", "mjpeg", "-i", "-",
    "-c:v", "libx264", "-preset", "veryfast", "-threads", "4", "-crf", "18", "-pix_fmt", "yuv420p", video]);
  ff.stderr.on("data", (d) => process.stdout.write("  [ffmpeg] " + d));

  await page.goto(BASE + "/demo.html?scene=" + encodeURIComponent(scene));
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
  while (!latest) await sleep(20);

  // one frame every 40 ms of wall time, repeating the last one when the page has not changed
  const t0 = Date.now();
  let n = 0, done = false;
  const pump = (async () => {
    while (!done) {
      const due = Math.floor((Date.now() - t0) / 40);
      while (n <= due) { ff.stdin.write(latest); n++; }
      await sleep(8);
    }
  })();

  console.log("recording " + scene + " ...");
  const handle = await page.waitForFunction(() => window.__demoResult, null, { timeout: 360000, polling: 250 });
  const result = await handle.jsonValue();
  await sleep(2000);                                  // hold the final screen a moment
  done = true;
  await pump;
  ff.stdin.end();
  await new Promise((r) => ff.on("close", r));
  await cdp.send("Page.stopScreencast").catch(() => {});
  await browser.close();

  const audio = path.join(OUT, scene + "_audio.webm");
  fs.writeFileSync(audio, Buffer.from(result.audio || "", "base64"));
  const offsetMs = Math.round(result.audioStart - t0);  // the audio starts this far into the video
  const take = process.env.TAKE ? "_t" + process.env.TAKE : "";
  const out = path.join(OUT, scene + take + ".mp4");
  const filter = offsetMs >= 0 ? "adelay=" + offsetMs + ":all=1,apad" : "atrim=start=" + (-offsetMs / 1000) + ",asetpts=PTS-STARTPTS,apad";
  const mux = spawnSync(FFMPEG, ["-y", "-loglevel", "error", "-i", video, "-i", audio, "-map", "0:v", "-map", "1:a",
    "-af", filter, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", out]);
  if (mux.status !== 0) console.log(String(mux.stderr));
  console.log(scene + take + " (room " + result.room + "): " + result.status + ", " + (n / 25).toFixed(1) + " s, audio offset " + offsetMs + " ms -> " + out);
  return { scene, status: result.status, out };
}

async function main() {
  const arg = process.argv[2] || "correction";
  const all = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../www/demo/scenes.json"), "utf8"));
  const scenes = arg === "all" ? all.order : arg.split(",");
  const done = [];
  for (const s of scenes) done.push(await record(s));
  if (scenes.length > 1) {
    const list = path.join(OUT, "concat.txt");
    fs.writeFileSync(list, done.map((d) => "file '" + d.out.replace(/\\/g, "/") + "'").join("\n"));
    const full = path.join(OUT, "demo_full.mp4");
    const cat = spawnSync(FFMPEG, ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", list, "-c", "copy", full]);
    if (cat.status !== 0) console.log(String(cat.stderr));
    console.log("full film: " + full);
  }
  console.log(done.map((d) => d.scene + ": " + d.status).join("\n"));
}

main().catch((e) => { console.error(e); process.exit(1); });
