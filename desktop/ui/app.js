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

function showError(box, err) {
  box.textContent = typeof err === "string" ? err : JSON.stringify(err, null, 2);
  box.classList.remove("hidden");
}

/* ---------------- view switching ---------------- */

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    $(`view-${btn.dataset.view}`).classList.add("active");
    if (btn.dataset.view === "overview") loadOverview();
    if (btn.dataset.view === "sessions" && !state.sessionsLoaded) loadSessions();
  });
});

/* ---------------- overview ---------------- */

const LAYERS = [
  ["skills", "skills"],
  ["agents", "agents"],
  ["mcp_servers", "MCP"],
  ["plugins", "plugins"],
  ["hooks", "hooks"],
  ["sessions", "sessions"],
];

async function loadOverview() {
  $("scan-error").classList.add("hidden");
  $("scan-loading").classList.remove("hidden");
  $("overview-content").classList.add("hidden");
  let data;
  try {
    data = await invoke("hcm_scan");
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
  // doctor
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

  // tools grid
  const tools = (data.inventory || []).filter((t) => t.installed);
  $("tools-heading").textContent = `工具（${tools.length}）`;
  const grid = $("tools-grid");
  grid.replaceChildren();
  for (const tool of tools) {
    const card = el("div", "tool-card");
    const header = el("div", "tool-card-header");
    header.append(el("div", "tool-name", tool.display || tool.tool));
    header.append(el("div", "tool-key", tool.tool));
    card.append(header);

    const chips = el("div", "layer-chips");
    for (const [key, label] of LAYERS) {
      const n = Array.isArray(tool[key]) ? tool[key].length : 0;
      const chip = el("span", `count-chip${n > 0 ? " on" : ""}`, `${label} ${n}`);
      chips.append(chip);
    }
    card.append(chips);
    grid.append(card);
  }
}

$("btn-refresh-scan").addEventListener("click", loadOverview);

/* ---------------- sessions ---------------- */

const state = {
  sessionsLoaded: false,
  sessions: [],
  searchResults: null,
};

function currentProject() {
  return $("project-input").value.trim() || ".";
}

async function loadSessions() {
  const list = $("sessions-list");
  list.replaceChildren(el("div", "loading", "读取会话…"));
  let data;
  try {
    data = await invoke("hcm_sessions_list", { project: currentProject(), limit: 200 });
  } catch (err) {
    list.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = typeof err === "string" ? err : JSON.stringify(err);
    list.append(box);
    return;
  }
  state.sessionsLoaded = true;
  state.sessions = data.sessions || [];
  state.searchResults = null;
  renderSessionList(state.sessions);
}

function renderSessionList(items) {
  const list = $("sessions-list");
  list.replaceChildren();
  if (!items.length) {
    list.append(el("div", "loading", "没有找到会话"));
    return;
  }
  for (const s of items) {
    const item = el("div", "session-item");
    item.dataset.ref = s.ref;

    const title = el("div", "session-title", s.title || s.ref);
    const meta = el("div", "session-meta");
    meta.append(el("span", "tool-badge", s.tool));
    meta.append(el("span", "meta-item", fmtDate(s.updated_at)));
    meta.append(el("span", "meta-item", `${s.message_count} 条`));
    if (s.branch) meta.append(el("span", "meta-item", s.branch));

    item.append(title, meta);
    item.addEventListener("click", () => selectSession(s, item));
    list.append(item);
  }
}

async function selectSession(session, itemEl) {
  document.querySelectorAll(".session-item.selected").forEach((n) => n.classList.remove("selected"));
  if (itemEl) itemEl.classList.add("selected");

  const detail = $("session-detail");
  detail.className = "session-detail";
  detail.replaceChildren(el("div", "loading", "读取会话详情…"));

  let data;
  try {
    data = await invoke("hcm_sessions_show", {
      ref: session.ref,
      project: currentProject(),
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
  badges.append(el("span", "tool-badge", s.tool));
  if (s.model) badges.append(el("span", "meta-item", s.model));
  if (s.branch) badges.append(el("span", "meta-item", `⑂ ${s.branch}`));
  header.append(badges);
  detail.append(header);

  const grid = el("div", "meta-grid");
  grid.append(metaCell("Ref", s.ref));
  grid.append(metaCell("更新", fmtDate(s.updated_at)));
  grid.append(metaCell("消息数", s.message_count));
  grid.append(metaCell("后端", s.backend));
  detail.append(grid);

  // handoff actions
  const actions = el("div", "detail-actions");
  const handoffBtn = el("button", "btn", "生成交接上下文");
  handoffBtn.addEventListener("click", async () => {
    handoffBtn.disabled = true;
    handoffBtn.textContent = "生成中…";
    try {
      const text = await invoke("hcm_sessions_context", {
        ref: s.ref,
        project: currentProject(),
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

  // transcript — parse "## STAMP ROLE" headers into styled spans (DOM APIs only, no innerHTML)
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

$("btn-refresh-sessions").addEventListener("click", loadSessions);
$("project-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadSessions();
});

/* session search */

async function runSearch() {
  const query = $("search-input").value.trim();
  if (!query) return;
  const list = $("sessions-list");
  list.replaceChildren(el("div", "loading", "搜索中…"));
  let data;
  try {
    data = await invoke("hcm_sessions_search", {
      query,
      project: currentProject(),
      limit: 50,
    });
  } catch (err) {
    list.replaceChildren();
    const box = el("div", "error-box");
    box.textContent = typeof err === "string" ? err : JSON.stringify(err);
    list.append(box);
    return;
  }
  const items = (data.hits || []).map((h) => h.session);
  list.replaceChildren();
  if (!items.length) {
    list.append(el("div", "loading", `没有匹配“${query}”的会话`));
    return;
  }
  for (let i = 0; i < items.length; i++) {
    const s = items[i];
    const hit = data.hits[i];
    const item = el("div", "session-item");
    item.append(el("div", "session-title", s.title || s.ref));
    const snippet = el("div", "meta-item");
    snippet.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
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

/* Two-step confirmation for the destructive apply action. */
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
