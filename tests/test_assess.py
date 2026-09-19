"""assess 层单测：项目画像提取 + 五层有用度规则。"""

from __future__ import annotations

from pathlib import Path

from conftest import make_agent, make_skill

from harness_config_manager.assess import assess_all, profile_project
from harness_config_manager.model import (
    AgentInfo,
    HookInfo,
    McpServerInfo,
    PluginInfo,
    SkillInfo,
    ToolReport,
)


def _academic_project(tmp_path: Path) -> Path:
    root = tmp_path / "paper-proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "main.tex").write_text("\\documentclass{article}\n")
    (root / "refs.bib").write_text("@article{a,title={A}}\n")
    (root / "solve.py").write_text("print('ok')\n")
    return root


def _report(skills=None, agents=None, mcp=None, plugins=None, hooks=None) -> ToolReport:
    return ToolReport(
        tool="claude", display="Claude Code", installed=True,
        skills=skills or [], agents=agents or [], mcp_servers=mcp or [],
        plugins=plugins or [], hooks=hooks or [],
    )


def test_profile_detects_python_academic_and_repo(tmp_path: Path) -> None:
    root = _academic_project(tmp_path)
    (root / ".git").mkdir()
    p = profile_project(root)
    assert "python" in p.languages
    assert "academic" in p.domains
    assert "code" in p.domains
    assert p.is_repo


def test_azure_skill_useless_on_academic_project(tmp_path: Path) -> None:
    profile = profile_project(_academic_project(tmp_path))
    reports = [_report(skills=[SkillInfo(
        name="azure-compute",
        path=Path("/tmp/azure-compute"),
        description="Azure VM/VMSS router for creating virtual machines.",
    )])]
    a = assess_all(profile, reports)["skills"][0]
    assert a.verdict == "useless"
    assert "azure" in a.reason
    assert a.suggestion == "移除或 exclude"


def test_azure_skill_useful_on_azure_project(tmp_path: Path) -> None:
    root = tmp_path / "az-proj"
    root.mkdir()
    (root / "main.bicep").write_text("param location string\n")
    profile = profile_project(root)
    reports = [_report(skills=[SkillInfo(
        name="azure-compute", path=Path("/tmp/x"),
        description="Azure VM/VMSS router.",
    )])]
    a = assess_all(profile, reports)["skills"][0]
    assert a.verdict == "useful"


def test_academic_skills_useful_on_research_project(tmp_path: Path) -> None:
    profile = profile_project(_academic_project(tmp_path))
    reports = [_report(skills=[
        SkillInfo(name="paper-polish", path=Path("/tmp/p"), description="论文语言润色"),
        SkillInfo(name="figure-plotter", path=Path("/tmp/f"), description="数据可视化与绘图"),
    ])]
    out = {a.item: a for a in assess_all(profile, reports)["skills"]}
    assert out["paper-polish"].verdict == "useful"
    assert out["figure-plotter"].verdict == "useful"


def test_mcp_platform_rules(tmp_path: Path) -> None:
    profile = profile_project(_academic_project(tmp_path))
    reports = [_report(mcp=[
        McpServerInfo(name="android-emulator", transport="stdio"),
        McpServerInfo(name="ios-simulator", transport="stdio"),
        McpServerInfo(name="codegraph", transport="stdio"),
        McpServerInfo(name="zotero", transport="stdio"),
    ])]
    out = {a.item: a for a in assess_all(profile, reports)["mcp"]}
    assert out["android-emulator"].verdict == "useless"
    assert out["ios-simulator"].verdict == "useless"
    assert out["codegraph"].verdict == "useful"
    assert out["zotero"].verdict == "useful"


def test_github_plugin_useful_in_repo(tmp_path: Path) -> None:
    root = _academic_project(tmp_path)
    (root / ".git").mkdir()
    profile = profile_project(root)
    reports = [_report(plugins=[PluginInfo(plugin_id="github@claude-plugins-official")])]
    a = assess_all(profile, reports)["plugins"][0]
    assert a.verdict == "useful"


def test_items_dedup_across_tools(tmp_path: Path) -> None:
    profile = profile_project(_academic_project(tmp_path))
    r1 = _report(skills=[SkillInfo(name="paper-polish", path=Path("/tmp/a"), description="润色")])
    r2 = ToolReport(tool="zcode", display="ZCode", installed=True,
                    skills=[SkillInfo(name="paper-polish", path=Path("/tmp/b"))])
    a = assess_all(profile, [r1, r2])["skills"][0]
    assert a.tools == ["claude", "zcode"]
