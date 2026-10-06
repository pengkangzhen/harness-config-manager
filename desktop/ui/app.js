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

/* ---------------- global state ---------------- */

const state = {
  scanCache: new Map(),    // machine("" = 本机) -> scan JSON
  machines: [],            // 已注册远程机器（halter machines list）
  machinesLoaded: false,
  machineFilter: "",       // "" = 本机；其他 = machines.toml 里的机器名
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
  });
});

/* ---------------- overview ---------------- */

const LAYERS = [
  ["skills", "layer.skills"],
  ["agents", "layer.agents"],
  ["memory", "layer.memory"],
  ["mcp_servers", "layer.mcp"],
  ["plugins", "layer.plugins"],
  ["hooks", "layer.hooks"],
  ["sessions", "layer.sessions"],
];

async function fetchScan(machine = "", force = false) {
  if (!force && state.scanCache.has(machine)) return state.scanCache.get(machine);
  const data = await invoke("halter_scan", machine ? { machine } : {});
  state.scanCache.set(machine, data);
  return data;
}

// 当前矩阵视图使用的 scan 缓存（"" = 本机）
const scanCacheOf = (machine = state.machineFilter) => state.scanCache.get(machine) || null;

async function loadOverview() {
  $("scan-error").classList.add("hidden");
  $("scan-loading").classList.remove("hidden");
  $("overview-content").classList.add("hidden");
  let data;
  try {
    data = await fetchScan();
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
  state.scanCache.delete("");
  loadOverview();
});

/* ---------------- matrix: harness × layer ---------------- */

const MATRIX_LAYERS = [
  { key: "skills", field: "skills", labelKey: "mlayer.skills" },
  { key: "agents", field: "agents", labelKey: "mlayer.agents" },
  { key: "memory", field: "memory", labelKey: "mlayer.memory" },
  { key: "mcp", field: "mcp_servers", labelKey: "mlayer.mcp" },
  { key: "plugins", field: "plugins", labelKey: "mlayer.plugins" },
  { key: "hooks", field: "hooks", labelKey: "mlayer.hooks" },
];

// 支持跨机器 push/pull 的层（memory/plugins/sessions 不在跨机范围）
const PUSH_LAYERS = ["skills", "agents", "mcp", "hooks"];

function matrixItemName(layer, item) {
  if (layer.key === "plugins") return item.plugin_id;
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
      const linked = ["skills", "agents", "memory"].includes(layer.key) ? !!item.linked : false;
      row.statuses.set(tool.tool, linked ? "synced" : "present");
      row.items.push(item);
    }
  }
  const list = [...rows.entries()].map(([name, r]) => ({
    name,
    statuses: r.statuses,
    items: r.items,
    // 预览用代表条目：优先取带 description 的（各工具同名条目内容一致）
    item: r.items.find((x) => x.description) || r.items[0],
    missing: tools.filter((x) => !r.statuses.has(x.tool)).length,
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
    data = await fetchScan(state.machineFilter, force);
    if (state.machineFilter) {
      // 幽灵行/拉取按钮需要本机 scan 作对照；失败不阻塞远端矩阵展示
      try { await fetchScan("", force); } catch { /* 本机 scan 失败时跳过对照 */ }
    }
  } catch (err) {
    $("matrix-loading").classList.add("hidden");
    showError($("matrix-error"), err);
    return;
  }
  if (!state.machinesLoaded) await loadMachines();
  else renderMachineSelect();
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
  const crossCompare = state.machineFilter && PUSH_LAYERS.includes(layer.key)
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

  const renderRow = (row, cls) => {
    const nameCell = el("div", `mx-name ${cls || ""}`);
    nameCell.title = row.name;
    if (layer.key === "skills") {
      nameCell.removeAttribute("title");
      attachHoverPreview(nameCell, () => skillHoverBody(row));
    }
    nameCell.append(el("span", "", row.name));
    // 远端视图：本机没有的条目给一个「拉取到本机」入口
    if (localNames && !localNames.has(row.name)) {
      const pull = el("button", "mx-pull-btn", "↓");
      pull.title = t("mx.pullHint", { item: row.name, machine: state.machineFilter });
      pull.addEventListener("click", (e) => {
        e.stopPropagation();
        pullRemoteItem(row.name);
      });
      nameCell.append(pull);
    }
    grid.append(nameCell);
    for (const tool of tools) {
      const st = row.statuses.get(tool.tool) || "missing";
      const cell = el("div", `mx-cell ${st}`, CELL_GLYPH[st] || "○");
      cell.title = t("mx.cellTitle", { tool: tool.tool, status: cellStatusText(st) });
      attachCellSync(cell, tool.tool, [row.name], st);
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
      attachCellSync(cell, tool.tool, group.members.map((m) => m.name), st);
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
    const sep = el("div", "mx-ghost-sep", t("mx.ghostSection", { machine: state.machineFilter }));
    sep.style.gridColumn = "1 / -1";
    grid.append(sep);
    for (const name of ghostNames) {
      const nameCell = el("div", "mx-name ghost");
      nameCell.title = t("mx.ghostRowTitle", { item: name, machine: state.machineFilter });
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
  const key = `sync|${state.machineFilter}|${tool}|${names.join(",")}`;
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
      ...(state.machineFilter ? { machine: state.machineFilter } : {}),
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
    result = await pushEntry(state.matrixLayer, item, { to: state.machineFilter });
  } catch (err) {
    cell.classList.remove("syncing");
    showMatrixToast(t("mx.pushFailed", { item, machine: state.machineFilter }), errorDetail(err), true);
    return;
  }
  cell.classList.remove("syncing");
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(
    t(result.ok ? "mx.pushDone" : "mx.pushFailed", { item, machine: state.machineFilter }),
    body || t("mx.syncNoop"),
    !result.ok,
  );
  await loadMatrix(true); // 推送后两端状态都可能变化，强制重扫
}

async function pullRemoteItem(item) {
  let result = null;
  try {
    result = await pushEntry(state.matrixLayer, item, { from: state.machineFilter });
  } catch (err) {
    showMatrixToast(t("mx.pullFailed", { item, machine: state.machineFilter }), errorDetail(err), true);
    return;
  }
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(
    t(result.ok ? "mx.pullDone" : "mx.pullFailed", { item, machine: state.machineFilter }),
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
    data = await invoke("halter_sessions_projects", { project: currentProject() });
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

/* ---------- session detail ---------- */

async function selectSession(session, itemEl) {
  const requestId = ++sessionDetailRequestId;
  document.querySelectorAll(".session-item.selected").forEach((n) => n.classList.remove("selected"));
  if (itemEl) itemEl.classList.add("selected");

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

async function copyToClipboard(text, btn, restoreKey) {
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

$("btn-refresh-sessions").addEventListener("click", loadSessionsView);

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
  // 六个矩阵层 + 可选 sessions（不在矩阵中，用工具栏开关控制）
  const layers = MATRIX_LAYERS.map((l) => l.key);
  if ($("matrix-include-sessions").checked) layers.push("sessions");
  return layers;
}

async function runFullSync(apply) {
  const key = `fullsync|${state.machineFilter}`;
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
      ...(state.machineFilter ? { machine: state.machineFilter } : {}),
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
  const lib = snap.library;
  $("mem-library-path").textContent = lib.path;
  const badge = $("mem-library-badge");
  badge.textContent = lib.exists ? t("mem.badgeOk") : t("mem.badgeMissing");
  badge.classList.toggle("warn", !lib.exists);
  $("btn-memory-open").classList.toggle("hidden", !lib.exists);
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
    if (item.present) {
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
    memoryState.snap = await invoke("halter_memory_show");
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

/* ---------------- 机器：跨机视图的选择器与注册表管理 ---------------- */

async function loadMachines() {
  try {
    const data = await invoke("halter_machines_list");
    state.machines = Array.isArray(data.machines) ? data.machines : [];
  } catch {
    state.machines = []; // 旧 sidecar 无此命令时静默退化为单机视图
  }
  state.machinesLoaded = true;
  // 当前选中的机器可能已被删除
  if (state.machineFilter && !state.machines.some((m) => m.name === state.machineFilter)) {
    state.machineFilter = "";
  }
  renderMachineSelect();
  renderMachinesPanel();
}

function renderMachineSelect() {
  const sel = $("matrix-machine-select");
  if (!sel) return;
  sel.replaceChildren();
  sel.append(new Option(t("mx.machineLocal"), ""));
  for (const m of state.machines) sel.append(new Option(m.name, m.name));
  sel.value = state.machineFilter;
  sel.classList.toggle("has-remote", state.machines.length > 0);
}

$("matrix-machine-select").addEventListener("change", (e) => {
  state.machineFilter = e.target.value;
  state.expandedFamilies.clear();
  loadMatrix();
});

$("btn-matrix-machines").addEventListener("click", () => {
  $("machines-panel").classList.toggle("hidden");
  renderMachinesPanel();
});

$("btn-machines-close").addEventListener("click", () => {
  $("machines-panel").classList.add("hidden");
});

function renderMachinesPanel() {
  const listEl = $("machines-list");
  if (!listEl) return;
  listEl.replaceChildren();
  if (!state.machines.length) {
    listEl.append(el("div", "dim", t("mx.machinesEmpty")));
    return;
  }
  for (const m of state.machines) {
    const row = el("div", "machine-row");
    const info = el("div", "machine-info");
    info.append(el("span", "machine-name", m.name));
    if (m.source === "ssh") {
      // ~/.ssh/config 自动发现：user/port 由 ssh 解析，只展示别名与解析到的账号
      info.append(el("span", "machine-host dim",
        `${m.user ? `${m.user}@` : ""}${m.host}`));
      info.append(el("span", "machine-origin", t("mx.machineFromSsh")));
      row.append(info);
      listEl.append(row);
      continue;
    }
    info.append(el("span", "machine-host dim",
      `${m.user ? `${m.user}@` : ""}${m.host}:${m.port || 22}`));
    row.append(info);
    const rm = el("button", "btn btn-sm", t("mx.machineRemove"));
    rm.addEventListener("click", async () => {
      try {
        await invoke("halter_machines_remove", { name: m.name });
      } catch (err) {
        showMatrixToast(t("mx.machineRemove"), errorDetail(err), true);
        return;
      }
      await loadMachines();
      if (state.machineFilter === m.name) {
        state.machineFilter = "";
        loadMatrix();
      }
    });
    row.append(rm);
    listEl.append(row);
  }
}

$("machine-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("machine-name").value.trim();
  const host = $("machine-host").value.trim();
  if (!name || !host) return;
  const user = $("machine-user").value.trim();
  const portRaw = $("machine-port").value.trim();
  const halterPath = $("machine-halter-path").value.trim();
  try {
    await invoke("halter_machines_add", {
      name,
      host,
      user: user || null,
      port: portRaw ? Number(portRaw) : null,
      halterPath: halterPath || null,
    });
  } catch (err) {
    showMatrixToast(t("mx.machineAdd"), errorDetail(err), true);
    return;
  }
  $("machine-add-form").reset();
  await loadMachines();
});

/* ---------------- language ---------------- */

$("lang-zh").addEventListener("click", () => setLang("zh"));
$("lang-en").addEventListener("click", () => setLang("en"));

// 静态文案由 i18n.js 的 applyI18n() 刷新；这里重渲染各视图的动态文案。
// armed 的 Apply 按钮文案会被 applyI18n 覆盖，需按当前状态重设。
document.addEventListener("halter:langchange", () => {
  if (applyArmed) $("btn-matrix-apply").textContent = t("mx.applyArmed");
  if (state.scanCache.has("")) renderOverview(state.scanCache.get(""));
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
});

/* ---------------- providers: 模型供应商切换（claude / codex） ---------------- */

const PV_TOOLS = [
  { key: "claude", labelKey: "pv.tabClaude" },
  { key: "codex", labelKey: "pv.tabCodex" },
];
const providersState = { tool: "claude", data: null, removeArmed: null, removeTimer: null };
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
    providersState.data = await invoke("halter_providers_list");
  } catch (err) {
    showError($("providers-error"), err);
    return;
  } finally {
    $("providers-loading").classList.add("hidden");
  }
  $("providers-content").classList.remove("hidden");
  renderProviders();
}

function pvStatusMeta(status) {
  if (status === "halter") return { cls: "halter", key: "pv.statusHalter" };
  if (status === "external") return { cls: "external", key: "pv.statusExternal" };
  return { cls: "official", key: "pv.statusOfficial" };
}

function renderProviders() {
  const data = providersState.data;
  if (!data) return;

  // 预设下拉（数据来自 providers list --json 的 presets 字段，单一事实源）
  const presetSel = $("pv-preset");
  const chosenPreset = presetSel.value;
  presetSel.replaceChildren(el("option", "", t("pv.formPresetCustom")));
  for (const p of data.presets || []) {
    const opt = el("option", "", p.label ? `${p.name} · ${p.label}` : p.name);
    opt.value = p.name;
    presetSel.append(opt);
  }
  if (chosenPreset) presetSel.value = chosenPreset;

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

  const statusEl = el("span", `pv-status ${pvStatusMeta(cur.status).cls}`, t(pvStatusMeta(cur.status).key));
  const curTitle = el("div", "pv-cur-title");
  curTitle.append(
    el("strong", "", cur.provider || t(cur.status === "external" ? "pv.externalName" : "pv.officialName")),
    statusEl,
  );
  const curMeta = el("div", "dim", `${cur.base_url || t("pv.officialEndpoint")}${cur.model ? " · " + cur.model : ""}`);
  const currentCard = el("div", "pv-current");
  currentCard.append(el("div", "pv-cur-label", t("pv.currentLabel")), curTitle, curMeta);
  if (cur.status === "external" && cur.detail) {
    currentCard.append(el("div", "pv-external-hint", t("pv.externalHint")));
  }
  $("providers-current").replaceChildren(currentCard);

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
  const endpoint = (p[tool] && p[tool].base_url) || t("pv.officialEndpoint");
  const model = p[tool] && p[tool].model ? ` · ${p[tool].model}` : "";
  info.append(nameLine, el("div", "dim pv-row-endpoint", endpoint + model));
  row.append(info);

  const actions = el("div", "pv-row-actions");
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
    result = await invoke("halter_providers_switch", { id, tool });
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
      result = await invoke("halter_providers_remove", { id });
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
  panel.classList.toggle("hidden");
  if (!panel.classList.contains("hidden")) $("pv-id").focus();
});
$("btn-providers-form-close").addEventListener("click", () => {
  $("providers-add-panel").classList.add("hidden");
});

// 选中预设 → 自动填充端点/显示名；预设不含当前工具时切到它支持的第一个工具
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
    url = p.urls[tool];
  }
  $("pv-base-url").value = url;
  if (!$("pv-label").value.trim()) $("pv-label").value = p.label || p.name;
});

$("provider-add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("pv-id").value.trim();
  const tool = $("pv-tool").value;
  const baseUrl = $("pv-base-url").value.trim();
  if (!id || !baseUrl) return;
  const args = {
    id,
    tool,
    baseUrl,
    label: $("pv-label").value.trim() || null,
    model: $("pv-model").value.trim() || null,
    token: $("pv-token").value || null,
  };
  if (tool === "codex") {
    const effort = $("pv-reasoning-effort").value.trim();
    const ctx = Number($("pv-context-window").value.trim());
    if (effort) args.reasoningEffort = effort;
    if (Number.isFinite(ctx) && ctx > 0) args.contextWindow = ctx;
  }
  let result = null;
  try {
    result = await invoke("halter_providers_add", args);
  } catch (err) {
    showMatrixToast(t("pv.addFail", { id }), errorDetail(err), true);
    return;
  }
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(
    t(result.ok ? "pv.addDone" : "pv.addFail", { id }),
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
    result = await invoke("halter_providers_adopt");
  } catch (err) {
    showMatrixToast(t("pv.adoptFail"), errorDetail(err), true);
    return;
  }
  const body = [result.stdout, result.stderr].filter((s) => s && s.trim()).join("\n").trim();
  showMatrixToast(t("pv.adoptDone"), body || t("pv.noop"), !result.ok);
  await loadProviders(true);
});

/* ---------------- boot ---------------- */

(async function boot() {
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
    $("halter-version").title = typeof err === "string" ? err : JSON.stringify(err);
  }
  loadOverview();
})();
