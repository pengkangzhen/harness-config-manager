"""Skills 层：通用扫描 + adopt 收集入库 + 条目级 symlink 同步。"""

from __future__ import annotations

import difflib
import filecmp
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import HalterConfig
from .model import SkillInfo, ToolReport
from .registry import BY_KEY, expand

# ---------------------------------------------------------------------------
# 事实源解析（三层回退：显式配置 > ~/.agents/skills > halter 自管库）


def resolve_library(cfg: HalterConfig, create: bool = False) -> Path:
    if cfg.library:
        lib = Path(cfg.library).expanduser()
    else:
        candidate = expand(".agents/skills")
        if candidate.is_dir():
            return candidate
        lib = expand(".config/halter/library/skills")
    if create:
        lib.mkdir(parents=True, exist_ok=True)
    return lib


# ---------------------------------------------------------------------------
# 扫描


def scan_skills(spec) -> tuple[list[SkillInfo], list[str]]:
    """扫描一个工具的所有用户级 skills 目录，返回 (skills, notes)。

    判定规则：目录下含 SKILL.md 的子目录视为一个 skill；
    symlink 指向库目录的记 linked=True（已同步标志）；
    同工具多目录同名时去重（保留先出现的，链接状态取并集）。
    """
    skills: list[SkillInfo] = []
    seen: dict[str, int] = {}
    notes: list[str] = []
    for pattern in spec.skills_dirs:
        root = expand(pattern)
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if not (child / "SKILL.md").exists():
                notes.append(f"跳过（无 SKILL.md）: {child.name} @ ~/{pattern}")
                continue
            if child.name in seen:
                skills[seen[child.name]].linked = (
                    skills[seen[child.name]].linked or child.is_symlink()
                )
                continue
            seen[child.name] = len(skills)
            skills.append(SkillInfo(name=child.name, path=child, linked=child.is_symlink()))
    return skills, notes


# ---------------------------------------------------------------------------
# adopt：把各工具独有的 skill 收集入库


@dataclass
class AdoptPlan:
    to_adopt: list[tuple[str, Path]] = field(default_factory=list)  # (tool, 源路径)
    conflicts: list[tuple[str, str, Path, Path]] = field(default_factory=list)  # (tool, name, 工具侧, 库侧)


def plan_adopt(library: Path, reports: list[ToolReport]) -> AdoptPlan:
    """收集各工具独有的非链接 skill。

    同名多来源：内容一致取其一；不一致记入 conflicts 供人工裁决。
    """
    lib_names = {p.name for p in library.iterdir()} if library.is_dir() else set()
    by_name: dict[str, list[tuple[str, Path]]] = {}
    for r in reports:
        for s in r.skills:
            if s.linked or s.name in lib_names:
                continue
            by_name.setdefault(s.name, []).append((r.tool, s.path))

    plan = AdoptPlan()
    for name, sources in sorted(by_name.items()):
        if len(sources) == 1:
            plan.to_adopt.append(sources[0])
            continue
        first_tool, first_path = sources[0]
        if all(dirs_equal(first_path, p) for _, p in sources[1:]):
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
            shutil.copytree(src, dest)
    return lines


def diff_summary(a: Path, b: Path) -> str:
    """同名 skill 两处不同的简要 diff（首个不同文件的 unified diff）。"""
    for fa in sorted(a.rglob("*")):
        if not fa.is_file():
            continue
        fb = b / fa.relative_to(a)
        if not fb.exists() or fa.read_bytes() != fb.read_bytes():
            ta = fa.read_text(encoding="utf-8", errors="replace").splitlines()
            tb = fb.read_text(encoding="utf-8", errors="replace").splitlines() if fb.exists() else []
            diff = difflib.unified_diff(tb, ta, fromfile=str(fb), tofile=str(fa), lineterm="")
            head = list(diff)[:12]
            return "\n".join(head) if head else "(仅存在性差异)"
    return "(内容一致)"


# ---------------------------------------------------------------------------
# sync：条目级 symlink 分发


@dataclass
class SyncAction:
    kind: str            # ok / link / relink / replace / conflict / adopt-hint / skip-excluded
    tool: str
    skill: str
    path: Path
    detail: str = ""


def dirs_equal(a: Path, b: Path) -> bool:
    cmp = filecmp.dircmp(a, b)
    equal = True
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    for sub in cmp.common_dirs:
        if not dirs_equal(a / sub, b / sub):
            return False
    return equal


def backup_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = expand(f".config/halter/backups/{stamp}/skills")
    d.mkdir(parents=True, exist_ok=True)
    return d


def plan_sync(library: Path, reports: list[ToolReport], exclude: list[str]) -> list[SyncAction]:
    actions: list[SyncAction] = []
    lib_skills = {p.name: p for p in library.iterdir()
                  if p.is_dir() and (p / "SKILL.md").exists()} if library.is_dir() else {}

    for r in reports:
        spec = BY_KEY.get(r.tool)
        seen: set[Path] = set()
        if spec:
            for d in (expand(p) for p in spec.skills_dirs):
                if d.is_dir():
                    seen.add(d)
        for s in r.skills:  # 未注册工具兜底：从扫描结果反推目录
            if s.path.parent.is_dir():
                seen.add(s.path.parent)
        # 工具目录里独有（库里没有）的非链接 skill → adopt 提示
        for s in r.skills:
            if s.name not in lib_skills and not s.linked and s.name not in exclude:
                actions.append(SyncAction("adopt-hint", r.tool, s.name, s.path,
                                          "仅此工具有，建议先 halter adopt（sync 自动收集）"))
        # 库 → 工具
        tool_entries = {s.name: s for s in r.skills}
        for skill_dir in sorted(seen):
            for name, lib_path in lib_skills.items():
                if name in exclude:
                    actions.append(SyncAction("skip-excluded", r.tool, name, skill_dir / name))
                    continue
                target = skill_dir / name
                entry = tool_entries.get(name)
                if entry is None and not target.exists():
                    actions.append(SyncAction("link", r.tool, name, target))
                elif target.is_symlink():
                    if Path(target.resolve()) == lib_path.resolve():
                        actions.append(SyncAction("ok", r.tool, name, target))
                    else:
                        actions.append(SyncAction("relink", r.tool, name, target,
                                                  f"现指向 {target.resolve()}"))
                elif target.is_dir():
                    if dirs_equal(target, lib_path):
                        actions.append(SyncAction("replace", r.tool, name, target,
                                                  "内容与库一致，替换为链接"))
                    else:
                        actions.append(SyncAction("conflict", r.tool, name, target,
                                                  "与库内容不同"))
                # entry 存在但 target 不存在等罕见情形：忽略
    return actions


def run_sync(actions: list[SyncAction], library: Path, apply: bool,
             prefer: str = "skip") -> list[str]:
    """执行同步计划。prefer 仅影响 conflict：skip（默认跳过）/ library（备份工具侧，用库覆盖）。"""
    lines: list[str] = []
    bak = backup_dir() if apply else None
    for act in actions:
        if act.kind == "link":
            lines.append(f"link   {act.tool}:{act.skill} -> {act.path}")
            if apply:
                act.path.symlink_to(library / act.skill)
        elif act.kind == "relink":
            lines.append(f"relink {act.tool}:{act.skill} ({act.detail})")
            if apply:
                act.path.unlink()
                act.path.symlink_to(library / act.skill)
        elif act.kind == "replace":
            lines.append(f"replace {act.tool}:{act.skill}（备份后链接）")
            if apply and bak is not None:
                dest = bak / act.tool / act.skill
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(act.path), str(dest))
                act.path.symlink_to(library / act.skill)
        elif act.kind == "conflict":
            if prefer == "library":
                lines.append(f"conflict {act.tool}:{act.skill} → 按库覆盖（备份工具侧）")
                if apply and bak is not None:
                    dest = bak / act.tool / act.skill
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(act.path), str(dest))
                    act.path.symlink_to(library / act.skill)
            else:
                lines.append(f"conflict {act.tool}:{act.skill} → 跳过（--prefer library 可覆盖）")
    return lines
