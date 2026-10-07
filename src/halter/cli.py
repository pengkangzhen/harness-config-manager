"""halter 命令行入口：只有两个命令——scan（看）/ sync（同步）。"""

from __future__ import annotations

import json as _json
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from .detect import detect_tools

app = typer.Typer(
    help="agent-config-manager: AI 编码工具的用户级 skills / MCP / 插件 统一检测、盘点、分发，并支持项目级跨助手历史会话。",
    no_args_is_help=True,
)
console = Console()


def emit_json(text: str) -> None:
    """--json 机器输出的唯一出口：原样写 stdout。

    绝不走 rich 的 print_json——rich 在 FORCE_COLOR 等被判定为终端的
    环境下会给 JSON 加 ANSI 语法高亮，令上游 JSON.parse 直接失败。"""
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _short_time(value: datetime | None) -> str:
    """表格用的紧凑本地时间：当年显示 MM-DD HH:MM，往年显示日期。"""
    if value is None:
        return "-"
    local = value.astimezone()
    return local.strftime("%m-%d %H:%M") if local.year == datetime.now().year else local.strftime("%Y-%m-%d")


def _short_ref(ref: str, keep: int = 8) -> str:
    """tool:session-id -> tool:id前8位，find_session 支持唯一前缀匹配。"""
    tool, sep, session_id = ref.partition(":")
    return f"{tool}:{session_id[:keep]}…" if sep and len(session_id) > keep else ref


def _run_on_machine(machine: str, args: list[str], timeout: int = 180,
                    input_bytes: bytes | None = None) -> None:
    """在远程机器上执行 halter 子命令并转发输出（--machine 通道）。"""
    from .machines import get_machine, run_remote

    m = get_machine(machine)
    if m is None:
        console.print(f"[red]机器 {machine} 不存在（halter machines list 查看）[/red]")
        raise typer.Exit(2)
    rc, out, err = run_remote(m, args, input_bytes=input_bytes, timeout=timeout)
    if rc is None:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(1)
    text = out.decode("utf-8", errors="replace")
    if rc != 0:
        console.print((err.strip() or text).strip(), style="red")
        raise typer.Exit(rc if 0 < rc < 256 else 1)
    if "--json" in args:
        emit_json(text)
    else:
        console.print(f"⚓ [cyan]{machine}[/cyan]")
        console.print(text.rstrip())


sessions_app = typer.Typer(help="查看并按需复用当前项目在多个 AI 编码工具中的历史会话。")
app.add_typer(sessions_app, name="sessions")

memory_app = typer.Typer(help="查看并写入用户级记忆事实源（desktop 记忆面板的数据源）。")
app.add_typer(memory_app, name="memory")

machines_app = typer.Typer(help="管理跨机器同步的远程机器（ssh 通道，远端需装有 halter）。")
app.add_typer(machines_app, name="machines")

providers_app = typer.Typer(help="管理模型供应商并一键切换 claude / codex 的端点与模型（本机独立，不参与多机同步）。")
app.add_typer(providers_app, name="providers")

update_app = typer.Typer(help="管理 harness 升级：npm 渠道版本对比与一键升级（desktop 更新面板的数据源）。")
app.add_typer(update_app, name="update")

@app.callback()
def _root() -> None:
    """agent-config-manager: AI 编码工具的用户级 skills / MCP / 插件 统一检测、盘点、分发，并支持项目级跨助手历史会话。"""


# ---------------------------------------------------------------------------
# sessions：项目级跨工具历史会话


@sessions_app.command("list")
def sessions_list(
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    project: Path = typer.Option(Path("."), "--project", "-p", help="项目路径，默认当前目录"),
    tool: list[str] = typer.Option([], "--tool", "-t", help="按来源工具过滤，可多选"),
    limit: int = typer.Option(30, "--limit", "-n", min=1, help="最多显示条数"),
    all_sessions: bool = typer.Option(False, "--all", help="显示全部，忽略 --limit"),
    all_projects: bool = typer.Option(False, "--all-projects", help="列出所有项目的会话（忽略 --project）"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """列出当前项目（或所有项目）在本地 AI 编码助手中的历史会话。"""
    from .sessions import scan_sessions, session_to_dict

    if machine is not None:
        # 项目路径按原样转发（远端路径在本机不存在，解析交给远端 halter）
        fwd = ["sessions", "list", "--limit", str(limit)]
        if all_sessions:
            fwd.append("--all")
        fwd += (["--all-projects"] if all_projects else ["--project", str(project)])
        fwd += [x for t2 in tool if t2 for x in ("--tool", t2)]
        fwd += ["--json"] if json_out else []
        _run_on_machine(machine, fwd)
        return
    project = project.expanduser().resolve(strict=False)
    items = scan_sessions(None if all_projects else project, tools=tool or None)
    if not all_sessions:
        items = items[:limit]
    if json_out:
        emit_json(_json.dumps({
            "project": "all" if all_projects else str(project),
            "count": len(items),
            "sessions": [session_to_dict(x) for x in items],
        }, ensure_ascii=False))
        return
    if not items:
        scope = "所有项目" if all_projects else str(project)
        console.print(f"[dim]{scope} 暂无历史会话 — 与任一 AI 编码助手对话后即可出现在这里。[/dim]")
        return
    table = Table(title=f"Sessions — {'所有项目' if all_projects else project}")
    table.add_column("Ref", style="cyan", no_wrap=True)
    table.add_column("Updated", no_wrap=True)
    table.add_column("Msgs", justify="right")
    table.add_column("Branch", style="dim", no_wrap=True)
    table.add_column("Title", style="dim", ratio=1, overflow="ellipsis")
    for item in items:
        table.add_row(
            _short_ref(item.ref),
            _short_time(item.updated_at or item.started_at),
            str(item.message_count),
            item.branch or "-",
            item.title or "-",
        )
    console.print(table)
    console.print("[dim]按需查看：halter sessions show <ref> --transcript；交接：halter sessions context <ref>[/dim]")


@sessions_app.command()
def show(
    ref: str = typer.Argument(help="会话引用，形如 tool:session-id"),
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    project: Path = typer.Option(Path("."), "--project", "-p"),
    transcript: bool = typer.Option(False, "--transcript", help="显式读取并输出会话文本"),
    tail: int = typer.Option(80, "--tail", min=1, help="仅输出最后 N 条消息"),
    include_tools: bool = typer.Option(False, "--include-tools", help="包含工具调用/结果（默认只读用户与助手文本）"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """查看一个历史会话的 metadata，或显式读取其 transcript。"""
    from .sessions import find_session, format_transcript, read_session, session_to_dict

    if machine is not None:
        fwd = ["sessions", "show", "--project", str(project), "--tail", str(tail)]
        if transcript:
            fwd.append("--transcript")
        if include_tools:
            fwd.append("--include-tools")
        fwd += ["--json"] if json_out else []
        fwd += ["--", ref]
        _run_on_machine(machine, fwd)
        return
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
        emit_json(_json.dumps(payload, ensure_ascii=False))
        return
    data = session_to_dict(info)
    meta = Table.grid(padding=(0, 2))
    meta.add_column(style="cyan", justify="right", no_wrap=True)
    meta.add_column(overflow="fold")
    for key, label in (
        ("ref", "Ref"), ("tool", "Tool"), ("title", "Title"), ("project", "Project"),
        ("branch", "Branch"), ("model", "Model"), ("started_at", "Started"),
        ("updated_at", "Updated"), ("message_count", "Messages"),
    ):
        value = data.get(key)
        meta.add_row(label, "-" if value in (None, "") else str(value))
    console.print(Panel(meta, title=f"Session — {info.ref}", border_style="cyan"))
    if transcript:
        console.print(format_transcript(messages, tail))


@sessions_app.command()
def search(
    query: str = typer.Argument(help="关键词，大小写不敏感"),
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    project: Path = typer.Option(Path("."), "--project", "-p"),
    tool: list[str] = typer.Option([], "--tool", "-t"),
    limit: int = typer.Option(20, "--limit", "-n", min=1),
    all_projects: bool = typer.Option(False, "--all-projects", help="搜索所有项目的会话"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """在当前项目（或所有项目）的历史会话文本中搜索。"""
    from .sessions import read_session, scan_sessions

    if machine is not None:
        fwd = ["sessions", "search", "--limit", str(limit)]
        fwd += (["--all-projects"] if all_projects else ["--project", str(project)])
        fwd += [x for t2 in tool if t2 for x in ("--tool", t2)]
        fwd += ["--json"] if json_out else []
        fwd += ["--", query]
        _run_on_machine(machine, fwd, timeout=300)
        return
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
        emit_json(_json.dumps({
            "project": str(project), "query": query, "hits": [
                {"session": session_to_dict(i), "snippet": s} for i, s in hits
            ]
        }, ensure_ascii=False))
        return
    if not hits:
        console.print(f"[dim]没有匹配 “{query}” 的会话。[/dim]")
        return
    table = Table(title=f"Session search — {query}")
    table.add_column("Ref", style="cyan", no_wrap=True)
    table.add_column("Updated", no_wrap=True)
    table.add_column("Snippet", style="dim", ratio=1, overflow="fold")
    for info, snippet in hits:
        table.add_row(_short_ref(info.ref), _short_time(info.updated_at or info.started_at), snippet.replace("\n", " "))
    console.print(table)


@sessions_app.command()
def context(
    ref: str = typer.Argument(help="会话引用，形如 tool:session-id"),
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    project: Path = typer.Option(Path("."), "--project", "-p"),
    tail: int = typer.Option(40, "--tail", min=1),
    output: Path = typer.Option(None, "--output", "-o", help="写入 handoff Markdown 文件"),
) -> None:
    """从指定历史会话生成确定性、脱敏的交接上下文。"""
    from .sessions import build_context

    if machine is not None:
        fwd = ["sessions", "context", "--project", str(project), "--tail", str(tail)]
        if output is not None:
            fwd += ["--output", str(output)]
        fwd += ["--", ref]
        _run_on_machine(machine, fwd)
        return
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
    """把 halter-sessions skill 安装到所有已检测工具，供任意助手调用历史会话。"""
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
    """显示 halter 版本。"""
    from importlib.metadata import PackageNotFoundError, version as pkg_version

    try:
        v = pkg_version("halter")
    except PackageNotFoundError:
        v = "0.0.0+dev"
    if json_out:
        emit_json(_json.dumps({"name": "halter", "version": v}, ensure_ascii=False))
    else:
        console.print(f"⚓ [bold]halter[/bold] [cyan]{v}[/cyan]")


# ---------------------------------------------------------------------------
# sessions projects：按项目聚合（桌面 App 第三维度）


@sessions_app.command("projects")
def sessions_projects(
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    project: Path = typer.Option(Path("."), "--project", "-p", help="用于标记当前项目"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """枚举所有本地项目及其跨助手会话分布。"""
    from datetime import datetime, timezone

    from .sessions import _iso, scan_sessions

    if machine is not None:
        _run_on_machine(machine, [
            "sessions", "projects", "--project", str(project)]
            + (["--json"] if json_out else []))
        return

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
        emit_json(_json.dumps({"count": len(payload), "projects": payload}, ensure_ascii=False))
        return
    if not payload:
        console.print("[dim]未发现任何历史会话项目。[/dim]")
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
            "[green]●[/green]" if row["current"] else "·",
            row["path"] or row["name"],
            str(row["sessions"]),
            str(row["messages"]),
            ", ".join(row["tools"]) or "-",
            row["last_activity"] or "-",
        )
    console.print(table)
    console.print("[dim]选择项目：halter sessions list --project <path>；全部项目：--all-projects[/dim]")


# ---------------------------------------------------------------------------
# scan：detect + 盘点 + doctor，只读全家桶


@app.command()
def scan(
    machine: str = typer.Option(None, "--machine", help="扫描远程机器（halter machines list 查看）"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
    detail: list[str] = typer.Option(
        [], "--detail", "-d",
        help="查看某层明细，可多选：skills / mcp / plugins / hooks / agents / memory / sessions",
    ),
) -> None:
    """看一眼：装了哪些工具、各配置了什么、有无健康问题。"""
    from . import report as rp
    from .doctor import message_zh, run_doctor
    from .scan import scan_all

    if machine is not None:
        fwd = ["scan"]
        fwd += ["--json"] if json_out else []
        fwd += [arg for d in detail for arg in ("-d", d)]
        _run_on_machine(machine, fwd)
        return

    detections = detect_tools()
    n_installed = sum(1 for d in detections if d.installed)

    reports = scan_all(detections)
    if json_out:
        emit_json(_json.dumps({
            "tools_detected": [d.__dict__ for d in detections if d.installed],
            "inventory": _json.loads(rp.to_json(reports)),
            "doctor": run_doctor(),
        }, ensure_ascii=False))
        return

    console.print(f"⚓ 检测到 [cyan]{n_installed}[/cyan] 个 AI 编码工具")
    rp.print_summary(reports)
    console.print("[dim]Skills 中的 (n链) = 其中 n 个为指向事实源库的 symlink（halter sync 分发，一处修改全部生效）；其余为本地拷贝[/dim]")

    # 默认显示六层覆盖矩阵（每个条目铺到了哪些工具）
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
        if "memory" in detail:
            rp.print_memory_detail(reports)
        if "sessions" in detail:
            rp.print_sessions_detail(reports)
    else:
        rp.print_skills_matrix(reports)
        rp.print_agents_matrix(reports)
        rp.print_memory_matrix(reports)
        rp.print_mcp_matrix(reports)
        rp.print_plugins_matrix(reports)
        rp.print_hooks_matrix(reports)
        rp.print_sessions_matrix(reports)
        console.print("[dim]图例  [green]●[/green] symlink 同步 · [yellow]◐[/yellow] 本地拷贝 · · 缺失[/dim]")
    notes = [(r.tool, n) for r in reports for n in r.scan_notes]
    if notes:
        console.print("[dim]扫描备注：[/dim]")
        for tool, note in notes:
            console.print(f"  [dim]{tool}: {note}[/dim]")

    issues = [i for i in run_doctor() if i["level"] != "ok"]
    if issues:
        console.print("\n[red]⚠ 健康问题：[/red]")
        for i in issues:
            color = "red" if i["level"] == "error" else "yellow"
            console.print(f"  [{color}]{i['where']}[/] {message_zh(i)}")


# ---------------------------------------------------------------------------
# assess：五层配置对当前项目的有用度评估


@app.command()
def assess(
    project: Path = typer.Option(Path("."), "--project", "-p", help="被评估的项目路径，默认当前目录"),
    layer: list[str] = typer.Option([], "--layer", "-l",
                                    help="只评估指定层，可多选：skills / agents / mcp / plugins / hooks"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """根据项目画像（语言/框架/领域/云信号）评估五层配置的有用度。"""
    from .assess import VERDICT_LABEL, assess_all, profile_project
    from .scan import scan_all

    valid_layers = ("skills", "agents", "mcp", "plugins", "hooks")
    chosen = [x for x in layer if x] or list(valid_layers)
    bad = [x for x in chosen if x not in valid_layers]
    if bad:
        raise typer.BadParameter(f"未知层：{', '.join(bad)}（可选 {', '.join(valid_layers)}）")

    profile = profile_project(project)
    reports = scan_all(detect_tools())
    result = assess_all(profile, reports)
    result = {k: v for k, v in result.items() if k in chosen}

    if json_out:
        emit_json(_json.dumps({
            "project": str(profile.path),
            "profile": {
                "languages": sorted(profile.languages),
                "frameworks": sorted(profile.frameworks),
                "domains": sorted(profile.domains),
                "clouds": sorted(profile.clouds),
                "is_repo": profile.is_repo,
            },
            "summary": {
                k: {v: sum(1 for a in items if a.verdict == v) for v in ("useful", "maybe", "useless")}
                for k, items in result.items()
            },
            "assessments": [a.to_dict() for items in result.values() for a in items],
        }, ensure_ascii=False))
        return

    signals = profile.all_signals()
    console.print(f"[cyan]项目画像[/cyan] [dim]{profile.path}[/dim]")
    console.print(f"[dim]信号：{', '.join(sorted(signals)) or '无'}"
                  f"{' · git 仓库' if profile.is_repo else ''}[/dim]\n")

    style = {"useful": "green", "maybe": "yellow", "useless": "red"}
    order = {"useless": 0, "maybe": 1, "useful": 2}
    layer_title = {"skills": "Skills", "agents": "Subagents", "mcp": "MCP", "plugins": "插件", "hooks": "Hooks"}
    for key, items in result.items():
        if not items:
            continue
        counts = {v: sum(1 for a in items if a.verdict == v) for v in ("useful", "maybe", "useless")}
        table = Table(title=f"{layer_title[key]} 评估 — ✓{counts['useful']} ?{counts['maybe']} ✕{counts['useless']}")
        table.add_column("条目", style="cyan", no_wrap=True)
        table.add_column("判定", no_wrap=True)
        table.add_column("理由", ratio=1, overflow="fold")
        table.add_column("建议", no_wrap=True)
        table.add_column("工具", style="dim", no_wrap=True)
        for a in sorted(items, key=lambda x: (order[x.verdict], x.item)):
            table.add_row(
                a.item,
                f"[{style[a.verdict]}]{VERDICT_LABEL[a.verdict]}[/{style[a.verdict]}]",
                a.reason, a.suggestion, ",".join(a.tools),
            )
        console.print(table)
    console.print("[dim]✕ 无用项：skills 可从 ~/.agents/skills 删除；MCP/插件/hooks 可用 config exclude 或手动卸载。评估为启发式规则，人工复核后再删。[/dim]")


# ---------------------------------------------------------------------------
# tui：交互式全屏矩阵浏览器


@app.command()
def tui() -> None:
    """交互式全屏矩阵浏览器（1-6 切层 · 方向键看详情 · g 缺口 · / 过滤 · q 退出）。"""
    import sys

    from .scan import scan_all
    from .tui import HalterTui

    if not sys.stdout.isatty():
        console.print("[red]tui 需要交互式终端；管道/脚本场景请用 [cyan]halter scan[/cyan][/red]")
        raise typer.Exit(code=1)
    HalterTui(scan_all(detect_tools())).run()


# ---------------------------------------------------------------------------
# sync：自动收集（adopt 已内化为前置步骤）+ 分发


@app.command()
def sync(
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    layer_skills: bool = typer.Option(True, "--skills/--no-skills", help="同步 skills 层"),
    layer_mcp: bool = typer.Option(True, "--mcp/--no-mcp", help="同步 MCP 层"),
    layer_plugins: bool = typer.Option(True, "--plugins/--no-plugins", help="同步插件层"),
    layer_hooks: bool = typer.Option(True, "--hooks/--no-hooks", help="同步 hooks 层"),
    layer_agents: bool = typer.Option(True, "--agents/--no-agents", help="同步 subagents 层"),
    layer_memory: bool = typer.Option(True, "--memory/--no-memory", help="同步用户级记忆层（CLAUDE.md / AGENTS.md…）"),
    layer_sessions: bool = typer.Option(True, "--sessions/--no-sessions", help="分发跨工具历史会话查询 skill"),
    apply: bool = typer.Option(False, "--apply", help="实际执行（默认 dry-run）"),
    prefer: str = typer.Option("skip", help="冲突处理：skip（默认跳过）/ library（备份工具侧后以清单覆盖）"),
    source: str = typer.Option("auto", "--from", help="清单为空时的收集源（默认自动选最全的工具）"),
    tool: str = typer.Option(None, "--tool", help="只分发到该工具（desktop 矩阵单元格点击）"),
    item: list[str] = typer.Option([], "--item", help="只分发指定条目，可多选（desktop 矩阵单元格点击）"),
) -> None:
    """同步一下：清单/库 -> 所有工具。清单为空会自动从最全的工具收集；--tool/--item 可收窄到单个矩阵单元格；默认 dry-run。"""
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
    from .memory import (
        pick_adopt_source,
        plan_adopt_memory,
        plan_sync_memory,
        resolve_memory_file,
        run_adopt_memory,
        run_sync_memory,
    )
    from .plugin_sync import auto_plugin_source, load_plugin_manifest, sync_plugins
    from .scan import scan_all
    from .sessions import install_session_skill
    from .skills import plan_adopt, plan_sync, resolve_library, run_adopt, run_sync

    if machine is not None:
        # 透传给远端 halter 自己执行：--tool 校验与方言写入都发生在远端，语义天然正确
        fwd = ["sync",
               "--skills" if layer_skills else "--no-skills",
               "--mcp" if layer_mcp else "--no-mcp",
               "--plugins" if layer_plugins else "--no-plugins",
               "--hooks" if layer_hooks else "--no-hooks",
               "--agents" if layer_agents else "--no-agents",
               "--memory" if layer_memory else "--no-memory",
               "--sessions" if layer_sessions else "--no-sessions"]
        if apply:
            fwd.append("--apply")
        if prefer != "skip":
            fwd += ["--prefer", prefer]
        if source != "auto":
            fwd += ["--from", source]
        if tool is not None:
            fwd += ["--tool", tool]
        fwd += [x for i in item if i for x in ("--item", i)]
        _run_on_machine(machine, fwd, timeout=600)
        return

    if prefer not in ("skip", "library"):
        console.print("[red]--prefer 仅支持 skip / library[/red]")
        raise typer.Exit(2)

    detections = detect_tools()
    installed = [d.tool for d in detections if d.installed]
    reports = scan_all(detections)
    cfg = load_config()

    # --- 单元格范围（--tool/--item）：desktop 矩阵点一下圆点时的同步语义 ---
    if tool is not None and tool not in installed:
        console.print(f"[red]--tool {tool} 未安装或未知（本机可用：{', '.join(installed) or '无'}）[/red]")
        raise typer.Exit(2)
    cell_items = [i for i in item if i]

    def wanted(name: str) -> bool:
        """--item 过滤：未指定 = 全部条目。"""
        return not cell_items or name in cell_items

    def hit_tool(name: str) -> bool:
        """--tool 过滤：未指定 = 全部工具。"""
        return tool is None or name == tool

    # --- sessions 层：只分发查询 skill；绝不迁移 / 写入任何工具原生 session 文件 ---
    if layer_sessions:
        console.print("\nsession continuity: 项目级历史会话查询 skill")
        for line in install_session_skill(cfg, [tool] if tool else installed, apply):
            console.print(f"  {'[apply]' if apply else '[plan]'} {line}"
                          if not line.startswith("conflict ") else f"  [yellow]{line}[/yellow]")

    # --- memory 层：库缺失先收养（--from 裁决分歧），然后 symlink 分发 ---
    if layer_memory:
        memory_lib = resolve_memory_file(cfg, create=apply)
        if not memory_lib.exists():
            plan = plan_adopt_memory(reports)
            pick = pick_adopt_source(plan, None if source == "auto" else source)
            if pick is not None:
                adopt_tool, adopt_src = pick
                console.print(f"\nmemory: 事实源为空，从 {adopt_tool} 收养")
                for line in run_adopt_memory(adopt_src, memory_lib, apply):
                    console.print(f"  {'[apply]' if apply else '[dry-run]'} {line}")
            elif plan.sources:
                if source != "auto":
                    console.print(f"[yellow]--from {source} 侧没有可收养的记忆文件[/yellow]")
                console.print("  [yellow]各工具记忆内容不一致，需 --from <tool> 指定收养源：[/yellow]")
                for src_tool, path in plan.sources:
                    console.print(f"    {src_tool}: {path}")
        if memory_lib.exists():
            memory_actions = [a for a in plan_sync_memory(memory_lib, reports) if hit_tool(a.tool)]
            memory_counts: dict[str, int] = {}
            for a in memory_actions:
                memory_counts[a.kind] = memory_counts.get(a.kind, 0) + 1
            console.print(f"\nmemory 事实源: {memory_lib}")
            console.print("计划: " + (", ".join(f"{k}×{v}" for k, v in sorted(memory_counts.items()))
                                 or "（无动作）"))
            if not apply:
                console.print("[dim]dry-run 模式（--apply 生效）[/dim]")
            for line in run_sync_memory(memory_actions, memory_lib, apply, prefer):
                style = {"link": "green", "relink": "yellow", "replace": "yellow",
                         "conflict": "red"}.get(line.split()[0], None)
                console.print(f"  {'[apply]' if apply else '[plan]'} {line}", style=style)
        elif not plan.sources:
            console.print("\nmemory: 无事实源也无现存工具侧记忆（先在任一工具建立后再 sync）")

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
        specs = [s for s in specs if wanted(s.name)]
        if specs:
            console.print(f"MCP 清单: {len(specs)} 个 server")
            for line in sync_mcp(specs, _load_secrets(), [tool] if tool else installed, apply, prefer):
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
        specs = [s for s in specs if wanted(s.plugin_id)]
        if specs:
            console.print(f"插件清单: {len(specs)} 个")
            for line in sync_plugins(specs, [tool] if tool else installed, apply):
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
        # 矩阵行名是 label（= gen_hook_id 基名，同工具重名带 #N 序号）；按剥离
        # 序号后的基名匹配 manifest id（收集时重名会追加 -2/-3 序号后缀）
        hook_bases = {i.split("#", 1)[0] for i in cell_items}
        if hook_bases:
            specs = [s for s in specs
                     if any(s.id == b or s.id.startswith(b + "-") for b in hook_bases)]
        if specs:
            console.print(f"hooks 清单: {len(specs)} 条")
            for line in sync_hooks(specs, [tool] if tool else installed, apply, prefer):
                console.print(f"  {'[apply]' if apply else '[plan]'} {line}"
                              if not line.startswith(("[red]", "[yellow]")) else f"  {line}")
        elif apply:
            console.print("[yellow]hooks 层跳过（清单仍为空）[/yellow]")

    # --- subagents 层：库外独有自动收集，然后逐条 symlink 分发 ---
    if layer_agents:
        agents_lib = resolve_agents_library(cfg, create=apply)
        agent_plan = plan_agent_adopt(agents_lib, reports)
        agent_plan.to_adopt = [(t, p) for t, p in agent_plan.to_adopt if wanted(p.stem)]
        agent_plan.conflicts = [c for c in agent_plan.conflicts if wanted(c[1])]
        if agent_plan.to_adopt:
            console.print(f"[blue]库外独有 subagents {len(agent_plan.to_adopt)} 个，自动收集[/blue]")
            for line in run_agent_adopt(agent_plan, agents_lib, apply):
                console.print(f"  {'[apply]' if apply else '[dry-run]'} {line}")
        for src_tool, name, path, other in agent_plan.conflicts:
            console.print(f"  [yellow]同名冲突[/yellow] {name} @ {src_tool}（多工具内容不一致，需人工裁决）")

        agent_actions = [a for a in plan_agent_sync(agents_lib, reports, cfg.exclude_agents)
                         if hit_tool(a.tool) and wanted(a.agent)]
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
        adopt_plan.to_adopt = [(t, p) for t, p in adopt_plan.to_adopt if wanted(p.name)]
        adopt_plan.conflicts = [c for c in adopt_plan.conflicts if wanted(c[1])]
        if adopt_plan.to_adopt:
            console.print(f"[blue]库外独有 skills {len(adopt_plan.to_adopt)} 个，自动收集[/blue]")
            for line in run_adopt(adopt_plan, library, apply):
                console.print(f"  {'[apply]' if apply else '[dry-run]'} {line}")
        for src_tool, name, path, other in adopt_plan.conflicts:
            console.print(f"  [yellow]同名冲突[/yellow] {name} @ {src_tool}（多工具内容不一致，需人工裁决）")

        actions = [a for a in plan_sync(library, reports, cfg.exclude_skills)
                   if hit_tool(a.tool) and wanted(a.skill)]
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


# ---------------------------------------------------------------------------
# memory：事实源内容的查看与写入（desktop 记忆面板 + 脚本两用）


@memory_app.command("show")
def memory_show(
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """查看记忆事实源与各工具侧的内容、状态与差异。"""
    from .config import load_config
    from .memory import memory_snapshot

    if machine is not None:
        _run_on_machine(machine, ["memory", "show"] + (["--json"] if json_out else []))
        return
    snap = memory_snapshot(load_config())
    if json_out:
        emit_json(_json.dumps(snap, ensure_ascii=False))
        return
    lib = snap["library"]
    state = "" if lib["exists"] else " [yellow](不存在)[/yellow]"
    console.print(f"memory 事实源: {lib['path']}{state}")
    for t in snap["tools"]:
        if t["linked"]:
            state = "[green]● 已链接[/green]"
        elif t["present"]:
            state = "[yellow]◐ 本地文件[/yellow]"
            if t["diff"]:
                state += " [red](与库分歧)[/red]"
        else:
            state = "[dim]· 缺失[/dim]"
        console.print(f"  {t['display']:<18} {state}  {t['path']}")


@memory_app.command("write")
def memory_write(
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
    content: str | None = typer.Option(None, "--content", help="新内容；缺省从 stdin 读取"),
) -> None:
    """把新内容写入记忆事实源（旧文件先备份到 backups/<时间戳>/memory/）。"""
    import shutil
    import sys

    from .config import load_config
    from .io_utils import atomic_write_text
    from .memory import backup_dir, resolve_memory_file

    text = content if content is not None else sys.stdin.read()
    lib = resolve_memory_file(load_config())
    backup: str | None = None
    if lib.is_file():
        dest = backup_dir() / lib.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(lib), str(dest))
        backup = str(dest)
    lib.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(lib, text)
    if json_out:
        emit_json(_json.dumps(
            {"path": str(lib), "backup": backup, "size": len(text)}, ensure_ascii=False))
    else:
        line = f"已写入 {lib}（{len(text)}B）"
        if backup:
            line += f"，旧文件备份于 {backup}"
        console.print(line)


# ---------------------------------------------------------------------------
# push / pull：单条目跨机器同步（A 机器的某 skill / MCP / subagent / hook -> B 机器）


def _push_core(layer: str, item: str, source: str | None, to: str | None,
               with_secrets: bool, prefer: str, apply: bool) -> None:
    """push 的编排：源侧 export -> 目标侧 ingest。缺省侧为本机。"""
    from . import transfer
    from .machines import get_machine, run_remote

    if source is None and to is None:
        console.print("[red]--from 与 --to 至少指定一个（缺省侧为本机）[/red]")
        raise typer.Exit(2)
    m_src = None if source is None else get_machine(source)
    if source is not None and m_src is None:
        console.print(f"[red]机器 {source} 不存在（halter machines list 查看）[/red]")
        raise typer.Exit(2)
    m_dst = None if to is None else get_machine(to)
    if to is not None and m_dst is None:
        console.print(f"[red]机器 {to} 不存在（halter machines list 查看）[/red]")
        raise typer.Exit(2)

    console.print(f"push {layer}:{item}  [cyan]{source or '本机'}[/cyan] -> [cyan]{to or '本机'}[/cyan]"
                  + ("（dry-run）" if not apply else ""))

    # 源侧导出
    if m_src is None:
        try:
            payload, warnings = transfer.encode_entry(layer, item, with_secrets)
        except (LookupError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        for w in warnings:
            console.print(f"[yellow]警告[/yellow] {w}")
    else:
        args = ["machines", "export", layer, item]
        if with_secrets:
            args.append("--with-secrets")
        rc, payload, err = run_remote(m_src, args, timeout=120)
        if rc is None or rc != 0:
            console.print(f"[red]远端导出失败：{(err or payload.decode('utf-8', errors='replace')).strip()}[/red]")
            raise typer.Exit(1)
        if err.strip():
            console.print(f"[yellow]警告[/yellow] {err.strip()}")

    # 目标侧落盘
    if m_dst is None:
        try:
            lines = transfer.apply_entry(layer, item, payload, prefer, with_secrets, apply)
            lines += transfer.distribute_item(layer, item, apply)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        for line in lines:
            style = {"add": "green", "ok": "green", "replace": "yellow",
                     "secrets": "yellow", "conflict": "red"}.get(line.split()[0], None)
            console.print(f"  {'[apply]' if apply else '[plan]'} {line}", style=style)
        return

    args = ["machines", "ingest", layer, item, "--prefer", prefer]
    if with_secrets:
        args.append("--with-secrets")
    if apply:
        args.append("--apply")
    rc, out, err = run_remote(m_dst, args, input_bytes=payload, timeout=300)
    if rc is None or rc != 0:
        console.print(f"[red]远端写入失败：{(err or out.decode('utf-8', errors='replace')).strip()}[/red]")
        raise typer.Exit(1)
    console.print(out.decode("utf-8", errors="replace").rstrip())


@app.command()
def push(
    layer: str = typer.Argument(help="层：skills / agents / mcp / hooks"),
    item: str = typer.Argument(help="条目名（skill 目录名 / agent 名 / MCP server 名 / hook id 基名）"),
    source: str = typer.Option(None, "--from", help="来源机器，缺省 = 本机"),
    to: str = typer.Option(None, "--to", help="目标机器，缺省 = 本机"),
    with_secrets: bool = typer.Option(False, "--with-secrets",
                                      help="连同 MCP 密钥真实值（ssh 加密通道；默认不同步）"),
    prefer: str = typer.Option("skip", help="目标侧同名冲突：skip（默认）/ replace（备份后覆盖）"),
    apply: bool = typer.Option(False, "--apply", help="实际执行（默认 dry-run）"),
) -> None:
    """把一个条目（skill / MCP server / subagent / hook）从 A 机器同步到 B 机器。"""
    from . import transfer

    if layer not in transfer.LAYERS:
        console.print(f"[red]未知层 {layer}（可选 {', '.join(transfer.LAYERS)}）[/red]")
        raise typer.Exit(2)
    if prefer not in ("skip", "replace"):
        console.print("[red]--prefer 仅支持 skip / replace[/red]")
        raise typer.Exit(2)
    _push_core(layer, item, source, to, with_secrets, prefer, apply)


@app.command()
def pull(
    layer: str = typer.Argument(help="层：skills / agents / mcp / hooks"),
    item: str = typer.Argument(help="条目名"),
    source: str = typer.Option(..., "--from", help="来源机器"),
    with_secrets: bool = typer.Option(False, "--with-secrets", help="连同 MCP 密钥真实值"),
    prefer: str = typer.Option("skip", help="本机同名冲突：skip（默认）/ replace（备份后覆盖）"),
    apply: bool = typer.Option(False, "--apply", help="实际执行（默认 dry-run）"),
) -> None:
    """把远程机器的一个条目拉到本机（= push --from <机器>，目标为本机）。"""
    from . import transfer

    if layer not in transfer.LAYERS:
        console.print(f"[red]未知层 {layer}（可选 {', '.join(transfer.LAYERS)}）[/red]")
        raise typer.Exit(2)
    if prefer not in ("skip", "replace"):
        console.print("[red]--prefer 仅支持 skip / replace[/red]")
        raise typer.Exit(2)
    _push_core(layer, item, source, None, with_secrets, prefer, apply)


# ---------------------------------------------------------------------------
# machines：跨机器同步的机器注册表（ssh 通道）


@machines_app.command("list")
def machines_list(json_out: bool = typer.Option(False, "--json", help="以 JSON 输出")) -> None:
    """列出可用机器：machines.toml 手动注册 + ~/.ssh/config 自动发现。"""
    from .machines import list_machines

    machines = list_machines()
    if json_out:
        from .machines import load_info_cache, local_machine_info

        info = load_info_cache()
        emit_json(_json.dumps({
            "count": len(machines),
            "local": local_machine_info(),
            "machines": [{**m.__dict__, "local": m.is_local,
                          "host_name": info.get(m.name, {}).get("host_name"),
                          "os": info.get(m.name, {}).get("os")} for m in machines],
        }, ensure_ascii=False))
        return
    if not machines:
        console.print("[dim]无可用机器 — 在 ~/.ssh/config 配置 Host 别名（自动识别），"
                      "或 halter machines add <name> --host <host> 手动注册[/dim]")
        return
    n_ssh = sum(1 for m in machines if m.source == "ssh")
    table = Table(title=f"Machines — {len(machines)} 台（手动 {len(machines) - n_ssh} · ssh config {n_ssh}）")
    table.add_column("名称", style="cyan", no_wrap=True)
    table.add_column("SSH 目标")
    table.add_column("halter", style="dim")
    table.add_column("来源", style="dim")
    for m in machines:
        origin = "~/.ssh/config" if m.source == "ssh" else "machines.toml"
        name = f"{m.name}（即本机）" if m.is_local else m.name
        table.add_row(name, m.destination, m.halter_path, origin)
    console.print(table)
    console.print("[dim]连通性检查：halter machines test <name>；单条目跨机同步：halter push <layer> <item> --to <name>[/dim]")


@machines_app.command()
def add(
    name: str = typer.Argument(help="机器别名（本机内唯一，如 desktop）"),
    host: str = typer.Option(..., "--host", help="ssh 主机名或 IP"),
    user: str = typer.Option(None, "--user", help="ssh 用户名，缺省用当前用户"),
    port: int = typer.Option(22, "--port", help="ssh 端口"),
    halter_path: str = typer.Option("halter", "--halter-path",
                                    help="远端 halter 可执行文件（PATH 名或绝对路径；非交互 ssh 常缺 ~/.local/bin）"),
) -> None:
    """注册一台远程机器（写入 ~/.config/halter/machines.toml）。"""
    import re

    from .machines import (MachineSpec, get_machine, list_machines,
                           load_machines, save_machines)

    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        console.print("[red]机器名只能含字母、数字、点、下划线、连字符[/red]")
        raise typer.Exit(2)
    if get_machine(name) is not None:
        existing = get_machine(name)
        if existing is not None and existing.source == "ssh":
            console.print(f"[yellow]{name} 已由 ~/.ssh/config 自动识别（user/port/跳板均走 ssh 配置）[/yellow]")
            console.print(f"[yellow]手动注册将优先生效，适合指定自定义 halter 路径；继续[/yellow]")
        else:
            console.print(f"[red]机器 {name} 已存在（halter machines remove {name} 后再添加）[/red]")
            raise typer.Exit(2)
    machines = [m for m in load_machines() if m.name != name]  # ssh 同名覆盖时去掉旧 manual 条目
    machines.append(MachineSpec(name=name, host=host, user=user, port=port, halter_path=halter_path))
    save_machines(machines)
    console.print(f"已注册 [cyan]{name}[/cyan] -> {user or ''}{'@' if user else ''}{host}:{port}（halter: {halter_path}）")
    console.print(f"[dim]下一步：halter machines test {name} 验证连通[/dim]")


@machines_app.command()
def remove(name: str = typer.Argument(help="机器别名")) -> None:
    """移除一台手动注册的远程机器（不动两台机器上的任何配置）。"""
    from .machines import get_machine, load_machines, save_machines

    existing = get_machine(name)
    if existing is None:
        console.print(f"[red]机器 {name} 不存在（halter machines list 查看）[/red]")
        raise typer.Exit(2)
    if existing.source == "ssh":
        console.print(f"[red]{name} 来自 ~/.ssh/config，编辑该文件即可（machines.toml 管不到它）[/red]")
        raise typer.Exit(2)
    save_machines([m for m in load_machines() if m.name != name])
    console.print(f"已移除 [cyan]{name}[/cyan]")


@machines_app.command()
def test(
    name: str = typer.Argument(help="机器别名"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """检查与远程机器的连通性及远端 halter 可用性；成功时缓存远端主机名。"""
    import json as _j

    from .machines import get_machine, probe_remote_halter, probe_remote_info, save_info_cache

    machine = get_machine(name)
    if machine is None:
        console.print(f"[red]机器 {name} 不存在（halter machines list 查看）[/red]")
        raise typer.Exit(2)
    if not json_out:
        console.print(f"连接 [cyan]{machine.destination}:{machine.port}[/cyan]（halter: {machine.halter_path}）…")
    ok, detail = probe_remote_halter(machine)
    host_name = os_name = None
    if ok:
        try:
            version = _j.loads(detail).get("version", "?")
        except _j.JSONDecodeError:
            version = "?"
        host_info = probe_remote_info(machine)
        if host_info:
            host_name, os_name = host_info["host_name"], host_info["os"]
            save_info_cache(name, host_info)
        if json_out:
            emit_json(_json.dumps({"ok": True, "version": version,
                                   "host_name": host_name, "os": os_name}, ensure_ascii=False))
            return
        suffix = f" · {host_name}（{os_name}）" if host_name else ""
        console.print(f"[green]● 通[/green] 远端 halter {version}{suffix}")
        console.print(f"[dim]跨机同步示例：halter push skills <item> --to {name}[/dim]")
        return
    if json_out:
        emit_json(_json.dumps({"ok": False, "detail": detail}, ensure_ascii=False))
    else:
        console.print(f"[red]✕ 不通[/red] {detail}")
        console.print("[dim]检查项：ssh 免密（公钥/ssh-agent）、--halter-path 绝对路径、远端 halter 已安装[/dim]")
    raise typer.Exit(1)


@machines_app.command()
def export(
    layer: str = typer.Argument(help="层：skills / agents / mcp / hooks"),
    name: str = typer.Argument(help="条目名（skill 目录名 / agent 名 / MCP server 名 / hook id 基名）"),
    with_secrets: bool = typer.Option(False, "--with-secrets",
                                       help="连同该条目引用的密钥真实值（仅 MCP；走 ssh 加密通道）"),
) -> None:
    """[内部] 把本机一个条目以传输格式写到 stdout（push/pull 调用，数据走 stdout、警告走 stderr）。"""
    import sys

    from . import transfer

    try:
        payload, warnings = transfer.encode_entry(layer, name, with_secrets)
    except (LookupError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    for w in warnings:
        print(f"警告 {w}", file=sys.stderr)
    sys.stdout.buffer.write(payload)


@machines_app.command()
def ingest(
    layer: str = typer.Argument(help="层：skills / agents / mcp / hooks"),
    name: str = typer.Argument(help="条目名"),
    prefer: str = typer.Option("skip", help="冲突处理：skip（默认）/ replace（备份目标侧后覆盖）"),
    with_secrets: bool = typer.Option(False, "--with-secrets",
                                      help="合并传输流中的 [secrets] 到本机 secrets.toml（0600）"),
    apply: bool = typer.Option(False, "--apply", help="实际执行并分发（默认 dry-run）"),
) -> None:
    """[内部] 从 stdin 读传输格式落盘到本机库/清单，--apply 时随后分发该条目到所有工具。"""
    import sys

    from . import transfer

    if prefer not in ("skip", "replace"):
        console.print("[red]--prefer 仅支持 skip / replace[/red]")
        raise typer.Exit(2)
    payload = sys.stdin.buffer.read()
    try:
        lines = transfer.apply_entry(layer, name, payload, prefer, with_secrets, apply)
        lines += transfer.distribute_item(layer, name, apply)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    for line in lines:
        style = {"add": "green", "ok": "green", "replace": "yellow",
                 "secrets": "yellow", "conflict": "red"}.get(line.split()[0], None)
        console.print(f"  {'[apply]' if apply else '[plan]'} {line}", style=style)


# ---------------------------------------------------------------------------
# providers：模型供应商清单与一键切换（claude / codex；本机独立，不参与多机同步）


def _provider_row(spec) -> dict:
    from .providers_write import detect_current

    row = {"id": spec.id, "label": spec.label or spec.id, "tools": spec.tools(), "builtin": False}
    if spec.claude is not None:
        # env 全是档位映射等非密钥托管键，随行下发供编辑表单预填
        row["claude"] = {"base_url": spec.claude.base_url, "model": spec.claude.model or "",
                         "env": dict(spec.claude.env)}
    if spec.codex is not None:
        row["codex"] = {"base_url": spec.codex.base_url, "model": spec.codex.model or "",
                        "wire_api": spec.codex.wire_api,
                        "reasoning_effort": spec.codex.reasoning_effort or "",
                        "context_window": spec.codex.context_window}
    return row


@providers_app.command("list")
def providers_list(
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    tool: str = typer.Option(None, "--tool", help="过滤工具：claude / codex"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """列出供应商清单与各工具当前激活状态（激活状态按目标文件实测推断）。"""
    from .providers_manifest import OFFICIAL_ID, PROVIDER_CAPABLE, load_manifest
    from .providers_write import detect_current

    if machine is not None:
        fwd = ["providers", "list"]
        if tool is not None:
            fwd += ["--tool", tool]
        fwd += ["--json"] if json_out else []
        _run_on_machine(machine, fwd)
        return
    if tool is not None and tool not in PROVIDER_CAPABLE:
        console.print(f"[red]--tool 仅支持：{' / '.join(PROVIDER_CAPABLE)}[/red]")
        raise typer.Exit(2)
    tools = [tool] if tool else list(PROVIDER_CAPABLE)
    specs = load_manifest()
    rows = [_provider_row(s) for s in specs]
    rows.append({"id": OFFICIAL_ID, "label": OFFICIAL_ID, "tools": list(PROVIDER_CAPABLE),
                 "builtin": True})
    current = {t: detect_current(t, specs) for t in tools}

    if json_out:
        from .providers_manifest import PRESETS

        emit_json(_json.dumps(
            {"count": len(rows), "providers": rows, "current": current,
             "presets": [{"name": n, "label": t.get("label", ""),
                          "tools": [k for k in t if k in ("claude", "codex")],
                          "urls": {k: t[k]["base_url"] for k in t
                                   if k in ("claude", "codex")}}
                         for n, t in PRESETS.items()]},
            ensure_ascii=False))
        return
    table = Table(title=f"Providers — {len(specs)} 个自定义 + official（内置）")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Tools", no_wrap=True)
    table.add_column("Claude 端点", overflow="ellipsis")
    table.add_column("Codex 端点", overflow="ellipsis")
    for r in rows:
        c_url = r.get("claude", {}).get("base_url", "-") if r.get("claude") else "-"
        x_url = r.get("codex", {}).get("base_url", "-") if r.get("codex") else "-"
        mark = "（内置）" if r["builtin"] else ""
        table.add_row(r["id"] + mark, ",".join(r["tools"]) or "-", c_url, x_url)
    console.print(table)
    for t in tools:
        cur = current[t]
        style = {"halter": "green", "external": "yellow", "official": "dim"}[cur["status"]]
        console.print(f"[{style}]▸ {t}: {cur['provider'] or 'external'}"
                      f"（{cur['status']}） {cur['base_url'] or '官方默认'}"
                      f"{(' — ' + cur['detail']) if cur['detail'] else ''}[/{style}]")
    console.print("[dim]切换：halter providers switch <id> --tool claude|codex；"
                  "收编现有配置：halter providers adopt --apply[/dim]")


@providers_app.command()
def show(
    pid: str = typer.Argument(help="provider id"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """查看单个供应商的完整定义（token 只显示有无，不显示值）。"""
    from .providers_manifest import get_token, load_manifest

    spec = next((s for s in load_manifest() if s.id == pid), None)
    if spec is None:
        console.print(f"[red]未找到 provider '{pid}'[/red]")
        raise typer.Exit(2)
    detail = _provider_row(spec)
    detail["tokens"] = {t: get_token(pid, t) is not None for t in spec.tools()}
    if json_out:
        emit_json(_json.dumps(detail, ensure_ascii=False))
        return
    console.print(Panel.fit(
        _json.dumps(detail, ensure_ascii=False, indent=2), title=f"provider {pid}"))


def _inherit_live_token(base_url: str) -> str | None:
    """收编安全垫：实况端点与目标一致时继承实况 token。

    未显式给 token 的收编若不带继承，switch（删托管键后按清单回写）会清掉
    实况的 ANTHROPIC_AUTH_TOKEN，Claude Code 直接失去认证。
    """
    from .providers_write import detect_current, live_claude_token

    cur = detect_current("claude")
    if cur["base_url"] and cur["base_url"].rstrip("/") == base_url.rstrip("/"):
        return live_claude_token()
    return None


def _load_block_def(def_json: str, tool: str, preset: str | None = None):
    """--def 配置 JSON（对标 CC Switch 的「配置JSON」）→ 工具块。

    @file 从文件读；preset 作底模板（端点等键可被 --def 覆盖）。
    token 刻意不在此 JSON 中——密钥与配置分离，token 走 --token-stdin/--token-env
    （桌面端粘贴含 token 的整块 JSON 由 UI 剥离后走 stdin）。
    """
    import json as _json
    from pathlib import Path

    from .providers_manifest import PRESETS, parse_block_def

    raw = Path(def_json[1:]).read_text(encoding="utf-8") if def_json.startswith("@") else def_json
    try:
        override = _json.loads(raw)
    except _json.JSONDecodeError as e:
        console.print(f"[red]--def JSON 语法错误：{e.msg}（第 {e.lineno} 行第 {e.colno} 列）[/red]")
        raise typer.Exit(2)
    base: dict = {}
    if preset is not None:
        tpl = PRESETS.get(preset)
        if tpl is None:
            console.print(f"[red]未知预设 {preset}（可选：{' / '.join(PRESETS)}）[/red]")
            raise typer.Exit(2)
        if tool not in tpl:
            console.print(f"[red]预设 {preset} 不含 {tool} 块（可选工具："
                          f"{' / '.join(k for k in tpl if k in ('claude', 'codex'))}）[/red]")
            raise typer.Exit(2)
        base = ({"ANTHROPIC_BASE_URL": tpl[tool]["base_url"]} if tool == "claude"
                else {"base_url": tpl[tool]["base_url"]})
        if tool == "codex" and tpl[tool].get("wire_api"):
            base["wire_api"] = tpl[tool]["wire_api"]
    merged = {**base, **override} if isinstance(override, dict) else override
    try:
        return parse_block_def(tool, merged)
    except ValueError as e:
        console.print(f"[red]--def {e}[/red]")
        raise typer.Exit(2)


@providers_app.command()
def add(
    pid: str = typer.Argument(help="provider id（字母数字._-；再次 add 同 id 可补充另一工具块）"),
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    tool: str = typer.Option(..., "--tool", help="claude / codex"),
    def_json: str = typer.Option(..., "--def", help="工具块配置 JSON，对标 CC Switch「配置JSON」（@file 从文件读）"),
    preset: str = typer.Option(None, "--preset", help="内置预设作底模板，其端点等键可被 --def 覆盖（halter providers presets 查看）"),
    label: str = typer.Option("", "--label", help="显示名，缺省 = 预设名或 id"),
    token_stdin: bool = typer.Option(False, "--token-stdin", help="从 stdin 读 token（不进 shell 历史）"),
    token_env: str = typer.Option(None, "--token-env", help="从该环境变量读 token"),
) -> None:
    """新增（或补充）供应商定义；token 只入 secrets.toml（0600），不进清单。"""
    import re
    import sys

    from .providers_manifest import (PRESETS, ProviderSpec, load_manifest,
                                     save_manifest, set_token,
                                     token_secret_key)

    if machine is not None:
        # 校验与解析都发生在远端；token 先读本地 stdin，再经 ssh stdin 转发，
        # 全程不进本机 argv（process list）
        token_bytes = sys.stdin.buffer.read() if token_stdin else None
        fwd = ["providers", "add", pid, "--tool", tool, "--def", def_json]
        if preset is not None:
            fwd += ["--preset", preset]
        if label:
            fwd += ["--label", label]
        if token_stdin:
            fwd.append("--token-stdin")
        if token_env is not None:
            fwd += ["--token-env", token_env]
        _run_on_machine(machine, fwd, input_bytes=token_bytes)
        return

    if not re.fullmatch(r"[A-Za-z0-9._-]+", pid) or pid == "official":
        console.print("[red]id 只能含字母数字._- 且不能是 official[/red]")
        raise typer.Exit(2)
    if tool not in ("claude", "codex"):
        console.print("[red]--tool 仅支持：claude / codex[/red]")
        raise typer.Exit(2)

    block = _load_block_def(def_json, tool, preset)
    if preset is not None and not label:
        label = PRESETS[preset].get("label", "")

    token = None
    if token_stdin:
        token = sys.stdin.read().strip() or None
    elif token_env:
        import os

        token = os.environ.get(token_env) or None
    if token_stdin and token_env:
        console.print("[red]--token-stdin 与 --token-env 只能选一个[/red]")
        raise typer.Exit(2)
    inherited = False
    if not token and tool == "claude":
        token = _inherit_live_token(block.base_url)
        inherited = token is not None

    specs = load_manifest()
    spec = next((s for s in specs if s.id == pid), None)
    if spec is None:
        spec = ProviderSpec(id=pid, label=label or pid)
        specs.append(spec)
        action = f"新增 provider {pid}"
    else:
        if tool in spec.tools():
            console.print(f"[red]{pid} 已声明 {tool} 块（手改 providers.toml 或先 remove）[/red]")
            raise typer.Exit(2)
        if label:
            spec.label = label
        action = f"补充 {pid} 的 {tool} 块"
    if tool == "claude":
        spec.claude = block
    else:
        spec.codex = block
    save_manifest(specs)
    if token:
        set_token(pid, tool, token)
    secret_note = f"，token → secrets.toml [{token_secret_key(pid, tool)}]" if token else ""
    if inherited:
        secret_note += "（继承自实况）"
    console.print(f"[green]{action}{secret_note}[/green]")
    console.print(f"[dim]激活：halter providers switch {pid} --tool {tool}[/dim]")


@providers_app.command()
def edit(
    pid: str = typer.Argument(help="provider id（须已存在于清单）"),
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    tool: str = typer.Option(..., "--tool", help="claude / codex"),
    def_json: str = typer.Option(..., "--def", help="工具块配置 JSON，整体替换该块（@file 从文件读）"),
    label: str = typer.Option("", "--label", help="显示名；缺省保持不变"),
    token_stdin: bool = typer.Option(False, "--token-stdin", help="从 stdin 读 token（不进 shell 历史）"),
    token_env: str = typer.Option(None, "--token-env", help="从该环境变量读 token"),
) -> None:
    """就地编辑供应商的某工具块（配置 JSON 整体替换）；该块恰为当前激活时同步重写实况配置。"""
    import sys

    from .providers_manifest import (load_manifest, save_manifest,
                                     set_token, token_secret_key)
    from .providers_write import detect_current, switch_provider

    if machine is not None:
        # token 经 ssh stdin 转发，全程不进本机 argv（与 add 同策略）
        token_bytes = sys.stdin.buffer.read() if token_stdin else None
        fwd = ["providers", "edit", pid, "--tool", tool, "--def", def_json]
        if label:
            fwd += ["--label", label]
        if token_stdin:
            fwd.append("--token-stdin")
        if token_env is not None:
            fwd += ["--token-env", token_env]
        _run_on_machine(machine, fwd, input_bytes=token_bytes)
        return

    if tool not in ("claude", "codex"):
        console.print("[red]--tool 仅支持：claude / codex[/red]")
        raise typer.Exit(2)
    specs = load_manifest()
    spec = next((s for s in specs if s.id == pid), None)
    if spec is None:
        console.print(f"[red]未找到 provider '{pid}'（halter providers list 查看）[/red]")
        raise typer.Exit(2)

    block = _load_block_def(def_json, tool)

    token = None
    if token_stdin:
        token = sys.stdin.read().strip() or None
    elif token_env:
        import os

        token = os.environ.get(token_env) or None
    inherited = False
    if not token and tool == "claude":
        token = _inherit_live_token(block.base_url)
        inherited = token is not None

    # 编辑前实况：恰为当前激活供应商时，保存后按新定义重写该工具配置，避免清单与实况漂移
    cur_before = detect_current(tool, specs)
    was_active = cur_before["status"] == "halter" and cur_before["provider"] == pid
    action = f"更新 {pid} 的 {tool} 块" if tool in spec.tools() else f"补充 {pid} 的 {tool} 块"
    if label:
        spec.label = label
    if tool == "claude":
        spec.claude = block
    else:
        spec.codex = block
    save_manifest(specs)
    if token:
        set_token(pid, tool, token)
    secret_note = f"，token → secrets.toml [{token_secret_key(pid, tool)}]" if token else ""
    if inherited:
        secret_note += "（继承自实况）"
    console.print(f"[green]{action}{secret_note}[/green]")
    if was_active:
        for line in switch_provider(pid, tool):
            console.print(line)
        console.print("[dim]已按新定义重写激活配置[/dim]")


@providers_app.command("rename")
def providers_rename(
    old: str = typer.Argument(help="现有 provider id"),
    new: str = typer.Argument(help="新 id（字母数字._-，不可与现有冲突）"),
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
) -> None:
    """重命名供应商 id（token 键随迁；实况按实测推断，不受改名影响）。"""
    import re as _re

    from .providers_manifest import (OFFICIAL_ID, load_manifest, load_tokens,
                                     save_manifest, save_tokens, token_secret_key)

    if machine is not None:
        _run_on_machine(machine, ["providers", "rename", old, new])
        return
    if not _re.fullmatch(r"[A-Za-z0-9._-]+", new) or new == OFFICIAL_ID:
        console.print(f"[red]新 id 只能含字母、数字、点、下划线、连字符，且不可为 {OFFICIAL_ID}[/red]")
        raise typer.Exit(2)
    specs = load_manifest()
    spec = next((s for s in specs if s.id == old), None)
    if spec is None:
        console.print(f"[red]未找到 provider '{old}'（halter providers list 查看）[/red]")
        raise typer.Exit(2)
    if new == old:
        return
    if any(s.id == new for s in specs):
        console.print(f"[red]provider id '{new}' 已存在[/red]")
        raise typer.Exit(2)
    spec.id = new
    if not spec.label or spec.label == old:
        spec.label = new  # 收编默认 label=id 随之同步；自定义显示名保留
    save_manifest(specs)
    # token 键迁移：旧键有值才写新键，旧键始终清除
    tokens = load_tokens()
    moved = []
    for tool in spec.tools():
        old_key = token_secret_key(old, tool)
        if tokens.get(old_key):
            tokens[token_secret_key(new, tool)] = tokens[old_key]
            moved.append(tool)
        tokens.pop(old_key, None)
    if moved:
        save_tokens(tokens)
    note = f"，token 随迁（{', '.join(moved)}）" if moved else ""
    console.print(f"[green]{old} → [cyan]{new}[/cyan]{note}[/green]")


@providers_app.command("presets")
def providers_presets(json_out: bool = typer.Option(False, "--json", help="以 JSON 输出")) -> None:
    """列出内置供应商预设（只固化端点，模型名由 --model 自填）。"""
    from .providers_manifest import PRESETS

    if json_out:
        emit_json(_json.dumps({"count": len(PRESETS), "presets": [
            {"name": name, "label": tpl.get("label", ""),
             "tools": [k for k in tpl if k in ("claude", "codex")],
             "claude": tpl.get("claude"), "codex": tpl.get("codex"),
             "note": tpl.get("note", "")}
            for name, tpl in PRESETS.items()]}, ensure_ascii=False))
        return
    table = Table(title=f"Provider presets — {len(PRESETS)} 个内置")
    table.add_column("预设", style="cyan", no_wrap=True)
    table.add_column("Tools", no_wrap=True)
    table.add_column("端点", overflow="ellipsis")
    table.add_column("说明", style="dim", overflow="ellipsis")
    for name, tpl in PRESETS.items():
        tools = [k for k in tpl if k in ("claude", "codex")]
        urls = "  ".join(f"{k}:{tpl[k]['base_url']}" for k in tools)
        table.add_row(name, ",".join(tools), urls, tpl.get("note", ""))
    console.print(table)
    console.print("[dim]使用：halter providers add <id> --tool <t> --preset <name> "
                  "--def '{\"model\": \"…\"}' --token-stdin（预设端点为底，--def 键覆盖）[/dim]")


@providers_app.command()
def adopt(
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    name: str = typer.Option(None, "--name", help="覆盖自动命名的 id（恰好收编出一个 provider 时生效）"),
    apply: bool = typer.Option(False, "--apply", help="实际写入（默认 dry-run）"),
) -> None:
    """收编 claude env / codex [model_providers.*] 中的现有供应商为清单条目。"""
    from .providers_manifest import adopt_providers

    if machine is not None:
        fwd = ["providers", "adopt"]
        if name is not None:
            fwd += ["--name", name]
        if apply:
            fwd.append("--apply")
        _run_on_machine(machine, fwd)
        return
    for line in adopt_providers(apply=apply, name=name):
        console.print(line)


@providers_app.command()
def switch(
    pid: str = typer.Argument(help="provider id（official = 切回官方默认端点）"),
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    tool: str = typer.Option(..., "--tool", help="claude / codex"),
) -> None:
    """切换工具的激活供应商（直接写目标配置；token 缺失时只写非密钥键）。"""
    from .providers_write import switch_provider

    if machine is not None:
        _run_on_machine(machine, ["providers", "switch", pid, "--tool", tool])
        return
    for line in switch_provider(pid, tool):
        if "[red]" in line:
            console.print(line)
            raise typer.Exit(1)
        console.print(f"[green]{line}[/green]")


@providers_app.command()
def remove(
    pid: str = typer.Argument(help="provider id"),
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    apply: bool = typer.Option(False, "--apply", help="实际执行（默认 dry-run）"),
) -> None:
    """删除清单条目与对应 token（目标工具的配置文件不动，必要时先 switch）。"""
    from .providers_manifest import load_manifest, load_tokens, save_manifest, save_tokens
    from .providers_write import detect_current

    if machine is not None:
        fwd = ["providers", "remove", pid]
        if apply:
            fwd.append("--apply")
        _run_on_machine(machine, fwd)
        return
    specs = load_manifest()
    spec = next((s for s in specs if s.id == pid), None)
    if spec is None:
        console.print(f"[red]未找到 provider '{pid}'[/red]")
        raise typer.Exit(2)
    active = [t for t in spec.tools() if detect_current(t, specs)["provider"] == pid]
    warn = (f"[yellow]注意：{pid} 仍是 {'/'.join(active)} 的当前激活供应商，"
            f"删除后其配置会残留在目标文件（先 switch official 或其它 provider）[/yellow]\n"
            if active else "")
    if not apply:
        console.print(f"{warn}[dim]dry-run 将删除 providers.toml 条目 {pid}"
                      f"（tools: {','.join(spec.tools())}）及其 secrets token（--apply 生效）[/dim]")
        return
    save_manifest([s for s in specs if s.id != pid])
    tokens = load_tokens()
    for key in [k for k in tokens if k.startswith(f"provider/{pid}/")]:
        del tokens[key]
    if tokens:
        save_tokens(tokens)
    else:
        from .mcp_manifest import SECRETS_FILE

        SECRETS_FILE().unlink(missing_ok=True)
    console.print(f"[green]已删除 {pid}（清单 + token）[/green]\n{warn}", style=None)


# ---------------------------------------------------------------------------
# update：npm 渠道 harness 的版本对比与一键升级（desktop 更新面板）


@update_app.command("status")
def update_status(
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """各 npm 渠道 harness 的当前版本 / registry 最新版本 / 升级状态。"""
    from .updates import collect_status

    if machine is not None:
        fwd = ["update", "status"]
        fwd += ["--json"] if json_out else []
        _run_on_machine(machine, fwd)
        return
    payload = collect_status()
    if json_out:
        emit_json(_json.dumps(payload, ensure_ascii=False))
        return
    state_text = {
        "latest": "[green]已就绪[/green]",
        "upgradeable": "[yellow]可升级[/yellow]",
        "ahead": "[red]高于最新[/red]",
        "missing": "[dim]未安装[/dim]",
        "unknown": "[dim]未知[/dim]",
    }
    table = Table(title=f"Harness updates — {payload['platform']}")
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("npm 包", no_wrap=True)
    table.add_column("当前版本", no_wrap=True)
    table.add_column("最新版本", no_wrap=True)
    table.add_column("状态", no_wrap=True)
    for tool in payload["tools"]:
        table.add_row(tool["display"], tool["npm_package"],
                      tool["current"] or "-", tool["latest"] or "-",
                      state_text[tool["state"]])
    console.print(table)
    if not payload["npm_available"]:
        console.print("[yellow]未找到 npm — 无法获取最新版本，也无法执行升级[/yellow]")
    console.print("[dim]升级：halter update run <tool>（tool 为 status 中的 npm 渠道 harness）[/dim]")


@update_app.command("run")
def update_run(
    tool: str = typer.Argument(help="registry 工具 key（halter update status 查看）"),
    machine: str = typer.Option(None, "--machine", help="在远程机器上执行本命令（halter machines list 查看）"),
) -> None:
    """把指定 harness 升级到 npm registry 最新版（npm install -g <pkg>@latest）。"""
    import subprocess

    from . import updates

    if machine is not None:
        _run_on_machine(machine, ["update", "run", tool])
        return
    if tool not in updates.NPM_PACKAGES:
        console.print(f"[red]不支持升级 {tool}（可选：{' / '.join(updates.NPM_PACKAGES)}）[/red]")
        raise typer.Exit(2)
    console.print(f"[cyan]npm install -g {updates.NPM_PACKAGES[tool]}@latest[/cyan]")
    try:
        proc = updates.apply_update(tool)
    except (OSError, subprocess.SubprocessError) as exc:
        console.print(f"[red]升级 {tool} 失败: {exc}[/red]")
        raise typer.Exit(1)
    if (proc.stdout or "").strip():
        console.print(proc.stdout.strip())
    if (proc.stderr or "").strip():
        console.print(proc.stderr.strip(), style="yellow")
    if proc.returncode != 0:
        raise typer.Exit(proc.returncode if 0 < proc.returncode < 256 else 1)
    console.print(f"[green]{tool} 已升级到最新版[/green]")
