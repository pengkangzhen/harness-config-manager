"""MCP 方言写入器：canonical -> 各工具配置（读-改-写，保留其余键；原子替换）。"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from .io_utils import atomic_write_json, atomic_write_text, load_json_object
from .mcp_manifest import McpSpec
from .registry import expand


def _atomic_write_json(path: Path, data: dict) -> None:
    atomic_write_json(path, data)


def _load_json_dict(path: Path) -> dict:
    # Invalid JSON must abort the read-modify-write; returning {} here would
    # cause callers to reconstruct and replace the user's entire config.
    return load_json_object(path)


def _canonical_to_common(spec: McpSpec) -> dict:
    """canonical -> claude/zcode/cursor 通用条目形态。"""
    entry: dict = {"type": spec.transport}
    if spec.transport in ("http", "sse"):
        entry["url"] = spec.url
        if spec.headers:
            entry["headers"] = spec.headers
    else:
        entry["command"] = spec.command
        if spec.args:
            entry["args"] = spec.args
        if spec.env:
            entry["env"] = spec.env
    for k, v in spec.extra.items():
        entry.setdefault(k, v)
    return entry


def write_claude(specs: list[McpSpec]) -> list[str]:
    """~/.claude.json 顶层 mcpServers 读-改-写（大状态文件，绝不能整文件重建）。"""
    path = expand(".claude.json")
    data = _load_json_dict(path)
    if not data:
        return [f"claude: 跳过（{path} 不存在或不可解析）"]
    data.setdefault("mcpServers", {})
    for s in specs:
        data["mcpServers"][s.name] = _canonical_to_common(s)
    _atomic_write_json(path, data)
    return [f"claude: 写入 {', '.join(s.name for s in specs)}"]


def write_zcode(specs: list[McpSpec]) -> list[str]:
    path = expand(".zcode/cli/config.json")
    data = _load_json_dict(path)
    if not data:
        return [f"zcode: 跳过（{path} 不存在或不可解析）"]
    mcp = data.setdefault("mcp", {})
    servers = mcp.setdefault("servers", {})
    for s in specs:
        servers[s.name] = _canonical_to_common(s)
    _atomic_write_json(path, data)
    return [f"zcode: 写入 {', '.join(s.name for s in specs)}"]


def write_cursor(specs: list[McpSpec]) -> list[str]:
    path = expand(".cursor/mcp.json")
    data = _load_json_dict(path)
    data.setdefault("mcpServers", {})
    for s in specs:
        data["mcpServers"][s.name] = _canonical_to_common(s)
    _atomic_write_json(path, data)
    return [f"cursor: 写入 {', '.join(s.name for s in specs)}"]


def write_vscode(specs: list[McpSpec]) -> list[str]:
    path = expand("Library/Application Support/Code/User/mcp.json")
    data = _load_json_dict(path)
    servers = data.setdefault("servers", {})
    for s in specs:
        entry = _canonical_to_common(s)
        servers[s.name] = entry
    _atomic_write_json(path, data)
    return [f"vscode(user): 写入 {', '.join(s.name for s in specs)}"]


def write_gemini(specs: list[McpSpec]) -> list[str]:
    path = expand(".gemini/settings.json")
    data = _load_json_dict(path)
    data.setdefault("mcpServers", {})
    for s in specs:
        data["mcpServers"][s.name] = _canonical_to_common(s)
    _atomic_write_json(path, data)
    return [f"gemini: 写入 {', '.join(s.name for s in specs)}"]


def write_opencode(specs: list[McpSpec]) -> list[str]:
    path = expand(".config/opencode/opencode.json")
    data = _load_json_dict(path)
    mcp = data.setdefault("mcp", {})
    for s in specs:
        mcp[s.name] = {
            "type": "local",
            "command": [s.command, *s.args],
            **({"env": s.env} if s.env else {}),
            **s.extra,
        }
    _atomic_write_json(path, data)
    return [f"opencode: 写入 {', '.join(s.name for s in specs)}"]


def write_codex(specs: list[McpSpec]) -> list[str]:
    """config.toml 用 tomlkit 读-改-写（保留注释与键序）。"""
    path = expand(".codex/config.toml")
    if not path.exists():
        return [f"codex: 跳过（{path} 不存在）"]
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    for s in specs:
        tbl = tomlkit.table()
        tbl["type"] = "stdio"
        tbl["command"] = s.command
        if s.args:
            tbl["args"] = s.args
        if s.env:
            env = tomlkit.table()
            for k, v in s.env.items():
                env[k] = v
            tbl["env"] = env
        doc.setdefault("mcp_servers", {})[s.name] = tbl
    atomic_write_text(path, tomlkit.dumps(doc))
    return [f"codex: 写入 {', '.join(s.name for s in specs)}"]


# 工具 -> (写入器, 是否支持 http)
MCP_WRITERS: dict[str, callable] = {
    "claude": write_claude,
    "zcode": write_zcode,
    "codex": write_codex,
    "cursor": write_cursor,
    "vscode": write_vscode,
    "gemini": write_gemini,
    "opencode": write_opencode,
}


# ---------------------------------------------------------------------------
# 分发协调


def _current_entries(tool: str) -> dict[str, dict]:
    """工具配置中的原始（未脱敏）server 条目，用于冲突比较。"""
    import tomllib

    if tool == "claude":
        return dict(_load_json_dict(expand(".claude.json")).get("mcpServers") or {})
    if tool == "zcode":
        return dict(_load_json_dict(expand(".zcode/cli/config.json")).get("mcp", {}).get("servers") or {})
    if tool == "cursor":
        return dict(_load_json_dict(expand(".cursor/mcp.json")).get("mcpServers") or {})
    if tool == "vscode":
        return dict(_load_json_dict(
            expand("Library/Application Support/Code/User/mcp.json")).get("servers") or {})
    if tool == "gemini":
        return dict(_load_json_dict(expand(".gemini/settings.json")).get("mcpServers") or {})
    if tool == "opencode":
        return dict(_load_json_dict(expand(".config/opencode/opencode.json")).get("mcp") or {})
    if tool == "codex":
        path = expand(".codex/config.toml")
        try:
            with path.open("rb") as f:
                return dict(tomllib.load(f).get("mcp_servers") or {})
        except (OSError, tomllib.TOMLDecodeError):
            return {}
    return {}


def _normalize_for_compare(tool: str, entry: dict) -> dict:
    """把工具方言条目规范化为可比较的公共字段集（command 数组展开等）。"""
    norm: dict = {}
    if "command" in entry:
        cmd = entry["command"]
        if isinstance(cmd, list):  # opencode
            norm["command"] = cmd[0] if cmd else None
            norm["args"] = list(cmd[1:])
        else:
            norm["command"] = cmd
            norm["args"] = list(entry.get("args") or [])
    if "url" in entry:
        norm["url"] = entry["url"]
    if entry.get("env"):
        norm["env"] = {k: str(v) for k, v in entry["env"].items()}
    return norm


def _canonical_compare_form(spec: McpSpec) -> dict:
    if spec.transport in ("http", "sse"):
        return {"url": spec.url}
    form = {"command": spec.command, "args": list(spec.args)}
    if spec.env:
        form["env"] = dict(spec.env)
    return form


def sync_mcp(specs: list[McpSpec], secrets: dict[str, str], installed_tools: list[str],
             apply: bool, prefer: str) -> list[str]:
    """分发 canonical 清单到各工具。返回输出行。"""
    from .mcp_manifest import HTTP_CAPABLE, expand_placeholders

    lines: list[str] = []
    # 展开占位符；有未解析变量的 server 整体跳过（不写半截配置）
    ready: list[tuple[McpSpec, McpSpec]] = []  # (原始, 展开后)
    for s in specs:
        expanded, missing = expand_placeholders(s, secrets)
        if missing:
            lines.append(f"[red]跳过 {s.name}: 密钥变量未定义 {' '.join(missing)}"
                         f"（补入环境变量或 secrets.toml）")
        else:
            ready.append((s, expanded))

    for tool in installed_tools:
        writer = MCP_WRITERS.get(tool)
        if writer is None:
            continue
        current = _current_entries(tool)
        to_write: list[McpSpec] = []
        for orig, expanded in ready:
            if orig.targets is not None and tool not in orig.targets:
                continue
            if expanded.transport in ("http", "sse") and tool not in HTTP_CAPABLE:
                lines.append(f"[yellow]跳过 {tool}:{expanded.name}（该工具不支持 "
                             f"{expanded.transport} 类型）[/yellow]")
                continue
            existing = current.get(expanded.name)
            if existing is not None:
                if _normalize_for_compare(tool, existing) == _canonical_compare_form(expanded):
                    continue  # 已是期望状态
                if prefer != "library":
                    lines.append(f"[yellow]conflict {tool}:{expanded.name} 已存在且定义不同"
                                 f" → 跳过（--prefer library 覆盖）[/yellow]")
                    continue
            to_write.append(expanded)
        if to_write:
            if apply:
                lines.extend(writer(to_write))
            else:
                lines.append(f"[plan] {tool}: 写入 {', '.join(s.name for s in to_write)}")
    return lines
