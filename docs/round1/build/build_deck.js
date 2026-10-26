// SRM_SE7EN.pptx: DUET's submission deck (8 slides, Samsung's limit). Every number is from the
// reported run (results/reported/gemma4_final_run) or the FDB-v3 paper, as the docs state them.
//   node build_deck.js <out.pptx> <assets dir>
const path = require("path");
const pptxgen = require("pptxgenjs");

const OUT = process.argv[2] || "SRM_SE7EN.pptx";
const ASSETS = process.argv[3] || path.join(__dirname, "assets");

// White and Samsung blue (#1428A0). Status colours are darkened so they read on white.
const C = {
  bg: "FFFFFF", panel: "F5F7FB", panel2: "E9EDF5", line: "D9DFEA", ink: "0F1C3F", muted: "4F5B73",
  faint: "8791A5", accent: "1428A0", accentDim: "E6EBFA", ok: "13804F", okDim: "E4F3EA", bad: "C73A3A",
  badDim: "FBE9E9", hold: "5B3FC4", holdDim: "EEEAFB", warn: "B8680A", warnDim: "FCF1E1",
  pillText: "1428A0", base: "B3BCCC", grid: "E7EBF2", coordFill: "EEF2FF", band: "EEF2FF", code: "F1F4FA",
  duetRow: "EEF2FF", blue2: "2F5BE8",
};
const F = "Segoe UI";
const FB = "Segoe UI Semibold";
const W = 13.333;

const pptx = new pptxgen();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "Team SE7EN (SRM)";
pptx.company = "SRM Institute of Science and Technology";
pptx.title = "DUET: a full-duplex voice agent that acts on what you mean";
pptx.subject = "Samsung PRISM GenAI Hackathon 2026-27, Theme 05";
const S = pptx.ShapeType;
const TOTAL = 8;

// --- helpers -------------------------------------------------------------------------------
function t(slide, text, x, y, w, h, o = {}) {
  slide.addText(text, {
    x, y, w, h, fontFace: o.bold ? FB : F, fontSize: 12, color: C.ink, margin: 0, valign: "top",
    paraSpaceAfter: 0, ...o, bold: false,
  });
}
function box(slide, x, y, w, h, o = {}) {
  slide.addShape(S.roundRect, {
    x, y, w, h, rectRadius: o.r ?? 0.1, fill: { color: o.fill || C.panel },
    line: { color: o.line || C.line, width: o.lw ?? 1, dashType: o.dash || "solid" },
    shadow: o.shadow ? { type: "outer", blur: 8, offset: 1.5, angle: 90, color: "1428A0", opacity: 0.10 } : undefined,
  });
}
function line(slide, x1, y1, x2, y2, o = {}) {
  const opts = {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.max(Math.abs(x2 - x1), 0.0001), h: Math.max(Math.abs(y2 - y1), 0.0001),
    line: { color: o.color || C.muted, width: o.w ?? 1.5, dashType: o.dash || "solid",
      endArrowType: o.end === false ? undefined : (o.end || "triangle"), beginArrowType: o.begin },
  };
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
  t(slide, String(n).padStart(2, "0") + " / 0" + TOTAL, W - 1.55, 0.32, 1.0, 0.26, { fontSize: 10, color: C.faint, align: "right", charSpacing: 2 });
  t(slide, title, 0.55, 0.6, 12.2, 0.62, { fontSize: 27, bold: true, color: C.ink });
  if (subtitle) t(slide, subtitle, 0.55, 1.2, 12.2, 0.4, { fontSize: 14, color: C.muted });
  slide.addShape(S.line, { x: 0.55, y: 7.02, w: W - 1.1, h: 0, line: { color: C.line, width: 0.75 } });
  t(slide, "DUET  ·  Team SE7EN, SRM (SRM_SE7EN)  ·  Samsung PRISM GenAI Hackathon 2026-27  ·  Theme 05", 0.55, 7.1, 8, 0.22, { fontSize: 8.5, color: C.faint });
  t(slide, "github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN", W - 5.55, 7.1, 5.0, 0.22, { fontSize: 8.5, color: C.faint, align: "right" });
}
function card(slide, x, y, w, h, head, body, color, o = {}) {
  box(slide, x, y, w, h, { fill: o.fill || C.panel, line: o.line || C.line });
  slide.addShape(S.rect, { x: x, y: y + 0.12, w: 0.06, h: h - 0.24, fill: { color }, line: { color, width: 0 } });
  t(slide, head, x + 0.22, y + 0.12, w - 0.34, 0.3, { fontSize: o.hs || 12, bold: true, color });
  t(slide, body, x + 0.22, y + 0.44, w - 0.34, h - 0.52, { fontSize: o.bs || 10.5, color: C.ink, lineSpacingMultiple: 1.05 });
}
function hbar(slide, label, value, max, x, y, w, color, o = {}) {
  const lw = o.lw ?? 1.9, bw = w - lw - 0.62;
  t(slide, label, x, y, lw - 0.1, 0.28, { fontSize: o.fs || 10.5, color: o.labelColor || C.muted, valign: "middle", align: "right" });
  slide.addShape(S.rect, { x: x + lw, y: y + 0.04, w: bw, h: 0.2, fill: { color: C.panel2 }, line: { color: C.panel2, width: 0 } });
  slide.addShape(S.rect, { x: x + lw, y: y + 0.04, w: Math.max(bw * value / max, 0.02), h: 0.2, fill: { color }, line: { color, width: 0 } });
  t(slide, o.text || value.toFixed(2), x + lw + bw + 0.08, y, 0.6, 0.28, { fontSize: o.fs || 10.5, bold: true, color: o.valueColor || C.ink, valign: "middle" });
}

// ============================================================================ 1. title
{
  // the cover: solid Samsung blue, white type
  const B = { bg: "1428A0", ink: "FFFFFF", soft: "D5DDFF", faint: "9FB0F5", card: "1C33B5", cardLine: "3650CC" };
  const s = pptx.addSlide();
  s.background = { color: B.bg };
  s.addShape(S.rect, { x: 0, y: 7.38, w: W, h: 0.12, fill: { color: "FFFFFF" }, line: { color: "FFFFFF", width: 0 } });
  t(s, "SAMSUNG PRISM GENAI HACKATHON 2026-27  ·  THEME 05  ·  INTERRUPTIBLE REAL-TIME AGENTS", 0.7, 0.55, 11, 0.3, { fontSize: 11, color: B.faint, bold: true, charSpacing: 3 });
  t(s, "DUET", 0.7, 1.05, 6, 1.25, { fontSize: 80, bold: true, color: B.ink, charSpacing: 6 });
  t(s, "A full-duplex voice agent that acts on what you mean,\nnot on what you said first.", 0.72, 2.35, 7.2, 0.9, { fontSize: 20, color: B.soft, lineSpacingMultiple: 1.1 });
  pill(s, "PLANS EARLY  ·  COMMITS LATE  ·  ACTS ONCE", 0.72, 3.45, 4.6, 0.42, "FFFFFF", B.bg, { size: 11, cs: 2 });

  // hero result, on a white card
  s.addShape(S.roundRect, { x: 8.35, y: 1.05, w: 4.3, h: 3.0, rectRadius: 0.1, fill: { color: "FFFFFF" }, line: { color: "FFFFFF", width: 0 },
    shadow: { type: "outer", blur: 14, offset: 3, angle: 90, color: "0A1250", opacity: 0.35 } });
  t(s, "FDB-V3 · ALL 100 REAL RECORDINGS", 8.6, 1.25, 3.9, 0.25, { fontSize: 9, color: C.muted, bold: true, charSpacing: 2 });
  t(s, "0.81", 8.6, 1.5, 2.6, 1.15, { fontSize: 66, bold: true, color: C.accent });
  t(s, "Pass@1, strict:\nevery tool, every\nargument, no extras", 10.75, 1.72, 1.8, 0.9, { fontSize: 10.5, color: C.muted });
  hbar(s, "DUET", 0.81, 1, 8.6, 2.78, 3.9, C.accent, { lw: 1.35, labelColor: C.ink });
  hbar(s, "GPT-Realtime", 0.6, 1, 8.6, 3.1, 3.9, C.base, { lw: 1.35 });
  hbar(s, "Gemini Live 3.1", 0.54, 1, 8.6, 3.42, 3.9, C.base, { lw: 1.35 });
  t(s, "Baselines: FDB-v3 paper. DUET: our reported run.", 8.6, 3.72, 3.9, 0.22, { fontSize: 8.5, color: C.faint });

  // the one-line story: five steps
  const steps = [["HEAR", "every word advances\nthe epoch"], ["WAIT", "turn closed and the\nuser quiet for a hold"],
    ["CHECK", "a plan made on changed\nwords is dropped"], ["ACT ONCE", "the ledger never runs\nan action twice"],
    ["ANSWER", "spoken from the tool\nresults, never guessed"]];
  steps.forEach(([h, b], i) => {
    const x = 0.7 + i * 2.45;
    s.addShape(S.roundRect, { x, y: 4.5, w: 2.15, h: 1.15, rectRadius: 0.1, fill: { color: B.card }, line: { color: B.cardLine, width: 1 } });
    t(s, String(i + 1).padStart(2, "0") + "  " + h, x + 0.18, 4.62, 1.9, 0.3, { fontSize: 13, bold: true, color: B.ink, charSpacing: 2 });
    t(s, b, x + 0.18, 4.95, 1.9, 0.65, { fontSize: 10.5, color: B.soft });
    if (i < steps.length - 1) line(s, x + 2.17, 5.07, x + 2.43, 5.07, { color: B.faint, w: 1.25 });
  });

  // team
  t(s, [
    { text: "Team SE7EN", options: { bold: true, fontFace: FB, color: B.ink, fontSize: 14 } },
    { text: "   ·   SRM Institute of Science and Technology   ·   SRM_SE7EN", options: { color: B.soft, fontSize: 13 } },
  ], 0.7, 6.0, 9, 0.35, {});
  t(s, "Tanmay Singh  ·  Panshul Arora  ·  Naman Rai  ·  Devansh Goenka", 0.7, 6.36, 9, 0.3, { fontSize: 13, color: B.ink });
  t(s, "Gemma 4 26B-A4B · LiveKit Agents · faster-whisper · Kokoro-82M · one command to reproduce", 0.7, 6.8, 7.2, 0.25, { fontSize: 10, color: B.faint });
  t(s, "github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN", 7.6, 6.8, 5.05, 0.25, { fontSize: 10, color: B.faint, align: "right" });
}

// ============================================================================ 2. problem
{
  const s = pptx.addSlide();
  frame(s, 2, "The problem, in our words", "The failure isn't hearing the correction. It's acting before it arrives.",
    "One real Full-Duplex-Bench recording (travel_19), moment by moment. Times are from DUET's own trace of the reported run.");
  const mono = "Consolas";
  box(s, 0.55, 1.75, 8.5, 3.78, { fill: C.panel, line: C.line });
  const cx0 = 2.2, cw = 1.36, gx = 0.08, ow = 1.02;
  const colX = (i) => cx0 + i * (cw + gx);
  const outX = colX(4);
  ["3.0 s", "6.4 s", "10.7 s", "13.4 s"].forEach((m, i) =>
    t(s, m, colX(i), 1.86, cw, 0.24, { fontSize: 9.5, bold: true, color: C.accent, align: "center", charSpacing: 1 }));
  t(s, "OUTCOME", outX, 1.86, ow, 0.24, { fontSize: 9, bold: true, color: C.muted, align: "center", charSpacing: 2 });
  s.addShape(S.line, { x: cx0, y: 2.13, w: outX - cx0 - 0.08, h: 0, line: { color: C.accent, width: 1, endArrowType: "triangle" } });

  const rowLabel = (y, h, head, sub, col) => {
    s.addShape(S.rect, { x: 0.68, y: y + 0.1, w: 0.05, h: h - 0.2, fill: { color: col }, line: { color: col, width: 0 } });
    t(s, head, 0.82, y + 0.1, 1.33, 0.3, { fontSize: 11.5, bold: true, color: C.ink });
    t(s, sub, 0.82, y + 0.4, 1.3, h - 0.45, { fontSize: 8.5, color: C.muted });
  };
  const cell = (i, y, h, fill, line, dash) => s.addShape(S.roundRect, {
    x: colX(i), y, w: cw, h, rectRadius: 0.06, fill: { color: fill }, line: { color: line, width: 1, dashType: dash || "solid" } });
  const quiet = (i, y, h) => { cell(i, y, h, "FFFFFF", C.grid); t(s, "listening", colX(i), y, cw, h, { fontSize: 9, italic: true, color: C.faint, align: "center", valign: "middle" }); };

  // row 1: the user's own words, their audio, and the intent so far
  const y1 = 2.24, h1 = 1.16;
  rowLabel(y1, h1, "The user", "a real recording, corrected twice", C.accent);
  const said = ["“…flights to Rome.”", "“No, wait… let's do Milan instead.”", "“…June 1st.”", "“Well, wait, but actually June 3rd…”"];
  const intent = [
    [{ text: "Rome", options: {} }],
    [{ text: "Rome", options: { strike: "sngStrike", color: C.bad } }, { text: "  Milan", options: {} }],
    [{ text: "Milan · June 1", options: {} }],
    [{ text: "June 1", options: { strike: "sngStrike", color: C.bad } }, { text: "  June 3", options: {} }],
  ];
  said.forEach((q, i) => {
    cell(i, y1, h1, "FFFFFF", C.line);
    t(s, q, colX(i) + 0.1, y1 + 0.06, cw - 0.2, 0.42, { fontSize: 9.5, italic: true, color: C.ink });
    s.addImage({ path: path.join(ASSETS, "wave" + (i + 1) + ".png"), x: colX(i) + 0.1, y: y1 + 0.5, w: cw - 0.2, h: 0.22 });
    t(s, "INTENT", colX(i) + 0.1, y1 + 0.78, cw - 0.2, 0.14, { fontSize: 7, bold: true, color: C.faint, charSpacing: 1.5 });
    t(s, intent[i], colX(i) + 0.1, y1 + 0.9, cw - 0.2, 0.22, { fontSize: 9.5, bold: true, color: C.accent });
  });
  s.addShape(S.roundRect, { x: outX, y: y1, w: ow, h: h1, rectRadius: 0.06, fill: { color: C.accentDim }, line: { color: C.accent, width: 1 } });
  t(s, "Final intent", outX + 0.06, y1 + 0.14, ow - 0.12, 0.22, { fontSize: 8.5, color: C.muted, align: "center" });
  t(s, "Milan\nJune 3", outX + 0.06, y1 + 0.4, ow - 0.12, 0.55, { fontSize: 12, bold: true, color: C.accent, align: "center" });

  // row 2: an agent that acts whenever a turn ends
  const y2 = 3.48, h2 = 0.9;
  rowLabel(y2, h2, "Greedy agent", "what acting at every turn end would call", C.bad);
  quiet(0, y2, h2);
  cell(1, y2, h2, "FFFFFF", C.grid); t(s, "no date yet", colX(1), y2, cw, h2, { fontSize: 9, italic: true, color: C.faint, align: "center", valign: "middle" });
  cell(2, y2, h2, C.badDim, C.bad);
  t(s, "search_flights(\nMilan, June 1)", colX(2) + 0.08, y2 + 0.1, cw - 0.16, 0.45, { fontSize: 9, fontFace: mono, color: C.bad, align: "center" });
  t(s, "✗ stale call", colX(2), y2 + 0.57, cw, 0.25, { fontSize: 9, bold: true, color: C.bad, align: "center" });
  cell(3, y2, h2, "FFFFFF", C.line);
  t(s, "search_flights(\nMilan, June 3)", colX(3) + 0.08, y2 + 0.1, cw - 0.16, 0.45, { fontSize: 9, fontFace: mono, color: C.ink, align: "center" });
  t(s, "right, but one too many", colX(3), y2 + 0.57, cw, 0.25, { fontSize: 8.5, color: C.muted, align: "center" });
  s.addShape(S.roundRect, { x: outX, y: y2, w: ow, h: h2, rectRadius: 0.06, fill: { color: C.bad }, line: { color: C.bad, width: 0 } });
  t(s, "2 calls\nFAILS", outX, y2, ow, h2, { fontSize: 12, bold: true, color: "FFFFFF", align: "center", valign: "middle" });

  // row 3: DUET, from its trace
  const y3 = 4.46, h3 = 0.98;
  rowLabel(y3, h3, "DUET", "from its own trace of the reported run", C.ok);
  quiet(0, y3, h3);
  [[1, "10.7 s"], [2, "13.4 s"]].forEach(([i, when]) => {
    cell(i, y3, h3, "FFFFFF", C.faint, "dash");
    t(s, "plans on it…", colX(i), y3 + 0.1, cw, 0.25, { fontSize: 9, color: C.muted, align: "center" });
    t(s, "dropped", colX(i), y3 + 0.34, cw, 0.28, { fontSize: 11, bold: true, color: C.muted, align: "center" });
    t(s, "new words at " + when, colX(i), y3 + 0.62, cw, 0.25, { fontSize: 8.5, color: C.faint, align: "center" });
  });
  cell(3, y3, h3, C.okDim, C.ok);
  t(s, "“One moment.”  21.1 s", colX(3), y3 + 0.07, cw, 0.22, { fontSize: 8.5, color: C.accent, align: "center", italic: true });
  t(s, "search_flights(\nMilan, June 3)", colX(3) + 0.08, y3 + 0.3, cw - 0.16, 0.42, { fontSize: 9, fontFace: mono, color: C.ok, align: "center" });
  t(s, "✓ runs once, 24.7 s", colX(3), y3 + 0.72, cw, 0.22, { fontSize: 8.5, bold: true, color: C.ok, align: "center" });
  s.addShape(S.roundRect, { x: outX, y: y3, w: ow, h: h3, rectRadius: 0.06, fill: { color: C.ok }, line: { color: C.ok, width: 0 } });
  t(s, "1 call\nPASSES", outX, y3, ow, h3, { fontSize: 12, bold: true, color: "FFFFFF", align: "center", valign: "middle" });

  // self-corrections across systems
  box(s, 9.28, 1.75, 3.5, 3.78, { fill: C.panel, line: C.line });
  t(s, "SELF-CORRECTION ITEMS", 9.48, 1.9, 3.1, 0.22, { fontSize: 9, bold: true, color: C.muted, charSpacing: 2 });
  t(s, "0.88", 9.48, 2.15, 1.55, 0.75, { fontSize: 40, bold: true, color: C.accent });
  t(s, "DUET's Pass@1\n15 of 17 items", 11.05, 2.3, 1.65, 0.5, { fontSize: 10, color: C.muted });
  const rows = [["DUET", 0.88, C.accent, true], ["GPT-Realtime", 0.588], ["Gemini Live 3.1", 0.353], ["Whisper → GPT-4o", 0.176]];
  const lw = 1.18, bw = 1.55;
  rows.forEach(([n, v, col, me], i) => {
    const y = 3.12 + i * 0.44;
    t(s, n, 9.44, y, lw - 0.08, 0.26, { fontSize: 9, bold: !!me, color: me ? C.ink : C.muted, align: "right", valign: "middle" });
    s.addShape(S.rect, { x: 9.44 + lw, y: y + 0.03, w: bw, h: 0.2, fill: { color: "FFFFFF" }, line: { color: C.grid, width: 0.75 } });
    s.addShape(S.rect, { x: 9.44 + lw, y: y + 0.03, w: bw * v, h: 0.2, fill: { color: col || C.base }, line: { color: col || C.base, width: 0 } });
    t(s, v.toFixed(2), 9.44 + lw + bw + 0.05, y, 0.5, 0.26, { fontSize: 9.5, bold: true, color: me ? C.accent : C.ink, valign: "middle" });
  });
  t(s, "Strict Pass@1 on the benchmark's self-correction items. Baselines: the FDB-v3 paper, which names the cause: “models commit intermediate parameters before the correction arrives.”",
    9.48, 4.92, 3.15, 0.55, { fontSize: 7.5, color: C.faint });

  // what theme 05 asks, and DUET's answer
  const asks = [["STAY RESPONSIVE", "“One moment.” as soon as the user is clearly done and the thinker is still working. All 100 conversations answered.", C.accent],
    ["WORK ASYNCHRONOUSLY", "Speech recognition, synthesis, model calls and tools all run off the audio loop. The user can talk over the agent at any time.", C.hold],
    ["RECOVER CLEANLY", "A plan built on words the user has changed never runs; an action never runs twice. 0% interruptions in the reported run.", C.ok]];
  asks.forEach(([h, b, col], i) => card(s, 0.55 + i * 4.13, 5.7, 3.95, 1.2, h, b, col, { bs: 10 }));
}

// ============================================================================ 3. architecture
{
  const s = pptx.addSlide();
  frame(s, 3, "Solution and architecture", "Two minds and a referee: every action leaves through one guarded exit",
    "The model decides what should happen. Deterministic code decides whether, and when, it may happen.");
  const R1 = 1.95, R2 = 4.2, H = 1.45;
  const cx = [0.55, 2.55, 5.55, 8.7, 11.2], cw = [1.45, 2.25, 2.4, 2.15, 1.58];
  // room
  box(s, cx[0], R1, cw[0], R2 + H - R1, { fill: C.panel2, line: C.line });
  t(s, "LIVEKIT\nROOM", cx[0] + 0.15, R1 + 0.2, cw[0] - 0.3, 0.6, { fontSize: 12, bold: true, color: C.ink, charSpacing: 2 });
  t(s, "WebRTC audio, both ways.\n\nThe benchmark's runner, or a Galaxy phone, on the other side.", cx[0] + 0.15, R1 + 0.9, cw[0] - 0.25, 2.2, { fontSize: 9.5, color: C.muted });
  // ears
  box(s, cx[1], R1, cw[1], H, { fill: C.panel, line: C.line, shadow: true });
  t(s, "EARS · local", cx[1] + 0.18, R1 + 0.12, 2, 0.26, { fontSize: 11.5, bold: true, color: C.accent });
  t(s, "Silero VAD → faster-whisper large-v3-turbo (GPU)\nLiveKit turn detector: has the turn ended?", cx[1] + 0.18, R1 + 0.45, cw[1] - 0.3, 0.95, { fontSize: 9.5, color: C.ink });
  // coordinator
  box(s, cx[2], R1, cw[2], R2 + H - R1, { fill: C.coordFill, line: C.accent, lw: 2, shadow: true });
  t(s, "COORDINATOR", cx[2] + 0.2, R1 + 0.15, 2.2, 0.3, { fontSize: 14, bold: true, color: C.accent, charSpacing: 2 });
  const mech = [["Epochs", "new words void every older plan"], ["Commit gate", "no action until the turn has closed and the user is quiet"],
    ["Idempotency ledger", "an action never runs twice"], ["Failure policy", "reads retried once, writes never re-sent"]];
  mech.forEach(([h, b], i) => {
    t(s, [{ text: h, options: { bold: true, fontFace: FB, color: C.ink, breakLine: true } }, { text: b, options: { color: C.muted } }],
      cx[2] + 0.2, R1 + 0.6 + i * 0.72, cw[2] - 0.35, 0.66, { fontSize: 10 });
  });
  // thinker + google api
  box(s, cx[3], R1, cw[3], H, { fill: C.panel, line: C.line, shadow: true });
  t(s, "THINKER · Gemma 4", cx[3] + 0.18, R1 + 0.12, 2.1, 0.26, { fontSize: 11.5, bold: true, color: C.hold });
  t(s, "Reads everything said since its last spoken answer, plans the tool calls, speaks once with the results.", cx[3] + 0.18, R1 + 0.45, cw[3] - 0.3, 0.95, { fontSize: 9.5, color: C.ink });
  box(s, cx[4], R1, cw[4], H, { fill: C.panel2, line: C.line });
  t(s, "GOOGLE API", cx[4] + 0.16, R1 + 0.12, 1.7, 0.26, { fontSize: 11.5, bold: true, color: C.ink });
  t(s, "gemma-4-26b-a4b-it\nkey pool · token budget · backup requests", cx[4] + 0.16, R1 + 0.45, cw[4] - 0.25, 0.95, { fontSize: 9.5, color: C.muted });
  // tools, voice, facts
  box(s, cx[1], R2, cw[1], H, { fill: C.panel, line: C.line, shadow: true });
  t(s, "TOOLS", cx[1] + 0.18, R2 + 0.12, 2, 0.26, { fontSize: 11.5, bold: true, color: C.ok });
  t(s, "The benchmark's 12 APIs (its own mock, unmodified), or the phone's tools in DUET for Galaxy.", cx[1] + 0.18, R2 + 0.45, cw[1] - 0.3, 0.95, { fontSize: 9.5, color: C.ink });
  box(s, cx[3], R2, cw[3], H, { fill: C.panel, line: C.line, shadow: true });
  t(s, "VOICE · local", cx[3] + 0.18, R2 + 0.12, 2, 0.26, { fontSize: 11.5, bold: true, color: C.warn });
  t(s, "Fast voice: “One moment.” after 1.6 s of quiet; never a value or a result.\nKokoro-82M speech (GPU).", cx[3] + 0.18, R2 + 0.45, cw[3] - 0.3, 0.95, { fontSize: 9.5, color: C.ink });
  box(s, cx[4], R2, cw[4], H, { fill: C.panel2, line: C.line });
  t(s, "ON ONE GPU", cx[4] + 0.16, R2 + 0.12, 1.7, 0.26, { fontSize: 11.5, bold: true, color: C.ink });
  t(s, "Speech models only: 2.9 GB at peak, of Samsung's 48 GB. The language model runs at Google.", cx[4] + 0.16, R2 + 0.45, cw[4] - 0.25, 0.95, { fontSize: 9.5, color: C.muted });
  // arrows + labels
  // labels sit inside the gap between two columns, never over a box
  const gap = (i) => [cx[i] + cw[i], cx[i + 1]];
  const lab = (txt, i, y, col, h = 0.4) => { const [a, b] = gap(i); t(s, txt, a + 0.02, y, b - a - 0.04, h, { fontSize: 8.5, color: col || C.muted, align: "center", valign: "middle" }); };
  line(s, gap(0)[0], R1 + 0.72, gap(0)[1], R1 + 0.72); lab("audio", 0, R1 + 0.3);
  line(s, gap(1)[0], R1 + 0.72, gap(1)[1], R1 + 0.72); lab("words ·\nturn closed", 1, R1 + 0.28);
  line(s, gap(2)[0], R1 + 0.5, gap(2)[1], R1 + 0.5, { color: C.hold }); lab("open\nrequest", 2, R1 + 0.06, C.hold);
  line(s, gap(2)[1], R1 + 1.0, gap(2)[0], R1 + 1.0, { color: C.hold }); lab("proposed\ncall", 2, R1 + 1.04, C.hold);
  line(s, gap(3)[0], R1 + 0.72, gap(3)[1], R1 + 0.72, { begin: "triangle" });
  line(s, gap(1)[1], R2 + 0.5, gap(1)[0], R2 + 0.5, { color: C.ok }); lab("committed\ncall, once", 1, R2 + 0.06, C.ok);
  line(s, gap(1)[0], R2 + 1.0, gap(1)[1], R2 + 1.0, { color: C.ok }); lab("result", 1, R2 + 1.04, C.ok, 0.25);
  line(s, gap(2)[0], R2 + 0.72, gap(2)[1], R2 + 0.72, { color: C.warn, dash: "dash" }); lab("cover\nthe wait", 2, R2 + 0.28, C.warn);
  line(s, cx[3] + 0.7, R1 + H, cx[3] + 0.7, R2, { color: C.hold });
  t(s, "the answer, with the facts", cx[3] + 0.8, R1 + H + 0.14, 1.9, 0.45, { fontSize: 8.5, color: C.hold });
  // audio out: around the bottom back to the room
  const yb = R2 + H + 0.3;
  line(s, cx[3] + 1.6, R2 + H, cx[3] + 1.6, yb, { end: false, color: C.faint });
  line(s, cx[3] + 1.6, yb, cx[0] + 0.75, yb, { end: false, color: C.faint });
  line(s, cx[0] + 0.75, yb, cx[0] + 0.75, R2 + H, { color: C.faint });
  t(s, "agent audio out", 4.6, yb - 0.24, 1.6, 0.2, { fontSize: 8.5, color: C.faint, align: "center" });
  t(s, "Every tool call in the benchmark and on the phone takes this one path, so the same guarantees hold everywhere: a correction always wins, and nothing is done twice.", 0.55, 6.52, 12.2, 0.4, { fontSize: 11, color: C.ink, italic: true });
}

// ============================================================================ 4. coordinator
{
  const s = pptx.addSlide();
  frame(s, 4, "Innovation: the coordinator", "Plans early, commits late, acts once",
    "Each proposed action travels a small state machine. Only the path to Done ever touches the outside world.");
  box(s, 0.55, 1.78, 7.45, 4.25, { fill: C.panel, line: C.line });
  const node = (txt, sub, x, y, w, col, dim) => {
    box(s, x, y, w, 0.78, { fill: dim, line: col, lw: 1.5 });
    t(s, txt, x + 0.12, y + 0.08, w - 0.24, 0.28, { fontSize: 12, bold: true, color: col, align: "center" });
    t(s, sub, x + 0.1, y + 0.38, w - 0.2, 0.36, { fontSize: 8.5, color: C.muted, align: "center" });
  };
  const y1 = 2.2;
  node("Planned", "from the thinker", 0.8, y1, 1.55, C.accent, C.accentDim);
  node("Held", "at the commit gate", 2.75, y1, 1.45, C.hold, C.holdDim);
  node("Ledger", "seen this exact call?", 4.6, y1, 1.5, C.ink, C.panel2);
  node("Running", "executed once", 6.45, y1, 1.35, C.warn, C.warnDim);
  line(s, 2.35, y1 + 0.39, 2.75, y1 + 0.39); line(s, 4.2, y1 + 0.39, 4.6, y1 + 0.39); line(s, 6.1, y1 + 0.39, 6.45, y1 + 0.39);
  t(s, "waits while the user speaks, or while words may still arrive", 2.2, y1 + 0.86, 2.6, 0.4, { fontSize: 8.5, color: C.hold, align: "center" });
  // branches
  const y2 = 3.55;
  node("Dropped", "new words since the plan", 2.6, y2, 1.75, C.bad, C.badDim);
  line(s, 3.47, y1 + 0.78 + 0.45, 3.47, y2);
  node("Not repeated", "answered from the ledger", 4.5, y2, 1.7, C.hold, C.holdDim);
  line(s, 5.35, y1 + 0.78, 5.35, y2);
  node("Done", "result to the thinker", 6.4, y2, 1.45, C.ok, C.okDim);
  line(s, 7.12, y1 + 0.78, 7.12, y2);
  const y3 = 4.85;
  box(s, 0.8, y3, 3.4, 0.95, { fill: C.panel2, line: C.line });
  t(s, [{ text: "A read failed", options: { bold: true, fontFace: FB, color: C.warn, breakLine: true } },
    { text: "retried once, then the thinker is told", options: { color: C.muted } }], 0.95, y3 + 0.1, 3.1, 0.8, { fontSize: 10 });
  box(s, 4.45, y3, 3.4, 0.95, { fill: C.panel2, line: C.line });
  t(s, [{ text: "A write timed out", options: { bold: true, fontFace: FB, color: C.bad, breakLine: true } },
    { text: "outcome unknown: never re-sent; the user is told and offered a human", options: { color: C.muted } }], 4.6, y3 + 0.1, 3.1, 0.8, { fontSize: 10 });
  t(s, "Running can also end in:", 0.8, y3 - 0.3, 3, 0.24, { fontSize: 9, color: C.faint });

  // mechanisms with their numbers
  const rows = [
    ["Epochs", "Every plan carries the epoch of the words it was made on. New words void it, even after the newer turn has closed. A cough brings no words: it delays an action, never cancels it."],
    ["Commit gate", "A tool runs only after the turn closes and the user is quiet for 1.1 s; 1.8 s after a revision (“no wait”); 2.2 s when the sentence is left open (“and…”)."],
    ["Idempotency ledger", "An identical call already made in the conversation is answered from the ledger, so a booking survives a barge-in and never happens twice."],
    ["Keep listening", "“The order number is…”: the thinker says nothing and does nothing until the user has been quiet for 2.5 s."],
  ];
  rows.forEach(([h, b], i) => {
    const y = 1.78 + i * 1.07;
    box(s, 8.25, y, 4.53, 0.98, { fill: C.panel, line: C.line });
    t(s, h, 8.42, y + 0.09, 4.2, 0.26, { fontSize: 11.5, bold: true, color: [C.accent, C.hold, C.ok, C.warn][i] });
    t(s, b, 8.42, y + 0.37, 4.25, 0.6, { fontSize: 9.3, color: C.ink });
  });
  box(s, 0.55, 6.18, 12.23, 0.72, { fill: C.band, line: C.line });
  t(s, [{ text: "Why it matters for Pass@1:  ", options: { bold: true, fontFace: FB, color: C.ok } },
    { text: "the score is strict, so one stale or duplicated call fails the item. In the reported run, 33 model calls were abandoned mid-flight because the user's new words had already overtaken their plan.", options: { color: C.ink } }],
  0.75, 6.28, 11.9, 0.55, { fontSize: 11, valign: "middle" });
}

// ============================================================================ 5. reliability + stack
{
  const s = pptx.addSlide();
  frame(s, 5, "Tools, tech stack and engineering", "Built to survive someone else's machine and someone else's API key",
    "Samsung re-runs our script; only that run counts. So every model call is defended, and every version is pinned.");
  // left: model client flow
  box(s, 0.55, 1.78, 5.3, 5.1, { fill: C.panel, line: C.line });
  t(s, "EVERY MODEL CALL · duet_voice/gemma_api.py", 0.75, 1.92, 5, 0.25, { fontSize: 9.5, bold: true, color: C.muted, charSpacing: 1.5 });
  const step = (txt, y, col) => { box(s, 0.8, y, 4.8, 0.52, { fill: C.panel2, line: col || C.line }); t(s, txt, 0.95, y, 4.5, 0.52, { fontSize: 10, color: C.ink, valign: "middle" }); };
  step("1  Book the call on the key with the most room: 15,000 of Google's 16,000 input tokens a minute per key", 2.28);
  step("2  Rate-limited (429): rest that key for the delay Google asks, try the next", 2.92);
  step("3  Server error (500, 503, 504): retry in 0.3 to 0.7 s", 3.56);
  step("4  No answer after 6 s: send a backup to another key (at most 4 in flight); the first answer wins", 4.2, C.accent);
  step("5  28 s deadline per call, inside the recording's 30 s window", 4.84);
  t(s, "IN THE REPORTED RUN", 0.8, 5.55, 3, 0.22, { fontSize: 9, bold: true, color: C.muted, charSpacing: 1.5 });
  const kv = [["220", "calls answered"], ["3.3 s", "median answer"], ["221", "transient errors retried"], ["109", "backups sent"]];
  kv.forEach(([v, k], i) => {
    t(s, v, 0.8 + i * 1.22, 5.8, 1.2, 0.45, { fontSize: 20, bold: true, color: C.ok });
    t(s, k, 0.8 + i * 1.22, 6.25, 1.15, 0.45, { fontSize: 8.5, color: C.muted });
  });
  // middle: stack table
  const rows = [["Voice framework", "LiveKit Agents 1.8.3 (custom agent)"], ["Language model", "Gemma 4 26B-A4B-it · Google API · temperature 1.0, top-p 0.95, top-k 64, seed 7"],
    ["Speech in", "faster-whisper large-v3-turbo @ 0a363e9 · Silero VAD · LiveKit turn detector"], ["Speech out", "Kokoro-82M @ f3ff357, voice af_heart"],
    ["Benchmark", "Full-Duplex-Bench v3 @ 3e799c4: runner, mock tools and evaluations unmodified"], ["Extension", "Capacitor Android app · LiveKit Cloud · RPC duet.tool"]];
  box(s, 6.05, 1.78, 3.55, 5.1, { fill: C.panel, line: C.line });
  t(s, "TECH STACK", 6.25, 1.92, 3, 0.25, { fontSize: 9.5, bold: true, color: C.muted, charSpacing: 1.5 });
  rows.forEach(([k, v], i) => {
    const y = 2.28 + i * 0.76;
    t(s, k, 6.25, y, 3.2, 0.22, { fontSize: 9.5, bold: true, color: C.accent });
    t(s, v, 6.25, y + 0.22, 3.2, 0.5, { fontSize: 9.5, color: C.ink });
  });
  // right: reproduction + quality
  box(s, 9.8, 1.78, 2.98, 5.1, { fill: C.panel, line: C.line });
  t(s, "ONE COMMAND", 10.0, 1.92, 2.6, 0.25, { fontSize: 9.5, bold: true, color: C.muted, charSpacing: 1.5 });
  box(s, 10.0, 2.25, 2.58, 0.42, { fill: C.code, line: C.line, r: 0.06 });
  t(s, "bash reproduce.sh", 10.12, 2.25, 2.4, 0.42, { fontSize: 11, color: C.ok, valign: "middle", fontFace: "Consolas" });
  const rep = ["Installs Python 3.11 and every package from lock files (uv)", "Benchmark commit, data SHA-256, LiveKit checksum pinned",
    "Preflight tests every key before anything runs", "Official runner, then the three official evaluations", "Dockerfile runs the same script"];
  t(s, rep.map((r) => ({ text: r, options: { bullet: { code: "25AA", indent: 11 }, breakLine: true } })), 10.0, 2.8, 2.65, 2.15, { fontSize: 9.3, color: C.ink, paraSpaceAfter: 4 });
  t(s, "TRUST", 10.0, 5.05, 2.6, 0.25, { fontSize: 9.5, bold: true, color: C.muted, charSpacing: 1.5 });
  const tr = ["75 unit tests", "A test fails if any benchmark answer appears in the agent", "A trace of every conversation; one log line per model call"];
  t(s, tr.map((r) => ({ text: r, options: { bullet: { code: "25AA", indent: 11 }, breakLine: true } })), 10.0, 5.35, 2.65, 1.45, { fontSize: 9.3, color: C.ink, paraSpaceAfter: 4 });
}

// ============================================================================ 6. results
{
  const s = pptx.addSlide();
  frame(s, 6, "Results", "Full-Duplex-Bench v3: 0.81 Pass@1 on all 100 real recordings",
    "Streamed in real time through the benchmark's own runner; scored by its own evaluation scripts.");
  const head = ["System", "Pass@1", "Tool F1", "Arg. acc.", "Response", "Turn-take", "Key info", "Interrupts"];
  const data = [["DUET · Gemma 4 26B-A4B", "0.81", "0.915", "0.850", "0.730", "100%", "14.4 s", "0%"],
    ["GPT-Realtime", "0.600", "0.876", "0.680", "0.792", "96.0%", "6.9 s", "13.5%"],
    ["Gemini Live 3.1", "0.540", "0.817", "0.588", "0.718", "78.0%", "4.3 s", "19.2%"],
    ["Whisper → GPT-4o → TTS", "0.450", "0.803", "0.562", "0.600", "100%", "10.1 s", "33.0%"]];
  const cell = (v, o = {}) => ({ text: v, options: { fontFace: o.b ? FB : F, fontSize: 11, color: o.c || C.ink, fill: { color: o.f || C.panel }, align: o.a || "center", valign: "middle" } });
  const rows = [head.map((h, i) => cell(h, { b: true, c: C.muted, f: C.panel2, a: i ? "center" : "left" }))];
  // green = the best value in its column, whichever system has it (lower is better for latency and interruptions)
  const num = (v) => parseFloat(v);
  const lowerBetter = [false, false, false, false, false, false, true, true];
  const best = head.map((_, j) => {
    if (!j) return null;
    const vals = data.map((r) => num(r[j]));
    return lowerBetter[j] ? Math.min(...vals) : Math.max(...vals);
  });
  data.forEach((r, i) => rows.push(r.map((v, j) => cell(v, {
    b: i === 0 || (j && num(v) === best[j]), c: j && num(v) === best[j] ? C.ok : C.ink,
    f: i === 0 ? C.duetRow : C.panel, a: j ? "center" : "left" }))));
  s.addTable(rows, { x: 0.55, y: 1.8, w: 8.2, colW: [2.3, 0.82, 0.82, 0.86, 0.86, 0.86, 0.84, 0.84], rowH: 0.4, border: { type: "solid", pt: 0.75, color: C.line }, margin: 0.06 });
  t(s, "Green: best in its column. Baselines: FDB-v3 paper. Response: response quality. Key info: time to the answer's key facts.", 0.55, 3.87, 8.2, 0.22, { fontSize: 8.5, color: C.faint });
  // breakdowns
  box(s, 0.55, 4.2, 4.0, 2.68, { fill: C.panel, line: C.line });
  t(s, "BY DISFLUENCY · PASS@1", 0.72, 4.3, 3.6, 0.22, { fontSize: 9, bold: true, color: C.muted, charSpacing: 1.5 });
  [["False start", 0.92, "11/12"], ["Self-correction", 0.88, "15/17"], ["Filler", 0.83, "24/29"], ["Hesitation", 0.8, "8/10"], ["Pause", 0.72, "13/18"], ["None", 0.68, "21/31"]]
    .forEach(([n, v, f], i) => hbar(s, n, v, 1, 0.62, 4.62 + i * 0.36, 3.85, C.accent, { lw: 1.35, fs: 9.5, text: v.toFixed(2) }));
  box(s, 4.75, 4.2, 4.0, 2.68, { fill: C.panel, line: C.line });
  t(s, "BY TOOLS EXPECTED · DIFFICULTY", 4.92, 4.3, 3.6, 0.22, { fontSize: 9, bold: true, color: C.muted, charSpacing: 1.5 });
  [["1 tool", 0.88], ["2 tools", 0.72], ["3 tools", 0.62], ["Easy", 0.86], ["Medium", 0.88], ["Hard", 0.67]]
    .forEach(([n, v], i) => hbar(s, n, v, 1, 4.82, 4.62 + i * 0.36, 3.85, i < 3 ? C.hold : C.warn, { lw: 1.35, fs: 9.5 }));
  // right: what the misses are + honesty
  box(s, 8.95, 1.8, 3.83, 5.08, { fill: C.panel, line: C.line });
  t(s, "THE 25 EXACT-MATCH MISSES", 9.12, 1.93, 3.5, 0.22, { fontSize: 9, bold: true, color: C.muted, charSpacing: 1.5 });
  const miss = [["Format only", 8, C.faint, "right tool and value, other wording"], ["Heard wrong", 7, C.warn, "spelled codes the recognizer misheard"],
    ["Provider stalls", 3, C.bad, "Google answered nothing for 28 s"], ["Conditional", 3, C.hold, "expected answer contradicts the mock data"],
    ["Not passable", 2, C.muted, "expected value never spoken, or not the one said"], ["Judgement", 2, C.accent, "DUET asked instead of acting"]];
  let xx = 9.12; const tot = 25, bw = 3.5;
  miss.forEach(([, n, col]) => { s.addShape(S.rect, { x: xx, y: 2.25, w: bw * n / tot - 0.02, h: 0.26, fill: { color: col }, line: { color: col, width: 0 } }); xx += bw * n / tot; });
  miss.forEach(([h, n, col, b], i) => {
    const y = 2.68 + i * 0.47;
    s.addShape(S.rect, { x: 9.12, y: y + 0.05, w: 0.14, h: 0.14, fill: { color: col }, line: { color: col, width: 0 } });
    t(s, [{ text: n + "  " + h, options: { bold: true, fontFace: FB, color: C.ink, breakLine: true } }, { text: b, options: { color: C.muted } }], 9.36, y, 3.35, 0.46, { fontSize: 9.3 });
  });
  t(s, [{ text: "How it was scored. ", options: { bold: true, fontFace: FB, color: C.warn } },
    { text: "The judge was Gemma 4 standing in for gpt-4o, with the official prompts unchanged. With no judge at all, by exact match, DUET passes 75 of 100. Samsung's re-run uses gpt-4o.", options: { color: C.ink } }],
  9.12, 5.55, 3.5, 1.3, { fontSize: 9.3 });
}

// ============================================================================ 7. use case
{
  const s = pptx.addSlide();
  frame(s, 7, "Use-case extension: DUET for Galaxy", "The same agent, coordinator and model on a Samsung phone",
    "An Android app in three modes. Real runs on Gemma 4, recorded; every phone action passes the same coordinator.");
  const shots = [["correction_phone.png", "ASSISTANT", "“Set an alarm for 3:30… no, set it for 4:30.”", "The 3:30 plan never runs. One alarm, 4:30.", C.accent],
    ["care_phone.png", "CARE", "“Did I take my tablet? I'll just take one now.”", "DUET checks the record and gently stops a second dose.", C.ok],
    ["drive_phone.png", "DRIVE", "“Take me to the office, no wait, home.”", "Only home is set; the ETA text to Priya goes once.", C.warn]];
  shots.forEach(([img, mode, q, a, col], i) => {
    const x = 0.55 + i * 2.62;
    // a dark device card, the same tone as the recording's background, so the phone sits on it cleanly
    s.addShape(S.roundRect, { x: x, y: 1.7, w: 2.48, h: 4.92, rectRadius: 0.16, fill: { color: "0B0D12" }, line: { color: "0B0D12", width: 0 },
      shadow: { type: "outer", blur: 8, offset: 1.5, angle: 90, color: "1428A0", opacity: 0.15 } });
    s.addImage({ path: path.join(ASSETS, img), x: x + 0.14, y: 1.78, w: 2.2, h: 4.76 });
    pill(s, mode, x + 0.64, 6.68, 1.2, 0.3, col, C.bg, { size: 9.5 });
    void q; void a;
  });
  // right: the three stories + how it works
  box(s, 8.5, 1.78, 4.28, 5.1, { fill: C.panel, line: C.line });
  let y = 1.95;
  shots.forEach(([, mode, q, a, col]) => {
    t(s, [{ text: mode + "  ", options: { bold: true, fontFace: FB, color: col } }, { text: q, options: { color: C.ink, italic: true } }], 8.7, y, 3.95, 0.45, { fontSize: 10 });
    t(s, a, 8.7, y + 0.33, 3.95, 0.3, { fontSize: 9.5, color: C.muted });
    y += 0.8;
  });
  t(s, "Recorded on the app's web build with a scripted user voice. The agent, the model and every tool call are live; the web build simulates the phone's side, which Android carries out on a Galaxy.",
    8.7, 6.36, 3.95, 0.46, { fontSize: 8, color: C.faint });
  t(s, "HOW IT WORKS", 8.7, 4.45, 3, 0.22, { fontSize: 9, bold: true, color: C.muted, charSpacing: 1.5 });
  t(s, [
    { text: "Phone ⇄ LiveKit Cloud ⇄ DUET agent ⇄ Gemma 4. The app shows each step live: Planned, Never ran, Done, Not repeated.", options: { breakLine: true } },
    { text: "Real Android interfaces: alarms, battery and screen readings, apps, brightness, settings, Maps, calls, messages. Simulated in the app: the SmartThings home, the medicine schedule, the car.", options: { breakLine: true } },
    { text: "8 of 8 phone scenarios pass end to end (scripts/galaxy_check_scenarios.py).", options: { color: C.ok } },
  ], 8.7, 4.72, 3.95, 1.62, { fontSize: 9.3, color: C.ink, paraSpaceAfter: 5 });
}

// ============================================================================ 8. impact + next
{
  const s = pptx.addSlide();
  frame(s, 8, "Impact and what's next", "One coordinator for every surface where a voice can act",
    "Wherever a spoken request can change something real, a correction has to win and nothing may happen twice.");
  // left: where the same guarantee matters (the directions from our use-case research)
  box(s, 0.55, 1.75, 7.62, 4.5, { fill: C.panel, line: C.line });
  t(s, "WHERE THE SAME GUARANTEE MATTERS", 0.75, 1.9, 6, 0.24, { fontSize: 9.5, bold: true, color: C.muted, charSpacing: 2 });
  const surfaces = [
    ["SmartThings home", "“Lock the front door… actually, not yet.”", "The lock never acts on a plan the user took back.", C.accent],
    ["Samsung service line", "“Book a technician for the washer… no, the dryer.”", "One visit is booked, for the dryer.", C.hold],
    ["Android Auto and Car-to-Home", "“The Starbucks on 5th… no, the one by the office.”", "The route is set once, to the final place.", C.warn],
    ["Galaxy, beside Bixby", "“Send it to Priya… actually, to Mom.”", "The message goes once, to Mom.", C.ok],
  ];
  surfaces.forEach(([name, said, done, col], i) => {
    const x = 0.75 + (i % 2) * 3.68, y = 2.25 + Math.floor(i / 2) * 1.95;
    s.addShape(S.roundRect, { x, y, w: 3.52, h: 1.8, rectRadius: 0.08, fill: { color: "FFFFFF" }, line: { color: C.line, width: 1 } });
    s.addShape(S.rect, { x, y: y + 0.14, w: 0.06, h: 1.52, fill: { color: col }, line: { color: col, width: 0 } });
    t(s, name, x + 0.22, y + 0.14, 3.2, 0.28, { fontSize: 12, bold: true, color: col });
    t(s, said, x + 0.22, y + 0.5, 3.18, 0.55, { fontSize: 10.5, italic: true, color: C.ink });
    t(s, [{ text: "DUET  ", options: { bold: true, fontFace: FB, color: C.ok } }, { text: done, options: { color: C.ink } }], x + 0.22, y + 1.1, 3.18, 0.55, { fontSize: 10.5 });
  });

  // right: the roadmap as a PRISM worklet
  box(s, 8.4, 1.75, 4.38, 4.5, { fill: C.panel, line: C.line });
  t(s, "ROADMAP AS A PRISM WORKLET", 8.6, 1.9, 4, 0.24, { fontSize: 9.5, bold: true, color: C.muted, charSpacing: 2 });
  const steps = [
    ["NOW", "Round 1", "DUET scores 0.81 Pass@1 on Full-Duplex-Bench v3, and runs on a Samsung phone in three modes.", C.accent],
    ["NEXT", "On the device", "Gemma on Galaxy: no round trip, no key limits, and the user's words stay on the phone. A code the recognizer is unsure of is read back before any tool runs.", C.hold],
    ["THEN", "Across Samsung", "SmartThings, Android Auto and the service line on real devices, measured by stale-action and duplicate-action rates.", C.ok],
  ];
  s.addShape(S.line, { x: 8.83, y: 2.45, w: 0, h: 3.0, line: { color: C.line, width: 2 } });
  steps.forEach(([tag, head, body, col], i) => {
    const y = 2.3 + i * 1.3;
    s.addShape(S.ellipse, { x: 8.72, y: y + 0.04, w: 0.22, h: 0.22, fill: { color: col }, line: { color: "FFFFFF", width: 2 } });
    t(s, [{ text: tag + "  ", options: { bold: true, fontFace: FB, color: col, charSpacing: 1.5 } }, { text: head, options: { bold: true, fontFace: FB, color: C.ink } }],
      9.1, y, 3.55, 0.3, { fontSize: 11.5 });
    t(s, body, 9.1, y + 0.32, 3.55, 0.9, { fontSize: 10, color: C.muted });
  });

  pill(s, "ROUND 2: INTERRUPT IT LIVE", 0.55, 6.44, 3.9, 0.38, C.accentDim, C.pillText, { size: 11, cs: 2 });
  t(s, [{ text: "Reproduce:  ", options: { color: C.muted } }, { text: "bash reproduce.sh", options: { color: C.ok, fontFace: "Consolas" } },
    { text: "     The app:  ", options: { color: C.muted } }, { text: "app/README.md", options: { color: C.ink, fontFace: "Consolas" } },
    { text: "     Every trace:  ", options: { color: C.muted } }, { text: "results/reported/", options: { color: C.ink, fontFace: "Consolas" } }],
  4.7, 6.44, 8.1, 0.38, { fontSize: 11, valign: "middle" });
}

pptx.writeFile({ fileName: OUT }).then((f) => console.log("wrote", f));
