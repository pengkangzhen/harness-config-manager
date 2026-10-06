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

test("matrix 点击圆点单元格触发定向同步并刷新矩阵", async ({ page }) => {
  // gemini 侧缺 paper-polish（○）；点击该格应定向 sync 并在重扫后变 ●
  const initialScan = {
    doctor: [],
    inventory: [
      {
        tool: "claude", display: "Claude Code", installed: true, category: "harness",
        skills: [{ name: "paper-polish", path: "/s/pp", linked: true }],
      },
      { tool: "gemini", display: "Gemini CLI", installed: true, category: "harness", skills: [] },
    ],
  };
  await openApp(page, bootData({ halter_scan: initialScan }));

  // 同步后 gemini 也有该 skill；两个 handler 经 window 共享翻转状态
  await setHandler(page, "halter_scan", () => () => ({
    doctor: [],
    inventory: [
      {
        tool: "claude", display: "Claude Code", installed: true, category: "harness",
        skills: [{ name: "paper-polish", path: "/s/pp", linked: true }],
      },
      {
        tool: "gemini", display: "Gemini CLI", installed: true, category: "harness",
        skills: window.__geminiLinked
          ? [{ name: "paper-polish", path: "/g/pp", linked: true }]
          : [],
      },
    ],
  }));
  await setHandler(page, "halter_sync", () => async (args) => {
    window.__syncArgs = args;
    window.__geminiLinked = true;
    return { ok: true, code: 0, stdout: "link   gemini:paper-polish -> /g/pp", stderr: "" };
  });

  await page.locator('.nav-item[data-view="matrix"]').click();
  const gapCell = page.locator(".mx-cell.missing").first();
  await expect(gapCell).toHaveClass(/clickable/);
  await gapCell.click();

  // 调用参数：定向到 gemini 的单个单元格（apply + 单层 + tool + items）
  const syncArgs = await page.evaluate(() => window.__syncArgs);
  expect(syncArgs).toEqual({
    apply: true,
    layers: ["skills"],
    tool: "gemini",
    items: ["paper-polish"],
  });

  // toast 展示 CLI 输出；重扫后 gemini 格变 ●（synced，不再可点击）
  await expect(page.locator("#matrix-toast")).toBeVisible();
  await expect(page.locator("#matrix-toast")).toContainText("link   gemini:paper-polish");
  await expect(page.locator(".mx-cell.synced")).toHaveCount(2);
  await expect(page.locator(".mx-cell.missing")).toHaveCount(0);
});

test("matrix 工具栏全量同步：预览、sessions 开关与两步确认", async ({ page }) => {
  await openApp(page, bootData());
  await setHandler(page, "halter_sync", () => async (args) => {
    (window.__fullSyncArgs = window.__fullSyncArgs || []).push(args);
    return { ok: true, code: 0, stdout: "计划: link×1", stderr: "" };
  });
  await page.locator('.nav-item[data-view="matrix"]').click();

  // 预览：dry-run，六个矩阵层 + sessions 全开
  await page.locator("#btn-matrix-preview").click();
  await expect(page.locator("#matrix-sync-output")).toContainText("计划: link×1");
  let args = await page.evaluate(() => window.__fullSyncArgs[0]);
  expect(args.apply).toBe(false);
  expect(args.layers).toEqual(
    ["skills", "agents", "memory", "mcp", "plugins", "hooks", "sessions"]);

  // 关掉 sessions 开关后不再包含
  await page.locator("#matrix-include-sessions").uncheck();
  await page.locator("#btn-matrix-preview").click();
  args = await page.evaluate(() => window.__fullSyncArgs[1]);
  expect(args.layers).not.toContain("sessions");
  await page.locator("#matrix-include-sessions").check();

  // 全量同步：两步确认，第一次点击只武装不调用
  await page.locator("#btn-matrix-apply").click();
  await expect(page.locator("#btn-matrix-apply")).toContainText("再点一次");
  expect(await page.evaluate(() => window.__fullSyncArgs.length)).toBe(2);
  await page.locator("#btn-matrix-apply").click();
  await expect(page.locator("#matrix-sync-output")).toContainText("exit code: 0");
  args = await page.evaluate(() => window.__fullSyncArgs[2]);
  expect(args.apply).toBe(true);
});

test("机器下拉切换远端矩阵：幽灵行推送与行级拉取", async ({ page }) => {  // 本机：claude 有 alpha；远端 desktop：codex 有 beta（交叉差异双向可见）
  const localScan = {
    doctor: [],
    inventory: [
      {
        tool: "claude", display: "Claude Code", installed: true, category: "harness",
        skills: [{ name: "alpha", path: "/s/alpha", linked: true }],
      },
    ],
  };
  const remoteScan = {
    doctor: [],
    inventory: [
      {
        tool: "codex", display: "Codex CLI", installed: true, category: "harness",
        skills: [{ name: "beta", path: "/r/beta", linked: true }],
      },
    ],
  };
  await openApp(page, {
    halter_version: { version: "0.1.0", desktop: "0.1.0" },
    halter_scan: localScan,
    halter_machines_list: {
      count: 1,
      machines: [{ name: "desktop", host: "10.0.0.2", user: null, port: 22, halter_path: "halter" }],
    },
  });

  // 进入矩阵后按 machine 参数分流两种 scan（闭包数据经 bindings 传入页面）
  await setHandler(page, "halter_scan",
    ({ local, remote }) => (args) => (args && args.machine ? remote : local),
    { local: localScan, remote: remoteScan });
  await setHandler(page, "halter_push", () => async (args) => {
    (window.__pushArgs = window.__pushArgs || []).push(args);
    return { ok: true, code: 0, stdout: `add    ${args.item}`, stderr: "" };
  });

  await page.locator('.nav-item[data-view="matrix"]').click();
  await expect(page.locator("#matrix-machine-select")).toContainText("desktop");
  await page.locator("#matrix-machine-select").selectOption("desktop");

  // 远端矩阵 = codex 列；本机对照产生幽灵行 alpha 与 beta 的拉取按钮
  await expect(page.locator(".mx-tool-head", { hasText: "codex" })).toBeVisible();
  await expect(page.locator(".mx-name.ghost", { hasText: "alpha" })).toBeVisible();
  await expect(page.locator(".mx-ghost-sep")).toContainText("desktop");
  await expect(page.locator(".mx-name .mx-pull-btn")).toHaveCount(1); // beta 行

  // 幽灵行推送：本机 alpha -> desktop
  await page.locator(".mx-cell.ghost-push").click();
  // 行级拉取：desktop 的 beta -> 本机
  await page.locator(".mx-pull-btn").click();
  const pushes = await page.evaluate(() => window.__pushArgs);
  expect(pushes[0]).toEqual({
    layer: "skills", item: "alpha", to: "desktop", from: null, withSecrets: false,
  });
  expect(pushes[1]).toEqual({
    layer: "skills", item: "beta", to: null, from: "desktop", withSecrets: false,
  });
  await expect(page.locator("#matrix-toast")).toContainText("add    beta");
});

// ---------------- providers 面板 ----------------

const PROVIDERS = {
  count: 2,
  presets: [
    {
      name: "zhipu", label: "Zhipu GLM", tools: ["claude", "codex"],
      urls: {
        claude: "https://open.bigmodel.cn/api/anthropic",
        codex: "https://open.bigmodel.cn/api/codex",
      },
    },
    {
      name: "deepseek", label: "DeepSeek", tools: ["claude"],
      urls: { claude: "https://api.deepseek.com/anthropic" },
    },
  ],
  providers: [
    {
      id: "zhipu", label: "Zhipu GLM", tools: ["claude", "codex"], builtin: false,
      claude: { base_url: "https://open.bigmodel.cn/api/anthropic", model: "glm-5.3[1M]" },
      codex: { base_url: "http://127.0.0.1:8787/api/v1", model: "glm-5.3" },
    },
    { id: "official", label: "official", tools: ["claude", "codex"], builtin: true },
  ],
  current: {
    claude: {
      status: "external", provider: "",
      base_url: "https://open.bigmodel.cn/api/anthropic",
      model: "glm-5.3-flash[1M]", detail: "端点不在 halter 清单中",
    },
    codex: {
      status: "halter", provider: "zhipu",
      base_url: "http://127.0.0.1:8787/api/v1", model: "glm-5.3", detail: "",
    },
  },
};

test("providers：external 当前卡、列表渲染与一键切换参数", async ({ page }) => {
  await openApp(page, bootData({ halter_providers_list: PROVIDERS }));
  await setHandler(page, "halter_providers_list",
    ({ base }) => () => base, { base: PROVIDERS });
  await setHandler(page, "halter_providers_switch", () => async (args) => {
    window.__switchArgs = args;
    return { ok: true, code: 0, stdout: `claude: ${args.id} → https://open.bigmodel.cn/api/anthropic`, stderr: "" };
  });

  await page.locator('.nav-item[data-view="providers"]').click();

  // 当前卡：external 状态 + 端点 + 收编提示
  await expect(page.locator(".pv-current .pv-status")).toHaveText(/外部工具配置/);
  await expect(page.locator(".pv-current .dim")).toContainText("open.bigmodel.cn");
  await expect(page.locator(".pv-external-hint")).toContainText("收编现有配置");

  // 列表：zhipu 可切换（非激活），official 内置徽标；codex 页签下 zhipu 是当前
  await expect(page.locator(".pv-row")).toHaveCount(2);
  await expect(page.locator(".pv-row", { hasText: "official" }).locator(".pv-badge")).toHaveText("内置");
  const zhipuRow = page.locator(".pv-row", { hasText: "zhipu" });
  await zhipuRow.locator("button", { hasText: "切换" }).click();

  const switchArgs = await page.evaluate(() => window.__switchArgs);
  expect(switchArgs).toEqual({ id: "zhipu", tool: "claude" });
  await expect(page.locator("#matrix-toast")).toContainText("claude: zhipu →");
});

test("providers：codex 页签显示当前徽标；添加表单提交 camelCase 参数", async ({ page }) => {
  await openApp(page, bootData({ halter_providers_list: PROVIDERS }));
  await setHandler(page, "halter_providers_list", ({ base }) => () => base, { base: PROVIDERS });
  await setHandler(page, "halter_providers_add", () => async (args) => {
    window.__addArgs = args;
    return { ok: true, code: 0, stdout: "新增 provider deepseek", stderr: "" };
  });

  await page.locator('.nav-item[data-view="providers"]').click();
  // codex 页签：zhipu 是当前激活 → 无切换按钮，带「当前」徽标
  await page.locator(".layer-tab", { hasText: "Codex" }).click();
  const zhipuRow = page.locator(".pv-row", { hasText: "zhipu" });
  await expect(zhipuRow.locator(".pv-badge.current")).toHaveText("当前");
  await expect(zhipuRow.locator("button", { hasText: "切换" })).toHaveCount(0);

  // 添加表单：填基础字段提交，参数为 camelCase；token 原样经 IPC 传递
  await page.locator("#btn-providers-add").click();
  await page.locator("#pv-id").fill("deepseek");
  await page.locator("#pv-base-url").fill("https://api.deepseek.com/api/anthropic");
  await page.locator("#pv-model").fill("deepseek-chat");
  await page.locator("#pv-token").fill("sk-test-token");
  await page.locator('#provider-add-form button[type="submit"]').click();

  const addArgs = await page.evaluate(() => window.__addArgs);
  expect(addArgs).toEqual({
    id: "deepseek",
    tool: "claude",
    baseUrl: "https://api.deepseek.com/api/anthropic",
    label: null,
    model: "deepseek-chat",
    token: "sk-test-token",
  });
  await expect(page.locator("#matrix-toast")).toContainText("新增 provider deepseek");
});

test("providers：删除两步确认，第二次点击才真正调用", async ({ page }) => {
  await openApp(page, bootData({ halter_providers_list: PROVIDERS }));
  await setHandler(page, "halter_providers_list", ({ base }) => () => base, { base: PROVIDERS });
  await setHandler(page, "halter_providers_remove", () => async (args) => {
    (window.__removeCalls = window.__removeCalls || []).push(args);
    return { ok: true, code: 0, stdout: "已删除 zhipu（清单 + token）", stderr: "" };
  });

  await page.locator('.nav-item[data-view="providers"]').click();
  const removeBtn = page.locator(".pv-row", { hasText: "zhipu" })
    .locator("button", { hasText: "删除" });
  await removeBtn.click();
  await expect(removeBtn).toHaveText("确认删除？");
  expect(await page.evaluate(() => window.__removeCalls || [])).toHaveLength(0);  // 第一次只武装
  await removeBtn.click();
  expect(await page.evaluate(() => window.__removeCalls)).toEqual([{ id: "zhipu" }]);
});

test("providers：预设下拉自动填充端点，不含当前工具时切工具", async ({ page }) => {
  await openApp(page, bootData({ halter_providers_list: PROVIDERS }));
  await setHandler(page, "halter_providers_list", ({ base }) => () => base, { base: PROVIDERS });

  await page.locator('.nav-item[data-view="providers"]').click();
  await page.locator("#btn-providers-add").click();
  await expect(page.locator("#pv-preset")).toContainText("自定义（手填端点）");
  await expect(page.locator("#pv-preset")).toContainText("zhipu · Zhipu GLM");

  // 选 zhipu：当前工具 claude 有块 → 直接填端点
  await page.locator("#pv-preset").selectOption("zhipu");
  await expect(page.locator("#pv-base-url")).toHaveValue("https://open.bigmodel.cn/api/anthropic");
  await expect(page.locator("#pv-label")).toHaveValue("Zhipu GLM");

  // 换到 codex 工具不自动变端点；选 deepseek（无 codex 块）→ 自动切回 claude 并填端点
  await page.locator("#pv-tool").selectOption("codex");
  await page.locator("#pv-preset").selectOption("deepseek");
  await expect(page.locator("#pv-tool")).toHaveValue("claude");
  await expect(page.locator("#pv-base-url")).toHaveValue("https://api.deepseek.com/anthropic");
});

test("providers：文案跟随语言切换", async ({ page }) => {
  await openApp(page, bootData({ halter_providers_list: PROVIDERS }));
  await page.locator('.nav-item[data-view="providers"]').click();
  await expect(page.locator("#view-providers h1")).toHaveText("模型供应商");
  await expect(page.locator(".pv-current .pv-cur-label")).toHaveText("当前激活");

  await page.locator("#lang-en").click();
  await expect(page.locator("#view-providers h1")).toHaveText("Model Providers");
  await expect(page.locator(".pv-current .pv-cur-label")).toHaveText("Active");
  await expect(page.locator(".pv-current .pv-status")).toHaveText(/external tool config/);
});
