"""doctor：健康检查（配置可解析 / 断链 / 密钥占位完整性）。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import tomlkit

from .config import load_config
from .mcp_manifest import _load_secrets, load_manifest
from .registry import TOOLS, expand
from .skills import resolve_library

CHECKABLE_FILES = [
    ("claude", ".claude.json"),
    ("claude", ".claude/settings.json"),
    ("zcode", ".zcode/cli/config.json"),
    ("codex", ".codex/config.toml"),
    ("cursor", ".cursor/mcp.json"),
    ("cursor", ".cursor/hooks.json"),
    ("gemini", ".gemini/settings.json"),
    ("opencode", ".config/opencode/opencode.json"),
    ("vscode", "Library/Application Support/Code/User/mcp.json"),
]


def _check_json(path: Path) -> str | None:
    try:
        with path.open("rb") as f:
            json.load(f)
        return None
    except OSError:
        return None  # 不存在不算病
    except json.JSONDecodeError as e:
        return f"JSON 解析失败: {e}"


def _check_toml(path: Path) -> str | None:
    try:
        with path.open("rb") as f:
            tomllib.load(f)
        return None
    except OSError:
        return None
    except tomllib.TOMLDecodeError as e:
        return f"TOML 解析失败: {e}"


def run_doctor() -> list[tuple[str, str, str]]:
    """返回 (级别, 位置, 说明) 列表；级别 ok / warn / error。"""
    results: list[tuple[str, str, str]] = []

    # 1. 工具配置文件可解析
    for tool, pattern in CHECKABLE_FILES:
        path = expand(pattern)
        if not path.exists():
            continue
        issue = _check_toml(path) if path.suffix == ".toml" else _check_json(path)
        if issue:
            results.append(("error", f"{tool}: ~/{pattern}", issue))
        else:
            results.append(("ok", f"{tool}: ~/{pattern}", "可解析"))

    # 2. skills / subagents 断链
    for spec in TOOLS:
        for pattern in spec.skills_dirs:
            root = expand(pattern)
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.is_symlink() and not child.exists():
                    results.append(("error", f"{spec.key}: {child.name}",
                                    f"断链（指向 {child.resolve(strict=False)} 不存在）"))
        for pattern in getattr(spec, "agents_dirs", ()):
            root = expand(pattern)
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.is_symlink() and not child.exists():
                    results.append(("error", f"{spec.key}: agent {child.name}",
                                    f"断链（指向 {child.resolve(strict=False)} 不存在）"))

    # 3. 库状态
    library = resolve_library(load_config())
    if library.is_dir():
        n = len([p for p in library.iterdir() if (p / "SKILL.md").exists()])
        results.append(("ok", f"库: {library}", f"{n} 个 skill"))
    else:
        results.append(("warn", f"库: {library}", "不存在（首次 sync --apply 时创建）"))

    # 4. MCP 清单密钥占位可解析
    import os

    specs = load_manifest()
    secrets = _load_secrets()
    for s in specs:
        placeholders = [v for v in (*s.env.values(), *s.headers.values())
                        if isinstance(v, str) and v.startswith("${") and v.endswith("}")]
        missing = [p for p in placeholders if p[2:-1] not in os.environ and p[2:-1] not in secrets]
        if missing:
            results.append(("warn", f"MCP 清单: {s.name}",
                            f"密钥变量未定义: {', '.join(p[2:-1] for p in missing)}"))

    # 5. MCP 死配置检测：command 指向的可执行文件必须存在
    #    规则：enabled=false 的跳过（禁用即不生效）；相对路径跳过（相对哪里不可判定）；
    #    绝对路径查存在性，裸命令名查 PATH。
    import shutil as _shutil

    from .mcp import MCP_READERS, plugin_provided_mcp
    from .registry import BY_KEY

    for tool, reader in MCP_READERS.items():
        spec = BY_KEY.get(tool)
        if spec is None or not any(expand(p).exists() for p in spec.config_dirs):
            continue
        servers: list = []
        reader(servers, [])
        servers.extend(plugin_provided_mcp(tool))
        for m in servers:
            if not m.command:
                continue  # http 型或宿主注入（无静态 command）
            if m.extra.get("enabled") is False:
                continue  # 已禁用，不会被拉起
            if m.command.startswith("/"):
                alive = Path(m.command).exists()
            elif "/" in m.command or "\\" in m.command:
                continue  # 相对路径，无法可靠判定
            else:
                alive = _shutil.which(m.command) is not None
            if not alive:
                results.append(("error", f"{tool}: MCP {m.name}",
                                f"command 指向的 {m.command} 不存在（死配置，建议删除）"))

    # 6. hooks 死配置检测：仅对「单一脚本路径」形式的 command 判定存在性。
    #    复合 shell 表达式（if/case/管道等）无法可靠判定，一律跳过不误报。
    import re as _re
    import shutil as _sh

    from .hooks import HOOK_READERS
    from .model import HookInfo

    _META = _re.compile(r"[;&|`<>(){}\[\]]")

    def _hook_alive(cmd: str, home: Path) -> bool | None:
        s = cmd.strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
            s = s[1:-1].strip()
        for var in ("${HOME}", "$HOME"):
            s = s.replace(var, str(home))
        if not s or _META.search(s) or " " in s or s.startswith("-"):
            return None                      # 复合 shell / 带参数 / 标志，放弃判定
        p = Path(home / s[2:]) if s.startswith("~/") else Path(s)
        return p.exists() if p.is_absolute() else _sh.which(s) is not None

    for tool, reader in HOOK_READERS.items():
        spec = BY_KEY.get(tool)
        if spec is None or not any(expand(p).exists() for p in spec.config_dirs):
            continue
        infos: list[HookInfo] = []
        reader(infos, [])
        for h in infos:
            if not h.command or h.type != "command":
                continue
            alive = _hook_alive(h.command, Path.home())
            if alive is False:
                results.append(("error", f"{tool}: hook {h.label} @ {h.event}",
                                f"command 指向的 {h.command} 不存在（死配置，建议删除）"))

    return results
