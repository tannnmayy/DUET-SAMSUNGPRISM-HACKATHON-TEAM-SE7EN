// Records the film's 30-second opening (app/www/demo/intro.html) to
// results/galaxy/video/intro.mp4, with the two voice lines laid under it at the times
// the page shows them (scripts/galaxy_demo_voices.py makes demo/audio/intro_*.wav).
//
//   node app/tools/record_intro.js 0.84        # DUET's Pass@1, shown in the last beat
const fs = require("fs");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const { chromium } = require("playwright-core");

const FFMPEG = process.env.FFMPEG || "E:/ffmpeg/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe";
const WWW = path.resolve(__dirname, "../www");
const OUT = path.resolve(__dirname, "../../results/galaxy/video");
const LINES = [[1.0, "intro_0.wav"], [3.9, "intro_1.wav"]];   // seconds into the clip (intro.html: T.lineA, T.lineB)
const LENGTH = 30.5;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const pass = process.argv[2] || "";
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--hide-scrollbars"] });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: "dark" });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  let latest = null;
  cdp.on("Page.screencastFrame", async (f) => {
    latest = Buffer.from(f.data, "base64");
    try { await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }); } catch (e) { /* closing */ }
  });
  const video = path.join(OUT, "intro_video.mp4");
  const ff = spawn(FFMPEG, ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", "25", "-c:v", "mjpeg", "-i", "-",
    "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", video]);
  const url = "file:///" + path.join(WWW, "demo", "intro.html").replace(/\\/g, "/") + "?wait=1" +
    (pass ? "&pass=" + encodeURIComponent(pass) : "");
  await page.goto(url);
  await page.waitForTimeout(500);                       // fonts and layout settle
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 95, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
  while (!latest) await sleep(20);
  const t0 = Date.now();
  await page.evaluate(() => window.start());
  let n = 0, done = false;
  const pump = (async () => {
    while (!done) {
      const due = Math.floor((Date.now() - t0) / 40);
      while (n <= due) { ff.stdin.write(latest); n++; }
      await sleep(8);
    }
  })();
  await sleep(LENGTH * 1000);
  done = true;
  await pump;
  ff.stdin.end();
  await new Promise((r) => ff.on("close", r));
  await browser.close();

  const out = path.join(OUT, "intro.mp4");
  const inputs = ["-i", video];
  const parts = [];
  LINES.forEach(([t, file], i) => {
    inputs.push("-i", path.join(WWW, "demo", "audio", file));
    parts.push(`[${i + 1}]aresample=48000,adelay=${Math.round(t * 1000)}:all=1[a${i}]`);
  });
  const mix = parts.join(";") + ";" + LINES.map((_, i) => `[a${i}]`).join("") +
    `amix=inputs=${LINES.length}:normalize=0,apad[a]`;
  const res = spawnSync(FFMPEG, ["-y", "-loglevel", "error", ...inputs, "-filter_complex", mix, "-map", "0:v", "-map", "[a]",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", out]);
  if (res.status !== 0) { console.log(String(res.stderr)); process.exit(1); }
  console.log(`intro: ${(n / 25).toFixed(1)} s -> ${out}`);
})().catch((e) => { console.error(e); process.exit(1); });
