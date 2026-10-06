"""doctor：健康检查（配置可解析 / 断链 / 密钥占位完整性）。

输出结构化事实：{level, where, code, params}。人读文案由展示层渲染——
CLI 端用本模块的 ZH_TEXT（中文终端），桌面端前端用 ui/i18n.js 的
doctor.* 模板（跟随界面语言）。两侧 code 必须保持一致。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import tomlkit

from .config import load_config
from .mcp_manifest import _load_secrets, load_manifest
from .memory import resolve_memory_file
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

# CLI（中文终端）用的文案模板；桌面端见 desktop/ui/i18n.js 的 doctor.* 条目。
ZH_TEXT = {
    "parse_ok": "可解析",
    "json_parse_error": "JSON 解析失败: {err}",
    "toml_parse_error": "TOML 解析失败: {err}",
    "broken_link": "断链（指向 {target} 不存在）",
    "library_ok": "{n} 个 skill",
    "library_missing": "不存在（首次 sync --apply 时创建）",
    "memory_library_ok": "{n}B",
    "memory_library_missing": "不存在（首次 sync --apply 时收养/创建）",
    "mcp_secret_missing": "密钥变量未定义: {vars}",
    "dead_command": "command 指向的 {command} 不存在（死配置，建议删除）",
    "provider_external": "自定义端点 {url} 不在 providers 清单（halter providers adopt 可收编）",
    "cc_switch_overlap": "检测到 cc-switch 配置痕迹，双重管理会互相覆盖（建议收敛到单一管理者）",
}


def message_zh(item: dict) -> str:
    s = ZH_TEXT[item["code"]]
    for k, v in item.get("params", {}).items():
        s = s.replace("{" + k + "}", str(v))
    return s


def _check_json(path: Path) -> str | None:
    try:
        with path.open("rb") as f:
            json.load(f)
        return None
    except OSError:
        return None  # 不存在不算病
    except json.JSONDecodeError as e:
        return str(e)


def _check_toml(path: Path) -> str | None:
    try:
        with path.open("rb") as f:
            tomllib.load(f)
        return None
    except OSError:
        return None
    except tomllib.TOMLDecodeError as e:
        return str(e)


def run_doctor() -> list[dict]:
    """返回 {level, where, code, params} 列表；level ok / warn / error。"""
    results: list[dict] = []

    def add(level: str, where: str, code: str, **params: object) -> None:
        results.append({"level": level, "where": where, "code": code, "params": params})

    # 1. 工具配置文件可解析
    for tool, pattern in CHECKABLE_FILES:
        path = expand(pattern)
        if not path.exists():
            continue
        if path.suffix == ".toml":
            err = _check_toml(path)
            if err:
                add("error", f"{tool}: ~/{pattern}", "toml_parse_error", err=err)
            else:
                add("ok", f"{tool}: ~/{pattern}", "parse_ok")
        else:
            err = _check_json(path)
            if err:
                add("error", f"{tool}: ~/{pattern}", "json_parse_error", err=err)
            else:
                add("ok", f"{tool}: ~/{pattern}", "parse_ok")

    # 2. skills / subagents 断链
    for spec in TOOLS:
        for pattern in spec.skills_dirs:
            root = expand(pattern)
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.is_symlink() and not child.exists():
                    add("error", f"{spec.key}: {child.name}", "broken_link",
                        target=str(child.resolve(strict=False)))
        for pattern in getattr(spec, "agents_dirs", ()):
            root = expand(pattern)
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.is_symlink() and not child.exists():
                    add("error", f"{spec.key}: agent {child.name}", "broken_link",
                        target=str(child.resolve(strict=False)))
        for pattern in spec.memory_files:
            target = expand(pattern)
            if target.is_symlink() and not target.exists():
                add("error", f"{spec.key}: memory {target.name}", "broken_link",
                    target=str(target.resolve(strict=False)))

    # 3. 库状态
    cfg = load_config()
    library = resolve_library(cfg)
    if library.is_dir():
        n = len([p for p in library.iterdir() if (p / "SKILL.md").exists()])
        add("ok", f"库: {library}", "library_ok", n=n)
    else:
        add("warn", f"库: {library}", "library_missing")
    memory_lib = resolve_memory_file(cfg)
    if memory_lib.is_file():
        add("ok", f"memory 库: {memory_lib}", "memory_library_ok", n=memory_lib.stat().st_size)
    else:
        add("warn", f"memory 库: {memory_lib}", "memory_library_missing")

    # 4. MCP 清单密钥占位可解析
    import os

    specs = load_manifest()
    secrets = _load_secrets()
    for s in specs:
        placeholders = [v for v in (*s.env.values(), *s.headers.values())
                        if isinstance(v, str) and v.startswith("${") and v.endswith("}")]
        missing = [p for p in placeholders if p[2:-1] not in os.environ and p[2:-1] not in secrets]
        if missing:
            add("warn", f"MCP 清单: {s.name}", "mcp_secret_missing",
                vars=", ".join(p[2:-1] for p in missing))

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
                add("error", f"{tool}: MCP {m.name}", "dead_command", command=m.command)

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
        # Unknown shell expansions are not evidence of a missing executable.
        if "$" in s or s == "~" or (s.startswith("~") and not s.startswith("~/")):
            return None
        p = Path(home / s[2:]) if s.startswith("~/") else Path(s)
        absolute = p.is_absolute() or (len(s) > 2 and s[1] == ":" and s[2] == "\\") or s.startswith("\\\\")
        return p.exists() if absolute else _sh.which(s) is not None

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
                add("error", f"{tool}: hook {h.label} @ {h.event}", "dead_command", command=h.command)

    # 7. provider 双重管理检测：外部自定义端点不在清单 + cc-switch 残留痕迹
    from .providers_manifest import PROVIDER_CAPABLE
    from .providers_write import _read_codex_doc, detect_current

    for tool in PROVIDER_CAPABLE:
        cur = detect_current(tool)
        if cur["status"] == "external" and cur["base_url"]:
            add("warn", f"{tool}: provider", "provider_external", url=cur["base_url"])
    if "cc-switch" in str(_read_codex_doc().get("model_catalog_json", "")):
        add("warn", "codex: provider", "cc_switch_overlap")

    return results
