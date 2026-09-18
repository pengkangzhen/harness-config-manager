"""halter 自身配置：~/.config/halter/config.toml（不存在时用默认值，不强制初始化）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from .io_utils import atomic_write_text

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
    # A corrupt or unreadable file must fail loudly. Returning defaults here
    # would let a later save overwrite the user's real configuration.
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
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
    """Save managed fields while preserving unrelated tables and comments."""
    path = path or _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()

    scalar_fields = {
        "library": cfg.library,
        "agents_library": cfg.agents_library,
    }
    for key, value in scalar_fields.items():
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = value

    list_fields = {
        "exclude_skills": cfg.exclude_skills,
        "exclude_mcp": cfg.exclude_mcp,
        "exclude_hooks": cfg.exclude_hooks,
        "exclude_agents": cfg.exclude_agents,
    }
    for key, values in list_fields.items():
        if values:
            doc[key] = values
        else:
            doc.pop(key, None)

    table_fields = {
        "models": cfg.models,
        "model_catalog": cfg.model_catalog,
        "model_providers": cfg.model_providers,
    }
    for key, values in table_fields.items():
        if not values:
            doc.pop(key, None)
            continue
        table = tomlkit.table()
        for name in sorted(values):
            table[name] = values[name]
        doc[key] = table

    atomic_write_text(path, tomlkit.dumps(doc))
