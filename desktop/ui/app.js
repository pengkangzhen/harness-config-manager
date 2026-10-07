// Halter desktop frontend — no build step, talks to Rust commands via Tauri IPC.
"use strict";

const invoke = (...a) => window.__TAURI__.core.invoke(...a);
const $ = (id) => document.getElementById(id);

/* ---------------- helpers ---------------- */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function fmtDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtTime(iso) {
  if (!iso) return "--:--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--";
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function relTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffMs = Date.now() - d.getTime();
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return t("rt.justNow");
  if (min < 60) return t("rt.minAgo", { n: min });
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return t("rt.today", { time: hm });
  const yest = new Date(now);
  yest.setDate(yest.getDate() - 1);
  if (d.toDateString() === yest.toDateString()) return t("rt.yesterday", { time: hm });
  if (diffMs < 7 * 86400000) return t("rt.daysAgo", { n: Math.floor(diffMs / 86400000) });
  return t("rt.monthDay", { m: d.getMonth() + 1, d: d.getDate() });
}

function errorDetail(err) {
  if (typeof err === "string") return err;
  return (err && (err.message || (err.toString && err.toString()))) ||
    JSON.stringify(err, null, 2);
}

function showError(box, err) {
  box.textContent = errorDetail(err);
  box.classList.remove("hidden");
}

const TOOL_COLORS = {
  claude: "#d97757",
  codex: "#10a37f",
  zcode: "#a78bfa",
  opencode: "#4cc2ff",
  halter: "#f59e0b",
  cursor: "#5ba8ff",
  gemini: "#7bd88f",
  vscode: "#5aa0e8",
  "copilot-cli": "#8f9bb3",
  continue: "#c792ea",
};
const toolColor = (tool) => TOOL_COLORS[tool] || "#8b96b8";
const basename = (p) => (p || "").split("/").filter(Boolean).pop() || p || "-";

function shortenPath(p) {
  if (!p) return "";
  if (p.startsWith("/private/var/folders/")) return "…/var/folders/…/T";
  const m = p.match(/^\/Users\/[^/]+(.*)$/);
  return m ? "~" + m[1] : p;
}

/* ---------------- dd：自定义下拉（原生 select 的样式替代） ----------------
   原生 select 隐藏保留为状态源（value / 表单读值 / change 事件全部不变），
   弹层样式跨平台一致。选项动态增删由 MutationObserver 自动同步；
   程序设值（sel.value = x）不触发事件，需补调 syncDD(sel) 刷新按钮显示；
   底部空间不足时弹层自动向上翻转。 */

const ddSync = new Map();

function syncDD(sel) {
  const fn = sel && ddSync.get(sel.id);
  if (fn) fn();
}

function enhanceSelect(sel) {
  if (!sel || sel.dataset.dd) return;
  sel.dataset.dd = "1";
  sel.classList.add("dd-native");
  const wrap = el("div", "dd-wrap");
  wrap.dataset.ddFor = sel.id;
  sel.insertAdjacentElement("afterend", wrap);
  const btn = el("button", "dd-btn");
  btn.type = "button";
  btn.setAttribute("aria-haspopup", "listbox");
  const label = el("span", "dd-label");
  btn.append(label, el("span", "dd-caret", "▾"));
  const menu = el("div", "dd-menu");
  menu.setAttribute("role", "listbox");
  wrap.append(btn, menu);

  const close = () => wrap.classList.remove("open", "dd-flip");
  const syncLabel = () => {
    const opt = sel.options[sel.selectedIndex];
    label.textContent = opt
      ? (opt.dataset.sub ? `${opt.text} · ${opt.dataset.sub}` : opt.text)
      : "";
    btn.title = label.textContent; // 按钮宽度截断时补全（如长主机名）
    btn.disabled = sel.disabled;
  };
  const buildItems = () => {
    menu.replaceChildren();
    for (const opt of sel.options) {
      const item = el("button", "dd-item" + (opt.selected ? " active" : ""));
      item.type = "button";
      item.dataset.value = opt.value;
      item.setAttribute("role", "option");
      item.append(el("span", "dd-item-main", opt.text));
      if (opt.dataset.sub) item.append(el("span", "dd-item-sub", opt.dataset.sub));
      item.addEventListener("click", () => {
        sel.value = opt.value;
        close();
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        syncLabel();
      });
      menu.append(item);
    }
  };
  btn.addEventListener("click", () => {
    const willOpen = !wrap.classList.contains("open");
    document.querySelectorAll(".dd-wrap.open").forEach((w) => w.classList.remove("open"));
    if (!willOpen) return;
    buildItems();
    wrap.classList.add("open");
    // 按钮以下空间不足以容纳弹层时向上翻
    const r = btn.getBoundingClientRect();
    wrap.classList.toggle("dd-flip",
      r.bottom + Math.min(menu.children.length * 32 + 12, 320) > innerHeight - 8);
    const first = menu.querySelector(".dd-item.active") || menu.querySelector(".dd-item");
    if (first) first.focus();
  });
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { close(); btn.focus(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const items = [...menu.querySelectorAll(".dd-item")];
      if (!items.length) return;
      const i = items.indexOf(document.activeElement);
      const next = e.key === "ArrowDown" ? items[(i + 1) % items.length] : items[(i - 1 + items.length) % items.length];
      next.focus();
    }
  });
  document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) close(); });
  new MutationObserver(() => { syncLabel(); if (wrap.classList.contains("open")) buildItems(); })
    .observe(sel, { childList: true, attributes: true, attributeFilter: ["disabled"] });
  ddSync.set(sel.id, syncLabel);
  syncLabel();
}


/* ---------------- global state ---------------- */

const state = {
  scanCache: new Map(),    // machine("" = 本机) -> scan JSON
  machines: [],            // 已注册远程机器（halter machines list）
  machinesLocal: null,     // 本机显示信息 {host_name, os}（同一次 list --json 的 local 节点）
  machinesLoaded: false,
  machine: "",       // "" = 本机；其他 = machines.toml 里的机器名
  sessionsLoaded: false,
  sessions: [],
  projects: [],
  projectFilter: null,    // null = 全部项目；string = 项目路径
  toolFilters: new Set(), // 空集 = 全部助手
  viewMode: "timeline",   // "timeline" | "list"
  detailMode: "chat",     // "chat"（事件流卡片）| "raw"（原始 transcript 文本）
  lastDetail: null,       // 最近一次渲染的会话详情，视图切换时原地重渲染
  searchQuery: null,      // 搜索命中的关键词，进入详情时用于高亮同一条流
  matrixLayer: "skills",
  matrixGapsOnly: false,
  matrixHarnessOnly: true,
  expandedFamilies: new Set(),
  projectsError: null,
  projectMatches: [],
};

/* ---------------- view switching ---------------- */

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => {
      b.classList.remove("active");
      b.removeAttribute("aria-current");
    });
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    btn.setAttribute("aria-current", "page");
    $(`view-${btn.dataset.view}`).classList.add("active");
    if (btn.dataset.view === "overview") loadOverview();
    if (btn.dataset.view === "matrix") loadMatrix();
    if (btn.dataset.view === "sessions" && !state.sessionsLoaded) loadSessionsView();
    if (btn.dataset.view === "memory") loadMemory();
    if (btn.dataset.view === "providers") loadProviders();
    if (btn.dataset.view === "updates") loadUpdates();
  });
});

/* ---------------- overview ---------------- */

const LAYERS = [
  ["skills", "layer.skills"],
  ["agents", "layer.agents"],
  ["memory", "layer.memory"],
  ["statusline", "layer.statusline"],
  ["mcp_servers", "layer.mcp"],
  ["plugins", "layer.plugins"],
  ["mods", "layer.mods"],
  ["hooks", "layer.hooks"],
  ["sessions", "layer.sessions"],
];

async function fetchScan(machine = "", force = false) {
  if (!force && state.scanCache.has(machine)) return state.scanCache.get(machine);
  const data = await invoke("halter_scan", machine ? { machine } : {});
  state.scanCache.set(machine, data);
  return data;
}

// 当前视图使用的 scan 缓存（键 = 机器作用域，"" = 本机）
const scanCacheOf = (machine = state.machine) => state.scanCache.get(machine) || null;

async function loadOverview() {
  $("scan-error").classList.add("hidden");
  $("scan-loading").classList.remove("hidden");
  $("overview-content").classList.add("hidden");
  let data;
  try {
    data = await fetchScan(state.machine);
  } catch (err) {
    $("scan-loading").classList.add("hidden");
    showError($("scan-error"), err);
    return;
  }
  $("scan-loading").classList.add("hidden");
  renderOverview(data);
  $("overview-content").classList.remove("hidden");
}

function renderOverview(data) {
  const doctor = Array.isArray(data.doctor) ? data.doctor : [];
  const issues = doctor.filter((d) => d.level !== "ok");
  const doctorSection = $("doctor-section");
  const doctorList = $("doctor-list");
  doctorList.replaceChildren();
  if (issues.length) {
    doctorSection.classList.remove("hidden");
    for (const item of issues) {
      const row = el("div", `doctor-item ${item.level === "error" ? "error" : "warn"}`);
      row.append(el("span", "where", item.where || "-"));
      row.append(el("span", "", t(`doctor.${item.code}`, item.params)));
      doctorList.append(row);
    }
  } else {
    doctorSection.classList.add("hidden");
  }

  const tools = (data.inventory || []).filter((t2) => t2.installed);
  $("tools-heading").textContent = t("ov.toolsCount", { n: tools.length });
  const grid = $("tools-grid");
  grid.replaceChildren();
  for (const tool of tools) {
    const card = el("div", "tool-card");
    const header = el("div", "tool-card-header");
    const name = el("div", "tool-name", tool.display || tool.tool);
    if ((tool.category || "harness") === "editor") {
      name.append(el("span", "cat-badge editor", t("ov.badgeEditor")));
    }
    header.append(name);
    header.append(el("div", "tool-key", tool.tool));
    card.append(header);

    const chips = el("div", "layer-chips");
    for (const [key, labelKey] of LAYERS) {
      let cell, on;
      if (key === "memory") {
        const m = tool.memory;
        cell = !m ? "-" : m.linked ? "●" : m.present ? "◐" : "·";
        on = !!m && (m.linked || m.present);
      } else if (key === "statusline") {
        const s = tool.statusline;
        cell = !s ? "-" : s.linked ? "●" : s.present ? "◐" : "·";
        on = !!s && (s.linked || s.present);
      } else {
        const n = Array.isArray(tool[key]) ? tool[key].length : 0;
        cell = String(n);
        on = n > 0;
      }
      chips.append(el("span", `count-chip${on ? " on" : ""}`, `${t(labelKey)} ${cell}`));
    }
    card.append(chips);
    grid.append(card);
  }
}

$("btn-refresh-scan").addEventListener("click", () => {
  state.scanCache.delete(state.machine);
  loadOverview();
});

/* ---------------- matrix: harness × layer ---------------- */

const MATRIX_LAYERS = [
  { key: "skills", field: "skills", labelKey: "mlayer.skills" },
  { key: "agents", field: "agents", labelKey: "mlayer.agents" },
  { key: "memory", field: "memory", labelKey: "mlayer.memory" },
  { key: "statusline", field: "statusline", labelKey: "mlayer.statusline" },
  { key: "mcp", field: "mcp_servers", labelKey: "mlayer.mcp" },
  { key: "plugins", field: "plugins", labelKey: "mlayer.plugins" },
  // mods 是插件的函数式子集（JS/TS 事件模块），只盘点；安装与启停走 plugins 层
  { key: "mods", field: "mods", labelKey: "mlayer.mods", sync: false },
  { key: "hooks", field: "hooks", labelKey: "mlayer.hooks" },
];

// 支持矩阵跨机推送（幽灵行/拉取按钮）的层。statusline 的行名 = 工具名 = 推送
// 条目（每工具一个片段），天然适配行级推送；memory/plugins/sessions 无跨机通道。
const PUSH_LAYERS = ["skills", "agents", "mcp", "hooks", "statusline"];

function matrixItemName(layer, item) {
  if (layer.key === "plugins" || layer.key === "mods") return item.plugin_id;
  if (layer.key === "hooks") return item.label;
  if (layer.key === "memory") return "MEMORY";
  return item.name;
}

function buildMatrix(scan, layerKey) {
  const layer = MATRIX_LAYERS.find((l) => l.key === layerKey);
  let tools = (scan.inventory || []).filter((x) => x.installed);
  if (state.matrixHarnessOnly) {
    // harness = 独立 AI 编码代理；editor（vscode/continue/cline 等）默认不进矩阵列
    tools = tools.filter((x) => (x.category || "harness") !== "editor");
  }
  const rows = new Map();
  for (const tool of tools) {
    // statusline 层：每工具一行（行名 = 工具名 = 跨机推送条目）。各工具片段
    // 独立、不互通，非本工具列渲染「不适用」而非缺失；库有片段但工具未配置
    // （synced !== null 且 !present）也出行，格子为 ○ 可点击同步。
    if (layer.key === "statusline") {
      const s = tool.statusline;
      if (!s || (!s.present && s.synced === null)) continue;
      const row = { statuses: new Map(), items: [], item: s };
      if (s.present) {
        row.statuses.set(tool.tool, s.linked ? "synced" : "present");
        row.items.push(s);
      }
      rows.set(tool.tool, row);
      continue;
    }
    const entries = layer.key === "memory"
      ? (tool.memory && tool.memory.present ? [tool.memory] : [])
      : tool[layer.field] || [];
    for (const item of entries) {
      const name = matrixItemName(layer, item);
      if (!name) continue;
      let row = rows.get(name);
      if (!row) {
        row = { statuses: new Map(), items: [] };
        rows.set(name, row);
      }
      const linked = ["skills", "agents", "memory"].includes(layer.key)
        ? !!item.linked : false;
      row.statuses.set(tool.tool, linked ? "synced" : "present");
      row.items.push(item);
    }
  }
  const list = [...rows.entries()].map(([name, r]) => ({
    name,
    statuses: r.statuses,
    items: r.items,
    // 预览用代表条目：优先取带 description 的（各工具同名条目内容一致）；
    // plugins 层优先取带 source_url 的（悬停卡与来源按钮取数据）
    item: r.items.find((x) => x.description)
      || r.items.find((x) => x.source_url)
      || r.items[0],
    // statusline 行只对自己列有意义：缺失 = 自己列缺，其它列是不适用不是缺口
    missing: layer.key === "statusline"
      ? (r.statuses.has(name) ? 0 : 1)
      : tools.filter((x) => !r.statuses.has(x.tool)).length,
  }));
  list.sort((a, b) => b.missing - a.missing || a.name.localeCompare(b.name));
  return { tools, rows: list };
}

/* ----------
 * Skill families: `hyperframes` + `hyperframes-animation` + ... are one
 * skill family in the matrix. Conservative rule: a row joins family `root`
 * only when `root` itself exists as a row (avoids merging `paper-reader`
 * and `paper-polish`, which merely share a prefix).
 * ---------- */
function familyRootOf(name, names) {
  const parts = name.split("-");
  for (let i = 1; i < parts.length; i++) {
    const candidate = parts.slice(0, i).join("-");
    if (names.has(candidate)) return candidate;
  }
  return name;
}

function groupRowsIntoFamilies(rows) {
  const names = new Set(rows.map((r) => r.name));
  const byRoot = new Map();
  for (const row of rows) {
    const root = familyRootOf(row.name, names);
    if (!byRoot.has(root)) byRoot.set(root, []);
    byRoot.get(root).push(row);
  }
  const groups = [];
  for (const [root, members] of byRoot) {
    if (members.length < 2) {
      groups.push({ type: "single", row: members[0] });
      continue;
    }
    members.sort((a, b) => a.name.localeCompare(b.name));
    groups.push({ type: "family", root, members });
  }
  groups.sort((a, b) => {
    const ma = a.type === "family" ? a.members.reduce((x, m) => x + m.missing, 0) : a.row.missing;
    const mb = b.type === "family" ? b.members.reduce((x, m) => x + m.missing, 0) : b.row.missing;
    return mb - ma || familyName(a).localeCompare(familyName(b));
  });
  return groups;
}

const familyName = (g) => (g.type === "family" ? g.root : g.row.name);

function aggregateFamilyStatus(members, tool) {
  const sts = members.map((m) => m.statuses.get(tool) || "missing");
  if (sts.every((x) => x === "synced")) return "synced";
  if (sts.every((x) => x !== "missing")) return "present";
  if (sts.some((x) => x !== "missing")) return "partial";
  return "missing";
}

async function loadMatrix(force = false) {
  $("matrix-error").classList.add("hidden");
  $("matrix-loading").classList.remove("hidden");
  $("matrix-content").classList.add("hidden");
  let data;
  try {
    data = await fetchScan(state.machine, force);
    if (state.machine) {
      // 幽灵行/拉取按钮需要本机 scan 作对照；失败不阻塞远端矩阵展示
      try { await fetchScan("", force); } catch { /* 本机 scan 失败时跳过对照 */ }
    }
  } catch (err) {
    $("matrix-loading").classList.add("hidden");
    showError($("matrix-error"), err);
    return;
  }
  if (!state.machinesLoaded) await loadMachines();
  $("matrix-loading").classList.add("hidden");
  renderMatrixLayerTabs();
  renderMatrix(data);
  $("matrix-content").classList.remove("hidden");
}

function renderMatrixLayerTabs() {
  const tabs = $("matrix-layer-tabs");
  tabs.replaceChildren();
  for (const layer of MATRIX_LAYERS) {
    const btn = el("button", `layer-tab${state.matrixLayer === layer.key ? " active" : ""}`, t(layer.labelKey));
    btn.addEventListener("click", () => {
      state.matrixLayer = layer.key;
      renderMatrixLayerTabs();
      renderMatrix(scanCacheOf());
    });
    tabs.append(btn);
  }
}

/* ---------------- hover preview card（skill 悬停预览） ---------------- */

let hoverTimer = null;

function ensureHoverCard() {
  let card = document.getElementById("hover-card");
  if (!card) {
    card = el("div", "hover-card hidden");
    card.id = "hover-card";
    document.body.append(card);
  }
  return card;
}

function hideHoverCard() {
  clearTimeout(hoverTimer);
  const card = document.getElementById("hover-card");
  if (card) card.classList.add("hidden");
}

function showHoverCard(target, buildBody) {
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(() => {
    const card = ensureHoverCard();
    card.replaceChildren(...buildBody());
    card.classList.remove("hidden");
    // 先复位再测量，避免上一位置的尺寸影响定位
    card.style.left = "0px";
    card.style.top = "0px";
    const rect = target.getBoundingClientRect();
    let x = rect.left;
    let y = rect.bottom + 6;
    if (x + card.offsetWidth > window.innerWidth - 8) {
      x = Math.max(8, window.innerWidth - card.offsetWidth - 8);
    }
    if (y + card.offsetHeight > window.innerHeight - 8) {
      y = Math.max(8, rect.top - card.offsetHeight - 6);
    }
    card.style.left = `${x}px`;
    card.style.top = `${y}px`;
  }, 180);
}

function attachHoverPreview(cell, buildBody) {
  cell.addEventListener("mouseenter", () => showHoverCard(cell, buildBody));
  cell.addEventListener("mouseleave", hideHoverCard);
}

function skillHoverBody(row) {
  const nodes = [el("div", "hc-name", row.name)];
  const desc = row.item && row.item.description;
  nodes.push(el("div", `hc-desc${desc ? "" : " empty"}`, desc || t("hv.noDesc")));
  const meta = el("div", "hc-meta");
  if (row.item && row.item.path) {
    meta.append(el("span", "hc-path", shortenPath(row.item.path)));
  }
  if (row.statuses.size && [...row.statuses.values()].includes("synced")) {
    meta.append(el("span", "hc-linked", t("hv.linked")));
  }
  nodes.push(meta);
  return nodes;
}

function pluginHoverBody(row) {
  const nodes = [el("div", "hc-name", row.name)];
  const it = row.item || {};
  const bits = [];
  if (it.version) bits.push(`v${it.version}`);
  if (it.marketplace) bits.push(it.marketplace);
  if (it.origin && it.origin !== "marketplace") bits.push(`@${it.origin}`);
  if (bits.length) nodes.push(el("div", "hc-sub", bits.join(" · ")));
  if (it.source_url) {
    nodes.push(el("div", "hc-src", t("hv.sourceRepo", { url: it.source_url })));
  }
  return nodes;
}

function familyHoverBody(group) {
  const nodes = [el("div", "hc-name", t("hv.familyName", { root: group.root }))];
  nodes.push(el("div", "hc-sub", t("hv.familyCount", { n: group.members.length })));
  const list = el("div", "hc-member-list");
  for (const m of group.members) {
    const item = el("div", "hc-member");
    item.append(el("div", "hc-member-name", m.name));
    item.append(el("div", "hc-member-desc", m.item && m.item.description
      ? firstLine(m.item.description)
      : t("hv.memberNoDesc")));
    list.append(item);
  }
  nodes.push(list);
  return nodes;
}

const firstLine = (text) => {
  const line = String(text).split(/(?<=[。.!?；;])\s+/)[0] || String(text);
  return line.length > 90 ? `${line.slice(0, 90)}…` : line;
};

function renderMatrix(scan) {
  const layer = MATRIX_LAYERS.find((l) => l.key === state.matrixLayer);
  const { tools, rows } = buildMatrix(scan, state.matrixLayer);

  // 跨机对照：远端视图下，本机行集用于「拉取」按钮与幽灵行
  const crossCompare = state.machine && PUSH_LAYERS.includes(layer.key)
    ? buildMatrix(scanCacheOf("") || { inventory: [] }, layer.key)
    : null;
  const localNames = crossCompare ? new Set(crossCompare.rows.map((r) => r.name)) : null;
  const ghostNames = crossCompare
    ? crossCompare.rows.filter((r) => !rows.some((x) => x.name === r.name)).map((r) => r.name)
    : [];

  const allGroups = groupRowsIntoFamilies(rows);
  const shownGroups = state.matrixGapsOnly
    ? allGroups.filter((g) => (g.type === "family" ? g.members.some((m) => m.missing > 0) : g.row.missing > 0))
    : allGroups;
  const totalGaps = rows.reduce((acc, r) => acc + r.missing, 0);
  const gapShown = state.matrixGapsOnly
    ? shownGroups.reduce((a, g) => a + (g.type === "family" ? g.members.filter((m) => m.missing > 0).length : 1), 0)
    : 0;

  const scopeLabel = state.matrixHarnessOnly
    ? t("mx.scopeHarness", { n: tools.length })
    : t("mx.scopeAll", { n: tools.length });
  $("matrix-summary").textContent =
    t("mx.summary", { label: t(layer.labelKey), rows: rows.length, scope: scopeLabel, gaps: totalGaps }) +
    (state.matrixGapsOnly ? t("mx.summaryGapsOnly", { n: gapShown }) : "");

  const wrap = $("matrix-wrap");
  wrap.replaceChildren();
  if (!shownGroups.length) {
    wrap.append(el("div", "loading", state.matrixGapsOnly ? t("mx.noGaps") : t("mx.noData")));
    return;
  }

  const groups = shownGroups;
  const familyCount = groups.filter((g) => g.type === "family").length;
  if (familyCount) {
    const base = $("matrix-summary").textContent;
    $("matrix-summary").textContent = base + t("mx.summaryFamilies", { n: familyCount });
  }

  const grid = el("div", "matrix");
  grid.style.gridTemplateColumns = `220px repeat(${tools.length}, 78px)`;

  // header row
  grid.append(el("div", "mx-corner"));
  for (const tool of tools) {
    const head = el("div", "mx-tool-head");
    const dot = el("span", "filter-dot");
    dot.style.backgroundColor = toolColor(tool.tool);
    head.append(dot, el("span", "", tool.tool));
    grid.append(head);
  }

  const CELL_GLYPH = { synced: "●", present: "◐", partial: "◔", missing: "○" };
  const cellStatusText = (st) => ({
    synced: t("mx.legendSynced"),
    present: t("mx.legendPresent"),
    partial: t("mx.cellPartial"),
    missing: t("mx.legendMissing"),
  })[st] || st;

  // mods 层无独立同步动作（mod 即插件），格子不可点击同步
  const cellSyncOn = layer.key !== "mods";

  const renderRow = (row, cls) => {
    const nameCell = el("div", `mx-name ${cls || ""}`);
    nameCell.title = row.name;
    if (layer.key === "skills") {
      nameCell.removeAttribute("title");
      attachHoverPreview(nameCell, () => skillHoverBody(row));
    }
    if (layer.key === "plugins" || layer.key === "mods") {
      nameCell.removeAttribute("title");
      attachHoverPreview(nameCell, () => pluginHoverBody(row));
    }
    nameCell.append(el("span", "", row.name));
    // 来源仓库：市场清单/仓库页，点击用系统浏览器打开
    if (layer.key === "plugins" && row.item && row.item.source_url) {
      const src = el("button", "mx-src-btn", "↗");
      src.title = t("mx.sourceHint", { url: row.item.source_url });
      src.addEventListener("click", (e) => {
        e.stopPropagation();
        invoke("open_url", { url: row.item.source_url })
          .catch((err) => showMatrixToast(t("mx.sourceFailed"), errorDetail(err), true));
      });
      nameCell.append(src);
    }
    // 远端视图：本机没有的条目给一个「拉取到本机」入口
    if (localNames && !localNames.has(row.name)) {
      const pull = el("button", "mx-pull-btn", "↓");
      pull.title = t("mx.pullHint", { item: row.name, machine: state.machine });
      pull.addEventListener("click", (e) => {
        e.stopPropagation();
        pullRemoteItem(row.name);
      });
      nameCell.append(pull);
    }
    grid.append(nameCell);
    for (const tool of tools) {
      // statusline 行：非本工具列是不适用（片段独立），既非缺失也不可点
      if (layer.key === "statusline" && tool.tool !== row.name) {
        const na = el("div", "mx-cell na", "—");
        na.title = t("mx.cellNA");
        grid.append(na);
        continue;
      }
      const st = row.statuses.get(tool.tool) || "missing";
      const cell = el("div", `mx-cell ${st}`, CELL_GLYPH[st] || "○");
      cell.title = t("mx.cellTitle", { tool: tool.tool, status: cellStatusText(st) });
      if (cellSyncOn) attachCellSync(cell, tool.tool, [row.name], st);
      grid.append(cell);
    }
  };

  for (const group of groups) {
    if (group.type === "single") {
      renderRow(group.row);
      continue;
    }

    // family row (aggregated)
    const expanded = state.expandedFamilies.has(group.root);
    const nameCell = el("div", "mx-name family");
    nameCell.title = t("mx.familyTitle", { root: group.root, n: group.members.length });
    if (layer.key === "skills") {
      nameCell.removeAttribute("title");
      attachHoverPreview(nameCell, () => familyHoverBody(group));
    }
    const caret = el("span", "mx-caret", expanded ? "▾" : "▸");
    nameCell.append(caret, el("span", "", group.root));
    nameCell.append(el("span", "mx-family-count", String(group.members.length)));
    nameCell.addEventListener("click", () => {
      if (expanded) state.expandedFamilies.delete(group.root);
      else state.expandedFamilies.add(group.root);
      renderMatrix(scanCacheOf());
    });
    grid.append(nameCell);
    for (const tool of tools) {
      const st = aggregateFamilyStatus(group.members, tool);
      const present = group.members.filter((m) => m.statuses.has(tool.tool)).length;
      const cell = el("div", `mx-cell ${st}`, CELL_GLYPH[st] || "○");
      cell.title = t("mx.cellTitleFamily", {
        tool: tool.tool,
        status: cellStatusText(st),
        present,
        total: group.members.length,
      });
      // 家族聚合格：点击 = 整个家族同步到该工具
      if (cellSyncOn) attachCellSync(cell, tool.tool, group.members.map((m) => m.name), st);
      grid.append(cell);
    }

    // member rows when expanded (respect gaps-only)
    if (expanded) {
      const members = state.matrixGapsOnly
        ? group.members.filter((m) => m.missing > 0)
        : group.members;
      for (const m of members) renderRow(m, "member");
    }
  }

  // 幽灵行：本机有、此机无的条目（远端视图专属），点击推送到当前机器
  if (ghostNames.length) {
    const sep = el("div", "mx-ghost-sep", t("mx.ghostSection", { machine: state.machine }));
    sep.style.gridColumn = "1 / -1";
    grid.append(sep);
    for (const name of ghostNames) {
      const nameCell = el("div", "mx-name ghost");
      nameCell.title = t("mx.ghostRowTitle", { item: name, machine: state.machine });
      nameCell.append(el("span", "", name));
      grid.append(nameCell);
      const cell = el("div", "mx-cell ghost-push", `→ ${t("mx.ghostPush")}`);
      cell.title = nameCell.title;
      cell.style.gridColumn = "2 / -1";
      cell.addEventListener("click", () => pushGhostItem(name, cell));
      grid.append(cell);
    }
  }
  wrap.append(grid);
}

$("matrix-gaps-only").addEventListener("change", (e) => {
  state.matrixGapsOnly = e.target.checked;
  if (scanCacheOf()) renderMatrix(scanCacheOf());
});

$("matrix-harness-only").addEventListener("change", (e) => {
  state.matrixHarnessOnly = e.target.checked;
  if (scanCacheOf()) renderMatrix(scanCacheOf());
});

$("btn-refresh-matrix").addEventListener("click", () => loadMatrix(true));

/* ---------------- 单元格点击同步：点一下圆点 = 该条目 → 该工具 ---------------- */

// 按 (动作|机器|目标) 粒度加锁：不同单元格/机器可并行，同一目标不重入
const syncBusyKeys = new Set();

function attachCellSync(cell, tool, names, st) {
  if (st === "synced") return; // 已同步格无事可做
  cell.classList.add("clickable");
  cell.title += " · " + t("mx.cellClickHint");
  cell.addEventListener("click", () => syncMatrixCell(cell, tool, names));
}

async function syncMatrixCell(cell, tool, names) {
  const key = `sync|${state.machine}|${tool}|${names.join(",")}`;
  if (syncBusyKeys.has(key)) return;
  syncBusyKeys.add(key);
  cell.classList.add("syncing");
  let result = null;
  let invokeErr = null;
  try {
    result = await invoke("halter_sync", {
      apply: true,
      layers: [state.matrixLayer],
      tool,
      items: names,
      ...(state.machine ? { machine: state.machine } : {}),
    });
  } catch (err) {
    invokeErr = err;
  }
  syncBusyKeys.delete(key);
  cell.classList.remove("syncing");
  if (invokeErr) {
    showMatrixToast(t("mx.syncFailed", { tool }), errorDetail(invokeErr), true);
    return;
  }
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(
    t(result.ok ? "mx.syncDone" : "mx.syncFailed", { tool }),
    body || t("mx.syncNoop"),
    !result.ok,
  );
  await loadMatrix(true); // 强制重扫，圆点状态即时更新
}

/* ---------------- 跨机器单条目同步：幽灵行推送 / 行级拉取 ---------------- */

async function pushEntry(layer, item, { to, from }) {
  const machine = to || from;
  const key = `push|${machine}|${item}`;
  if (syncBusyKeys.has(key)) return { ok: false };
  syncBusyKeys.add(key);
  let result = null;
  try {
    result = await invoke("halter_push", {
      layer,
      item,
      to: to || null,
      from: from || null,
      withSecrets: false,
    });
  } finally {
    syncBusyKeys.delete(key);
  }
  return result;
}

async function pushGhostItem(item, cell) {
  cell.classList.add("syncing");
  let result = null;
  try {
    result = await pushEntry(state.matrixLayer, item, { to: state.machine });
  } catch (err) {
    cell.classList.remove("syncing");
    showMatrixToast(t("mx.pushFailed", { item, machine: state.machine }), errorDetail(err), true);
    return;
  }
  cell.classList.remove("syncing");
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(
    t(result.ok ? "mx.pushDone" : "mx.pushFailed", { item, machine: state.machine }),
    body || t("mx.syncNoop"),
    !result.ok,
  );
  await loadMatrix(true); // 推送后两端状态都可能变化，强制重扫
}

async function pullRemoteItem(item) {
  let result = null;
  try {
    result = await pushEntry(state.matrixLayer, item, { from: state.machine });
  } catch (err) {
    showMatrixToast(t("mx.pullFailed", { item, machine: state.machine }), errorDetail(err), true);
    return;
  }
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(
    t(result.ok ? "mx.pullDone" : "mx.pullFailed", { item, machine: state.machine }),
    body || t("mx.syncNoop"),
    !result.ok,
  );
  await loadMatrix(true);
}

let matrixToastTimer = null;

function showMatrixToast(title, body, isError) {
  let toast = document.getElementById("matrix-toast");
  if (!toast) {
    toast = el("div", "matrix-toast");
    toast.id = "matrix-toast";
    toast.addEventListener("click", hideMatrixToast);
    document.body.append(toast);
  }
  toast.classList.toggle("error", !!isError);
  toast.replaceChildren(el("div", "mx-toast-title", title), el("pre", "mx-toast-body", body));
  toast.classList.remove("hidden");
  clearTimeout(matrixToastTimer);
  matrixToastTimer = setTimeout(hideMatrixToast, isError ? 12000 : 7000);
}

function hideMatrixToast() {
  clearTimeout(matrixToastTimer);
  const toast = document.getElementById("matrix-toast");
  if (toast) toast.classList.add("hidden");
}

/* ---------------- sessions ---------------- */

let sessionsRequestId = 0;
let projectsRequestId = 0;
let sessionDetailRequestId = 0;
let searchRequestId = 0;

function currentProject() {
  // 维度面板接管项目选择后，这里仅作为 CLI --project 标记参数
  return ".";
}

// 实际传给 sidecar 的项目参数：必须跟随维度面板的选中值，
// 而不是 "."（App 从 Finder 启动时 cwd 是 /，会把所有项目都匹配进来）。
function projectArg() {
  return state.projectFilter || currentProject();
}

async function loadSessionsView() {
  await loadSessionProjects();
  await loadSessions();
}

/* ---------- dimension 3: project picker ---------- */

async function loadSessionProjects() {
  const requestId = ++projectsRequestId;
  state.projectsError = null;
  let data;
  try {
    data = await invoke("halter_sessions_projects", {
      project: currentProject(),
      machine: state.machine || undefined,
    });
  } catch (err) {
    if (requestId !== projectsRequestId) return;
    state.projectsError = errorDetail(err);
    renderProjectInput();
    if (projectDropdownOpen) renderProjectDropdownList($("project-input").value);
    return;
  }
  if (requestId !== projectsRequestId) return;
  state.projects = data.projects || [];
  renderProjectInput();
  if (projectDropdownOpen) renderProjectDropdownList($("project-input").value);
}

let projectDropdownOpen = false;

function toggleProjectDropdown(open) {
  projectDropdownOpen = open;
  $("project-dropdown").classList.toggle("hidden", !open);
  if (!open) return;
  renderProjectDropdownList($("project-input").value);
}

function selectProject(path) {
  state.projectFilter = path;
  toggleProjectDropdown(false);
  renderProjectInput();
  loadSessions();
}

function renderProjectInput() {
  const input = $("project-input");
  const clear = $("project-clear");
  if (state.projectsError) {
    input.value = "";
    input.placeholder = t("ss.projectLoadFailed");
    clear.classList.add("hidden");
    return;
  }
  if (state.projectFilter === null) {
    input.value = "";
    const real = state.projects.filter((p) => (p.kind || "project") === "project").length;
    const others = state.projects.length - real;
    input.placeholder = others
      ? t("ss.allProjectsInputAll", { n: real, m: others })
      : t("ss.allProjectsInput", { n: real });
    clear.classList.add("hidden");
  } else {
    const p = state.projects.find((x) => x.path === state.projectFilter);
    input.value = p ? (p.name || basename(p.path)) : basename(state.projectFilter);
    input.placeholder = t("ss.projectInputFilter");
    clear.classList.remove("hidden");
  }
}

function renderProjectDropdownList(filter) {
  const listEl = $("project-dropdown-list");
  listEl.replaceChildren();
  if (state.projectsError) {
    const box = el("div", "error-box");
    box.textContent = state.projectsError;
    listEl.append(box);
    state.projectMatches = [];
    return;
  }
  const needle = (filter || "").trim().toLowerCase();

  const allRow = el("div", `project-item${state.projectFilter === null ? " selected" : ""}`);
  const allHead = el("div", "project-head");
  allHead.append(el("span", "project-name", t("ss.allProjects")));
  const totalSessions = state.projects.reduce((a, p) => a + p.sessions, 0);
  allHead.append(el("span", "project-count", t("ss.projectsSessions", { n: state.projects.length, m: totalSessions })));
  allRow.append(allHead);
  allRow.addEventListener("click", () => selectProject(null));
  listEl.append(allRow);

  const projects = needle
    ? state.projects.filter((p) =>
        (p.path || "").toLowerCase().includes(needle) ||
        (p.name || "").toLowerCase().includes(needle))
    : state.projects;
  const real = projects.filter((p) => (p.kind || "project") === "project");
  const others = projects.filter((p) => (p.kind || "project") !== "project");

  const renderProjectRow = (p) => {
    const row = el("div", `project-item${state.projectFilter === p.path ? " selected" : ""}`);
    const head = el("div", "project-head");
    const name = el("span", "project-name", p.name || basename(p.path));
    if (p.kind && p.kind !== "project") {
      name.append(el("span", "project-kind-badge", t(`ss.kind.${p.kind}`)));
    }
    head.append(name);
    head.append(el("span", "project-count", t("ss.nSessions", { n: p.sessions })));
    row.append(head);

    const sub = el("div", "project-sub");
    const dots = el("span", "project-dots");
    for (const tool of p.tools || []) {
      const dot = el("span", "filter-dot");
      dot.style.backgroundColor = toolColor(tool);
      dot.title = tool;
      dots.append(dot);
    }
    sub.append(dots);
    const pathEl = el("span", "project-path", shortenPath(p.path));
    pathEl.title = p.path;
    sub.append(pathEl);
    sub.append(el("span", "project-last", relTime(p.last_activity)));
    row.append(sub);

    row.addEventListener("click", () => selectProject(p.path));
    listEl.append(row);
  };

  for (const p of real) renderProjectRow(p);
  if (others.length) {
    listEl.append(el("div", "project-group-header", t("ss.othersGroupHeader", { n: others.length })));
    for (const p of others) renderProjectRow(p);
  }
  state.projectMatches = projects;
  if (needle && !projects.length) {
    const raw = filter.trim();
    if (raw.startsWith("/") || raw.startsWith("~")) {
      const hint = el("div", "project-raw-hint", t("ss.rawPathHint", { path: raw }));
      listEl.append(hint);
    } else {
      listEl.append(el("div", "loading", t("ss.noMatchProjects")));
    }
  }
}

const projectInput = () => $("project-input");

projectInput().addEventListener("focus", () => {
  if (!state.projectsError && state.projectFilter !== null) return; // 已选中时聚焦不弹层，避免打断编辑
  toggleProjectDropdown(true);
});
projectInput().addEventListener("input", (e) => {
  if (state.projectsError) {
    state.projectsError = null;
    loadSessionProjects();
    return;
  }
  toggleProjectDropdown(true);
  renderProjectDropdownList(e.target.value);
});
projectInput().addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    const value = e.target.value.trim();
    const first = state.projectMatches[0];
    if (first) {
      selectProject(first.path);
    } else if (value.startsWith("/") || value.startsWith("~")) {
      selectProject(value);
    }
  } else if (e.key === "Escape") {
    toggleProjectDropdown(false);
    renderProjectInput();
    e.target.blur();
  }
});
$("project-clear").addEventListener("click", (e) => {
  e.stopPropagation();
  selectProject(null);
});
document.addEventListener("click", (e) => {
  if (projectDropdownOpen && !e.target.closest(".facet-dropdown-wrap")) {
    toggleProjectDropdown(false);
    renderProjectInput();
  }
});

async function loadSessions() {
  const requestId = ++sessionsRequestId;
  const list = $("sessions-list");
  list.replaceChildren(el("div", "loading", t("ss.loadingSessions")));
  const allProjects = state.projectFilter === null;
  let data;
  try {
    data = await invoke("halter_sessions_list", {
      project: projectArg(),
      limit: 500,
      allProjects,
      machine: state.machine || undefined,
    });
  } catch (err) {
    if (requestId !== sessionsRequestId) return;
    list.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = errorDetail(err);
    list.append(box);
    return;
  }
  if (requestId !== sessionsRequestId) return;
  state.sessionsLoaded = true;
  state.sessions = data.sessions || [];
  renderToolFilter();
  renderSessions(state.sessions);
}

/* ---------- dimension 1: harness filter ---------- */

function renderToolFilter() {
  const box = $("tool-filter");
  const counts = new Map();
  for (const s of state.sessions) {
    counts.set(s.tool, (counts.get(s.tool) || 0) + 1);
  }
  $("tool-filter-row").classList.toggle("hidden", counts.size < 2);
  box.replaceChildren();
  if (counts.size < 2) return;

  const anySelected = state.toolFilters.size > 0;
  const all = el("button", `filter-chip${!anySelected ? " active" : ""}`, t("ss.allTools", { n: state.sessions.length }));
  all.addEventListener("click", () => {
    state.toolFilters.clear();
    renderToolFilter();
    renderSessions(state.sessions);
  });
  box.append(all);

  for (const [tool, n] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
    const chip = el("button", `filter-chip${state.toolFilters.has(tool) ? " active" : ""}`);
    const dot = el("span", "filter-dot");
    dot.style.backgroundColor = toolColor(tool);
    chip.append(dot, el("span", "", tool), el("span", "filter-count", String(n)));
    chip.addEventListener("click", () => {
      if (state.toolFilters.has(tool)) state.toolFilters.delete(tool);
      else state.toolFilters.add(tool);
      renderToolFilter();
      renderSessions(state.sessions);
    });
    box.append(chip);
  }
}

function applyToolFilter(items) {
  return state.toolFilters.size ? items.filter((s) => state.toolFilters.has(s.tool)) : items;
}

/* ---------- dimension 2: timeline / list views ---------- */

function renderSessions(items) {
  const filtered = applyToolFilter(items);
  updateSessionCount(filtered.length, items.length);
  $("session-sort").textContent = t("ss.sortRecent");
  state.searchQuery = null; // 高亮只跟随搜索结果，回到普通列表即失效
  if (state.viewMode === "timeline") renderTimeline(filtered);
  else renderFlatList(filtered);
}

function updateSessionCount(shown, total, suffix) {
  const scope = state.projectFilter === null ? t("ss.allProjects") : basename(state.projectFilter);
  const toolScope = state.toolFilters.size ? ` · ${t("ss.countTools", { n: state.toolFilters.size })}` : "";
  const tail = suffix ? ` · ${suffix}` : "";
  const node = $("session-count");
  node.textContent = `${shown} / ${total}${tail}`;
  node.title = `${scope}${toolScope}`;
}

function sessionCard(s, opts = {}) {
  const item = el("div", "session-item");
  item.style.setProperty("--tool-color", toolColor(s.tool));
  if (opts.timeline) item.classList.add("tl-card");

  const top = el("div", "session-top");
  const titleText = s.title || s.ref;
  const title = el("div", "session-title", titleText);
  title.title = titleText;
  top.append(title);
  const stamp = s.updated_at || s.started_at;
  const time = el("span", "session-time", opts.timeText || relTime(stamp));
  time.title = fmtDate(stamp);
  top.append(time);
  item.append(top);

  const meta = el("div", "session-meta");
  const toolTag = el("span", "tool-tag");
  const dot = el("span", "filter-dot");
  dot.style.backgroundColor = toolColor(s.tool);
  toolTag.append(dot, el("span", "", s.tool));
  meta.append(toolTag);
  if (state.projectFilter === null && s.project) {
    meta.append(el("span", "project-chip", basename(s.project)));
  }
  meta.append(el("span", "meta-item", t("ss.nMessages", { n: s.message_count })));
  if (s.branch) meta.append(el("span", "meta-item", `⑂ ${s.branch}`));

  item.append(meta);
  item.addEventListener("click", () => selectSession(s, item));
  return item;
}

function renderFlatList(items) {
  const list = $("sessions-list");
  list.className = "sessions-list";
  list.replaceChildren();
  if (!items.length) {
    list.append(el("div", "loading", state.toolFilters.size ? t("ss.noSessionsFiltered") : t("ss.noSessions")));
    return;
  }
  for (const s of items) list.append(sessionCard(s));
}

function renderTimeline(items) {
  const list = $("sessions-list");
  list.className = "sessions-list timeline";
  list.replaceChildren();
  if (!items.length) {
    list.append(el("div", "loading", state.toolFilters.size ? t("ss.noSessionsFiltered") : t("ss.noSessions")));
    return;
  }

  const groups = [];
  const byKey = new Map();
  for (const s of items) {
    const key = dateKey(s.updated_at || s.started_at);
    if (!byKey.has(key)) {
      byKey.set(key, []);
      groups.push(key);
    }
    byKey.get(key).push(s);
  }

  const tl = el("div", "timeline");
  for (const key of groups) {
    const group = el("div", "tl-group");
    const date = el("div", "tl-date");
    date.append(el("span", "", dateLabel(key)));
    date.append(el("span", "tl-count", t("ss.dayCount", { n: byKey.get(key).length })));
    group.append(date);
    for (const s of byKey.get(key)) {
      const entry = el("div", "tl-item");
      const dot = el("span", "tl-dot");
      dot.style.backgroundColor = toolColor(s.tool);
      entry.append(dot);
      entry.append(sessionCard(s, { timeline: true, timeText: fmtTime(s.updated_at || s.started_at) }));
      group.append(entry);
    }
    tl.append(group);
  }
  list.append(tl);
}

/* calendar helpers */
const WEEKDAYS = {
  zh: ["日", "一", "二", "三", "四", "五", "六"],
  en: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
};

function dateKey(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "unknown";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function dateLabel(key) {
  const today = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const todayKey = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
  const yest = new Date(today);
  yest.setDate(yest.getDate() - 1);
  const yestKey = `${yest.getFullYear()}-${pad(yest.getMonth() + 1)}-${pad(yest.getDate())}`;
  if (key === todayKey) return t("date.today");
  if (key === yestKey) return t("date.yesterday");
  const d = new Date(key + "T00:00:00");
  if (!Number.isNaN(d.getTime())) {
    return t("date.weekday", { date: key, w: (WEEKDAYS[i18nLang] || WEEKDAYS.zh)[d.getDay()] });
  }
  return key;
}

/* ---------- view mode toggle ---------- */

function setViewMode(mode) {
  state.viewMode = mode;
  $("btn-view-list").classList.toggle("active", mode === "list");
  $("btn-view-list").setAttribute("aria-pressed", String(mode === "list"));
  $("btn-view-timeline").classList.toggle("active", mode === "timeline");
  $("btn-view-timeline").setAttribute("aria-pressed", String(mode === "timeline"));
  renderSessions(state.sessions);
}

$("btn-view-list").addEventListener("click", () => setViewMode("list"));
$("btn-view-timeline").addEventListener("click", () => setViewMode("timeline"));

/* ---------- session detail（弹层：选中会话才出现，不常驻右栏） ---------- */

function closeSessionModal() {
  $("session-modal").classList.add("hidden");
  const detail = $("session-detail");
  detail.replaceChildren();
  state.lastDetail = null;
  document.querySelectorAll(".session-item.selected").forEach((n) => n.classList.remove("selected"));
}

async function selectSession(session, itemEl) {
  const requestId = ++sessionDetailRequestId;
  document.querySelectorAll(".session-item.selected").forEach((n) => n.classList.remove("selected"));
  if (itemEl) itemEl.classList.add("selected");

  $("session-modal").classList.remove("hidden");
  const detail = $("session-detail");
  detail.className = "session-detail";
  detail.replaceChildren(el("div", "loading", t("ss.loadingDetail")));

  const project = session.project || state.projectFilter || ".";
  let data;
  try {
    data = await invoke("halter_sessions_show", {
      ref: session.ref,
      project,
      tail: 120,
      machine: state.machine || undefined,
    });
  } catch (err) {
    if (requestId !== sessionDetailRequestId) return;
    detail.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = errorDetail(err);
    detail.append(box);
    return;
  }
  if (requestId !== sessionDetailRequestId) return;
  renderSessionDetail(data);
}

function metaCell(label, value) {
  const cell = el("div", "meta-cell");
  cell.append(el("div", "meta-label", label));
  cell.append(el("div", "meta-value", value ?? "-"));
  return cell;
}

/* ---------- transcript 事件流渲染（借鉴 dsh Trajectory 的形态） ---------- */

const MSG_HEADER_RE = /^## (\S+) (USER|ASSISTANT|TOOL)(?:\s+\((.+)\))?$/;

function parseTranscript(text) {
  const messages = [];
  let cur = null;
  for (const line of String(text).split("\n")) {
    const m = line.match(MSG_HEADER_RE);
    if (m) {
      if (cur) messages.push(cur);
      cur = { stamp: m[1], role: m[2], tool: m[3] || null, text: "" };
    } else if (cur) {
      cur.text += (cur.text ? "\n" : "") + line;
    }
  }
  if (cur) messages.push(cur);
  return messages;
}

function appendMarkedText(parent, text, query) {
  // 命中词高亮：搜索作用于同一条事件流
  const needle = (query || "").trim();
  if (!needle) {
    parent.append(document.createTextNode(text));
    return;
  }
  const lower = text.toLowerCase();
  const hit = needle.toLowerCase();
  let i = 0;
  for (;;) {
    const at = lower.indexOf(hit, i);
    if (at < 0) break;
    if (at > i) parent.append(document.createTextNode(text.slice(i, at)));
    parent.append(el("mark", "", text.slice(at, at + needle.length)));
    i = at + needle.length;
  }
  if (i < text.length) parent.append(document.createTextNode(text.slice(i)));
}

function msgBlock(msg, query) {
  const block = el("div", `msg-block role-${msg.role}`);
  const head = el("div", "msg-head");
  const collapsible = msg.role === "TOOL";
  if (collapsible) {
    head.classList.add("collapsible");
    head.title = t("ss.traceToggleHint");
    const caret = el("span", "msg-caret", "▸");
    head.append(caret);
  }
  head.append(el("span", `msg-role ${msg.role}`, msg.role));
  if (msg.tool) head.append(el("span", "msg-tool", msg.tool));
  const stamp = el("span", "msg-time", msg.stamp && msg.stamp !== "-" ? fmtTime(msg.stamp) : "");
  if (msg.stamp && msg.stamp !== "-") stamp.title = fmtDate(msg.stamp);
  head.append(stamp);
  block.append(head);

  const body = el("div", "msg-body");
  appendMarkedText(body, msg.text.replace(/^\n+|\n+$/g, ""), query);
  block.append(body);

  if (collapsible) {
    const hasHit = query && msg.text.toLowerCase().includes(query.trim().toLowerCase());
    if (!hasHit) block.classList.add("collapsed"); // 命中的工具块自动展开，保证高亮可见
    const preview = el("div", "msg-preview", msg.text.replace(/\s+/g, " ").trim().slice(0, 160) || t("ss.traceEmptyTool"));
    block.append(preview);
    head.addEventListener("click", () => {
      block.classList.toggle("collapsed");
      const caret = head.querySelector(".msg-caret");
      if (caret) caret.textContent = block.classList.contains("collapsed") ? "▸" : "▾";
    });
  }
  return block;
}

function renderTraceStream(detail, messages) {
  const counts = { USER: 0, ASSISTANT: 0, TOOL: 0 };
  for (const m of messages) counts[m.role] = (counts[m.role] || 0) + 1;

  const metrics = el("div", "trace-metrics");
  metrics.append(el("span", "tm-item total", t("ss.traceTotal", { n: state.lastDetail.session.message_count })));
  if (state.lastDetail.session.message_count > messages.length) {
    metrics.append(el("span", "tm-item", t("ss.traceTail", { n: messages.length })));
  }
  for (const role of ["USER", "ASSISTANT", "TOOL"]) {
    if (counts[role]) metrics.append(el("span", `tm-item role-${role}`, `${role} ${counts[role]}`));
  }
  detail.append(metrics);

  const stream = el("div", "msg-stream");
  for (const m of messages) stream.append(msgBlock(m, state.searchQuery));
  detail.append(stream);
}

function setDetailMode(mode) {
  state.detailMode = mode;
  if (state.lastDetail) renderSessionDetail(state.lastDetail);
}

// 单色描边小图标（Lucide 路径）：复制按钮与复制成功/失败反馈共用
const ICON_PATHS = {
  copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
};

function iconSvg(name) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name]}</svg>`;
}

async function copyToClipboard(text, btn, restoreKey, { icon = false } = {}) {
  let ok = true;
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    const ta = el("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0;";
    document.body.append(ta);
    ta.select();
    try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
    ta.remove();
  }
  if (icon) { // 图标按钮：反馈走图标切换（成功打勾变绿），不动文字
    btn.innerHTML = iconSvg(ok ? "check" : "x");
    btn.classList.toggle("copied", ok);
    setTimeout(() => { btn.innerHTML = iconSvg("copy"); btn.classList.remove("copied"); }, 1200);
    return;
  }
  btn.textContent = ok ? t("ss.traceCopied") : t("ss.traceCopyFailed");
  setTimeout(() => { btn.textContent = t(restoreKey); }, 1200);
}

function renderSessionDetail(data) {
  const s = data.session || {};
  state.lastDetail = data;
  const detail = $("session-detail");
  detail.className = "session-detail";
  detail.replaceChildren();

  const header = el("div", "detail-header");
  header.append(el("div", "detail-title", s.title || s.ref));
  const badges = el("div", "session-meta");
  const badge = el("span", "tool-tag lg");
  const badgeDot = el("span", "filter-dot");
  badgeDot.style.backgroundColor = toolColor(s.tool);
  badge.append(badgeDot, el("span", "", s.tool));
  badges.append(badge);
  if (s.model) badges.append(el("span", "meta-item", s.model));
  if (s.branch) badges.append(el("span", "meta-item", `⑂ ${s.branch}`));
  header.append(badges);
  detail.append(header);

  const grid = el("div", "meta-grid");
  grid.append(metaCell("Ref", s.ref));
  grid.append(metaCell(t("ss.metaProject"), basename(s.project)));
  grid.append(metaCell(t("ss.metaUpdated"), fmtDate(s.updated_at)));
  grid.append(metaCell(t("ss.metaMessages"), s.message_count));
  grid.append(metaCell(t("ss.metaBackend"), s.backend));
  detail.append(grid);

  const actions = el("div", "detail-actions");
  const handoffBtn = el("button", "btn", t("ss.handoffBtn"));
  handoffBtn.addEventListener("click", async () => {
    handoffBtn.disabled = true;
    handoffBtn.textContent = t("ss.handoffBusy");
    try {
      const text = await invoke("halter_sessions_context", {
        ref: s.ref,
        project: s.project || state.projectFilter || ".",
        tail: 40,
        machine: state.machine || undefined,
      });
      let block = detail.querySelector(".handoff-block");
      if (!block) {
        block = el("div", "handoff-block");
        detail.append(block);
      }
      block.replaceChildren();
      block.append(el("h2", "", t("ss.handoffTitle")));
      const pre = el("pre", "transcript");
      pre.textContent = text;
      block.append(pre);
    } catch (err) {
      const box = el("div", "error-box");
      box.textContent = errorDetail(err);
      detail.append(box);
    } finally {
      handoffBtn.disabled = false;
      handoffBtn.textContent = t("ss.handoffBtn");
    }
  });
  actions.append(handoffBtn);

  const copyBtn = el("button", "btn", t("ss.traceCopy"));
  copyBtn.addEventListener("click", () => copyToClipboard(data.transcript || "", copyBtn, "ss.traceCopy"));
  actions.append(copyBtn);

  const spacer = el("span", "actions-spacer");
  actions.append(spacer);
  const modeToggle = el("div", "view-toggle");
  for (const [mode, key] of [["chat", "ss.traceViewChat"], ["raw", "ss.traceViewRaw"]]) {
    const btn = el("button", `toggle-btn${state.detailMode === mode ? " active" : ""}`, t(key));
    btn.addEventListener("click", () => setDetailMode(mode));
    modeToggle.append(btn);
  }
  actions.append(modeToggle);
  detail.append(actions);

  detail.append(el("h2", "", t("ss.recentTranscript")));
  const text = data.transcript || "";
  if (state.detailMode === "raw") {
    const pre = el("pre", "transcript");
    pre.textContent = text || t("ss.noTranscript");
    detail.append(pre);
    return;
  }
  const messages = parseTranscript(text);
  if (!messages.length) {
    detail.append(el("div", "loading", t("ss.noTranscript")));
    return;
  }
  renderTraceStream(detail, messages);
}

// 刷新会重扫列表，弹层里的详情会变陈旧，一并收起
$("btn-refresh-sessions").addEventListener("click", () => {
  closeSessionModal();
  loadSessionsView();
});
$("session-modal-close").addEventListener("click", closeSessionModal);
$("session-modal").querySelector(".session-modal-backdrop").addEventListener("click", closeSessionModal);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("session-modal").classList.contains("hidden")) closeSessionModal();
});

/* ---------- session search (follows project + harness filters) ---------- */

async function runSearch() {
  const requestId = ++searchRequestId;
  const query = $("search-input").value.trim();
  if (!query) return;
  state.searchQuery = query;
  const list = $("sessions-list");
  list.replaceChildren(el("div", "loading", t("ss.searching")));
  const allProjects = state.projectFilter === null;
  let data;
  try {
    data = await invoke("halter_sessions_search", {
      query,
      project: projectArg(),
      limit: 50,
      allProjects,
      machine: state.machine || undefined,
    });
  } catch (err) {
    if (requestId !== searchRequestId) return;
    list.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = errorDetail(err);
    list.append(box);
    return;
  }
  if (requestId !== searchRequestId) return;
  const allHits = data.hits || [];
  const hits = applyToolFilter(allHits);
  updateSessionCount(hits.length, allHits.length, t("ss.searchSuffix", { q: query }));
  $("session-sort").textContent = t("ss.sortRelevance");
  list.className = "sessions-list";
  list.replaceChildren();
  if (!hits.length) {
    list.append(el("div", "loading", t("ss.noSearchHit", { q: query })));
    return;
  }
  for (const hit of hits) {
    const item = sessionCard(hit.session);
    const snippet = el("div", "session-snippet", (hit.snippet || "").replace(/\s+/g, " "));
    item.append(snippet);
    list.append(item);
  }
}

$("search-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runSearch();
});

/* ---------------- 全量同步（原 Sync 面板并入矩阵工具栏） ---------------- */

function fullSyncLayers() {
  // 矩阵层（mods 只盘点不参与同步）+ 可选 sessions（不在矩阵中，用工具栏开关控制）
  const layers = MATRIX_LAYERS.filter((l) => l.sync !== false).map((l) => l.key);
  if ($("matrix-include-sessions").checked) layers.push("sessions");
  return layers;
}

async function runFullSync(apply) {
  const key = `fullsync|${state.machine}`;
  if (syncBusyKeys.has(key)) return;
  syncBusyKeys.add(key);
  const out = $("matrix-sync-output");
  out.classList.remove("hidden");
  out.textContent = apply ? t("mx.syncApplying") : t("mx.syncPlanning");
  $("btn-matrix-preview").disabled = true;
  $("btn-matrix-apply").disabled = true;
  try {
    const result = await invoke("halter_sync", {
      apply,
      layers: fullSyncLayers(),
      ...(state.machine ? { machine: state.machine } : {}),
    });
    const parts = [];
    parts.push(`exit code: ${result.code} (${result.ok ? "ok" : "failed"})`);
    if (result.stdout.trim()) parts.push("--- stdout ---\n" + result.stdout.trim());
    if (result.stderr.trim()) parts.push("--- stderr ---\n" + result.stderr.trim());
    out.textContent = parts.join("\n\n");
  } catch (err) {
    out.textContent = t("mx.syncInvokeFailed") + (typeof err === "string" ? err : JSON.stringify(err, null, 2));
  } finally {
    syncBusyKeys.delete(key);
    $("btn-matrix-preview").disabled = false;
    $("btn-matrix-apply").disabled = false;
    disarmApply();
    if (apply) await loadMatrix(true); // 全量写入后强制重扫，圆点状态更新
  }
}

let applyArmed = false;
let applyTimer = null;

function disarmApply() {
  applyArmed = false;
  clearTimeout(applyTimer);
  const btn = $("btn-matrix-apply");
  btn.classList.remove("armed");
  btn.textContent = t("mx.syncApply");
}

$("btn-matrix-preview").addEventListener("click", () => {
  disarmApply();
  runFullSync(false);
});

$("btn-matrix-apply").addEventListener("click", () => {
  const btn = $("btn-matrix-apply");
  if (!applyArmed) {
    applyArmed = true;
    btn.classList.add("armed");
    btn.textContent = t("mx.applyArmed");
    applyTimer = setTimeout(disarmApply, 5000);
    return;
  }
  runFullSync(true);
});

/* ---------------- memory: 全局指令事实源（路径 + 打开 + 差异，只读） ---------------- */

const memoryState = { snap: null };

function memToolState(t) {
  if (t.linked) return { cls: "synced", mark: "●", key: "mem.stateLinked" };
  if (t.present) {
    return t.diff
      ? { cls: "present", mark: "◐", key: "mem.stateDrift" }
      : { cls: "present", mark: "◐", key: "mem.stateLocal" };
  }
  return { cls: "missing", mark: "·", key: "mem.stateMissing" };
}

function renderMemory() {
  const snap = memoryState.snap;
  if (!snap) return;
  const remote = !!state.machine; // 远端路径无法用本机 opener 打开
  const lib = snap.library;
  $("mem-library-path").textContent = lib.path;
  const badge = $("mem-library-badge");
  badge.textContent = lib.exists ? t("mem.badgeOk") : t("mem.badgeMissing");
  badge.classList.toggle("warn", !lib.exists);
  $("btn-memory-open").classList.toggle("hidden", !lib.exists || remote);
  $("mem-content").classList.toggle("hidden", !lib.exists);
  $("memory-editor").textContent = lib.content ?? "";
  const tools = $("mem-tools");
  tools.replaceChildren();
  for (const item of snap.tools) {
    const card = el("div", "mem-tool");
    const head = el("div", "mem-tool-head");
    const st = memToolState(item);
    // 主标题 = Harness 名（第一视觉），记忆文件名（CLAUDE.md…）做成徽章
    head.append(el("span", `legend-dot ${st.cls}`, st.mark));
    head.append(el("span", "mem-tool-display", item.display));
    head.append(el("span", "mem-tool-file", item.path.split("/").pop()));
    head.append(el("span", "toolbar-spacer"));
    if (item.present && !remote) {
      const openBtn = el("button", "btn btn-sm", t("mem.open"));
      openBtn.addEventListener("click", () => {
        invoke("open_path", { path: item.path })
          .catch((err) => showError($("memory-error"), err));
      });
      head.append(openBtn);
    }
    card.append(head);
    card.append(el("div", "mem-tool-meta dim", `${item.path} · ${t(st.key)} · ${item.size}B`));
    if (item.diff) {
      const details = el("details", "mem-diff");
      details.append(el("summary", "", t("mem.diffSummary")));
      details.append(el("pre", "mem-diff-pre", item.diff));
      card.append(details);
    } else if (item.present && !item.linked) {
      card.append(el("div", "mem-tool-meta dim", t("mem.identical")));
    }
    tools.append(card);
  }
}

async function loadMemory() {
  $("memory-error").classList.add("hidden");
  $("memory-loading").classList.remove("hidden");
  $("memory-content").classList.add("hidden");
  try {
    memoryState.snap = await invoke("halter_memory_show", {
      machine: state.machine || undefined,
    });
  } catch (err) {
    $("memory-loading").classList.add("hidden");
    showError($("memory-error"), err);
    return;
  }
  $("memory-loading").classList.add("hidden");
  renderMemory();
  $("memory-content").classList.remove("hidden");
}

$("btn-memory-open").addEventListener("click", () => {
  const p = memoryState.snap?.library.path;
  if (p) invoke("open_path", { path: p }).catch((err) => showError($("memory-error"), err));
});

$("btn-memory-refresh").addEventListener("click", () => loadMemory());

/* ---------------- 机器：全局作用域选择器 ---------------- */

const OS_LABEL = { wsl: "WSL", macos: "macOS", linux: "Linux", windows: "Windows" };

async function loadMachines() {
  try {
    const data = await invoke("halter_machines_list");
    state.machines = Array.isArray(data.machines) ? data.machines : [];
    state.machinesLocal = data.local || null;
  } catch {
    state.machines = []; // 旧 sidecar 无此命令时静默退化为单机视图
    state.machinesLocal = null;
  }
  state.machinesLoaded = true;
  // 当前选中的机器可能已被删除（local 条目不可选，等同已删除）
  if (state.machine && !selectableMachines().some((m) => m.name === state.machine)) {
    state.machine = "";
  }
  renderMachineSelect();
  probeMissingHostNames();
}

// 可切换的远程机器：回环条目即本机，与「● 本机」选项重复，折叠不显示
const selectableMachines = () => state.machines.filter((m) => !m.local);

// 后台补探测：显示名缺 host_name 的机器跑一次 machines test（成功即写入后端缓存），
// 全部完成后重拉列表刷新显示名；每台机器每次会话只探测一次，失败保持注册名。
const machinesProbed = new Set();
async function probeMissingHostNames() {
  const pending = selectableMachines()
    .filter((m) => !m.host_name && !machinesProbed.has(m.name));
  if (!pending.length) return;
  pending.forEach((m) => machinesProbed.add(m.name));
  const settled = await Promise.allSettled(
    pending.map((m) => invoke("halter_machines_test", { name: m.name })));
  if (settled.some((r) => r.status === "fulfilled")) await loadMachines();
}

function renderMachineSelect() {
  const sel = $("global-machine-select");
  if (!sel) return;
  sel.replaceChildren();
  const loc = state.machinesLocal;
  const localOpt = document.createElement("option");
  localOpt.value = "";
  localOpt.text = loc?.host_name || t("mx.machineLocal");
  localOpt.dataset.sub = loc
    ? t("mx.machineLocalSub", { os: OS_LABEL[loc.os] || loc.os || "" })
    : "";
  sel.append(localOpt);
  for (const m of selectableMachines()) {
    const o = document.createElement("option");
    o.value = m.name;
    o.text = m.host_name || m.name;
    o.dataset.sub = m.host_name ? m.name : "";
    o.title = `${m.user ? `${m.user}@` : ""}${m.host}:${m.port || 22}`;
    sel.append(o);
  }
  sel.value = state.machine;
  sel.classList.toggle("has-remote", selectableMachines().length > 0);
  syncDD(sel);
}

// 切换机器作用域：所有视图的数据都在被切机器之下，快照类缓存全部作废
// （scan 缓存按机器键保留，切回本机/远端无需重扫），然后重载当前视图。
// 不设相等守卫：删除当前机器时 loadMachines 已把 state.machine 复位，
// 这里仍需走完整失效路径。
function setMachine(name) {
  state.machine = name;
  localStorage.setItem("halter-machine", name);
  state.expandedFamilies.clear();
  state.sessionsLoaded = false;
  state.sessions = [];
  state.projects = [];
  state.projectFilter = null;
  state.toolFilters.clear();
  memoryState.snap = null;
  providersState.data = null;
  updatesState.data = null;
  renderMachineSelect();
  reloadActiveView();
}

function reloadActiveView() {
  const active = document.querySelector(".nav-item.active");
  const view = active ? active.dataset.view : "overview";
  if (view === "overview") loadOverview();
  else if (view === "matrix") loadMatrix();
  else if (view === "sessions") loadSessionsView();
  else if (view === "memory") loadMemory();
  else if (view === "providers") loadProviders();
  else if (view === "updates") loadUpdates();
}

$("global-machine-select").addEventListener("change", (e) => {
  setMachine(e.target.value);
});

/* ---------------- settings：语言 / 主题 / 关于 ---------------- */

$("lang-zh").addEventListener("click", () => setLang("zh"));
$("lang-en").addEventListener("click", () => setLang("en"));

// 主题三档：跟随系统 / 深色 / 浅色，持久化到 localStorage("halter-theme")；
// index.html <head> 里有同逻辑的首帧预置，避免浅色用户加载时闪暗色底
const THEME_MODES = ["auto", "dark", "light"];
const themeMedia = window.matchMedia("(prefers-color-scheme: light)");

function themeMode() {
  let saved = null;
  try { saved = localStorage.getItem("halter-theme"); } catch (e) { /* ignore */ }
  return THEME_MODES.includes(saved) ? saved : "auto";
}

function applyTheme(mode = themeMode()) {
  document.documentElement.dataset.theme =
    mode === "auto" ? (themeMedia.matches ? "light" : "dark") : mode;
  for (const m of THEME_MODES) {
    const btn = document.getElementById(`theme-${m}`);
    if (btn) btn.classList.toggle("active", m === mode);
  }
}

function setTheme(mode) {
  if (!THEME_MODES.includes(mode) || mode === themeMode()) return;
  try { localStorage.setItem("halter-theme", mode); } catch (e) { /* ignore */ }
  applyTheme(mode);
}

for (const mode of THEME_MODES) {
  document.getElementById(`theme-${mode}`).addEventListener("click", () => setTheme(mode));
}
themeMedia.addEventListener("change", () => {
  if (themeMode() === "auto") applyTheme("auto");
});
applyTheme();

// 设置浮层开合：按钮切换；点面板外或 Esc 收起
$("btn-settings").addEventListener("click", () => {
  $("settings-panel").classList.toggle("hidden");
});
document.addEventListener("click", (e) => {
  const panel = $("settings-panel");
  if (panel.classList.contains("hidden")) return;
  if (panel.contains(e.target) || $("btn-settings").contains(e.target)) return;
  panel.classList.add("hidden");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("settings-panel").classList.add("hidden");
});

// 静态文案由 i18n.js 的 applyI18n() 刷新；这里重渲染各视图的动态文案。
// armed 的 Apply 按钮文案会被 applyI18n 覆盖，需按当前状态重设。
document.addEventListener("halter:langchange", () => {
  if (applyArmed) $("btn-matrix-apply").textContent = t("mx.applyArmed");
  if (scanCacheOf()) renderOverview(scanCacheOf());
  renderMachineSelect();
  if (scanCacheOf()) {
    renderMatrixLayerTabs();
    renderMatrix(scanCacheOf());
  }
  if (state.sessionsLoaded) {
    renderProjectInput();
    renderToolFilter();
    renderSessions(state.sessions);
  }
  if (memoryState.snap) renderMemory();
  if (providersState.data) renderProviders();
  if (updatesState.data) renderUpdates();
});

/* ---------------- providers: 模型供应商切换（claude / codex） ---------------- */

const PV_TOOLS = [
  { key: "claude", labelKey: "pv.tabClaude" },
  { key: "codex", labelKey: "pv.tabCodex" },
];

const providersState = { tool: "claude", data: null, removeArmed: null, removeTimer: null,
  form: { mode: "add", id: null, tool: null } };
const pvBusy = new Set();

async function loadProviders(force = false) {
  if (providersState.data && !force) {
    renderProviders();
    return;
  }
  $("providers-error").classList.add("hidden");
  $("providers-loading").classList.remove("hidden");
  $("providers-content").classList.add("hidden");
  try {
    providersState.data = await invoke("halter_providers_list", {
      machine: state.machine || undefined,
    });
  } catch (err) {
    showError($("providers-error"), err);
    return;
  } finally {
    $("providers-loading").classList.add("hidden");
  }
  $("providers-content").classList.remove("hidden");
  renderProviders();
}

function renderProviders() {
  const data = providersState.data;
  if (!data) return;

  // 预设下拉（数据来自 providers list --json 的 presets 字段，单一事实源）
  const presetSel = $("pv-preset");
  const chosenPreset = presetSel.value;
  const customOpt = el("option", "", t("pv.formPresetCustom"));
  customOpt.value = "";   // 缺 value 时 select.value 返回显示文本，会误当预设名
  presetSel.replaceChildren(customOpt);
  for (const p of data.presets || []) {
    const opt = el("option", "", p.label ? `${p.name} · ${p.label}` : p.name);
    opt.value = p.name;
    presetSel.append(opt);
  }
  if (chosenPreset) { presetSel.value = chosenPreset; syncDD(presetSel); }

  const tabs = $("providers-tool-tabs");
  tabs.replaceChildren();
  for (const tool of PV_TOOLS) {
    const btn = el(
      "button",
      `layer-tab${providersState.tool === tool.key ? " active" : ""}`,
      t(tool.labelKey),
    );
    btn.addEventListener("click", () => {
      providersState.tool = tool.key;
      renderProviders();
    });
    tabs.append(btn);
  }

  const tool = providersState.tool;
  const cur = data.current[tool] || { status: "official", provider: "", base_url: "", model: "" };

  // 顶部当前卡仅在 external 态渲染：清单之外的配置在列表无行可表达，需要它可见；
  // halter / official 态的激活信息由列表行的 active 徽标表达，不再顶部重复
  const currentHost = $("providers-current");
  if (cur.status === "external" && cur.base_url) {
    const statusEl = el("span", "pv-status external", t("pv.statusExternal"));
    const curTitle = el("div", "pv-cur-title");
    curTitle.append(
      el("strong", "", cur.provider || t("pv.externalName")),
      statusEl,
    );
    const curMeta = el("div", "dim pv-row-endpoint");
    curMeta.append(document.createTextNode(cur.base_url));
    if (cur.model) curMeta.append(el("span", "pv-chip", cur.model));
    const currentCard = el("div", "pv-current");
    currentCard.append(el("div", "pv-cur-label", t("pv.currentLabel")), curTitle, curMeta);
    if (cur.detail) {
      currentCard.append(el("div", "pv-external-hint", t("pv.externalHint")));
    }
    // 双击当前卡进入收编表单——带实况端点/模型/扁平 env（档位映射等一并带入），
    // 起个 id 保存即收编进清单（卡片即入口；token 未填时自动继承实况）
    currentCard.classList.add("adoptable");
    currentCard.title = t("pv.externalHint");
    currentCard.addEventListener("dblclick", () => openProviderForm("external", {
      [tool]: { base_url: cur.base_url, model: cur.model || "", env: cur.env || {} },
    }));
    currentHost.replaceChildren(currentCard);
  } else {
    currentHost.replaceChildren();
  }

  const list = $("providers-list");
  list.replaceChildren();
  const rows = (data.providers || []).filter((p) => p.builtin || p.tools.includes(tool));
  if (!rows.length) {
    const empty = el("div", "empty-state");
    empty.append(el("div", "empty-sub", t("pv.empty")));
    list.append(empty);
  }
  for (const p of rows) {
    list.append(renderProviderRow(p, tool, cur));
  }
}

function renderProviderRow(p, tool, cur) {
  const isActive = cur.status === "halter" && cur.provider === p.id;
  const row = el("div", `pv-row${isActive ? " active" : ""}`);

  const info = el("div", "pv-row-info");
  const nameLine = el("div", "pv-row-name");
  nameLine.append(el("strong", "", p.id));
  if (p.label && p.label !== p.id) nameLine.append(el("span", "dim", p.label));
  if (p.builtin) nameLine.append(el("span", "pv-badge", t("pv.builtin")));
  if (isActive) nameLine.append(el("span", "pv-badge current", t("pv.current")));
  // 端点与模型分列：URL 等宽、模型独立 chip，不再挤一行
  const endpointLine = el("div", "dim pv-row-endpoint");
  const endpoint = (p[tool] && p[tool].base_url) || t("pv.officialEndpoint");
  endpointLine.append(document.createTextNode(endpoint));
  if (p[tool] && p[tool].model) endpointLine.append(el("span", "pv-chip", p[tool].model));
  info.append(nameLine, endpointLine);
  row.append(info);

  const actions = el("div", "pv-row-actions");
  if (!p.builtin) {
    const editBtn = el("button", "btn btn-sm", t("pv.edit"));
    editBtn.addEventListener("click", () => openProviderForm("edit", p));
    actions.append(editBtn);
  }
  if (!isActive) {
    const switchBtn = el("button", "btn btn-sm", t("pv.switch"));
    switchBtn.addEventListener("click", () => switchProviderRow(p.id, tool));
    actions.append(switchBtn);
  }
  if (!p.builtin) {
    const removeBtn = el("button", "btn btn-sm danger", t("pv.remove"));
    removeBtn.addEventListener("click", () => removeProviderRow(removeBtn, p.id));
    actions.append(removeBtn);
  }
  row.append(actions);
  return row;
}

async function switchProviderRow(id, tool) {
  const key = `pv-switch|${tool}|${id}`;
  if (pvBusy.has(key)) return;
  pvBusy.add(key);
  let result = null;
  let invokeErr = null;
  try {
    result = await invoke("halter_providers_switch", {
      id,
      tool,
      machine: state.machine || undefined,
    });
  } catch (err) {
    invokeErr = err;
  }
  pvBusy.delete(key);
  if (invokeErr) {
    showMatrixToast(t("pv.switchFail", { id }), errorDetail(invokeErr), true);
    return;
  }
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(
    t(result.ok ? "pv.switchDone" : "pv.switchFail", { id }),
    body || t("pv.noop"),
    !result.ok,
  );
  await loadProviders(true);
}

function removeProviderRow(btn, id) {
  if (providersState.removeArmed !== id) {
    disarmPvRemove();
    providersState.removeArmed = id;
    btn.classList.add("armed");
    btn.textContent = t("pv.removeArmed");
    providersState.removeTimer = setTimeout(disarmPvRemove, 5000);
    return;
  }
  disarmPvRemove();
  void (async () => {
    const key = `pv-remove|${id}`;
    if (pvBusy.has(key)) return;
    pvBusy.add(key);
    let result = null;
    try {
      result = await invoke("halter_providers_remove", {
        id,
        machine: state.machine || undefined,
      });
    } catch (err) {
      pvBusy.delete(key);
      showMatrixToast(t("pv.removeFail", { id }), errorDetail(err), true);
      return;
    }
    pvBusy.delete(key);
    const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
    showMatrixToast(
      t(result.ok ? "pv.removeDone" : "pv.removeFail", { id }),
      body || t("pv.noop"),
      !result.ok,
    );
    await loadProviders(true);
  })();
}

function disarmPvRemove() {
  providersState.removeArmed = null;
  clearTimeout(providersState.removeTimer);
  document.querySelectorAll("#providers-list .btn.danger.armed").forEach((b) => {
    b.classList.remove("armed");
    b.textContent = t("pv.remove");
  });
}

$("btn-refresh-providers").addEventListener("click", () => loadProviders(true));

$("btn-providers-add").addEventListener("click", () => {
  const panel = $("providers-add-panel");
  // 新增模式已展开时按钮起开合作用；编辑/外部收编模式则切回新增表单
  if (!panel.classList.contains("hidden") && providersState.form.mode === "add") {
    panel.classList.add("hidden");
    return;
  }
  openProviderForm("add");
});
$("btn-providers-form-close").addEventListener("click", () => {
  $("providers-add-panel").classList.add("hidden");
});

// 配置 JSON 编辑器（对标 CC Switch 的「配置JSON」）：整块定义即编辑内容，保存时
// 整体替换——JSON 里没写的键就是没有。claude 侧即 settings.json env 的扁平托管键
// （与 ccswitch / 手工配置同构，可整块粘贴迁移）；token 刻意不在 JSON 里（防进 argv）。
function pvDefTemplate(tool) {
  return tool === "claude"
    ? { ANTHROPIC_BASE_URL: "" }
    : { base_url: "", model: "", wire_api: "responses" };
}

// list --json 的工具块行 / external 实况 → def 对象（空值键不出现，与 CLI 校验口径一致）。
// claude：base_url/model 映射回 ANTHROPIC_BASE_URL / ANTHROPIC_MODEL，env 扁平摊开
//（external 的 cur.env 已含这两键，覆盖即等价）
function pvDefFromSection(sec = {}) {
  const def = {};
  if (providersState.tool === "claude") {
    if (sec.base_url) def.ANTHROPIC_BASE_URL = sec.base_url;
    if (sec.model) def.ANTHROPIC_MODEL = sec.model;
    Object.assign(def, sec.env || {});
    return def;
  }
  if (sec.base_url) def.base_url = sec.base_url;
  if (sec.model) def.model = sec.model;
  if (sec.wire_api) def.wire_api = sec.wire_api;
  if (sec.reasoning_effort) def.reasoning_effort = sec.reasoning_effort;
  if (sec.context_window) def.context_window = sec.context_window;
  return def;
}

function pvSetDef(obj) {
  $("pv-def").value = JSON.stringify(obj, null, 2);
  pvValidateDef();
}

// 实时校验：红边框 + 行内错误；JSON 合法且为对象时返回解析结果，否则 null
function pvValidateDef() {
  const ta = $("pv-def");
  const errEl = $("pv-def-error");
  let parsed = null;
  try {
    parsed = JSON.parse(ta.value);
  } catch (err) {
    ta.classList.add("invalid");
    errEl.textContent = `${t("pv.defInvalid")} ${err.message}`;
    errEl.classList.remove("hidden");
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    ta.classList.add("invalid");
    errEl.textContent = t("pv.defInvalid");
    errEl.classList.remove("hidden");
    return null;
  }
  ta.classList.remove("invalid");
  errEl.classList.add("hidden");
  return parsed;
}

$("pv-def").addEventListener("input", pvValidateDef);
$("pv-def-format").addEventListener("click", () => {
  const parsed = pvValidateDef();
  if (parsed) $("pv-def").value = JSON.stringify(parsed, null, 2);
});

// add / edit / external 共用一张表单：edit 锁定工具/预设并预填现有配置 JSON（token 留空 = 保持不变）；
// external 带入外部实况的端点/模型，预设开放改选（保存 = 新增收编），工具锁定为实况所属页签
function openProviderForm(mode, entry = null) {
  const editing = mode === "edit";
  const external = mode === "external";
  providersState.form = editing
    ? { mode, id: entry.id, tool: providersState.tool }
    : { mode, id: null, tool: null };
  $("provider-add-form").reset();
  const title = $("pv-form-title");
  if (editing) {
    title.removeAttribute("data-i18n");   // 编辑标题含 id 参数，不走 data-i18n 静态应用
    title.textContent = t("pv.formTitleEdit", { id: entry.id, tool: providersState.tool });
  } else {
    title.setAttribute("data-i18n", external ? "pv.formTitleExternal" : "pv.formTitle");
    title.textContent = t(external ? "pv.formTitleExternal" : "pv.formTitle");
  }
  const hint = $("pv-form-hint");
  hint.dataset.i18n = editing ? "pv.formHintEdit"
    : external ? "pv.formHintExternal" : "pv.formHint";
  hint.textContent = t(hint.dataset.i18n);
  $("pv-preset").disabled = editing;
  $("pv-tool").disabled = editing || external;
  // 编辑模式解锁 ID（改名走 providers rename，token 键随迁）；add/external 由预设/端点自动推导
  $("pv-id").classList.toggle("hidden", !editing);
  $("pv-id").value = editing ? entry.id : "";
  const tokenInput = $("pv-token");
  tokenInput.dataset.i18nPlaceholder = editing ? "pv.formTokenKeep" : "pv.formToken";
  tokenInput.placeholder = t(tokenInput.dataset.i18nPlaceholder);
  $("pv-tool").value = providersState.tool;
  syncDD($("pv-tool"));
  if (editing || external) {
    // 端点精确命中内置预设 → 预选之：external 一看便知是哪家厂商（仍可改选），
    // editing 下拉已锁定、纯展示厂商归属；add 模式才由用户自选
    const url = (entry[providersState.tool] || {}).base_url || "";
    const hit = ((providersState.data && providersState.data.presets) || [])
      .find((p) => p.urls && p.urls[providersState.tool] === url);
    $("pv-preset").value = hit ? hit.name : "";
    syncDD($("pv-preset"));
  }
  if (editing || external) {
    pvSetDef(pvDefFromSection(entry[providersState.tool] || {}));
    if (editing) {
      $("pv-label").value = entry.label && entry.label !== entry.id ? entry.label : "";
    }
    $("pv-def").focus();
  } else {
    pvSetDef(pvDefTemplate(providersState.tool));
    $("pv-preset").focus();
  }
  $("providers-add-panel").classList.remove("hidden");
}

// 新增（含收编）的供应商标识自动生成：选了预设用预设名，否则取端点域名；
// 与清单现有条目撞名则 -2、-3 递增（保存后列表行名与 CLI switch 命令都用它）
function pvDeriveId(tool, def) {
  const preset = $("pv-preset").value;
  let base = preset;
  if (!base) {
    const url = tool === "claude" ? def.ANTHROPIC_BASE_URL : def.base_url;
    try {
      base = new URL(url).hostname.replace(/^www\./, "");
    } catch {
      base = "provider";
    }
  }
  const taken = new Set((providersState.data && providersState.data.providers || [])
    .map((p) => p.id));
  let id = base;
  for (let n = 2; taken.has(id); n += 1) id = `${base}-${n}`;
  return id;
}

// 新增模式切换工具 → 换对应模板（编辑/收编模式工具锁定）
$("pv-tool").addEventListener("change", () => {
  if (providersState.form.mode === "add") pvSetDef(pvDefTemplate($("pv-tool").value));
});

// 选中预设 → 端点合入配置 JSON（其余键保留，JSON 有语法错则先修复再选）；
// 预设不含当前工具时切到它支持的第一个工具并重置为新工具的模板
$("pv-preset").addEventListener("change", () => {
  const p = ((providersState.data && providersState.data.presets) || [])
    .find((x) => x.name === $("pv-preset").value);
  if (!p) return;
  let tool = $("pv-tool").value;
  let url = p.urls && p.urls[tool];
  if (!url) {
    tool = (p.tools || [])[0];
    if (!tool) return;
    $("pv-tool").value = tool;
    syncDD($("pv-tool"));
    $("pv-def").value = JSON.stringify(pvDefTemplate(tool), null, 2);
    url = p.urls[tool];
  }
  const def = pvValidateDef();
  if (!def) return;
  if (tool === "claude") def.ANTHROPIC_BASE_URL = url;
  else def.base_url = url;
  pvSetDef(def);
  if (!$("pv-label").value.trim()) $("pv-label").value = p.label || p.name;
});

$("provider-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const editing = providersState.form.mode === "edit";
  const tool = editing ? providersState.form.tool : $("pv-tool").value;
  const def = pvValidateDef();
  if (!def) {
    $("pv-def").focus();
    return;
  }
  const id = editing ? providersState.form.id : pvDeriveId(tool, def);
  const endpointKey = tool === "claude" ? "ANTHROPIC_BASE_URL" : "base_url";
  const errEl = $("pv-def-error");
  if (!def[endpointKey]) {
    $("pv-def").classList.add("invalid");
    errEl.textContent = t("pv.defNoBaseUrl");
    errEl.classList.remove("hidden");
    $("pv-def").focus();
    return;
  }
  // 编辑改了 ID → 先 rename（token 键随迁），后续 edit 用新 id；rename 失败即中断
  let finalId = id;
  if (editing) {
    const wanted = $("pv-id").value.trim();
    if (wanted && wanted !== id) {
      let rn = null;
      try {
        rn = await invoke("halter_providers_rename", {
          old: id, new: wanted, machine: state.machine || undefined,
        });
      } catch (err) {
        showMatrixToast(t("pv.renameFail", { id: wanted }), errorDetail(err), true);
        return;
      }
      if (!rn.ok) {
        showMatrixToast(t("pv.renameFail", { id: wanted }), rn.stderr || rn.stdout || "", true);
        return;
      }
      finalId = wanted;
    }
  }
  // 整块粘贴的 JSON 可含 token（CLI 侧 --def 走 argv 会泄密故拒绝；UI 无此约束）：
  // 保存前剥离出 def，经 stdin 通道进 secrets.toml——表单 token 字段非空时优先
  let token = $("pv-token").value || null;
  if (tool === "claude" && def.ANTHROPIC_AUTH_TOKEN) {
    if (!token) token = def.ANTHROPIC_AUTH_TOKEN;
    delete def.ANTHROPIC_AUTH_TOKEN;
  }
  const args = {
    id: finalId,
    tool,
    def: JSON.stringify(def),
    label: $("pv-label").value.trim() || null,   // 空 = 保持不变 / 缺省
    token,                                       // 空 = 保持不变 / 不存
    machine: state.machine || undefined,
  };
  const doneKey = editing ? "pv.editDone" : "pv.addDone";
  const failKey = editing ? "pv.editFail" : "pv.addFail";
  let result = null;
  try {
    result = await invoke(editing ? "halter_providers_edit" : "halter_providers_add", args);
  } catch (err) {
    showMatrixToast(t(failKey, { id: finalId }), errorDetail(err), true);
    return;
  }
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(
    t(result.ok ? doneKey : failKey, { id: finalId }),
    body || t("pv.noop"),
    !result.ok,
  );
  if (result.ok) {
    e.target.reset();
    $("providers-add-panel").classList.add("hidden");
    providersState.tool = tool;
  }
  await loadProviders(true);
});

$("btn-providers-adopt").addEventListener("click", async () => {
  let result = null;
  try {
    result = await invoke("halter_providers_adopt", {
      machine: state.machine || undefined,
    });
  } catch (err) {
    showMatrixToast(t("pv.adoptFail"), errorDetail(err), true);
    return;
  }
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(t("pv.adoptDone"), body || t("pv.noop"), !result.ok);
  await loadProviders(true);
});

/* ---------------- updates: harness 升级面板（npm 渠道，只读状态） ---------------- */

const updatesState = { data: null };

function upStateMeta(upState) {
  if (upState === "latest") return { cls: "ok", key: "up.stateLatest" };
  if (upState === "upgradeable") return { cls: "upgradeable", key: "up.stateUpgradeable" };
  if (upState === "ahead") return { cls: "ahead", key: "up.stateAhead", tip: "up.stateAheadTip" };
  if (upState === "missing") return { cls: "missing", key: "up.stateMissing" };
  return { cls: "unknown", key: "up.stateUnknown" };
}

function upRow(labelKey, value) {
  const row = el("div", "up-row");
  row.append(el("span", "up-row-label", t(labelKey)));
  row.append(el("span", "up-row-value", value));
  return row;
}

async function loadUpdates(force = false) {
  if (updatesState.data && !force) {
    renderUpdates();
    return;
  }
  $("updates-error").classList.add("hidden");
  $("updates-loading").classList.remove("hidden");
  $("updates-content").classList.add("hidden");
  try {
    updatesState.data = await invoke("halter_update_status", {
      machine: state.machine || undefined,
    });
  } catch (err) {
    showError($("updates-error"), err);
    return;
  } finally {
    $("updates-loading").classList.add("hidden");
  }
  $("updates-content").classList.remove("hidden");
  renderUpdates();
}

function renderUpdates() {
  const data = updatesState.data;
  if (!data) return;
  $("updates-npm-warning").classList.toggle("hidden", !!data.npm_available);
  const grid = $("updates-grid");
  grid.replaceChildren();
  for (const tool of data.tools || []) {
    grid.append(renderUpdateCard(tool));
  }
}

function renderUpdateCard(tool) {
  const meta = upStateMeta(tool.state);
  // 整卡红绿着色表达状态（绿=已最新 红=待更新/超前）；名称独占整行，npm 包名在第二行
  const card = el("div", `up-card state-${tool.state}`);

  const header = el("div", "up-card-header");
  const title = el("div", "up-title");
  const name = el("div", "up-name");
  const dot = el("span", "filter-dot");
  dot.style.backgroundColor = toolColor(tool.key);
  name.append(dot, el("span", "up-display", tool.display || tool.key));
  title.append(name, el("div", "up-key", tool.npm_package || tool.key));
  const stateBadge = el("span", `up-state ${meta.cls}`, t(meta.key));
  stateBadge.title = t(meta.tip || meta.key);
  header.append(title, stateBadge);
  card.append(header);

  const rows = el("div", "up-rows");
  rows.append(upRow("up.platform", tool.platform || updatesState.data.platform || "-"));
  rows.append(upRow("up.current", tool.current || "—"));
  rows.append(upRow("up.latest", tool.latest || "—"));
  card.append(rows);

  // 手动安装 / 升级命令：逐条独立成行（官方脚本与 npm 分开），命令单行省略（悬停看全文，
  // 复制始终是完整命令），行尾是图标复制按钮；卡内按钮仅此用途，升级动作仍在 CLI
  if (Array.isArray(tool.install) && tool.install.length) {
    const install = el("div", "up-install");
    install.append(el("span", "up-install-label", t("up.install")));
    const list = el("div", "up-install-list");
    for (const cmd of tool.install) {
      const item = el("div", "up-install-item");
      const cmdEl = el("code", "up-install-cmd", cmd);
      cmdEl.title = cmd;
      item.append(cmdEl);
      const copy = el("button", "btn btn-sm up-copy");
      copy.type = "button";
      copy.innerHTML = iconSvg("copy");
      copy.title = t("up.copy");
      copy.setAttribute("aria-label", t("up.copy"));
      copy.addEventListener("click", () => copyToClipboard(cmd, copy, "up.copy", { icon: true }));
      item.append(copy);
      list.append(item);
    }
    install.append(list);
    card.append(install);
  }
  return card;
}

$("btn-refresh-updates").addEventListener("click", () => loadUpdates(true));

/* ---------------- boot ---------------- */

(async function boot() {
  document.querySelectorAll("select").forEach(enhanceSelect);
  try {
    const v = await invoke("halter_version");
    const badge = $("halter-version");
    badge.textContent = `halter ${v.version}`;
    if (v.desktop && v.version && String(v.version) !== String(v.desktop)) {
      badge.classList.add("mismatch");
      badge.title = t("bt.mismatch", { version: v.version, desktop: v.desktop });
    }
  } catch (err) {
    $("halter-version").textContent = t("bt.unavailable");
    // Error 的可枚举属性为空，JSON.stringify 只会得到 "{}"，取 message 才能看到原因
    $("halter-version").title =
      typeof err === "string" ? err : (err?.message ?? JSON.stringify(err));
  }
  // 机器作用域先行：注册表加载 + 恢复上次选择（halter-machine 持久化），
  // 之后所有视图都在所选机器之下加载
  await loadMachines();
  const saved = localStorage.getItem("halter-machine");
  if (saved && selectableMachines().some((m) => m.name === saved)) {
    state.machine = saved;
    renderMachineSelect();
  }
  loadOverview();
})();
