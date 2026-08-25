"""插件层：各工具插件清单的只读解析（安装动作见阶段 5）。

家族：
  claude 系   installed_plugins.json（claude 与 zcode 格式同源）+ 各自 enabled 开关
  codex       config.toml 顶层 [plugins."name@marketplace"]
  cursor      plugins/cache/<marketplace>/ 下目录枚举（机制不透明，仅盘点）
  vscode      `code --list-extensions --show-versions` 子进程
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

from .model import PluginInfo
from .registry import expand


def _load_json(path: Path) -> dict | None:
    try:
        with path.open("rb") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _read_installed_plugins(installed_path: Path) -> list[PluginInfo]:
    """兼容两种同源形态：
    claude: {"plugins": {"<id>@<marketplace>": [{version, installPath, scope, ...}]}}
    zcode:  {"plugins": [{"id": ..., "version": ..., "marketplace": ...}]}
    """
    data = _load_json(installed_path)
    if not data:
        return []
    plugins_field = data.get("plugins")
    result: list[PluginInfo] = []
    if isinstance(plugins_field, dict):
        for pid, records in plugins_field.items():
            version = None
            if isinstance(records, list) and records and isinstance(records[0], dict):
                version = records[0].get("version")
            result.append(PluginInfo(plugin_id=pid, version=version))
    elif isinstance(plugins_field, list):
        for p in plugins_field:
            if isinstance(p, dict):
                result.append(PluginInfo(
                    plugin_id=p.get("id", p.get("name", "?")),
                    version=p.get("version"),
                    marketplace=p.get("marketplace"),
                ))
    return result


def _read_enabled_map(path: Path) -> dict[str, bool]:
    data = _load_json(path)
    if not data:
        return {}
    enabled = data.get("enabledPlugins")
    if enabled is None and isinstance(data.get("plugins"), dict):
        # zcode：开关嵌套在 plugins.enabledPlugins 下
        enabled = data["plugins"].get("enabledPlugins")
    return {k: bool(v) for k, v in (enabled or {}).items()}


def read_claude_plugins() -> list[PluginInfo]:
    plugins = _read_installed_plugins(expand(".claude/plugins/installed_plugins.json"))
    enabled = _read_enabled_map(expand(".claude/settings.json"))
    for p in plugins:
        p.enabled = enabled.get(p.plugin_id)
    return plugins


def read_zcode_plugins() -> list[PluginInfo]:
    plugins = _read_installed_plugins(expand(".zcode/cli/plugins/installed_plugins.json"))
    enabled = _read_enabled_map(expand(".zcode/cli/config.json"))
    for p in plugins:
        p.enabled = enabled.get(p.plugin_id)
    return plugins


def read_codex_plugins() -> list[PluginInfo]:
    path = expand(".codex/config.toml")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    # [plugins."name@marketplace"] enabled = true
    result: list[PluginInfo] = []
    for pid, entry in (data.get("plugins") or {}).items():
        if isinstance(entry, bool):
            result.append(PluginInfo(plugin_id=pid, enabled=entry))
        elif isinstance(entry, dict):
            result.append(PluginInfo(plugin_id=pid, enabled=entry.get("enabled")))
    return result


def read_cursor_plugins() -> list[PluginInfo]:
    cache = expand(".cursor/plugins/cache")
    if not cache.is_dir():
        return []
    result: list[PluginInfo] = []
    for marketplace_dir in sorted(cache.iterdir()):
        if not marketplace_dir.is_dir():
            continue
        for plugin_dir in sorted(marketplace_dir.iterdir()):
            if plugin_dir.is_dir():
                result.append(PluginInfo(plugin_id=plugin_dir.name,
                                         marketplace=marketplace_dir.name))
    return result


# VS Code 扩展里与 AI 编码相关的关键词（其余为纯编辑器扩展，不属于 hcm 管理范围）
AI_EXTENSION_KEYWORDS = (
    "copilot", "chatgpt", "claude", "codex", "cursor", "gemini", "continue",
    "cline", "codeium", "windsurf", "tabnine", "cody", "augment", "amazonq",
    "aider", "roo-code", "trae",
)


def read_vscode_plugins() -> list[PluginInfo]:
    try:
        proc = subprocess.run(
            ["code", "--list-extensions", "--show-versions"],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    result = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or "@" not in line:
            continue
        ext_id, _, version = line.rpartition("@")
        if not any(k in ext_id.lower() for k in AI_EXTENSION_KEYWORDS):
            continue  # 纯编辑器扩展（Python/Jupyter/Docker 等），不是 AI 编码插件
        result.append(PluginInfo(plugin_id=ext_id, version=version))
    return result


PLUGIN_READERS: dict[str, callable] = {
    "claude": read_claude_plugins,
    "zcode": read_zcode_plugins,
    "codex": read_codex_plugins,
    "cursor": read_cursor_plugins,
    "vscode": read_vscode_plugins,
}
