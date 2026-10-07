"""数据模型：工具探测结果与各配置层对象（skills / MCP / 插件 / mods / hooks / subagents / memory / statusline）的统一表示。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sessions import SessionInfo

# 敏感字段名：报告与终端输出时对值脱敏
SENSITIVE_KEYS = {
    "authorization",
    "x-api-key",
    "api_key",
    "apikey",
    "access_key_id",
    "secret_access_key",
    "session_token",
    "token",
    "secret",
    "password",
}


def is_sensitive_key(key: str) -> bool:
    """Match credential names without redacting names like max_tokens."""
    normalized = key.strip().lower()
    return any(
        normalized == sensitive
        or normalized.endswith("_" + sensitive)
        or normalized.endswith("-" + sensitive)
        for sensitive in SENSITIVE_KEYS
    )


def redact(value: object) -> object:
    """递归脱敏：疑似密钥的值替换为 <REDACTED>。"""
    if isinstance(value, dict):
        return {
            k: ("<REDACTED>" if is_sensitive_key(str(k)) else redact(v))
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
    agents_dirs: tuple[str, ...] = ()
    # 用户级记忆/指令文件（CLAUDE.md / AGENTS.md / GEMINI.md…），相对主目录；
    # 多候选按序取第一个父目录存在者（跨平台路径差异）
    memory_files: tuple[str, ...] = ()
    # harness = 独立 AI 编码代理（CLI/Agent 形态）；editor = 编辑器宿主（扩展/插件寄生）
    category: str = "harness"
    notes: str = ""


@dataclass
class Detection:
    tool: str
    display: str
    installed: bool
    evidence: list[str] = field(default_factory=list)
    category: str = "harness"


@dataclass
class SkillInfo:
    name: str
    path: Path
    linked: bool = False          # 是否为 symlink（已同步的标志）
    builtin: bool = False         # 工具内置技能，同步时跳过
    description: str | None = None  # SKILL.md frontmatter 的 description（展示用）


@dataclass
class AgentInfo:
    name: str                     # 文件名去 .md 后缀
    path: Path
    linked: bool = False          # 是否为 symlink（已同步的标志）
    model: str | None = None      # frontmatter 中的 model 字段（仅展示用）


@dataclass
class MemoryInfo:
    path: Path                    # 工具侧记忆文件（CLAUDE.md / AGENTS.md / GEMINI.md…）
    present: bool = False         # 文件存在（真实文件或有效链接）
    linked: bool = False          # 是否为 symlink（已同步的标志）
    size: int = 0
    mtime: float | None = None    # 修改时间戳（展示用）


@dataclass
class StatuslineInfo:
    settings_path: Path           # statusline 配置所在文件（settings.json / config.toml…）
    present: bool = False         # statusline 配置存在
    payload: dict = field(default_factory=dict)  # 工具侧 canonical 片段（家族方言归一后）
    command: str | None = None    # statusLine.command（command 驱动家族；展示 / doctor 用）
    items: list[str] | None = None  # 声明式家族的条目列表（codex [tui].status_line）
    script: Path | None = None    # command 引用的本地脚本绝对路径（无则 None）
    script_linked: bool = False   # 脚本为 symlink（command 驱动家族的同步标志）
    synced: bool | None = None    # 与库片段一致（None = 库缺失无从判定）

    @property
    def linked(self) -> bool:
        """矩阵 ● 的统一口径：脚本已链接 或 配置与库片段一致。"""
        return self.script_linked or self.synced is True


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
    # 来源仓库/市场页 URL：插件自身 repository/homepage 优先，回退所属市场仓库
    source_url: str | None = None


@dataclass
class ModsInfo:
    plugin_id: str                # name@marketplace 或保留来源 name@builtin / name@synced
    origin: str = "marketplace"   # marketplace / builtin / synced
    version: str | None = None
    enabled: bool | None = None
    marketplace: str | None = None
    # hooks/hooks.json 声明的事件模块入口（保留来源的内置 mod 无从读取，为空）
    modules: list[str] = field(default_factory=list)


@dataclass
class HookInfo:
    event: str                    # canonical 事件名（Claude/ZCode 大写驼峰；cursor-only 保留小驼峰原名）
    label: str                    # 展示名：halter id / 第三方标记 / command 首段截断
    type: str = "command"         # command / prompt（仅 cursor）
    matcher: str | None = None
    command: str | None = None    # prompt 型为 None
    timeout: float | None = None  # 统一秒
    extra: dict = field(default_factory=dict)  # 原始字段（已脱敏，含 _otty 等第三方标记）


@dataclass
class ToolReport:
    tool: str
    display: str
    installed: bool
    category: str = "harness"
    skills: list[SkillInfo] = field(default_factory=list)
    agents: list[AgentInfo] = field(default_factory=list)
    memory: MemoryInfo | None = None
    statusline: StatuslineInfo | None = None
    mcp_servers: list[McpServerInfo] = field(default_factory=list)
    plugins: list[PluginInfo] = field(default_factory=list)
    mods: list[ModsInfo] = field(default_factory=list)
    hooks: list[HookInfo] = field(default_factory=list)
    sessions: list["SessionInfo"] = field(default_factory=list)
    scan_notes: list[str] = field(default_factory=list)
