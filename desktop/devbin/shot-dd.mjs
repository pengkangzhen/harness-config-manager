// 下拉组件（dd）视觉验证：起 ui-server 后截「关闭 / 打开 / 切到远端」三态。
// 用法：node devbin/shot-dd.mjs   （SHOT_PORT 默认 4761，输出 /tmp/dd-*.png）
import { chromium } from "@playwright/test";

const PORT = process.env.SHOT_PORT || 4761;
const OPEN = `.dd-wrap[data-dd-for="global-machine-select"] .dd-btn`;

const browser = await chromium.launch({ headless: true, channel: "chromium-headless-shell" });
const page = await browser.newPage({ viewport: { width: 1352, height: 932 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(OPEN, { timeout: 20000 });
await page.waitForTimeout(2500); // 等 machines list + 后台探测刷新显示名

await page.screenshot({ path: "/tmp/dd-closed.png" });
await page.locator(OPEN).click();
await page.waitForTimeout(400);
await page.screenshot({ path: "/tmp/dd-open.png" });
const item = page.locator('.dd-wrap[data-dd-for="global-machine-select"] .dd-item[data-value="mac"]');
if (await item.count()) {
  await item.click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: "/tmp/dd-mac.png" });
}
if (errors.length) console.error("console errors:\n" + errors.join("\n"));
await browser.close();
console.log("done: /tmp/dd-closed.png /tmp/dd-open.png" + (item ? " /tmp/dd-mac.png" : ""));
