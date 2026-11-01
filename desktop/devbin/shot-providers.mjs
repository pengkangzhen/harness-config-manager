// providers 面板截图：现状视觉评估用。node devbin/shot-providers.mjs
import { chromium } from "@playwright/test";

const PORT = process.env.SHOT_PORT || 4761;
const browser = await chromium.launch({ headless: true, channel: "chromium-headless-shell" });
const page = await browser.newPage({ viewport: { width: 1352, height: 932 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "domcontentloaded" });
await page.waitForSelector('.nav-item[data-view="providers"]', { timeout: 20000 });
await page.locator('.nav-item[data-view="providers"]').click();
await page.waitForSelector("#providers-list, .pv-card, #pv-cur", { timeout: 20000 });
await page.waitForTimeout(2500);
await page.screenshot({ path: "/tmp/pv-list.png", fullPage: false });
// 打开编辑表单看锁定状态（zhipu 行的编辑按钮）
const editBtn = page.locator(".pv-row button").filter({ hasText: /编辑|edit/i }).first();
if (await editBtn.count()) {
  await editBtn.click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: "/tmp/pv-edit-form.png" });
}
if (errors.length) console.error("console errors:\n" + errors.join("\n"));
await browser.close();
console.log("done: /tmp/pv-list.png" + (editBtn ? " /tmp/pv-edit-form.png" : ""));
