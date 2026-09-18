"""Model-route resolution and provider health diagnostics.

Single source of truth for turning ``local/qwen-coder`` or ``zhipu/glm-4.7``
into (bare model, base URL, API-key env name).  ``OpenAICompatibleModelClient``
and the AHP availability check both delegate here, and ``check_model_health``
turns a resolved route into a list of status checks for ``halter model check``
and the desktop model view.  Secret values never leave this module: only
environment-variable *names* are reported.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .config import HalterConfig

# provider prefix -> (api key env, default base URL)
BUILTIN_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "zhipu": ("ZHIPUAI_API_KEY", "https://open.bigmodel.cn/api/paas/v4"),
}

LOCAL_HOSTNAMES = {
    "localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal",
}

ProbeFn = Callable[[str, dict[str, str], float, str], tuple[int, str]]


def is_local_base_url(base_url: str) -> bool:
    import urllib.parse

    try:
        return urllib.parse.urlparse(base_url).hostname in LOCAL_HOSTNAMES
    except ValueError:
        return False


@dataclass(frozen=True)
class ResolvedRoute:
    """Where a model string routes to, without ever holding a key value."""

    model: str                 # bare model id sent to the API
    provider: str              # resolved prefix ("openai" when unprefixed)
    prefix_known: bool         # prefix is builtin or user-configured
    prefix_configured: bool    # prefix comes from [model_providers]
    base_url: str | None
    base_url_env: str | None   # env var that overrode the base URL, if any
    api_key_env: str | None
    error: str | None          # resolution failure, e.g. missing base_url


def resolve_route(
    model: str | None, cfg: HalterConfig, environ: Mapping[str, str] | None = None
) -> ResolvedRoute:
    environ = os.environ if environ is None else environ
    raw = model or ""
    prefix, separator, bare = raw.partition("/")
    configured = cfg.model_providers.get(prefix) if separator else None

    if configured is not None:
        key_env = configured.get("api_key_env", "")
        default_base = configured.get("base_url") or BUILTIN_PROVIDERS.get(prefix, (None, ""))[1]
        if not default_base:
            return ResolvedRoute(
                model=bare or "default", provider=prefix, prefix_known=True,
                prefix_configured=True, base_url=None, base_url_env=None,
                api_key_env=key_env or None,
                error=f"model provider {prefix!r} has no base_url",
            )
        override_env = configured.get("base_url_env", "")
        base_url = environ.get(override_env, default_base) if override_env else default_base
        return ResolvedRoute(
            model=bare or "default", provider=prefix, prefix_known=True,
            prefix_configured=True, base_url=base_url,
            base_url_env=override_env or None, api_key_env=key_env or None,
            error=None,
        )

    if separator and prefix in BUILTIN_PROVIDERS:
        key_env, default_base = BUILTIN_PROVIDERS[prefix]
        override = f"{prefix.upper()}_BASE_URL"
        base_url = environ.get(override, default_base)
        return ResolvedRoute(
            model=bare or "default", provider=prefix, prefix_known=True,
            prefix_configured=False, base_url=base_url,
            base_url_env=override if override in environ else None,
            api_key_env=key_env, error=None,
        )

    # Unknown prefix or no prefix at all: the whole string goes to the
    # OpenAI-compatible default, matching the runtime client's behaviour.
    return ResolvedRoute(
        model=raw, provider="openai", prefix_known=not separator,
        prefix_configured=False,
        base_url=environ.get("OPENAI_BASE_URL", BUILTIN_PROVIDERS["openai"][1]),
        base_url_env="OPENAI_BASE_URL" if "OPENAI_BASE_URL" in environ else None,
        api_key_env=BUILTIN_PROVIDERS["openai"][0], error=None,
    )


def default_probe(url: str, headers: dict[str, str], timeout: float, _model: str) -> tuple[int, str]:
    """GET the given URL; returns (http status, body snippet)."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(2048).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2048).decode("utf-8", errors="replace")


def check_model_health(
    cfg: HalterConfig,
    *,
    model: str | None = None,
    probe: bool = False,
    timeout: float = 5.0,
    environ: Mapping[str, str] | None = None,
    probe_fn: ProbeFn | None = None,
) -> dict[str, Any]:
    """Static (+optional network) diagnostics; never emits key values."""
    environ = os.environ if environ is None else environ
    resolved_model = model or cfg.models.get("halter") or ""
    checks: list[dict[str, Any]] = []

    def add(check_id: str, label: str, status: str, detail: str = "") -> None:
        checks.append({"id": check_id, "label": label, "status": status, "detail": detail})

    if not resolved_model:
        add("config", "默认模型配置", "error",
            "config.toml 未配置 [models].halter；先运行 halter model configure")
        return {"ok": False, "available": False, "model": None,
                "provider": None, "apiKeyEnv": None, "checks": checks}
    add("config", "默认模型配置", "ok", resolved_model)

    route = resolve_route(resolved_model, cfg, environ=environ)
    if route.prefix_known:
        kind = "自定义 provider" if route.prefix_configured else "内置 provider"
        add("provider", "provider 前缀", "ok", f"{kind} {route.provider}")
    else:
        add("provider", "provider 前缀", "warn",
            f"未知前缀 {resolved_model.split('/')[0]}/，按 OpenAI 默认路由")

    if route.error or not route.base_url:
        add("base-url", "base URL", "error", route.error or "base URL 缺失")
    else:
        add("base-url", "base URL", "ok", route.base_url)

    key_present = bool(
        route.api_key_env and str(environ.get(route.api_key_env) or "").strip()
    )
    local = is_local_base_url(route.base_url or "")
    if key_present:
        add("api-key", "API key 环境变量", "ok",
            f"{route.api_key_env} 已设置（值不回显）")
    elif local:
        add("api-key", "API key 环境变量", "ok",
            "endpoint 为本地，允许 no-auth（未设置 key）"
            if route.api_key_env else "本地 endpoint 无需 key（no-auth）")
    elif route.api_key_env:
        add("api-key", "API key 环境变量", "error",
            f"环境变量 {route.api_key_env} 未设置；模型调用会被拒绝")
    else:
        add("api-key", "API key 环境变量", "error",
            "未配置 api_key_env，且 endpoint 非本地")

    if probe:
        if route.base_url and not route.error:
            headers: dict[str, str] = {}
            if route.api_key_env:
                value = str(environ.get(route.api_key_env) or "")
                if value:
                    headers["Authorization"] = f"Bearer {value}"
            try:
                status, _body = (probe_fn or default_probe)(
                    route.base_url.rstrip("/") + "/models",
                    headers, timeout, route.model,
                )
            except (OSError, ValueError) as exc:
                add("probe", "base URL 连通性", "error", f"{type(exc).__name__}: {exc}")
            else:
                if status == 200:
                    add("probe", "base URL 连通性", "ok", f"HTTP 200")
                elif status in (401, 403):
                    add("probe", "base URL 连通性", "error",
                        f"HTTP {status}：鉴权失败（key 缺失或无效）")
                else:
                    add("probe", "base URL 连通性", "warn",
                        f"HTTP {status}（/models 不可用不代表 chat 不可用）")
        else:
            add("probe", "base URL 连通性", "skipped", "base URL 不可用，跳过探测")
    else:
        add("probe", "base URL 连通性", "skipped", "未启用 --probe")

    add("tool-schema", "tool call schema 支持", "skipped",
        "需要真实 chat 请求才能验证；本检查不发起计费请求")

    ok = all(check["status"] != "error" for check in checks)
    return {
        "ok": ok,
        "available": ok,
        "model": resolved_model,
        "provider": route.provider,
        "apiKeyEnv": route.api_key_env,
        "checks": checks,
    }
