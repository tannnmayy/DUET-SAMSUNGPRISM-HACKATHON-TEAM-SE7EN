// Renders every Mermaid diagram in the documentation, to check that each one parses
// (GitHub shows an error box for one that does not) and to export images for the
// video and slides.
//
//   node app/tools/render_diagrams.js          -> docs/diagrams/<doc>-<n>.svg and .png
//
// Uses the system Chrome (playwright-core) and Mermaid 11 from jsDelivr.
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const ROOT = path.resolve(__dirname, "../..");
const OUT = path.join(ROOT, "docs", "diagrams");
const DOCS = ["README.md", "docs/ARCHITECTURE.md", "docs/RESULTS.md", "docs/USE_CASE_RESEARCH.md", "app/README.md"];

function blocks(file) {
  const text = fs.readFileSync(path.join(ROOT, file), "utf8");
  const found = [];
  const re = /```mermaid\r?\n([\s\S]*?)```/g;
  let m;
  while ((m = re.exec(text))) found.push(m[1]);
  return found;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
  await page.setContent(`<!doctype html><html><head><meta charset="utf-8">
    <style>body{margin:0;background:#fff;font-family:Inter,Segoe UI,Arial,sans-serif} #box{display:inline-block;padding:24px}</style>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script></head>
    <body><div id="box"></div></body></html>`);
  await page.waitForFunction(() => window.mermaid !== undefined, null, { timeout: 60000 });
  await page.evaluate(() => window.mermaid.initialize({ startOnLoad: false, theme: "default", securityLevel: "loose" }));
  let failed = 0, total = 0;
  for (const doc of DOCS) {
    if (!fs.existsSync(path.join(ROOT, doc))) continue;
    const list = blocks(doc);
    const stem = doc.replace(/[\\/]/g, "_").replace(/\.md$/i, "");
    for (let i = 0; i < list.length; i++) {
      total++;
      const name = `${stem}-${i + 1}`;
      const res = await page.evaluate(async ({ code, id }) => {
        try {
          const { svg } = await window.mermaid.render(id, code);
          document.getElementById("box").innerHTML = svg;
          return { ok: true, svg };
        } catch (e) {
          return { ok: false, error: String(e && e.message ? e.message : e) };
        }
      }, { code: list[i], id: "d" + total });
      if (!res.ok) {
        failed++;
        console.log(`FAIL ${name}: ${res.error.split("\n").slice(0, 3).join(" | ")}`);
        continue;
      }
      fs.writeFileSync(path.join(OUT, name + ".svg"), res.svg);
      const box = await page.$("#box");
      await box.screenshot({ path: path.join(OUT, name + ".png") });
      console.log(`ok   ${name}`);
    }
  }
  await browser.close();
  console.log(`${total - failed} of ${total} diagrams rendered`);
  process.exit(failed ? 1 : 0);
})();
