// Screenshots of a page at given moments, to check an animated card before recording it.
//   node app/tools/snap_page.js <page.html> <out-prefix> 6.5 18 27
const path = require("path");
const { chromium } = require("playwright-core");

(async () => {
  const [page, prefix, ...times] = process.argv.slice(2);
  const browser = await chromium.launch({ channel: "chrome", headless: true, args: ["--hide-scrollbars"] });
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: "dark" });
  const tab = await ctx.newPage();
  const url = page.startsWith("http") ? page : "file:///" + path.resolve(page).replace(/\\/g, "/");
  const t0 = Date.now();
  await tab.goto(url);
  for (const t of times.map(Number).sort((a, b) => a - b)) {
    const wait = t * 1000 - (Date.now() - t0);
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    await tab.screenshot({ path: `${prefix}_${String(t).replace(".", "_")}.png` });
    console.log("snap", t);
  }
  await browser.close();
})();
