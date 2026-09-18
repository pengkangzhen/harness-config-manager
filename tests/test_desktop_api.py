"""桌面 App（Tauri sidecar）依赖的 CLI JSON API 契约。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from harness_config_manager.cli import app

runner = CliRunner()


def test_version_json_contract() -> None:
    result = runner.invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["name"] == "halter"
    assert isinstance(payload["version"], str)
    assert payload["version"]


def test_version_plain() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.startswith("halter ")


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


def test_sessions_projects_json_contract(fake_home, monkeypatch, tmp_path) -> None:
    """projects 聚合：跨助手计数 / 最近活动排序 / 当前项目标记。"""
    from datetime import datetime, timezone

    from harness_config_manager import sessions as sess
    from harness_config_manager.sessions import SessionInfo

    t = datetime(2026, 9, 18, tzinfo=timezone.utc)
    captured: list[object] = []

    def fake_scan(project, tools=None):
        captured.append(project)
        return [
            SessionInfo("codex", "s1", tmp_path / "a.jsonl", project=Path("/Users/x/projA"),
                        updated_at=t, message_count=5),
            SessionInfo("claude", "s2", tmp_path / "b.jsonl", project=Path("/Users/x/projA"),
                        updated_at=t, message_count=3),
            SessionInfo("codex", "s3", tmp_path / "c.jsonl", project=Path("/Users/x/projB"),
                        started_at=t, message_count=1),
        ]

    monkeypatch.setattr(sess, "scan_sessions", fake_scan)
    result = runner.invoke(app, ["sessions", "projects", "--project", "/Users/x/projA", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert captured == [None]  # 全量扫描，不按单项目过滤
    assert payload["count"] == 2
    first = payload["projects"][0]
    assert first["name"] == "projA"
    assert first["sessions"] == 2
    assert first["messages"] == 8
    assert first["tools"] == ["claude", "codex"]
    assert first["tool_counts"] == {"codex": 1, "claude": 1}
    assert first["current"] is True
    assert payload["projects"][1]["current"] is False


def test_sessions_list_all_projects(fake_home, monkeypatch, tmp_path) -> None:
    """--all-projects 时以 project=None 全量扫描。"""
    from harness_config_manager import sessions as sess

    captured: list[object] = []

    def fake_scan(project, tools=None):
        captured.append(project)
        return []

    monkeypatch.setattr(sess, "scan_sessions", fake_scan)
    result = runner.invoke(app, ["sessions", "list", "--all-projects", "--json"])
    assert result.exit_code == 0
    assert captured == [None]
    payload = json.loads(result.output)
    assert payload["project"] == "all"


def test_tool_category_contract(fake_home) -> None:
    """矩阵默认只把独立 AI Harness 当列；编辑器宿主标记为 editor。"""
    from harness_config_manager.registry import TOOLS

    editors = {t.key for t in TOOLS if t.category == "editor"}
    harnesses = {t.key for t in TOOLS if t.category == "harness"}
    assert {"vscode", "continue", "cline", "trae", "aider", "windsurf"} <= editors
    assert {"claude", "codex", "zcode", "cursor", "gemini", "opencode", "copilot-cli"} <= harnesses

    result = runner.invoke(app, ["scan", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    cats = {t["tool"]: t.get("category") for t in payload["inventory"]}
    assert cats.get("vscode") == "editor"
    assert cats.get("claude") == "harness"


def test_sessions_projects_kind_classification(fake_home, monkeypatch, tmp_path) -> None:
    """temp/dated/virtual 目录不与真实项目混排；真实项目按存在性判定。"""
    from harness_config_manager import sessions as sess
    from harness_config_manager.sessions import SessionInfo

    real = tmp_path / "realproj"
    real.mkdir()
    cases = [
        (str(real), "project"),
        ("/private/tmp", "temp"),
        (str(fake_home / ".zcode/workspace/default"), "virtual"),
        (str(fake_home / "Documents/Codex/2026-09-08/hi"), "dated"),
        (str(tmp_path / "deleted-project"), "stale"),
    ]

    def fake_scan(project, tools=None):
        return [
            SessionInfo("codex", f"s{i}", tmp_path / f"f{i}.jsonl",
                        project=Path(path), message_count=1)
            for i, (path, _) in enumerate(cases)
        ]

    monkeypatch.setattr(sess, "scan_sessions", fake_scan)
    result = runner.invoke(app, ["sessions", "projects", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    got = {p["path"]: p["kind"] for p in payload["projects"]}
    for path, kind in cases:
        assert got[path] == kind, (path, got.get(path), kind)
