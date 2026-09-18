"""工具注册表：本机已知的 AI 编码工具声明（检测方式 + skills 目录 + 能力边界）。

路径均相对用户主目录（macOS 实测，2026-08）。新增工具只需在此追加一条 ToolSpec。
"""

from __future__ import annotations

from pathlib import Path

from .model import ToolSpec

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="claude",
        display="Claude Code",
        cli_names=("claude",),
        config_dirs=(".claude",),
        skills_dirs=(".claude/skills", ".agents/skills"),  # 后者为其跨工具共享发现路径
        agents_dirs=(".claude/agents",),
        notes="MCP 在 ~/.claude.json 顶层 mcpServers；插件用 `claude plugin install -y` 无头安装",
    ),
    ToolSpec(
        key="zcode",
        display="ZCode",
        config_dirs=(".zcode",),
        skills_dirs=(".zcode/skills", ".agents/skills"),  # 同上
        agents_dirs=(".zcode/agents",),  # 注意不是 .zcode/cli/agents（那是会话数据）
        notes="MCP 在 ~/.zcode/cli/config.json 的 mcp.servers；插件体系与 Claude Code 同源",
    ),
    ToolSpec(
        key="codex",
        display="OpenAI Codex",
        cli_names=("codex",),
        config_dirs=(".codex",),
        skills_dirs=(".codex/skills",),
        notes="MCP 在 ~/.codex/config.toml 的 [mcp_servers.*]（TOML）；插件开关也在该文件 [plugins.*]",
    ),
    ToolSpec(
        key="cursor",
        display="Cursor",
        cli_names=("cursor-agent",),
        config_dirs=(".cursor",),
        skills_dirs=(".cursor/skills",),  # skills-cursor/ 为内置，跳过
        agents_dirs=(".cursor/agents",),
        notes="MCP 在 ~/.cursor/mcp.json；插件 v1 仅盘点不安装",
    ),
    ToolSpec(
        key="vscode",
        display="VS Code (Copilot)",
        cli_names=("code",),
        config_dirs=(
            "Library/Application Support/Code/User",
            ".vscode",
        ),
        category="editor",
        notes="MCP 两处：User/mcp.json（键 servers）与 ~/.vscode/mcp.json（键 mcpServers）；扩展用 code --install-extension",
    ),
    ToolSpec(
        key="gemini",
        display="Gemini CLI",
        cli_names=("gemini",),
        config_dirs=(".gemini",),
        skills_dirs=(".gemini/skills",),
        notes="MCP 在 ~/.gemini/settings.json 嵌套 mcpServers（另有 config/mcp_config.json）",
    ),
    ToolSpec(
        key="opencode",
        display="OpenCode",
        cli_names=("opencode",),
        config_dirs=(".config/opencode", ".opencode"),
        skills_dirs=(".config/opencode/skills",),
        notes="MCP 在 opencode.json 的 mcp 键，command 为数组格式",
    ),
    ToolSpec(
        key="copilot-cli",
        display="GitHub Copilot CLI",
        cli_names=("copilot",),
        notes="CLI 内置能力，无用户级 skills/MCP/插件配置可管理",
    ),
    ToolSpec(
        key="continue",
        display="Continue",
        config_dirs=(".continue",),
        skills_dirs=(".continue/skills",),
        category="editor",
        notes="仅 skills 层",
    ),
    ToolSpec(
        key="cline",
        display="Cline",
        config_dirs=(".cline",),
        category="editor",
        notes="仅配置目录检测，无用户级 skills 目录",
    ),
    ToolSpec(
        key="trae",
        display="Trae",
        config_dirs=(".trae",),
        skills_dirs=(".trae/skills",),
        category="editor",
        notes="仅 skills 层；skill-config.json 管理内置技能状态",
    ),
    ToolSpec(
        key="aider",
        display="Aider Desktop",
        config_dirs=(".aider-desk",),
        skills_dirs=(".aider-desk/skills",),
        category="editor",
        notes="仅 skills 层",
    ),
    ToolSpec(
        key="windsurf",
        display="Windsurf",
        config_dirs=(".codeium/windsurf",),
        skills_dirs=(".codeium/windsurf/skills",),
        category="editor",
        notes="仅 skills 层",
    ),
)

BY_KEY: dict[str, ToolSpec] = {t.key: t for t in TOOLS}


def expand(path_pattern: str) -> Path:
    """把注册表中的相对路径展开为绝对路径。"""
    return Path.home() / path_pattern
