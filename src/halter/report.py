"""报告输出：rich 表格 + JSON 序列化。"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

from rich import box
from rich.console import Console
from rich.table import Table

from .model import ToolReport
from .sessions import _iso, session_to_dict

console = Console()


def to_json(reports: list[ToolReport]) -> str:
    payload = []
    for r in reports:
        payload.append({
            "tool": r.tool,
            "display": r.display,
            "installed": r.installed,
            "category": r.category,
            "skills": [
                {"name": s.name, "path": str(s.path), "linked": s.linked,
                 "description": s.description}
                for s in r.skills
            ],
            "agents": [
                {"name": a.name, "path": str(a.path), "linked": a.linked,
                 "model": a.model}
                for a in r.agents
            ],
            "memory": None if r.memory is None else {
                "path": str(r.memory.path), "present": r.memory.present,
                "linked": r.memory.linked, "size": r.memory.size,
                "mtime": r.memory.mtime,
            },
            "statusline": None if r.statusline is None else {
                "settings_path": str(r.statusline.settings_path),
                "present": r.statusline.present,
                "command": r.statusline.command,
                "items": r.statusline.items,
                "script": None if r.statusline.script is None else str(r.statusline.script),
                "linked": r.statusline.linked,
                "synced": r.statusline.synced,
            },
            "mcp_servers": [
                {"name": m.name, "transport": m.transport, "command": m.command,
                 "url": m.url, "extra": m.extra}
                for m in r.mcp_servers
            ],
            "plugins": [dataclasses.asdict(p) for p in r.plugins],
            "mods": [dataclasses.asdict(m) for m in r.mods],
            "sessions": [session_to_dict(x) for x in r.sessions],
            "hooks": [
                {"event": h.event, "label": h.label, "type": h.type,
                 "matcher": h.matcher, "command": h.command, "timeout": h.timeout,
                 "extra": h.extra}
                for h in r.hooks
            ],
            "notes": r.scan_notes,
        })
    return json.dumps(payload, ensure_ascii=False, indent=2)


def print_summary(reports: list[ToolReport]) -> None:
    table = Table(title="⚓ AI 编码工具配置盘点")
    table.add_column("工具", style="cyan")
    table.add_column("Skills", justify="right")
    table.add_column("Subagents", justify="right")
    table.add_column("Memory", justify="center")
    table.add_column("Statusline", justify="center")
    table.add_column("MCP", justify="right")
    table.add_column("插件", justify="right")
    table.add_column("Mods", justify="right")
    table.add_column("Hooks", justify="right")
    table.add_column("Sessions", justify="right")
    for r in reports:
        linked = sum(1 for s in r.skills if s.linked)
        skills_cell = str(len(r.skills)) + (f" [green]({linked}链)[/green]" if linked else "")
        memory_cell = "-"
        if r.memory is not None:
            if r.memory.linked:
                memory_cell = "[green]●[/green]"
            elif r.memory.present:
                memory_cell = "[yellow]◐[/yellow]"
            else:
                memory_cell = "[dim]·[/dim]"
        statusline_cell = "-"
        if r.statusline is not None:
            if r.statusline.linked:
                statusline_cell = "[green]●[/green]"
            elif r.statusline.present:
                statusline_cell = "[yellow]◐[/yellow]"
            else:
                statusline_cell = "[dim]·[/dim]"
        table.add_row(
            r.display,
            skills_cell,
            str(len(r.agents)) if r.agents else "-",
            memory_cell,
            statusline_cell,
            str(len(r.mcp_servers)),
            str(len(r.plugins)),
            str(len(r.mods)) if r.mods else "-",
            str(len(r.hooks)) if r.hooks else "-",
            str(len(r.sessions)) if r.sessions else "-",
        )
    console.print(table)


def _first_sentence(text: str, limit: int = 60) -> str:
    """取描述首句并截断（表格紧凑展示；全文见 --json 或桌面端悬停）。"""
    import re

    body = text.strip().splitlines()[0] if text.strip() else ""
    match = re.search(r"[.。!！?？]", body)
    first = body[: match.end()] if match else body
    if len(first) > limit:
        return first[: limit - 1].rstrip() + "…"
    return first


def print_skills_detail(reports: list[ToolReport]) -> None:
    for r in reports:
        if not r.skills:
            continue
        table = Table(title=f"{r.display} — skills ({len(r.skills)})")
        table.add_column("名称", style="cyan", no_wrap=True)
        table.add_column("链接", justify="center", no_wrap=True)
        table.add_column("描述", style="dim", ratio=1, overflow="fold")
        for s in sorted(r.skills, key=lambda x: x.name):
            mark = "[green]→link[/green]" if s.linked else " "
            table.add_row(s.name, mark, _first_sentence(s.description or ""))
        console.print(table)


def print_agents_detail(reports: list[ToolReport]) -> None:
    for r in reports:
        if not r.agents:
            continue
        table = Table(title=f"{r.display} — subagents ({len(r.agents)})")
        table.add_column("名称", style="cyan")
        table.add_column("链接", justify="center")
        table.add_column("model", style="dim")
        table.add_column("路径", style="dim")
        for a in sorted(r.agents, key=lambda x: x.name):
            mark = "[green]→link[/green]" if a.linked else " "
            table.add_row(a.name, mark, a.model or "-", str(a.path))
        console.print(table)


def print_memory_detail(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.memory is not None]
    if not capable:
        return
    table = Table(title=f"⚓ 用户级记忆 (memory)")
    table.add_column("工具", style="cyan")
    table.add_column("状态", justify="center")
    table.add_column("文件", style="dim")
    table.add_column("大小", justify="right")
    for r in capable:
        m = r.memory
        if m.linked:
            state = "[green]● 已同步[/green]"
        elif m.present:
            state = "[yellow]◐ 本地文件[/yellow]"
        else:
            state = "[dim]· 缺失[/dim]"
        size = f"{m.size}B" if m.present else "-"
        table.add_row(r.display, state, str(m.path), size)
    console.print(table)
    console.print("[dim]● 为指向事实源的 symlink（halter sync 分发）；◐ 为本地独立文件（内容可能与库漂移）[/dim]")


def print_statusline_detail(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.statusline is not None]
    if not capable:
        return
    table = Table(title="⚓ 状态栏 (statusline)")
    table.add_column("工具", style="cyan")
    table.add_column("状态", justify="center")
    table.add_column("command", style="dim")
    table.add_column("settings", style="dim")
    for r in capable:
        s = r.statusline
        if s.linked:
            state = "[green]● 已同步[/green]"
        elif s.present:
            state = "[yellow]◐ 本地配置[/yellow]"
        else:
            state = "[dim]· 未配置[/dim]"
        target = s.command or (", ".join(s.items) if s.items else None) or "-"
        table.add_row(r.display, state, target, str(s.settings_path))
    console.print(table)
    console.print("[dim]● 为脚本指向事实源库的 symlink + statusLine 键与库一致；◐ 为本地独立配置（可能与库漂移）[/dim]")


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


def print_mods_detail(reports: list[ToolReport]) -> None:
    shown = False
    for r in reports:
        if not r.mods:
            continue
        shown = True
        table = Table(title=f"{r.display} — mods ({len(r.mods)})")
        table.add_column("ID", style="cyan")
        table.add_column("版本")
        table.add_column("启用", justify="center")
        table.add_column("来源", style="dim")
        table.add_column("事件模块", style="dim")
        for m in r.mods:
            enabled = {True: "[green]✓[/green]", False: "[red]✗[/red]", None: "-"}[m.enabled]
            source = m.marketplace if m.origin == "marketplace" else f"@{m.origin}"
            table.add_row(m.plugin_id, m.version or "-", enabled, source or "-",
                          ", ".join(m.modules) or "-")
        console.print(table)
    if shown:
        console.print("[dim]mod = 自带进程内 JS/TS 事件模块的插件（Claude Code 2.1.287+）；安装与启停走插件层[/dim]")


def print_sessions_detail(reports: list[ToolReport]) -> None:
    for r in reports:
        if not r.sessions:
            continue
        table = Table(title=f"{r.display} — sessions ({len(r.sessions)})")
        table.add_column("Ref", style="cyan", no_wrap=True)
        table.add_column("Updated")
        table.add_column("Messages", justify="right")
        table.add_column("Title", style="dim")
        for s in sorted(r.sessions, key=lambda x: x.updated_at or x.started_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
            table.add_row(s.ref, _iso(s.updated_at), str(s.message_count), (s.title or "-")[:80])
        console.print(table)


def print_hooks_detail(reports: list[ToolReport]) -> None:
    for r in reports:
        if not r.hooks:
            continue
        table = Table(title=f"{r.display} — hooks ({len(r.hooks)})")
        table.add_column("事件", style="cyan", no_wrap=True)
        table.add_column("名称")
        table.add_column("matcher", style="dim")
        table.add_column("command / prompt", style="dim")
        for h in r.hooks:
            target = h.command or (f"[prompt] {h.extra.get('prompt', '')[:40]}" if h.type == "prompt" else "?")
            target = str(target)
            table.add_row(h.event, h.label, str(h.matcher or "-"),
                          target[:80] + "…" if len(target) > 80 else target)
        console.print(table)


# ---------------------------------------------------------------------------
# 覆盖矩阵（默认视图）：条目为行、工具为列、交叉点 ●/◐/·


def _print_matrix(label: str, first_col: str, reports: list[ToolReport],
                  get_entry: callable, names: list[str], *,
                  linked: bool = False, merge_base: bool = False) -> None:
    """极简线框矩阵：缺口优先 + 全覆盖折叠 + 信号点交叉格。

    - linked 层（skills/agents）区分 symlink(●)/本地拷贝(◐)；
    - merge_base 层（插件）按 @marketplace 前的基名合并同一逻辑条目；
    - 只展开有缺口的行（缺口多者在前），全覆盖条目折叠为一行摘要。
    """
    if not reports or not names:
        return

    def canon(name: str) -> str:
        return name.split("@", 1)[0] if merge_base else name

    # 基名合并：任一变体存在即算该工具已配置
    rows: dict[str, dict[str, object]] = {}
    for name in names:
        key = canon(name)
        slot = rows.setdefault(key, {})
        for r in reports:
            entry = get_entry(r, name)
            if entry is None:
                continue
            prev = slot.get(r.tool)
            if prev is None:
                slot[r.tool] = entry
            elif getattr(entry, "linked", False) and not getattr(prev, "linked", False):
                slot[r.tool] = entry  # symlink(●) 优先于本地拷贝(◐)

    gapped = [(n, cells) for n, cells in rows.items() if len(cells) < len(reports)]
    complete = sorted(n for n, cells in rows.items() if len(cells) == len(reports))
    gapped.sort(key=lambda kv: (-(len(reports) - len(kv[1])), kv[0]))
    gaps = sum(len(reports) - len(cells) for _, cells in rows.items())

    console.print()
    if not gapped:
        console.print(f"[bold cyan]▸ {label}[/bold cyan]"
                      f"  [dim]{len(rows)} × {len(reports)} · [/dim][green]✓ 全部覆盖[/green]")
        return
    stats = f"{len(rows)} × {len(reports)} · gap {gaps}"
    if complete:
        stats += f" · [green]✓{len(complete)}[/green] 折叠"
    console.print(f"[bold cyan]▸ {label}[/bold cyan]  [dim]{stats}[/dim]")

    table = Table(
        box=box.MINIMAL_HEAVY_HEAD,
        header_style="dim",
        pad_edge=False,
        show_edge=False,
        collapse_padding=True,
    )
    table.add_column(first_col, style="cyan", overflow="ellipsis")
    for r in reports:
        table.add_column(r.tool, justify="center", no_wrap=True)
    for name, cells in gapped:
        row = [name]
        for r in reports:
            entry = cells.get(r.tool)
            if entry is None:
                row.append("[dim]·[/dim]")
            elif linked and getattr(entry, "linked", False):
                row.append("[green]●[/green]")
            elif linked:
                row.append("[yellow]◐[/yellow]")
            else:
                row.append("●")
        table.add_row(*row)
    console.print(table)
    if complete:
        shown = "、".join(complete[:6]) + ("…" if len(complete) > 6 else "")
        console.print(f"  [green]✓[/green] [dim]全覆盖 {len(complete)} 项：{shown}[/dim]")


def print_skills_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.skills]
    names = sorted({s.name for r in capable for s in r.skills})
    _print_matrix("SKILLS", "SKILL", capable,
                  lambda r, n: next((s for s in r.skills if s.name == n), None),
                  names, linked=True)


def print_agents_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.agents]
    names = sorted({a.name for r in capable for a in r.agents})
    _print_matrix("SUBAGENTS", "AGENT", capable,
                  lambda r, n: next((a for a in r.agents if a.name == n), None),
                  names, linked=True)


def print_memory_matrix(reports: list[ToolReport]) -> None:
    """单条目层的矩阵：一行 MEMORY × 各工具的同步状态（缺失=· 本地=◐ 链接=●）。"""
    capable = [r for r in reports if r.memory is not None]
    if not capable:
        return
    _print_matrix("MEMORY", "MEMORY", capable,
                  lambda r, n: r.memory if r.memory.present else None,
                  ["MEMORY"], linked=True)


def print_statusline_matrix(reports: list[ToolReport]) -> None:
    """单条目层的矩阵：一行 STATUSLINE × 各工具的同步状态（缺失=· 本地=◐ 链接=●）。"""
    capable = [r for r in reports if r.statusline is not None]
    if not capable:
        return
    _print_matrix("STATUSLINE", "STATUSLINE", capable,
                  lambda r, n: r.statusline if r.statusline.present else None,
                  ["STATUSLINE"], linked=True)


def print_mcp_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.mcp_servers]
    names = sorted({m.name for r in capable for m in r.mcp_servers})
    _print_matrix("MCP", "SERVER", capable,
                  lambda r, n: next((m for m in r.mcp_servers if m.name == n), None), names)


def print_plugins_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.plugins]
    names = sorted({p.plugin_id for r in capable for p in r.plugins})
    _print_matrix("PLUGINS", "PLUGIN", capable,
                  lambda r, n: next((p for p in r.plugins if p.plugin_id == n), None),
                  names, merge_base=True)


def print_mods_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.mods]
    names = sorted({m.plugin_id for r in capable for m in r.mods})
    _print_matrix("MODS", "MOD", capable,
                  lambda r, n: next((m for m in r.mods if m.plugin_id == n), None),
                  names, merge_base=True)


def print_hooks_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.hooks]
    names = sorted({h.label for r in capable for h in r.hooks})
    _print_matrix("HOOKS", "HOOK", capable,
                  lambda r, n: next((h for h in r.hooks if h.label == n), None), names)


def print_sessions_matrix(reports: list[ToolReport]) -> None:
    capable = [r for r in reports if r.sessions]
    sessions = {s.ref for r in capable for s in r.sessions}
    if not capable or not sessions:
        return
    _print_matrix("SESSIONS", "SESSION", capable,
                  lambda r, n: next((s for s in r.sessions if s.ref == n), None),
                  sorted(sessions))
