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

// 设置浮层：语言 / 主题 / 关于（版本徽章）都收在侧栏底部 ⚙ 按钮之后
async function openSettings(page) {
  await page.locator("#btn-settings").click();
  await expect(page.locator("#settings-panel")).toBeVisible();
}

// 自定义下拉（dd 组件替代原生 select）：点开按钮 → 点菜单项
async function ddChoose(page, id, value) {
  const wrap = page.locator(`.dd-wrap[data-dd-for="${id}"]`);
  await wrap.locator(".dd-btn").click();
  await wrap.locator(`.dd-item[data-value="${value}"]`).click();
}

test("boot 显示运行时版本与工具总览", async ({ page }) => {
  await openApp(page, bootData());
  await openSettings(page);
  await expect(page.locator("#halter-version")).toBeVisible();
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
  await openSettings(page);
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
  await openSettings(page);
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
  await expect(card).toContainText("已链接工具");
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

  await openSettings(page);
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
  await openSettings(page);
  await page.locator("#lang-zh").click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.locator("#tools-heading")).toHaveText("Harness（1）");
  await expect(page.locator(".nav-item.active")).toContainText("总览");
});

test("设置面板：主题三档切换与持久化、关于版本", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await openApp(page, bootData());
  await openSettings(page);

  // 默认跟随系统（emulate dark → 深色）
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("#theme-auto")).toHaveClass(/active/);

  // 切浅色：主题属性即时切换，reload 后仍为浅色（localStorage 持久化）
  await page.locator("#theme-light").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  // 关于：版本徽章收进设置面板
  await openSettings(page);
  await expect(page.locator("#halter-version")).toHaveText("halter 0.1.0");

  // 切回跟随系统（深色）；点面板外自动收起
  await page.locator("#theme-auto").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.locator("#view-overview h1").click();
  await expect(page.locator("#settings-panel")).toBeHidden();
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

  // 预览：dry-run，七个矩阵层 + sessions 全开
  await page.locator("#btn-matrix-preview").click();
  await expect(page.locator("#matrix-sync-output")).toContainText("计划: link×1");
  let args = await page.evaluate(() => window.__fullSyncArgs[0]);
  expect(args.apply).toBe(false);
  expect(args.layers).toEqual(
    ["skills", "agents", "memory", "statusline", "mcp", "plugins", "hooks", "sessions"]);

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
      count: 2,
      local: { host_name: "WSLBOX", os: "wsl" },
      machines: [
        { name: "desktop", host: "10.0.0.2", user: null, port: 22, halter_path: "halter",
          local: false, host_name: "studio.example", os: "linux" },
        { name: "self", host: "127.0.0.1", user: null, port: 2222, halter_path: "halter", local: true },
      ],
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
  const machineDD = page.locator('.dd-wrap[data-dd-for="global-machine-select"]');
  // 本机选项显示真实主机名；远端显示远端主机名（sub = ssh 名）
  await expect(machineDD.locator(".dd-btn")).toContainText("WSLBOX");
  await machineDD.locator(".dd-btn").click();
  await expect(machineDD.locator(".dd-menu")).toContainText("studio.example");
  // 回环条目（local）即本机，折叠不进下拉框
  await expect(machineDD.locator(".dd-menu")).not.toContainText("self");
  await machineDD.locator('.dd-item[data-value="desktop"]').click();

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

test("机器作用域：切换后 sessions/memory/providers 请求都带 machine 并持久化", async ({ page }) => {
  // 先选机器、视图在机器之下：侧栏全局选择器切换时重载当前视图，
  // 其余视图在进入时按当前机器取数；选择持久化到 localStorage
  await openApp(page, {
    halter_version: { version: "0.1.0", desktop: "0.1.0" },
    halter_scan: SCAN,
    halter_machines_list: {
      count: 2,
      local: { host_name: "WSLBOX", os: "wsl" },
      machines: [
        { name: "desktop", host: "10.0.0.2", user: null, port: 22, halter_path: "halter",
          local: false, host_name: "studio.example", os: "linux" },
        { name: "self", host: "127.0.0.1", user: null, port: 2222, halter_path: "halter", local: true },
      ],
    },
  });
  await setHandler(page, "halter_sessions_projects", () => (args) => {
    window.__projArgs = args;
    return { count: 0, projects: [] };
  });
  await setHandler(page, "halter_sessions_list", () => (args) => {
    window.__listArgs = args;
    return { project: "all", count: 0, sessions: [] };
  });
  await setHandler(page, "halter_memory_show", () => (args) => {
    window.__memArgs = args;
    return { library: { path: "/h/.agents/AGENTS.md", exists: true, content: "x" }, tools: [] };
  });
  await setHandler(page, "halter_providers_list", () => (args) => {
    window.__pvArgs = args;
    return {
      count: 0, providers: [], presets: [],
      current: {
        claude: { status: "official", provider: "", base_url: "", model: "" },
        codex: { status: "official", provider: "", base_url: "", model: "" },
      },
    };
  });

  // 本机：sessions 不带 machine
  await page.locator('.nav-item[data-view="sessions"]').click();
  await page.waitForFunction(() => window.__listArgs);
  expect((await page.evaluate(() => window.__listArgs)).machine).toBeUndefined();

  // 切到 desktop：当前 sessions 视图自动重载并携带 machine
  await ddChoose(page, "global-machine-select", "desktop");
  await page.waitForFunction(() => window.__listArgs && window.__listArgs.machine === "desktop");
  expect((await page.evaluate(() => window.__projArgs)).machine).toBe("desktop");

  // 其余视图进入时也在所选机器之下
  await page.locator('.nav-item[data-view="memory"]').click();
  await page.waitForFunction(() => window.__memArgs && window.__memArgs.machine === "desktop");
  await page.locator('.nav-item[data-view="providers"]').click();
  await page.waitForFunction(() => window.__pvArgs && window.__pvArgs.machine === "desktop");

  // reload 后 boot 恢复机器作用域（选择器值 + localStorage）
  await page.reload();
  await expect(page.locator("#global-machine-select")).toHaveValue("desktop");
  expect(await page.evaluate(() => localStorage.getItem("halter-machine"))).toBe("desktop");
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
      claude: { base_url: "https://open.bigmodel.cn/api/anthropic", model: "glm-5.3[1M]",
                env: { ANTHROPIC_DEFAULT_SONNET_MODEL: "glm-5.3[1M]",
                       ANTHROPIC_DEFAULT_HAIKU_MODEL: "glm-5.3-flash[1M]" } },
      codex: { base_url: "http://127.0.0.1:8787/api/v1", model: "glm-5.3", wire_api: "responses" },
    },
    { id: "official", label: "official", tools: ["claude", "codex"], builtin: true },
  ],
  current: {
    claude: {
      status: "external", provider: "",
      base_url: "https://open.bigmodel.cn/api/anthropic",
      model: "glm-5.3-flash[1M]", detail: "端点不在 halter 清单中",
      env: {
        ANTHROPIC_BASE_URL: "https://open.bigmodel.cn/api/anthropic",
        ANTHROPIC_MODEL: "glm-5.3-flash[1M]",
        ANTHROPIC_DEFAULT_SONNET_MODEL: "glm-5.3-flash[1M]",
      },
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

  // 当前卡：external 状态 + 端点 + 收编提示 + 「编辑」带入实况参数
  await expect(page.locator(".pv-current .pv-status")).toHaveText(/外部工具配置/);
  await expect(page.locator(".pv-current .dim")).toContainText("open.bigmodel.cn");
  await expect(page.locator(".pv-external-hint")).toContainText("收编现有配置");
  await page.locator(".pv-current").dblclick();   // 双击外部配置卡进入收编表单
  await expect(page.locator("#pv-form-title")).toHaveText("编辑当前外部配置（收编进清单）");
  // 收编带入实况全部扁平托管键（端点/模型/档位映射）
  expect(JSON.parse(await page.locator("#pv-def").inputValue())).toEqual({
    ANTHROPIC_BASE_URL: "https://open.bigmodel.cn/api/anthropic",
    ANTHROPIC_MODEL: "glm-5.3-flash[1M]",
    ANTHROPIC_DEFAULT_SONNET_MODEL: "glm-5.3-flash[1M]" });
  // 实况端点命中 zhipu 预设 → 预选之（收编时一眼看出厂商归属；标识无字段，保存时自动生成）
  await expect(page.locator("#pv-preset")).toHaveValue("zhipu");
  await expect(page.locator("#pv-preset")).toBeEnabled();
  await expect(page.locator("#pv-tool")).toBeDisabled();
  await page.locator("#btn-providers-form-close").click();

  // 列表：zhipu 可切换（非激活），official 内置徽标；codex 页签下 zhipu 是当前
  await expect(page.locator(".pv-row")).toHaveCount(2);
  await expect(page.locator(".pv-row", { hasText: "official" }).locator(".pv-badge")).toHaveText("内置");
  const zhipuRow = page.locator(".pv-row", { hasText: "zhipu" });
  await zhipuRow.locator("button", { hasText: "切换" }).click();

  const switchArgs = await page.evaluate(() => window.__switchArgs);
  expect(switchArgs).toEqual({ id: "zhipu", tool: "claude" });
  await expect(page.locator("#matrix-toast")).toContainText("claude: zhipu →");
});

test("providers：codex 页签显示当前徽标；添加表单以配置 JSON 提交", async ({ page }) => {
  await openApp(page, bootData({ halter_providers_list: PROVIDERS }));
  await setHandler(page, "halter_providers_list", ({ base }) => () => base, { base: PROVIDERS });
  await setHandler(page, "halter_providers_add", () => async (args) => {
    window.__addArgs = args;
    return { ok: true, code: 0, stdout: "新增 provider deepseek", stderr: "" };
  });

  await page.locator('.nav-item[data-view="providers"]').click();
  // codex 页签：zhipu 是当前激活（halter 态）→ 当前卡无动作行，行内无切换按钮
  await page.locator(".layer-tab", { hasText: "Codex" }).click();
  await expect(page.locator(".pv-current")).toHaveCount(0);   // halter 态：激活信息由列表行 active 徽标表达，无顶部卡
  const zhipuRow = page.locator(".pv-row", { hasText: "zhipu" });
  await expect(zhipuRow.locator(".pv-badge.current")).toHaveText("当前");
  await expect(zhipuRow.locator("button", { hasText: "切换" })).toHaveCount(0);

  // 添加表单（ccswitch 式配置 JSON）：填 JSON + token 提交；token 原样经 IPC 传递。
  // 无预设 → 标识自动取端点域名 api.deepseek.com（codex 页签，工具跟随页签）
  await page.locator("#btn-providers-add").click();
  await page.locator("#pv-def").fill(JSON.stringify(
    { base_url: "https://api.deepseek.com/api/anthropic", model: "deepseek-chat" }));
  await page.locator("#pv-token").fill("sk-test-token");
  await page.locator('#provider-add-form button[type="submit"]').click();

  const addArgs = await page.evaluate(() => window.__addArgs);
  expect(addArgs).toEqual({
    id: "api.deepseek.com",
    tool: "codex",
    def: '{"base_url":"https://api.deepseek.com/api/anthropic","model":"deepseek-chat"}',
    label: null,
    token: "sk-test-token",
  });
  await expect(page.locator("#matrix-toast")).toContainText("已保存 api.deepseek.com");
});

test("providers：编辑表单预填配置 JSON 并整体替换提交（编辑按钮 → halter_providers_edit）", async ({ page }) => {
  await openApp(page, bootData({ halter_providers_list: PROVIDERS }));
  await setHandler(page, "halter_providers_list", ({ base }) => () => base, { base: PROVIDERS });
  await setHandler(page, "halter_providers_edit", () => async (args) => {
    window.__editArgs = args;
    return { ok: true, code: 0, stdout: "更新 zhipu 的 claude 块", stderr: "" };
  });
  await setHandler(page, "halter_providers_rename", () => async (args) => {
    window.__renameArgs = args;
    return { ok: true, code: 0, stdout: "zhipu → zhipu9", stderr: "" };
  });

  await page.locator('.nav-item[data-view="providers"]').click();
  // official 内置行没有编辑按钮；zhipu 行点击编辑
  await expect(page.locator(".pv-row", { hasText: "official" })
    .locator("button", { hasText: "编辑" })).toHaveCount(0);
  await page.locator(".pv-row", { hasText: "zhipu" })
    .locator("button", { hasText: "编辑" }).click();

  // 编辑模式：标题带 id、工具/预设锁定、现有块预填为配置 JSON、token 占位符提示保持不变
  await expect(page.locator("#pv-form-title")).toHaveText("编辑供应商 zhipu（claude）");
  await expect(page.locator("#pv-tool")).toBeDisabled();
  await expect(page.locator("#pv-preset")).toBeDisabled();
  await expect(page.locator("#pv-preset")).toHaveValue("zhipu");   // 锁定态按端点展示厂商
  await expect(page.locator("#pv-label")).toHaveValue("Zhipu GLM");
  await expect(page.locator("#pv-id")).toBeVisible();   // 编辑模式解锁 ID（可改名）
  await expect(page.locator("#pv-id")).toHaveValue("zhipu");
  await expect(page.locator("#pv-token")).toHaveAttribute("placeholder", "API token（留空 = 保持不变）");
  expect(JSON.parse(await page.locator("#pv-def").inputValue())).toEqual({
    ANTHROPIC_BASE_URL: "https://open.bigmodel.cn/api/anthropic",
    ANTHROPIC_MODEL: "glm-5.3[1M]",
    ANTHROPIC_DEFAULT_SONNET_MODEL: "glm-5.3[1M]",
    ANTHROPIC_DEFAULT_HAIKU_MODEL: "glm-5.3-flash[1M]",
  });

  // 整体替换：加 Opus 档、删 Sonnet 档、Haiku 档保持、换主模型；JSON 里粘了 token
  // → 保存前剥离出 def，经 token 参数走 stdin（def 串不含密钥）
  await page.locator("#pv-def").fill(JSON.stringify({
    ANTHROPIC_BASE_URL: "https://open.bigmodel.cn/api/anthropic",
    ANTHROPIC_MODEL: "glm-5.4[1M]",
    ANTHROPIC_DEFAULT_OPUS_MODEL: "glm-5.4[1M]",
    ANTHROPIC_DEFAULT_HAIKU_MODEL: "glm-5.3-flash[1M]",
    ANTHROPIC_AUTH_TOKEN: "sk-json-pasted",
  }));
  // ID 改为 zhipu9 → 保存先 rename（token 键随迁），再以新 id 提交 edit
  await page.locator("#pv-id").fill("zhipu9");
  await page.locator('#provider-add-form button[type="submit"]').click();
  expect(await page.evaluate(() => window.__renameArgs))
    .toEqual({ old: "zhipu", new: "zhipu9", machine: undefined });
  const editArgs = await page.evaluate(() => window.__editArgs);
  expect(editArgs).toEqual({
    id: "zhipu9",
    tool: "claude",
    def: '{"ANTHROPIC_BASE_URL":"https://open.bigmodel.cn/api/anthropic","ANTHROPIC_MODEL":"glm-5.4[1M]",'
      + '"ANTHROPIC_DEFAULT_OPUS_MODEL":"glm-5.4[1M]",'
      + '"ANTHROPIC_DEFAULT_HAIKU_MODEL":"glm-5.3-flash[1M]"}',
    label: "Zhipu GLM",
    token: "sk-json-pasted",
  });
  await expect(page.locator("#matrix-toast")).toContainText("更新 zhipu9");

  // 重新点「添加供应商」恢复新增模式：JSON 回到 claude 模板，ID 字段收回
  await page.locator("#btn-providers-add").click();
  await expect(page.locator("#pv-form-title")).toHaveText("添加供应商");
  await expect(page.locator("#pv-id")).toBeHidden();
  expect(JSON.parse(await page.locator("#pv-def").inputValue()))
    .toEqual({ ANTHROPIC_BASE_URL: "" });
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

test("providers：预设下拉把端点合入配置 JSON，不含当前工具时切工具", async ({ page }) => {
  await openApp(page, bootData({ halter_providers_list: PROVIDERS }));
  await setHandler(page, "halter_providers_list", ({ base }) => () => base, { base: PROVIDERS });

  await page.locator('.nav-item[data-view="providers"]').click();
  await page.locator("#btn-providers-add").click();
  await expect(page.locator("#pv-preset")).toContainText("自定义（手填端点）");
  await expect(page.locator("#pv-preset")).toContainText("zhipu · Zhipu GLM");

  // 选 zhipu：当前工具 claude 有块 → 端点合入 JSON（claude 为扁平托管键）、显示名跟随
  await ddChoose(page, "pv-preset", "zhipu");
  expect(JSON.parse(await page.locator("#pv-def").inputValue()).ANTHROPIC_BASE_URL)
    .toBe("https://open.bigmodel.cn/api/anthropic");
  await expect(page.locator("#pv-label")).toHaveValue("Zhipu GLM");

  // 换到 codex 工具换模板；选 deepseek（无 codex 块）→ 自动切回 claude 并合入端点
  await ddChoose(page, "pv-tool", "codex");
  await ddChoose(page, "pv-preset", "deepseek");
  await expect(page.locator("#pv-tool")).toHaveValue("claude");
  expect(JSON.parse(await page.locator("#pv-def").inputValue()).ANTHROPIC_BASE_URL)
    .toBe("https://api.deepseek.com/anthropic");
});

test("providers：文案跟随语言切换", async ({ page }) => {
  await openApp(page, bootData({ halter_providers_list: PROVIDERS }));
  await page.locator('.nav-item[data-view="providers"]').click();
  await expect(page.locator("#view-providers h1")).toHaveText("模型供应商");
  await expect(page.locator(".pv-current .pv-cur-label")).toHaveText("当前激活");

  await openSettings(page);
  await page.locator("#lang-en").click();
  await expect(page.locator("#view-providers h1")).toHaveText("Model Providers");
  await expect(page.locator(".pv-current .pv-cur-label")).toHaveText("Active");
  await expect(page.locator(".pv-current .pv-status")).toHaveText(/external tool config/);
});

// ---------------- updates 面板 ----------------

const UPDATES = {
  platform: "macOS",
  npm_available: true,
  tools: [
    { key: "claude", display: "Claude Code", npm_package: "@anthropic-ai/claude-code",
      installed: true, current: "2.1.292", latest: "2.1.292", state: "latest",
      install: [
        "bash -c 'tmp=$(mktemp) && curl -fsSL https://claude.ai/install.sh -o $tmp && bash $tmp; status=$?; rm -f $tmp; exit $status'",
        "npm install -g @anthropic-ai/claude-code@latest",
      ] },
    { key: "codex", display: "Codex", npm_package: "@openai/codex",
      installed: true, current: "0.158.0", latest: "0.160.1", state: "upgradeable",
      install: ["npm install -g @openai/codex@latest"] },
    { key: "pi", display: "Pi", npm_package: "@earendil-works/pi-coding-agent",
      installed: true, current: "0.83.0", latest: "0.73.1", state: "ahead",
      install: ["npm install -g @earendil-works/pi-coding-agent@latest"] },
    { key: "kimi", display: "Kimi Code", npm_package: "@moonshot-ai/kimi-code",
      installed: false, current: null, latest: "2.1.1", state: "missing",
      install: ["npm install -g @moonshot-ai/kimi-code@latest"] },
  ],
};

test("updates：版本卡片渲染与整卡红绿灰状态", async ({ page }) => {
  await openApp(page, bootData({ halter_update_status: UPDATES }));

  await page.locator('.nav-item[data-view="updates"]').click();

  // 已最新：绿卡（绿描边着色类）+ 状态徽标（文字 + 颜色双通道）；无任何动作按钮
  const claudeCard = page.locator(".up-card", { hasText: "Claude Code" });
  await expect(claudeCard).toHaveClass(/state-latest/);
  await expect(claudeCard.locator(".up-state")).toHaveClass(/ok/);
  await expect(claudeCard.locator(".up-state")).toHaveText("已就绪");
  await expect(claudeCard.locator(".up-state")).toHaveAttribute("title", "已就绪");
  // 卡内按钮均为安装命令的图标复制按钮：官方脚本与 npm 各一行、逐行复制（title/aria 无障碍）
  await expect(claudeCard.locator("button")).toHaveCount(2);
  await expect(claudeCard.locator(".up-copy")).toHaveCount(2);
  await expect(claudeCard.locator(".up-copy").first()).toHaveAttribute("title", "复制");
  await expect(claudeCard.locator(".up-copy").first()).toHaveAttribute("aria-label", "复制");
  await expect(claudeCard.locator(".up-copy svg")).toHaveCount(2);
  const claudeCmds = claudeCard.locator(".up-install-cmd");
  await expect(claudeCmds).toHaveCount(2);
  // 单行省略：DOM 文本仍是完整命令（CSS 截断展示），悬停 title 提供全文
  await expect(claudeCmds.first()).toContainText("https://claude.ai/install.sh");
  await expect(claudeCmds.first()).toHaveAttribute("title", UPDATES.tools[0].install[0]);
  await expect(claudeCmds.first()).not.toContainText("npm install");
  await expect(claudeCmds.nth(1)).toHaveText("npm install -g @anthropic-ai/claude-code@latest");
  await expect(claudeCard.locator(".up-row-value")).toHaveText(["macOS", "2.1.292", "2.1.292"]);

  // 待更新：红卡 + 状态徽标；未安装：灰卡（最新版可查、当前为 —）；均无按钮（升级走 CLI）
  const codexCard = page.locator(".up-card", { hasText: "Codex" });
  await expect(codexCard).toHaveClass(/state-upgradeable/);
  await expect(codexCard.locator(".up-state")).toHaveClass(/upgradeable/);
  await expect(codexCard.locator(".up-state")).toHaveText("待更新");
  // 超前：current > latest 同为红卡（弃更/源码安装不再假绿），提示更新即降级
  // （hasText 大小写不敏感，"Pi" 会误中 @anthropic-ai，故用包名片段定位）
  const piCard = page.locator(".up-card", { hasText: "@earendil-works" });
  await expect(piCard).toHaveClass(/state-ahead/);
  await expect(piCard.locator(".up-state")).toHaveClass(/ahead/);
  await expect(piCard.locator(".up-state")).toHaveText("高于最新");
  await expect(piCard.locator(".up-state"))
    .toHaveAttribute("title", "高于 registry 最新（疑似源码/预发布安装），此刻更新等于降级");
  const kimiCard = page.locator(".up-card", { hasText: "Kimi Code" });
  await expect(kimiCard).toHaveClass(/state-missing/);
  await expect(kimiCard.locator(".up-state")).toHaveText("未安装");
  await expect(kimiCard.locator(".up-row-value")).toHaveText(["macOS", "—", "2.1.1"]);
  // 复制按钮 = 命令行数（claude 2 条 + codex/kimi/pi 各 1 条），没有其他动作按钮（升级走 CLI）
  await expect(page.locator(".up-card button")).toHaveCount(5);
  await expect(codexCard.locator(".up-install-cmd")).toHaveText("npm install -g @openai/codex@latest");

  // 语言切换：已渲染的更新面板动态文案重渲染（状态徽标与悬停提示变英文）
  await openSettings(page);
  await page.locator("#lang-en").click();
  const claudeBadgeEn = page.locator(".up-card", { hasText: "Claude Code" }).locator(".up-state");
  await expect(claudeBadgeEn).toHaveText("Up to date");
  await expect(claudeBadgeEn).toHaveAttribute("title", "Up to date");
});

test("updates：npm 不可用时显示告警且不出现动作按钮", async ({ page }) => {
  const broken = {
    ...UPDATES,
    npm_available: false,
    tools: UPDATES.tools.map((tool) => ({ ...tool, latest: null, state: "unknown" })),
  };
  await openApp(page, bootData({ halter_update_status: broken }));
  await setHandler(page, "halter_update_status", ({ base }) => () => base, { base: broken });

  await page.locator('.nav-item[data-view="updates"]').click();
  await expect(page.locator("#updates-npm-warning")).toBeVisible();
  await expect(page.locator("#updates-npm-warning")).toContainText("npm");
  // npm 缺失：latest 全空 → 全部卡片徽标为「未知」；安装命令照常展示（复制按钮数 = 命令行数）
  await expect(page.locator(".up-card .up-state").first()).toHaveText("未知");
  await expect(page.locator(".up-card button")).toHaveCount(5);
});
