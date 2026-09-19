// Playwright 静态 UI 测试：不起 Tauri，直接加载 desktop/ui/index.html，
// 以 window.__TAURI__ mock 驱动桥命令。
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  workers: 1,
  use: {
    headless: true,
    // chromium headless shell 由 `npx playwright install chromium --only-shell` 提供
    channel: "chromium-headless-shell",
  },
});
