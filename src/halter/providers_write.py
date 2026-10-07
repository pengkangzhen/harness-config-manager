"""providers 方言写入器：canonical -> claude / codex 供应商配置（读-改-写，原子替换）。

claude：只动 settings.json env 中满足 is_managed_env_key 的键，其余 env 与顶层键不碰；
codex：halter 写入的段固定命名 model_providers.halter_<id>（前缀即归属标记），
       切换时移除其它 halter_* 段（token 卫生），第三方段（如 CC Switch 写的）保留。
official（内置 provider）：claude = 清空托管键；codex = 删 halter_* 段与托管顶层键。
"""

from __future__ import annotations

import tomllib
from datetime import datetime
from shutil import copy2

import tomlkit

from .io_utils import atomic_write_json, atomic_write_text, load_json_object
from .providers_manifest import (
    OFFICIAL_ID,
    PROVIDER_CAPABLE,
    ProviderClaude,
    ProviderSpec,
    get_token,
    is_managed_env_key,
    load_manifest,
    preset_label_for_url,
)
from .registry import expand

HALTER_SECTION_PREFIX = "halter_"


def _backup_before_write(path, tool: str) -> None:
    """切换前备份目标文件（借鉴 CC Switch 的安全垫：改配置前留可回滚副本）。"""
    if not path.exists():
        return
    # 微秒级时间戳：同一秒内多次切换也不互相覆盖
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = expand(".config/halter/backups/providers")
    backup_dir.mkdir(parents=True, exist_ok=True)
    copy2(path, backup_dir / f"{stamp}-{tool}-{path.name}")

# codex 顶层 halter 托管键
_CODEX_MANAGED_TOP = ("model_provider", "model", "model_reasoning_effort", "model_context_window")


def _section_id(pid: str) -> str:
    return f"{HALTER_SECTION_PREFIX}{pid}"


def _claude_env_values(c: ProviderClaude, token: str | None) -> dict[str, str]:
    env: dict[str, str] = {"ANTHROPIC_BASE_URL": c.base_url}
    if token:
        env["ANTHROPIC_AUTH_TOKEN"] = token
    if c.model:
        env["ANTHROPIC_MODEL"] = c.model
    env.update(c.env)
    return env


def write_claude(spec: ProviderSpec, token: str | None) -> list[str]:
    """~/.claude/settings.json 的 env 读-改-写。

    清理只针对托管键（ANTHROPIC_* / CLAUDE_CODE_*）——非托管键永不误删；
    provider env 可含任意键（def 白名单已放开），切换时一并写入。
    """
    path = expand(".claude/settings.json")
    data = load_json_object(path)
    if spec.id == OFFICIAL_ID or spec.claude is None:
        env = data.get("env")
        if not isinstance(env, dict) or not any(is_managed_env_key(k) for k in env):
            return ["claude: 已是官方默认（无托管 env 键）"]
        _backup_before_write(path, "claude")
        for k in [k for k in list(env) if is_managed_env_key(k)]:
            del env[k]
        atomic_write_json(path, data)
        return ["claude: 清空托管 env 键 → 官方默认 (~/.claude/settings.json)"]

    values = _claude_env_values(spec.claude, token)
    env = data.setdefault("env", {})
    for k in [k for k in list(env) if is_managed_env_key(k)]:
        del env[k]                       # 先清旧 provider 的托管键
    env.update(values)
    _backup_before_write(path, "claude")
    atomic_write_json(path, data)
    return [f"claude: {spec.id} → {spec.claude.base_url} (~/.claude/settings.json env)"]


def write_codex(spec: ProviderSpec, token: str | None) -> list[str]:
    """~/.codex/config.toml 读-改-写（tomlkit 保注释与第三方段）。"""
    path = expand(".codex/config.toml")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text)
    _backup_before_write(path, "codex")

    if spec.id == OFFICIAL_ID or spec.codex is None:
        providers = doc.get("model_providers")
        if providers is not None:
            for k in [k for k in list(providers) if str(k).startswith(HALTER_SECTION_PREFIX)]:
                del providers[k]
            if not providers:
                del doc["model_providers"]
        for k in _CODEX_MANAGED_TOP:
            if k in doc:
                del doc[k]
        atomic_write_text(path, tomlkit.dumps(doc))
        return ["codex: 移除 halter 段与托管顶层键 → 官方默认 (~/.codex/config.toml)"]

    providers = doc.setdefault("model_providers", tomlkit.table())
    section = _section_id(spec.id)
    doc["model_provider"] = section
    c = spec.codex
    if c.model:
        doc["model"] = c.model
    if c.reasoning_effort:
        doc["model_reasoning_effort"] = c.reasoning_effort
    if c.context_window is not None:
        doc["model_context_window"] = int(c.context_window)

    tbl = tomlkit.table()
    tbl["name"] = c.name or spec.label or spec.id
    tbl["base_url"] = c.base_url
    tbl["wire_api"] = c.wire_api
    tbl["requires_openai_auth"] = False
    if token:
        tbl["experimental_bearer_token"] = token
    providers[section] = tbl
    for k in [k for k in list(providers)
              if str(k).startswith(HALTER_SECTION_PREFIX) and str(k) != section]:
        del providers[k]                 # token 卫生：不留非激活段的密钥副本
    atomic_write_text(path, tomlkit.dumps(doc))
    return [f"codex: {spec.id} → {c.base_url} (~/.codex/config.toml)"]


PROVIDER_WRITERS = {"claude": write_claude, "codex": write_codex}


# ---------------------------------------------------------------------------
# 当前供应商推断：以目标文件实测为准，清单仅用于身份匹配


def _read_claude_env() -> dict[str, str]:
    path = expand(".claude/settings.json")
    if not path.exists():
        return {}
    try:
        data = load_json_object(path)
    except ValueError:
        return {}
    env = data.get("env")
    return {k: str(v) for k, v in env.items()} if isinstance(env, dict) else {}


def _read_codex_doc() -> dict:
    path = expand(".codex/config.toml")
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def live_claude_token() -> str | None:
    """实况 settings.json 的 ANTHROPIC_AUTH_TOKEN（收编继承用；不进 argv 与日志）。"""
    return _read_claude_env().get("ANTHROPIC_AUTH_TOKEN") or None


def detect_current(tool: str, specs: list[ProviderSpec] | None = None) -> dict:
    """推断工具当前供应商：{status, provider, base_url, model, detail}；claude 另带 env。

    status: halter（清单匹配）/ external（第三方配置）/ official（无自定义端点）。
    provider: halter = 清单 id；external = 命中内置预设时为厂商真名（如 Zhipu GLM），
              codex 退回第三方段名，claude 未知端点为空串。
    env（仅 claude）: 实况 settings.json 的扁平 env 全量（不含 ANTHROPIC_AUTH_TOKEN），
              供收编表单整块带入配置 JSON（含 API_TIMEOUT_MS 等非托管键）。
    """
    if specs is None:
        specs = load_manifest()
    if tool == "claude":
        env = _read_claude_env()
        base_url = env.get("ANTHROPIC_BASE_URL", "")
        flat = {k: v for k, v in env.items() if k != "ANTHROPIC_AUTH_TOKEN"}
        if not base_url:
            return {"status": "official", "provider": OFFICIAL_ID, "base_url": "",
                    "model": env.get("ANTHROPIC_MODEL", ""), "detail": "无自定义端点",
                    "env": flat}
        token = env.get("ANTHROPIC_AUTH_TOKEN", "")
        for s in specs:
            if s.claude is None:
                continue
            pid_token = get_token(s.id, "claude") or ""
            same_token = (not pid_token and not token) or (pid_token and pid_token == token)
            if s.claude.base_url == base_url and same_token:
                return {"status": "halter", "provider": s.id, "base_url": base_url,
                        "model": env.get("ANTHROPIC_MODEL", ""), "detail": "", "env": flat}
        return {"status": "external", "provider": preset_label_for_url(base_url) or "",
                "base_url": base_url,
                "model": env.get("ANTHROPIC_MODEL", ""),
                "detail": "端点不在 halter 清单中（halter providers adopt 可收编）",
                "env": flat}
    if tool == "codex":
        doc = _read_codex_doc()
        pid = doc.get("model_provider")
        if not isinstance(pid, str) or not pid:
            return {"status": "official", "provider": OFFICIAL_ID, "base_url": "",
                    "model": str(doc.get("model", "") or ""), "detail": "无 model_provider"}
        for s in specs:
            if s.codex is not None and _section_id(s.id) == pid:
                section = (doc.get("model_providers") or {}).get(pid, {})
                return {"status": "halter", "provider": s.id,
                        "base_url": str(section.get("base_url", "")),
                        "model": str(doc.get("model", "") or ""), "detail": ""}
        section = (doc.get("model_providers") or {}).get(pid, {})
        base_url = str(section.get("base_url", ""))
        return {"status": "external",
                "provider": preset_label_for_url(base_url) or pid,
                "base_url": base_url,
                "model": str(doc.get("model", "") or ""),
                "detail": f"第三方段 [{pid}]"}
    return {"status": "official", "provider": "", "base_url": "", "model": "", "detail": "未知工具"}


# ---------------------------------------------------------------------------
# 切换编排


def switch_provider(pid: str, tool: str) -> list[str]:
    """切换工具的激活供应商（含内置 official）。直接执行并写目标配置。

    token 缺失不阻止切换（本地网关可不鉴权），仅写入非密钥键。
    """
    if tool not in PROVIDER_CAPABLE:
        return [f"[red]不支持的工具 {tool}（可选：{' / '.join(PROVIDER_CAPABLE)}）[/red]"]
    specs = load_manifest()
    if pid == OFFICIAL_ID:
        spec = ProviderSpec(id=OFFICIAL_ID, label="official")
    else:
        spec = next((s for s in specs if s.id == pid), None)
        if spec is None:
            return [f"[red]未找到 provider '{pid}'（halter providers list 查看）[/red]"]
        if tool not in spec.tools():
            return [f"[red]provider '{pid}' 未声明 {tool} 配置块[/red]"]
    return PROVIDER_WRITERS[tool](spec, get_token(pid, tool))
