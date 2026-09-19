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
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return `今天 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const yest = new Date(now);
  yest.setDate(yest.getDate() - 1);
  if (d.toDateString() === yest.toDateString()) return `昨天 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (diffMs < 7 * 86400000) return `${Math.floor(diffMs / 86400000)} 天前`;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
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
  scanCache: null,
  sessionsLoaded: false,
  sessions: [],
  projects: [],
  projectFilter: null,    // null = 全部项目；string = 项目路径
  toolFilters: new Set(), // 空集 = 全部助手
  viewMode: "timeline",   // "timeline" | "list"
  matrixLayer: "skills",
  matrixGapsOnly: false,
  matrixHarnessOnly: true,
  expandedFamilies: new Set(),
  projectsError: null,
  projectMatches: [],
  dispatchLoaded: false,
  dispatchModels: {},      // harness -> 默认模型（config.toml [models]）
  dispatchCatalog: {},     // harness -> 可选模型列表（[model_catalog] 或内置 GLM 系）
  dispatchProjects: [],
  dispatchCards: new Map(),   // task_id -> {node, timer}
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
    if (btn.dataset.view === "dispatch") loadDispatchView();
  });
});

/* ---------------- overview ---------------- */

const LAYERS = [
  ["skills", "skills"],
  ["agents", "agents"],
  ["mcp_servers", "MCP"],
  ["plugins", "插件"],
  ["hooks", "hooks"],
  ["sessions", "sessions"],
];

async function fetchScan(force = false) {
  if (!force && state.scanCache) return state.scanCache;
  const data = await invoke("halter_scan");
  state.scanCache = data;
  return data;
}

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
      row.append(el("span", "", item.message || ""));
      doctorList.append(row);
    }
  } else {
    doctorSection.classList.add("hidden");
  }

  const tools = (data.inventory || []).filter((t) => t.installed);
  $("tools-heading").textContent = `工具（${tools.length}）`;
  const grid = $("tools-grid");
  grid.replaceChildren();
  for (const tool of tools) {
    const card = el("div", "tool-card");
    const header = el("div", "tool-card-header");
    const name = el("div", "tool-name", tool.display || tool.tool);
    if ((tool.category || "harness") === "editor") {
      name.append(el("span", "cat-badge editor", "编辑器"));
    }
    header.append(name);
    header.append(el("div", "tool-key", tool.tool));
    card.append(header);

    const chips = el("div", "layer-chips");
    for (const [key, label] of LAYERS) {
      const n = Array.isArray(tool[key]) ? tool[key].length : 0;
      chips.append(el("span", `count-chip${n > 0 ? " on" : ""}`, `${label} ${n}`));
    }
    card.append(chips);
    grid.append(card);
  }
}

$("btn-refresh-scan").addEventListener("click", () => {
  state.scanCache = null;
  loadOverview();
});

/* ---------------- matrix: harness × layer ---------------- */

const MATRIX_LAYERS = [
  { key: "skills", field: "skills", label: "Skills" },
  { key: "agents", field: "agents", label: "Subagents" },
  { key: "mcp", field: "mcp_servers", label: "MCP" },
  { key: "plugins", field: "plugins", label: "插件" },
  { key: "hooks", field: "hooks", label: "Hooks" },
];

function matrixItemName(layer, item) {
  if (layer.key === "plugins") return item.plugin_id;
  if (layer.key === "hooks") return item.label;
  return item.name;
}

function buildMatrix(scan, layerKey) {
  const layer = MATRIX_LAYERS.find((l) => l.key === layerKey);
  let tools = (scan.inventory || []).filter((t) => t.installed);
  if (state.matrixHarnessOnly) {
    // harness = 独立 AI 编码代理；editor（vscode/continue/cline 等）默认不进矩阵列
    tools = tools.filter((t) => (t.category || "harness") !== "editor");
  }
  const rows = new Map();
  for (const tool of tools) {
    for (const item of tool[layer.field] || []) {
      const name = matrixItemName(layer, item);
      if (!name) continue;
      let row = rows.get(name);
      if (!row) {
        row = { statuses: new Map(), items: [] };
        rows.set(name, row);
      }
      const linked = layer.key === "skills" || layer.key === "agents" ? !!item.linked : false;
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
    missing: tools.filter((t) => !r.statuses.has(t.tool)).length,
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
    data = await fetchScan(force);
  } catch (err) {
    $("matrix-loading").classList.add("hidden");
    showError($("matrix-error"), err);
    return;
  }
  $("matrix-loading").classList.add("hidden");
  renderMatrixLayerTabs();
  renderMatrix(data);
  $("matrix-content").classList.remove("hidden");
}

function renderMatrixLayerTabs() {
  const tabs = $("matrix-layer-tabs");
  tabs.replaceChildren();
  for (const layer of MATRIX_LAYERS) {
    const btn = el("button", `layer-tab${state.matrixLayer === layer.key ? " active" : ""}`, layer.label);
    btn.addEventListener("click", () => {
      state.matrixLayer = layer.key;
      renderMatrixLayerTabs();
      renderMatrix(state.scanCache);
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
  nodes.push(el("div", `hc-desc${desc ? "" : " empty"}`, desc || "（SKILL.md 未提供 description）"));
  const meta = el("div", "hc-meta");
  if (row.item && row.item.path) {
    meta.append(el("span", "hc-path", shortenPath(row.item.path)));
  }
  if (row.statuses.size && [...row.statuses.values()].includes("synced")) {
    meta.append(el("span", "hc-linked", "库链接"));
  }
  nodes.push(meta);
  return nodes;
}

function familyHoverBody(group) {
  const nodes = [el("div", "hc-name", `${group.root} 家族`)];
  nodes.push(el("div", "hc-sub", `${group.members.length} 个成员`));
  const list = el("div", "hc-member-list");
  for (const m of group.members) {
    const item = el("div", "hc-member");
    item.append(el("div", "hc-member-name", m.name));
    item.append(el("div", "hc-member-desc", m.item && m.item.description
      ? firstLine(m.item.description)
      : "（无描述）"));
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
  const allGroups = groupRowsIntoFamilies(rows);
  const shownGroups = state.matrixGapsOnly
    ? allGroups.filter((g) => (g.type === "family" ? g.members.some((m) => m.missing > 0) : g.row.missing > 0))
    : allGroups;
  const totalGaps = rows.reduce((acc, r) => acc + r.missing, 0);
  const gapShown = state.matrixGapsOnly
    ? shownGroups.reduce((a, g) => a + (g.type === "family" ? g.members.filter((m) => m.missing > 0).length : 1), 0)
    : 0;

  const scopeLabel = state.matrixHarnessOnly ? `${tools.length} 个 AI Harness` : `${tools.length} 个工具（含编辑器）`;
  $("matrix-summary").textContent =
    `${layer.label}：${rows.length} 个条目 × ${scopeLabel} · 缺口 ${totalGaps} 处` +
    (state.matrixGapsOnly ? ` · 仅显示缺口的 ${gapShown} 条` : "");

  const wrap = $("matrix-wrap");
  wrap.replaceChildren();
  if (!shownGroups.length) {
    wrap.append(el("div", "loading", state.matrixGapsOnly ? "没有缺口 — 所有工具均覆盖" : "没有数据"));
    return;
  }

  const groups = shownGroups;
  const familyCount = groups.filter((g) => g.type === "family").length;
  if (familyCount) {
    const base = $("matrix-summary").textContent;
    $("matrix-summary").textContent = base + ` · 已聚合 ${familyCount} 个家族`;
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
  const CELL_TEXT = {
    synced: "已同步（symlink）",
    present: "已存在（非 halter 管理）",
    partial: "部分成员存在",
    missing: "缺少",
  };

  const renderRow = (row, cls) => {
    const nameCell = el("div", `mx-name ${cls || ""}`);
    nameCell.title = row.name;
    if (layer.key === "skills") {
      nameCell.removeAttribute("title");
      attachHoverPreview(nameCell, () => skillHoverBody(row));
    }
    nameCell.append(el("span", "", row.name));
    grid.append(nameCell);
    for (const tool of tools) {
      const st = row.statuses.get(tool.tool) || "missing";
      const cell = el("div", `mx-cell ${st}`, CELL_GLYPH[st] || "○");
      cell.title = `${tool.tool}：${CELL_TEXT[st] || st}`;
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
    nameCell.title = `${group.root} 家族（${group.members.length} 个成员）`;
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
      renderMatrix(state.scanCache);
    });
    grid.append(nameCell);
    for (const tool of tools) {
      const st = aggregateFamilyStatus(group.members, tool);
      const present = group.members.filter((m) => m.statuses.has(tool.tool)).length;
      const cell = el("div", `mx-cell ${st}`, CELL_GLYPH[st] || "○");
      cell.title = `${tool.tool}：${CELL_TEXT[st] || st}（${present}/${group.members.length}）`;
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
  wrap.append(grid);
}

$("matrix-gaps-only").addEventListener("change", (e) => {
  state.matrixGapsOnly = e.target.checked;
  if (state.scanCache) renderMatrix(state.scanCache);
});

$("matrix-harness-only").addEventListener("change", (e) => {
  state.matrixHarnessOnly = e.target.checked;
  if (state.scanCache) renderMatrix(state.scanCache);
});

$("btn-refresh-matrix").addEventListener("click", () => loadMatrix(true));

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
    input.placeholder = "项目加载失败 — 点击重试";
    clear.classList.add("hidden");
    return;
  }
  if (state.projectFilter === null) {
    input.value = "";
    const real = state.projects.filter((p) => (p.kind || "project") === "project").length;
    const others = state.projects.length - real;
    input.placeholder = others
      ? `全部项目（${real} 个 + ${others} 个历史/临时目录）— 输入即筛选`
      : `全部项目（${real} 个）— 输入即筛选`;
    clear.classList.add("hidden");
  } else {
    const p = state.projects.find((x) => x.path === state.projectFilter);
    input.value = p ? (p.name || basename(p.path)) : basename(state.projectFilter);
    input.placeholder = "输入项目名/路径筛选…";
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
  allHead.append(el("span", "project-name", "全部项目"));
  const totalSessions = state.projects.reduce((a, p) => a + p.sessions, 0);
  allHead.append(el("span", "project-count", `${state.projects.length} 项目 · ${totalSessions} 会话`));
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
      const KIND_TEXT = { temp: "临时", dated: "会话目录", virtual: "内部", stale: "已删除" };
      name.append(el("span", "project-kind-badge", KIND_TEXT[p.kind] || p.kind));
    }
    head.append(name);
    head.append(el("span", "project-count", `${p.sessions} 会话`));
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
    listEl.append(el("div", "project-group-header", `临时 / 自动会话目录 / 已删除（${others.length}）`));
    for (const p of others) renderProjectRow(p);
  }
  state.projectMatches = projects;
  if (needle && !projects.length) {
    const raw = filter.trim();
    if (raw.startsWith("/") || raw.startsWith("~")) {
      const hint = el("div", "project-raw-hint", `回车使用路径：${raw}`);
      listEl.append(hint);
    } else {
      listEl.append(el("div", "loading", "没有匹配的项目"));
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
  list.replaceChildren(el("div", "loading", "读取会话…"));
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
  box.replaceChildren();
  if (counts.size < 2) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");

  const anySelected = state.toolFilters.size > 0;
  const all = el("button", `filter-chip${!anySelected ? " active" : ""}`, `全部 ${state.sessions.length}`);
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
  if (state.viewMode === "timeline") renderTimeline(filtered);
  else renderFlatList(filtered);
}

function updateSessionCount(shown, total, suffix) {
  const scope = state.projectFilter === null ? "全部项目" : basename(state.projectFilter);
  const toolScope = state.toolFilters.size ? ` · ${state.toolFilters.size} 个助手` : "";
  const tail = suffix ? ` · ${suffix}` : "";
  $("session-count").textContent = `${scope}${toolScope}${tail} · ${shown}/${total}`;
}

function sessionCard(s, opts = {}) {
  const item = el("div", "session-item");
  if (opts.timeline) item.classList.add("tl-card");

  const title = el("div", "session-title", s.title || s.ref);
  const meta = el("div", "session-meta");
  const badge = el("span", "tool-badge", s.tool);
  badge.style.color = toolColor(s.tool);
  badge.style.borderColor = `${toolColor(s.tool)}55`;
  meta.append(badge);
  if (state.projectFilter === null && s.project) {
    meta.append(el("span", "project-chip", basename(s.project)));
  }
  if (opts.timePrefix) meta.append(el("span", "meta-item", opts.timePrefix));
  meta.append(el("span", "meta-item", opts.dateText || fmtDate(s.updated_at)));
  meta.append(el("span", "meta-item", `${s.message_count} 条`));
  if (s.branch) meta.append(el("span", "meta-item", `⑂ ${s.branch}`));

  item.append(title, meta);
  item.addEventListener("click", () => selectSession(s, item));
  return item;
}

function renderFlatList(items) {
  const list = $("sessions-list");
  list.className = "sessions-list";
  list.replaceChildren();
  if (!items.length) {
    list.append(el("div", "loading", state.toolFilters.size ? "所选助手没有会话" : "没有找到会话"));
    return;
  }
  for (const s of items) list.append(sessionCard(s));
}

function renderTimeline(items) {
  const list = $("sessions-list");
  list.className = "sessions-list timeline";
  list.replaceChildren();
  if (!items.length) {
    list.append(el("div", "loading", state.toolFilters.size ? "所选助手没有会话" : "没有找到会话"));
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
    group.append(el("div", "tl-date", dateLabel(key)));
    for (const s of byKey.get(key)) {
      const entry = el("div", "tl-item");
      const dot = el("span", "tl-dot");
      dot.style.backgroundColor = toolColor(s.tool);
      entry.append(dot);
      entry.append(sessionCard(s, { timeline: true, timePrefix: fmtTime(s.updated_at), dateText: dateLabel(key) }));
      group.append(entry);
    }
    tl.append(group);
  }
  list.append(tl);
}

/* calendar helpers */
const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

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
  if (key === todayKey) return "今天";
  if (key === yestKey) return "昨天";
  const d = new Date(key + "T00:00:00");
  if (!Number.isNaN(d.getTime())) {
    return `${key} · 周${WEEKDAYS[d.getDay()]}`;
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
  detail.replaceChildren(el("div", "loading", "读取会话详情…"));

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

function renderSessionDetail(data) {
  const s = data.session || {};
  const detail = $("session-detail");
  detail.className = "session-detail";
  detail.replaceChildren();

  const header = el("div", "detail-header");
  header.append(el("div", "detail-title", s.title || s.ref));
  const badges = el("div", "session-meta");
  const badge = el("span", "tool-badge", s.tool);
  badge.style.color = toolColor(s.tool);
  badge.style.borderColor = `${toolColor(s.tool)}55`;
  badges.append(badge);
  if (s.model) badges.append(el("span", "meta-item", s.model));
  if (s.branch) badges.append(el("span", "meta-item", `⑂ ${s.branch}`));
  header.append(badges);
  detail.append(header);

  const grid = el("div", "meta-grid");
  grid.append(metaCell("Ref", s.ref));
  grid.append(metaCell("项目", basename(s.project)));
  grid.append(metaCell("更新", fmtDate(s.updated_at)));
  grid.append(metaCell("消息数", s.message_count));
  grid.append(metaCell("后端", s.backend));
  detail.append(grid);

  const actions = el("div", "detail-actions");
  const handoffBtn = el("button", "btn", "生成交接上下文");
  handoffBtn.addEventListener("click", async () => {
    handoffBtn.disabled = true;
    handoffBtn.textContent = "生成中…";
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
      block.append(el("h2", "", "交接上下文（已脱敏）"));
      const pre = el("pre", "transcript");
      pre.textContent = text;
      block.append(pre);
    } catch (err) {
      const box = el("div", "error-box");
      box.textContent = errorDetail(err);
      detail.append(box);
    } finally {
      handoffBtn.disabled = false;
      handoffBtn.textContent = "生成交接上下文";
    }
  });
  actions.append(handoffBtn);
  detail.append(actions);

  detail.append(el("h2", "", "最近对话"));
  const transcript = el("div", "transcript");
  const text = data.transcript || "（无 transcript）";
  const lines = text.split("\n");
  let buf = [];
  const flush = () => {
    if (buf.length) {
      transcript.append(el("span", "", buf.join("\n")));
      buf = [];
    }
  };
  for (const line of lines) {
    const m = line.match(/^## (\S+) (USER|ASSISTANT|TOOL)(?:\s+\((.+)\))?$/);
    if (m) {
      flush();
      const head = el("div", "msg-header");
      head.append(el("span", `msg-role-${m[2]}`, `${m[1]} ${m[2]}`));
      if (m[3]) head.append(el("span", "", ` (${m[3]})`));
      transcript.append(head);
    } else {
      buf.push(line);
    }
  }
  flush();
  detail.append(transcript);
}

$("btn-refresh-sessions").addEventListener("click", loadSessionsView);

/* ---------- session search (follows project + harness filters) ---------- */

async function runSearch() {
  const requestId = ++searchRequestId;
  const query = $("search-input").value.trim();
  if (!query) return;
  const list = $("sessions-list");
  list.replaceChildren(el("div", "loading", "搜索中…"));
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
  updateSessionCount(hits.length, allHits.length, `搜索“${query}”`);
  list.className = "sessions-list";
  list.replaceChildren();
  if (!hits.length) {
    list.append(el("div", "loading", `没有匹配“${query}”的会话`));
    return;
  }
  for (let i = 0; i < hits.length; i++) {
    const s = hits[i].session;
    const hit = hits[i];
    const item = el("div", "session-item");
    item.append(el("div", "session-title", s.title || s.ref));
    const badge = el("span", "tool-badge", s.tool);
    badge.style.color = toolColor(s.tool);
    badge.style.borderColor = `${toolColor(s.tool)}55`;
    const meta = el("div", "session-meta");
    meta.append(badge);
    if (allProjects && s.project) meta.append(el("span", "project-chip", basename(s.project)));
    meta.append(el("span", "meta-item", fmtDate(s.updated_at)));
    item.append(meta);
    const snippet = el("div", "meta-item");
    snippet.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-dim);margin-top:4px;";
    snippet.textContent = (hit.snippet || "").replace(/\s+/g, " ");
    item.append(snippet);
    item.addEventListener("click", () => selectSession(s, item));
    list.append(item);
  }
}

$("btn-search").addEventListener("click", runSearch);
$("search-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runSearch();
});

/* ---------------- dispatch（多 Harness 任务调度） ---------------- */

const DISPATCH_RUNNERS = [
  { key: "claude", label: "Claude Code", aliases: ["claude", "claudecode", "cc"] },
  { key: "codex", label: "Codex", aliases: ["codex", "cx"] },
  { key: "zcode", label: "ZCode", aliases: ["zcode", "z"] },
  { key: "opencode", label: "OpenCode", aliases: ["opencode", "oc"] },
];

function parseDispatchMentions(text) {
  const found = [];   // {tool, model|null}
  const rest = text.replace(
    /(^|[^\w@])@([a-z][a-z0-9-]*(?:\/[^\s@]+)?)/gi,
    (m, pre, raw) => {
      const [name, ...modelParts] = raw.split("/");
      const spec = DISPATCH_RUNNERS.find((r) => r.aliases.includes(name.toLowerCase()));
      if (spec) {
        const model = modelParts.join("/") || null;
        if (!found.some((t) => t.tool === spec.key && t.model === model)) {
          found.push({ tool: spec.key, model });
        }
        return pre;
      }
      return m;
    }
  );
  return { targets: found, prompt: rest.replace(/\s+/g, " ").trim() };
}

async function loadDispatchView() {
  if (!state.dispatchLoaded) {
    bindDispatchEvents();
    state.dispatchLoaded = true;
  }
  loadDispatchModels();
  await loadDispatchProjects();
  updateDispatchHint();
}

async function loadDispatchModels() {
  try {
    const data = await invoke("halter_models");
    state.dispatchModels = data.models || {};
    state.dispatchCatalog = data.catalog || {};
    renderDispatchSelectors();
  } catch {
    state.dispatchModels = {};
    state.dispatchCatalog = {};
  }
}

function renderDispatchSelectors() {
  const harnessSel = $("dispatch-harness-select");
  const modelSel = $("dispatch-model-select");
  if (!harnessSel || !modelSel) return;

  const prevHarness = harnessSel.value;
  harnessSel.replaceChildren(new Option("选择 Harness…", ""));
  for (const spec of DISPATCH_RUNNERS) {
    harnessSel.append(new Option(spec.label, spec.key));
  }
  harnessSel.value = prevHarness && [...harnessSel.options].some((o) => o.value === prevHarness)
    ? prevHarness : "";

  const key = harnessSel.value;
  const models = key ? (state.dispatchCatalog[key] || []) : [];
  modelSel.replaceChildren(new Option(key ? "模型（可选）" : "先选 Harness…", ""));
  if (key && state.dispatchModels[key] && !models.includes(state.dispatchModels[key])) {
    modelSel.append(new Option(state.dispatchModels[key] + "（默认）", state.dispatchModels[key]));
  }
  for (const m of models) {
    modelSel.append(new Option(m, m));
  }
  modelSel.value = "";
}

function onHarnessSelect(key) {
  renderDispatchSelectors();
  if (!key) return;
  const input = $("dispatch-input");
  const { targets } = parseDispatchMentions(input.value);
  if (!targets.some((t) => t.tool === key)) {
    input.value = `@${key} ${input.value}`.trim() + " ";
    const pos = input.value.length;
    input.focus();
    input.setSelectionRange(pos, pos);
  } else {
    input.focus();
  }
  updateDispatchHint();
}

function onModelSelect(model) {
  if (!model) return;
  const input = $("dispatch-input");
  // 更新第一个 @harness 提及为 @harness/model
  const re = /(^|[^\w@])@([a-z][a-z0-9-]*)(\/[^\s@]+)?/i;
  if (re.test(input.value)) {
    input.value = input.value.replace(re, (m, pre, name) => `${pre}@${name}/${model}`);
  }
  updateDispatchHint();
}

async function loadDispatchProjects() {
  const select = $("dispatch-project");
  try {
    const data = await invoke("halter_sessions_projects", { project: "." });
    state.dispatchProjects = (data.projects || []).filter((p) => (p.kind || "project") === "project");
  } catch {
    state.dispatchProjects = [];
  }
  const prev = select.value;
  select.replaceChildren();
  if (!state.dispatchProjects.length) {
    select.append(new Option("当前目录", ""));
    select.value = "";
    return;
  }
  for (const p of state.dispatchProjects) {
    select.append(new Option(shortenPath(p.path) || p.path, p.path));
  }
  // 默认选会话最多的项目；保留用户已选
  const busiest = [...state.dispatchProjects].sort((a, b) => b.sessions - a.sessions)[0];
  select.value = prev && [...select.options].some((o) => o.value === prev)
    ? prev
    : (busiest ? busiest.path : "");
}

function dispatchTargets() {
  const input = $("dispatch-input");
  const parsed = parseDispatchMentions(input.value);
  if (parsed.targets.length) return parsed;
  // 输入框没有 @提及时，回退到下拉选择（Harness + 模型）
  const harness = $("dispatch-harness-select") ? $("dispatch-harness-select").value : "";
  if (!harness) return { targets: [], prompt: parsed.prompt };
  const model = $("dispatch-model-select") ? $("dispatch-model-select").value : "";
  return { targets: [{ tool: harness, model: model || null }], prompt: parsed.prompt };
}

function updateDispatchHint() {
  const hint = $("dispatch-mention-hint");
  const input = $("dispatch-input");
  const raw = input.value.trim();
  const { targets, prompt } = dispatchTargets();
  if (!targets.length && !raw) {
    // 空输入：中性引导（不是警告）
    hint.textContent = "用 @claude / @codex / @zcode / @opencode 选择执行者；指定模型如 @claude/opus";
    hint.className = "dispatch-mention-hint";
  } else if (!targets.length) {
    // 有内容但没识别到提及：提示可用项
    hint.textContent = "未识别到 @harness，可用：@claude @codex @zcode @opencode；指定模型如 @claude/opus";
    hint.className = "dispatch-mention-hint none";
  } else {
    const labels = targets.map((t) => "@" + t.tool + (t.model ? "/" + t.model : ""));
    hint.textContent = `将派发给 ${labels.join(" ")}${prompt ? "" : "（缺少任务描述）"}`;
    hint.className = "dispatch-mention-hint" + (prompt ? "" : " none");
  }
}

function bindDispatchEvents() {
  // input 覆盖大多数场景；keyup/compositionend/change 兜底 IME、粘贴与自动化注入
  for (const evt of ["input", "keyup", "compositionend", "change"]) {
    $("dispatch-input").addEventListener(evt, updateDispatchHint);
  }
  $("dispatch-input").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      sendDispatch();
    }
  });
  $("btn-dispatch-send").addEventListener("click", sendDispatch);
  $("dispatch-harness-select").addEventListener("change", (e) => { onHarnessSelect(e.target.value); updateDispatchHint(); });
  $("dispatch-model-select").addEventListener("change", (e) => { onModelSelect(e.target.value); });
}

let dispatchBusy = false;

async function sendDispatch() {
  if (dispatchBusy) return;
  dispatchBusy = true;

  const errBox = $("dispatch-error");
  errBox.classList.add("hidden");
  const message = $("dispatch-input").value.trim();
  const { targets, prompt } = dispatchTargets();
  if (!targets.length || !prompt) {
    errBox.textContent = !targets.length
      ? "未选择执行者：在输入框 @claude，或在上拉选择 Harness"
      : "还需要任务描述";
    errBox.classList.remove("hidden");
    dispatchBusy = false;
    return;
  }

  const project = $("dispatch-project").value || ".";
  const mode = $("dispatch-yolo").checked ? "yolo" : "safe";
  const btn = $("btn-dispatch-send");
  btn.disabled = true;
  btn.textContent = "派发中…";

  try {
    const data = await invoke("halter_dispatch_run", { message, project, mode });
    $("dispatch-input").value = "";
    $("dispatch-harness-select").value = "";
    renderDispatchSelectors();
    updateDispatchHint();
    for (const task of data.tasks || []) {
      try {
        addDispatchCard(task);
        pollDispatchTask(task.task_id);
      } catch (cardErr) {
        showError(errBox, "任务卡渲染失败: " + errorDetail(cardErr));
      }
    }
  } catch (err) {
    showError(errBox, err);
  } finally {
    dispatchBusy = false;
    btn.disabled = false;
    btn.textContent = "⏵ 派发 ⌘↵";
  }
}

function dispatchStatusBadge(status) {
  const map = {
    running: ["run", "运行中"],
    done: ["ok", "完成"],
    failed: ["err", "失败"],
    cancelled: ["cancel", "已取消"],
  };
  const [cls, label] = map[status] || ["", status || "-"];
  return el("span", `dispatch-status ${cls}`, label);
}

function addDispatchCard(task) {
  const list = $("dispatch-tasks");
  const card = el("div", "dispatch-card");
  card.dataset.taskId = task.task_id;

  const head = el("div", "dispatch-card-head");
  const badge = el("span", "dispatch-tool-badge");
  badge.style.setProperty("--c", toolColor(task.tool));
  badge.textContent = "@" + task.tool;
  const title = el("span", "dispatch-prompt", task.prompt);
  head.append(badge, title, dispatchStatusBadge(task.status));
  if (task.model) head.append(el("span", "dispatch-model-tag", task.model));
  if (task.mode === "yolo") head.append(el("span", "dispatch-yolo-tag", "yolo"));

  const meta = el("div", "dispatch-card-meta");
  meta.append(el("span", "", shortenPath(task.project)));
  meta.append(el("span", "", task.started_at || ""));

  const out = el("pre", "dispatch-output", "");
  card.append(head, meta, out);
  list.prepend(card);
  state.dispatchCards.set(task.task_id, { node: card, output: out, timer: null });
}

function pollDispatchTask(taskId) {
  const entry = state.dispatchCards.get(taskId);
  if (!entry) return;
  const deadline = Date.now() + 30 * 60 * 1000;
  const tick = async () => {
    if (Date.now() > deadline) {
      const stale = state.dispatchCards.get(taskId);
      if (stale?.timer) clearInterval(stale.timer);
      state.dispatchCards.delete(taskId);
      return;
    }
    try {
      const t = await invoke("halter_task_show", { taskId, tail: 8000 });
      const card = state.dispatchCards.get(taskId);
      if (!card) return; // 已被移除
      const badge = card.node.querySelector(".dispatch-status");
      const next = dispatchStatusBadge(t.status);
      badge.replaceWith(next);
      card.output.textContent = t.output_tail || "（等待输出…）";
      card.output.classList.toggle("streaming", t.status === "running");
      if (t.status !== "running") {
        clearInterval(card.timer);
        card.timer = null;
        state.dispatchCards.delete(taskId);
        card.node.classList.add("finished");
      }
    } catch (err) {
      const card = state.dispatchCards.get(taskId);
      if (card) {
        clearInterval(card.timer);
        card.timer = null;
        state.dispatchCards.delete(taskId);
        card.output.textContent = "轮询失败：" + errorDetail(err);
      }
    }
  };
  entry.timer = setInterval(tick, 1500);
  tick();
}

/* ---------------- sync ---------------- */

function selectedLayers() {
  return Array.from(document.querySelectorAll(".layer-chip input:checked")).map((i) => i.value);
}

let syncBusy = false;

async function runSync(apply) {
  if (syncBusy) return;
  syncBusy = true;
  const out = $("sync-output");
  out.classList.remove("hidden");
  out.textContent = apply ? "正在执行 sync --apply（写入变更）…\n" : "正在生成 dry-run 计划…\n";
  $("btn-sync-preview").disabled = true;
  $("btn-sync-apply").disabled = true;
  try {
    const result = await invoke("halter_sync", { apply, layers: selectedLayers() });
    const parts = [];
    parts.push(`exit code: ${result.code} (${result.ok ? "ok" : "failed"})`);
    if (result.stdout.trim()) parts.push("--- stdout ---\n" + result.stdout.trim());
    if (result.stderr.trim()) parts.push("--- stderr ---\n" + result.stderr.trim());
    out.textContent = parts.join("\n\n");
  } catch (err) {
    out.textContent = `调用失败：${typeof err === "string" ? err : JSON.stringify(err, null, 2)}`;
  } finally {
    syncBusy = false;
    $("btn-sync-preview").disabled = false;
    $("btn-sync-apply").disabled = false;
    disarmApply();
  }
}

let applyArmed = false;
let applyTimer = null;

function disarmApply() {
  applyArmed = false;
  clearTimeout(applyTimer);
  const btn = $("btn-sync-apply");
  btn.classList.remove("armed");
  btn.textContent = "Apply 实际执行";
}

$("btn-sync-preview").addEventListener("click", () => {
  disarmApply();
  runSync(false);
});

$("btn-sync-apply").addEventListener("click", () => {
  const btn = $("btn-sync-apply");
  if (!applyArmed) {
    applyArmed = true;
    btn.classList.add("armed");
    btn.textContent = "⚠ 再点一次确认写入";
    applyTimer = setTimeout(disarmApply, 5000);
    return;
  }
  runSync(true);
});

/* ---------------- boot ---------------- */

(async function boot() {
  try {
    const v = await invoke("halter_version");
    const badge = $("halter-version");
    badge.textContent = `halter ${v.version}`;
    if (v.desktop && v.version && String(v.version) !== String(v.desktop)) {
      badge.classList.add("mismatch");
      badge.title = `运行时版本 ${v.version} 与桌面打包期望 ${v.desktop} 不一致；release 不应携带旧 sidecar，请重新构建`;
    }
  } catch (err) {
    $("halter-version").textContent = "halter 不可用";
    $("halter-version").title = typeof err === "string" ? err : JSON.stringify(err);
  }
  loadOverview();
})();
