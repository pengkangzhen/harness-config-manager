"""hcm 命令行入口：只有两个命令——scan（看）/ sync（同步）。"""

from __future__ import annotations

import json as _json

import typer
from rich.console import Console
from rich.table import Table

from .detect import detect_tools

app = typer.Typer(
    help="agent-config-manager: AI 编码工具的用户级 skills / MCP / 插件 统一检测、盘点与分发。",
    no_args_is_help=True,
)
console = Console()


@app.callback()
def _root() -> None:
    """agent-config-manager: AI 编码工具的用户级 skills / MCP / 插件 统一检测、盘点与分发。"""


# ---------------------------------------------------------------------------
# scan：detect + 盘点 + doctor，只读全家桶


@app.command()
def scan(
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
    detail: list[str] = typer.Option(
        [], "--detail", "-d",
        help="查看某层明细，可多选：skills / mcp / plugins",
    ),
) -> None:
    """看一眼：装了哪些工具、各配置了什么、有无健康问题。"""
    from . import report as rp
    from .doctor import run_doctor
    from .scan import scan_all

    detections = detect_tools()
    n_installed = sum(1 for d in detections if d.installed)

    reports = scan_all()
    if json_out:
        console.print_json(_json.dumps({
            "tools_detected": [d.__dict__ for d in detections if d.installed],
            "inventory": _json.loads(rp.to_json(reports)),
        }, ensure_ascii=False))
        return

    console.print(f"⚓ 检测到 [cyan]{n_installed}[/cyan] 个 AI 编码工具")
    rp.print_summary(reports)

    # 默认显示三层覆盖矩阵（每个条目铺到了哪些工具）
    if detail:
        if "skills" in detail:
            rp.print_skills_detail(reports)
        if "mcp" in detail:
            rp.print_mcp_detail(reports)
        if "plugins" in detail:
            rp.print_plugins_detail(reports)
    else:
        rp.print_skills_matrix(reports)
        rp.print_mcp_matrix(reports)
        rp.print_plugins_matrix(reports)
    notes = [(r.tool, n) for r in reports for n in r.scan_notes]
    if notes:
        console.print("[dim]扫描备注：[/dim]")
        for tool, note in notes:
            console.print(f"  [dim]{tool}: {note}[/dim]")

    issues = [(level, where, msg) for level, where, msg in run_doctor() if level != "ok"]
    if issues:
        console.print("\n[red]⚠ 健康问题：[/red]")
        for level, where, msg in issues:
            console.print(f"  [{'red' if level == 'error' else 'yellow'}]{where}[/] {msg}")


# ---------------------------------------------------------------------------
# sync：自动收集（adopt 已内化为前置步骤）+ 分发


@app.command()
def sync(
    layer_skills: bool = typer.Option(True, "--skills/--no-skills", help="同步 skills 层"),
    layer_mcp: bool = typer.Option(True, "--mcp/--no-mcp", help="同步 MCP 层"),
    layer_plugins: bool = typer.Option(True, "--plugins/--no-plugins", help="同步插件层"),
    apply: bool = typer.Option(False, "--apply", help="实际执行（默认 dry-run）"),
    prefer: str = typer.Option("skip", help="冲突处理：skip（默认跳过）/ library（备份工具侧后以清单覆盖）"),
    source: str = typer.Option("auto", "--from", help="清单为空时的收集源（默认自动选最全的工具）"),
) -> None:
    """同步一下：清单/库 -> 所有工具。清单为空会自动从最全的工具收集；默认 dry-run。"""
    from .config import load_config
    from .detect import detect_tools
    from .mcp_manifest import _load_secrets, auto_mcp_source, load_manifest
    from .mcp_write import sync_mcp
    from .plugin_sync import auto_plugin_source, load_plugin_manifest, sync_plugins
    from .scan import scan_all
    from .skills import plan_adopt, plan_sync, resolve_library, run_adopt, run_sync

    if prefer not in ("skip", "library"):
        console.print("[red]--prefer 仅支持 skip / library[/red]")
        raise typer.Exit(2)

    installed = [d.tool for d in detect_tools() if d.installed]
    reports = scan_all()
    cfg = load_config()

    # --- MCP 层：清单为空则自动收集 ---
    if layer_mcp:
        specs = load_manifest()
        if not specs:
            src = source if source != "auto" else auto_mcp_source(installed)
            console.print(f"[blue]MCP 清单为空，自动从 {src} 收集[/blue]")
            from .mcp_manifest import adopt_mcp
            for line in adopt_mcp(src, apply):
                console.print(f"  {'[apply]' if apply else '[dry-run]'} {line}")
            specs = load_manifest() if apply else []
        if specs:
            console.print(f"MCP 清单: {len(specs)} 个 server")
            for line in sync_mcp(specs, _load_secrets(), installed, apply, prefer):
                console.print(f"  {'[apply]' if apply else '[plan]'} {line}"
                              if not line.startswith(("[red]", "[yellow]")) else f"  {line}")
        elif apply:
            console.print("[yellow]MCP 层跳过（清单仍为空）[/yellow]")

    # --- 插件层：清单为空则自动收集 ---
    if layer_plugins:
        specs = load_plugin_manifest()
        if not specs:
            src = source if source != "auto" else auto_plugin_source()
            console.print(f"[blue]插件清单为空，自动从 {src} 收集[/blue]")
            from .plugin_sync import adopt_plugins
            for line in adopt_plugins(src, apply):
                console.print(f"  {'[apply]' if apply else '[dry-run]'} {line}")
            specs = load_plugin_manifest() if apply else []
        if specs:
            console.print(f"插件清单: {len(specs)} 个")
            for line in sync_plugins(specs, installed, apply):
                console.print(f"  {'[apply]' if apply else '[plan]'} {line}"
                              if not line.startswith(("[red]", "[yellow]")) else f"  {line}")
        elif apply:
            console.print("[yellow]插件层跳过（清单仍为空）[/yellow]")

    # --- skills 层：库外独有自动收集，然后分发 ---
    if layer_skills:
        library = resolve_library(cfg, create=apply)
        adopt_plan = plan_adopt(library, reports)
        if adopt_plan.to_adopt:
            console.print(f"[blue]库外独有 skills {len(adopt_plan.to_adopt)} 个，自动收集[/blue]")
            for line in run_adopt(adopt_plan, library, apply):
                console.print(f"  {'[apply]' if apply else '[dry-run]'} {line}")
        for tool, name, path, other in adopt_plan.conflicts:
            console.print(f"  [yellow]同名冲突[/yellow] {name} @ {tool}（多工具内容不一致，需人工裁决）")

        actions = plan_sync(library, reports, cfg.exclude_skills)
        counts: dict[str, int] = {}
        for a in actions:
            counts[a.kind] = counts.get(a.kind, 0) + 1
        console.print(f"\n事实源库: {library}")
        console.print("计划: " + ", ".join(f"{k}×{v}" for k, v in sorted(counts.items())))
        if not apply:
            console.print("[dim]dry-run 模式（--apply 生效）[/dim]")
        for line in run_sync(actions, library, apply, prefer):
            style = {"link": "green", "relink": "yellow", "replace": "yellow",
                     "conflict": "red"}.get(line.split()[0], None)
            console.print(f"  {'[apply]' if apply else '[plan]'} {line}", style=style)
        for a in actions:
            if a.kind == "adopt-hint":
                console.print(f"  [blue]提示[/blue] {a.tool}:{a.skill} 仅该工具有")
