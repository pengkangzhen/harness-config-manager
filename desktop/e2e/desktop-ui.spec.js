// 桌面 UI 静态 e2e：mock Tauri IPC,驱动 handoff 文档 M7 要求的状态机
// (approval / deny / rollback / cancel / restore session / model save /
// plan evidence 展开 / audit deep link / runtime version mismatch)。
const { test, expect } = require("@playwright/test");
const {
  openApp, setHandler, callsWithArgs, emitAhp,
} = require("./tauri-mock");

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

test("模型视图渲染健康面板并完成保存流程", async ({ page }) => {
  await openApp(page, bootData({
    halter_models: {
      models: { halter: "local/qwen" },
      catalog: {},
      providers: { local: { base_url: "http://127.0.0.1:11434/v1", api_key_env: "LOCAL_KEY" } },
      health: {
        ok: false, model: "local/qwen", apiKeyEnv: "LOCAL_KEY",
        checks: [
          { id: "config", label: "默认模型配置", status: "ok", detail: "local/qwen" },
          { id: "api-key", label: "API key 环境变量", status: "error", detail: "环境变量 LOCAL_KEY 未设置" },
        ],
      },
    },
  }));
  await page.click('.nav-item[data-view="models"]');
  await expect(page.locator("#model-health .model-health-item.error")).toContainText("LOCAL_KEY");
  await expect(page.locator("#native-model-input")).toHaveValue("local/qwen");

  await setHandler(page, "halter_model_configure", { model: "local/qwen2", apiKeyStored: false });
  await setHandler(page, "halter_ahp_stop", {});
  await setHandler(page, "halter_models", {
    models: { halter: "local/qwen2" },
    health: { ok: true, checks: [
      { id: "config", label: "默认模型配置", status: "ok", detail: "local/qwen2" },
    ]},
  });
  await page.fill("#native-model-input", "local/qwen2");
  await page.click("#btn-save-native-model");
  await expect(page.locator("#model-status")).toHaveClass(/ok/);

  const invoked = await callsWithArgs(page);
  const configure = invoked.find(([command]) => command === "halter_model_configure");
  expect(configure[1]).toMatchObject({ model: "local/qwen2" });
  expect(invoked.map(([command]) => command)).toContain("halter_ahp_stop");
});

test("audit 列表加载与 plan evidence deep link 高亮定位", async ({ page }) => {
  await openApp(page, bootData({
    halter_audit_list: {
      audits: [{
        session_id: "ahp-x", title: "T", provider: "halter",
        workspace: "file:///tmp/p", event_count: 2,
        last_event_at: "2026-09-18T00:00:00Z", kinds: { "tool.call": 2 },
      }],
    },
  }));
  await setHandler(page, "halter_audit_show", () => ({
    summary: { session_id: "ahp-x", provider: "halter", workspace: "file:///tmp/p" },
    total_events: 2, returned_events: 2,
    events: [
      { ts: "2026-09-18T00:00:01Z", kind: "tool.call", id: "call-9", name: "read_file" },
      { ts: "2026-09-18T00:00:02Z", kind: "tool.result", id: "call-10", name: "apply_patch",
        result: { transactionId: "patch-z" } },
    ],
  }));
  await page.click('.nav-item[data-view="audit"]');
  await expect(page.locator(".audit-item")).toHaveCount(1);
  await page.click(".audit-item");
  await expect(page.locator(".audit-event")).toHaveCount(2);

  // deep link:由 plan 证据跳转,清空过滤并高亮目标事件
  await page.evaluate(() => jumpToAuditEvidence("ahp-x", "patch-z"));
  await expect(page.locator(".audit-event.jump")).toHaveCount(1);
  await expect(page.locator(".audit-event.jump")).toContainText("apply_patch");
});

async function setupDispatch(page, { existingSession = false } = {}) {
  await openApp(page, bootData({
    halter_models: { models: {}, catalog: {} },
    halter_sessions_projects: { projects: [] },
  }));
  await setHandler(page, "halter_ahp_connect", { init: { snapshots: [] } });
  await setHandler(page, "halter_ahp_rpc",
    ({ existing }) => ({ method, params }) => {
      if (method === "listSessions") {
        return { items: existing
          ? [{ resource: "ahp-session:/r", provider: "halter", workingDirectories: ["."] }]
          : [] };
      }
      if (method === "subscribe") {
        const channel = (params && params.channel) || "";
        if (existing && channel.startsWith("ahp-session:")) {
          return { snapshot: { state: { defaultChat: "ahp-chat:/restored" } } };
        }
        if (existing && channel.startsWith("ahp-chat:")) {
          return { snapshot: { state: {
            currentPlan: { steps: [{ id: "step-r", title: "恢复的步骤", status: "in_progress" }], note: "" },
            planEvidence: {},
            approvalHistory: [{ id: "appr-old", tool: "apply_patch", status: "approved", decidedAt: "2026-09-18T00:00:00Z" }],
            auditSessionId: "ahp-x",
          } } };
        }
        return { snapshot: { state: { auditSessionId: "ahp-new" } } };
      }
      return {};
    }, { existing: existingSession });
  await setHandler(page, "halter_ahp_notify", {});
  await page.click('.nav-item[data-view="dispatch"]');
  await page.fill("#dispatch-input", "@halter 修一下 README");
  await page.click("#btn-dispatch-send");
  await expect(page.locator(".dispatch-card")).toHaveCount(1);
  return page.evaluate(() => [...(state.ahp ? state.ahp.cards.keys() : [])][0]);
}

test("dispatch 状态机:plan 证据、审批批准、回滚与取消", async ({ page }) => {
  const channel = await setupDispatch(page);

  await emitAhp(page, channel, {
    type: "halter/planChanged",
    plan: { steps: [{ id: "step-a", title: "Fix README", status: "in_progress" }], note: "" },
  });
  await expect(page.locator(".dispatch-plan-title")).toHaveText("Fix README");

  await emitAhp(page, channel, {
    type: "halter/planEvidence",
    evidence: {
      "step-a": {
        toolCallIds: [{ id: "call-1", name: "read_file", ts: "2026-09-18T00:00:00Z" }],
        approvalIds: [], transactionIds: [], auditTimestamps: [], updatedAt: "x",
      },
    },
  });
  await expect(page.locator(".dispatch-plan-evidence-toggle")).toBeVisible();
  await page.click(".dispatch-plan-evidence-toggle");
  await expect(page.locator(".dispatch-plan-evidence-item")).toHaveCount(1);
  // 证据可跳转审计
  await page.click(".dispatch-plan-evidence-link");
  await expect(page.locator(".nav-item[data-view=\"audit\"]")).toHaveClass(/active/);

  // 回到 dispatch,继续状态机
  await page.click('.nav-item[data-view="dispatch"]');
  await emitAhp(page, channel, { type: "halter/toolCall", part: { id: "call-2", name: "apply_patch", arguments: {} } });
  await emitAhp(page, channel, {
    type: "halter/toolResult",
    part: { id: "call-2", name: "apply_patch", ok: true, result: { transactionId: "patch-1", applied: true } },
  });
  const rollback = page.locator(".dispatch-event-rollback");
  await expect(rollback).toBeVisible();
  await rollback.click();
  let notified = await page.evaluate(() => window.__calls
    .filter(([command]) => command === "halter_ahp_notify")
    .map(([, args]) => args.params.action.type));
  expect(notified).toContain("halter/rollbackRequest");

  // 审批:批准
  await emitAhp(page, channel, {
    type: "halter/approvalRequest",
    approval: { id: "appr-1", tool: "apply_patch", summary: "second patch", input: { patch: "--- a\n" } },
  });
  await expect(page.locator(".dispatch-approval-title")).toContainText("second patch");
  await page.locator(".dispatch-approval-actions button").first().click();
  await page.waitForFunction(() => window.__calls.some(([command, args]) =>
    command === "halter_ahp_notify"
    && args.params.action.type === "halter/approvalResponse"
    && args.params.action.approved === true));
  // host 确认后面板转 approved、审批历史更新
  await emitAhp(page, channel, {
    type: "halter/approvalResult", approvalId: "appr-1", approved: true,
  });
  await expect(page.locator(".dispatch-approval.approved")).toHaveCount(1);
  await expect(page.locator(".dispatch-approval-history-item.approved")).toHaveCount(1);

  // 取消
  await page.click(".dispatch-cancel-btn");
  await page.waitForFunction(() => window.__calls.some(([command, args]) =>
    command === "halter_ahp_notify" && args.params.action.type === "chat/turnCancelled"));
});

test("dispatch 状态机:拒绝审批", async ({ page }) => {
  const channel = await setupDispatch(page);
  await emitAhp(page, channel, {
    type: "halter/approvalRequest",
    approval: { id: "appr-deny", tool: "apply_patch", summary: "bad patch", input: { patch: "x" } },
  });
  await page.locator(".dispatch-approval-actions button.danger").click();
  await page.waitForFunction(() => window.__calls.some(([command, args]) =>
    command === "halter_ahp_notify"
    && args.params.action.type === "halter/approvalResponse"
    && args.params.action.approved === false));
  await emitAhp(page, channel, {
    type: "halter/approvalResult", approvalId: "appr-deny", approved: false,
  });
  await expect(page.locator(".dispatch-approval.denied")).toHaveCount(1);
});

test("恢复既有 native 会话:plan 与审批历史回填卡片", async ({ page }) => {
  await setupDispatch(page, { existingSession: true });
  await expect(page.locator(".dispatch-plan-title")).toHaveText("恢复的步骤");
  await expect(page.locator(".dispatch-approval-history-item.approved")).toHaveCount(1);
});
