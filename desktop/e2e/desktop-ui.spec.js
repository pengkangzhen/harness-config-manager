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
  await expect(page.locator("#tools-heading")).toHaveText("Harness（1）");
  await expect(page.locator(".tool-card .tool-name")).toContainText("Claude Code");
});

test("doctor 健康检查文案跟随语言切换（code 驱动）", async ({ page }) => {
  const scan = {
    doctor: [
      { level: "error", where: "codex: paper-polish", code: "broken_link", params: { target: "/home/dev/.agents/skills/paper-polish" } },
      { level: "error", where: "zcode: MCP zotero", code: "dead_command", params: { command: "/home/dev/.local/bin/zotero-mcp" } },
    ],
    inventory: [{ tool: "claude", display: "Claude Code", installed: true, category: "harness" }],
  };
  await openApp(page, bootData({ halter_scan: scan }));
  await expect(page.locator(".doctor-item").first())
    .toContainText("断链（指向 /home/dev/.agents/skills/paper-polish 不存在）");
  await page.locator("#lang-en").click();
  await expect(page.locator(".doctor-item").first())
    .toContainText("Broken link (target /home/dev/.agents/skills/paper-polish does not exist)");
  await expect(page.locator(".doctor-item").nth(1))
    .toContainText("command points to /home/dev/.local/bin/zotero-mcp which does not exist (dead config, consider removing)");
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

test("切换语言中英文：文案、html lang、矩阵摘要与持久化", async ({ page }) => {
  const scan = {
    doctor: [],
    inventory: [
      {
        tool: "claude", display: "Claude Code", installed: true, category: "harness",
        skills: [{ name: "paper-polish", path: "/s/paper-polish", linked: true }],
      },
    ],
  };
  await openApp(page, bootData({ halter_scan: scan }));
  await expect(page.locator("#tools-heading")).toHaveText("Harness（1）");

  await page.locator("#lang-en").click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("#tools-heading")).toHaveText("Harness (1)");
  await expect(page.locator(".nav-item.active")).toContainText("Overview");
  await expect(page.locator("#view-sessions h1")).toHaveText("Cross-assistant sessions");

  // 已渲染视图的动态文案随语言重渲染
  await page.locator('.nav-item[data-view="matrix"]').click();
  await expect(page.locator("#matrix-summary")).toContainText("1 entries × 1 AI harnesses · 0 gaps");

  // reload 后语言保持英文（localStorage 持久化）
  await page.reload();
  await expect(page.locator("#tools-heading")).toHaveText("Harness (1)");

  // 切回中文
  await page.locator("#lang-zh").click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.locator("#tools-heading")).toHaveText("Harness（1）");
  await expect(page.locator(".nav-item.active")).toContainText("总览");
});
