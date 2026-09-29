// Joins the recorded scenes into one film: an opening card, every scene of
// app/www/demo/scenes.json (recorded by record_demo.js), a closing card, with
// short fades and loudness-normalised sound.
//
//   node app/tools/build_film.js            -> results/galaxy/video/DUET_for_Galaxy_demo.mp4
"use strict";
const { chromium } = require("playwright-core");
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const FFMPEG = process.env.FFMPEG || "E:/ffmpeg/ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe";
const BASE = process.env.DEMO_URL || "http://localhost:8787";
const OUT = path.resolve(__dirname, "../../results/galaxy/video");
const CARDS = [["intro", 11], ["outro", 9]];

function ff(args) {
  const r = spawnSync(FFMPEG, ["-y", "-loglevel", "error", ...args], { maxBuffer: 1 << 26 });
  if (r.status !== 0) throw new Error(String(r.stderr));
}

async function cards() {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  for (const [kind, seconds] of CARDS) {
    await page.goto(BASE + "/demo/card.html?kind=" + kind);
    await page.waitForTimeout(400);
    const png = path.join(OUT, "card_" + kind + ".png");
    await page.screenshot({ path: png });
    ff(["-loop", "1", "-framerate", "25", "-t", String(seconds), "-i", png,
      "-f", "lavfi", "-t", String(seconds), "-i", "anullsrc=r=48000:cl=stereo",
      "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
      path.join(OUT, "card_" + kind + ".mp4")]);
  }
  await browser.close();
}

function duration(file) {
  const r = spawnSync(FFMPEG, ["-i", file], { encoding: "utf8" });
  const m = /Duration: (\d+):(\d+):([\d.]+)/.exec(r.stderr || "");
  return m ? (+m[1]) * 3600 + (+m[2]) * 60 + parseFloat(m[3]) : 0;
}

async function main() {
  await cards();
  const order = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../www/demo/scenes.json"), "utf8")).order;
  const parts = [path.join(OUT, "card_intro.mp4"), ...order.map((s) => path.join(OUT, s + ".mp4")), path.join(OUT, "card_outro.mp4")]
    .filter((f) => fs.existsSync(f));
  const inputs = [], chains = [], labels = [];
  parts.forEach((f, i) => {
    const d = duration(f);
    inputs.push("-i", f);
    chains.push("[" + i + ":v]fps=25,format=yuv420p,fade=t=in:st=0:d=0.5,fade=t=out:st=" + Math.max(0, d - 0.6).toFixed(2) + ":d=0.6[v" + i + "]");
    chains.push("[" + i + ":a]aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:st=0:d=0.3,afade=t=out:st=" + Math.max(0, d - 0.5).toFixed(2) + ":d=0.5[a" + i + "]");
    labels.push("[v" + i + "][a" + i + "]");
  });
  const graph = chains.join(";") + ";" + labels.join("") + "concat=n=" + parts.length + ":v=1:a=1[v][araw];[araw]loudnorm=I=-16:TP=-1.5:LRA=11[a]";
  const film = path.join(OUT, "DUET_for_Galaxy_demo.mp4");
  ff([...inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
    "-threads", "6", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", film]);
  console.log(parts.map((p) => path.basename(p) + " " + duration(p).toFixed(1) + " s").join("\n"));
  console.log("film: " + film + " (" + duration(film).toFixed(1) + " s)");
}

main().catch((e) => { console.error(e); process.exit(1); });
