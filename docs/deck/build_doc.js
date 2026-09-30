// SRM_SE7EN_Documentation.pdf: DUET's technical documentation, built from the repository's own
// Markdown so the PDF and the repository cannot disagree. Samsung's requirements come first.
//   node build_doc.js <repo root> <out.pdf> <phone screenshots dir>
const fs = require("fs");
const path = require("path");
const { marked } = require("marked");
const { chromium } = require("playwright-core");

const ROOT = process.argv[2] || path.resolve(__dirname, "../..");
const OUT = process.argv[3] || path.join(ROOT, "SRM_SE7EN_Documentation.pdf");
const SHOTS = process.argv[4] || path.join(__dirname, "assets");
const REPO = "https://github.com/tannnmayy/DUET-SAMSUNGPRISM-HACKATHON-TEAM-SE7EN";
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// --- Markdown -> HTML, with links pointing at the repository and diagrams kept for Mermaid ---------
function mdToHtml(docPath, md) {
  // base64 on one line: a blank line inside a diagram would otherwise end Markdown's HTML block
  md = md.replace(/```mermaid\r?\n([\s\S]*?)```/g, (_, code) =>
    `\n<figure class="fig"><div class="mermaid-src" data-code="${Buffer.from(code, "utf8").toString("base64")}"></div></figure>\n`);
  const renderer = new marked.Renderer();
  renderer.link = (href, title, text) => {
    let url = href || "";
    if (!/^(https?:|mailto:)/.test(url)) {
      const [p, anchor] = url.split("#");
      if (!p) url = `${REPO}/blob/main/${docPath}#${anchor}`;
      else {
        const target = path.posix.normalize(path.posix.join(path.posix.dirname(docPath), p)).replace(/\/$/, "");
        const isDir = fs.existsSync(path.join(ROOT, target)) && fs.statSync(path.join(ROOT, target)).isDirectory();
        url = `${REPO}/${isDir ? "tree" : "blob"}/main/${target}${anchor ? "#" + anchor : ""}`;
      }
    }
    return `<a href="${esc(url)}">${text}</a>`;
  };
  // a table whose header cells are all empty (a key-value table) prints without a header bar
  return marked.parse(md, { renderer, gfm: true }).replace(/<thead>\s*<tr>(\s*<th[^>]*>\s*<\/th>)+\s*<\/tr>\s*<\/thead>/g, "");
}
function read(rel) { return fs.readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n"); }
function dropTitle(md) { return md.replace(/^# .*\n/, ""); }
function sections(md, keep) {           // keep the intro and the ## sections named in `keep`
  const parts = md.split(/\n(?=## )/);
  return parts.filter((p, i) => i === 0 || keep.some((k) => p.startsWith("## " + k))).join("\n");
}

// --- Samsung's requirements, each with where it is met ---------------------------------------------
const B = (p) => `${REPO}/blob/main/${p}`, T = (p) => `${REPO}/tree/main/${p}`;
const MET = "met", FORM = "form", SUB = "sub", PART = "part";
const LABEL = { met: "Met", form: "Submission form", sub: "At submission", part: "Partly" };
const matrix = [
  ["Theme 05 guide: what to submit", [
    ["Code repository with a README covering the architecture (a diagram), exact setup and run steps, and the extension clearly marked",
      `<a href="${B("README.md")}">README.md</a>: architecture diagrams, "Reproduce the benchmark", "Use-case extension: DUET for Galaxy". Chapter 3 of this document.`, MET],
    ["One-command reproduction that runs FDB-v3 end to end (install, configure, evaluate) and declares the model provider",
      `<a href="${B("reproduce.sh")}">reproduce.sh</a>; README "Models and providers (declaration)": a custom LiveKit agent, Gemma 4 26B-A4B-it through Google's API.`, MET],
    ["Benchmark results and run logs (scores, seeds, configuration) from our best run",
      `<a href="${T("results/reported/gemma4_final_run")}">results/reported/gemma4_final_run</a>: summary.json, run_config.json (seed 7, sampling, every threshold, the git commit), the official reports, 100 per-recording results, a trace of every conversation, the agent, runner and evaluation logs. Chapter 5.`, MET],
    ["Demo video, 3 to 5 minutes: an interruption on the benchmark, then the extension",
      `The link is in the submission form. Plan: <a href="${B("docs/VIDEO_SCRIPT.md")}">docs/VIDEO_SCRIPT.md</a>; the extension's recorded runs: <a href="${T("results/galaxy/video")}">results/galaxy/video</a>.`, FORM],
    ["Slide deck, at most 8 slides: problem, architecture, results, what next",
      `<a href="${B("SRM_SE7EN.pptx")}">SRM_SE7EN.pptx</a> and <a href="${B("SRM_SE7EN.pdf")}">SRM_SE7EN.pdf</a>: 8 slides.`, MET],
    ["API keys documented, not included",
      `README "API keys": which keys and where they go. Keys are read from the environment or the git-ignored .env.local; the repository's entire history was scanned and holds none.`, MET],
  ]],
  ["Theme 05 guide: how the score is used", [
    ["Benchmark 60%: Samsung re-runs our script on one 48 GB GPU or our declared hosted APIs; only the re-run counts",
      `The declared hosted API is Google's (Gemma 4). The GPU holds only the speech models (2.9 GB at peak). A preflight tests every key and tool calling before anything runs, and every model call survives rate limits, server errors and stalls (Chapter 4, section 7).`, MET],
    ["If the script does not reproduce, that portion scores zero: test it on a clean machine",
      `A fresh clone of the repository on a clean Ubuntu system (1 Oct 2026, the submitted code): installation from the lock files, the pinned benchmark and its data, the speech models and the scoring recognizer all completed. A complete end-to-end run on a fresh clone was verified on an earlier revision.`, PART],
    ["Use-case extension 20%: relevant, runs end to end, shown working in the video",
      `DUET for Galaxy (Chapter 7): three modes on the same agent and coordinator; 8 of 8 phone scenarios pass end to end (<a href="${B("scripts/galaxy_check_scenarios.py")}">galaxy_check_scenarios.py</a>).`, MET],
    ["Documentation, architecture and video 20%: a clear README and an honest architecture explanation",
      `README, <a href="${B("docs/ARCHITECTURE.md")}">ARCHITECTURE.md</a>, <a href="${B("docs/RESULTS.md")}">RESULTS.md</a>, <a href="${B("docs/ENGINEERING_NOTES.md")}">ENGINEERING_NOTES.md</a>, and this document. Every stand-in and simulation is stated where it applies.`, MET],
    ["Official evaluation: the LLM judge enabled, one pinned judge for every team; ties break on strict Pass@1",
      `The official scripts run unmodified with --use-llm; gpt-4o is used whenever OPENAI_API_KEY is set. Our reported run: Pass@1 0.81.`, MET],
  ]],
  ["Theme 05 guide: dos and don'ts", [
    ["Cite the public checkpoints and hosted APIs used", `README "Models and providers" and "References".`, MET],
    ["Pin seeds and versions", `Every package including transitive ones (requirements*.lock); Hugging Face revisions of Whisper and Kokoro; the Gemma model, its sampling and seed 7 on every call; the benchmark commit, data SHA-256 and LiveKit checksum.`, MET],
    ["Test the reproduction script on a machine that is not ours", `See the clean-machine row above.`, PART],
    ["Keep the extension honest", `app/README "What is real and what is simulated"; the deck and the video say it on screen.`, MET],
    ["Don't hardcode, memorise or fine-tune on benchmark test items", `Nothing is trained or tuned. <a href="${B("tests/test_voice_integrity.py")}">test_voice_integrity.py</a> fails if any value the benchmark expects appears in the agent.`, MET],
    ["Don't call your own servers at evaluation time", `The only remote services are Google's API (declared) and, if chosen, LiveKit Cloud.`, MET],
    ["Don't cache anything across scenarios", `A fresh coordinator, toolbox and conversation for every room. The warm-up streams only silence; the benchmark's mock backend is stateless.`, MET],
  ]],
  ["Theme 05 brief (launch deck): what to build", [
    ["Keep the conversation alive without losing logical depth", `Two minds: a fast voice covers every wait with a fixed line; the thinker (Gemma 4) reasons over the whole request.`, MET],
    ["Handle and recover from real-time interruptions while driving to the goal", `Epochs and the commit gate: a plan built on words the user has changed never runs; the next turn re-reads everything.`, MET],
    ["Changes of goal without losing the session's context", `The thinker reads everything said since its last spoken answer, and is told what has already been done in the conversation.`, MET],
    ["A solid harness for safe, reliable agentic behaviour", `The coordinator: commit gate, idempotency ledger, failure policy (reads retried, writes never blindly re-sent).`, MET],
    ["Full-duplex: begin before the utterance ends; session-scoped memory only", `Planning starts at each pause, while the user may still be mid-request, and actions wait at the gate. State lives in the room and ends with it.`, MET],
    ["Streaming and async designs; latency; multimodal inputs and outputs", `Speech, model calls and tools run off the audio loop; latency is measured per recording. Voice in and out; the phone adds an on-screen timeline and device readings. Camera input is not used in Round 1.`, MET],
  ]],
  ["Hackathon submission rules (launch deck and FAQ)", [
    ["Working prototype code in a public or shared GitHub repository", `<a href="${REPO}">${REPO.replace("https://", "")}</a> (public).`, MET],
    ["README with reproducible setup instructions, Docker files and other requirements", `README; <a href="${B("Dockerfile")}">Dockerfile</a> (runs the same reproduce.sh); requirements*.lock.`, MET],
    ["Release tag PRISM_GENAI_HACKATHON_Y2026 on the final commit; everything referenced is in that commit", `Created on the final commit when the team submits.`, SUB],
    ["Demo video, at most 5 minutes (YouTube or Drive link)", `In the submission form.`, FORM],
    ["Presentation (PPT or PDF) named CollegeName_TeamName", `SRM_SE7EN.pptx and SRM_SE7EN.pdf.`, MET],
    ["Theme ID, project title, team, problem, solution and architecture, tech stack, innovation, results and limitations in the presentation", `Slides 1 to 8 of SRM_SE7EN.`, MET],
    ["AI usage disclosure form", `Filled in and signed by the team, submitted with the form.`, FORM],
  ]],
  ["Theme 05 FAQ: the organizers' clarifications", [
    ["One GPU with 48 GB (A6000 class); frugal compute is encouraged", `2.9 GB of GPU memory at peak in the reported run; the language model runs at Google.`, MET],
    ["Dependencies from public, safe sources; other domains allowlisted on request", `README "Network access" lists every host the reproduction contacts, and why.`, MET],
    ["A per-scenario wall-clock cap of 300 s", `At most 62 s per conversation (median 38 s); 100 recordings in 116 minutes.`, MET],
  ]],
];
function matrixHtml() {
  return matrix.map(([group, rows]) => `<h3>${esc(group)}</h3><table class="req"><thead><tr><th style="width:36%">Requirement</th><th>Where it is met</th><th style="width:13%">Status</th></tr></thead><tbody>` +
    rows.map(([r, where, st]) => `<tr><td>${esc(r)}</td><td>${where}</td><td><span class="st ${st}">${LABEL[st]}</span></td></tr>`).join("") + `</tbody></table>`).join("");
}

// --- the document ------------------------------------------------------------------------------------
const chapters = [];
function chapter(id, title, html, lead) { chapters.push({ id, title, html, lead }); }

chapter("requirements", "Samsung's requirements, and where each is met", `
<p>Every requirement in Samsung's four documents for this round: the Theme 05 participant guide, the hackathon launch deck, the FAQ, and the AI usage disclosure form. "Submission form" and "At submission" mark items delivered with the Google Form rather than in this repository.</p>
${matrixHtml()}`, "The checklist a reviewer can verify against the repository.");

chapter("overview", "DUET in one chapter", mdToHtml("README.md", dropTitle(sections(read("README.md"),
  ["Results", "Models and providers", "Reproduce the benchmark", "Run logs", "Engineering quality", "Repository map", "References"]))),
"From the repository's README: results, the model declaration, reproduction, run logs.");
chapter("architecture", "Architecture", mdToHtml("docs/ARCHITECTURE.md", dropTitle(read("docs/ARCHITECTURE.md"))),
  "Every component, the turn loop, the coordinator, the model client and the harness.");
chapter("results", "Results", mdToHtml("docs/RESULTS.md", dropTitle(read("docs/RESULTS.md"))),
  "The reported run on all 100 recordings, in detail.");
chapter("engineering", "Engineering notes", mdToHtml("docs/ENGINEERING_NOTES.md", dropTitle(read("docs/ENGINEERING_NOTES.md"))),
  "How the benchmark scores, what we measured, and the decision each measurement led to.");
const shots = ["correction_phone.png", "care_phone.png", "drive_phone.png"].map((f, i) =>
  `<figure class="shot"><img src="file:///${path.join(SHOTS, f).replace(/\\/g, "/")}"><figcaption>${["Assistant: “Set an alarm for 3:30… no, 4:30.” One alarm, 4:30.", "Care: DUET checks the record and stops a second dose.", "Drive: “The office, no wait, home.” Only home is set."][i]}</figcaption></figure>`).join("");
chapter("extension", "Use-case extension: DUET for Galaxy",
  `<div class="shots">${shots}</div><p class="note">Frames from the recorded runs on Gemma 4 (the app's web build, with a scripted user voice; the agent, the model and every tool call are live).</p>` +
  mdToHtml("app/README.md", dropTitle(read("app/README.md"))), "The same agent, coordinator and model on a Samsung phone.");
chapter("next", "Limitations and next steps", `
<h2>Limitations we measured</h2>
<table><tbody>
<tr><td><b>Latency</b></td><td>First response 4.8 s; key information spoken at 14.4 s (GPT-Realtime: 6.9 s). A hosted model call takes 3.3 s at the median and 7.2 s at the 90th percentile.</td></tr>
<tr><td><b>Provider stalls</b></td><td>3 of 100 recordings were lost to model calls Google never answered (504 Deadline Exceeded), despite backups on every key.</td></tr>
<tr><td><b>Hearing</b></td><td>Spelled-out codes are misheard (“D, E, L, bye, V” for DELIV): 7 of 100 items.</td></tr>
<tr><td><b>Response quality</b></td><td>0.73 against GPT-Realtime's 0.79: the answers are correct but plain.</td></tr>
<tr><td><b>Our scoring</b></td><td>Judged by Gemma 4 standing in for gpt-4o; 75 of 100 pass by exact match with no judge at all. Samsung's re-run uses gpt-4o and Parakeet.</td></tr>
<tr><td><b>The extension's demo</b></td><td>Recorded on the app's web build; the home, the medicine schedule and the car are simulated.</td></tr>
</tbody></table>
<h2>Next, as a PRISM worklet</h2>
<ol>
<li><b>On-device Gemma on Galaxy.</b> No round trip and no key limits: the answer's latency drops, and the user's words never leave the phone.</li>
<li><b>Confirm before acting on codes.</b> Recognizer confidence per word; a low-confidence order id is read back before the tool runs.</li>
<li><b>Speculative reads, gated writes.</b> Start safe lookups while the user is still talking; keep every state change behind the commit gate.</li>
<li><b>SmartThings and Android Auto.</b> The Care and Drive modes on real devices, with Knox-consented health reads.</li>
<li><b>Field metrics.</b> Stale-action rate, duplicate-action rate and time to first feedback, measured on real users.</li>
</ol>`, "What we still lose, and where DUET goes next.");
chapter("research", "Appendix: use-case research", mdToHtml("docs/USE_CASE_RESEARCH.md", dropTitle(read("docs/USE_CASE_RESEARCH.md"))),
  "The research behind DUET for Galaxy.");

const cover = `
<section class="cover">
  <div class="kicker">Samsung PRISM GenAI Hackathon 2026-27 · Theme 05 · Interruptible Real-Time Agents</div>
  <h1>DUET</h1>
  <div class="sub">A full-duplex voice agent that acts on what you mean, not on what you said first.</div>
  <div class="doc">Technical documentation</div>
  <div class="tiles">
    <div><b>0.81</b><span>Pass@1 on all 100 recordings of Full-Duplex-Bench v3 (best published: 0.60)</span></div>
    <div><b>0%</b><span>interruptions, and every one of the 100 conversations answered</span></div>
    <div><b>1</b><span>command to reproduce: <code>bash reproduce.sh</code></span></div>
  </div>
  <div class="team"><b>Team SE7EN</b> · SRM Institute of Science and Technology · SRM_SE7EN<br>Tanmay Singh · Pranjal · Naman</div>
  <div class="repo">${REPO}</div>
</section>`;

const glance = `
<section class="chapter" id="glance">
  <div class="chap-no">At a glance</div><h1>DUET in two minutes</h1>
  <p><b>The problem.</b> People interrupt, hesitate and correct themselves mid-sentence. A voice agent that acts on their behalf fails exactly there: the Full-Duplex-Bench v3 paper finds that systems “commit intermediate parameters before the correction arrives.” Under the benchmark's strict Pass@1, one stale or duplicated tool call fails the whole request.</p>
  <p><b>The solution.</b> DUET is a custom LiveKit voice agent with two minds and a referee. A fast voice keeps the conversation alive; a thinker, Google's Gemma 4 26B-A4B, reads everything the user has said and plans the tool calls; and a coordinator decides whether and when any action may run. New words void older plans, nothing runs while the user is still talking, and no action ever runs twice.</p>
  <table class="kv"><tbody>
    <tr><td>Pass@1 (strict)</td><td><b>0.81</b> on all 100 recordings · GPT-Realtime 0.600 · Gemini Live 3.1 0.540</td></tr>
    <tr><td>Tool selection · arguments</td><td>0.915 · 0.850 (the best published: 0.876 · 0.680)</td></tr>
    <tr><td>Self-corrections</td><td>15 of 17 pass (0.88; GPT-Realtime 0.588)</td></tr>
    <tr><td>Conversations answered · interruptions</td><td>100% · 0%</td></tr>
    <tr><td>How it was scored</td><td>The benchmark's own runner and scripts; the judge was Gemma 4 standing in for gpt-4o (75 of 100 by exact match with no judge)</td></tr>
    <tr><td>Language model</td><td>Gemma 4 26B-A4B-it (open weights, Apache 2.0) through Google's API; seed 7</td></tr>
    <tr><td>Use-case extension</td><td>DUET for Galaxy: Assistant, Care and Drive modes on a Samsung phone</td></tr>
  </tbody></table>
  <h2>Reproduce it</h2>
  <pre><code>export GOOGLE_API_KEYS=key1,key2        # keys from Google AI Studio projects without billing
bash reproduce.sh --only travel_19_695bd157114f0d2317f88617   # a quick check
bash reproduce.sh --force               # all 100 recordings, then the official evaluations</code></pre>
  <h2>The submission</h2>
  <table><thead><tr><th>Item</th><th>Where</th></tr></thead><tbody>
    <tr><td>Code and README</td><td><a href="${REPO}">${REPO.replace("https://", "")}</a></td></tr>
    <tr><td>Presentation</td><td>SRM_SE7EN.pptx, SRM_SE7EN.pdf (8 slides)</td></tr>
    <tr><td>This documentation</td><td>SRM_SE7EN_Documentation.pdf</td></tr>
    <tr><td>The reported run</td><td>results/reported/gemma4_final_run: reports, 100 traces, logs</td></tr>
    <tr><td>Extension demo recordings</td><td>results/galaxy/video/</td></tr>
    <tr><td>Demo video, AI usage disclosure</td><td>With the submission form</td></tr>
  </tbody></table>
</section>`;

const toc = `<section class="chapter toc"><div class="chap-no">Contents</div><h1>Contents</h1><ol>
  <li><a href="#glance">DUET in two minutes</a></li>
  ${chapters.map((c) => `<li><a href="#${c.id}">${esc(c.title)}</a><span>${esc(c.lead || "")}</span></li>`).join("")}</ol></section>`;

const body = cover + toc + glance + chapters.map((c, i) => `
<section class="chapter" id="${c.id}"><div class="chap-no">Chapter ${i + 2}</div><h1>${esc(c.title)}</h1>
${c.lead ? `<p class="lead">${esc(c.lead)}</p>` : ""}${c.html}</section>`).join("");

const css = `
@page { size: A4; margin: 18mm 17mm 20mm 17mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10pt; line-height: 1.5; color: #0F1C3F; margin: 0; }
a { color: #1428A0; text-decoration: none; }
h1 { font-size: 22pt; margin: 0 0 6pt; color: #0F1C3F; font-weight: 650; }
h2 { font-size: 14pt; color: #1428A0; margin: 18pt 0 6pt; font-weight: 650; break-after: avoid; }
h3 { font-size: 11.5pt; color: #0F1C3F; margin: 14pt 0 4pt; font-weight: 650; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 8.8pt; background: #F1F4FA; padding: 0 3px; border-radius: 3px; }
pre { background: #F1F4FA; border: 1px solid #D9DFEA; border-radius: 6px; padding: 8pt 10pt; overflow: hidden; white-space: pre-wrap; word-break: break-word; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.5pt; }
table { width: 100%; border-collapse: collapse; margin: 6pt 0 10pt; font-size: 9pt; }
thead { display: table-header-group; }
th { background: #1428A0; color: #fff; text-align: left; font-weight: 600; padding: 5pt 6pt; }
td { border-bottom: 1px solid #D9DFEA; padding: 5pt 6pt; vertical-align: top; }
tr { break-inside: avoid; }
tbody tr:nth-child(even) td { background: #F7F9FC; }
blockquote { margin: 8pt 0; padding: 4pt 12pt; border-left: 3px solid #1428A0; background: #F5F7FB; color: #4F5B73; }
.chapter { break-before: page; }
.chap-no { font-size: 9pt; letter-spacing: .18em; text-transform: uppercase; color: #1428A0; font-weight: 700; margin-bottom: 4pt; }
.lead { font-size: 11pt; color: #4F5B73; margin-top: 0; border-bottom: 2px solid #1428A0; padding-bottom: 8pt; }
.fig { margin: 10pt 0; text-align: center; break-inside: avoid; }
.fig svg { max-width: 100% !important; height: auto; max-height: 150mm; }
.req td:first-child { font-weight: 600; }
.st { display: inline-block; font-size: 8pt; font-weight: 700; padding: 1pt 7pt; border-radius: 9pt; white-space: nowrap; }
.st.met { background: #E4F3EA; color: #13804F; } .st.form { background: #E6EBFA; color: #1428A0; }
.st.sub { background: #FCF1E1; color: #B8680A; } .st.part { background: #FCF1E1; color: #B8680A; }
.kv td:first-child { width: 34%; color: #4F5B73; }
.cover { height: 257mm; background: #1428A0; color: #fff; border-radius: 10px; padding: 26mm 18mm 18mm; position: relative; }
.cover .kicker { font-size: 9.5pt; letter-spacing: .16em; text-transform: uppercase; color: #C8D3FF; font-weight: 700; }
.cover h1 { color: #fff; font-size: 64pt; letter-spacing: .08em; margin: 18mm 0 4mm; }
.cover .sub { font-size: 16pt; color: #D5DDFF; max-width: 150mm; }
.cover .doc { margin-top: 12mm; display: inline-block; background: #fff; color: #1428A0; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; font-size: 10pt; padding: 5pt 14pt; border-radius: 14pt; }
.cover .tiles { display: flex; gap: 6mm; margin-top: 22mm; }
.cover .tiles div { flex: 1; background: #1C33B5; border: 1px solid #3650CC; border-radius: 8px; padding: 6mm; }
.cover .tiles b { display: block; font-size: 26pt; color: #fff; }
.cover .tiles span { font-size: 9pt; color: #D5DDFF; }
.cover .tiles code { background: rgba(255,255,255,.12); color: #fff; }
.cover .team { position: absolute; left: 18mm; bottom: 26mm; font-size: 11pt; color: #fff; line-height: 1.6; }
.cover .repo { position: absolute; left: 18mm; bottom: 16mm; font-size: 9pt; color: #9FB0F5; }
.toc ol { padding-left: 16pt; font-size: 11pt; line-height: 1.5; }
.toc li { margin-bottom: 8pt; } .toc li span { display: block; font-size: 9pt; color: #4F5B73; }
.shots { display: flex; gap: 5mm; justify-content: center; margin: 6pt 0; break-inside: avoid; }
.shot { margin: 0; width: 52mm; text-align: center; }
.shot img { width: 100%; border-radius: 8px; background: #0B0D12; padding: 2mm; }
.shot figcaption { font-size: 8pt; color: #4F5B73; margin-top: 3pt; }
.note { font-size: 8.5pt; color: #8791A5; text-align: center; }
`;

(async () => {
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>DUET: technical documentation (SRM_SE7EN)</title>
<style>${css}</style><script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script></head><body>${body}</body></html>`;
  const tmp = path.join(__dirname, "doc_build.html");
  fs.writeFileSync(tmp, html);
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage();
  await page.goto("file:///" + tmp.replace(/\\/g, "/"));
  await page.waitForFunction(() => window.mermaid !== undefined, null, { timeout: 60000 });
  const failed = await page.evaluate(async () => {
    window.mermaid.initialize({ startOnLoad: false, theme: "base", securityLevel: "loose", fontFamily: "Segoe UI, Arial, sans-serif",
      themeVariables: { primaryColor: "#EEF2FF", primaryTextColor: "#0F1C3F", primaryBorderColor: "#1428A0", lineColor: "#4F5B73",
        secondaryColor: "#F5F7FB", secondaryBorderColor: "#B3BCCC", tertiaryColor: "#FFFFFF", tertiaryBorderColor: "#D9DFEA",
        clusterBkg: "#F5F7FB", clusterBorder: "#B3BCCC", edgeLabelBackground: "#FFFFFF", fontSize: "14px",
        actorBkg: "#EEF2FF", actorBorder: "#1428A0", actorTextColor: "#0F1C3F", signalColor: "#4F5B73", signalTextColor: "#0F1C3F",
        noteBkgColor: "#FCF1E1", noteBorderColor: "#B8680A", noteTextColor: "#0F1C3F", labelBoxBkgColor: "#EEF2FF",
        labelBoxBorderColor: "#1428A0", activationBkgColor: "#E6EBFA", sequenceNumberColor: "#FFFFFF" } });
    let n = 0, bad = 0;
    for (const el of document.querySelectorAll("div.mermaid-src")) {
      const code = new TextDecoder().decode(Uint8Array.from(atob(el.dataset.code), (c) => c.charCodeAt(0)));
      try { const { svg } = await window.mermaid.render("m" + (n++), code); el.outerHTML = svg; }
      catch (e) { bad++; el.insertAdjacentHTML("beforebegin", `<p style="color:#C73A3A">diagram failed: ${String(e).slice(0, 120)}</p>`); }
    }
    return { rendered: n - bad, bad };
  });
  console.log("diagrams", JSON.stringify(failed));
  await page.pdf({ path: OUT, format: "A4", printBackground: true, displayHeaderFooter: true,
    headerTemplate: "<div></div>",
    footerTemplate: `<div style="font-family:Segoe UI,Arial;font-size:7.5px;color:#8791A5;width:100%;padding:0 17mm;display:flex;justify-content:space-between">
      <span>DUET · Team SE7EN (SRM_SE7EN) · Technical documentation · Samsung PRISM GenAI Hackathon 2026-27</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>`,
    margin: { top: "18mm", bottom: "20mm", left: "17mm", right: "17mm" } });
  await browser.close();
  console.log("wrote", OUT);
})().catch((e) => { console.error(e); process.exit(1); });
