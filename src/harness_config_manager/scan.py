"""组装：探测 + 五层扫描 -> ToolReport 列表。"""

from __future__ import annotations

from pathlib import Path

from .agents import scan_agents
from .detect import detect_tools
from .hooks import HOOK_READERS
from .model import ToolReport
from .mcp import MCP_READERS, plugin_provided_mcp
from .plugins import PLUGIN_READERS
from .registry import BY_KEY, TOOLS
from .sessions import scan_sessions
from .skills import scan_skills


def scan_tool(key: str) -> ToolReport:
    spec = BY_KEY[key]
    report = ToolReport(tool=spec.key, display=spec.display, installed=True, category=spec.category)

    skills, notes = scan_skills(spec)
    report.skills = skills
    report.scan_notes.extend(notes)

    agents, notes = scan_agents(spec)
    report.agents = agents
    report.scan_notes.extend(notes)

    if key in MCP_READERS:
        MCP_READERS[key](report.mcp_servers, report.scan_notes)
        # 插件提供的 MCP（.mcp.json 声明 + 宿主内置注入），与配置注册的同名去重
        existing = {m.name for m in report.mcp_servers}
        for m in plugin_provided_mcp(key):
            if m.name not in existing:
                report.mcp_servers.append(m)

    if key in PLUGIN_READERS:
        report.plugins = PLUGIN_READERS[key]()

    if key in HOOK_READERS:
        HOOK_READERS[key](report.hooks, report.scan_notes)

    # Session history is project-scoped rather than a distributed config layer.
    report.sessions = scan_sessions(Path.cwd(), tools=[key])

    return report


def scan_all() -> list[ToolReport]:
    reports: list[ToolReport] = []
    for det in detect_tools():
        if not det.installed:
            continue
        reports.append(scan_tool(det.tool))
    return reports


def all_tool_keys() -> list[str]:
    return [t.key for t in TOOLS]
