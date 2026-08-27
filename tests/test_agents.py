"""subagents 层测试：盘点/frontmatter、adopt 收集、symlink 分发语义（全部跑在 fake $HOME）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import make_agent

from harness_config_manager.agents import (
    AdoptPlan,
    plan_adopt,
    plan_sync,
    resolve_agents_library,
    run_adopt,
    run_sync,
    scan_agents,
)
from harness_config_manager.model import ToolReport
from harness_config_manager.registry import BY_KEY


# ---------------------------------------------------------------------------
# 工具函数


def _mklib(fake_home: Path, names: list[str], model: str | None = None) -> Path:
    lib = fake_home / ".agents/agents"
    for n in names:
        make_agent(lib, n, model=model)
    return lib


def _report(tool: str, entries: list[tuple[str, Path]]) -> ToolReport:
    from harness_config_manager.model import AgentInfo

    return ToolReport(tool=tool, display=tool.upper(), installed=True,
                      agents=[AgentInfo(name=n, path=p) for n, p in entries])


@pytest.fixture
def claude_agent_dir(fake_home: Path) -> Path:
    d = fake_home / ".claude/agents"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# 盘点


def test_scan_agents_basic(claude_agent_dir: Path) -> None:
    make_agent(claude_agent_dir, "code-reviewer", model="opus")
    make_agent(claude_agent_dir, "session-scribe")
    (claude_agent_dir / "notes.txt").write_text("x")
    (claude_agent_dir / "subdir").mkdir()

    agents, notes = scan_agents(BY_KEY["claude"])
    assert [a.name for a in agents] == ["code-reviewer", "session-scribe"]
    assert agents[0].model == "opus"
    assert agents[0].linked is False
    assert not any(a.linked for a in agents)
    # 非 .md 文件与子目录被跳过且留痕
    assert sum("notes.txt" in n for n in notes) == 1
    assert sum("subdir" in n for n in notes) == 1


def test_resolve_agents_library_fallbacks(fake_home: Path) -> None:
    from harness_config_manager.config import HcmConfig

    # 1. 显式配置优先
    cfg = HcmConfig(agents_library="/tmp/explicit-agents")
    assert resolve_agents_library(cfg) == Path("/tmp/explicit-agents").expanduser()
    # 2. ~/.agents/agents 存在则直接用（不 mkdir 自管库）
    shared = _mklib(fake_home, ["session-scribe"])
    assert resolve_agents_library(HcmConfig()) == shared
    # 3. 均无 → hcm 自管库；create=True 时创建（移除共享目录还原场景）
    import shutil as _shutil

    _shutil.rmtree(shared)
    empty_cfg = HcmConfig()
    assert not resolve_agents_library(empty_cfg).exists()
    created = resolve_agents_library(empty_cfg, create=True)
    assert created.is_dir() and "library/agents" in str(created)


def test_scan_json_serialization(claude_agent_dir: Path) -> None:
    from harness_config_manager.report import to_json

    make_agent(claude_agent_dir, "dev-planner", model="sonnet")
    r = ToolReport(tool="claude", display="Claude Code", installed=True,
                   agents=scan_agents(BY_KEY["claude"])[0])
    payload = json.loads(to_json([r]))[0]
    assert payload["agents"][0]["model"] == "sonnet"
    assert payload["agents"][0]["name"] == "dev-planner"


# ---------------------------------------------------------------------------
# adopt


def test_adopt_single_and_dedup(claude_agent_dir: Path, fake_home: Path) -> None:
    make_agent(claude_agent_dir, "bug-analyzer")
    rep = _report("claude", [("bug-analyzer", claude_agent_dir / "bug-analyzer.md")])

    lib = _mklib(fake_home, ["session-scribe"])
    plan = plan_adopt(lib, [rep])
    assert len(plan.to_adopt) == 1 and not plan.conflicts

    lines = run_adopt(plan, lib, apply=True)
    assert any("adopt bug-analyzer.md" in l for l in lines)
    assert (lib / "bug-analyzer.md").is_file()


def test_adopt_conflict_detected(claude_agent_dir: Path, fake_home: Path) -> None:
    make_agent(claude_agent_dir, "x", body="A version\n")
    other = fake_home / ".zcode/agents"
    make_agent(other, "x", body="B version\n")

    lib = fake_home / ".agents/agents"
    lib.mkdir(parents=True)
    reports = [_report("claude", [("x", claude_agent_dir / "x.md")]),
               _report("zcode", [("x", other / "x.md")])]
    plan = plan_adopt(lib, reports)
    assert not plan.to_adopt and len(plan.conflicts) == 2


# ---------------------------------------------------------------------------
# sync 计划与执行


def test_plan_link_and_ok(claude_agent_dir: Path, fake_home: Path) -> None:
    lib = _mklib(fake_home, ["alpha"])
    # 先链接一个，库多出的一个应计划 link
    target = claude_agent_dir / "alpha.md"
    target.symlink_to(lib / "alpha.md")
    make_agent(lib, "beta")
    reps = [_report("claude", [("alpha", target)])]

    actions = plan_sync(lib, reps, [])
    kinds = {(a.kind, a.agent) for a in actions}
    assert ("ok", "alpha") in kinds and ("link", "beta") in kinds
    run_sync(actions, lib, apply=True)
    assert (claude_agent_dir / "beta.md").is_symlink()
    assert os.readlink(claude_agent_dir / "beta.md") == str(lib / "beta.md")


def test_sync_replace_identical_with_backup(claude_agent_dir: Path, fake_home: Path) -> None:
    lib = _mklib(fake_home, ["alpha"])
    make_agent(claude_agent_dir, "alpha")          # 同名实体文件，内容与库一致
    reps = [_report("claude", [("alpha", claude_agent_dir / "alpha.md")])]

    actions = plan_sync(lib, reps, [])
    assert [(a.kind, a.agent) for a in actions] == [("replace", "alpha")]

    run_sync(actions, lib, apply=True)
    t = claude_agent_dir / "alpha.md"
    assert t.is_symlink() and t.resolve() == (lib / "alpha.md").resolve()
    backups = list((fake_home / ".config/hcm/backups").rglob("alpha.md"))
    assert backups, "replace 前应产生备份"


def test_sync_conflict_default_skip_then_prefer_library(
        claude_agent_dir: Path, fake_home: Path) -> None:
    lib = _mklib(fake_home, ["alpha"])
    make_agent(claude_agent_dir, "alpha", body="tool-side version\n")
    reps = [_report("claude", [("alpha", claude_agent_dir / "alpha.md")])]

    actions = plan_sync(lib, reps, [])
    assert actions[0].kind == "conflict"
    run_sync(actions, lib, apply=True, prefer="skip")
    t = claude_agent_dir / "alpha.md"
    assert not t.is_symlink()
    assert t.read_text().endswith("tool-side version\n")

    run_sync(actions, lib, apply=True, prefer="library")
    assert t.is_symlink() and t.resolve() == (lib / "alpha.md").resolve()
    backups = list((fake_home / ".config/hcm/backups").rglob("alpha.md"))
    assert backups


def test_sync_exclude_and_adopt_hint(claude_agent_dir: Path, fake_home: Path) -> None:
    lib = fake_home / ".agents/agents"
    make_agent(lib, "excluded-one")               # skip-excluded 只对库内条目生效
    make_agent(claude_agent_dir, "only-here")
    make_agent(claude_agent_dir, "excluded-one")
    reps = [_report("claude", [
        ("only-here", claude_agent_dir / "only-here.md"),
        ("excluded-one", claude_agent_dir / "excluded-one.md"),
    ])]

    actions = plan_sync(lib, reps, exclude=["excluded-one"])
    kinds = {(a.kind, a.agent) for a in actions}
    assert ("adopt-hint", "only-here") in kinds
    assert ("skip-excluded", "excluded-one") in kinds


def test_relink_wrong_target(claude_agent_dir: Path, fake_home: Path) -> None:
    lib = _mklib(fake_home, ["alpha"])
    elsewhere = fake_home / "elsewhere.md"
    elsewhere.write_text("---\n---\n", encoding="utf-8")
    target = claude_agent_dir / "alpha.md"
    target.symlink_to(elsewhere)

    actions = plan_sync(lib, [_report("claude", [("alpha", target)])], [])
    assert [(a.kind, a.agent) for a in actions] == [("relink", "alpha")]
    run_sync(actions, lib, apply=True)
    assert Path(target.resolve()) == (lib / "alpha.md").resolve()


# ---------------------------------------------------------------------------
# CLI 集成


def test_cli_scan_detail_agents(claude_agent_dir: Path) -> None:
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    make_agent(claude_agent_dir, "code-reviewer", model="opus")
    res = CliRunner().invoke(app, ["scan", "-d", "agents", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    claude = next(t for t in data["inventory"] if t["tool"] == "claude")
    assert {a["name"] for a in claude["agents"]} >= {"code-reviewer"}


def test_cli_sync_agents_layer(claude_agent_dir: Path, fake_home: Path) -> None:
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    make_agent(claude_agent_dir, "only-in-claude")
    res = CliRunner().invoke(app, ["sync"])       # dry-run 默认
    assert res.exit_code == 0
    assert "subagents 事实源库" in res.output
    assert "adopt" in res.output                  # 空库触发自动收集提示
    # dry-run 不落盘
    assert not (fake_home / ".agents/agents/only-in-claude.md").exists()


def test_cli_sync_no_agents_flag(claude_agent_dir: Path) -> None:
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    make_agent(claude_agent_dir, "only-in-claude")
    res = CliRunner().invoke(app, ["sync", "--no-agents"])
    assert res.exit_code == 0
    assert "subagents 事实源库" not in res.output
