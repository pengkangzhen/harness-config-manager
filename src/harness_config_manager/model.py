"""数据模型：工具探测结果与三层对象（skills / MCP / 插件）的统一表示。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# 敏感字段名：报告与终端输出时对值脱敏
SENSITIVE_KEYS = {
    "authorization",
    "x-api-key",
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
}


def redact(value: object) -> object:
    """递归脱敏：疑似密钥的值替换为 <REDACTED>。"""
    if isinstance(value, dict):
        return {
            k: ("<REDACTED>" if any(s in k.lower() for s in SENSITIVE_KEYS) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


@dataclass
class ToolSpec:
    """注册表中一个 AI 编码工具的声明式描述。"""

    key: str
    display: str
    cli_names: tuple[str, ...] = ()
    # 以下路径均相对用户主目录；"{home}" 占位符由 registry 展开（用于嵌套 Library 路径）
    config_dirs: tuple[str, ...] = ()
    skills_dirs: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class Detection:
    tool: str
    display: str
    installed: bool
    evidence: list[str] = field(default_factory=list)


@dataclass
class SkillInfo:
    name: str
    path: Path
    linked: bool = False          # 是否为 symlink（已同步的标志）
    builtin: bool = False         # 工具内置技能，同步时跳过


@dataclass
class McpServerInfo:
    name: str
    transport: str                # stdio / http / sse
    command: str | None = None
    url: str | None = None
    extra: dict = field(default_factory=dict)  # 原始字段（已脱敏）


@dataclass
class PluginInfo:
    plugin_id: str                # name@marketplace 或扩展 id
    version: str | None = None
    enabled: bool | None = None
    marketplace: str | None = None


@dataclass
class ToolReport:
    tool: str
    display: str
    installed: bool
    skills: list[SkillInfo] = field(default_factory=list)
    mcp_servers: list[McpServerInfo] = field(default_factory=list)
    plugins: list[PluginInfo] = field(default_factory=list)
    scan_notes: list[str] = field(default_factory=list)
