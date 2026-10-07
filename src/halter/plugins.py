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


def _marketplace_repos(known_path: Path) -> dict[str, str]:
    """known_marketplaces.json → {市场名: 仓库 URL}（仅 github 源可解析出地址）。
    兼容两种形态：claude {name: meta} / zcode {"marketplaces": [meta]}。
    """
    data = _load_json(known_path)
    if not data:
        return {}
    if isinstance(data.get("marketplaces"), list):
        pairs = [(m.get("id") or m.get("name"), m)
                 for m in data["marketplaces"] if isinstance(m, dict)]
    else:
        pairs = [(name, meta) for name, meta in data.items() if isinstance(meta, dict)]
    repos: dict[str, str] = {}
    for name, meta in pairs:
        source = meta.get("source")
        repo = source.get("repo") if isinstance(source, dict) else None
        if name and isinstance(repo, str) and repo:
            repos[name] = f"https://github.com/{repo}"
    return repos


def _marketplace_plugin_sources(marketplaces_dir: Path) -> dict[tuple[str, str], str]:
    """市场清单 marketplaces/<市场>/…/marketplace.json 的条目级来源 → {(市场, 插件名): URL}。
    git-subdir/url 型 source 的 url 即插件仓库；相对路径型（同仓库子目录）回退条目 homepage。
    """
    sources: dict[tuple[str, str], str] = {}
    if not marketplaces_dir.is_dir():
        return sources
    for market_dir in sorted(marketplaces_dir.iterdir()):
        manifest = market_dir / ".claude-plugin" / "marketplace.json"
        if not manifest.is_file():
            manifest = market_dir / "marketplace.json"
        data = _load_json(manifest)
        if not data or not isinstance(data.get("plugins"), list):
            continue
        for entry in data["plugins"]:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            source = entry.get("source")
            url = source.get("url") if isinstance(source, dict) else None
            if not _is_source_page(url):
                url = entry.get("homepage")
            if _is_source_page(url):
                sources[(market_dir.name, entry["name"])] = url
    return sources


def _is_source_page(url: object) -> bool:
    """归档下载地址（.zip/.tgz 等 CDN 分发物）不是来源页，排除。"""
    return (isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and not url.split("?")[0].endswith((".zip", ".tgz", ".tar.gz")))


def _read_installed_plugins(
    installed_path: Path,
    plugin_sources: dict[tuple[str, str], str],
    repo_map: dict[str, str],
) -> list[PluginInfo]:
    """兼容两种同源形态：
    claude: {"plugins": {"<id>@<marketplace>": [{version, installPath, scope, ...}]}}
    zcode:  {"plugins": [{"id": ..., "version": ..., "marketplace": ...}]}
    来源优先级：市场清单条目 URL > 所属市场仓库 URL。
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
            result.append(PluginInfo(
                plugin_id=pid,
                version=version,
                marketplace=pid.rpartition("@")[2] if "@" in pid else None,
                source_url=_resolve_source_url(pid, plugin_sources, repo_map),
            ))
    elif isinstance(plugins_field, list):
        for p in plugins_field:
            if isinstance(p, dict):
                pid = p.get("id", p.get("name", "?"))
                result.append(PluginInfo(
                    plugin_id=pid,
                    version=p.get("version"),
                    marketplace=p.get("marketplace"),
                    source_url=_resolve_source_url(pid, plugin_sources, repo_map),
                ))
    return result


def _resolve_source_url(
    pid: str,
    plugin_sources: dict[tuple[str, str], str],
    repo_map: dict[str, str],
) -> str | None:
    name, _, marketplace = pid.partition("@")
    if not marketplace:
        return None
    return plugin_sources.get((marketplace, name)) or repo_map.get(marketplace)


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
    base = expand(".claude/plugins")
    plugin_sources = _marketplace_plugin_sources(base / "marketplaces")
    repo_map = _marketplace_repos(base / "known_marketplaces.json")
    plugins = _read_installed_plugins(base / "installed_plugins.json", plugin_sources, repo_map)
    enabled = _read_enabled_map(expand(".claude/settings.json"))
    for p in plugins:
        p.enabled = enabled.get(p.plugin_id)
    return plugins


def read_zcode_plugins() -> list[PluginInfo]:
    base = expand(".zcode/cli/plugins")
    plugin_sources = _marketplace_plugin_sources(base / "marketplaces")
    repo_map = _marketplace_repos(base / "known_marketplaces.json")
    plugins = _read_installed_plugins(base / "installed_plugins.json", plugin_sources, repo_map)
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


# VS Code 扩展里与 AI 编码相关的关键词（其余为纯编辑器扩展，不属于 halter 管理范围）
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
        # VS Code 扩展的来源即官方市场页（发布者与仓库信息都在市场页上）
        result.append(PluginInfo(
            plugin_id=ext_id,
            version=version,
            source_url=f"https://marketplace.visualstudio.com/items?itemName={ext_id}",
        ))
    return result


PLUGIN_READERS: dict[str, callable] = {
    "claude": read_claude_plugins,
    "zcode": read_zcode_plugins,
    "codex": read_codex_plugins,
    "cursor": read_cursor_plugins,
    "vscode": read_vscode_plugins,
}
