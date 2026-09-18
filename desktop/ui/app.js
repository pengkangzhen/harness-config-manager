// Halter desktop frontend — no build step, talks to Rust commands via Tauri IPC.
"use strict";

const invoke = (...a) => window.__TAURI__.core.invoke(...a);
const tauriListen = (...a) => window.__TAURI__.event.listen(...a);
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
  modelsLoaded: false,
  auditLoaded: false,
  auditItems: [],
  selectedAudit: null,
  auditJump: null,
  dispatchLoaded: false,
  dispatchModels: {},      // harness -> 默认模型（config.toml [models]）
  dispatchCatalog: {},     // harness -> 可选模型列表（[model_catalog] 或内置 GLM 系）
  dispatchProjects: [],
  ahp: null,               // {ready, agents, sessions, cards, nativeChats}
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
    if (btn.dataset.view === "models") loadModelsView();
    if (btn.dataset.view === "audit") loadAuditView();
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
        row = { statuses: new Map() };
        rows.set(name, row);
      }
      const linked = layer.key === "skills" || layer.key === "agents" ? !!item.linked : false;
      row.statuses.set(tool.tool, linked ? "synced" : "present");
    }
  }
  const list = [...rows.entries()].map(([name, r]) => ({
    name,
    statuses: r.statuses,
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

/* ---------------- models（原生模型配置） ---------------- */

const HEALTH_ICONS = { ok: "✓", warn: "!", error: "✕", skipped: "-" };

function renderModelHealth(health) {
  const box = $("model-health");
  if (!box) return;
  box.classList.remove("hidden");
  box.replaceChildren();
  if (!health) {
    box.classList.add("hidden");
    return;
  }
  const head = el("div", "model-health-head",
    health.ok ? "模型路由可用" : "模型路由不可用");
  head.className = `model-health-head ${health.ok ? "ok" : "err"}`;
  box.append(head);
  const list = el("div", "model-health-list");
  for (const item of health.checks || []) {
    const row = el("div", `model-health-item ${item.status}`);
    row.append(el("span", "model-health-icon", HEALTH_ICONS[item.status] || "-"));
    const text = el("span", "model-health-text");
    text.append(el("span", "model-health-label", item.label || item.id || "-"));
    if (item.detail) text.append(el("span", "model-health-detail", item.detail));
    row.append(text);
    list.append(row);
  }
  box.append(list);
}

async function loadModelsView(force = false) {
  if (!force && state.modelsLoaded) return;
  const status = $("model-status");
  status.textContent = "";
  try {
    const data = await invoke("halter_models");
    const model = (data.models || {}).halter || "";
    $("native-model-input").value = model;
    const provider = model.includes("/") ? model.split("/")[0] : "";
    const providerConfig = (data.providers || {})[provider] || {};
    $("native-base-url-input").value = providerConfig.base_url || "";
    $("native-api-key-env-input").value = providerConfig.api_key_env || "";
    renderModelHealth(data.health);
    state.modelsLoaded = true;
  } catch (err) {
    showError($("models-error"), err);
  }
}

async function saveNativeModel() {
  const errorBox = $("models-error");
  const status = $("model-status");
  errorBox.classList.add("hidden");
  status.className = "model-status";
  status.textContent = "保存中…";
  const button = $("btn-save-native-model");
  button.disabled = true;
  try {
    const model = $("native-model-input").value.trim();
    const baseUrl = $("native-base-url-input").value.trim();
    const apiKeyEnv = $("native-api-key-env-input").value.trim();
    if (!model) throw new Error("默认模型不能为空");
    await invoke("halter_model_configure", {
      model,
      baseUrl: baseUrl || null,
      apiKeyEnv,
    });
    // AHP host caches config at startup. Stop our managed host so the next
    // dispatch recreates it with the new provider routing.
    try {
      await invoke("halter_ahp_stop");
    } catch (_err) {
      // An externally managed host may refuse stop; surface a restart hint.
    }
    state.ahp = null;
    state.dispatchLoaded = false;
    state.modelsLoaded = false;
    await loadModelsView(true);
    status.className = "model-status ok";
    status.textContent = "已保存。托管 AHP 将在下次派发时重启；外部手动 host 需要重启后生效。";
  } catch (err) {
    status.className = "model-status";
    status.textContent = "";
    showError(errorBox, err);
  } finally {
    button.disabled = false;
  }
}

$("btn-refresh-models").addEventListener("click", () => {
  state.modelsLoaded = false;
  loadModelsView(true);
});
$("btn-save-native-model").addEventListener("click", saveNativeModel);

/* ---------------- audit（原生 Agent 审计） ---------------- */

async function loadAuditView(force = false) {
  const loading = $("audit-loading");
  const content = $("audit-content");
  const errorBox = $("audit-error");
  errorBox.classList.add("hidden");
  if (!force && state.auditLoaded) return;
  loading.classList.remove("hidden");
  content.classList.add("hidden");
  try {
    const data = await invoke("halter_audit_list", { limit: 100 });
    state.auditItems = data.audits || [];
    state.auditLoaded = true;
    loading.classList.add("hidden");
    renderAuditList();
    content.classList.remove("hidden");
  } catch (err) {
    loading.classList.add("hidden");
    showError(errorBox, err);
  }
}

function auditKindTone(kind) {
  if (kind === "turn.failed" || kind === "transaction.rollbackFailed") return "err";
  if (kind === "approval.response" || kind === "transaction.rolledBack") return "approval";
  if (kind === "tool.call" || kind === "tool.result") return "tool";
  if (kind.startsWith("context.")) return "context";
  return "";
}

function renderAuditList() {
  const list = $("audit-list");
  list.replaceChildren();
  if (!state.auditItems.length) {
    const empty = el("div", "empty-state", "暂无原生 Agent 审计轨迹");
    list.append(empty);
    return;
  }
  for (const item of state.auditItems) {
    const row = el("div", "audit-item" + (state.selectedAudit?.session_id === item.session_id ? " selected" : ""));
    const title = el("div", "audit-title", item.title || item.session_id);
    const meta = el("div", "audit-meta");
    meta.append(el("span", "tool-badge", item.provider || "native"));
    meta.append(el("span", "meta-item", `${item.event_count} events`));
    meta.append(el("span", "meta-item", fmtDate(item.last_event_at)));
    const kindRow = el("div", "audit-kind-row");
    const kinds = Object.entries(item.kinds || {}).slice(0, 6);
    for (const [kind, count] of kinds) {
      const chip = el("span", `audit-kind-chip ${auditKindTone(kind)}`, `${kind} ×${count}`);
      chip.addEventListener("click", () => {
        $("audit-kind-input").value = kind;
        if (state.selectedAudit) selectAudit(state.selectedAudit);
      });
      kindRow.append(chip);
    }
    row.append(title, meta, kindRow);
    row.addEventListener("click", () => selectAudit(item));
    list.append(row);
  }
}

async function selectAudit(item) {
  state.selectedAudit = item;
  renderAuditList();
  const detail = $("audit-detail");
  detail.className = "audit-detail";
  detail.replaceChildren(el("div", "loading", "正在读取审计事件…"));
  const kind = $("audit-kind-input").value.trim();
  try {
    const data = await invoke("halter_audit_show", {
      sessionId: item.session_id,
      kind: kind || null,
      tail: 300,
    });
    renderAuditDetail(data);
  } catch (err) {
    detail.className = "audit-detail";
    detail.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = typeof err === "string" ? err : (err?.message || JSON.stringify(err));
    detail.append(box);
  }
}

function renderAuditDetail(data) {
  const detail = $("audit-detail");
  detail.className = "audit-detail";
  detail.replaceChildren();
  const summary = data.summary || {};
  const header = el("div", "detail-header");
  header.append(el("h2", "detail-title", summary.title || summary.session_id || "Audit"));
  const meta = el("div", "meta-grid");
  const fields = [
    ["Session", summary.session_id],
    ["Provider", summary.provider],
    ["Workspace", summary.workspace],
    ["Events", `${data.returned_events || 0} / ${data.total_events || 0}`],
    ["First", summary.first_event_at],
    ["Last", summary.last_event_at],
    ["Size", `${summary.size_bytes || 0} B`],
  ];
  for (const [label, value] of fields) {
    const cell = el("div", "meta-cell");
    cell.append(el("div", "meta-label", label));
    cell.append(el("div", "meta-value", value || "-"));
    meta.append(cell);
  }
  header.append(meta);
  detail.append(header);

  const events = el("div", "audit-events");
  for (const event of data.events || []) {
    const item = el("div", `audit-event ${auditKindTone(event.kind)}`);
    item.dataset.eventId = String(event.id ?? "");
    const txnId = event.result && typeof event.result === "object"
      ? String(event.result.transactionId ?? "")
      : "";
    if (txnId) item.dataset.transactionId = txnId;
    const head = el("div", "audit-event-head");
    head.append(el("span", "audit-event-kind", event.kind || "-"));
    head.append(el("span", "audit-event-time", event.ts || "-"));
    item.append(head);
    const body = el("pre", "audit-event-body");
    const payload = { ...event };
    delete payload.ts;
    delete payload.kind;
    body.textContent = JSON.stringify(payload, null, 2);
    item.append(body);
    events.append(item);
  }
  detail.append(events);

  // Plan-evidence deep link: highlight and scroll to the target event.
  const jump = state.auditJump;
  state.auditJump = null;
  if (jump && jump.sessionId === summary.session_id) {
    const matched = [...events.querySelectorAll(".audit-event")].filter((node) =>
      node.dataset.eventId === jump.matchId || node.dataset.transactionId === jump.matchId);
    matched.forEach((node) => node.classList.add("jump"));
    if (matched.length) matched[0].scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

async function jumpToAuditEvidence(sessionId, matchId) {
  if (!sessionId || !matchId) return;
  state.auditJump = { sessionId, matchId: String(matchId) };
  $("audit-kind-input").value = "";
  const nav = document.querySelector('.nav-item[data-view="audit"]');
  if (nav && !nav.classList.contains("active")) nav.click();
  await loadAuditView();
  const item = (state.auditItems || []).find((x) => x.session_id === sessionId)
    || { session_id: sessionId };
  await selectAudit(item);
}

$("btn-refresh-audit").addEventListener("click", () => {
  state.auditLoaded = false;
  state.selectedAudit = null;
  loadAuditView(true);
});
$("audit-kind-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && state.selectedAudit) selectAudit(state.selectedAudit);
});

/* ---------------- dispatch（多 Harness 任务调度） ---------------- */

const DISPATCH_RUNNERS = [
  { key: "claude", label: "Claude Code", aliases: ["claude", "claudecode", "cc"] },
  { key: "codex", label: "Codex", aliases: ["codex", "cx"] },
  { key: "zcode", label: "ZCode", aliases: ["zcode", "z"] },
  { key: "opencode", label: "OpenCode", aliases: ["opencode", "oc"] },
  { key: "halter", label: "halter（原生 Agent）", aliases: ["halter", "h"] },
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
    hint.textContent = "用 @claude / @codex / @zcode / @opencode / @halter 选择执行者；@halter 是可审计的原生 Agent；默认只读，可开启逐 patch 写入";
    hint.className = "dispatch-mention-hint";
  } else if (!targets.length) {
    // 有内容但没识别到提及：提示可用项
    hint.textContent = "未识别到 @harness，可用：@claude @codex @zcode @opencode @halter；指定模型如 @claude/opus";
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

/* ---------- AHP 直连客户端 ---------- */

let ahpConnecting = null;

async function ensureAhp() {
  if (state.ahp && state.ahp.ready) return state.ahp;
  if (ahpConnecting) return ahpConnecting;
  ahpConnecting = (async () => {
    if (state.ahp && state.ahp.stop) {
      try { state.ahp.stop(); } catch { /* listener already gone */ }
      state.ahp = null;
    }
    const init = await invoke("halter_ahp_connect");
    const fresh = {
      ready: false,
      agents: [],
      sessions: [],
      cards: new Map(),       // chatUri -> active output card
      nativeChats: new Map(), // `${provider}:${project}` -> {sessionUri, chatUri}
      stop: null,
    };
    // Rust 桥把 server->client 消息以 ahp-message 事件转发
    fresh.stop = await tauriListen("ahp-message", (ev) => handleAhpData(fresh, ev.payload));
    state.ahp = fresh;
    const root = ((init.init || {}).snapshots || []).find((x) => x.resource === "ahp-root://");
  fresh.agents = (root && root.state && root.state.agents) || [];
  try {
    const listed = await ahpRequest(fresh, "listSessions", {});
    fresh.sessions = (listed && listed.items) || [];
  } catch {
    fresh.sessions = [];
  }
  fresh.ready = true;
  return fresh;
  })();
  try {
    return await ahpConnecting;
  } finally {
    ahpConnecting = null;
  }
}

function ahpRequest(st, method, params) {
  return invoke("halter_ahp_rpc", { method, params });
}

function ahpNotify(st, method, params) {
  return invoke("halter_ahp_notify", { method, params });
}

function handleAhpData(st, data) {
  let msg;
  try { msg = JSON.parse(data); } catch { return; }
  if (msg.method === "action") ahpApplyAction(st, msg.params);
}

function updateApprovalHistory(entry, record) {
  if (!entry.approvalHistory) entry.approvalHistory = [];
  const existing = entry.approvalHistory.find((item) => item.id === record.id);
  if (existing) Object.assign(existing, record);
  else entry.approvalHistory.push(record);
  if (!entry.approvalPanel) {
    entry.approvalPanel = el("section", "dispatch-approval-history");
    entry.root.insertBefore(entry.approvalPanel, entry.output);
  }
  entry.approvalPanel.replaceChildren(
    el("div", "dispatch-approval-history-title", `审批历史（${entry.approvalHistory.length}）`)
  );
  const list = el("div", "dispatch-approval-history-list");
  for (const item of entry.approvalHistory) {
    const row = el("div", `dispatch-approval-history-item ${item.status || "pending"}`);
    const marker = el("span", "dispatch-approval-history-marker", {
      pending: "…", approved: "✓", denied: "×",
    }[item.status] || "…");
    const body = el("div", "dispatch-approval-history-body");
    body.append(el("div", "dispatch-approval-history-tool", item.tool || "-"));
    if (item.summary) body.append(el("div", "dispatch-approval-history-summary", item.summary));
    if (item.decidedAt) body.append(el("div", "dispatch-approval-history-time", new Date(item.decidedAt).toLocaleString()));
    row.append(marker, body);
    list.append(row);
  }
  entry.approvalPanel.append(list);
}

function planEvidenceBadge(evidence) {
  if (!evidence) return "";
  const tools = (evidence.toolCallIds || []).length;
  const approvals = (evidence.approvalIds || []).length;
  const txns = (evidence.transactionIds || []).length;
  if (!tools && !approvals && !txns) return "";
  return `证据 ${tools + approvals + txns}（工具 ${tools} / 审批 ${approvals} / 事务 ${txns}）`;
}

function renderPlanEvidenceItem(entry, kind, item) {
  const row = el("div", "dispatch-plan-evidence-item");
  const kindLabel = { tool: "工具", approval: "审批", txn: "事务" }[kind] || kind;
  row.append(el("span", `dispatch-plan-evidence-kind ${kind}`, kindLabel));
  const label = kind === "tool"
    ? `${item.name || "-"} · ${item.id || "-"}`
    : kind === "approval"
      ? `${item.tool || "-"} · ${item.approved === true ? "已批准" : item.approved === false ? "已拒绝" : "待审批"}`
      : (item.id || "-");
  row.append(el("span", "dispatch-plan-evidence-label", label));
  if (item.ts) row.append(el("span", "dispatch-plan-evidence-time", new Date(item.ts).toLocaleTimeString()));
  const link = el("button", "dispatch-plan-evidence-link", "审计");
  link.type = "button";
  link.addEventListener("click", () => jumpToAuditEvidence(entry.auditSessionId, item.id));
  row.append(link);
  return row;
}

function renderDispatchPlan(entry, plan) {
  if (!entry.plan) return;
  if (plan) entry.currentPlan = plan;
  entry.plan.replaceChildren();
  const current = entry.currentPlan;
  if (!current || !(current.steps || []).length) {
    entry.plan.classList.add("empty");
    entry.plan.append(el("div", "dispatch-plan-empty", "尚无结构化计划"));
    return;
  }
  entry.plan.classList.remove("empty");
  if (current.note) entry.plan.append(el("div", "dispatch-plan-note", current.note));
  const list = el("div", "dispatch-plan-list");
  for (const step of current.steps) {
    const evidence = (entry.planEvidence || {})[step.id];
    const badge = planEvidenceBadge(evidence);
    const item = el("div", `dispatch-plan-item ${step.status || "pending"}`);
    item.append(el("span", "dispatch-plan-marker", {
      pending: "○", in_progress: "◐", done: "●", blocked: "✕",
    }[step.status] || "○"));
    const body = el("div", "dispatch-plan-body");
    body.append(el("div", "dispatch-plan-title", step.title || "-"));
    if (step.detail) body.append(el("div", "dispatch-plan-detail", step.detail));
    if (badge) {
      const expanded = entry.expandedSteps?.has(step.id) || false;
      const toggle = el("button", `dispatch-plan-evidence-toggle${expanded ? " open" : ""}`, `${expanded ? "▾" : "▸"} ${badge}`);
      toggle.type = "button";
      toggle.addEventListener("click", () => {
        if (!entry.expandedSteps) entry.expandedSteps = new Set();
        if (entry.expandedSteps.has(step.id)) entry.expandedSteps.delete(step.id);
        else entry.expandedSteps.add(step.id);
        renderDispatchPlan(entry);
      });
      body.append(toggle);
      if (expanded) {
        const evidenceList = el("div", "dispatch-plan-evidence-list");
        for (const tool of evidence.toolCallIds || []) evidenceList.append(renderPlanEvidenceItem(entry, "tool", tool));
        for (const approval of evidence.approvalIds || []) evidenceList.append(renderPlanEvidenceItem(entry, "approval", approval));
        for (const txn of evidence.transactionIds || []) evidenceList.append(renderPlanEvidenceItem(entry, "txn", txn));
        body.append(evidenceList);
      }
    }
    item.append(body);
    list.append(item);
  }
  entry.plan.append(list);
}

const COMPARISON_LABELS = {
  identical: "一致",
  conflicting: "冲突",
  overlapping: "不同区域",
  unique: "独有",
};

function hunkHeaderOf(line) {
  const m = /^(@@ [^@]*@@)/.exec(line);
  return m ? m[1] : line;
}

function renderDelegateDiff(diffText, conflictHeaders) {
  if (!conflictHeaders || !conflictHeaders.size) {
    return el("pre", "dispatch-compare-diff", diffText);
  }
  // 冲突 hunk 高亮需要行级 DOM；非冲突保持 <pre> 紧凑展示。
  const box = el("div", "dispatch-compare-diff line-mode");
  let inConflict = false;
  for (const line of diffText.split("\n")) {
    if (line.startsWith("@@ ")) inConflict = conflictHeaders.has(hunkHeaderOf(line));
    const row = el("div", "diff-line" + (inConflict ? " conflict" : ""));
    row.textContent = line;
    box.append(row);
  }
  return box;
}

function appendDelegateComparisonReport(panel, comparison) {
  const report = el("div", "dispatch-compare-report");
  if (comparison.strategy) {
    report.append(el("div", "dispatch-compare-strategy", comparison.strategy));
  }
  const chips = el("div", "dispatch-compare-files");
  for (const file of comparison.files || []) {
    const label = `${file.path} · ${COMPARISON_LABELS[file.classification] || file.classification}`
      + ((file.providers || []).length > 1 ? `（${file.providers.join("/")}）` : "");
    chips.append(el("span", `dispatch-compare-class ${file.classification}`, label));
  }
  report.append(chips);
  panel.append(report);
}

function updateDelegateComparison(entry, part, result) {
  const args = part.arguments || {};
  const payload = part.result || {};
  const provider = payload.provider || args.provider || "delegate";
  const id = part.id || "";
  let panel = entry.compareGrid?.querySelector(`[data-compare-id="${CSS.escape(id)}"]`);
  if (!entry.compareGrid) {
    entry.compare = el("section", "dispatch-compare");
    const title = el("div", "dispatch-compare-title", "多 Harness 结果对比");
    entry.compareGrid = el("div", "dispatch-compare-grid");
    entry.compare.append(title, entry.compareGrid);
    entry.root.insertBefore(entry.compare, entry.output);
  }
  if (!panel) {
    panel = el("article", "dispatch-compare-panel running");
    panel.dataset.compareId = id;
    entry.compareGrid.append(panel);
  }
  panel.className = `dispatch-compare-panel ${result ? (part.ok ? "ok" : "err") : "running"}`;
  panel.replaceChildren();
  const head = el("div", "dispatch-compare-head");
  head.append(el("span", "dispatch-compare-provider", provider));
  head.append(el("span", "dispatch-compare-state", result
    ? (part.ok ? `exit ${payload.exitCode ?? "-"}` : "失败")
    : "运行中"));
  panel.append(head);

  const comparison = payload.comparison || null;
  if (comparison) appendDelegateComparisonReport(panel, comparison);

  const conflictHeaders = new Set();
  for (const conflict of (comparison && comparison.conflicts) || []) {
    const header = conflict.hunks && conflict.hunks[provider];
    if (header) conflictHeaders.add(header);
  }
  const diff = String(payload.diff || "").trim();
  panel.append(diff
    ? renderDelegateDiff(diff, conflictHeaders)
    : el("pre", "dispatch-compare-diff", result
      ? "（该子代理没有产生 diff）"
      : "（等待 sandbox 结果…）"));
  if (result) {
    const output = el("pre", "dispatch-compare-output", String(payload.stdout || payload.stderr || "").slice(0, 8000));
    panel.append(output);
    const snapshot = payload.snapshot || {};
    if (snapshot.untrackedCopied || snapshot.untrackedSkipped) {
      panel.append(el("div", "dispatch-compare-meta",
        `untracked: ${snapshot.untrackedCopied?.length || 0} copied / ${snapshot.untrackedSkipped?.length || 0} skipped`));
    }
  }
}

function updateDispatchToolEvent(entry, part, result) {
  let node = entry.events.querySelector(`[data-call-id="${CSS.escape(part.id || "")}"]`);
  if (!node) {
    node = el("div", "dispatch-event running");
    node.dataset.callId = part.id || "";
    entry.events.append(node);
  }
  node.className = `dispatch-event ${result ? (part.ok ? "ok" : "err") : "running"}`;
  node.replaceChildren();
  const title = el("div", "dispatch-event-title");
  const badge = el("span", `dispatch-event-dot ${result ? (part.ok ? "ok" : "err") : "run"}`, result ? (part.ok ? "✓" : "×") : "…");
  const name = el("span", "dispatch-event-name", part.name || "-");
  title.append(badge, name);
  node.append(title);
  const detail = el("pre", "dispatch-event-detail");
  detail.textContent = result
    ? JSON.stringify(part.result || {}, null, 2).slice(0, 12000)
    : JSON.stringify(part.arguments || {}, null, 2);
  node.append(detail);
  if (part.name === "delegate_harness") {
    updateDelegateComparison(entry, part, result);
  }
  if (result && part.name === "apply_patch" && part.ok && part.result && part.result.transactionId) {
    const transactionId = part.result.transactionId;
    const rollback = el("button", "btn danger dispatch-event-rollback", "回滚此变更");
    rollback.type = "button";
    rollback.dataset.transactionId = transactionId;
    rollback.addEventListener("click", () => {
      rollback.disabled = true;
      rollback.textContent = "回滚中…";
      ahpNotify(null, "dispatchAction", {
        channel: entry.channel,
        clientSeq: Date.now(),
        action: { type: "halter/rollbackRequest", transactionId },
      }).catch((err) => {
        rollback.disabled = false;
        rollback.textContent = "重试回滚";
        detail.textContent += `\n[rollback failed] ${err && err.message ? err.message : String(err)}`;
      });
    });
    node.append(rollback);
  }
  entry.events.scrollTop = entry.events.scrollHeight;
}

function ahpApplyAction(st, params) {
  const entry = st.cards.get(params.channel);
  if (!entry) return;
  const a = params.action || {};
  if (a.type === "chat/delta") {
    entry.output.textContent += a.content || "";
    entry.output.classList.add("streaming");
    entry.output.scrollTop = entry.output.scrollHeight;
  } else if (a.type === "chat/turnComplete") {
    ahpFinishCard(entry, "done");
  } else if (a.type === "halter/planChanged") {
    renderDispatchPlan(entry, a.plan || {});
  } else if (a.type === "halter/planEvidence") {
    entry.planEvidence = a.evidence || {};
    renderDispatchPlan(entry);
  } else if (a.type === "halter/approvalRequest") {
    const approval = a.approval || {};
    updateApprovalHistory(entry, {
      id: approval.id,
      tool: approval.tool,
      summary: approval.summary,
      status: "pending",
    });
    const box = el("div", "dispatch-approval");
    const title = el("div", "dispatch-approval-title",
      `等待写入审批：${approval.summary || approval.tool || "apply_patch"}`);
    const rawInput = approval.input || {};
    const inputText = rawInput.patch !== undefined
      ? String(rawInput.patch)
      : JSON.stringify(rawInput, null, 2);
    const patch = el("pre", "dispatch-approval-patch", inputText);
    const actions = el("div", "dispatch-approval-actions");
    const approve = el("button", "btn", "批准应用");
    const deny = el("button", "btn danger", "拒绝");
    const respond = (approved) => {
      [approve, deny].forEach((btn) => { btn.disabled = true; });
      approve.textContent = approved ? "已批准" : "已拒绝";
      deny.textContent = approved ? "已批准" : "已拒绝";
      ahpNotify(null, "dispatchAction", {
        channel: params.channel,
        clientSeq: Date.now(),
        action: {
          type: "halter/approvalResponse",
          approvalId: approval.id,
          approved,
        },
      }).catch((err) => {
        [approve, deny].forEach((btn) => { btn.disabled = false; });
        approve.textContent = "重试批准";
        deny.textContent = "重试拒绝";
        entry.output.textContent += `\n[approval failed] ${err && err.message ? err.message : String(err)}\n`;
      });
    };
    approve.addEventListener("click", () => respond(true));
    deny.addEventListener("click", () => respond(false));
    actions.append(approve, deny);
    box.dataset.approvalId = approval.id || "";
    box.append(title, patch, actions);
    entry.root.append(box);
    box.scrollIntoView({ block: "nearest" });
  } else if (a.type === "halter/approvalResult") {
    updateApprovalHistory(entry, {
      id: a.approvalId,
      status: a.approved ? "approved" : "denied",
      decidedAt: new Date().toISOString(),
    });
    entry.root.querySelectorAll(".dispatch-approval").forEach((box) => {
      if (box.dataset.approvalId !== a.approvalId) return;
      const buttons = box.querySelectorAll("button");
      buttons.forEach((btn) => { btn.disabled = true; });
      if (buttons[0]) buttons[0].textContent = a.approved ? "已批准" : "已拒绝";
      if (buttons[1]) buttons[1].textContent = a.approved ? "已批准" : "已拒绝";
      box.classList.add(a.approved ? "approved" : "denied");
    });
  } else if (a.type === "halter/rollbackResult") {
    entry.events.querySelectorAll(".dispatch-event-rollback").forEach((btn) => {
      if (btn.dataset.transactionId !== a.transactionId) return;
      btn.disabled = true;
      btn.textContent = a.ok ? "已回滚" : "回滚失败";
      btn.classList.add(a.ok ? "ok" : "err");
    });
  } else if (a.type === "halter/toolCall") {
    updateDispatchToolEvent(entry, a.part || {}, false);
  } else if (a.type === "halter/toolResult") {
    updateDispatchToolEvent(entry, a.part || {}, true);
  } else if (a.type === "chat/error") {
    const err = a.part && a.part.error ? `${a.part.error.errorType}: ${a.part.error.message}\n` : "unknown error\n";
    entry.output.textContent += err;
    ahpFinishCard(entry, "failed");
  }
}

function ahpFinishCard(entry, status) {
  if (state.ahp) state.ahp.cards.delete(entry.channel);
  entry.status.replaceWith(dispatchStatusBadge(status));
  entry.output.classList.remove("streaming");
  entry.root.classList.add("finished");
  entry.root.querySelectorAll(".dispatch-cancel-btn").forEach((btn) => {
    btn.disabled = true;
    btn.textContent = status === "done" ? "已完成" : "已结束";
  });
}

function addAhpCancelButton(entry, chatUri) {
  const button = el("button", "btn danger dispatch-cancel-btn", "取消");
  button.type = "button";
  button.addEventListener("click", () => {
    button.disabled = true;
    button.textContent = "取消中…";
    ahpNotify(null, "dispatchAction", {
      channel: chatUri,
      clientSeq: Date.now(),
      action: { type: "chat/turnCancelled" },
    }).catch((err) => {
      button.disabled = false;
      button.textContent = "重试取消";
      entry.output.textContent += `\n[cancel failed] ${err && err.message ? err.message : String(err)}\n`;
    });
  });
  entry.root.querySelector(".dispatch-card-head")?.append(button);
}

function uuidAhp(kind) {
  const id = (window.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `ahp-${kind}:/${id}`;
}

async function restoreNativeChat(st, project) {
  const projectUri = project.startsWith("/") ? "file://" + project : project;
  const session = (st.sessions || []).find((s) =>
    s.provider === "halter" &&
    (s.workingDirectories || []).some((dir) => dir === projectUri)
  );
  if (!session) return undefined;
  try {
    const subscribed = await ahpRequest(st, "subscribe", { channel: session.resource });
    const state = ((subscribed.snapshot || {}).state || {});
    const chatUri = state.defaultChat ||
      (state.chats && state.chats[0] && state.chats[0].resource);
    if (!chatUri) return undefined;
    const chatSubscription = await ahpRequest(st, "subscribe", { channel: chatUri });
    const chatState = ((chatSubscription.snapshot || {}).state || {});
    return {
      sessionUri: session.resource,
      chatUri,
      restored: true,
      currentPlan: chatState.currentPlan || null,
      approvalHistory: chatState.approvalHistory || [],
      planEvidence: chatState.planEvidence || {},
      auditSessionId: chatState.auditSessionId || null,
    };
  } catch {
    return undefined;
  }
}

async function dispatchViaAhp(targets, prompt, project, mode) {
  const st = await ensureAhp();
  const outcomes = await Promise.allSettled(targets.map(async (t) => {
    const provider = t.tool;
    const model = t.model || state.dispatchModels[provider] || undefined;
    const writeEnabled = !!($("dispatch-write") && $("dispatch-write").checked);
    const effectiveMode = provider === "halter" && writeEnabled ? "workspace-write" : mode;
    const chatKey = `${provider}:${project}`;
    // Native conversations keep model/tool history in the AHP host, so reuse
    // the chat for follow-up turns. External CLIs are still one-shot processes.
    let channels = provider === "halter" ? st.nativeChats.get(chatKey) : undefined;
    if (!channels && provider === "halter") {
      channels = await restoreNativeChat(st, project);
    }
    if (!channels) {
      const sessionUri = uuidAhp("session");
      const chatUri = uuidAhp("chat");
      await ahpRequest(st, "createSession", {
        channel: sessionUri,
        provider,
        workingDirectories: [project.startsWith("/") ? "file://" + project : project],
      });
      await ahpRequest(st, "createChat", { channel: sessionUri, chat: chatUri });
      const subscribed = await ahpRequest(st, "subscribe", { channel: chatUri });
      channels = {
        sessionUri, chatUri,
        auditSessionId: ((subscribed.snapshot || {}).state || {}).auditSessionId || null,
      };
      if (provider === "halter") st.nativeChats.set(chatKey, channels);
    }
    const { chatUri } = channels;

    const card = makeDispatchCard({
      tool: provider, model, prompt, project, mode: effectiveMode,
    });
    card.channel = chatUri;
    card.auditSessionId = channels.auditSessionId || null;
    card.planEvidence = channels.planEvidence || {};
    if (channels.currentPlan) renderDispatchPlan(card, channels.currentPlan);
    for (const record of channels.approvalHistory || []) updateApprovalHistory(card, record);
    st.cards.set(chatUri, card);
    addAhpCancelButton(card, chatUri);
    results.push(card);

    await ahpNotify(st, "dispatchAction", {
      channel: chatUri, clientSeq: 1,
      action: {
        type: "chat/turnStarted",
        turnId: `t-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`}`,
        startedAt: new Date().toISOString(),
        message: {
          text: prompt,
          origin: { kind: "user" },
          ...(model && model !== "default" ? { model: { id: model } } : {}),
          halter: { mode: effectiveMode },
        },
      },
    });
    return card;
  }));
  const results = [];
  const failures = [];
  outcomes.forEach((outcome, index) => {
    if (outcome.status === "fulfilled") results.push(outcome.value);
    else failures.push(`@${targets[index].tool}: ${errorDetail(outcome.reason)}`);
  });
  if (failures.length) {
    const error = new Error(`AHP 派发部分失败（不会自动重发已成功的目标）：\n${failures.join("\n")}`);
    error.anySucceeded = results.length > 0;
    throw error;
  }
  return results;
}

function makeDispatchCard({ tool, model, prompt, project, mode }) {
  const list = $("dispatch-tasks");
  const root = el("div", "dispatch-card");
  const head = el("div", "dispatch-card-head");
  const badge = el("span", "dispatch-tool-badge");
  badge.style.setProperty("--c", toolColor(tool));
  badge.textContent = "@" + tool;
  const title = el("span", "dispatch-prompt", prompt);
  const status = dispatchStatusBadge("running");
  head.append(badge, title, status);
  if (model) head.append(el("span", "dispatch-model-tag", model));
  if (mode === "yolo") head.append(el("span", "dispatch-yolo-tag", "yolo"));
  if (mode === "workspace-write") head.append(el("span", "dispatch-yolo-tag", "write"));
  const meta = el("div", "dispatch-card-meta");
  meta.append(el("span", "", shortenPath(project)));
  meta.append(el("span", "", new Date().toISOString()));
  const plan = el("div", "dispatch-plan empty");
  const output = el("pre", "dispatch-output", "");
  const events = el("div", "dispatch-events");
  root.append(head, meta, plan, output, events);
  list.prepend(root);
  return { root, output, events, plan, status };
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
    let dispatched = false;
    try {
      await dispatchViaAhp(targets, prompt, project, mode);
      dispatched = true;
    } catch (ahpErr) {
      if (targets.some((t) => t.tool === "halter") || dispatched || ahpErr?.anySucceeded) {
        showError(errBox, ahpErr);
        return;
      }
      const dbg = document.createElement("div");
      dbg.className = "dispatch-mention-hint none";
      dbg.textContent = "AHP 直连失败已回退: " + errorDetail(ahpErr);
      $("dispatch-tasks").prepend(dbg);
    }

    if (dispatched) {
      $("dispatch-input").value = "";
      $("dispatch-harness-select").value = "";
      renderDispatchSelectors();
      updateDispatchHint();
      return;
    }

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
    $("halter-version").textContent = `halter ${v.version}`;
  } catch (err) {
    $("halter-version").textContent = "halter 不可用";
    $("halter-version").title = typeof err === "string" ? err : JSON.stringify(err);
  }
  loadOverview();
})();
