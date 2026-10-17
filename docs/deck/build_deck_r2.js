// SRM_SE7EN_Round2.pptx: the Round 2 live-demo deck (DUET for Galaxy). Same look as the Round 1 deck.
// Every number is in N below, from the runs named next to it.
//   node build_deck_r2.js <out.pptx>
const pptxgen = require("pptxgenjs");

const OUT = process.argv[2] || "SRM_SE7EN_Round2.pptx";

// ---- the numbers (update from the latest runs before the demo) ------------------------------------
const N = {
  r1Pass: "0.81",                 // Round 1, FDB-v3 Pass@1, results/reported/gemma4_final_run
  suiteScenarios: 36,             // scripts/galaxy_suite.py
  suiteRuns: "__RUNS__",          // results/galaxy/suite/final_*/summary.json
  suitePassed: "__PASSED__",
  suiteStale: "__STALE__",
  suiteDup: "__DUP__",
  ackP50: "__ACK__",              // seconds from the end of speech
  actionP50: "__ACTION__",
  answerP50: "__ANSWER__",
  fastDecisions: 62, fastWrong: 0, fastHeld: "14/14", fastP50: "0.23",   // scripts/galaxy_fast_eval.py, 17 Oct
  e2eCases: 19,                   // scripts/phone_e2e.py on Android 16
  unitTests: "__TESTS__",
  before: { ack: 1.78, action: 5.6, answer: 14.3 },                         // 30 Sep, the phone app before Round 2
};

// White and Samsung blue (#1428A0), as in Round 1
const C = {
  bg: "FFFFFF", panel: "F5F7FB", panel2: "E9EDF5", line: "D9DFEA", ink: "0F1C3F", muted: "4F5B73",
  faint: "8791A5", accent: "1428A0", accentDim: "E6EBFA", ok: "13804F", okDim: "E4F3EA", bad: "C73A3A",
  badDim: "FBE9E9", hold: "5B3FC4", holdDim: "EEEAFB", warn: "B8680A", warnDim: "FCF1E1", base: "B3BCCC",
  grid: "E7EBF2",
};
const F = "Segoe UI";
const FB = "Segoe UI Semibold";
const MONO = "Consolas";
const W = 13.333;
const TOTAL = 10;

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "Team SE7EN (SRM)";
pptx.company = "SRM Institute of Science and Technology";
pptx.title = "DUET for Galaxy: Round 2";
pptx.subject = "Samsung PRISM GenAI Hackathon 2026-27, Theme 05, Round 2";
const S = pptx.ShapeType;

// --- helpers (as in build_deck.js) ---------------------------------------------------------------
function t(slide, text, x, y, w, h, o = {}) {
  slide.addText(text, { x, y, w, h, fontFace: o.bold ? FB : F, fontSize: 12, color: C.ink, margin: 0, valign: "top",
    paraSpaceAfter: 0, ...o, bold: false });
}
function box(slide, x, y, w, h, o = {}) {
  slide.addShape(S.roundRect, { x, y, w, h, rectRadius: o.r ?? 0.1, fill: { color: o.fill || C.panel },
    line: { color: o.line || C.line, width: o.lw ?? 1, dashType: o.dash || "solid" } });
}
function arrow(slide, x1, y1, x2, y2, o = {}) {
  const opts = { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.max(Math.abs(x2 - x1), 0.0001), h: Math.max(Math.abs(y2 - y1), 0.0001),
    line: { color: o.color || C.muted, width: o.w ?? 1.5, dashType: o.dash || "solid", endArrowType: o.end === false ? undefined : "triangle",
      beginArrowType: o.both ? "triangle" : undefined } };
  if (x2 < x1) opts.flipH = true;
  if (y2 < y1) opts.flipV = true;
  slide.addShape(S.line, opts);
}
function pill(slide, text, x, y, w, h, fill, color, o = {}) {
  slide.addShape(S.roundRect, { x, y, w, h, rectRadius: h / 2, fill: { color: fill }, line: { color: fill, width: 0 } });
  t(slide, text, x, y, w, h, { fontSize: o.size || 10, color, align: "center", valign: "middle", bold: true, charSpacing: o.cs ?? 1 });
}
function frame(slide, n, kicker, title, subtitle) {
  slide.background = { color: C.bg };
  slide.addShape(S.rect, { x: 0, y: 0, w: W, h: 0.06, fill: { color: C.accent }, line: { color: C.accent, width: 0 } });
  t(slide, kicker.toUpperCase(), 0.55, 0.32, 9, 0.26, { fontSize: 10, color: C.accent, bold: true, charSpacing: 3 });
  t(slide, String(n).padStart(2, "0") + " / " + String(TOTAL).padStart(2, "0"), W - 1.55, 0.32, 1.0, 0.26, { fontSize: 10, color: C.faint, align: "right", charSpacing: 2 });
  t(slide, title, 0.55, 0.6, 12.2, 0.62, { fontSize: 26, bold: true, color: C.ink });
  if (subtitle) t(slide, subtitle, 0.55, 1.2, 12.2, 0.4, { fontSize: 13.5, color: C.muted });
  slide.addShape(S.line, { x: 0.55, y: 7.02, w: W - 1.1, h: 0, line: { color: C.line, width: 0.75 } });
  t(slide, "DUET for Galaxy  ·  Team SE7EN, SRM  ·  Samsung PRISM GenAI Hackathon 2026-27  ·  Theme 05  ·  Round 2", 0.55, 7.1, 9, 0.22, { fontSize: 8.5, color: C.faint });
}
function card(slide, x, y, w, h, head, body, color, o = {}) {
  box(slide, x, y, w, h, { fill: o.fill || C.panel, line: o.line || C.line });
  slide.addShape(S.rect, { x, y: y + 0.12, w: 0.06, h: h - 0.24, fill: { color }, line: { color, width: 0 } });
  t(slide, head, x + 0.22, y + 0.12, w - 0.34, 0.3, { fontSize: o.hs || 12, bold: true, color });
  t(slide, body, x + 0.22, y + 0.44, w - 0.34, h - 0.52, { fontSize: o.bs || 10.5, color: C.ink, lineSpacingMultiple: 1.05 });
}
function stat(slide, x, y, w, big, small, color) {
  t(slide, big, x, y, w, 0.62, { fontSize: 34, bold: true, color: color || C.accent });
  t(slide, small, x, y + 0.64, w, 0.5, { fontSize: 10, color: C.muted });
}

// ============================================================================ 1. cover
{
  const B = { bg: "1428A0", ink: "FFFFFF", soft: "D5DDFF", faint: "9FB0F5", card: "1C33B5", cardLine: "3650CC" };
  const s = pptx.addSlide();
  s.background = { color: B.bg };
  s.addShape(S.rect, { x: 0, y: 7.38, w: W, h: 0.12, fill: { color: "FFFFFF" }, line: { color: "FFFFFF", width: 0 } });
  t(s, "SAMSUNG PRISM GENAI HACKATHON 2026-27  ·  THEME 05  ·  ROUND 2  ·  LIVE DEMO", 0.7, 0.55, 11, 0.3, { fontSize: 11, color: B.faint, bold: true, charSpacing: 3 });
  t(s, "DUET for Galaxy", 0.7, 1.05, 8, 1.1, { fontSize: 60, bold: true, color: B.ink, charSpacing: 2 });
  t(s, "Interrupt it, hesitate, change your mind.\nIt acts on what you finally meant: once, and only says what happened.",
    0.72, 2.2, 7.4, 0.9, { fontSize: 17, color: B.soft, lineSpacingMultiple: 1.1 });
  pill(s, "TWO SPEEDS  ·  ONE COORDINATOR  ·  REAL ACTIONS", 0.72, 3.35, 6.0, 0.42, "FFFFFF", B.bg, { size: 11, cs: 2 });

  s.addShape(S.roundRect, { x: 8.35, y: 1.05, w: 4.3, h: 3.0, rectRadius: 0.1, fill: { color: "FFFFFF" }, line: { color: "FFFFFF", width: 0 } });
  t(s, "ON A GALAXY, TODAY", 8.6, 1.22, 3.9, 0.25, { fontSize: 9, color: C.muted, bold: true, charSpacing: 2 });
  const rows = [[N.suiteStale + " · " + N.suiteDup, "stale · repeated actions in " + N.suiteRuns + " spoken test conversations"],
    [N.ackP50 + " s", "to “Sure.” after you stop talking (p50)"],
    [String(N.fastWrong), "wrong actions by the fast mind in " + N.fastDecisions + " decisions"],
    [N.r1Pass, "Round 1: FDB-v3 Pass@1 (GPT-Realtime 0.60)"]];
  rows.forEach(([big, small], i) => {
    t(s, big, 8.6, 1.5 + i * 0.62, 1.35, 0.45, { fontSize: 22, bold: true, color: C.accent });
    t(s, small, 10.0, 1.55 + i * 0.62, 2.55, 0.5, { fontSize: 9.5, color: C.muted });
  });

  const steps = [["HEAR", "words, while it talks"], ["ROUTE", "fast mind or cloud mind"], ["WAIT", "until you're done"],
    ["ACT ONCE", "never on words you took back"], ["CONFIRM", "from the device's own reply"]];
  steps.forEach(([h, b], i) => {
    const x = 0.7 + i * 2.45;
    s.addShape(S.roundRect, { x, y: 4.5, w: 2.15, h: 1.05, rectRadius: 0.1, fill: { color: B.card }, line: { color: B.cardLine, width: 1 } });
    t(s, String(i + 1).padStart(2, "0") + "  " + h, x + 0.18, 4.62, 1.9, 0.3, { fontSize: 13, bold: true, color: B.ink, charSpacing: 2 });
    t(s, b, x + 0.18, 4.95, 1.9, 0.55, { fontSize: 10.5, color: B.soft });
    if (i < steps.length - 1) arrow(s, x + 2.17, 5.02, x + 2.43, 5.02, { color: B.faint, w: 1.25 });
  });
  t(s, [{ text: "Team SE7EN", options: { bold: true, fontFace: FB, color: B.ink, fontSize: 14 } },
    { text: "   ·   SRM Institute of Science and Technology", options: { color: B.soft, fontSize: 13 } }], 0.7, 6.0, 9, 0.35, {});
  t(s, "Tanmay Singh  ·  Panshul Arora  ·  Naman Rai  ·  Devansh Goenka", 0.7, 6.36, 9, 0.3, { fontSize: 13, color: B.ink });
  t(s, "Gemma 4 E2B on our GPU  ·  Gemma 4 26B in the cloud  ·  LiveKit  ·  Whisper  ·  Kokoro  ·  SmartThings  ·  Android", 0.7, 6.8, 11, 0.25, { fontSize: 10, color: B.faint });
}

// ============================================================================ 2. the problem
{
  const s = pptx.addSlide();
  frame(s, 2, "The problem", "Voice assistants act on the first thing you said",
    "People correct themselves mid-sentence. An agent that acts at the end of every phrase does the wrong thing, or both things.");
  box(s, 0.55, 1.8, 7.9, 3.6);
  t(s, "“Set an alarm for 6:30…  no, make it 7.”", 0.8, 1.98, 7.4, 0.4, { fontSize: 17, italic: true, color: C.ink });
  const rows = [["A greedy assistant", "set_alarm(6:30)   then   set_alarm(7:00)", "two alarms: you wake at 6:30", C.bad, C.badDim],
    ["A cautious one", "waits for you to confirm", "slow, and asks what you already said", C.warn, C.warnDim],
    ["DUET", "6:30 never runs  ·  7:00 runs once", "“Sure. Alarm set for 7 in the morning.”  ~1.5 s", C.ok, C.okDim]];
  rows.forEach(([who, what, out, col, dim], i) => {
    const y = 2.6 + i * 0.9;
    box(s, 0.8, y, 7.4, 0.76, { fill: dim, line: col });
    t(s, who, 1.0, y + 0.12, 1.9, 0.3, { fontSize: 12, bold: true, color: col });
    t(s, what, 2.95, y + 0.1, 5.1, 0.3, { fontSize: 11, fontFace: MONO, color: C.ink });
    t(s, out, 2.95, y + 0.42, 5.1, 0.3, { fontSize: 10.5, color: C.muted });
  });
  box(s, 8.75, 1.8, 4.03, 3.6);
  t(s, "SELF-CORRECTIONS, FULL-DUPLEX-BENCH V3", 8.95, 1.95, 3.7, 0.25, { fontSize: 9, bold: true, color: C.muted, charSpacing: 1.5 });
  const bars = [["DUET (Round 1)", 0.88, C.accent], ["GPT-Realtime", 0.588, C.base], ["Gemini Live 3.1", 0.353, C.base], ["Whisper → GPT-4o", 0.176, C.base]];
  bars.forEach(([n, v, col], i) => {
    const y = 2.45 + i * 0.55;
    t(s, n, 8.95, y, 1.55, 0.26, { fontSize: 9.5, color: i ? C.muted : C.ink, bold: !i, align: "right", valign: "middle" });
    s.addShape(S.rect, { x: 10.6, y: y + 0.03, w: 1.6, h: 0.2, fill: { color: C.panel2 }, line: { color: C.panel2, width: 0 } });
    s.addShape(S.rect, { x: 10.6, y: y + 0.03, w: 1.6 * v, h: 0.2, fill: { color: col }, line: { color: col, width: 0 } });
    t(s, v.toFixed(2), 12.25, y, 0.5, 0.26, { fontSize: 10, bold: true, color: i ? C.ink : C.accent, valign: "middle" });
  });
  t(s, "Strict Pass@1 on the benchmark's self-correction items. Baselines from the FDB-v3 paper: “models commit intermediate parameters before the correction arrives.”",
    8.95, 4.7, 3.7, 0.6, { fontSize: 8, color: C.faint });
  const asks = [["WHO NEEDS IT", "Anyone whose hands or eyes are busy: driving, cooking, an older person living alone.", C.accent],
    ["WHAT GOES WRONG TODAY", "Stale actions, duplicate actions, claims of “done” that did not happen, silence while it thinks.", C.bad],
    ["WHAT DUET CHANGES", "Acting is gated by deterministic code, not by the model's timing. The model can be wrong about when; the user never pays for it.", C.ok]];
  asks.forEach(([h, b, col], i) => card(s, 0.55 + i * 4.13, 5.62, 3.95, 1.25, h, b, col, { bs: 10.5 }));
}

// ============================================================================ 3. architecture
{
  const s = pptx.addSlide();
  frame(s, 3, "Architecture", "The phone is the ears, mouth and hands; two minds share one coordinator",
    "What runs where in today's demo. Every arrow into the phone or the home passes the coordinator first.");
  // phone
  box(s, 0.55, 1.85, 3.2, 4.9, { fill: C.panel });
  t(s, "GALAXY S24 · DUET APP", 0.75, 2.0, 2.9, 0.25, { fontSize: 10, bold: true, color: C.accent, charSpacing: 2 });
  ["Microphone + speaker (echo cancelled)", "Live timeline: what it heard, planned, held back, did, and where it ran",
    "Hands: DUET alarms, timers, Maps, calls, texts, settings, battery, apps, torch, media, volume, calendar, contacts",
    "Keeps listening with Google Maps in front"].forEach((x, i) => {
    box(s, 0.75, 2.4 + i * 1.03, 2.8, 0.9, { fill: "FFFFFF" });
    t(s, x, 0.9, 2.47 + i * 1.03, 2.55, 0.8, { fontSize: 10, color: C.ink });
  });
  // laptop
  box(s, 4.35, 1.85, 4.6, 4.9, { fill: C.accentDim, line: C.accent });
  t(s, "OUR EDGE MACHINE (LAPTOP, RTX 3060)", 4.55, 2.0, 4.3, 0.25, { fontSize: 10, bold: true, color: C.accent, charSpacing: 2 });
  const L = [["Ears", "Silero VAD · Whisper large-v3-turbo · LiveKit turn detector", C.muted],
    ["Fast mind", "Gemma 4 E2B on the GPU (llama.cpp): simple phone actions, decided in about 0.2 s", C.ok],
    ["Coordinator", "epochs · commit gate · ledger · failure policy · guards", C.hold],
    ["Mouth", "Kokoro-82M; fixed lines pre-rendered", C.muted]];
  L.forEach(([h, b, col], i) => card(s, 4.55, 2.4 + i * 1.03, 4.2, 0.9, h, b, col, { bs: 9.5, hs: 11, fill: "FFFFFF" }));
  // cloud
  box(s, 9.55, 1.85, 3.23, 4.9, { fill: C.panel });
  t(s, "CLOUD", 9.75, 2.0, 2.9, 0.25, { fontSize: 10, bold: true, color: C.accent, charSpacing: 2 });
  [["Cloud mind", "Gemma 4 26B (Google API): questions, explanations, the weather, the web, care", C.accent],
    ["SmartThings", "the real home: 7 devices, live in Samsung's app", C.warn],
    ["LiveKit Cloud", "WebRTC audio and the phone's tool calls (RPC)", C.muted]].forEach(([h, b, col], i) =>
    card(s, 9.75, 2.4 + i * 1.37, 2.85, 1.22, h, b, col, { bs: 9.5, hs: 11, fill: "FFFFFF" }));
  arrow(s, 3.75, 4.3, 4.35, 4.3, { both: true, color: C.accent });
  arrow(s, 8.95, 3.0, 9.55, 3.0, { both: true, color: C.accent });
  arrow(s, 8.95, 4.4, 9.55, 4.4, { both: true, color: C.warn });
  t(s, "Simple actions never wait for the cloud; questions never wait for the phone. Models are configuration; the coordinator is the product.",
    0.55, 6.82, 12.2, 0.2, { fontSize: 9, italic: true, color: C.muted });
}

// ============================================================================ 4. two speeds
{
  const s = pptx.addSlide();
  frame(s, 4, "Two speeds", "A small Gemma acts in about a second, and hands over anything unclear",
    "An on-device and cloud split, as today's assistants use, with one guarantee for both: code checks every plan before it runs.");
  const flow = [["Your words", C.muted, 0.55], ["Fast mind\nGemma 4 E2B", C.ok, 2.75], ["Checked\nby code", C.hold, 4.95], ["Coordinator", C.hold, 7.15], ["Phone · home", C.accent, 9.35]];
  flow.forEach(([x, col, xx]) => { box(s, xx, 1.9, 1.85, 0.95, { fill: "FFFFFF", line: col, lw: 1.5 }); t(s, x, xx, 1.9, 1.85, 0.95, { fontSize: 12, bold: true, color: col, align: "center", valign: "middle" }); });
  for (let i = 0; i < 4; i++) arrow(s, 0.55 + 1.85 + i * 2.2, 2.37, 0.55 + 2.2 * (i + 1), 2.37, { color: C.muted });
  box(s, 11.55, 1.9, 1.23, 0.95, { fill: C.okDim, line: C.ok });
  t(s, "“Sure.\nTorch on.”", 11.55, 1.9, 1.23, 0.95, { fontSize: 10.5, bold: true, color: C.ok, align: "center", valign: "middle" });
  arrow(s, 11.2, 2.37, 11.55, 2.37, { color: C.ok });
  box(s, 4.95, 3.15, 4.05, 0.85, { fill: C.accentDim, line: C.accent });
  t(s, "Anything else → the cloud mind, Gemma 4 26B,\ntold what the fast mind already did", 5.1, 3.2, 3.8, 0.75, { fontSize: 10.5, color: C.accent, valign: "middle" });
  arrow(s, 3.67, 2.85, 4.95, 3.55, { color: C.accent, dash: "dash" });
  const rules = [["Named in your words", "“open the shopping apps” is not open_app(apps); “dentist at 4” is not an alarm"],
    ["Values you said", "a contact, a place, a number the user never said goes to the cloud mind"],
    ["The last value said", "“3:30, no, cancel that, 430 instead” must plan 4:30"],
    ["Not a question", "“Is the AC on?” is answered, never switched on"],
    ["Nothing dropped", "“…actually never mind” is the cloud mind's to judge"]];
  t(s, "THE FAST MIND MAY ACT ONLY WHEN", 0.55, 4.25, 6, 0.25, { fontSize: 10, bold: true, color: C.hold, charSpacing: 2 });
  rules.forEach(([h, b], i) => {
    t(s, "✓ " + h, 0.55, 4.6 + i * 0.44, 2.6, 0.3, { fontSize: 11, bold: true, color: C.ink });
    t(s, b, 3.15, 4.62 + i * 0.44, 5.6, 0.3, { fontSize: 10, color: C.muted });
  });
  box(s, 9.1, 4.25, 3.68, 2.55, { fill: C.panel });
  t(s, "THE FAST MIND ALONE", 9.3, 4.38, 3.3, 0.25, { fontSize: 9, bold: true, color: C.muted, charSpacing: 2 });
  stat(s, 9.3, 4.68, 1.6, String(N.fastWrong), "wrong actions in " + N.fastDecisions + " decisions", C.ok);
  stat(s, 11.0, 4.68, 1.7, N.fastP50 + " s", "to decide (p50)", C.accent);
  t(s, "Held-out sentences: " + N.fastHeld + ". A plan that fails a check costs a second, never a wrong action.", 9.3, 5.95, 3.3, 0.7, { fontSize: 9.5, color: C.muted });
}

// ============================================================================ 5. coordinator and guards
{
  const s = pptx.addSlide();
  frame(s, 5, "The coordinator and its guards", "Plan early, commit late, act once: whichever mind made the plan",
    "Deterministic, about 250 lines, covered by unit tests. The same code passed Round 1's benchmark.");
  const c = [["Epochs", "Every new word voids older plans, even after the turn has closed. A cough only delays them.", C.hold],
    ["Commit gate", "An action waits until you have been quiet 1.1 s (1.8 s after “no wait”, 2.2 s after an open sentence). Thinking starts at once; only acting waits.", C.hold],
    ["Idempotency ledger", "The same action never runs twice, even if you barge in while it runs.", C.hold],
    ["Failure policy", "A failed reading is retried once; a failed or unconfirmed change is never re-sent, and you are told.", C.hold]];
  c.forEach(([h, b, col], i) => card(s, 0.55 + (i % 2) * 6.17, 1.8 + Math.floor(i / 2) * 1.32, 5.98, 1.18, h, b, col, { bs: 10.5 }));
  t(s, "GUARDS ADDED FOR THE PHONE: THEY HOLD WHATEVER A MODEL DECIDES", 0.55, 4.55, 12, 0.25, { fontSize: 10, bold: true, color: C.accent, charSpacing: 2 });
  const g = [["“Call my…”", "cut off mid-phrase: nothing is done; DUET asks for the rest"],
    ["“6:30, or maybe 7, hmm”", "undecided: nothing is done; DUET asks which"],
    ["“Set an alarm for 6:30” at 11:50 pm", "a time without AM/PM means the next one: 6:30 tomorrow morning"],
    ["“Turn off the light” (two lights)", "a name that fits two devices is asked about, never guessed"],
    ["A request the user dropped", "never acted on by the fast mind; the cloud mind judges what is left"]];
  g.forEach(([h, b], i) => {
    box(s, 0.55, 4.9 + i * 0.41, 12.23, 0.36, { fill: i % 2 ? "FFFFFF" : C.panel, line: C.grid });
    t(s, h, 0.75, 4.95 + i * 0.41, 3.9, 0.28, { fontSize: 10.5, bold: true, color: C.ink, valign: "middle" });
    t(s, b, 4.7, 4.95 + i * 0.41, 7.9, 0.28, { fontSize: 10.5, color: C.muted, valign: "middle" });
  });
}

// ============================================================================ 6. real on the phone
{
  const s = pptx.addSlide();
  frame(s, 6, "Real on a Galaxy", "Android's public interfaces, a real SmartThings home, and the world",
    "Three modes: Assistant for every Galaxy owner, Care for an older person living alone, Drive for eyes on the road.");
  const items = [["Alarms", "DUET's own: exact set, cancel, list; ring; on the lock screen", "real"],
    ["Timers", "the Clock app", "real"], ["Battery & heat", "charge, drain, temperature, health, screen, apps used", "real"],
    ["Brightness & timeout", "changed on request, never unasked", "real"], ["Apps & settings", "open, count, the right settings screen", "real"],
    ["Maps", "navigation; DUET keeps listening behind it", "real"], ["Calls & texts", "your contacts; direct or with one tap", "real"],
    ["Torch · media · volume", "on, off, play, pause, skip, set", "real"], ["Calendar", "events in the phone's calendar", "real"],
    ["SmartThings", "lights, AC, TV, lock, vacuum, washer: live in Samsung's app", "real API"],
    ["Weather", "Open-Meteo, any town, the next 6 days", "real"], ["Web answers", "Gemma with Google Search grounding, with sources", "real"],
    ["Medicines", "schedule and double-dose guard (Care)", "on-phone data"], ["The car", "trip and climate (Drive)", "simulated, labelled"]];
  items.forEach(([h, b, tag], i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.55 + col * 6.17, y = 1.8 + row * 0.7;
    box(s, x, y, 5.98, 0.6, { fill: "FFFFFF", line: C.grid });
    t(s, h, x + 0.18, y + 0.08, 1.95, 0.45, { fontSize: 11.5, bold: true, color: C.ink, valign: "middle" });
    t(s, b, x + 2.15, y + 0.08, 2.75, 0.45, { fontSize: 9.5, color: C.muted, valign: "middle" });
    const fill = tag === "real" ? C.okDim : tag === "real API" ? C.warnDim : C.panel2;
    const ink = tag === "real" ? C.ok : tag === "real API" ? C.warn : C.muted;
    pill(s, tag.toUpperCase(), x + 4.95, y + 0.17, 0.92, 0.26, fill, ink, { size: 7.5, cs: 0.5 });
  });
}

// ============================================================================ 7. honest by design
{
  const s = pptx.addSlide();
  frame(s, 7, "Honest by design", "It never says something is done before the device says so",
    "Each rule below exists because a test caught DUET, or Android, doing otherwise.");
  const r = [["Confirmed from the device's reply", "“Alarm set for 7 in the morning.” is built from Android's own answer, with no model call, the moment the action is done.", C.ok],
    ["Sent is not done", "A SmartThings change counts only when the device reports its new state; otherwise DUET says it was sent, not done.", C.ok],
    ["Unknown stays unknown", "If the phone never answers, the action is never re-sent and DUET says it may not have gone through. (A test caught “I've set a timer” here.)", C.bad],
    ["No cloud? Said at once", "After 2 s of not reaching Google, DUET says so; simple actions keep working on the fast mind. A drill button cuts the cloud live.", C.bad],
    ["Android's limits, said plainly", "Behind Google Maps, Android blocks opening other apps unless DUET may appear on top: DUET says what to allow instead of pretending.", C.warn],
    ["Its own alarms", "Google's Clock ignored a cancel request and Android lets no app check the Clock's alarms. So DUET keeps exact alarms of its own.", C.warn]];
  r.forEach(([h, b, col], i) => card(s, 0.55 + (i % 2) * 6.17, 1.8 + Math.floor(i / 2) * 1.68, 5.98, 1.52, h, b, col, { bs: 10.5 }));
}

// ============================================================================ 8. tested
{
  const s = pptx.addSlide();
  frame(s, 8, "How we know it works", "Four kinds of tests, run again after every fix",
    "Spoken scenarios have three kinds of expectations: what DUET must do, must never do, and must never claim.");
  const k = [[String(N.unitTests), "unit tests", "every rule: the gate, the ledger, the guards, the routing, the key pool"],
    [String(N.suiteScenarios), "spoken scenarios", "the Galaxy Correction Suite: corrections, wavering, barge-in, noise, drills"],
    [String(N.e2eCases), "end-to-end cases", "the real app on Android 16, also behind Google Maps and with the cloud cut"],
    [String(N.fastDecisions), "fast-mind decisions", "with held-out sentences written after its checks"]];
  k.forEach(([big, h, b], i) => {
    const x = 0.55 + i * 3.1;
    box(s, x, 1.8, 2.92, 1.75, { fill: C.panel });
    t(s, big, x + 0.2, 1.9, 2.5, 0.65, { fontSize: 34, bold: true, color: C.accent });
    t(s, h, x + 0.2, 2.55, 2.5, 0.3, { fontSize: 12, bold: true, color: C.ink });
    t(s, b, x + 0.2, 2.85, 2.55, 0.65, { fontSize: 9.5, color: C.muted });
  });
  box(s, 0.55, 3.75, 5.6, 3.1, { fill: C.okDim, line: C.ok });
  t(s, "THE LATEST FULL RUN", 0.75, 3.88, 5.2, 0.25, { fontSize: 10, bold: true, color: C.ok, charSpacing: 2 });
  stat(s, 0.75, 4.2, 2.6, N.suitePassed + "/" + N.suiteRuns, "spoken conversations passed", C.ok);
  stat(s, 3.4, 4.2, 2.6, N.suiteStale + " · " + N.suiteDup, "stale · duplicate actions", C.ok);
  t(s, "Fast mind on our GPU, cloud mind Gemma 4 26B, a simulated phone answering exactly as the app does, the real SmartThings home.",
    0.75, 5.5, 5.2, 1.0, { fontSize: 10, color: C.ink });
  box(s, 6.35, 3.75, 6.43, 3.1, { fill: C.panel });
  t(s, "BUGS OUR TESTS CAUGHT BEFORE ANY JURY DID", 6.55, 3.88, 6.0, 0.25, { fontSize: 10, bold: true, color: C.bad, charSpacing: 1.5 });
  ["“Garage door” matched the front door lock", "“Turn on the AC at 22” left it at 24",
    "A timer the phone never confirmed was later called “set”", "A blocked call was retried eight times",
    "“Call my” … “my daughter” lost the word “call”", "A dropped alarm was set on a second look",
    "The Clock app ignored a cancel; Android blocked actions behind Maps"].forEach((x, i) =>
    t(s, "✗→✓  " + x, 6.55, 4.25 + i * 0.36, 6.1, 0.3, { fontSize: 10.5, color: C.ink }));
}

// ============================================================================ 9. speed
{
  const s = pptx.addSlide();
  frame(s, 9, "Speed", "No dead air: something useful within a second",
    "Seconds from the end of the user's speech, p50. Before: the phone app on 30 Sep (cloud mind only). Now: two speeds, the latest suite.");
  const rows = [["“Sure.” / “One moment.”", N.before.ack, Number(N.ackP50)], ["Action done", N.before.action, Number(N.actionP50)],
    ["Answer ready", N.before.answer, Number(N.answerP50)]];
  const max = 15, x0 = 3.6, bw = 7.6;
  rows.forEach(([label, before, now], i) => {
    const y = 2.05 + i * 1.35;
    t(s, label, 0.55, y + 0.15, 2.9, 0.4, { fontSize: 13, bold: true, color: C.ink, align: "right" });
    s.addShape(S.rect, { x: x0, y, w: bw * before / max, h: 0.4, fill: { color: C.base }, line: { color: C.base, width: 0 } });
    t(s, before.toFixed(1) + " s  before", x0 + bw * before / max + 0.1, y + 0.05, 2.0, 0.3, { fontSize: 11, color: C.muted });
    s.addShape(S.rect, { x: x0, y: y + 0.48, w: Math.max(bw * now / max, 0.05), h: 0.4, fill: { color: C.accent }, line: { color: C.accent, width: 0 } });
    t(s, (isNaN(now) ? "?" : now.toFixed(1)) + " s  now", x0 + bw * now / max + 0.1, y + 0.53, 2.0, 0.3, { fontSize: 11, bold: true, color: C.accent });
  });
  const why = [["Fast mind first", "simple actions never wait for the cloud"], ["Minimal thinking", "the cloud mind answers without a long reasoning step"],
    ["Acknowledged by route", "“Sure.” the moment the fast mind acts; “One moment.” the moment the cloud takes over"],
    ["Confirmed from the device", "no second model call to say “done”"]];
  why.forEach(([h, b], i) => card(s, 0.55 + i * 3.1, 5.95, 2.92, 0.95, h, b, C.accent, { bs: 9, hs: 10.5 }));
}

// ============================================================================ 10. worklet
{
  const s = pptx.addSlide();
  frame(s, 10, "From prototype to PRISM worklet", "The same split moves onto the phone; the coordinator moves with it",
    "Today the brain runs on our edge machine and Google's cloud. The design was made to fit on the device.");
  const steps = [["Today", "Laptop edge: ears, fast mind, coordinator. Phone: the hands. Cloud: depth.", C.muted],
    ["Next: on the phone", "Kotlin coordinator (a 1:1 port, the same tests); Gemma 4 E2B on-device with Google's LiteRT-LM; on-device speech; the cloud only for depth", C.accent],
    ["With Samsung", "Bind to Bixby's device agents, AppFunctions and SmartThings directly; Samsung Clock's own API; the side button", C.ok]];
  steps.forEach(([h, b, col], i) => {
    card(s, 0.55 + i * 4.13, 1.85, 3.95, 1.9, h, b, col, { bs: 10.5 });
    if (i < 2) arrow(s, 0.55 + i * 4.13 + 3.95, 2.8, 0.55 + (i + 1) * 4.13, 2.8, { color: C.accent });
  });
  card(s, 0.55, 4.0, 6.0, 2.1, "What it is not, yet", "The phone app's brain runs on a laptop today (the phone is ears, mouth and hands).\nThe car is simulated. English only.\nNo payments, no acting inside other apps (Android's rules), no emergency calls: it says so.", C.bad, { bs: 10.5 });
  card(s, 6.78, 4.0, 6.0, 2.1, "Why it matters for Samsung", "Any assistant that acts (Bixby's agents, Car-to-Home, an AI contact centre) needs the same guarantees: never act on a corrected value, never twice, never claim before it happened.\nDUET is that layer: models are configuration, the coordinator is the product.", C.ok, { bs: 10.5 });
  box(s, 0.55, 6.3, 12.23, 0.55, { fill: C.accentDim, line: C.accent });
  t(s, "Thank you. Now please interrupt it yourselves.", 0.55, 6.3, 12.23, 0.55, { fontSize: 15, bold: true, color: C.accent, align: "center", valign: "middle" });
}

pptx.writeFile({ fileName: OUT }).then((f) => console.log("wrote " + f));
