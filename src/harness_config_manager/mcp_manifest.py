"""MCP canonical 清单：~/.config/hcm/mcp.toml（期望状态的事实源）。

密钥策略：manifest 中一律 ${VAR} 占位；真实值存 ~/.config/hcm/secrets.toml（0600），
展开顺序 os.environ > secrets.toml。终端与报告永不打印真实值。
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from .model import SENSITIVE_KEYS, redact
from .registry import expand

MCP_MANIFEST = lambda: expand(".config/hcm/mcp.toml")  # noqa: E731
SECRETS_FILE = lambda: expand(".config/hcm/secrets.toml")  # noqa: E731

HTTP_CAPABLE = {"claude", "zcode"}  # 仅这两家支持 http/sse 类型


@dataclass
class McpSpec:
    name: str
    transport: str = "stdio"           # stdio / http / sse
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)      # 方言特有字段原样保留
    targets: list[str] | None = None               # None = 所有已装工具


def _looks_secret(key: str) -> bool:
    return any(s in key.lower() for s in SENSITIVE_KEYS)


def spec_from_tool_entry(name: str, entry: dict) -> McpSpec:
    """工具方言条目 -> canonical（密钥值换占位符）。"""
    spec = McpSpec(name=name)
    spec.transport = str(entry.get("type", "http" if "url" in entry else "stdio"))
    spec.command = entry.get("command")
    spec.args = list(entry.get("args") or [])
    spec.url = entry.get("url")
    if isinstance(spec.command, list):  # opencode 方言
        spec.command = spec.command[0] if spec.command else None
        spec.args = list(entry.get("command") or [])[1:]
    for k, v in (entry.get("env") or {}).items():
        spec.env[k] = f"${{{k}}}" if _looks_secret(k) else str(v)
    for k, v in (entry.get("headers") or {}).items():
        var = k.upper().replace("-", "_")
        spec.headers[k] = f"${{{var}}}" if _looks_secret(k) else str(v)
    for k, v in entry.items():
        if k not in ("type", "command", "args", "env", "url", "headers"):
            spec.extra[k] = v
    return spec


def load_manifest(path: Path | None = None) -> list[McpSpec]:
    path = path or MCP_MANIFEST()
    if not path.exists():
        return []
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    specs: list[McpSpec] = []
    for tbl in doc.get("server", []):
        specs.append(McpSpec(
            name=tbl["name"],
            transport=tbl.get("transport", "stdio"),
            command=tbl.get("command"),
            args=list(tbl.get("args", [])),
            env={k: str(v) for k, v in dict(tbl.get("env", {})).items()},
            url=tbl.get("url"),
            headers={k: str(v) for k, v in dict(tbl.get("headers", {})).items()},
            extra=dict(tbl.get("extra", {})),
            targets=list(tbl["targets"]) if "targets" in tbl else None,
        ))
    return specs


def save_manifest(specs: list[McpSpec], path: Path | None = None) -> None:
    path = path or MCP_MANIFEST()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    aot = tomlkit.aot()
    for s in specs:
        tbl = tomlkit.table()
        tbl["name"] = s.name
        tbl["transport"] = s.transport
        if s.command:
            tbl["command"] = s.command
        if s.args:
            tbl["args"] = s.args
        if s.env:
            env_tbl = tomlkit.table()
            for k, v in s.env.items():
                env_tbl[k] = v
            tbl["env"] = env_tbl
        if s.url:
            tbl["url"] = s.url
        if s.headers:
            h_tbl = tomlkit.table()
            for k, v in s.headers.items():
                h_tbl[k] = v
            tbl["headers"] = h_tbl
        if s.extra:
            e_tbl = tomlkit.table()
            for k, v in redact(s.extra).items():
                e_tbl[k] = v
            tbl["extra"] = e_tbl
        if s.targets is not None:
            tbl["targets"] = s.targets
        aot.append(tbl)
    doc["server"] = aot
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------------------
# secrets（仅存密钥真实值；文件权限 0600）


def _load_secrets(path: Path | None = None) -> dict[str, str]:
    path = path or SECRETS_FILE()
    if not path.exists():
        return {}
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    return {k: str(v) for k, v in doc.items()}


def save_secrets(secrets: dict[str, str], path: Path | None = None) -> None:
    path = path or SECRETS_FILE()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    for k, v in secrets.items():
        doc[k] = v
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def collect_secret(spec: McpSpec, entry: dict) -> dict[str, str]:
    """从工具配置原条目提取密钥真实值（manifest 占位符对应回填）。"""
    out: dict[str, str] = {}
    for k, v in (entry.get("env") or {}).items():
        if _looks_secret(k) and k in spec.env:
            out[k] = str(v)
    for k, v in (entry.get("headers") or {}).items():
        var = k.upper().replace("-", "_")
        if _looks_secret(k) and spec.headers.get(k) == f"${{{var}}}":
            out[var] = str(v)
    return out


def expand_placeholders(spec: McpSpec, secrets: dict[str, str]) -> tuple[McpSpec, list[str]]:
    """展开 ${VAR}：os.environ > secrets。返回 (展开后副本, 未解析变量名)。"""
    resolved, missing = dict(spec.__dict__), []

    def _expand(value: str) -> str:
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            var = value[2:-1]
            if var in os.environ:
                return os.environ[var]
            if var in secrets:
                return secrets[var]
            missing.append(var)
        return value

    resolved["env"] = {k: _expand(v) for k, v in spec.env.items()}
    resolved["headers"] = {k: _expand(v) for k, v in spec.headers.items()}
    return McpSpec(**resolved), missing


def auto_mcp_source(installed_tools: list[str]) -> str:
    """自动选 MCP 收集源：server 数最多的已装工具。"""
    from .mcp import MCP_READERS

    best, best_n = "claude", 0
    for tool in installed_tools:
        reader = MCP_READERS.get(tool)
        if reader is None:
            continue
        servers: list = []
        notes: list[str] = []
        reader(servers, notes)
        if len(servers) > best_n:
            best, best_n = tool, len(servers)
    return best


def adopt_mcp(source: str, apply: bool) -> list[str]:
    """从源工具收集 MCP 定义到 manifest（密钥真实值存 secrets.toml，权限 0600）。"""
    from .mcp_write import _current_entries

    raw = _current_entries(source)
    if not raw:
        return [f"{source}: 未发现 MCP servers，或配置不可读"]

    existing = {s.name: s for s in load_manifest()}
    secrets = _load_secrets()
    lines: list[str] = []
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        spec = spec_from_tool_entry(name, entry)
        got = collect_secret(spec, entry)
        if name in existing:
            lines.append(f"保留现有 manifest 定义 {name}（如需更新请手改 mcp.toml）")
            continue
        existing[name] = spec
        secrets.update(got)
        secret_note = f"，密钥×{len(got)} 入 secrets.toml" if got else ""
        lines.append(f"adopt {name} ({source}){secret_note}")

    if apply:
        save_manifest(list(existing.values()))
        if secrets:
            save_secrets(secrets)
    return lines
