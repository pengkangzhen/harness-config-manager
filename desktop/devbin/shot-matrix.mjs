// Matrix 界面截图工具：先用 `node devbin/ui-server.mjs` 起浏览器桥，
// 再 `node devbin/shot-matrix.mjs` 输出中/英两张 README 用图
// （WSL 编不了 Tauri 时的可视化验证手段）。
import { chromium } from "@playwright/test";

const OUT_ZH = new URL("../../docs/images/app-matrix.png", import.meta.url).pathname;
const OUT_EN = new URL("../../docs/images/app-matrix-en.png", import.meta.url).pathname;

const PORT = process.env.SHOT_PORT || 4761;

const browser = await chromium.launch({ headless: true, channel: "chromium-headless-shell" });
// 1352×932 @2x：与仓库既有 README 截图规格一致
const page = await browser.newPage({ viewport: { width: 1352, height: 932 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "domcontentloaded" });
await page.click('.nav-item[data-view="matrix"]');
await page.waitForSelector("#matrix-content:not(.hidden)", { timeout: 20000 });
await page.waitForTimeout(600); // 等矩阵渲染稳定
await page.screenshot({ path: OUT_ZH });

await page.click("#lang-en");
await page.waitForFunction(() =>
  document.querySelector('.nav-item[data-view="overview"]').textContent.includes("Overview"));
await page.waitForTimeout(300);
await page.screenshot({ path: OUT_EN });

console.log("page errors:", errors.length ? errors : "none");
console.log("zh ->", OUT_ZH);
console.log("en ->", OUT_EN);
await browser.close();
