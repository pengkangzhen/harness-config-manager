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
  scanCache: null,
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
  state.scanCache = null;
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
    const btn = el("button", `layer-tab${state.matrixLayer === layer.key ? " active" : ""}`, t(layer.labelKey));
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
    grid.append(nameCell);
    for (const tool of tools) {
      const st = row.statuses.get(tool.tool) || "missing";
      const cell = el("div", `mx-cell ${st}`, CELL_GLYPH[st] || "○");
      cell.title = t("mx.cellTitle", { tool: tool.tool, status: cellStatusText(st) });
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
      renderMatrix(state.scanCache);
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
  out.textContent = apply ? t("sy.applying") : t("sy.planning");
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
    out.textContent = t("sy.invokeFailed") + (typeof err === "string" ? err : JSON.stringify(err, null, 2));
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
  btn.textContent = t("sy.apply");
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
    btn.textContent = t("sy.applyArmed");
    applyTimer = setTimeout(disarmApply, 5000);
    return;
  }
  runSync(true);
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
    head.append(el("span", `legend-dot ${st.cls}`, st.mark));
    // 标题 = 对应的记忆文件名（CLAUDE.md / AGENTS.md…），副注 = 工具名
    head.append(el("span", "mem-tool-file", item.path.split("/").pop()));
    head.append(el("span", "mem-tool-display", item.display));
    head.append(el("span", "mem-tool-meta", `${t(st.key)} · ${item.size}B`));
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
    card.append(el("div", "mem-tool-meta dim", item.path));
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

/* ---------------- language ---------------- */

$("lang-zh").addEventListener("click", () => setLang("zh"));
$("lang-en").addEventListener("click", () => setLang("en"));

// 静态文案由 i18n.js 的 applyI18n() 刷新；这里重渲染各视图的动态文案。
// armed 的 Apply 按钮文案会被 applyI18n 覆盖，需按当前状态重设。
document.addEventListener("halter:langchange", () => {
  if (applyArmed) $("btn-sync-apply").textContent = t("sy.applyArmed");
  if (state.scanCache) {
    renderOverview(state.scanCache);
    renderMatrixLayerTabs();
    renderMatrix(state.scanCache);
  }
  if (state.sessionsLoaded) {
    renderProjectInput();
    renderToolFilter();
    renderSessions(state.sessions);
  }
  if (memoryState.snap) renderMemory();
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
