"""model_health：路由解析与 provider 健康诊断（不输出 secret 值）。"""

from __future__ import annotations

import json

from harness_config_manager.config import HalterConfig
from harness_config_manager.model_health import (
    check_model_health,
    resolve_route,
)


SECRET = "sk-super-secret-do-not-leak"


def _cfg(model: str = "", providers: dict | None = None) -> HalterConfig:
    cfg = HalterConfig()
    if model:
        cfg.models["halter"] = model
    if providers:
        cfg.model_providers.update(providers)
    return cfg


def _by_id(report: dict) -> dict:
    return {check["id"]: check for check in report["checks"]}


def test_missing_model_config_is_reported() -> None:
    report = check_model_health(_cfg(), environ={})
    assert report["ok"] is False
    checks = _by_id(report)
    assert checks["config"]["status"] == "error"
    assert "halter model configure" in checks["config"]["detail"]


def test_local_endpoint_without_key_is_no_auth_available() -> None:
    report = check_model_health(
        _cfg("local/qwen-coder", {"local": {"base_url": "http://127.0.0.1:11434/v1"}}),
        environ={},
    )
    assert report["ok"] is True
    checks = _by_id(report)
    assert checks["api-key"]["status"] == "ok"
    assert "no-auth" in checks["api-key"]["detail"]


def test_remote_provider_missing_env_is_error_with_variable_name() -> None:
    report = check_model_health(
        _cfg("custom/gpt-x", {"custom": {"base_url": "https://api.example.com/v1",
                                         "api_key_env": "CUSTOM_API_KEY"}}),
        environ={},
    )
    assert report["ok"] is False
    assert report["apiKeyEnv"] == "CUSTOM_API_KEY"
    checks = _by_id(report)
    assert checks["api-key"]["status"] == "error"
    assert "CUSTOM_API_KEY" in checks["api-key"]["detail"]
    assert SECRET not in json.dumps(report)


def test_remote_provider_with_env_is_available_and_never_leaks_value() -> None:
    report = check_model_health(
        _cfg("custom/gpt-x", {"custom": {"base_url": "https://api.example.com/v1",
                                         "api_key_env": "CUSTOM_API_KEY"}}),
        environ={"CUSTOM_API_KEY": SECRET},
    )
    assert report["ok"] is True
    assert SECRET not in json.dumps(report)


def test_unknown_prefix_falls_back_to_openai_with_warning() -> None:
    report = check_model_health(_cfg("weird/model"), environ={})
    checks = _by_id(report)
    assert checks["provider"]["status"] == "warn"
    assert "未知前缀" in checks["provider"]["detail"]


def test_custom_provider_without_base_url_is_error() -> None:
    report = check_model_health(
        _cfg("broken/m", {"broken": {"api_key_env": "X_KEY"}}), environ={},
    )
    checks = _by_id(report)
    assert checks["base-url"]["status"] == "error"
    assert report["ok"] is False


def test_probe_distinguishes_no_auth_and_auth_failure() -> None:
    cfg = _cfg("local/qwen", {"local": {"base_url": "http://127.0.0.1:9/v1"}})

    def ok_probe(base_url, headers, timeout, model):
        assert headers == {}  # localhost + 无 key：不应带 Authorization
        return 200, '{"data": []}'

    def unauthorized_probe(base_url, headers, timeout, model):
        return 401, '{"error": "invalid key"}'

    healthy = check_model_health(cfg, probe=True, environ={}, probe_fn=ok_probe)
    assert _by_id(healthy)["probe"]["status"] == "ok"

    denied = check_model_health(cfg, probe=True, environ={},
                                probe_fn=unauthorized_probe)
    probe_check = _by_id(denied)["probe"]
    assert probe_check["status"] == "error"
    assert "鉴权失败" in probe_check["detail"]


def test_probe_with_key_sends_bearer_header() -> None:
    seen: dict = {}

    def probe(base_url, headers, timeout, model):
        seen.update(headers=headers, base_url=base_url)
        return 200, ""

    check_model_health(
        _cfg("custom/m", {"custom": {"base_url": "https://api.example.com/v1",
                                     "api_key_env": "CUSTOM_API_KEY"}}),
        probe=True, environ={"CUSTOM_API_KEY": SECRET}, probe_fn=probe,
    )
    assert seen["base_url"] == "https://api.example.com/v1/models"
    assert seen["headers"]["Authorization"] == f"Bearer {SECRET}"


def test_cli_model_check_json_output(fake_home, monkeypatch) -> None:
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    monkeypatch.setenv("CUSTOM_API_KEY", SECRET)
    (fake_home / ".config/halter").mkdir(parents=True, exist_ok=True)
    (fake_home / ".config/halter/config.toml").write_text(
        '[models]\nhalter = "custom/gpt-x"\n\n'
        '[model_providers.custom]\nbase_url = "https://api.example.com/v1"\n'
        'api_key_env = "CUSTOM_API_KEY"\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["model", "check", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["model"] == "custom/gpt-x"
    assert payload["apiKeyEnv"] == "CUSTOM_API_KEY"
    assert SECRET not in result.output


def test_resolve_route_matches_client_routing_semantics(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", SECRET)
    monkeypatch.setenv("ZHIPU_BASE_URL", "https://proxy.example.com/v1")

    from harness_config_manager.agent import OpenAICompatibleModelClient

    client = OpenAICompatibleModelClient()
    model, key, base = client._provider("zhipu/glm-4.7")
    assert model == "glm-4.7"
    assert key == SECRET
    assert base == "https://proxy.example.com/v1"

    route = resolve_route("zhipu/glm-4.7", HalterConfig())
    assert route.model == "glm-4.7"
    assert route.api_key_env == "ZHIPUAI_API_KEY"
    assert route.base_url == "https://proxy.example.com/v1"
    assert route.base_url_env == "ZHIPU_BASE_URL"
