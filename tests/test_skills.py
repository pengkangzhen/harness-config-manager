"""skills 层单测：库解析 / adopt 去重 / 条目级 sync 计划与执行。"""

from __future__ import annotations

import os
from pathlib import Path

from harness_config_manager.config import HcmConfig
from harness_config_manager.model import SkillInfo, ToolReport
from harness_config_manager.skills import (
    plan_adopt,
    plan_sync,
    resolve_library,
    run_sync,
)

from conftest import make_skill


# ---------------------------------------------------------------------------
# resolve_library 三层回退


def test_library_fallback_to_agents_dir(fake_home: Path) -> None:
    lib = fake_home / ".agents/skills"
    make_skill(lib, "alpha")
    assert resolve_library(HcmConfig()) == lib


def test_library_explicit_config_wins(fake_home: Path) -> None:
    lib = fake_home / ".agents/skills"
    make_skill(lib, "alpha")
    custom = fake_home / "mylib"
    custom.mkdir()
    assert resolve_library(HcmConfig(library=str(custom))) == custom


def test_library_self_managed(fake_home: Path) -> None:
    assert resolve_library(HcmConfig()) == fake_home / ".config/hcm/library/skills"


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
    backups = list((fake_home / ".config/hcm/backups").rglob("alpha"))
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
