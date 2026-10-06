// 真通道 e2e：ui-server.mjs 桥接真实 halter 二进制 + 隔离 HOME。
// 与 desktop-ui.spec.js（mock Tauri IPC）互补：这里覆盖 mock 层永远测不到的
// 真实子进程调用与文件系统副作用（adopt 落库、链接生成、providers 写 settings.json）。
// 二进制默认用仓库 venv 的 halter（与 PyInstaller sidecar 同源代码）；
// 探索式测试可设 HALTER_BINARY 指向真 sidecar。端口默认 4789，可用
// E2E_REAL_PORT 覆盖（夜间多 agent 并行时每个场景占一个端口）。
const { test, expect } = require("@playwright/test");
const {
  mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync,
  lstatSync,
} = require("node:fs");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..");
const HALTER = process.env.HALTER_BINARY || path.join(REPO, ".venv", "bin", "halter");
const UI_SERVER = path.join(REPO, "desktop", "devbin", "ui-server.mjs");
const PORT = Number(process.env.E2E_REAL_PORT || 4789);
const BASE = `http://127.0.0.1:${PORT}`;

let fakeHome;
let server;

test.beforeAll(async () => {
  fakeHome = mkdtempSync(path.join(tmpdir(), "halter-real-home-"));
  const seed = (rel, body) => {
    const p = path.join(fakeHome, rel);
    mkdirSync(path.dirname(p), { recursive: true });
    writeFileSync(p, body);
  };
  seed(".claude/settings.json", "{}\n");
  seed(".claude/skills/seed-skill/SKILL.md",
    "---\nname: seed-skill\ndescription: Seeded in claude for adopt\n---\n# seed\n");
  seed(".agents/skills/demo-skill/SKILL.md",
    "---\nname: demo-skill\ndescription: Library skill as single source\n---\n# library copy\n");
  // claude 侧放同名旧副本：sync 收集进清单后应把它替换为指向库的链接
  seed(".claude/skills/demo-skill/SKILL.md",
    "---\nname: demo-skill\ndescription: Stale claude-side copy\n---\n# stale claude copy\n");
  seed(".codex/config.toml", "");

  // provider 经真实 CLI 种入，token 走 stdin，与产品路径完全一致
  const runCli = (args, input) => spawnSync(HALTER, args, {
    env: { ...process.env, HOME: fakeHome }, input, encoding: "utf8",
  });
  const ver = runCli(["version", "--json"]);
  if (ver.status !== 0) {
    throw new Error(`halter version failed (${ver.status}): ${ver.stderr || ver.stdout}`);
  }
  const add = runCli(
    ["providers", "add", "demo", "--tool", "claude",
      "--base-url", "https://example.com/api/anthropic", "--token-stdin"],
    "sk-e2e-token\n",
  );
  if (add.status !== 0) {
    throw new Error(`providers add failed (${add.status}): ${add.stderr || add.stdout}`);
  }

  server = spawn("node", [UI_SERVER, String(PORT)], {
    env: { ...process.env, HOME: fakeHome, HALTER_BINARY: HALTER },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const deadline = Date.now() + 15_000;
  let ready = false;
  while (Date.now() < deadline && !ready) {
    try {
      const r = await fetch(`${BASE}/`);
      ready = r.ok;
    } catch { /* server not listening yet */ }
    if (!ready) await new Promise((r) => setTimeout(r, 200));
  }
  if (!ready) throw new Error(`ui-server not ready on ${BASE}`);
});

test.afterAll(() => {
  server?.kill();
  if (fakeHome) rmSync(fakeHome, { recursive: true, force: true });
});

test("真通道 boot：真实版本与扫描渲染矩阵", async ({ page }) => {
  await page.goto(BASE);
  await expect(page.locator("#halter-version")).toHaveText(/^halter \d+\.\d+/);
  await expect(page.locator("#halter-version")).not.toHaveClass(/mismatch/);
  // 工具检测依赖宿主 PATH，不锁定数量，只断言有种配置目录证据的 claude
  await expect(page.locator(".tool-card .tool-name").first()).toContainText(/./);

  await page.locator('.nav-item[data-view="matrix"]').click();
  // 真实扫描：库里与 claude 里的两个 skill 都出现在矩阵行
  await expect(page.locator(".mx-name", { hasText: "demo-skill" })).toBeVisible();
  await expect(page.locator(".mx-name", { hasText: "seed-skill" })).toBeVisible();
});

test("真通道全量同步：adopt 落库 + 链接真实写盘", async ({ page }) => {
  await page.goto(BASE);
  await page.locator('.nav-item[data-view="matrix"]').click();

  await page.locator("#btn-matrix-preview").click();
  await expect(page.locator("#matrix-sync-output")).toContainText(/计划|Plan/);

  await page.locator("#btn-matrix-apply").click();
  await expect(page.locator("#btn-matrix-apply")).toContainText(/再点一次|again/i);
  await page.locator("#btn-matrix-apply").click();
  await expect(page.locator("#matrix-sync-output")).toContainText("exit code: 0");

  // 真实文件系统副作用与冲突保护语义：
  // - 库外 skill 被 adopt 进库，claude 侧替换为指向库的符号链接
  // - 库/工具内容冲突的 skill 默认跳过，claude 侧原目录不被覆盖
  expect(existsSync(path.join(fakeHome, ".agents/skills/seed-skill/SKILL.md")))
    .toBeTruthy();
  const seedDir = path.join(fakeHome, ".claude/skills/seed-skill");
  expect(lstatSync(seedDir).isSymbolicLink()).toBeTruthy();
  const demoDir = path.join(fakeHome, ".claude/skills/demo-skill");
  expect(lstatSync(demoDir).isSymbolicLink()).toBeFalsy();
  expect(readFileSync(path.join(demoDir, "SKILL.md"), "utf8"))
    .toContain("# stale claude copy");
  await expect(page.locator("#matrix-sync-output")).toContainText(/conflict|跳过/);
});

test("真通道 providers：切换真实写入 settings.json", async ({ page }) => {
  await page.goto(BASE);
  await page.locator('.nav-item[data-view="providers"]').click();

  const row = page.locator(".pv-row", { hasText: "demo" });
  await expect(row).toBeVisible();
  await row.locator("button", { hasText: "切换" }).click();
  await expect(page.locator("#matrix-toast")).toContainText("demo");

  const settings = JSON.parse(
    readFileSync(path.join(fakeHome, ".claude/settings.json"), "utf8"));
  expect(settings.env.ANTHROPIC_BASE_URL).toBe("https://example.com/api/anthropic");
  expect(settings.env.ANTHROPIC_AUTH_TOKEN).toBe("sk-e2e-token");
});
