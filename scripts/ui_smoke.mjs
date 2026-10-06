// Browser-level smoke test of the main user workflow (17 checks, fails on console errors).
//
//   cd scripts && npm install --no-save puppeteer && node ui_smoke.mjs http://localhost:8080
//
// Set CHROME_PATH to use an installed Chrome/Chromium instead of Puppeteer's download.
// It registers a throwaway user, so run it against a development or demo deployment.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import puppeteer from "puppeteer";
const base = (process.argv[2] ?? "http://127.0.0.1:4173").replace(/\/$/, "");
const browser = await puppeteer.launch({ headless: true, executablePath: process.env.CHROME_PATH || undefined });
const page = await browser.newPage();
const errors = []; const results = [];
const ok = (name, cond) => { results.push(`${cond ? "PASS" : "FAIL"}  ${name}`); };
page.on("pageerror", (e) => errors.push(`${page.url()} :: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error" && !/401|Unauthorized/.test(m.text())) errors.push(`${page.url()} :: ${m.text()}`); });
await page.setViewport({ width: 1440, height: 1000 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = () => page.evaluate(() => document.body.innerText);
const click = async (t) => {
  const b = await page.waitForSelector(`xpath/.//button[contains(., "${t}")]`, { timeout: 20000 });
  await b.click();
};
const input = async (label) => { const [l] = await page.$$(`xpath/.//label[contains(., "${label}")]`); const id = await l.evaluate((n) => n.getAttribute("for")); return page.$(`[id="${id}"]`); };
const go = async (path) => { await page.goto(base + path, { waitUntil: "networkidle0", timeout: 60000 }); await sleep(500); };

// 1. Protected route redirects to login
await go("/dashboard");
ok("anonymous user is redirected to sign-in", page.url().includes("/login"));
// 2. Register through the form (client-side validation first)
await go("/register");
const email = `ui-${Date.now()}@example.com`;
await page.type("input[type=email]", email);
const pw = await page.$$("input[type=password]");
await pw[0].type("short");
await page.click("button[type=submit]"); await sleep(400);
ok("weak password rejected in the form", /at least 10 characters/i.test(await text()));
for (const p of pw) {
  await p.focus();
  await page.keyboard.down("Control"); await page.keyboard.press("KeyA"); await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  await p.type("Ui-Smoke-Pass-1");
}
const nameField = await page.$("input[autocomplete=name]"); if (nameField) await nameField.type("UI Smoke");
await Promise.all([page.click("button[type=submit]"), page.waitForNavigation({ waitUntil: "networkidle0" }).catch(() => {})]);
await sleep(800);
if (page.url().includes("/login")) {
  await page.type("input[type=email]", email); await page.type("input[type=password]", "Ui-Smoke-Pass-1");
  await Promise.all([page.click("button[type=submit]"), page.waitForNavigation({ waitUntil: "networkidle0" })]);
}
ok("registered and signed in", !page.url().includes("/login") && !page.url().includes("/register"));
// 3. Dashboard and fund pages
await go("/dashboard"); const dash = await text();
ok("dashboard shows KPIs and synthetic label", /Sharpe ratio/.test(dash) && /Synthetic/i.test(dash));
await go("/funds"); ok("fund explorer lists 10 funds", ((await text()).match(/FS-[A-Z]+-\d{3}/g) || []).length >= 10);
await go("/funds/3"); ok("short history shows 'not covered'", /not covered/.test(await text()));
await go("/funds/compare?ids=1,2"); ok("comparison renders", /Growth of 100|Compare/i.test(await text()));
await go("/risk"); await click("Calculate correlations"); await sleep(1500); ok("correlation matrix", /Correlation matrix/.test(await text()));
// 4. Portfolio: create through the API in the page, then optimise in the UI
const pid = await page.evaluate(async () => {
  const csrf = document.cookie.split("; ").find((c) => c.startsWith("fs_csrf="))?.split("=")[1];
  const r = await fetch("/api/v1/portfolios", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify({ name: "UI core", initial_value: 100000, assets: [{ fund_id: 1, weight_pct: 50 }, { fund_id: 2, weight_pct: 30 }, { fund_id: 7, weight_pct: 20 }] }) });
  return (await r.json()).id;
});
await go(`/portfolios/${pid}`); await click("Run optimisation");
await page.waitForFunction(() => document.body.innerText.includes("Efficient frontier"), { timeout: 60000 });
ok("optimiser result with frontier", true);
await click("Apply optimised weights"); await sleep(1500);
ok("optimised weights applied", /applied|saved|updated/i.test(await text()));
// 5. What-if and ML
await go("/what-if"); await click("Project"); await sleep(1200); ok("SIP projection", /Invested vs value/i.test(await text()));
await go("/ml"); await click("Train and forecast");
await page.waitForFunction(() => document.body.innerText.includes("Held-out test period") || document.body.innerText.includes("held-out"), { timeout: 90000 });
ok("forecast with backtest", /baseline/i.test(await text()));
// 6. Upload a document through the UI and ask about it
const notePath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "finsense-ui-")), "heron_ui_note.txt");
fs.writeFileSync(notePath, "HERON UI NOTE\n\nSummary\n\nThe Heron Value Fund keeps a cash buffer of 7.5% of net assets at all times.\n");
await go("/documents");
const fileInput = await page.$("input[type=file]"); await fileInput.uploadFile(notePath);
await click("Upload and index");
await page.waitForFunction(() => /indexed/i.test(document.body.innerText) && document.body.innerText.includes("heron_ui_note"), { timeout: 60000 }).catch(() => {});
await sleep(2000); await go("/documents");
ok("uploaded document indexed", /heron_ui_note/i.test(await text()));
await go("/assistant");
await page.type("#question", "What cash buffer does the Heron Value Fund keep?"); await click("Ask");
await page.waitForFunction(() => !document.body.innerText.includes("Retrieving evidence"), { timeout: 30000 }); await sleep(800);
const ans = await text();
ok("assistant answers from the uploaded document with a citation", ans.includes("7.5%") && /S1/.test(ans));
await page.click("#question", { clickCount: 3 }); await page.keyboard.press("Backspace");
await click("New conversation").catch(() => {});
await page.type("#question", "Who is the CEO of Tesla?"); await click("Ask");
await page.waitForFunction(() => !document.body.innerText.includes("Retrieving evidence"), { timeout: 30000 }); await sleep(800);
ok("assistant declines without evidence", /could not find enough evidence|insufficient/i.test(await text()));
await go("/settings"); ok("settings and system status", /System status/.test(await text()));
await go("/admin"); ok("non-admin cannot open administration", !/Import a CSV/.test(await text()));
await browser.close();
results.forEach((r) => console.log(r));
console.log(`CONSOLE/PAGE ERRORS: ${errors.length}`); errors.forEach((e) => console.log("  " + e));
process.exitCode = results.some((r) => r.startsWith("FAIL")) || errors.length ? 1 : 0;
