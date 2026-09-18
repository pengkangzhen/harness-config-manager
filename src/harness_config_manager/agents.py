"""Subagents 层：通用扫描 + adopt 收集入库 + 条目级 symlink 同步。

与 skills 层的唯一实质差异：分发单位是单个 .md 文件（YAML frontmatter + 正文），
而非「含 SKILL.md 的子目录」。frontmatter 仅盘点展示用（手写最小解析，
不引入 yaml 依赖）；同步按整文件操作。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import HalterConfig
from .model import AgentInfo, ToolReport
from .registry import BY_KEY, expand

# ---------------------------------------------------------------------------
# 事实源解析（三层回退：显式配置 > ~/.agents/agents > halter 自管库）


def resolve_agents_library(cfg: HalterConfig, create: bool = False) -> Path:
    if cfg.agents_library:
        lib = Path(cfg.agents_library).expanduser()
    else:
        candidate = expand(".agents/agents")
        if candidate.is_dir():
            return candidate
        lib = expand(".config/halter/library/agents")
    if create:
        lib.mkdir(parents=True, exist_ok=True)
    return lib


# ---------------------------------------------------------------------------
# 扫描


def _frontmatter_field(path: Path, key: str) -> str | None:
    """从 .md 文件的 YAML frontmatter 提取单个顶层字段值（仅展示用）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        s = line.strip()
        if s == "---":
            break
        name, _, value = s.partition(":")
        if name.strip() == key:
            v = value.strip().strip("\"'")
            return v or None
    return None


def scan_agents(spec) -> tuple[list[AgentInfo], list[str]]:
    """扫描一个工具的所有用户级 subagents 目录，返回 (agents, notes)。

    判定规则：目录下每个 .md 文件视为一个 subagent；
    symlink 指向库文件的记 linked=True（已同步标志）；
    同工具多目录同名时去重（保留先出现的，链接状态取并集）。
    """
    agents: list[AgentInfo] = []
    seen: dict[str, int] = {}
    notes: list[str] = []
    for pattern in spec.agents_dirs:
        root = expand(pattern)
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir():
                notes.append(f"跳过（子代理为单文件，忽略目录）: {child.name} @ ~/{pattern}")
                continue
            if child.suffix != ".md":
                notes.append(f"跳过（非 .md 文件）: {child.name} @ ~/{pattern}")
                continue
            if child.stem in seen:
                agents[seen[child.stem]].linked = (
                    agents[seen[child.stem]].linked or child.is_symlink()
                )
                continue
            seen[child.stem] = len(agents)
            agents.append(AgentInfo(
                name=child.stem, path=child, linked=child.is_symlink(),
                model=_frontmatter_field(child, "model"),
            ))
    return agents, notes


# ---------------------------------------------------------------------------
# adopt：把各工具独有的 subagent 收集入库


@dataclass
class AdoptPlan:
    to_adopt: list[tuple[str, Path]] = field(default_factory=list)  # (tool, 源文件)
    conflicts: list[tuple[str, str, Path, Path]] = field(default_factory=list)  # (tool, name, 工具侧, 库侧)


def plan_adopt(library: Path, reports: list[ToolReport]) -> AdoptPlan:
    """收集各工具独有的非链接 subagent。

    同名多来源：内容一致取其一；不一致记入 conflicts 供人工裁决。
    """
    lib_names = {p.name for p in library.iterdir()} if library.is_dir() else set()
    by_name: dict[str, list[tuple[str, Path]]] = {}
    for r in reports:
        for a in r.agents:
            fname = a.path.name
            if a.linked or fname in lib_names:
                continue
            by_name.setdefault(a.name, []).append((r.tool, a.path))

    plan = AdoptPlan()
    for name, sources in sorted(by_name.items()):
        if len(sources) == 1:
            plan.to_adopt.append(sources[0])
            continue
        first_tool, first_path = sources[0]
        if all(first_path.read_bytes() == p.read_bytes() for _, p in sources[1:]):
            plan.to_adopt.append((first_tool, first_path))
        else:
            for tool, path in sources:
                plan.conflicts.append((tool, name, path, first_path))
    return plan


def run_adopt(plan: AdoptPlan, library: Path, apply: bool) -> list[str]:
    lines: list[str] = []
    for tool, src in plan.to_adopt:
        dest = library / src.name
        lines.append(f"adopt {src.name} ({tool}) -> {dest}")
        if apply:
            shutil.copy2(src, dest)
    return lines


def agent_diff_summary(a: Path, b: Path) -> str:
    """同名 agent 两处不同时的简要说明。"""
    try:
        same = a.read_bytes() == b.read_bytes()
    except OSError:
        same = False
    return "(内容一致)" if same else "(内容不同)"


# ---------------------------------------------------------------------------
# sync：条目级 symlink 分发


@dataclass
class SyncAction:
    kind: str            # ok / link / relink / replace / conflict / adopt-hint / skip-excluded
    tool: str
    agent: str
    path: Path
    detail: str = ""


def backup_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = expand(f".config/halter/backups/{stamp}/agents")
    d.mkdir(parents=True, exist_ok=True)
    return d


def plan_sync(library: Path, reports: list[ToolReport], exclude: list[str]) -> list[SyncAction]:
    actions: list[SyncAction] = []
    lib_agents = {p.stem: p for p in library.iterdir()
                  if p.is_file() and p.suffix == ".md"} if library.is_dir() else {}

    for r in reports:
        spec = BY_KEY.get(r.tool)
        seen: set[Path] = set()
        if spec:
            for d in (expand(p) for p in spec.agents_dirs):
                if d.is_dir():
                    seen.add(d)
        for a in r.agents:  # 未注册工具兜底：从扫描结果反推目录
            if a.path.parent.is_dir():
                seen.add(a.path.parent)
        # 工具目录里独有（库里没有）的非链接 agent → adopt 提示
        for a in r.agents:
            if a.name not in lib_agents and not a.linked and a.name not in exclude:
                actions.append(SyncAction("adopt-hint", r.tool, a.name, a.path,
                                          "仅此工具有，建议先收集入库（sync 自动收集）"))
        # 库 → 工具
        tool_entries = {a.name: a for a in r.agents}
        for agent_dir in sorted(seen):
            for name, lib_path in lib_agents.items():
                if name in exclude:
                    actions.append(SyncAction("skip-excluded", r.tool, name, agent_dir / f"{name}.md"))
                    continue
                target = agent_dir / f"{name}.md"
                entry = tool_entries.get(name)
                if entry is None and not target.exists():
                    actions.append(SyncAction("link", r.tool, name, target))
                elif target.is_symlink():
                    if Path(target.resolve()) == lib_path.resolve():
                        actions.append(SyncAction("ok", r.tool, name, target))
                    else:
                        actions.append(SyncAction("relink", r.tool, name, target,
                                                  f"现指向 {target.resolve()}"))
                elif target.is_file():
                    if target.read_bytes() == lib_path.read_bytes():
                        actions.append(SyncAction("replace", r.tool, name, target,
                                                  "内容与库一致，替换为链接"))
                    else:
                        actions.append(SyncAction(
                            "conflict", r.tool, name, target,
                            agent_diff_summary(target, lib_path)))
                # 目录占位同名等罕见情形：忽略
    return actions


def run_sync(actions: list[SyncAction], library: Path, apply: bool,
             prefer: str = "skip") -> list[str]:
    """执行同步计划。prefer 仅影响 conflict：skip（默认跳过）/ library（备份工具侧，用库覆盖）。"""
    lines: list[str] = []
    bak = backup_dir() if apply else None
    for act in actions:
        if act.kind == "link":
            lines.append(f"link   {act.tool}:{act.agent} -> {act.path}")
            if apply:
                act.path.symlink_to(library / f"{act.agent}.md")
        elif act.kind == "relink":
            lines.append(f"relink {act.tool}:{act.agent} ({act.detail})")
            if apply:
                act.path.unlink()
                act.path.symlink_to(library / f"{act.agent}.md")
        elif act.kind == "replace":
            lines.append(f"replace {act.tool}:{act.agent}（备份后链接）")
            if apply and bak is not None:
                dest = bak / act.tool / act.path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(act.path), str(dest))
                act.path.symlink_to(library / f"{act.agent}.md")
        elif act.kind == "conflict":
            detail = f"（{act.detail}）" if act.detail else ""
            if prefer == "library":
                lines.append(f"conflict {act.tool}:{act.agent}{detail} → 按库覆盖（备份工具侧）")
                if apply and bak is not None:
                    dest = bak / act.tool / act.path.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(act.path), str(dest))
                    act.path.symlink_to(library / f"{act.agent}.md")
            else:
                lines.append(f"conflict {act.tool}:{act.agent}{detail}"
                             f" → 跳过（--prefer library 可覆盖）")
    return lines
