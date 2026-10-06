// 一键刷新全部 README 截图：自动在空闲端口起 ui-server，
// 串行跑本目录下全部 shot-*.mjs（按文件名字母序），结束后关闭服务。
// 用法：node desktop/devbin/shot-all.mjs
//      SHOT_ONLY=matrix,sessions node desktop/devbin/shot-all.mjs   # 只跑子集
import { spawn } from "node:child_process";
import { readdirSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// OS 分配空闲端口：绕开残留的旧 ui-server 进程（4761 曾被旧代码占用过）
const freePort = () =>
  new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });

const waitForServer = async (url, timeoutMs) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).ok) return;
    } catch { /* 尚未监听 */ }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`ui-server ${timeoutMs}ms 内未就绪：${url}`);
};

const runShot = (script, port) =>
  new Promise((resolve) => {
    const child = spawn("node", [script], {
      stdio: "inherit",
      env: { ...process.env, SHOT_PORT: String(port) },
    });
    child.on("close", (code) => resolve(code ?? -1));
  });

const only = new Set(
  (process.env.SHOT_ONLY || "").split(",").map((s) => s.trim()).filter(Boolean),
);
const scripts = readdirSync(__dirname)
  .filter((f) => /^shot-.*\.mjs$/.test(f) && f !== "shot-all.mjs")  // 排除自己，否则无限递归
  .filter((f) => !only.size || only.has(f.replace(/^shot-|\.mjs$/g, "")))
  .sort()
  .map((f) => path.join(__dirname, f));
if (!scripts.length) {
  console.error(`shot-all: 没有匹配的 shot-*.mjs（SHOT_ONLY=${[...only].join(",") || "未设置"}）`);
  process.exit(1);
}

const port = await freePort();
const server = spawn("node", [path.join(__dirname, "ui-server.mjs"), String(port)], {
  stdio: "inherit",
});
let failed = 0;
try {
  await waitForServer(`http://127.0.0.1:${port}/`, 15000);
  for (const s of scripts) {
    console.log(`\n==== ${path.basename(s)} ====`);
    if ((await runShot(s, port)) !== 0) {
      failed++;
      console.error(`shot-all: ${path.basename(s)} 失败`);
    }
  }
} finally {
  server.kill();
}
process.exit(failed ? 1 : 0);
