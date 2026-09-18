"""hcm 命令行入口：只有两个命令——scan（看）/ sync（同步）。"""

from __future__ import annotations

import json as _json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .detect import detect_tools

app = typer.Typer(
    help="agent-config-manager: AI 编码工具的用户级 skills / MCP / 插件 统一检测、盘点、分发，并支持项目级跨助手历史会话。",
    no_args_is_help=True,
)
console = Console()

sessions_app = typer.Typer(help="查看并按需复用当前项目在多个 AI 编码工具中的历史会话。")
app.add_typer(sessions_app, name="sessions")


@app.callback()
def _root() -> None:
    """agent-config-manager: AI 编码工具的用户级 skills / MCP / 插件 统一检测、盘点、分发，并支持项目级跨助手历史会话。"""


# ---------------------------------------------------------------------------
# sessions：项目级跨工具历史会话


@sessions_app.command("list")
def sessions_list(
    project: Path = typer.Option(Path("."), "--project", "-p", help="项目路径，默认当前目录"),
    tool: list[str] = typer.Option([], "--tool", "-t", help="按来源工具过滤，可多选"),
    limit: int = typer.Option(30, "--limit", "-n", min=1, help="最多显示条数"),
    all_sessions: bool = typer.Option(False, "--all", help="显示全部，忽略 --limit"),
    all_projects: bool = typer.Option(False, "--all-projects", help="列出所有项目的会话（忽略 --project）"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """列出当前项目（或所有项目）在本地 AI 编码助手中的历史会话。"""
    from .sessions import scan_sessions, session_to_dict

    project = project.expanduser().resolve(strict=False)
    items = scan_sessions(None if all_projects else project, tools=tool or None)
    if not all_sessions:
        items = items[:limit]
    if json_out:
        console.print_json(_json.dumps({
            "project": "all" if all_projects else str(project),
            "count": len(items),
            "sessions": [session_to_dict(x) for x in items],
        }, ensure_ascii=False))
        return
    table = Table(title=f"Sessions — {project}")
    table.add_column("Ref", style="cyan", no_wrap=True)
    table.add_column("Updated")
    table.add_column("Messages", justify="right")
    table.add_column("Branch", style="dim")
    table.add_column("Title", style="dim")
    from .sessions import _iso
    for item in items:
        table.add_row(
            item.ref,
            _iso(item.updated_at) or "-",
            str(item.message_count),
            item.branch or "-",
            (item.title or "-")[:100],
        )
    console.print(table)
    console.print("[dim]按需查看：hcm sessions show <ref> --transcript；交接：hcm sessions context <ref>[/dim]")


@sessions_app.command()
def show(
    ref: str = typer.Argument(help="会话引用，形如 tool:session-id"),
    project: Path = typer.Option(Path("."), "--project", "-p"),
    transcript: bool = typer.Option(False, "--transcript", help="显式读取并输出会话文本"),
    tail: int = typer.Option(80, "--tail", min=1, help="仅输出最后 N 条消息"),
    include_tools: bool = typer.Option(False, "--include-tools", help="包含工具调用/结果（默认只读用户与助手文本）"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """查看一个历史会话的 metadata，或显式读取其 transcript。"""
    from .sessions import find_session, format_transcript, read_session, session_to_dict

    project = project.expanduser().resolve(strict=False)
    try:
        info = find_session(ref, project)
        messages = read_session(ref, project, include_tools) if transcript else []
    except (KeyError, RuntimeError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if json_out:
        payload: dict[str, object] = {"session": session_to_dict(info)}
        if transcript:
            payload["transcript"] = format_transcript(messages, tail)
        console.print_json(_json.dumps(payload, ensure_ascii=False))
        return
    console.print_json(_json.dumps(session_to_dict(info), ensure_ascii=False))
    if transcript:
        console.print(format_transcript(messages, tail))


@sessions_app.command()
def search(
    query: str = typer.Argument(help="关键词，大小写不敏感"),
    project: Path = typer.Option(Path("."), "--project", "-p"),
    tool: list[str] = typer.Option([], "--tool", "-t"),
    limit: int = typer.Option(20, "--limit", "-n", min=1),
    all_projects: bool = typer.Option(False, "--all-projects", help="搜索所有项目的会话"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """在当前项目（或所有项目）的历史会话文本中搜索。"""
    from .sessions import read_session, scan_sessions

    project = project.expanduser().resolve(strict=False)
    needle = query.casefold()
    hits: list[tuple[object, str]] = []
    for info in scan_sessions(None if all_projects else project, tools=tool or None):
        try:
            messages = read_session(info.ref, None if all_projects else project, include_tools=False)
        except (RuntimeError, OSError):
            continue
        matched = [m for m in messages if needle in m.text.casefold()]
        if not matched:
            continue
        snippet = matched[0].text
        if len(snippet) > 360:
            snippet = snippet[:360] + "…"
        hits.append((info, snippet))
        if len(hits) >= limit:
            break
    if json_out:
        from .sessions import session_to_dict
        console.print_json(_json.dumps({
            "project": str(project), "query": query, "hits": [
                {"session": session_to_dict(i), "snippet": s} for i, s in hits
            ]
        }, ensure_ascii=False))
        return
    table = Table(title=f"Session search — {query}")
    table.add_column("Ref", style="cyan", no_wrap=True)
    table.add_column("Snippet", style="dim")
    for info, snippet in hits:
        table.add_row(info.ref, snippet.replace("\n", " "))
    console.print(table)


@sessions_app.command()
def context(
    ref: str = typer.Argument(help="会话引用，形如 tool:session-id"),
    project: Path = typer.Option(Path("."), "--project", "-p"),
    tail: int = typer.Option(40, "--tail", min=1),
    output: Path = typer.Option(None, "--output", "-o", help="写入 handoff Markdown 文件"),
) -> None:
    """从指定历史会话生成确定性、脱敏的交接上下文。"""
    from .sessions import build_context

    project = project.expanduser().resolve(strict=False)
    try:
        text = build_context(ref, project, tail)
    except (KeyError, RuntimeError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if output is not None:
        output = output.expanduser().resolve(strict=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]handoff[/green] {ref} -> {output}")
    else:
        console.print(text)


@sessions_app.command()
def install(
    apply: bool = typer.Option(False, "--apply", help="实际安装（默认 dry-run）"),
) -> None:
    """把 hcm-sessions skill 安装到所有已检测工具，供任意助手调用历史会话。"""
    from .config import load_config
    from .detect import detect_tools
    from .sessions import install_session_skill

    installed = [d.tool for d in detect_tools() if d.installed]
    lines = install_session_skill(load_config(), installed, apply)
    for line in lines:
        style = "yellow" if line.startswith("conflict ") else None
        console.print(f"  {'[apply]' if apply else '[plan]'} {line}", style=style)


# ---------------------------------------------------------------------------
# version：桌面 App / 脚本探测用


@app.command()
def version(
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """显示 hcm 版本。"""
    from importlib.metadata import PackageNotFoundError, version as pkg_version

    try:
        v = pkg_version("harness-config-manager")
    except PackageNotFoundError:
        v = "0.0.0+dev"
    if json_out:
        console.print_json(_json.dumps({"name": "hcm", "version": v}, ensure_ascii=False))
    else:
        console.print(f"hcm {v}")


# ---------------------------------------------------------------------------
# sessions projects：按项目聚合（桌面 App 第三维度）


@sessions_app.command("projects")
def sessions_projects(
    project: Path = typer.Option(Path("."), "--project", "-p", help="用于标记当前项目"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """枚举所有本地项目及其跨助手会话分布。"""
    from datetime import datetime, timezone

    from .sessions import _iso, scan_sessions

    def _same_path(a: str, b: Path) -> bool:
        if not a:
            return False
        try:
            return Path(a).expanduser().resolve(strict=False) == b
        except OSError:
            return False

    def _project_kind(path_str: str) -> str:
        """project=真实项目；temp/dated/virtual/stale=非项目目录（下拉中折叠展示）。"""
        import re as _re

        if not path_str:
            return "virtual"
        raw = Path(path_str).expanduser()
        try:
            resolved = raw.resolve(strict=False)
        except OSError:
            resolved = raw
        rs = str(resolved)
        if rs in ("/tmp", "/private/tmp", "/var/tmp"):
            return "temp"
        if rs.startswith("/private/var/folders/") and rs.endswith("/T"):
            return "temp"
        home = Path.home()
        for base, kind in (
            (home / ".zcode/workspace", "virtual"),
        ):
            try:
                if resolved.is_relative_to(base):
                    return kind
                if raw.is_relative_to(base):
                    return kind
            except (OSError, ValueError):
                continue
        codex_root = home / "Documents/Codex"
        for cand in (raw, resolved):
            try:
                if cand.is_relative_to(codex_root):
                    first = cand.relative_to(codex_root).parts[0]
                    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", first):
                        return "dated"
            except (OSError, ValueError):
                continue
        if not resolved.exists():
            return "stale"
        return "project"

    current = project.expanduser().resolve(strict=False)
    agg: dict[str, dict] = {}
    for item in scan_sessions(None):
        key = str(item.project) if item.project else ""
        slot = agg.setdefault(key, {"tools": {}, "sessions": 0, "messages": 0, "last": None})
        slot["sessions"] += 1
        slot["messages"] += item.message_count
        slot["tools"][item.tool] = slot["tools"].get(item.tool, 0) + 1
        stamp = item.updated_at or item.started_at
        if stamp and (slot["last"] is None or stamp > slot["last"]):
            slot["last"] = stamp

    far_past = datetime.min.replace(tzinfo=timezone.utc)
    rows = sorted(agg.items(), key=lambda kv: kv[1]["last"] or far_past, reverse=True)
    payload = [
        {
            "path": key,
            "name": Path(key).name if key else "(未知项目)",
            "sessions": v["sessions"],
            "messages": v["messages"],
            "tools": sorted(v["tools"]),
            "tool_counts": v["tools"],
            "last_activity": _iso(v["last"]),
            "current": _same_path(key, current),
            "kind": _project_kind(key),
        }
        for key, v in rows
    ]
    if json_out:
        console.print_json(_json.dumps({"count": len(payload), "projects": payload}, ensure_ascii=False))
        return
    table = Table(title="Projects — 所有本地项目")
    table.add_column("", justify="center")
    table.add_column("项目", style="cyan")
    table.add_column("会话", justify="right")
    table.add_column("消息", justify="right")
    table.add_column("助手")
    table.add_column("最近活动", style="dim")
    for row in payload:
        table.add_row(
            "●" if row["current"] else "",
            row["path"] or row["name"],
            str(row["sessions"]),
            str(row["messages"]),
            ", ".join(row["tools"]) or "-",
            row["last_activity"] or "-",
        )
    console.print(table)
    console.print("[dim]选择项目：hcm sessions list --project <path>；全部项目：--all-projects[/dim]")


# ---------------------------------------------------------------------------
# scan：detect + 盘点 + doctor，只读全家桶


@app.command()
def scan(
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
    detail: list[str] = typer.Option(
        [], "--detail", "-d",
        help="查看某层明细，可多选：skills / mcp / plugins / hooks / agents / sessions",
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
        doctor = [
            {"level": level, "where": where, "message": msg}
            for level, where, msg in run_doctor()
        ]
        console.print_json(_json.dumps({
            "tools_detected": [d.__dict__ for d in detections if d.installed],
            "inventory": _json.loads(rp.to_json(reports)),
            "doctor": doctor,
        }, ensure_ascii=False))
        return

    console.print(f"⚓ 检测到 [cyan]{n_installed}[/cyan] 个 AI 编码工具")
    rp.print_summary(reports)

    # 默认显示五层覆盖矩阵（每个条目铺到了哪些工具）
    if detail:
        if "skills" in detail:
            rp.print_skills_detail(reports)
        if "mcp" in detail:
            rp.print_mcp_detail(reports)
        if "plugins" in detail:
            rp.print_plugins_detail(reports)
        if "hooks" in detail:
            rp.print_hooks_detail(reports)
        if "agents" in detail:
            rp.print_agents_detail(reports)
        if "sessions" in detail:
            rp.print_sessions_detail(reports)
    else:
        rp.print_skills_matrix(reports)
        rp.print_agents_matrix(reports)
        rp.print_mcp_matrix(reports)
        rp.print_plugins_matrix(reports)
        rp.print_hooks_matrix(reports)
        rp.print_sessions_matrix(reports)
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
    layer_hooks: bool = typer.Option(True, "--hooks/--no-hooks", help="同步 hooks 层"),
    layer_agents: bool = typer.Option(True, "--agents/--no-agents", help="同步 subagents 层"),
    layer_sessions: bool = typer.Option(True, "--sessions/--no-sessions", help="分发跨工具历史会话查询 skill"),
    apply: bool = typer.Option(False, "--apply", help="实际执行（默认 dry-run）"),
    prefer: str = typer.Option("skip", help="冲突处理：skip（默认跳过）/ library（备份工具侧后以清单覆盖）"),
    source: str = typer.Option("auto", "--from", help="清单为空时的收集源（默认自动选最全的工具）"),
) -> None:
    """同步一下：清单/库 -> 所有工具。清单为空会自动从最全的工具收集；默认 dry-run。"""
    from .agents import (
        plan_adopt as plan_agent_adopt,
        plan_sync as plan_agent_sync,
        resolve_agents_library,
        run_adopt as run_agent_adopt,
        run_sync as run_agent_sync,
    )
    from .config import load_config
    from .detect import detect_tools
    from .hooks_manifest import (
        adopt_hooks,
        auto_hook_source,
        load_manifest as load_hook_manifest,
    )
    from .hooks_write import sync_hooks
    from .mcp_manifest import _load_secrets, auto_mcp_source, load_manifest
    from .mcp_write import sync_mcp
    from .plugin_sync import auto_plugin_source, load_plugin_manifest, sync_plugins
    from .scan import scan_all
    from .sessions import install_session_skill
    from .skills import plan_adopt, plan_sync, resolve_library, run_adopt, run_sync

    if prefer not in ("skip", "library"):
        console.print("[red]--prefer 仅支持 skip / library[/red]")
        raise typer.Exit(2)

    installed = [d.tool for d in detect_tools() if d.installed]
    reports = scan_all()
    cfg = load_config()

    # --- sessions 层：只分发查询 skill；绝不迁移 / 写入任何工具原生 session 文件 ---
    if layer_sessions:
        console.print("\nsession continuity: 项目级历史会话查询 skill")
        for line in install_session_skill(cfg, installed, apply):
            console.print(f"  {'[apply]' if apply else '[plan]'} {line}"
                          if not line.startswith("conflict ") else f"  [yellow]{line}[/yellow]")

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

    # --- hooks 层：清单为空则自动收集（含第三方注入条目；exclude_hooks 黑名单兜底） ---
    if layer_hooks:
        specs = [s for s in load_hook_manifest() if s.id not in cfg.exclude_hooks]
        if not specs:
            src = source if source != "auto" else auto_hook_source(installed)
            console.print(f"[blue]hooks 清单为空，自动从 {src} 收集[/blue]")
            for line in adopt_hooks(src, apply):
                console.print(f"  {'[apply]' if apply else '[dry-run]'} {line}")
            specs = ([s for s in load_hook_manifest() if s.id not in cfg.exclude_hooks]
                     if apply else [])
        if specs:
            console.print(f"hooks 清单: {len(specs)} 条")
            for line in sync_hooks(specs, installed, apply, prefer):
                console.print(f"  {'[apply]' if apply else '[plan]'} {line}"
                              if not line.startswith(("[red]", "[yellow]")) else f"  {line}")
        elif apply:
            console.print("[yellow]hooks 层跳过（清单仍为空）[/yellow]")

    # --- subagents 层：库外独有自动收集，然后逐条 symlink 分发 ---
    if layer_agents:
        agents_lib = resolve_agents_library(cfg, create=apply)
        agent_plan = plan_agent_adopt(agents_lib, reports)
        if agent_plan.to_adopt:
            console.print(f"[blue]库外独有 subagents {len(agent_plan.to_adopt)} 个，自动收集[/blue]")
            for line in run_agent_adopt(agent_plan, agents_lib, apply):
                console.print(f"  {'[apply]' if apply else '[dry-run]'} {line}")
        for tool, name, path, other in agent_plan.conflicts:
            console.print(f"  [yellow]同名冲突[/yellow] {name} @ {tool}（多工具内容不一致，需人工裁决）")

        agent_actions = plan_agent_sync(agents_lib, reports, cfg.exclude_agents)
        agent_counts: dict[str, int] = {}
        for a in agent_actions:
            agent_counts[a.kind] = agent_counts.get(a.kind, 0) + 1
        console.print(f"\nsubagents 事实源库: {agents_lib}")
        console.print("计划: " + ", ".join(f"{k}×{v}" for k, v in sorted(agent_counts.items())))
        if not apply:
            console.print("[dim]dry-run 模式（--apply 生效）[/dim]")
        for line in run_agent_sync(agent_actions, agents_lib, apply, prefer):
            style = {"link": "green", "relink": "yellow", "replace": "yellow",
                     "conflict": "red"}.get(line.split()[0], None)
            console.print(f"  {'[apply]' if apply else '[plan]'} {line}", style=style)
        for a in agent_actions:
            if a.kind == "adopt-hint":
                console.print(f"  [blue]提示[/blue] {a.tool}:{a.agent} 仅该工具有")

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
