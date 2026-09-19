"""skills 层单测：库解析 / adopt 去重 / 条目级 sync 计划与执行。"""

from __future__ import annotations

import os
from pathlib import Path

from harness_config_manager.config import HalterConfig
from harness_config_manager.model import SkillInfo, ToolReport
from harness_config_manager.skills import (
    plan_adopt,
    scan_skills,
    plan_sync,
    resolve_library,
    run_sync,
)

from conftest import make_skill


# ---------------------------------------------------------------------------
# resolve_library：显式配置 > ~/.agents/skills（统一默认）


def test_library_defaults_to_agents_dir(fake_home: Path) -> None:
    lib = resolve_library(HalterConfig())
    assert lib == fake_home / ".agents/skills"
    assert not lib.exists()  # 只解析不创建
    assert resolve_library(HalterConfig(), create=True).is_dir()


def test_library_explicit_config_wins(fake_home: Path) -> None:
    make_skill(fake_home / ".agents/skills", "alpha")
    custom = fake_home / "mylib"
    custom.mkdir()
    assert resolve_library(HalterConfig(library=str(custom))) == custom


def test_library_migrates_legacy_self_managed(fake_home: Path) -> None:
    legacy = make_skill(fake_home / ".config/halter/library/skills", "alpha")
    tool_dir = fake_home / ".claude/skills"
    tool_dir.mkdir(parents=True)
    link = tool_dir / "alpha"
    link.symlink_to(legacy)

    lib = resolve_library(HalterConfig())
    assert lib == fake_home / ".agents/skills"
    assert (lib / "alpha" / "SKILL.md").is_file()
    assert not legacy.exists()
    # 工具目录里的旧 symlink 被重定向到新库
    assert link.is_symlink() and link.resolve() == lib / "alpha"


# ---------------------------------------------------------------------------
# SKILL.md description 解析（扫描时提取，供桌面端悬停预览）


def test_scan_skills_extracts_description(fake_home: Path) -> None:
    from harness_config_manager.registry import BY_KEY

    make_skill(fake_home / ".claude/skills", "single", body=(
        "---\nname: single\ndescription: 一句话描述\n---\n# skill\n"))
    make_skill(fake_home / ".claude/skills", "folded", body=(
        "---\nname: folded\ndescription: >\n  语言润色 — Academic English polishing.\n"
        "  支持 LaTeX 手稿。\n---\n# skill\n"))
    make_skill(fake_home / ".claude/skills", "none", body="# 无 frontmatter\n")

    skills, _ = scan_skills(BY_KEY["claude"])
    by_name = {s.name: s for s in skills}
    assert by_name["single"].description == "一句话描述"
    assert by_name["folded"].description == (
        "语言润色 — Academic English polishing. 支持 LaTeX 手稿。")
    assert by_name["none"].description is None


# ---------------------------------------------------------------------------
# adopt：同名多工具去重


def test_adopt_identical_duplicates(fake_home: Path) -> None:
    lib = fake_home / "lib"
    lib.mkdir()
    a = make_skill(fake_home / "t1skills", "shared", "v1")
    make_skill(fake_home / "t2skills", "shared", "v1")
    reports = [
        ToolReport(tool="t1", display="T1", installed=True,
                   skills=[SkillInfo(name="shared", path=a)]),
        ToolReport(tool="t2", display="T2", installed=True,
                   skills=[SkillInfo(name="shared", path=fake_home / "t2skills/shared")]),
    ]
    plan = plan_adopt(lib, reports)
    assert len(plan.to_adopt) == 1
    assert not plan.conflicts


def test_adopt_conflicting_duplicates(fake_home: Path) -> None:
    lib = fake_home / "lib"
    lib.mkdir()
    a = make_skill(fake_home / "t1skills", "shared", "old")
    make_skill(fake_home / "t2skills", "shared", "new")
    reports = [
        ToolReport(tool="t1", display="T1", installed=True,
                   skills=[SkillInfo(name="shared", path=a)]),
        ToolReport(tool="t2", display="T2", installed=True,
                   skills=[SkillInfo(name="shared", path=fake_home / "t2skills/shared")]),
    ]
    plan = plan_adopt(lib, reports)
    assert not plan.to_adopt
    assert len(plan.conflicts) == 2


# ---------------------------------------------------------------------------
# sync 计划与执行


def _mklib(fake_home: Path, names: list[str]) -> Path:
    lib = fake_home / "lib"
    lib.mkdir(exist_ok=True)
    for n in names:
        make_skill(lib, n)
    return lib


def test_sync_links_missing(fake_home: Path) -> None:
    lib = _mklib(fake_home, ["alpha", "beta"])
    tool_dir = fake_home / "t1skills"
    tool_dir.mkdir()
    (tool_dir / "alpha").symlink_to(lib / "alpha")  # 已链接

    reports = [ToolReport(tool="t1", display="T1", installed=True, skills=[
        SkillInfo(name="alpha", path=tool_dir / "alpha", linked=True)])]
    actions = plan_sync(lib, reports, [])
    kinds = {(a.kind, a.skill) for a in actions}
    assert ("ok", "alpha") in kinds
    assert ("link", "beta") in kinds


def test_sync_replace_identical(fake_home: Path) -> None:
    lib = _mklib(fake_home, ["alpha"])  # SKILL.md 内容为默认 "# skill\n"
    tool_dir = fake_home / "t1skills"
    make_skill(tool_dir, "alpha", "# skill\n")  # 与库一致
    reports = [ToolReport(tool="t1", display="T1", installed=True, skills=[
        SkillInfo(name="alpha", path=tool_dir / "alpha")])]
    actions = plan_sync(lib, reports, [])
    assert actions[0].kind == "replace"

    run_sync(actions, lib, apply=True)
    target = tool_dir / "alpha"
    assert target.is_symlink()
    assert Path(os.readlink(target)) == lib / "alpha" or target.resolve() == (lib / "alpha").resolve()
    # 备份存在
    backups = list((fake_home / ".config/halter/backups").rglob("alpha"))
    assert backups, "replace 前应产生备份"


def test_sync_conflict_default_skip(fake_home: Path) -> None:
    lib = _mklib(fake_home, ["alpha"])  # SKILL.md 内容为默认 "# skill\n"
    tool_dir = fake_home / "t1skills"
    make_skill(tool_dir, "alpha", "# tool-version\n")  # 与库不同
    reports = [ToolReport(tool="t1", display="T1", installed=True, skills=[
        SkillInfo(name="alpha", path=tool_dir / "alpha")])]
    actions = plan_sync(lib, reports, [])

    run_sync(actions, lib, apply=True, prefer="skip")
    target = tool_dir / "alpha"
    assert not target.is_symlink()
    assert (target / "SKILL.md").read_text() == "# tool-version\n"

    run_sync(actions, lib, apply=True, prefer="library")
    assert target.is_symlink()
    assert target.resolve() == (lib / "alpha").resolve()


def test_sync_exclude(fake_home: Path) -> None:
    lib = _mklib(fake_home, ["alpha", "secret"])
    tool_dir = fake_home / "t1skills"
    alpha = make_skill(tool_dir, "alpha")  # 实体 skill，使目录被发现
    reports = [ToolReport(tool="t1", display="T1", installed=True,
                          skills=[SkillInfo(name="alpha", path=alpha)])]
    actions = plan_sync(lib, reports, exclude=["secret"])
    kinds = {(a.kind, a.skill) for a in actions}
    assert ("skip-excluded", "secret") in kinds
    assert ("link", "alpha") in kinds or ("replace", "alpha") in kinds
    assert not any(s == "secret" and k == "link" for k, s in kinds)
