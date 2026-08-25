"""报告输出：rich 表格 + JSON 序列化。"""

from __future__ import annotations

import dataclasses
import json

from rich.console import Console
from rich.table import Table

from .model import ToolReport

console = Console()


def to_json(reports: list[ToolReport]) -> str:
    payload = []
    for r in reports:
        payload.append({
            "tool": r.tool,
            "display": r.display,
            "installed": r.installed,
            "skills": [
                {"name": s.name, "path": str(s.path), "linked": s.linked}
                for s in r.skills
            ],
            "mcp_servers": [
                {"name": m.name, "transport": m.transport, "command": m.command,
                 "url": m.url, "extra": m.extra}
                for m in r.mcp_servers
            ],
            "plugins": [dataclasses.asdict(p) for p in r.plugins],
            "notes": r.scan_notes,
        })
    return json.dumps(payload, ensure_ascii=False, indent=2)


def print_summary(reports: list[ToolReport]) -> None:
    table = Table(title="⚓ AI 编码工具配置盘点")
    table.add_column("工具", style="cyan")
    table.add_column("Skills", justify="right")
    table.add_column("  已链接", justify="right", style="green")
    table.add_column("MCP", justify="right")
    table.add_column("插件", justify="right")
    for r in reports:
        linked = sum(1 for s in r.skills if s.linked)
        table.add_row(
            r.display,
            str(len(r.skills)),
            str(linked) if linked else "-",
            str(len(r.mcp_servers)),
            str(len(r.plugins)),
        )
    console.print(table)


def print_skills_detail(reports: list[ToolReport]) -> None:
    for r in reports:
        if not r.skills:
            continue
        table = Table(title=f"{r.display} — skills ({len(r.skills)})")
        table.add_column("名称", style="cyan")
        table.add_column("链接", justify="center")
        table.add_column("路径", style="dim")
        for s in sorted(r.skills, key=lambda x: x.name):
            mark = "[green]→link[/green]" if s.linked else " "
            table.add_row(s.name, mark, str(s.path))
        console.print(table)


def print_mcp_detail(reports: list[ToolReport]) -> None:
    for r in reports:
        if not r.mcp_servers:
            continue
        table = Table(title=f"{r.display} — MCP ({len(r.mcp_servers)})")
        table.add_column("名称", style="cyan")
        table.add_column("类型")
        table.add_column("command / url", style="dim")
        for m in r.mcp_servers:
            target = m.command or m.url or "?"
            table.add_row(m.name, m.transport, str(target))
        console.print(table)


def print_plugins_detail(reports: list[ToolReport]) -> None:
    for r in reports:
        if not r.plugins:
            continue
        table = Table(title=f"{r.display} — 插件 ({len(r.plugins)})")
        table.add_column("ID", style="cyan")
        table.add_column("版本")
        table.add_column("启用", justify="center")
        table.add_column("市场", style="dim")
        for p in r.plugins:
            enabled = {True: "[green]✓[/green]", False: "[red]✗[/red]", None: "-"}[p.enabled]
            table.add_row(p.plugin_id, p.version or "-", enabled, p.marketplace or "-")
        console.print(table)


# ---------------------------------------------------------------------------
# 覆盖矩阵（默认视图）：条目为行、工具为列、交叉点 ✓/·


def _print_matrix(title: str, first_col: str, reports: list[ToolReport],
                  has_entry: callable, names: list[str]) -> None:
    table = Table(title=title)
    table.add_column(first_col, style="cyan", no_wrap=True)
    for r in reports:
        table.add_column(r.tool, justify="center")
    for name in names:
        row = [name]
        for r in reports:
            row.append("[green]✓[/green]" if has_entry(r, name) else "[dim]·[/dim]")
        table.add_row(*row)
    console.print(table)


def print_skills_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.skills]
    names = sorted({s.name for r in capable for s in r.skills})
    _print_matrix(f"⚓ 矩阵：AI 编码工具 × Skills（{len(names)}）", "Skill \\ 工具",
                  capable, lambda r, n: any(s.name == n for s in r.skills), names)


def print_mcp_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.mcp_servers]
    names = sorted({m.name for r in capable for m in r.mcp_servers})
    _print_matrix(f"⚓ 矩阵：AI 编码工具 × MCP（{len(names)}）", "Server \\ 工具",
                  capable, lambda r, n: any(m.name == n for m in r.mcp_servers), names)


def print_plugins_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.plugins]
    names = sorted({p.plugin_id for r in capable for p in r.plugins})
    _print_matrix(f"⚓ 矩阵：AI 编码工具 × 插件（{len(names)}）", "插件 / 扩展 \\ 工具",
                  capable, lambda r, n: any(p.plugin_id == n for p in r.plugins), names)
