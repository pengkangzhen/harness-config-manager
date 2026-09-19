"""halter tui 单测：层切换 / 缺口过滤 / 条目过滤 / 非 TTY 防护。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from harness_config_manager.cli import app
from harness_config_manager.model import (
    McpServerInfo,
    PluginInfo,
    SkillInfo,
    ToolReport,
)
from harness_config_manager.tui import HalterTui

runner = CliRunner()


def _reports() -> list[ToolReport]:
    claude = ToolReport(
        tool="claude", display="Claude Code", installed=True,
        skills=[
            SkillInfo(name="paper-polish", path=Path("/lib/paper-polish"), linked=True,
                      description="论文语言润色"),
            SkillInfo(name="figure-plotter", path=Path("/lib/figure-plotter"),
                      description="数据可视化与绘图"),
        ],
        mcp_servers=[McpServerInfo(name="codegraph", transport="stdio")],
        plugins=[PluginInfo(plugin_id="github@claude-plugins-official")],
    )
    zcode = ToolReport(
        tool="zcode", display="ZCode", installed=True,
        skills=[SkillInfo(name="paper-polish", path=Path("/z/paper-polish"))],
        mcp_servers=[],
        plugins=[PluginInfo(plugin_id="github@zcode-plugins-official")],
    )
    return [claude, zcode]


def _drive(coro) -> None:
    asyncio.run(coro)


async def _check_boot() -> None:
    app_ = HalterTui(_reports())
    async with app_.run_test() as pilot:
        table = app_.query_one("#matrix")
        assert app_._layer == "skills"
        assert table.row_count == 2  # paper-polish + figure-plotter
        names = {r.name for r in app_._visible}
        assert names == {"paper-polish", "figure-plotter"}


def test_tui_boot_skills_layer() -> None:
    _drive(_check_boot())


async def _check_layer_switch_and_gaps() -> None:
    app_ = HalterTui(_reports())
    async with app_.run_test() as pilot:
        await pilot.press("3")  # MCP
        assert app_._layer == "mcp"
        assert {r.name for r in app_._visible} == {"codegraph"}
        await pilot.press("g")
        # codegraph 在 zcode 缺失 → 仍显示；切回 skills 后 figure-plotter(zcode 缺) 也在
        assert app_._gaps_only is True
        assert {r.name for r in app_._visible} == {"codegraph"}
        await pilot.press("1")
        assert {r.name for r in app_._visible} == {"figure-plotter"}


def test_tui_layer_switch_and_gaps_only() -> None:
    _drive(_check_layer_switch_and_gaps())


async def _check_filter() -> None:
    app_ = HalterTui(_reports())
    async with app_.run_test() as pilot:
        f = app_.query_one("#filter")
        assert f.display is False
        await pilot.press("slash")
        assert f.display is True
        await pilot.press(*"paper")
        await pilot.press("enter")
        assert app_._filter == "paper"
        assert {r.name for r in app_._visible} == {"paper-polish"}


def test_tui_filter() -> None:
    _drive(_check_filter())


async def _check_plugins_merge() -> None:
    app_ = HalterTui(_reports())
    async with app_.run_test() as pilot:
        await pilot.press("4")  # PLUGINS
        rows = {r.name: r for r in app_._visible}
        assert "github" in rows  # 两个 @market 变体合并为一行
        assert set(rows["github"].entries) == {"claude", "zcode"}
        assert sorted(rows["github"].merged) == [
            "github@claude-plugins-official",
            "github@zcode-plugins-official",
        ]


def test_tui_plugins_merge_by_base_name() -> None:
    _drive(_check_plugins_merge())


def test_tui_requires_tty() -> None:
    result = runner.invoke(app, ["tui"])
    assert result.exit_code == 1
    assert "交互式终端" in result.output


async def _check_help_and_sort() -> None:
    from harness_config_manager.tui import HelpScreen

    app_ = HalterTui(_reports())
    async with app_.run_test() as pilot:
        await pilot.press("question_mark")
        assert isinstance(app_.screen, HelpScreen)
        await pilot.press("escape")
        assert not isinstance(app_.screen, HelpScreen)

        # s 循环排序：gap -> name -> cover
        await pilot.press("s")
        assert app_._sort == 1
        names = [r.name for r in app_._visible]
        assert names == sorted(names)
        await pilot.press("s")
        assert app_._sort == 2
        await pilot.press("s")
        assert app_._sort == 0


def test_tui_help_and_sort() -> None:
    _drive(_check_help_and_sort())


async def _check_tab_click() -> None:
    app_ = HalterTui(_reports())
    async with app_.run_test() as pilot:
        await pilot.click("#tab-mcp")
        assert app_._layer == "mcp"


def test_tui_tab_click_switches_layer() -> None:
    _drive(_check_tab_click())


def test_tui_snapshot(snap_compare) -> None:
    """渲染快照回归：样式/布局改动后 pytest --snapshot-update 更新基线。"""
    assert snap_compare(HalterTui(_reports()), press=["down", "down"])
