"""hcm 自身配置：~/.config/hcm/config.toml（不存在时用默认值，不强制初始化）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

DEFAULT_CONFIG_PATH = Path.home() / ".config/hcm/config.toml"


@dataclass
class HcmConfig:
    library: str | None = None            # skills 事实源，None 走三层回退
    exclude_skills: list[str] = field(default_factory=list)
    exclude_mcp: list[str] = field(default_factory=list)
    exclude_hooks: list[str] = field(default_factory=list)   # hooks 清单按 id 排除
    agents_library: str | None = None     # subagents 事实源，None 走三层回退
    exclude_agents: list[str] = field(default_factory=list)


def load_config(path: Path | None = None) -> HcmConfig:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return HcmConfig()
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except (OSError, tomlkit.exceptions.ParseError):
        return HcmConfig()
    return HcmConfig(
        library=doc.get("library"),
        exclude_skills=list(doc.get("exclude_skills", [])),
        exclude_mcp=list(doc.get("exclude_mcp", [])),
        exclude_hooks=list(doc.get("exclude_hooks", [])),
        agents_library=doc.get("agents_library"),
        exclude_agents=list(doc.get("exclude_agents", [])),
    )


def save_config(cfg: HcmConfig, path: Path | None = None) -> None:
    path = path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    if cfg.library:
        doc["library"] = cfg.library
    if cfg.exclude_skills:
        doc["exclude_skills"] = cfg.exclude_skills
    if cfg.exclude_mcp:
        doc["exclude_mcp"] = cfg.exclude_mcp
    if cfg.exclude_hooks:
        doc["exclude_hooks"] = cfg.exclude_hooks
    if cfg.agents_library:
        doc["agents_library"] = cfg.agents_library
    if cfg.exclude_agents:
        doc["exclude_agents"] = cfg.exclude_agents
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
