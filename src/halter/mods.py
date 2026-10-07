"""Mods 层：Claude Code 函数式插件（mods，v2.1.287+）的只读盘点。

mod = 自带进程内 JS/TS 事件模块的插件：hooks/hooks.json 的 "modules"
指向事件处理入口（可改写界面 / 拦截工具调用与 prompt）。官方未给 mods
独立的目录或命令，安装与启停完全走插件体系（`claude plugin install` /
settings 的 `enabledPlugins`），因此本层只盘点不安装，同步走 plugins 层。

识别口径：
  marketplace   插件缓存内 hooks/hooks.json 含非空 modules 的已装插件
  builtin/synced  enabledPlugins 里的保留来源后缀（@builtin / @synced），
                  内置于 CLI 或由 claude.ai 同步，磁盘上不在插件缓存内
"""

from __future__ import annotations

from pathlib import Path

from .model import ModsInfo
from .plugins import _load_json, _read_enabled_map
from .registry import expand

# enabledPlugins 的保留来源后缀：@builtin（CLI 内置）/ @synced（claude.ai 同步）
RESERVED_ORIGINS = ("builtin", "synced")


def _mod_modules(install_dir: Path | None) -> list[str] | None:
    """是 mod 则返回 hooks/hooks.json 声明的事件模块入口，否则 None。

    只认 "modules"（进程内函数钩子）；classic 的 command 型 hooks.json 不算 mod。
    """
    if install_dir is None:
        return None
    data = _load_json(install_dir / "hooks" / "hooks.json")
    if not data:
        return None
    modules = data.get("modules")
    if isinstance(modules, list) and modules:
        return [str(m) for m in modules if isinstance(m, str) and m]
    return None


def _installed_records(installed_path: Path) -> list[tuple[str, str | None, Path | None]]:
    """installed_plugins.json 两种形态 → [(plugin_id, version, installPath)]。"""
    data = _load_json(installed_path)
    if not data:
        return []
    plugins_field = data.get("plugins")
    out: list[tuple[str, str | None, Path | None]] = []
    if isinstance(plugins_field, dict):
        # claude：{"id@marketplace": [{version, installPath, ...}]}
        for pid, records in plugins_field.items():
            rec = records[0] if isinstance(records, list) and records else {}
            rec = rec if isinstance(rec, dict) else {}
            path = rec.get("installPath")
            out.append((pid, rec.get("version"), Path(path) if path else None))
    elif isinstance(plugins_field, list):
        # zcode：[{id, version, installPath, ...}]
        for p in plugins_field:
            if not isinstance(p, dict):
                continue
            path = p.get("installPath")
            out.append((p.get("id", p.get("name", "?")), p.get("version"),
                        Path(path) if path else None))
    return out


def _family_mods(plugins_base: Path, enabled_path: Path) -> list[ModsInfo]:
    enabled = _read_enabled_map(enabled_path)
    mods: list[ModsInfo] = []
    seen: set[str] = set()
    for pid, version, install_dir in _installed_records(plugins_base / "installed_plugins.json"):
        modules = _mod_modules(install_dir)
        if modules is None:
            continue
        seen.add(pid)
        mods.append(ModsInfo(
            plugin_id=pid,
            version=version,
            marketplace=pid.rpartition("@")[2] if "@" in pid else None,
            origin="marketplace",
            enabled=enabled.get(pid),
            modules=modules,
        ))
    # 保留来源条目不在插件缓存里（CLI 内置 / claude.ai 同步），只能从声明识别
    for pid, on in sorted(enabled.items()):
        origin = pid.rpartition("@")[2]
        if origin in RESERVED_ORIGINS and pid not in seen:
            mods.append(ModsInfo(plugin_id=pid, origin=origin, enabled=on))
    return mods


def read_claude_mods() -> list[ModsInfo]:
    return _family_mods(expand(".claude/plugins"), expand(".claude/settings.json"))


def read_zcode_mods() -> list[ModsInfo]:
    return _family_mods(expand(".zcode/cli/plugins"), expand(".zcode/cli/config.json"))


MODS_READERS: dict[str, callable] = {
    "claude": read_claude_mods,
    "zcode": read_zcode_mods,
}
