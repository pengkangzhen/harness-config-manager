"""桌面 App（Tauri sidecar）依赖的 CLI JSON API 契约。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from harness_config_manager.cli import app

runner = CliRunner()


def test_version_json_contract() -> None:
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["name"] == "hcm"
    assert isinstance(payload["version"], str)
    assert payload["version"]


def test_version_plain() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.startswith("hcm ")


def test_scan_json_includes_doctor(fake_home) -> None:
    result = runner.invoke(app, ["scan", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload["tools_detected"], list)
    assert isinstance(payload["inventory"], list)
    assert isinstance(payload["doctor"], list)
    for item in payload["doctor"]:
        assert item["level"] in ("ok", "warn", "error")
        assert "where" in item
        assert "message" in item
