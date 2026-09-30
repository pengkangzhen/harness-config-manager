"""用户级记忆层：各 harness 全局指令文件（CLAUDE.md / AGENTS.md / GEMINI.md…）的一致性管理。

与 subagents 层同机制：单一事实源（默认 ~/.agents/memory/MEMORY.md）以 symlink
分发到每个工具的用户级记忆路径。symlink 意味着在任一工具侧编辑即改库文件，
天然保持一致；工具侧真实文件与库内容不同时记 conflict（默认跳过，
--prefer library 备份后覆盖）。库文件缺失时按 skills 的收养语义从工具侧收集：
各工具内容一致（或仅一处存在）取其一，不一致时报告冲突，--from <tool> 指定收养源。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import HalterConfig
from .model import MemoryInfo, ToolReport
from .registry import BY_KEY, expand

DEFAULT_MEMORY_FILE = ".agents/memory/MEMORY.md"


# ---------------------------------------------------------------------------
# 事实源解析：显式配置 > ~/.agents/memory/MEMORY.md


def resolve_memory_file(cfg: HalterConfig, create: bool = False) -> Path:
    if cfg.memory_file:
        lib = Path(cfg.memory_file).expanduser()
    else:
        lib = expand(DEFAULT_MEMORY_FILE)
    if create:
        lib.parent.mkdir(parents=True, exist_ok=True)
    return lib


# ---------------------------------------------------------------------------
# 扫描


def memory_target(spec) -> Path | None:
    """一个工具的记忆文件路径：候选中第一个父目录存在者（跨平台差异）。

    无候选或父目录均不存在（工具未安装 / 无此层）返回 None。
    """
    for pattern in spec.memory_files:
        target = expand(pattern)
        if target.parent.is_dir():
            return target
    return None


def scan_memory(spec) -> tuple[MemoryInfo | None, list[str]]:
    """扫描一个工具的用户级记忆文件，返回 (memory, notes)。"""
    target = memory_target(spec)
    if target is None:
        return None, []
    info = MemoryInfo(path=target)
    if target.exists():
        info.present = True
        info.linked = target.is_symlink()
        st = target.stat()
        info.size = st.st_size
        info.mtime = st.st_mtime
    return info, []


# ---------------------------------------------------------------------------
# adopt：库缺失时从工具侧收养事实源


@dataclass
class MemoryAdoptPlan:
    sources: list[tuple[str, Path]] = field(default_factory=list)  # (tool, 源文件)
    divergent: bool = False  # 各工具现存内容不一致，需 --from <tool> 裁决


def plan_adopt_memory(reports: list[ToolReport]) -> MemoryAdoptPlan:
    """收集各工具现存的非链接记忆文件；内容不一致标记 divergent。"""
    sources: list[tuple[str, Path]] = []
    contents: list[bytes] = []
    for r in reports:
        if r.memory is None or not r.memory.present or r.memory.linked:
            continue
        sources.append((r.tool, r.memory.path))
        try:
            contents.append(r.memory.path.read_bytes())
        except OSError:
            contents.append(b"")
    return MemoryAdoptPlan(sources=sources, divergent=len(set(contents)) > 1)


def pick_adopt_source(plan: MemoryAdoptPlan, from_tool: str | None) -> tuple[str, Path] | None:
    """裁决收养源：--from 指定 > 各处内容一致时取第一个。

    内容不一致且未指定 --from 时返回 None（由调用方报告冲突）。
    """
    if not plan.sources:
        return None
    if from_tool is not None:
        for tool, path in plan.sources:
            if tool == from_tool:
                return tool, path
        return None
    if plan.divergent:
        return None
    return plan.sources[0]


def run_adopt_memory(source: Path, library: Path, apply: bool) -> list[str]:
    lines = [f"adopt memory {source} -> {library}"]
    if apply:
        library.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, library)
    return lines


def memory_diff_summary(tool_side: Path, library: Path) -> str:
    """工具侧与库内容不同时的简要说明（字节数对比）。"""
    try:
        theirs = tool_side.stat().st_size
        ours = library.stat().st_size
    except OSError:
        return "(内容不同)"
    return f"(内容不同: 工具侧 {theirs}B vs 库 {ours}B)"


# ---------------------------------------------------------------------------
# sync：symlink 分发


@dataclass
class MemoryAction:
    kind: str            # ok / link / relink / replace / conflict
    tool: str
    path: Path
    detail: str = ""


def backup_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = expand(f".config/halter/backups/{stamp}/memory")
    d.mkdir(parents=True, exist_ok=True)
    return d


def plan_sync_memory(library: Path, reports: list[ToolReport]) -> list[MemoryAction]:
    """库 → 各工具的 symlink 分发计划。未安装 / 无 memory 层的工具自动跳过。"""
    actions: list[MemoryAction] = []
    lib_bytes = library.read_bytes()
    lib_resolved = library.resolve()
    for r in reports:
        spec = BY_KEY.get(r.tool)
        if spec is None:
            continue
        target = memory_target(spec)
        if target is None:
            continue
        if not target.is_symlink() and target.resolve() == lib_resolved:
            # memory_file 直接配在工具侧路径上：库即目标，跳过以防自链接
            # （已正确链接到库的 symlink 不在此列，走下方 ok 分支）
            continue
        if not target.exists() and not target.is_symlink():
            actions.append(MemoryAction("link", r.tool, target))
        elif target.is_symlink():
            if Path(target.resolve()) == library.resolve():
                actions.append(MemoryAction("ok", r.tool, target))
            else:
                actions.append(MemoryAction("relink", r.tool, target,
                                            f"现指向 {target.resolve()}"))
        elif target.is_file():
            if target.read_bytes() == lib_bytes:
                actions.append(MemoryAction("replace", r.tool, target,
                                            "内容与库一致，替换为链接"))
            else:
                actions.append(MemoryAction("conflict", r.tool, target,
                                            memory_diff_summary(target, library)))
        # 目录占位同名等罕见情形：忽略
    return actions


def run_sync_memory(actions: list[MemoryAction], library: Path, apply: bool,
                    prefer: str = "skip") -> list[str]:
    """执行分发计划。prefer 仅影响 conflict：skip（默认跳过）/ library（备份工具侧，用库覆盖）。"""
    lines: list[str] = []
    bak = backup_dir() if apply else None
    for act in actions:
        if act.kind == "link":
            lines.append(f"link    {act.tool}:{act.path.name} -> {act.path}")
            if apply:
                act.path.symlink_to(library)
        elif act.kind == "relink":
            lines.append(f"relink  {act.tool}:{act.path.name}（{act.detail}）")
            if apply:
                act.path.unlink()
                act.path.symlink_to(library)
        elif act.kind == "replace":
            lines.append(f"replace {act.tool}:{act.path.name}（备份后链接）")
            if apply and bak is not None:
                dest = bak / act.tool / act.path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(act.path), str(dest))
                act.path.symlink_to(library)
        elif act.kind == "conflict":
            detail = f"（{act.detail}）" if act.detail else ""
            if prefer == "library":
                lines.append(f"conflict {act.tool}:{act.path.name}{detail} → 按库覆盖（备份工具侧）")
                if apply and bak is not None:
                    dest = bak / act.tool / act.path.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(act.path), str(dest))
                    act.path.symlink_to(library)
            else:
                lines.append(f"conflict {act.tool}:{act.path.name}{detail}"
                             f" → 跳过（--prefer library 可覆盖）")
    return lines
