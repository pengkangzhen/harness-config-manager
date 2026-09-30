// Sync 界面截图工具：先用 `node devbin/ui-server.mjs` 起浏览器桥，
// 再 `node devbin/shot-sync.mjs` 输出中/英两张 README 用图
// （WSL 编不了 Tauri 时的可视化验证手段）。
// 中文版点 dry-run 预览展示计划输出；英文版保持首次打开的干净状态
// （halter CLI 的 stdout 目前只有中文，避免英文界面下混排）。
import { chromium } from "@playwright/test";

const OUT_ZH = new URL("../../docs/images/app-sync.png", import.meta.url).pathname;
const OUT_EN = new URL("../../docs/images/app-sync-en.png", import.meta.url).pathname;

const browser = await chromium.launch({ headless: true, channel: "chromium-headless-shell" });
// 1352×932 @2x：与仓库既有 README 截图规格一致
const page = await browser.newPage({ viewport: { width: 1352, height: 932 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

await page.goto("http://127.0.0.1:4761/", { waitUntil: "domcontentloaded" });
await page.click('.nav-item[data-view="sync"]');
await page.click("#btn-sync-preview");
await page.waitForSelector("#sync-output:not(.hidden)", { timeout: 120000 });
await page.waitForTimeout(600);
await page.screenshot({ path: OUT_ZH });

await page.click("#lang-en");
await page.waitForFunction(() =>
  document.querySelector('.nav-item[data-view="overview"]').textContent.includes("Overview"));
await page.waitForTimeout(300);
// 语言切换不重载页面，中文轮的输出面板残留在 DOM 里；重置回首次打开的隐藏态
await page.evaluate(() => document.getElementById("sync-output").classList.add("hidden"));
await page.screenshot({ path: OUT_EN });

console.log("page errors:", errors.length ? errors : "none");
console.log("zh ->", OUT_ZH);
console.log("en ->", OUT_EN);
await browser.close();
