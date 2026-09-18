"""MCP 层：各工具配置方言的读取器（写入器见 mcp_write，阶段 4）。

方言一览（macOS 实测）：
  claude   ~/.claude.json                      -> mcpServers{...}   JSON（stdio/http 两型）
  zcode    ~/.zcode/cli/config.json            -> mcp.servers{...}  JSON
  codex    ~/.codex/config.toml                -> [mcp_servers.*]   TOML
  cursor   ~/.cursor/mcp.json                  -> mcpServers{...}   JSON
  vscode   User/mcp.json（键 servers）+ ~/.vscode/mcp.json（键 mcpServers）JSON
  gemini   ~/.gemini/settings.json             -> mcpServers{...}   JSON（嵌套在 settings）
  opencode ~/.config/opencode/opencode.json    -> mcp{...}          JSON（command 为数组）
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Callable

from .io_utils import version_key
from .model import McpServerInfo, redact
from .registry import expand

McpReader = Callable[[list[McpServerInfo], list[str]], None]
"""方言读取器：向 out 追加 server，向 notes 追加扫描说明（如文件缺失/解析失败）。"""


def _load_json(path: Path) -> dict | None:
    try:
        with path.open("rb") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _parse_common(entry: dict) -> tuple[str, str | None, str | None]:
    """从方言条目提取 (transport, command, url)。"""
    transport = str(entry.get("type", "stdio"))
    if "url" in entry:
        transport = transport if transport != "stdio" else "http"
    return transport, entry.get("command"), entry.get("url")


def _from_mcp_servers(raw: dict) -> list[McpServerInfo]:
    servers: list[McpServerInfo] = []
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        transport, command, url = _parse_common(entry)
        extra = {k: v for k, v in redact(entry).items()
                 if k not in ("type", "command", "url")}
        servers.append(McpServerInfo(name=name, transport=transport,
                                     command=command, url=url, extra=extra))
    return servers


def _reader_json(path_pattern: str, top_key: str) -> McpReader:
    def read(out: list[McpServerInfo], notes: list[str]) -> None:
        path = expand(path_pattern)
        data = _load_json(path)
        if data is None:
            if path.exists():
                notes.append(f"MCP 配置解析失败: ~/{path_pattern}")
            return
        raw = data.get(top_key) or {}
        out.extend(_from_mcp_servers(raw))
    return read


def read_claude(out: list, notes: list) -> None:
    _reader_json(".claude.json", "mcpServers")(out, notes)


def read_zcode(out: list, notes: list) -> None:
    path = expand(".zcode/cli/config.json")
    data = _load_json(path)
    if data is None:
        if path.exists():
            notes.append("MCP 配置解析失败: ~/.zcode/cli/config.json")
        return
    mcp = data.get("mcp") or {}
    out.extend(_from_mcp_servers(mcp.get("servers") or {}))


def read_codex(out: list, notes: list) -> None:
    path = expand(".codex/config.toml")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return
    for name, entry in (data.get("mcp_servers") or {}).items():
        if not isinstance(entry, dict):
            continue
        transport, command, url = _parse_common(entry)
        extra = {k: v for k, v in redact(dict(entry)).items()
                 if k not in ("type", "command", "url")}
        out.append(McpServerInfo(name=name, transport=transport,
                                 command=command, url=url, extra=extra))


def read_vscode(out: list, notes: list) -> None:
    # 用户级：键名是 servers
    user = _load_json(expand("Library/Application Support/Code/User/mcp.json"))
    if user and "servers" in user:
        out.extend(_from_mcp_servers(user["servers"]))
    # CLI/外部工具级：键名是 mcpServers
    _reader_json(".vscode/mcp.json", "mcpServers")(out, notes)


def read_gemini(out: list, notes: list) -> None:
    _reader_json(".gemini/settings.json", "mcpServers")(out, notes)
    # 独立 MCP 配置文件（部分版本使用）
    alt = expand(".gemini/config/mcp_config.json")
    data = _load_json(alt)
    if data and "mcpServers" in data:
        for s in _from_mcp_servers(data["mcpServers"]):
            if all(s.name != existing.name for existing in out):
                out.append(s)


def read_opencode(out: list, notes: list) -> None:
    path = expand(".config/opencode/opencode.json")
    data = _load_json(path)
    if data is None:
        return
    for name, entry in (data.get("mcp") or {}).items():
        if not isinstance(entry, dict):
            continue
        command_field = entry.get("command")
        # opencode 的 command 是数组：[程序, 参数...]
        if isinstance(command_field, list):
            command = command_field[0] if command_field else None
            extra = {**{k: v for k, v in redact(entry).items() if k != "command"},
                     "args_from_command_array": command_field[1:]}
        else:
            command = command_field
            extra = {k: v for k, v in redact(entry).items() if k != "command"}
        out.append(McpServerInfo(name=name, transport=str(entry.get("type", "local")),
                                 command=command, url=entry.get("url"), extra=extra))


# 宿主为特定插件内置注入的 MCP（无静态声明文件，按已启用插件推断）。
# 例：ZCode 宿主为 browser-use 插件提供 node_repl（运行时身份由宿主管理）。
BUNDLED_PLUGIN_MCP: dict[str, list[str]] = {
    "browser-use": ["node_repl"],
    "zcode-cua": ["computer-use"],
}

# 各工具插件缓存根与启用开关读取
_PLUGIN_CACHE_ROOT = {
    "claude": ".claude/plugins/cache",
    "zcode": ".zcode/cli/plugins/cache",
}


def plugin_provided_mcp(tool: str) -> list[McpServerInfo]:
    """枚举该工具插件提供的 MCP：插件 .mcp.json 声明 + 宿主内置注入。

    插件计入条件：已登记且 enabled=true，或未登记但缓存存在（宿主预装，
    如 ZCode 的 browser-use/zcode-cua 不出现在 installed_plugins.json）。
    """
    if tool not in _PLUGIN_CACHE_ROOT:
        return []
    from .plugins import read_claude_plugins, read_zcode_plugins

    if tool == "claude":
        installed = {p.plugin_id: p for p in read_claude_plugins()}
    else:
        installed = {p.plugin_id: p for p in read_zcode_plugins()}
    cache_root = expand(_PLUGIN_CACHE_ROOT[tool])

    def _active(plugin_id: str) -> bool:
        p = installed.get(plugin_id)
        return True if p is None else p.enabled is not False

    servers: list[McpServerInfo] = []
    if not cache_root.is_dir():
        return servers
    for marketplace_dir in cache_root.iterdir():
        if not marketplace_dir.is_dir():
            continue
        for plugin_dir in marketplace_dir.iterdir():
            if not plugin_dir.is_dir():
                continue
            plugin_id = f"{plugin_dir.name}@{marketplace_dir.name}"
            if not _active(plugin_id):
                continue
            # 宿主内置注入
            for bundled in BUNDLED_PLUGIN_MCP.get(plugin_dir.name, []):
                servers.append(McpServerInfo(
                    name=bundled, transport="stdio", command=None,
                    extra={"via": f"插件 {plugin_dir.name}（宿主内置注入）"}))
            # 插件 .mcp.json 声明（取最新版本目录）
            versions = sorted((d for d in plugin_dir.iterdir() if d.is_dir()), key=lambda item: version_key(item.name))
            if not versions:
                continue
            decl = _load_json(versions[-1] / ".mcp.json")
            if not decl:
                continue
            declared_servers = decl.get("mcpServers")
            if not isinstance(declared_servers, dict):
                continue
            for srv, entry in declared_servers.items():
                if not isinstance(entry, dict):
                    continue
                transport, command, url = _parse_common(entry)
                servers.append(McpServerInfo(
                    name=srv, transport=transport, command=command, url=url,
                    extra={"via": f"插件 {plugin_id}"}))
    return servers


MCP_READERS: dict[str, McpReader] = {
    "claude": read_claude,
    "zcode": read_zcode,
    "codex": read_codex,
    "cursor": _reader_json(".cursor/mcp.json", "mcpServers"),
    "vscode": read_vscode,
    "gemini": read_gemini,
    "opencode": read_opencode,
}
