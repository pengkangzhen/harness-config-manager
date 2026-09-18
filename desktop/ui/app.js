// HCM desktop frontend — no build step, talks to Rust commands via Tauri IPC.
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

function showError(box, err) {
  box.textContent = typeof err === "string" ? err : JSON.stringify(err, null, 2);
  box.classList.remove("hidden");
}

const TOOL_COLORS = {
  claude: "#d97757",
  codex: "#10a37f",
  zcode: "#a78bfa",
  opencode: "#4cc2ff",
  cursor: "#5ba8ff",
  gemini: "#7bd88f",
  vscode: "#5aa0e8",
  "copilot-cli": "#8f9bb3",
  continue: "#c792ea",
};
const toolColor = (tool) => TOOL_COLORS[tool] || "#8b96b8";
const basename = (p) => (p || "").split("/").filter(Boolean).pop() || p || "-";

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
};

/* ---------------- view switching ---------------- */

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    $(`view-${btn.dataset.view}`).classList.add("active");
    if (btn.dataset.view === "overview") loadOverview();
    if (btn.dataset.view === "matrix") loadMatrix();
    if (btn.dataset.view === "sessions" && !state.sessionsLoaded) loadSessionsView();
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
  const data = await invoke("hcm_scan");
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
    present: "已存在（非 hcm 管理）",
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

function currentProject() {
  // 维度面板接管项目选择后，这里仅作为 CLI --project 标记参数
  return ".";
}

async function loadSessionsView() {
  await loadSessionProjects();
  await loadSessions();
}

/* ---------- dimension 3: project picker ---------- */

async function loadSessionProjects() {
  const picker = $("project-picker");
  picker.replaceChildren(el("div", "loading", "读取项目分布…"));
  let data;
  try {
    data = await invoke("hcm_sessions_projects", { project: currentProject() });
  } catch (err) {
    picker.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = typeof err === "string" ? err : JSON.stringify(err);
    picker.append(box);
    return;
  }
  state.projects = data.projects || [];
  renderProjectSelectButton();
}

let projectDropdownOpen = false;

function toggleProjectDropdown(open) {
  projectDropdownOpen = open;
  $("project-dropdown").classList.toggle("hidden", !open);
  $("project-dropdown-btn").classList.toggle("open", open);
  if (open) {
    $("project-search").value = "";
    renderProjectDropdownList("");
    $("project-search").focus();
  }
}

function selectProject(path) {
  state.projectFilter = path;
  toggleProjectDropdown(false);
  renderProjectSelectButton();
  loadSessions();
}

function renderProjectSelectButton() {
  const btn = $("project-dropdown-btn");
  btn.replaceChildren();
  if (state.projectFilter === null) {
    btn.append(el("span", "facet-select-label", `全部项目 · ${state.projects.length} 个`));
  } else {
    const p = state.projects.find((x) => x.path === state.projectFilter);
    btn.append(el("span", "facet-select-label", p ? (p.name || basename(p.path)) : basename(state.projectFilter)));
    if (p) {
      const dots = el("span", "project-dots");
      for (const tool of p.tools || []) {
        const dot = el("span", "filter-dot");
        dot.style.backgroundColor = toolColor(tool);
        dot.title = tool;
        dots.append(dot);
      }
      btn.append(dots);
    }
  }
  btn.append(el("span", "facet-caret", "▾"));
}

function renderProjectDropdownList(filter) {
  const listEl = $("project-dropdown-list");
  listEl.replaceChildren();
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
  for (const p of projects) {
    const row = el("div", `project-item${state.projectFilter === p.path ? " selected" : ""}`);
    const head = el("div", "project-head");
    head.append(el("span", "project-name", p.name || basename(p.path)));
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
    sub.append(el("span", "project-last", relTime(p.last_activity)));
    row.append(sub);

    row.title = p.path;
    row.addEventListener("click", () => selectProject(p.path));
    listEl.append(row);
  }
  if (needle && !projects.length) {
    listEl.append(el("div", "loading", "没有匹配的项目"));
  }
}

$("project-dropdown-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  toggleProjectDropdown(!projectDropdownOpen);
});
$("project-search").addEventListener("input", (e) => renderProjectDropdownList(e.target.value));
document.addEventListener("click", (e) => {
  if (projectDropdownOpen && !e.target.closest(".facet-dropdown-wrap")) {
    toggleProjectDropdown(false);
  }
});

async function loadSessions() {
  const list = $("sessions-list");
  list.replaceChildren(el("div", "loading", "读取会话…"));
  const allProjects = state.projectFilter === null;
  let data;
  try {
    data = await invoke("hcm_sessions_list", {
      project: currentProject(),
      limit: 500,
      allProjects,
    });
  } catch (err) {
    list.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = typeof err === "string" ? err : JSON.stringify(err);
    list.append(box);
    return;
  }
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
  $("btn-view-timeline").classList.toggle("active", mode === "timeline");
  renderSessions(state.sessions);
}

$("btn-view-list").addEventListener("click", () => setViewMode("list"));
$("btn-view-timeline").addEventListener("click", () => setViewMode("timeline"));

/* ---------- session detail ---------- */

async function selectSession(session, itemEl) {
  document.querySelectorAll(".session-item.selected").forEach((n) => n.classList.remove("selected"));
  if (itemEl) itemEl.classList.add("selected");

  const detail = $("session-detail");
  detail.className = "session-detail";
  detail.replaceChildren(el("div", "loading", "读取会话详情…"));

  const project = session.project || state.projectFilter || ".";
  let data;
  try {
    data = await invoke("hcm_sessions_show", {
      ref: session.ref,
      project,
      tail: 120,
    });
  } catch (err) {
    detail.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = typeof err === "string" ? err : JSON.stringify(err);
    detail.append(box);
    return;
  }

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
      const text = await invoke("hcm_sessions_context", {
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
      box.textContent = typeof err === "string" ? err : JSON.stringify(err);
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
  const query = $("search-input").value.trim();
  if (!query) return;
  const list = $("sessions-list");
  list.replaceChildren(el("div", "loading", "搜索中…"));
  const allProjects = state.projectFilter === null;
  let data;
  try {
    data = await invoke("hcm_sessions_search", {
      query,
      project: currentProject(),
      limit: 50,
      allProjects,
    });
  } catch (err) {
    list.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = typeof err === "string" ? err : JSON.stringify(err);
    list.append(box);
    return;
  }
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
    const result = await invoke("hcm_sync", { apply, layers: selectedLayers() });
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
    const v = await invoke("hcm_version");
    $("hcm-version").textContent = `hcm ${v.version}`;
  } catch (err) {
    $("hcm-version").textContent = "hcm 不可用";
    $("hcm-version").title = typeof err === "string" ? err : JSON.stringify(err);
  }
  loadOverview();
})();
