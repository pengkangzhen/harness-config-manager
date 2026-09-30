// 浏览器版桌面 UI 开发服务器：静态托管 desktop/ui/ 并把
// window.__TAURI__.core.invoke 桥接到真实 halter CLI。
// 命令→参数映射 1:1 复刻 src-tauri/src/main.rs，行为不一致时以 main.rs 为准。
// 用法：node desktop/devbin/ui-server.mjs [port]
import http from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const UI_DIR = path.join(__dirname, "..", "ui");
const PORT = Number(process.argv[2] || process.env.PORT || 4761);

// 与 main.rs resolve_halter() 同序：HALTER_BINARY → 仓库 venv → PATH。
function resolveHalter() {
  const fromEnv = process.env.HALTER_BINARY?.trim();
  if (fromEnv) return fromEnv;
  const venv = path.join(REPO, ".venv", "bin", "halter");
  return venv; // spawn 的 ENOENT 语义等价于 PATH 回退失败，错误信息足够定位
}

// main.rs 的 SidecarOutput：原样返回 stdout/stderr，不做 JSON 解析。
function runHalter(args) {
  return new Promise((resolve) => {
    const child = spawn(resolveHalter(), args, {
      env: { ...process.env, HALTER_UI: "1", NO_COLOR: "1" },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (c) => (stdout += c));
    child.stderr.on("data", (c) => (stderr += c));
    child.on("error", (err) =>
      resolve({ ok: false, code: -1, stdout, stderr: String(err) }),
    );
    child.on("close", (code) =>
      resolve({ ok: code === 0, code: code ?? -1, stdout, stderr }),
    );
  });
}

function failDetail(out) {
  return out.stderr.trim() || out.stdout.trim();
}

async function runJson(args) {
  const out = await runHalter(args);
  if (!out.ok) {
    throw new Error(`halter exited with code ${out.code}: ${failDetail(out)}`);
  }
  try {
    return JSON.parse(out.stdout.trim());
  } catch (err) {
    throw new Error(
      `failed to parse halter JSON output: ${err}\n--- stdout ---\n${out.stdout}`,
    );
  }
}

async function runText(args) {
  const out = await runHalter(args);
  if (!out.ok) {
    throw new Error(`halter exited with code ${out.code}: ${failDetail(out)}`);
  }
  return out.stdout;
}

// halter_version 附加桌面端版本号；与 main.rs 的 env!("CARGO_PKG_VERSION")
// 同源，读 src-tauri/Cargo.toml 的 [package].version。
async function desktopVersion() {
  const toml = await readFile(path.join(REPO, "desktop", "src-tauri", "Cargo.toml"), "utf8");
  return toml.match(/^version\s*=\s*"([^"]+)"/m)?.[1] ?? "0.0.0";
}

// 键名与 app.js 实际传参一致（Tauri 会把 snake_case 转 camelCase）。
// 用系统默认程序打开文件（Memory 面板的「打开」按钮）。
// 安全约束：只允许打开家目录内、真实存在的文件。打开器逐个回退：
// linux 先试 xdg-open / wslview（GUI 环境）；WSL 无显示会话时用
// `wslpath -w` 转成 \\wsl.localhost\… 路径，优先 PowerShell Invoke-Item
// （退出码决定成败），explorer.exe 兜底（其成功时惯常返回 1）。
const OPENERS = {
  darwin: [["open"]],
  win32: [["cmd", "/c", "start", ""]],
  linux: [["xdg-open"], ["wslview"]],
};

function openWith(entries) {
  return new Promise((resolve, reject) => {
    const tryNext = (i) => {
      if (i >= entries.length) {
        reject(new Error("no opener succeeded"));
        return;
      }
      const { argv, okCodes } = entries[i];
      const child = spawn(argv[0], argv.slice(1), { stdio: "ignore" });
      child.on("error", () => tryNext(i + 1)); // ENOENT：没装，试下一个
      child.on("close", (code) => {
        if (okCodes.includes(code)) resolve();
        else tryNext(i + 1);
      });
    };
    tryNext(0);
  });
}

function winPathOf(p) {
  return new Promise((resolve) => {
    const child = spawn("wslpath", ["-w", p]);
    let out = "";
    child.stdout.on("data", (c) => (out += c));
    child.on("error", () => resolve(null));
    child.on("close", (code) => resolve(code === 0 ? out.trim() : null));
  });
}

async function openInSystem(rawPath) {
  const home = os.homedir();
  const resolved = path.resolve(String(rawPath));
  if (resolved !== home && !resolved.startsWith(home + path.sep)) {
    throw new Error(`refusing to open outside home: ${resolved}`);
  }
  if (!existsSync(resolved)) throw new Error(`file not found: ${resolved}`);
  const entries = (OPENERS[process.platform] ?? OPENERS.linux)
    .map((argv) => ({ argv: [...argv, resolved], okCodes: [0] }));
  if (process.platform === "linux") {
    const winPath = await winPathOf(resolved);
    if (winPath) {
      const ps = existsSync("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        ? "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        : "powershell.exe";
      entries.push({
        argv: [ps, "-NoProfile", "-Command", `Invoke-Item -LiteralPath '${winPath}'`],
        okCodes: [0],
      });
      const exe = existsSync("/mnt/c/Windows/explorer.exe")
        ? "/mnt/c/Windows/explorer.exe"
        : "explorer.exe";
      entries.push({ argv: [exe, winPath], okCodes: [0, 1] });
    }
  }
  await openWith(entries);
  return { opened: resolved };
}

const COMMANDS = {
  halter_version: async () => {
    const value = await runJson(["version", "--json"]);
    value.desktop = await desktopVersion();
    return value;
  },
  halter_scan: () => runJson(["scan", "--json"]),
  halter_memory_show: () => runJson(["memory", "show", "--json"]),
  open_path: ({ path: target }) => openInSystem(target),
  halter_sessions_list: ({ project, limit, allProjects }) =>
    runJson([
      "sessions",
      "list",
      "--limit",
      String(limit),
      "--json",
      ...(allProjects
        ? ["--all-projects"]
        : ["--project", String(project)]),
    ]),
  halter_sessions_projects: ({ project }) =>
    runJson(["sessions", "projects", "--project", String(project), "--json"]),
  halter_sessions_show: ({ ref, project, tail }) =>
    runJson([
      "sessions",
      "show",
      "--project",
      String(project),
      "--transcript",
      "--tail",
      String(tail),
      "--json",
      "--",
      String(ref),
    ]),
  halter_sessions_search: ({ query, project, limit, allProjects }) =>
    runJson([
      "sessions",
      "search",
      "--limit",
      String(limit),
      "--json",
      ...(allProjects
        ? ["--all-projects"]
        : ["--project", String(project)]),
      "--",
      String(query),
    ]),
  halter_sessions_context: ({ ref, project, tail }) =>
    runText([
      "sessions",
      "context",
      "--project",
      String(project),
      "--tail",
      String(tail),
      "--",
      String(ref),
    ]),
  // 与 main.rs 一致：sync 返回 SidecarOutput 原始结构，不解析。
  // tool / items 把同步收窄到矩阵的一个单元格（层由 layers 收窄）。
  halter_sync: async ({ apply, layers, tool, items }) => {
    const allowed = ["skills", "mcp", "plugins", "hooks", "agents", "memory", "sessions"];
    const args = ["sync"];
    for (const layer of allowed) {
      if (!layers.includes(layer)) args.push(`--no-${layer}`);
    }
    if (tool) args.push("--tool", tool);
    for (const item of items || []) args.push("--item", item);
    if (apply) args.push("--apply");
    return runHalter(args);
  },
};

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
};

// 注入顺序必须在 app.js 之前，否则第 4 行的 window.__TAURI__ 取值为 undefined。
const BRIDGE_SCRIPT = `
  <script>
    window.__TAURI__ = {
      core: {
        invoke: (command, args) =>
          fetch("/invoke", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ command, args: args || {} }),
          })
            .then((r) => r.json())
            .then((d) => (d.ok ? d.value : Promise.reject(new Error(d.error)))),
      },
      event: { listen: async () => () => {} },
    };
  </script>`;

async function serveStatic(req, res) {
  const urlPath = decodeURIComponent(new URL(req.url, "http://x").pathname);
  const rel = urlPath === "/" ? "index.html" : urlPath.replace(/^\/+/, "");
  const file = path.join(UI_DIR, rel);
  if (!file.startsWith(UI_DIR)) {
    res.writeHead(403).end();
    return;
  }
  try {
    let body = await readFile(file);
    if (rel === "index.html") {
      const html = body.toString("utf8").replace("</head>", `${BRIDGE_SCRIPT}</head>`);
      body = html;
    }
    res.writeHead(200, {
      "content-type": MIME[path.extname(file)] || "application/octet-stream",
    });
    res.end(body);
  } catch {
    res.writeHead(404).end("not found");
  }
}

const server = http.createServer(async (req, res) => {
  if (req.method === "POST" && req.url === "/invoke") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      try {
        const { command, args } = JSON.parse(body);
        const handler = COMMANDS[command];
        if (!handler) throw new Error(`unknown command: ${command}`);
        const value = await handler(args);
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: true, value }));
      } catch (err) {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: String(err?.message || err) }));
      }
    });
    return;
  }
  await serveStatic(req, res);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`halter UI (browser bridge) -> http://127.0.0.1:${PORT}`);
  console.log(`halter binary: ${resolveHalter()}`);
});
