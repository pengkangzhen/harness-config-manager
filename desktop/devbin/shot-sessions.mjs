// Sessions 界面截图工具：先用 `node devbin/ui-server.mjs` 起浏览器桥，
// 再 `node devbin/shot-sessions.mjs` 输出 /tmp/halter-ui-shots/*.png 验证图，
// 以及 docs/images/ 下中/英两张 README 用图（WSL 编不了 Tauri 时的可视化验证手段）。
import { chromium } from "@playwright/test";

const OUT_ZH = new URL("../../docs/images/app-sessions.png", import.meta.url).pathname;
const OUT_EN = new URL("../../docs/images/app-sessions-en.png", import.meta.url).pathname;

const PORT = process.env.SHOT_PORT || 4761;

const browser = await chromium.launch({ headless: true, channel: "chromium-headless-shell" });
// 1352×932 @2x：与仓库既有 README 截图规格一致
const page = await browser.newPage({ viewport: { width: 1352, height: 932 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(500);
await page.click('.nav-item[data-view="sessions"]');
await page.waitForSelector("#sessions-list .session-item", { timeout: 20000 });
await page.waitForTimeout(3000); // 等 halter CLI 扫描完成
await page.screenshot({ path: "/tmp/halter-ui-shots/sessions-timeline.png" });

await page.click("#btn-view-list");
await page.waitForTimeout(300);
await page.screenshot({ path: "/tmp/halter-ui-shots/sessions-list.png" });

await page.click(".session-item");
await page.waitForTimeout(3000);
await page.screenshot({ path: "/tmp/halter-ui-shots/sessions-detail.png" });

// —— 事件流细节验证：展开一个 TOOL 块 ——
const toolHead = page.locator("#session-detail .msg-head.collapsible").first();
if (await toolHead.count()) {
  await toolHead.click();
  await page.waitForTimeout(200);
}
await page.screenshot({ path: "/tmp/halter-ui-shots/sessions-detail-tool.png" });

// —— 原始（raw）视图 ——
await page.click('#session-detail .view-toggle .toggle-btn:nth-child(2)');
await page.waitForTimeout(300);
await page.screenshot({ path: "/tmp/halter-ui-shots/sessions-detail-raw.png" });
await page.click('#session-detail .view-toggle .toggle-btn:nth-child(1)'); // 切回对话视图

// —— 搜索命中词在 transcript 内高亮（容忍零命中）——
await page.fill("#search-input", "的");
await page.press("#search-input", "Enter");
await page.waitForTimeout(4000);
if (await page.locator("#sessions-list .session-item").count()) {
  await page.click("#sessions-list .session-item >> nth=0");
  await page.waitForTimeout(3000);
  await page.screenshot({ path: "/tmp/halter-ui-shots/sessions-search-highlight.png" });
} else {
  console.log("search '的': no hits, skip highlight shot");
}

// ---- README 图对：时间线视图 + 选中第二个会话（时间线上下文更完整）----
const pickSession = async () => {
  const items = page.locator("#sessions-list .session-item");
  const count = await items.count();
  await items.nth(Math.min(1, count - 1)).click();
  await page.waitForSelector("#session-detail:not(.empty)", { timeout: 20000 });
  await page.waitForTimeout(600);
};

// 回到时间线视图再截 README 图（列表视图只留给验证图）
await page.click("#btn-view-timeline");
await page.waitForTimeout(300);
await pickSession();
await page.screenshot({ path: OUT_ZH });

await page.click("#lang-en");
await page.waitForFunction(() =>
  document.querySelector('.nav-item[data-view="overview"]').textContent.includes("Overview"));
await page.waitForTimeout(300);
await pickSession(); // 详情面板重选一次，动态文案才会按英文重渲染
await page.screenshot({ path: OUT_EN });

console.log("page errors:", errors.length ? errors : "none");
console.log("zh ->", OUT_ZH);
console.log("en ->", OUT_EN);
await browser.close();
