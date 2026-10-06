"""providers canonical 清单：~/.config/halter/providers.toml（模型供应商身份的事实源）。

密钥策略：token 真实值只存 secrets.toml（0600），键名约定 provider/<id>/<tool>；
providers.toml 永不出现真实密钥。本层不参与多机同步（push/pull），每台机器独立管理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import tomlkit

from .io_utils import atomic_write_text, parse_manifest_toml
from .registry import expand

PROVIDER_MANIFEST = lambda: expand(".config/halter/providers.toml")  # noqa: E731

PROVIDER_CAPABLE = ("claude", "codex")   # 仅有这两个写入器
OFFICIAL_ID = "official"                 # 内置：切回官方默认端点

# 内置供应商预设（借鉴 CC Switch 的 provider presets）：只固化「端点」这一
# 稳定事实；模型名迭代快，一律由用户 --model 自填。新增预设须有官方文档背书。
PRESETS: dict[str, dict] = {
    "zhipu": {
        "label": "Zhipu GLM",
        "claude": {"base_url": "https://open.bigmodel.cn/api/anthropic"},
        "codex": {"base_url": "https://open.bigmodel.cn/api/codex", "wire_api": "responses"},
        "note": "GLM Coding Plan 与 API key 均用此端点",
    },
    "deepseek": {
        "label": "DeepSeek",
        "claude": {"base_url": "https://api.deepseek.com/anthropic"},
        "note": "claude-opus 档位映射 deepseek-v4-pro（官方文档）",
    },
    "moonshot": {
        "label": "Moonshot Kimi",
        "claude": {"base_url": "https://api.moonshot.cn/anthropic"},
        "note": "仅中国站 key；国际站 api.moonshot.ai 不兼容此端点",
    },
}


# ---------------------------------------------------------------------------
# claude settings.json env 托管键：halter 在 env 中唯一负责读写的键集合

_EXACT_MANAGED_ENV = {
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_EFFORT_LEVEL",
}
_PREFIX_MANAGED_ENV = ("ANTHROPIC_DEFAULT_",)


def is_managed_env_key(key: str) -> bool:
    return key in _EXACT_MANAGED_ENV or key.startswith(_PREFIX_MANAGED_ENV)


def token_secret_key(pid: str, tool: str) -> str:
    return f"provider/{pid}/{tool}"


@dataclass
class ProviderClaude:
    base_url: str
    model: str | None = None                       # -> env.ANTHROPIC_MODEL
    env: dict[str, str] = field(default_factory=dict)   # 其余托管 env 键


@dataclass
class ProviderCodex:
    base_url: str
    model: str | None = None                       # -> 顶层 model
    name: str | None = None                        # 段内 name 显示名
    wire_api: str = "responses"
    reasoning_effort: str | None = None
    context_window: int | None = None


@dataclass
class ProviderSpec:
    id: str
    label: str = ""
    claude: ProviderClaude | None = None
    codex: ProviderCodex | None = None

    def tools(self) -> list[str]:
        out = []
        if self.claude is not None:
            out.append("claude")
        if self.codex is not None:
            out.append("codex")
        return out


def _claude_from_table(tbl) -> ProviderClaude:
    return ProviderClaude(
        base_url=str(tbl["base_url"]),
        model=tbl.get("model"),
        env={k: str(v) for k, v in dict(tbl.get("env", {})).items()},
    )


def _claude_to_table(c: ProviderClaude):
    tbl = tomlkit.table()
    tbl["base_url"] = c.base_url
    if c.model:
        tbl["model"] = c.model
    if c.env:
        env_tbl = tomlkit.table()
        for k, v in c.env.items():
            env_tbl[k] = v
        tbl["env"] = env_tbl
    return tbl


def _codex_from_table(tbl) -> ProviderCodex:
    return ProviderCodex(
        base_url=str(tbl["base_url"]),
        model=tbl.get("model"),
        name=tbl.get("name"),
        wire_api=str(tbl.get("wire_api", "responses")),
        reasoning_effort=tbl.get("reasoning_effort"),
        context_window=tbl.get("context_window"),
    )


def _codex_to_table(c: ProviderCodex):
    tbl = tomlkit.table()
    tbl["base_url"] = c.base_url
    if c.model:
        tbl["model"] = c.model
    if c.name:
        tbl["name"] = c.name
    if c.wire_api != "responses":
        tbl["wire_api"] = c.wire_api
    if c.reasoning_effort:
        tbl["reasoning_effort"] = c.reasoning_effort
    if c.context_window is not None:
        tbl["context_window"] = int(c.context_window)
    return tbl


def spec_from_table(tbl) -> ProviderSpec:
    return ProviderSpec(
        id=str(tbl["id"]),
        label=str(tbl.get("label", "")),
        claude=_claude_from_table(tbl["claude"]) if "claude" in tbl else None,
        codex=_codex_from_table(tbl["codex"]) if "codex" in tbl else None,
    )


def spec_to_table(s: ProviderSpec):
    tbl = tomlkit.table()
    tbl["id"] = s.id
    if s.label:
        tbl["label"] = s.label
    if s.claude is not None:
        tbl["claude"] = _claude_to_table(s.claude)
    if s.codex is not None:
        tbl["codex"] = _codex_to_table(s.codex)
    return tbl


def load_manifest(path: Path | None = None) -> list[ProviderSpec]:
    path = path or PROVIDER_MANIFEST()
    if not path.exists():
        return []
    doc = parse_manifest_toml(path)
    return [spec_from_table(tbl) for tbl in doc.get("provider", [])]


def save_manifest(specs: list[ProviderSpec], path: Path | None = None) -> None:
    path = path or PROVIDER_MANIFEST()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    aot = tomlkit.aot()
    for s in specs:
        aot.append(spec_to_table(s))
    doc["provider"] = aot
    atomic_write_text(path, tomlkit.dumps(doc))


# ---------------------------------------------------------------------------
# token 存取：真实值只进 secrets.toml（复用 mcp 层的 0600 写入器）


def load_tokens(path: Path | None = None) -> dict[str, str]:
    from .mcp_manifest import _load_secrets

    return _load_secrets(path)


def save_tokens(tokens: dict[str, str], path: Path | None = None) -> None:
    from .mcp_manifest import save_secrets

    save_secrets(tokens, path)


def get_token(pid: str, tool: str) -> str | None:
    return load_tokens().get(token_secret_key(pid, tool))


def set_token(pid: str, tool: str, value: str) -> None:
    tokens = load_tokens()
    tokens[token_secret_key(pid, tool)] = value
    save_tokens(tokens)


# ---------------------------------------------------------------------------
# adopt：从工具现有供应商配置收编入库（token -> secrets）


def _provider_name_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return "custom"
    if re.fullmatch(r"[\d.]+", host):          # IP（本地网关）-> local
        return "local"
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else parts[0]


def _unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        taken.add(base)
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    got = f"{base}-{n}"
    taken.add(got)
    return got


def _read_claude_current() -> dict[str, str]:
    """claude settings.json env 中的托管键（未脱敏）。"""
    from .io_utils import load_json_object

    path = expand(".claude/settings.json")
    if not path.exists():
        return {}
    data = load_json_object(path)
    env = data.get("env")
    if not isinstance(env, dict):
        return {}
    return {k: str(v) for k, v in env.items() if is_managed_env_key(k)}


def _read_codex_current() -> dict:
    """codex config.toml 当前激活供应商相关键（未脱敏）。"""
    import tomllib

    path = expand(".codex/config.toml")
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            doc = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    pid = doc.get("model_provider")
    if not isinstance(pid, str) or pid.startswith("halter_"):
        return {}                       # 未配置或已由 halter 管理
    section = (doc.get("model_providers") or {}).get(pid)
    if not isinstance(section, dict):
        return {}
    return {"section_id": pid, "section": section,
            "model": doc.get("model"), "reasoning_effort": doc.get("model_reasoning_effort"),
            "context_window": doc.get("model_context_window")}


def adopt_providers(apply: bool, name: str | None = None) -> list[str]:
    """从 claude env 与 codex [model_providers.*] 收编现有供应商为清单条目。

    claude 与 codex 端点同注册域时合并为同一 provider；否则各自成条目。
    """
    claude_env = _read_claude_current()
    codex_cur = _read_codex_current()

    claude_part: ProviderClaude | None = None
    codex_part: ProviderCodex | None = None
    claude_name = codex_name = None

    if claude_env.get("ANTHROPIC_BASE_URL"):
        rest = {k: v for k, v in claude_env.items()
                if k not in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL")}
        claude_part = ProviderClaude(
            base_url=claude_env["ANTHROPIC_BASE_URL"],
            model=claude_env.get("ANTHROPIC_MODEL"),
            env=rest,
        )
        claude_name = _provider_name_from_url(claude_part.base_url)
    if codex_cur:
        section = codex_cur["section"]
        codex_part = ProviderCodex(
            base_url=str(section.get("base_url", "")),
            model=codex_cur.get("model"),
            name=section.get("name"),
            wire_api=str(section.get("wire_api", "responses")),
            reasoning_effort=codex_cur.get("reasoning_effort"),
            context_window=codex_cur.get("context_window"),
        )
        codex_name = _provider_name_from_url(codex_part.base_url) if codex_part.base_url \
            else codex_cur["section_id"]

    if claude_part is None and codex_part is None:
        return ["未发现可收编的供应商配置（claude env 无自定义端点，codex 无第三方 provider 段）"]

    # 同注册域 -> 同一 provider
    merge = claude_part is not None and codex_part is not None and claude_name == codex_name
    if merge:
        candidates: list[tuple[str, ProviderClaude | None, ProviderCodex | None]] = [
            (claude_name, claude_part, codex_part)
        ]
    else:
        candidates = []
        if claude_part is not None:
            candidates.append((claude_name, claude_part, None))
        if codex_part is not None:
            candidates.append((codex_name, None, codex_part))
    if name is not None and len(candidates) == 1:
        candidates = [(name, candidates[0][1], candidates[0][2])]

    existing = load_manifest()
    taken = {s.id for s in existing} | {OFFICIAL_ID}
    lines: list[str] = []
    new_specs: list[ProviderSpec] = []
    new_tokens: dict[str, str] = {}

    for cand_name, c_part, x_part in candidates:
        pid = _unique_id(cand_name, taken)
        spec = ProviderSpec(id=pid, label=pid, claude=c_part, codex=x_part)
        if c_part is not None and claude_env.get("ANTHROPIC_AUTH_TOKEN"):
            new_tokens[token_secret_key(pid, "claude")] = claude_env["ANTHROPIC_AUTH_TOKEN"]
        if x_part is not None and codex_cur["section"].get("experimental_bearer_token"):
            new_tokens[token_secret_key(pid, "codex")] = \
                str(codex_cur["section"]["experimental_bearer_token"])
        tools = "/".join(spec.tools())
        secret_note = f"，token×{sum(1 for k in new_tokens if k.startswith(f'provider/{pid}/'))} 入 secrets.toml"
        lines.append(f"adopt {pid} ({tools}){secret_note}")
        new_specs.append(spec)

    if apply:
        save_manifest(existing + new_specs)
        if new_tokens:
            save_tokens(load_tokens() | new_tokens)
    else:
        lines.append("[dim]dry-run 未写入 providers.toml（--apply 生效）[/dim]")
    return lines
