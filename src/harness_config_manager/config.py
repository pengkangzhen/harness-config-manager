"""halter 自身配置：~/.config/halter/config.toml（不存在时用默认值，不强制初始化）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

DEFAULT_CONFIG_PATH = Path.home() / ".config/halter/config.toml"  # 兼容引用；运行时走 _config_path()


def _config_path() -> Path:
    """惰性求值：让测试的 Path.home() monkeypatch 生效。"""
    return Path.home() / ".config/halter/config.toml"


@dataclass
class HalterConfig:
    library: str | None = None            # skills 事实源，None 走三层回退
    exclude_skills: list[str] = field(default_factory=list)
    exclude_mcp: list[str] = field(default_factory=list)
    exclude_hooks: list[str] = field(default_factory=list)   # hooks 清单按 id 排除
    agents_library: str | None = None     # subagents 事实源，None 走三层回退
    exclude_agents: list[str] = field(default_factory=list)
    # 每个 harness 的默认模型（@harness/model 内联指定优先于此）
    models: dict[str, str] = field(default_factory=dict)
    # 每个 harness 的可选模型列表（下拉选择用；缺省用内置 GLM 系列表）
    model_catalog: dict[str, list[str]] = field(default_factory=dict)
    # OpenAI-compatible 模型端点；真实 key 仍只存环境变量 / secrets，不入 config
    model_providers: dict[str, dict[str, str]] = field(default_factory=dict)


def load_config(path: Path | None = None) -> HalterConfig:
    path = path or _config_path()
    if not path.exists():
        return HalterConfig()
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except (OSError, tomlkit.exceptions.ParseError):
        return HalterConfig()
    return HalterConfig(
        library=doc.get("library"),
        exclude_skills=list(doc.get("exclude_skills", [])),
        exclude_mcp=list(doc.get("exclude_mcp", [])),
        exclude_hooks=list(doc.get("exclude_hooks", [])),
        agents_library=doc.get("agents_library"),
        exclude_agents=list(doc.get("exclude_agents", [])),
        models={str(k): str(v) for k, v in dict(doc.get("models", {})).items()},
        model_catalog={
            str(k): [str(x) for x in list(v)]
            for k, v in dict(doc.get("model_catalog", {})).items()
        },
        model_providers={
            str(k): {
                str(field): str(value)
                for field, value in dict(v).items()
            }
            for k, v in dict(doc.get("model_providers", {})).items()
        },
    )


def save_config(cfg: HalterConfig, path: Path | None = None) -> None:
    path = path or _config_path()
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
    if cfg.models:
        models = tomlkit.table()
        for k in sorted(cfg.models):
            models[k] = cfg.models[k]
        doc["models"] = models
    if cfg.model_providers:
        providers = tomlkit.table()
        for k in sorted(cfg.model_providers):
            inner = tomlkit.table()
            for field in sorted(cfg.model_providers[k]):
                inner[field] = cfg.model_providers[k][field]
            providers[k] = inner
        doc["model_providers"] = providers
    if cfg.model_catalog:
        catalog = tomlkit.table()
        for k in sorted(cfg.model_catalog):
            inner = tomlkit.array()
            for item in cfg.model_catalog[k]:
                inner.append(item)
            catalog[k] = inner
        doc["model_catalog"] = catalog
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
