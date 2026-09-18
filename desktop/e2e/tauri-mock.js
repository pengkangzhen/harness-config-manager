// Tauri IPC mock：在页面加载前注入 window.__TAURI__。
// - openApp(page, dataHandlers)：boot 阶段（halter_version / halter_scan）的
//   命令必须在 initScript 里以纯数据形式就位
// - setHandler(page, command, fn)：页面就绪后注入动态 handler（可读 args）
// - window.__calls 记录 [command, args] 序列
// - emitAhp(page, channel, action)：注入一条 server->client 的 AHP action
const { pathToFileURL } = require("node:url");
const path = require("node:path");

const UI_URL = pathToFileURL(
  path.join(__dirname, "..", "ui", "index.html"),
).href;

async function openApp(page, dataHandlers = {}) {
  await page.addInitScript((initial) => {
    window.__mockHandlers = initial;
    window.__calls = [];
    window.__listeners = {};
    window.__mockInvoke = (command, args) => {
      window.__calls.push([command, args]);
      const handler = window.__mockHandlers[command];
      if (handler === undefined) {
        return Promise.reject(new Error(`unmocked command: ${command}`));
      }
      try {
        return Promise.resolve(
          typeof handler === "function" ? handler(args) : handler,
        );
      } catch (err) {
        return Promise.reject(err);
      }
    };
    window.__TAURI__ = {
      core: { invoke: (command, args) => window.__mockInvoke(command, args) },
      event: {
        listen: async (name, callback) => {
          (window.__listeners[name] = window.__listeners[name] || []).push(callback);
          return () => {};
        },
      },
    };
  }, dataHandlers);
  await page.goto(UI_URL);
  return page;
}

async function setHandler(page, command, handler, bindings = {}) {
  if (typeof handler !== "function") {
    // 纯数据 fixture 可直接结构化克隆
    await page.evaluate(({ command, handler }) => {
      window.__mockHandlers[command] = handler;
    }, { command, handler });
    return;
  }
  // 函数无法克隆：传源码字符串，在页面上下文重建；闭包变量必须通过
  // bindings（纯数据）显式传入，factory 签名为 (bindings) => handler。
  const source = handler.toString();
  await page.evaluate(({ command, source, bindings }) => {
    const factory = eval(`(${source})`);
    window.__mockHandlers[command] = factory(bindings);
  }, { command, source, bindings });
}

async function calls(page) {
  return page.evaluate(() => window.__calls.map(([c]) => c));
}

async function callsWithArgs(page) {
  return page.evaluate(() => window.__calls);
}

// 注入一条 server->client 的 AHP action 信封（同 Rust 桥的 ahp-message 载荷）
async function emitAhp(page, channel, action) {
  await page.evaluate(({ channel, action }) => {
    const payload = JSON.stringify({
      jsonrpc: "2.0",
      method: "action",
      params: { channel, action },
    });
    for (const callback of window.__listeners["ahp-message"] || []) {
      callback({ payload });
    }
  }, { channel, action });
}

module.exports = { openApp, setHandler, calls, callsWithArgs, emitAhp, UI_URL };
