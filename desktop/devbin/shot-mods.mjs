// Mods 页签截图（mock 数据）：视觉评估用，截图输出 /tmp 不入库。node devbin/shot-mods.mjs
import { chromium } from "@playwright/test";
import { pathToFileURL, fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UI_URL = pathToFileURL(path.join(__dirname, "..", "ui", "index.html")).href;

const SCAN = {
  doctor: [],
  inventory: [
    {
      tool: "claude", display: "Claude Code", installed: true, category: "harness",
      plugins: [{ plugin_id: "cc-diff@claude-plugins-official", version: "1.0.0", enabled: true }],
      mods: [
        { plugin_id: "cc-diff@claude-plugins-official", origin: "marketplace", version: "1.0.0", enabled: true, marketplace: "claude-plugins-official", modules: ["./register.js"] },
        { plugin_id: "cc-plugin-you-should-know@builtin", origin: "builtin", version: null, enabled: true, marketplace: null, modules: [] },
      ],
    },
    { tool: "zcode", display: "ZCode", installed: true, category: "harness", mods: [
      { plugin_id: "cc-diff@zcode-plugins-official", origin: "marketplace", version: "1.0.0", enabled: null, marketplace: "zcode-plugins-official", modules: ["./register.js"] },
    ] },
    { tool: "gemini", display: "Gemini CLI", installed: true, category: "harness" },
  ],
};

const browser = await chromium.launch({ headless: true, channel: "chromium-headless-shell" });
const page = await browser.newPage({ viewport: { width: 1352, height: 932 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

await page.addInitScript((scan) => {
  const handlers = {
    halter_version: () => ({ version: "0.1.0", desktop: "0.1.0" }),
    halter_scan: () => scan,
    halter_machines_list: () => [],
  };
  window.__TAURI__ = {
    core: { invoke: (cmd, args) => Promise.resolve(handlers[cmd]?.(args)) },
    event: { listen: async () => () => {} },
  };
}, SCAN);

await page.goto(UI_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector('.nav-item[data-view="matrix"]', { timeout: 20000 });
await page.locator('.nav-item[data-view="matrix"]').click();
await page.locator(".layer-tab", { hasText: "Mods" }).click();
await page.waitForSelector(".mx-cell", { timeout: 20000 });
await page.waitForTimeout(600);
// 悬停 marketplace mod 行，展示预览卡（版本 · 市场）
await page.locator(".mx-name", { hasText: "cc-diff" }).first().hover();
await page.waitForTimeout(600);
await page.screenshot({ path: "/tmp/mx-mods.png" });
if (errors.length) console.error("console errors:\n" + errors.join("\n"));
await browser.close();
console.log("done: /tmp/mx-mods.png");
