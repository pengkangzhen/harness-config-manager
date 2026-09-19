"""配置有用度评估：根据项目画像判定 Skills/Subagents/MCP/插件/Hooks 的适用性。

确定性规则引擎（不调 LLM）：先从项目文件提取信号（语言、框架、领域、云厂商），
再按「已知条目规则 + 名称/描述关键词匹配」逐层评估，输出 useful / maybe / useless
与可执行建议（保留 / 观察 / 移除或 exclude）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .model import ToolReport

# ---------------------------------------------------------------------------
# 项目画像


@dataclass(frozen=True)
class ProjectProfile:
    path: Path
    languages: frozenset[str] = frozenset()
    frameworks: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()
    clouds: frozenset[str] = frozenset()
    files: frozenset[str] = frozenset()
    is_repo: bool = False

    def has_any(self, *signals: str) -> bool:
        pool = set(self.languages) | set(self.frameworks) | set(self.domains) | set(self.clouds)
        return any(s in pool for s in signals)

    def all_signals(self) -> set[str]:
        return set(self.languages) | set(self.frameworks) | set(self.domains) | set(self.clouds)


def profile_project(root: Path, max_files: int = 4000) -> ProjectProfile:
    """扫描项目（≤3 层目录）提取技术信号。只读；文件数封顶防大仓库卡顿。"""
    root = root.expanduser().resolve(strict=False)
    names: set[str] = set()
    suffixes: dict[str, int] = {}
    count = 0
    queue: list[Path] = [root]
    for depth in range(4):
        nxt: list[Path] = []
        for d in queue:
            try:
                for child in d.iterdir():
                    name = child.name
                    if name.startswith(".") and name not in {".github", ".azure", ".git", ".vscode"}:
                        continue
                    count += 1
                    if count > max_files:
                        break
                    names.add(name)
                    if child.is_dir():
                        if depth < 3:
                            nxt.append(child)
                    else:
                        ext = child.suffix.lower()
                        suffixes[ext] = suffixes.get(ext, 0) + 1
            except OSError:
                continue
        queue = nxt

    languages: set[str] = set()
    frameworks: set[str] = set()
    domains: set[str] = set()
    clouds: set[str] = set()

    ext_lang = {
        ".py": "python", ".pyw": "python", ".ipynb": "python",
        ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin",
        ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
        ".swift": "swift", ".rb": "ruby", ".php": "php", ".cs": "csharp",
        ".sh": "shell", ".lua": "lua",
    }
    for ext, lang in ext_lang.items():
        if suffixes.get(ext):
            languages.add(lang)

    manifest_lang = {
        "pyproject.toml": "python", "requirements.txt": "python", "setup.py": "python",
        "package.json": "node", "tsconfig.json": "typescript", "Cargo.toml": "rust",
        "go.mod": "go", "pom.xml": "jvm", "build.gradle": "jvm", "build.gradle.kts": "jvm",
        "composer.json": "php", "Gemfile": "ruby",
    }
    for marker, lang in manifest_lang.items():
        if marker in names:
            languages.add(lang)

    if suffixes.get(".tex") or suffixes.get(".bib"):
        domains.add("academic")
    if "playwright.config.js" in names or "playwright.config.ts" in names:
        frameworks.add("playwright")
    if "tauri.conf.json" in names:
        frameworks.add("tauri")
    if "next.config.js" in names or "next.config.mjs" in names:
        frameworks.add("nextjs")
    if "vite.config.js" in names or "vite.config.ts" in names:
        frameworks.add("vite")
    if "docker-compose.yml" in names or "docker-compose.yaml" in names or "Dockerfile" in names:
        clouds.add("docker")
    if any(n.endswith(".bicep") for n in names) or "azure.yaml" in names or ".azure" in names:
        clouds.add("azure")
    if any(n.endswith(".tf") for n in names):
        clouds.add("terraform")
    if any(n.endswith(".tfvars") for n in names):
        clouds.add("terraform")
    if "AndroidManifest.xml" in names or ("app" in names and "build.gradle" in names):
        domains.add("android")
    if any(n.endswith(".xcodeproj") for n in names) or "Podfile" in names:
        domains.add("ios")
    if suffixes.get(".r"):
        domains.add("data-science")
    if languages:
        domains.add("code")

    return ProjectProfile(
        path=root,
        languages=frozenset(languages),
        frameworks=frozenset(frameworks),
        domains=frozenset(domains),
        clouds=frozenset(clouds),
        files=frozenset(n for n in names if "." in n or n in {"Makefile", "justfile", "Dockerfile"}),
        is_repo=(root / ".git").exists(),
    )


# ---------------------------------------------------------------------------
# 评估


@dataclass
class Assessment:
    layer: str            # skills / agents / mcp / plugins / hooks
    item: str
    verdict: str          # useful / maybe / useless
    reason: str
    suggestion: str       # 保留 / 观察 / 移除或 exclude
    tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "layer": self.layer, "item": self.item, "verdict": self.verdict,
            "reason": self.reason, "suggestion": self.suggestion, "tools": self.tools,
        }


VERDICT_LABEL = {"useful": "✓ 有用", "maybe": "? 可能", "useless": "✕ 无用"}
SUGGEST_KEEP = "保留"
SUGGEST_WATCH = "观察"
SUGGEST_REMOVE = "移除或 exclude"


def _kw_match(name: str, desc: str, profile: ProjectProfile,
              groups: dict[str, tuple[str, ...]]) -> tuple[str, str] | None:
    """按 keyword→signals 组匹配。名称命中优先于描述命中。

    返回 (verdict, reason)；未命中任何组返回 None。
    """
    for scope, text in (("名称", name.lower()), ("描述", desc.lower())):
        for kw, signals in groups.items():
            if kw in text:
                hit = sorted({s for s in signals if profile.has_any(s)})
                if hit:
                    return "useful", f"「{kw}」匹配项目信号：{', '.join(hit)}"
                return "useless", f"「{kw}」对应领域在项目中无信号（需 {'/'.join(signals)}）"
    return None


def _collect(reports: list[ToolReport], attr: str, name_of) -> dict[str, list[str]]:
    items: dict[str, list[str]] = {}
    for r in reports:
        if not r.installed:
            continue
        for item in getattr(r, attr):
            name = name_of(item)
            tools = items.setdefault(name, [])
            if r.tool not in tools:
                tools.append(r.tool)
    return items


def assess_skills(profile: ProjectProfile, reports: list[ToolReport]) -> list[Assessment]:
    descs: dict[str, str] = {}
    for r in reports:
        for s in r.skills:
            descs.setdefault(s.name, s.description or "")
    groups = {
        "azure": ("azure",), "entra": ("azure",), "aks": ("azure",), "airunway": ("azure",),
        "appservice": ("azure",), "foundry": ("azure",), "appinsights": ("azure",),
        "kubernetes": ("docker",), "docker": ("docker",),
        "paper": ("academic",), "latex": ("academic",), "citation": ("academic",),
        "literature": ("academic",), "zotero": ("academic",), "润色": ("academic",),
        "plot": ("academic", "data-science", "python"), "figure": ("academic", "data-science"),
        "diagram": ("docs", "academic"), "drawio": ("docs", "academic"),
        "android": ("android",), "ios": ("ios",), "swift": ("ios",),
        "rust": ("rust",), " golang": ("go",), "java": ("jvm",),
    }
    utility = {"find-skills", "skill-creator", "skill-installer", "plugin-creator", "handoff"}
    out: list[Assessment] = []
    for name, tools in _collect(reports, "skills", lambda s: s.name).items():
        if name in utility:
            out.append(Assessment("skills", name, "useful", "通用工具链 skill", SUGGEST_KEEP, tools))
            continue
        hit = _kw_match(name, descs.get(name, ""), profile, groups)
        if hit:
            verdict, reason = hit
            out.append(Assessment("skills", name, verdict, reason,
                                  SUGGEST_KEEP if verdict == "useful" else SUGGEST_REMOVE, tools))
        else:
            out.append(Assessment("skills", name, "maybe", "未匹配到明确领域信号", SUGGEST_WATCH, tools))
    return out


def assess_agents(profile: ProjectProfile, reports: list[ToolReport]) -> list[Assessment]:
    groups = {
        "jargon": ("academic",), "paper": ("academic",), "writing": ("academic", "docs"),
        "review": ("code",), "code": ("code",), "plan": ("code",),
    }
    out: list[Assessment] = []
    for name, tools in _collect(reports, "agents", lambda a: a.name).items():
        hit = _kw_match(name, "", profile, groups)
        if hit:
            verdict, reason = hit
            out.append(Assessment("agents", name, verdict, reason,
                                  SUGGEST_KEEP if verdict == "useful" else SUGGEST_REMOVE, tools))
        else:
            out.append(Assessment("agents", name, "maybe", "通用 subagent，未匹配专项信号", SUGGEST_WATCH, tools))
    return out


def assess_mcp(profile: ProjectProfile, reports: list[ToolReport]) -> list[Assessment]:
    rules: dict[str, tuple[tuple[str, ...], str]] = {
        "android-emulator": (("android",), "Android 开发信号"),
        "ios-simulator": (("ios",), "iOS 开发信号"),
        "node_repl": (("node", "javascript", "typescript"), "Node/JS 信号"),
        "drawio": (("academic", "docs", "code"), "论文/文档/架构作图信号"),
        "zotero": (("academic",), "文献管理信号"),
        "codegraph": (("code",), "代码项目信号"),
    }
    general = {"web-search", "web-reader", "web_search", "context7", "zai-mcp-server", "fetch"}
    out: list[Assessment] = []
    for name, tools in _collect(reports, "mcp_servers", lambda m: m.name).items():
        if name in rules:
            signals, label = rules[name]
            hit = [s for s in signals if profile.has_any(s)]
            if hit:
                out.append(Assessment("mcp", name, "useful", f"{label}命中：{', '.join(hit)}", SUGGEST_KEEP, tools))
            else:
                out.append(Assessment("mcp", name, "useless", f"项目无{label}", SUGGEST_REMOVE, tools))
        elif any(g in name for g in general):
            out.append(Assessment("mcp", name, "useful", "通用检索/模型能力", SUGGEST_KEEP, tools))
        else:
            out.append(Assessment("mcp", name, "maybe", "未匹配专项信号，通用 MCP 默认观察", SUGGEST_WATCH, tools))
    return out


def assess_plugins(profile: ProjectProfile, reports: list[ToolReport]) -> list[Assessment]:
    groups = {
        "playwright": ("playwright", "web", "typescript", "javascript"),
        "android": ("android",), "ios": ("ios",),
        "azure": ("azure",), "foundry": ("azure",),
        "document": ("academic", "docs"), "docx": ("academic", "docs"),
        "github": ("repo",), "git": ("repo",),
    }
    workflow_kw = ("code-review", "orchestrate", "remember", "context-mode",
                   "commit", "skill-creator", "ralph", "codex", "claude-code-setup")
    out: list[Assessment] = []
    for pid, tools in _collect(reports, "plugins", lambda p: p.plugin_id).items():
        low = pid.lower()
        if any(k in low for k in workflow_kw):
            out.append(Assessment("plugins", pid, "useful", "通用开发工作流", SUGGEST_KEEP, tools))
            continue
        if ("github" in low or "git" in low) and profile.is_repo:
            out.append(Assessment("plugins", pid, "useful", "当前项目是 git 仓库", SUGGEST_KEEP, tools))
            continue
        groups.pop("github", None)
        groups.pop("git", None)
        hit = _kw_match(pid, "", profile, groups)
        if hit:
            verdict, reason = hit
            out.append(Assessment("plugins", pid, verdict, reason,
                                  SUGGEST_KEEP if verdict == "useful" else SUGGEST_REMOVE, tools))
        else:
            out.append(Assessment("plugins", pid, "maybe", "未匹配专项信号", SUGGEST_WATCH, tools))
    return out


def assess_hooks(profile: ProjectProfile, reports: list[ToolReport],
                 plugin_ids: set[str] | None = None) -> list[Assessment]:
    plugin_ids = plugin_ids or set()
    out: list[Assessment] = []
    for label, tools in _collect(reports, "hooks", lambda h: h.label).items():
        low = label.lower()
        if "codegraph" in low:
            if profile.has_any("code"):
                out.append(Assessment("hooks", label, "useful", "代码项目信号命中", SUGGEST_KEEP, tools))
            else:
                out.append(Assessment("hooks", label, "useless", "非代码项目", SUGGEST_REMOVE, tools))
        elif "context-mode" in low:
            if any("context-mode" in p for p in plugin_ids):
                out.append(Assessment("hooks", label, "useful", "配套 context-mode 插件存在", SUGGEST_KEEP, tools))
            else:
                out.append(Assessment("hooks", label, "maybe", "未发现配套插件", SUGGEST_WATCH, tools))
        else:
            out.append(Assessment("hooks", label, "maybe", "未匹配专项信号", SUGGEST_WATCH, tools))
    return out


def assess_all(profile: ProjectProfile, reports: list[ToolReport]) -> dict[str, list[Assessment]]:
    plugin_ids = {p.plugin_id for r in reports if r.installed for p in r.plugins}
    return {
        "skills": assess_skills(profile, reports),
        "agents": assess_agents(profile, reports),
        "mcp": assess_mcp(profile, reports),
        "plugins": assess_plugins(profile, reports),
        "hooks": assess_hooks(profile, reports, plugin_ids),
    }
