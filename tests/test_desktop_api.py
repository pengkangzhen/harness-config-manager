"""桌面 App（Tauri sidecar）依赖的 CLI JSON API 契约。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from halter.cli import app

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
    assert "halter" in result.output


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
        assert "code" in item
        assert "params" in item


def test_scan_json_statusline_contract(fake_home: Path) -> None:
    """desktop overview chip / 矩阵的 statusline 数据源：三态字段形状。"""
    (fake_home / ".claude").mkdir()
    script = fake_home / ".claude/statusline.py"
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (fake_home / ".claude/settings.json").write_text(json.dumps({
        "statusLine": {"type": "command", "command": f"python3 {script}", "padding": 0}}),
        encoding="utf-8")
    result = runner.invoke(app, ["scan", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    claude = next(t for t in payload["inventory"] if t["tool"] == "claude")
    s = claude["statusline"]
    assert s["present"] is True and s["linked"] is False   # ◐ 本地配置
    assert s["command"] == f"python3 {script}"
    assert s["script"] == str(script)


def test_sessions_projects_json_contract(fake_home, monkeypatch, tmp_path) -> None:
    """projects 聚合：跨助手计数 / 最近活动排序 / 当前项目标记。"""
    from datetime import datetime, timezone

    from halter import sessions as sess
    from halter.sessions import SessionInfo

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
    from halter import sessions as sess

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


def test_tool_category_contract(fake_home, monkeypatch) -> None:
    """矩阵默认只把独立 AI Harness 当列；编辑器宿主标记为 editor。"""
    from halter import cli
    from halter.model import Detection
    from halter.registry import TOOLS

    monkeypatch.setattr(cli, "detect_tools", lambda: [
        Detection("claude", "Claude Code", True, category="harness"),
        Detection("vscode", "VS Code", True, category="editor"),
    ])

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
    from halter import sessions as sess
    from halter.sessions import SessionInfo

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


def test_providers_json_contract(fake_home: Path, monkeypatch) -> None:
    """desktop Providers 面板的数据源：list 形状 + add→switch→current 全链路。"""
    import json as _json

    (fake_home / ".claude").mkdir()
    (fake_home / ".claude/settings.json").write_text(_json.dumps({
        "env": {"ENABLE_TOOL_SEARCH": "true"}}), encoding="utf-8")

    result = runner.invoke(app, ["providers", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1                                  # 仅 official 内置
    assert payload["providers"][0] == {"id": "official", "label": "official",
                                       "tools": ["claude", "codex"], "builtin": True}
    for tool in ("claude", "codex"):
        cur = payload["current"][tool]
        assert cur["status"] == "official" and set(cur) == {
            "status", "provider", "base_url", "model", "detail"} | (
            {"env"} if tool == "claude" else set())   # claude 另带扁平托管 env（收编预填）

    monkeypatch.setenv("ZHIPU_TOKEN", "sk-from-env")
    r = runner.invoke(app, [
        "providers", "add", "zhipu", "--tool", "claude",
        "--def", json.dumps({"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                             "ANTHROPIC_MODEL": "glm-5.3[1M]"}),
        "--token-env", "ZHIPU_TOKEN"])
    assert r.exit_code == 0

    result = runner.invoke(app, ["providers", "list", "--json"])
    payload = json.loads(result.output)
    zhipu = next(p for p in payload["providers"] if p["id"] == "zhipu")
    assert zhipu["tools"] == ["claude"] and zhipu["builtin"] is False
    assert zhipu["claude"] == {"base_url": "https://open.bigmodel.cn/api/anthropic",
                               "model": "glm-5.3[1M]", "env": {}}
    assert payload["current"]["claude"]["status"] == "official"

    r = runner.invoke(app, ["providers", "switch", "zhipu", "--tool", "claude"])
    assert r.exit_code == 0
    result = runner.invoke(app, ["providers", "list", "--json"])
    payload = json.loads(result.output)
    cur = payload["current"]["claude"]
    assert cur == {"status": "halter", "provider": "zhipu",
                   "base_url": "https://open.bigmodel.cn/api/anthropic",
                   "model": "glm-5.3[1M]", "detail": "",
                   "env": {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                           "ANTHROPIC_MODEL": "glm-5.3[1M]",
                           "ENABLE_TOOL_SEARCH": "true"}}   # 非托管键全量下发
    env = json.loads((fake_home / ".claude/settings.json").read_text(encoding="utf-8"))["env"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-from-env"
    assert env["ENABLE_TOOL_SEARCH"] == "true"


def test_machines_list_json_contract(fake_home: Path) -> None:
    """desktop 机器下拉的数据源：machines 字段与 MachineSpec 一一对应。"""
    result = runner.invoke(app, ["machines", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 0 and payload["machines"] == []
    assert payload["local"]["host_name"] and payload["local"]["os"]  # 本机显示信息

    r = runner.invoke(app, ["machines", "add", "desktop", "--host", "10.0.0.2",
                            "--user", "pk", "--port", "2222", "--halter-path", "/usr/bin/halter"])
    assert r.exit_code == 0
    result = runner.invoke(app, ["machines", "list", "--json"])
    payload = json.loads(result.output)
    assert payload["count"] == 1
    m = payload["machines"][0]
    assert m == {"name": "desktop", "host": "10.0.0.2", "user": "pk",
                 "port": 2222, "halter_path": "/usr/bin/halter", "source": "manual",
                 "local": False, "host_name": None, "os": None}  # 未探测过 → null


def test_update_status_json_contract(fake_home, monkeypatch) -> None:
    """desktop 更新面板数据源：npm 渠道工具的当前/最新/状态形状与比较口径。"""
    from halter import updates as upd

    monkeypatch.setattr(upd, "npm_available", lambda: True)
    monkeypatch.setattr(upd, "tool_installed",
                        lambda names: bool(names) and names[0] in ("claude", "codex", "pi"))
    monkeypatch.setattr(upd, "cli_version",
                        lambda cli: {"claude": "2.1.292", "codex": "0.158.0",
                                     "pi": "0.83.0"}.get(cli))
    monkeypatch.setattr(upd, "npm_latest",
                        lambda pkg: {"@anthropic-ai/claude-code": "2.1.292",
                                     "@openai/codex": "0.160.1",
                                     "@earendil-works/pi-coding-agent": "0.73.1"}.get(pkg))

    result = runner.invoke(app, ["update", "status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["npm_available"] is True
    assert payload["platform"]
    by_key = {t["key"]: t for t in payload["tools"]}
    assert by_key["claude"] == {"key": "claude", "display": "Claude Code",
                                "npm_package": "@anthropic-ai/claude-code",
                                "install": ["bash -c 'tmp=$(mktemp) && curl -fsSL"
                                            " https://claude.ai/install.sh -o $tmp && bash $tmp;"
                                            " status=$?; rm -f $tmp; exit $status'",
                                            "npm install -g @anthropic-ai/claude-code@latest"],
                                "installed": True, "current": "2.1.292",
                                "latest": "2.1.292", "state": "latest"}
    assert by_key["codex"]["state"] == "upgradeable"
    # 高于 registry 最新不再判绿（pi 弃更事件教训）：单列 ahead，红色警示
    assert by_key["pi"]["state"] == "ahead"
    # 未安装的 npm 渠道 harness 也列出（灰卡），latest 拿不到时状态仍为 missing
    gemini = by_key["gemini"]
    assert gemini["installed"] is False and gemini["state"] == "missing"
    # 2026-10 批量纳入的 npm 渠道 harness
    assert {"kimi", "pi", "qwen", "iflow", "amp"} <= set(by_key)
    assert by_key["kimi"]["npm_package"] == "@moonshot-ai/kimi-code"
    assert by_key["pi"]["npm_package"] == "@earendil-works/pi-coding-agent"


def test_update_install_commands() -> None:
    """手动安装命令：有官方安装脚本的脚本与 npm 各一条（分行展示），其余仅 npm。"""
    from halter.updates import NPM_PACKAGES, install_commands

    for key in NPM_PACKAGES:
        cmds = install_commands(key)
        assert isinstance(cmds, list) and 1 <= len(cmds) <= 2, key
        assert cmds[-1] == f"npm install -g {NPM_PACKAGES[key]}@latest", key
        for cmd in cmds[:-1]:          # 除末条 npm 外均为 curl 安装脚本
            assert cmd.startswith("bash -c ") and "mktemp" in cmd and "rm -f $tmp" in cmd
    claude = install_commands("claude")
    assert claude[0] == ("bash -c 'tmp=$(mktemp) && curl -fsSL https://claude.ai/install.sh"
                         " -o $tmp && bash $tmp; status=$?; rm -f $tmp; exit $status'")
    assert claude[1] == "npm install -g @anthropic-ai/claude-code@latest"
    assert "https://opencode.ai/install" in install_commands("opencode")[0]
    assert install_commands("codex") == ["npm install -g @openai/codex@latest"]
    assert install_commands("nope") is None


def test_update_registry_covers_npm_packages() -> None:
    """守卫：NPM_PACKAGES 的每个 key 都必须是注册表里有 CLI 名的 ToolSpec。"""
    from halter.registry import BY_KEY
    from halter.updates import NPM_PACKAGES

    for key in NPM_PACKAGES:
        spec = BY_KEY.get(key)
        assert spec is not None, f"{key} 不在 registry.TOOLS"
        assert spec.cli_names, f"{key} 缺少 cli_names，版本探测无从执行"


def test_update_status_npm_missing(fake_home, monkeypatch) -> None:
    """npm 不在 PATH：latest 全空、状态 unknown，面板据此显示告警。"""
    from halter import updates as upd

    monkeypatch.setattr(upd, "npm_available", lambda: False)
    monkeypatch.setattr(upd, "tool_installed", lambda names: True)
    monkeypatch.setattr(upd, "cli_version", lambda cli: "1.2.3")
    monkeypatch.setattr(upd, "npm_latest", lambda pkg: None)

    result = runner.invoke(app, ["update", "status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["npm_available"] is False
    assert payload["tools"] and all(t["latest"] is None and t["state"] == "unknown"
                                    for t in payload["tools"])


def test_update_run_invokes_npm_install(fake_home, monkeypatch) -> None:
    """update run 转发到 updates.apply_update 并透传输出；未知工具退出码 2。"""
    from halter import updates as upd

    captured: dict = {}

    class FakeProc:
        returncode = 0
        stdout = "added 1 package in 4s"
        stderr = ""

    def fake_apply(tool):
        captured["tool"] = tool
        return FakeProc()

    monkeypatch.setattr(upd, "apply_update", fake_apply)
    result = runner.invoke(app, ["update", "run", "claude"])
    assert result.exit_code == 0
    assert captured["tool"] == "claude"
    assert "npm install -g @anthropic-ai/claude-code@latest" in result.output
    assert "claude 已升级到最新版" in result.output

    bad = runner.invoke(app, ["update", "run", "nope"])
    assert bad.exit_code == 2
