// Halter desktop i18n — zh / en, no build step.
// Static text is driven by data-i18n attributes (see applyI18n); dynamic text
// calls t() directly. Language persists in localStorage ("halter-lang"),
// default zh. app.js re-renders the active view on "halter:langchange".
"use strict";

const I18N = {
  zh: {
    "app.title": "Halter — Harness 配置管理器",

    "nav.overview": "总览",
    "nav.matrix": "矩阵",
    "nav.sessions": "会话",
    "nav.sync": "同步",
    "nav.memory": "记忆",

    "mem.heading": "用户级记忆",
    "mem.refresh": "↻ 刷新",
    "mem.note": "所有 AI 编码工具共享的全局指令（CLAUDE.md / AGENTS.md / GEMINI.md…）。编辑保存的是事实源文件；已链接的工具侧副本即时生效，未链接的副本需到「同步」面板分发。",
    "mem.loading": "正在读取记忆事实源…",
    "mem.open": "打开",
    "mem.content": "库内容",
    "mem.badgeOk": "已存在",
    "mem.badgeMissing": "不存在",
    "mem.stateLinked": "已链接",
    "mem.stateLocal": "本地文件",
    "mem.stateDrift": "与库分歧",
    "mem.stateMissing": "缺失",
    "mem.diffSummary": "展开与库的差异",
    "mem.identical": "内容与库一致",
    "mem.toolsHeading": "各工具侧副本",
    "lang.label": "语言",

    "ov.heading": "总览",
    "ov.refresh": "↻ 刷新",
    "ov.scanning": "正在扫描本机 AI 编码工具…",
    "ov.doctor": "健康检查",
    "ov.tools": "Harness",
    "ov.toolsCount": "Harness（{n}）",
    "ov.badgeEditor": "编辑器",

    "layer.skills": "skills",
    "layer.agents": "agents",
    "layer.memory": "记忆",
    "layer.mcp": "MCP",
    "layer.plugins": "插件",
    "layer.hooks": "hooks",
    "layer.sessions": "sessions",

    "mlayer.skills": "Skills",
    "mlayer.agents": "Subagents",
    "mlayer.memory": "Memory",
    "mlayer.mcp": "MCP",
    "mlayer.plugins": "插件",
    "mlayer.hooks": "Hooks",

    "mx.heading": "配置矩阵",
    "mx.harnessOnly": "仅 AI Harness",
    "mx.gapsOnly": "仅显示缺口",
    "mx.refresh": "↻ 刷新",
    "mx.building": "正在构建 Harness × 配置矩阵…",
    "mx.legendSynced": "已同步（symlink）",
    "mx.legendPresent": "已存在（非 halter 管理）",
    "mx.legendPartial": "家族部分成员存在",
    "mx.legendMissing": "缺少",
    "mx.cellPartial": "部分成员存在",
    "mx.scopeHarness": "{n} 个 AI Harness",
    "mx.scopeAll": "{n} 个工具（含编辑器）",
    "mx.summary": "{label}：{rows} 个条目 × {scope} · 缺口 {gaps} 处",
    "mx.summaryGapsOnly": " · 仅显示缺口的 {n} 条",
    "mx.summaryFamilies": " · 已聚合 {n} 个家族",
    "mx.noGaps": "没有缺口 — 所有工具均覆盖",
    "mx.noData": "没有数据",
    "mx.familyTitle": "{root} 家族（{n} 个成员）",
    "mx.cellTitle": "{tool}：{status}",
    "mx.cellTitleFamily": "{tool}：{status}（{present}/{total}）",
    "mx.cellClickHint": "点击同步",
    "mx.syncDone": "已同步 → {tool}",
    "mx.syncFailed": "同步失败（{tool}）",
    "mx.syncNoop": "无变更（内容与事实源一致，或冲突被跳过 — 详见输出）",

    "hv.noDesc": "（SKILL.md 未提供 description）",
    "hv.linked": "库链接",
    "hv.familyName": "{root} 家族",
    "hv.familyCount": "{n} 个成员",
    "hv.memberNoDesc": "（无描述）",

    "ss.heading": "跨助手会话",
    "ss.refresh": "↻ 刷新",
    "ss.searchPlaceholder": "搜索会话内容，回车执行…",
    "ss.list": "列表",
    "ss.timeline": "时间线",
    "ss.sortRecent": "↓ 按最近更新排序",
    "ss.sortRelevance": "↓ 按匹配度排序",
    "ss.dayCount": "{n} 个会话",
    "ss.emptyDetailTitle": "选择左侧的一个会话",
    "ss.emptyDetailSub": "查看元信息与最近对话，可生成交接上下文",
    "ss.projectInputPlaceholder": "输入项目名/路径筛选，回车选中…",
    "ss.projectClearTitle": "清除，回到全部项目",
    "ss.projectClearAria": "清除项目筛选",
    "ss.projectLoadFailed": "项目加载失败 — 点击重试",
    "ss.allProjectsInputAll": "全部项目（{n} 个 + {m} 个历史/临时目录）— 输入即筛选",
    "ss.allProjectsInput": "全部项目（{n} 个）— 输入即筛选",
    "ss.projectInputFilter": "输入项目名/路径筛选…",
    "ss.allProjects": "全部项目",
    "ss.projectsSessions": "{n} 项目 · {m} 会话",
    "ss.nSessions": "{n} 会话",
    "ss.kind.temp": "临时",
    "ss.kind.dated": "会话目录",
    "ss.kind.virtual": "内部",
    "ss.kind.stale": "已删除",
    "ss.othersGroupHeader": "临时 / 自动会话目录 / 已删除（{n}）",
    "ss.rawPathHint": "回车使用路径：{path}",
    "ss.noMatchProjects": "没有匹配的项目",
    "ss.loadingSessions": "读取会话…",
    "ss.allTools": "全部 {n}",
    "ss.noSessionsFiltered": "所选助手没有会话",
    "ss.noSessions": "没有找到会话",
    "ss.nMessages": "{n} 条",
    "ss.loadingDetail": "读取会话详情…",
    "ss.metaProject": "项目",
    "ss.metaUpdated": "更新",
    "ss.metaMessages": "消息数",
    "ss.metaBackend": "后端",
    "ss.handoffBtn": "生成交接上下文",
    "ss.handoffBusy": "生成中…",
    "ss.handoffTitle": "交接上下文（已脱敏）",
    "ss.recentTranscript": "最近对话",
    "ss.noTranscript": "（无 transcript）",
    "ss.traceViewChat": "对话",
    "ss.traceViewRaw": "原始",
    "ss.traceCopy": "复制原文",
    "ss.traceCopied": "已复制 ✓",
    "ss.traceCopyFailed": "复制失败",
    "ss.traceTotal": "共 {n} 条",
    "ss.traceTail": "显示最近 {n} 条",
    "ss.traceToggleHint": "点击展开/折叠工具结果",
    "ss.traceEmptyTool": "（空结果）",
    "ss.searching": "搜索中…",
    "ss.searchSuffix": "搜索“{q}”",
    "ss.noSearchHit": "没有匹配“{q}”的会话",
    "ss.countTools": "{n} 个助手",

    "rt.justNow": "刚刚",
    "rt.minAgo": "{n} 分钟前",
    "rt.today": "今天 {time}",
    "rt.yesterday": "昨天 {time}",
    "rt.daysAgo": "{n} 天前",
    "rt.monthDay": "{m}月{d}日",
    "date.today": "今天",
    "date.yesterday": "昨天",
    "date.weekday": "{date} · 周{w}",

    "sy.heading": "同步",
    "sy.note": "默认 dry-run：只生成计划、不写任何文件。Apply 才会实际执行（skills 覆盖前自动备份）。",
    "sy.preview": "预览 dry-run",
    "sy.apply": "Apply 实际执行",
    "sy.applyArmed": "⚠ 再点一次确认写入",
    "sy.applying": "正在执行 sync --apply（写入变更）…\n",
    "sy.planning": "正在生成 dry-run 计划…\n",
    "sy.invokeFailed": "调用失败：",

    "bt.unavailable": "halter 不可用",
    "bt.mismatch": "运行时版本 {version} 与桌面打包期望 {desktop} 不一致；release 不应携带旧 sidecar，请重新构建",

    // doctor 健康检查条目（code 与 src/harness_config_manager/doctor.py 的 ZH_TEXT 对应）
    "doctor.parse_ok": "可解析",
    "doctor.json_parse_error": "JSON 解析失败: {err}",
    "doctor.toml_parse_error": "TOML 解析失败: {err}",
    "doctor.broken_link": "断链（指向 {target} 不存在）",
    "doctor.library_ok": "{n} 个 skill",
    "doctor.library_missing": "不存在（首次 sync --apply 时创建）",
    "doctor.memory_library_ok": "{n}B",
    "doctor.memory_library_missing": "不存在（首次 sync --apply 时收养/创建）",
    "doctor.mcp_secret_missing": "密钥变量未定义: {vars}",
    "doctor.dead_command": "command 指向的 {command} 不存在（死配置，建议删除）",
  },

  en: {
    "app.title": "Halter — Harness Config Manager",

    "nav.overview": "Overview",
    "nav.matrix": "Matrix",
    "nav.sessions": "Sessions",
    "nav.sync": "Sync",
    "nav.memory": "Memory",

    "mem.heading": "Global memory",
    "mem.refresh": "↻ Refresh",
    "mem.note": "Global instructions shared by every AI coding tool (CLAUDE.md / AGENTS.md / GEMINI.md…). Editing saves the source-of-truth file; linked tool-side copies take effect instantly, unlinked copies need a sync from the Sync panel.",
    "mem.loading": "Reading memory source of truth…",
    "mem.open": "Open",
    "mem.content": "Library content",
    "mem.badgeOk": "exists",
    "mem.badgeMissing": "missing",
    "mem.stateLinked": "linked",
    "mem.stateLocal": "local file",
    "mem.stateDrift": "diverged from library",
    "mem.stateMissing": "missing",
    "mem.diffSummary": "Show diff vs library",
    "mem.identical": "Identical to library",
    "mem.toolsHeading": "Tool-side copies",
    "lang.label": "Language",

    "ov.heading": "Overview",
    "ov.refresh": "↻ Refresh",
    "ov.scanning": "Scanning installed AI coding tools…",
    "ov.doctor": "Health check",
    "ov.tools": "Harness",
    "ov.toolsCount": "Harness ({n})",
    "ov.badgeEditor": "Editor",

    "layer.skills": "skills",
    "layer.agents": "agents",
    "layer.memory": "Memory",
    "layer.mcp": "MCP",
    "layer.plugins": "Plugins",
    "layer.hooks": "hooks",
    "layer.sessions": "sessions",

    "mlayer.skills": "Skills",
    "mlayer.agents": "Subagents",
    "mlayer.memory": "Memory",
    "mlayer.mcp": "MCP",
    "mlayer.plugins": "Plugins",
    "mlayer.hooks": "Hooks",

    "mx.heading": "Config matrix",
    "mx.harnessOnly": "AI harnesses only",
    "mx.gapsOnly": "Gaps only",
    "mx.refresh": "↻ Refresh",
    "mx.building": "Building harness × config matrix…",
    "mx.legendSynced": "Synced (symlink)",
    "mx.legendPresent": "Present (not managed by halter)",
    "mx.legendPartial": "Family partially present",
    "mx.legendMissing": "Missing",
    "mx.cellPartial": "Some members present",
    "mx.scopeHarness": "{n} AI harnesses",
    "mx.scopeAll": "{n} tools (incl. editors)",
    "mx.summary": "{label}: {rows} entries × {scope} · {gaps} gaps",
    "mx.summaryGapsOnly": " · {n} rows with gaps shown",
    "mx.summaryFamilies": " · {n} families aggregated",
    "mx.noGaps": "No gaps — every tool covered",
    "mx.noData": "No data",
    "mx.familyTitle": "{root} family ({n} members)",
    "mx.cellTitle": "{tool}: {status}",
    "mx.cellTitleFamily": "{tool}: {status} ({present}/{total})",
    "mx.cellClickHint": "Click to sync",
    "mx.syncDone": "Synced → {tool}",
    "mx.syncFailed": "Sync failed ({tool})",
    "mx.syncNoop": "No changes (identical to the library, or a conflict was skipped — see output)",

    "hv.noDesc": "(no description in SKILL.md)",
    "hv.linked": "Library link",
    "hv.familyName": "{root} family",
    "hv.familyCount": "{n} members",
    "hv.memberNoDesc": "(no description)",

    "ss.heading": "Cross-assistant sessions",
    "ss.refresh": "↻ Refresh",
    "ss.searchPlaceholder": "Search sessions, press Enter…",
    "ss.list": "List",
    "ss.timeline": "Timeline",
    "ss.sortRecent": "↓ Sorted by last updated",
    "ss.sortRelevance": "↓ Best matches first",
    "ss.dayCount": "{n} sessions",
    "ss.emptyDetailTitle": "Select a session on the left",
    "ss.emptyDetailSub": "View metadata and the recent conversation; generate a handoff context",
    "ss.projectInputPlaceholder": "Type a name/path to filter, Enter to select…",
    "ss.projectClearTitle": "Clear, back to all projects",
    "ss.projectClearAria": "Clear project filter",
    "ss.projectLoadFailed": "Failed to load projects — click to retry",
    "ss.allProjectsInputAll": "All projects ({n} + {m} historical/temp dirs) — type to filter",
    "ss.allProjectsInput": "All projects ({n}) — type to filter",
    "ss.projectInputFilter": "Filter by project name/path…",
    "ss.allProjects": "All projects",
    "ss.projectsSessions": "{n} projects · {m} sessions",
    "ss.nSessions": "{n} sessions",
    "ss.kind.temp": "temp",
    "ss.kind.dated": "dated",
    "ss.kind.virtual": "internal",
    "ss.kind.stale": "deleted",
    "ss.othersGroupHeader": "Temp / auto session dirs / deleted ({n})",
    "ss.rawPathHint": "Enter to use path: {path}",
    "ss.noMatchProjects": "No matching projects",
    "ss.loadingSessions": "Loading sessions…",
    "ss.allTools": "All {n}",
    "ss.noSessionsFiltered": "No sessions for the selected assistants",
    "ss.noSessions": "No sessions found",
    "ss.nMessages": "{n} msgs",
    "ss.loadingDetail": "Loading session details…",
    "ss.metaProject": "Project",
    "ss.metaUpdated": "Updated",
    "ss.metaMessages": "Messages",
    "ss.metaBackend": "Backend",
    "ss.handoffBtn": "Generate handoff context",
    "ss.handoffBusy": "Generating…",
    "ss.handoffTitle": "Handoff context (sanitized)",
    "ss.recentTranscript": "Recent conversation",
    "ss.noTranscript": "(no transcript)",
    "ss.traceViewChat": "Chat",
    "ss.traceViewRaw": "Raw",
    "ss.traceCopy": "Copy raw",
    "ss.traceCopied": "Copied ✓",
    "ss.traceCopyFailed": "Copy failed",
    "ss.traceTotal": "{n} total",
    "ss.traceTail": "last {n} shown",
    "ss.traceToggleHint": "Click to expand/collapse tool result",
    "ss.traceEmptyTool": "(empty result)",
    "ss.searching": "Searching…",
    "ss.searchSuffix": "search “{q}”",
    "ss.noSearchHit": "No sessions matching “{q}”",
    "ss.countTools": "{n} assistants",

    "rt.justNow": "just now",
    "rt.minAgo": "{n} min ago",
    "rt.today": "Today {time}",
    "rt.yesterday": "Yesterday {time}",
    "rt.daysAgo": "{n} days ago",
    "rt.monthDay": "{m}/{d}",
    "date.today": "Today",
    "date.yesterday": "Yesterday",
    "date.weekday": "{date} · {w}",

    "sy.heading": "Sync",
    "sy.note": "Dry-run by default: only a plan is generated, nothing is written. Apply executes for real (skills are backed up before overwrite).",
    "sy.preview": "Preview dry-run",
    "sy.apply": "Apply for real",
    "sy.applyArmed": "⚠ Click again to confirm write",
    "sy.applying": "Running sync --apply (writing changes)…\n",
    "sy.planning": "Generating dry-run plan…\n",
    "sy.invokeFailed": "Invoke failed: ",

    "bt.unavailable": "halter unavailable",
    "bt.mismatch": "Runtime version {version} differs from the packaged desktop expectation {desktop}; the release must not ship a stale sidecar — rebuild it.",

    // doctor health-check entries (codes mirror ZH_TEXT in src/harness_config_manager/doctor.py)
    "doctor.parse_ok": "parseable",
    "doctor.json_parse_error": "JSON parse error: {err}",
    "doctor.toml_parse_error": "TOML parse error: {err}",
    "doctor.broken_link": "Broken link (target {target} does not exist)",
    "doctor.library_ok": "{n} skills",
    "doctor.library_missing": "Missing (created on first sync --apply)",
    "doctor.memory_library_ok": "{n}B",
    "doctor.memory_library_missing": "Missing (adopted/created on first sync --apply)",
    "doctor.mcp_secret_missing": "Undefined secret variables: {vars}",
    "doctor.dead_command": "command points to {command} which does not exist (dead config, consider removing)",
  },
};

const I18N_LANGS = ["zh", "en"];

let i18nLang = (() => {
  try {
    const saved = localStorage.getItem("halter-lang");
    if (I18N_LANGS.includes(saved)) return saved;
  } catch (e) { /* no localStorage: fall back to default */ }
  return "zh";
})();

function t(key, params) {
  let s = (I18N[i18nLang] && I18N[i18nLang][key]) || I18N.zh[key] || key;
  if (params) {
    for (const [k, v] of Object.entries(params)) s = s.split(`{${k}}`).join(String(v));
  }
  return s;
}

function applyI18n() {
  document.documentElement.lang = i18nLang === "zh" ? "zh-CN" : "en";
  document.title = t("app.title");
  for (const node of document.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
  for (const node of document.querySelectorAll("[data-i18n-placeholder]")) {
    node.placeholder = t(node.dataset.i18nPlaceholder);
  }
  for (const node of document.querySelectorAll("[data-i18n-title]")) {
    node.title = t(node.dataset.i18nTitle);
  }
  for (const node of document.querySelectorAll("[data-i18n-aria-label]")) {
    node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
  }
  for (const id of I18N_LANGS) {
    const btn = document.getElementById(`lang-${id}`);
    if (btn) btn.classList.toggle("active", id === i18nLang);
  }
}

function setLang(next) {
  if (!I18N_LANGS.includes(next) || next === i18nLang) return;
  i18nLang = next;
  try { localStorage.setItem("halter-lang", next); } catch (e) { /* ignore */ }
  applyI18n();
  document.dispatchEvent(new CustomEvent("halter:langchange"));
}

applyI18n();
