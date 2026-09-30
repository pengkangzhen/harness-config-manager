// 桌面 UI 静态 e2e：mock Tauri IPC，覆盖 boot / 版本告警 / 任务分发轮询 / matrix 预览。
const { test, expect } = require("@playwright/test");
const { openApp, setHandler, callsWithArgs } = require("./tauri-mock");

const SCAN = {
  doctor: [],
  inventory: [
    { tool: "claude", display: "Claude Code", installed: true, category: "harness" },
    { tool: "vscode", display: "VS Code", installed: false, category: "editor" },
  ],
};

function bootData(extra = {}) {
  return {
    halter_version: { version: "0.1.0", desktop: "0.1.0" },
    halter_scan: SCAN,
    ...extra,
  };
}

test("boot 显示运行时版本与工具总览", async ({ page }) => {
  await openApp(page, bootData());
  await expect(page.locator("#halter-version")).toHaveText("halter 0.1.0");
  await expect(page.locator("#halter-version")).not.toHaveClass(/mismatch/);
  await expect(page.locator("#tools-heading")).toHaveText("工具（1）");
  await expect(page.locator(".tool-card .tool-name")).toContainText("Claude Code");
});

test("runtime 版本与桌面期望不一致时给出 mismatch 告警", async ({ page }) => {
  await openApp(page, bootData({
    halter_version: { version: "0.0.1-old", desktop: "0.1.0" },
  }));
  const badge = page.locator("#halter-version");
  await expect(badge).toHaveClass(/mismatch/);
  await expect(badge).toHaveText(/0\.0\.1-old/);
});

test("matrix 悬停 skill 显示功能与描述预览卡", async ({ page }) => {
  const scan = {
    doctor: [],
    inventory: [
      {
        tool: "claude", display: "Claude Code", installed: true, category: "harness",
        skills: [
          {
            name: "paper-polish",
            path: "/Users/dev/.agents/skills/paper-polish",
            linked: true,
            description: "语言润色 — Academic English paper polishing for LaTeX manuscripts.",
          },
          {
            name: "zotero-paper-fetch",
            path: "/Users/dev/.claude/skills/zotero-paper-fetch",
            linked: false,
            description: "批量检索文献、下载 PDF 并入库 Zotero 的完整管线。",
          },
        ],
      },
    ],
  };
  await openApp(page, bootData({ halter_scan: scan }));
  await page.locator('.nav-item[data-view="matrix"]').click();
  await page.locator(".mx-name", { hasText: "paper-polish" }).first().hover();
  const card = page.locator("#hover-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("语言润色");
  await expect(card).toContainText("库链接");
});
