"""Mods 层单测：mod 识别（hooks modules）/ 保留来源声明 / CLI 明细与 JSON 契约。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from halter.cli import app
from halter.mods import read_claude_mods, read_zcode_mods

runner = CliRunner()


def _install_claude_plugin(home: Path, pid: str, *, with_mod: bool,
                           version: str = "1.0.0") -> Path:
    """在 claude 侧登记一个已装插件（installed_plugins.json + 缓存目录）。"""
    name, _, marketplace = pid.partition("@")
    d = home / f".claude/plugins/cache/{marketplace}/{name}/{version}"
    if with_mod:
        (d / "hooks").mkdir(parents=True)
        (d / "hooks" / "hooks.json").write_text(
            json.dumps({"modules": ["./register.js"]}), encoding="utf-8")
    else:
        d.mkdir(parents=True)
    installed = home / ".claude/plugins/installed_plugins.json"
    installed.parent.mkdir(parents=True, exist_ok=True)
    data = (json.loads(installed.read_text()) if installed.exists()
            else {"version": 2, "plugins": {}})
    data["plugins"][pid] = [{"version": version, "installPath": str(d), "scope": "user"}]
    installed.write_text(json.dumps(data), encoding="utf-8")
    return d


def _claude_settings(home: Path, enabled: dict) -> None:
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": enabled}), encoding="utf-8")


def test_marketplace_mod_detected(fake_home: Path) -> None:
    _install_claude_plugin(fake_home, "cc-diff@claude-plugins-official", with_mod=True)
    _install_claude_plugin(fake_home, "remember@claude-plugins-official", with_mod=False)
    _claude_settings(fake_home, {"cc-diff@claude-plugins-official": True})

    mods = read_claude_mods()
    assert [m.plugin_id for m in mods] == ["cc-diff@claude-plugins-official"]
    m = mods[0]
    assert m.origin == "marketplace"
    assert m.marketplace == "claude-plugins-official"
    assert m.enabled is True
    assert m.version == "1.0.0"
    assert m.modules == ["./register.js"]


def test_classic_hook_plugin_is_not_mod(fake_home: Path) -> None:
    """classic 命令型 hooks（settings 式、无 modules）不算 mod。"""
    d = _install_claude_plugin(fake_home, "legacy@market", with_mod=True)
    (d / "hooks" / "hooks.json").write_text(
        json.dumps({"PreToolUse": [{"command": "true"}]}), encoding="utf-8")
    assert read_claude_mods() == []


def test_builtin_mod_from_declaration(fake_home: Path) -> None:
    """@builtin / @synced 保留来源不在插件缓存里，从 enabledPlugins 声明识别。"""
    _claude_settings(fake_home, {"cc-plugin-you-should-know@builtin": True,
                                 "my-tool@synced": False})
    mods = read_claude_mods()
    assert {m.plugin_id: m.origin for m in mods} == {
        "cc-plugin-you-should-know@builtin": "builtin",
        "my-tool@synced": "synced",
    }
    assert mods[0].enabled is True and mods[1].enabled is False


def test_zcode_mod_detected(fake_home: Path) -> None:
    d = fake_home / ".zcode/cli/plugins/cache/zcode-plugins-official/mod-x/0.1.0/hooks"
    d.mkdir(parents=True)
    (d / "hooks.json").write_text(json.dumps({"modules": ["./m.js"]}), encoding="utf-8")
    installed = fake_home / ".zcode/cli/plugins/installed_plugins.json"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text(json.dumps({"version": 1, "plugins": [
        {"id": "mod-x@zcode-plugins-official", "version": "0.1.0",
         "installPath": str(d.parent)},
    ]}), encoding="utf-8")
    cfg = fake_home / ".zcode/cli/config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"plugins": {
        "enabledPlugins": {"mod-x@zcode-plugins-official": True}}}), encoding="utf-8")

    mods = read_zcode_mods()
    assert [m.plugin_id for m in mods] == ["mod-x@zcode-plugins-official"]
    assert mods[0].origin == "marketplace" and mods[0].enabled is True


def test_scan_detail_and_json(fake_home: Path) -> None:
    _install_claude_plugin(fake_home, "cc-diff@claude-plugins-official", with_mod=True)
    _claude_settings(fake_home, {
        "cc-diff@claude-plugins-official": True,
        "cc-plugin-you-should-know@builtin": True,
    })

    result = runner.invoke(app, ["scan", "-d", "mods"])
    assert result.exit_code == 0
    assert "mods (" in result.output

    result = runner.invoke(app, ["scan", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    claude = next(t for t in payload["inventory"] if t["tool"] == "claude")
    by_id = {m["plugin_id"]: m for m in claude["mods"]}
    assert set(by_id) == {"cc-diff@claude-plugins-official",
                          "cc-plugin-you-should-know@builtin"}
    assert by_id["cc-diff@claude-plugins-official"]["modules"] == ["./register.js"]
    assert by_id["cc-plugin-you-should-know@builtin"]["origin"] == "builtin"
    assert by_id["cc-plugin-you-should-know@builtin"]["enabled"] is True


def test_scan_json_mods_capability_gate(fake_home: Path) -> None:
    """mods 字段只对具备 mods 机制的工具有：无键 = 该层不适用（桌面矩阵收列依据）。"""
    from halter.report import to_json
    from halter.scan import scan_tool

    payload = json.loads(to_json([scan_tool("claude"), scan_tool("codex")]))
    by_tool = {t["tool"]: t for t in payload}
    assert "mods" in by_tool["claude"]
    assert "mods" not in by_tool["codex"]


def test_mods_matrix_only_capable_columns(capsys) -> None:
    """CLI mods 矩阵只列具备 mods 机制的列（mods_supported），不具备的没有 Mod 功能。"""
    from halter.model import ModsInfo, ToolReport
    from halter.report import print_mods_matrix

    claude = ToolReport(tool="claude", display="Claude Code", installed=True,
                        mods_supported=True,
                        mods=[ModsInfo(plugin_id="cc-diff@mp", origin="marketplace")])
    # zcode 具备机制但 0 个 mod：出列且计缺口；codex 无机制：不出列
    zcode = ToolReport(tool="zcode", display="ZCode", installed=True, mods_supported=True)
    codex = ToolReport(tool="codex", display="Codex CLI", installed=True)
    print_mods_matrix([claude, zcode, codex])
    out = capsys.readouterr().out
    assert "1 × 2" in out and "gap 1" in out   # cc-diff 覆盖 claude、缺 zcode
    assert "zcode" in out                       # 具备机制的空列也出列
    assert "codex" not in out                   # 无机制的列不进矩阵
