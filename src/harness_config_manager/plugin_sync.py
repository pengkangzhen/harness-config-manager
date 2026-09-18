"""插件层分发：manifest -> 各工具安装动作。

家族与安装路径：
  claude 系  claude 目标: `claude plugin install <id> -y -s user`
             zcode 目标: 镜像复制 claude 侧缓存 + 登记 installed_plugins.json + config.json 开关
             （CLAUDE_CONFIG_DIR 重定向实测不识别 ZCode 插件目录，故走文件镜像）
  codex      config.toml [plugins."<id>"] enabled = true
  vscode     `code --install-extension <id>`
  cursor     v1 仅盘点（市场机制不透明）
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import tomlkit

from .registry import expand

PLUGINS_MANIFEST = lambda: expand(".config/halter/plugins.toml")  # noqa: E731


@dataclass
class PluginSpec:
    plugin_id: str                      # name@marketplace 或 publisher.ext
    family: str = "claude"              # claude / codex / vscode
    targets: list[str] | None = None    # None = family 默认目标
    version: str | None = None          # zcode 镜像时使用的版本（缺省取 claude 侧最新）


DEFAULT_TARGETS = {
    "claude": ["claude", "zcode"],
    "codex": ["codex"],
    "vscode": ["vscode"],
}


def load_plugin_manifest() -> list[PluginSpec]:
    path = PLUGINS_MANIFEST()
    if not path.exists():
        return []
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    return [
        PluginSpec(
            plugin_id=t["id"],
            family=t.get("family", "claude"),
            version=t.get("version"),
            targets=list(t["targets"]) if "targets" in t else None,
        )
        for t in doc.get("plugin", [])
    ]


def save_plugin_manifest(specs: list[PluginSpec]) -> None:
    path = PLUGINS_MANIFEST()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    aot = tomlkit.aot()
    for s in specs:
        tbl = tomlkit.table()
        tbl["id"] = s.plugin_id
        tbl["family"] = s.family
        if s.version:
            tbl["version"] = s.version
        if s.targets is not None:
            tbl["targets"] = s.targets
        aot.append(tbl)
    doc["plugin"] = aot
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def auto_plugin_source() -> str:
    """自动选插件收集源：启用插件数最多的 claude 系工具。"""
    from .plugins import read_claude_plugins, read_zcode_plugins

    try:
        n_claude = sum(1 for p in read_claude_plugins() if p.enabled)
    except Exception:
        n_claude = 0
    try:
        n_zcode = sum(1 for p in read_zcode_plugins() if p.enabled)
    except Exception:
        n_zcode = 0
    return "zcode" if n_zcode > n_claude else "claude"


def adopt_plugins(source: str, apply: bool) -> list[str]:
    """从 claude/zcode 收集启用的插件进 manifest（family=claude）。"""
    if source not in ("claude", "zcode"):
        return [f"暂不支持从 {source} 收集插件（支持 claude / zcode）"]

    if source == "claude":
        from .plugins import read_claude_plugins
        installed = read_claude_plugins()
    else:
        from .plugins import read_zcode_plugins
        installed = read_zcode_plugins()
    enabled = [p for p in installed if p.enabled]

    existing = {p.plugin_id for p in load_plugin_manifest()}
    specs: list[PluginSpec] = list(load_plugin_manifest())
    lines: list[str] = []
    for p in enabled:
        if p.plugin_id in existing:
            continue
        specs.append(PluginSpec(plugin_id=p.plugin_id, family="claude",
                                version=p.version))
        lines.append(f"adopt {p.plugin_id} ({source})")
    if apply and lines:
        save_plugin_manifest(specs)
    if not lines:
        lines.append("没有新启用插件可收集")
    return lines


# ---------------------------------------------------------------------------
# 安装


def _installed_ids(tool: str) -> set[str]:
    if tool == "claude":
        path = expand(".claude/plugins/installed_plugins.json")
    else:
        path = expand(".zcode/cli/plugins/installed_plugins.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    plugins_field = data.get("plugins")
    if isinstance(plugins_field, dict):
        return set(plugins_field.keys())
    if isinstance(plugins_field, list):
        return {p.get("id", p.get("name", "?")) for p in plugins_field if isinstance(p, dict)}
    return set()


def _install_claude(spec: PluginSpec) -> str:
    proc = subprocess.run(
        ["claude", "plugin", "install", spec.plugin_id, "-s", "user", "-y"],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        return f"claude: 安装 {spec.plugin_id} 失败: {(proc.stderr or proc.stdout).strip()[:200]}"
    return f"claude: 已安装 {spec.plugin_id}"


def _install_zcode(spec: PluginSpec) -> str:
    """镜像 claude 缓存到 zcode（两侧清单格式同源，ZCode 为数组形态）。"""
    name, _, marketplace = spec.plugin_id.partition("@")
    src_root = expand(f".claude/plugins/cache/{marketplace}/{name}")
    if not src_root.is_dir():
        return (f"zcode: 跳过 {spec.plugin_id}（claude 侧缓存不存在，"
                f"请先在 claude 目标安装或手动处理）")
    versions = sorted(d.name for d in src_root.iterdir() if d.is_dir())
    version = spec.version if spec.version in versions else (versions[-1] if versions else "0.0.0")
    src = src_root / version
    dst = expand(f".zcode/cli/plugins/cache/{marketplace}/{name}/{version}")
    if not dst.exists():
        shutil.copytree(src, dst)

    installed_path = expand(".zcode/cli/plugins/installed_plugins.json")
    try:
        data = json.loads(installed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"version": 1, "plugins": []}
    ids = {p.get("id") for p in data.get("plugins", [])}
    if spec.plugin_id not in ids:
        now = datetime.now(timezone.utc).isoformat()
        data.setdefault("plugins", []).append({
            "id": spec.plugin_id,
            "name": name,
            "marketplace": marketplace,
            "version": version,
            "installPath": str(dst),
            "installedAt": now,
            "updatedAt": now,
            "scope": "user",
            "source": f"./plugins/{name}",
        })
        installed_path.parent.mkdir(parents=True, exist_ok=True)
        installed_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    cfg_path = expand(".zcode/cli/config.json")
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cfg = {}
    cfg.setdefault("plugins", {}).setdefault("enabledPlugins", {})[spec.plugin_id] = True
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"zcode: 已镜像 {spec.plugin_id}@{version}"


def _install_codex(spec: PluginSpec) -> str:
    path = expand(".codex/config.toml")
    if not path.exists():
        return f"codex: 跳过（{path} 不存在）"
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    doc.setdefault("plugins", {})[spec.plugin_id] = {"enabled": True}  # 含 @ 的键序列化时自动加引号
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return f"codex: 已启用 {spec.plugin_id}"


def _install_vscode(spec: PluginSpec) -> str:
    proc = subprocess.run(
        ["code", "--install-extension", spec.plugin_id],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        return f"vscode: 安装 {spec.plugin_id} 失败: {(proc.stderr or proc.stdout).strip()[:200]}"
    return f"vscode: 已安装 {spec.plugin_id}"


INSTALLERS = {
    "claude": _install_claude,
    "zcode": _install_zcode,
    "codex": _install_codex,
    "vscode": _install_vscode,
}


def sync_plugins(specs: list[PluginSpec], installed_tools: list[str],
                 apply: bool) -> list[str]:
    lines: list[str] = []
    for s in specs:
        targets = s.targets if s.targets is not None else DEFAULT_TARGETS.get(s.family, [])
        installer_missing = [t for t in targets if t not in INSTALLERS]
        for t in installer_missing:
            lines.append(f"[yellow]跳过 {t}:{s.plugin_id}（v1 不支持向 {t} 安装）[/yellow]")
        for target in (t for t in targets if t in INSTALLERS and t in installed_tools):
            already = s.plugin_id in _installed_ids(target) if target in ("claude", "zcode") else False
            if already:
                lines.append(f"{target}: {s.plugin_id} 已安装，跳过")
                continue
            if apply:
                lines.append(INSTALLERS[target](s))
            else:
                lines.append(f"[plan] {target}: 安装 {s.plugin_id}")
    return lines
