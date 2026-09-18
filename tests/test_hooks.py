"""hooks 层测试：三方言解析、adopt 收集、sync dry-run/apply 语义（全部跑在 fake $HOME）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# fixture：按真实配置的方言结构伪造三家配置

@pytest.fixture
def claude_settings(fake_home: Path) -> Path:
    d = fake_home / ".claude"
    d.mkdir(parents=True)
    p = d / "settings.json"
    p.write_text(json.dumps({
        "model": "glm-5.1",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "halter": "gh-proxy-guard", "hooks": [
                    {"type": "command", "command": '"$HOME/.local/bin/gh-proxy-guard"',
                     "timeout": 40}]},
                {"_otty": True, "hooks": [
                    {"type": "command", "command": "/bin/otty-hook.sh processing",
                     }]},
                {"matcher": "*", "hooks": [
                    {"type": "command", "command": "if [ -f x ]; then /bin/orca.sh; fi",
                     "timeout": 10}]},
            ],
            "SessionStart": [
                {"hooks": [{"type": "command",
                            "command": '"/home-u/.claude/hooks/heal.mjs"'}]},
                {"_otty": True, "hooks": [
                    {"type": "command", "command": "/bin/otty-hook.sh idle"}]},
            ],
        },
    }), encoding="utf-8")
    return p


@pytest.fixture
def zcode_config(fake_home: Path) -> Path:
    d = fake_home / ".zcode/cli"
    d.mkdir(parents=True)
    p = d / "config.json"
    p.write_text(json.dumps({
        "mcp": {"servers": {"zotero": {"type": "stdio", "command": "zotero-mcp"}}},
        "plugins": {"enabledPlugins": {"github@claude-plugins-official": True}},
        "hooks": {
            "enabled": True,
            "events": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": '"$HOME/.local/bin/gh-proxy-guard"',
                         "timeoutMs": 40000, "statusMessage": "代理保活检查"}]},
                ],
            },
        },
    }), encoding="utf-8")
    return p


@pytest.fixture
def cursor_hooks(fake_home: Path) -> Path:
    d = fake_home / ".cursor"
    d.mkdir(parents=True)
    p = d / "hooks.json"
    p.write_text(json.dumps({
        "version": 1,
        "hooks": {
            "beforeSubmitPrompt": [
                {"_otty": True, "command": "/bin/otty-hook.sh processing"},
                {"command": "/bin/sh '/home-u/.orca/agent-hooks/cursor-hook.sh'",
                 "timeout": 10},
            ],
            "beforeShellExecution": [
                {"command": "/bin/sh cursor-only.sh"},
            ],
        },
    }), encoding="utf-8")
    return p


@pytest.fixture
def all_three(claude_settings: Path, zcode_config: Path, cursor_hooks: Path) -> None:
    _ = (claude_settings, zcode_config, cursor_hooks)


# ---------------------------------------------------------------------------
# 盘点侧


def test_read_claude_hooks(fake_home: Path, claude_settings: Path) -> None:
    from harness_config_manager.hooks import read_claude

    infos: list = []
    notes: list = []
    read_claude(infos, notes)
    by_label = {i.label: i for i in infos}
    guard = by_label["gh-proxy-guard"]
    assert guard.event == "PreToolUse" and guard.matcher == "Bash"
    assert guard.command == '"$HOME/.local/bin/gh-proxy-guard"'
    assert guard.timeout == 40.0
    assert any(i.extra.get("_otty") is True for i in infos)
    # 复合 shell 命令按其内嵌脚本路径命名（orca.sh → orca），不再退化为 shell 关键字
    orca = [i for i in infos if i.matcher == "*"][0]
    assert orca.label == "orca" and orca.timeout == 10.0
    assert not notes


def test_read_zcode_hooks(zcode_config: Path) -> None:
    from harness_config_manager.hooks import read_zcode

    infos: list = []
    notes: list = []
    read_zcode(infos, notes)
    assert len(infos) == 1
    h = infos[0]
    assert h.label == "gh-proxy-guard"
    assert h.timeout == 40.0                      # timeoutMs 毫秒归一为秒
    assert h.extra.get("statusMessage") == "代理保活检查"
    assert any("enabled" in n for n in notes)


def test_read_cursor_hooks(cursor_hooks: Path) -> None:
    from harness_config_manager.hooks import read_cursor

    infos: list = []
    read_cursor(infos, [])
    events = {i.event for i in infos}
    assert "UserPromptSubmit" in events           # beforeSubmitPrompt 反查 canonical
    assert "beforeShellExecution" in events       # cursor 独有事件名原样保留
    otty = [i for i in infos if i.extra.get("_otty") is True]
    assert otty and otty[0].timeout is None
    plain = [i for i in infos if i.timeout == 10.0]
    assert plain[0].event == "UserPromptSubmit"


def test_scan_all_includes_hooks(all_three: None) -> None:
    from harness_config_manager.model import ToolReport
    from harness_config_manager.report import to_json
    from harness_config_manager.scan import scan_tool

    r_claude = scan_tool("claude")
    assert isinstance(r_claude, ToolReport)
    assert {h.label for h in r_claude.hooks} >= {"gh-proxy-guard"}
    r_cursor = scan_tool("cursor")
    assert r_cursor.hooks
    assert '"hooks"' in to_json([r_claude])


# ---------------------------------------------------------------------------
# canonical 清单与 adopt


def test_gen_hook_id_rules() -> None:
    from harness_config_manager.hooks_manifest import gen_hook_id
    from harness_config_manager.model import HookInfo

    assert gen_hook_id(HookInfo(event="PreToolUse", label="x",
                                extra={"_otty": True})) == "otty"
    assert gen_hook_id(HookInfo(
        event="PreToolUse", label="x",
        command='"/Users/u/.zcode/hooks/pytest_after_edit.py"')) == "pytest_after_edit"
    assert gen_hook_id(HookInfo(
        event="PreToolUse", label="x", command='"$HOME/.local/bin/gh-proxy-guard"'
    )) == "gh-proxy-guard"


def test_manifest_roundtrip(tmp_path: Path) -> None:
    from harness_config_manager.hooks_manifest import HookSpec, load_manifest, save_manifest

    p = tmp_path / "hooks.toml"
    specs = [HookSpec(id="a", events=["PreToolUse"], matcher="Bash", command="/bin/x.sh",
                      timeout=40.0, description="d",
                      extra={"_otty": True}, targets=["claude"])]
    save_manifest(specs, p)
    back = load_manifest(p)
    assert back == specs


def test_adopt_collects_third_party(fake_home: Path, claude_settings: Path,
                                    zcode_config: Path) -> None:
    from harness_config_manager.config import DEFAULT_CONFIG_PATH
    from harness_config_manager.hooks_manifest import adopt_hooks, load_manifest

    _ = DEFAULT_CONFIG_PATH  # 展开 manifest 路径用 fake home

    lines = adopt_hooks("claude", apply=False)
    assert any("dry-run" in l for l in lines)
    assert not load_manifest()

    lines = adopt_hooks("claude", apply=True)
    assert any(l.startswith("adopt otty") for l in lines)
    specs = {s.id: s for s in load_manifest()}
    assert "gh-proxy-guard" in specs and "otty" in specs
    assert specs["gh-proxy-guard"].events == ["PreToolUse"]
    assert specs["gh-proxy-guard"].matcher == "Bash"
    # id 去重：第二个以 otty 开头的条目得到 -2 后缀
    otty_ids = [k for k in specs if k.startswith("otty")]
    assert len(otty_ids) >= 2


# ---------------------------------------------------------------------------
# 分发侧


def _spec(id_, events, command='"$HOME/.local/bin/gh-proxy-guard"',
          matcher=None, timeout=None):
    from harness_config_manager.hooks_manifest import HookSpec
    return HookSpec(id=id_, events=events, command=command, matcher=matcher, timeout=timeout)


def test_sync_dry_run_no_write(all_three: None, fake_home: Path) -> None:
    from harness_config_manager.hooks_write import sync_hooks

    before = {p: p.read_bytes() for p in [
        fake_home / ".claude/settings.json",
        fake_home / ".zcode/cli/config.json",
        fake_home / ".cursor/hooks.json"]}
    lines = sync_hooks([_spec("my-hook", ["PreToolUse"], matcher="Bash", timeout=40)],
                       installed_tools=["claude", "zcode", "cursor"],
                       apply=False, prefer="skip")
    assert all("[plan]" in l for l in lines) and len(lines) == 3
    for p, b in before.items():
        assert p.read_bytes() == b, f"{p} 不应被 dry-run 改动"


def test_sync_apply_three_dialects(all_three: None, fake_home: Path) -> None:
    from harness_config_manager.hooks_write import sync_hooks

    sync_hooks([_spec("my-hook", ["PreToolUse", "Stop"], matcher="Bash", timeout=40)],
               installed_tools=["claude", "zcode", "cursor"], apply=True, prefer="skip")

    cl = json.loads((fake_home / ".claude/settings.json").read_text())
    entries = cl["hooks"]["PreToolUse"]
    mine = [e for e in entries if e.get("halter") == "my-hook"][0]
    assert mine["matcher"] == "Bash" and mine["hooks"][0]["timeout"] == 40
    assert cl["hooks"]["Stop"]

    zc = json.loads((fake_home / ".zcode/cli/config.json").read_text())
    ze = zc["hooks"]["events"]["PreToolUse"]
    zm = [e for e in ze if e.get("halter") == "my-hook"][0]["hooks"][0]
    assert zm["timeoutMs"] == 40000
    assert zm.get("statusMessage") is None            # 无 description 不写该键
    assert zc["hooks"]["enabled"] is True                 # 全局开关未被触碰
    assert "zotero" in zc["mcp"]["servers"]               # 其他键保留

    cu = json.loads((fake_home / ".cursor/hooks.json").read_text())
    assert cu["version"] == 1
    cm = [e for e in cu["hooks"]["preToolUse"] if e.get("halter") == "my-hook"][0]
    assert cm["timeout"] == 40 and cm["matcher"] == "Bash"
    assert cu["hooks"]["stop"]                            # Stop->stop 大小写映射


def test_sync_idempotent_and_conflict(all_three: None) -> None:
    from harness_config_manager.hooks_write import sync_hooks

    spec = _spec("my-hook", ["PreToolUse"], matcher="Bash", timeout=40)
    installed = ["claude", "zcode", "cursor"]
    sync_hooks([spec], installed, apply=True, prefer="skip")

    # 二次同步：已是期望状态，无任何输出
    assert sync_hooks([spec], installed, apply=True, prefer="skip") == []

    # 内容变更：默认 conflict 跳过；--prefer library 覆盖
    changed = _spec("my-hook", ["PreToolUse"], command='/bin/new.sh', timeout=5)
    out = sync_hooks([changed], installed, apply=False, prefer="skip")
    assert any("conflict" in l for l in out)
    out2 = sync_hooks([changed], installed, apply=True, prefer="library")
    assert any("[apply]" in l or "写入" in l for l in out2)
    assert sync_hooks([changed], installed, apply=True, prefer="skip") == []


def test_third_party_entries_untouched(all_three: None, fake_home: Path) -> None:
    from harness_config_manager.hooks_write import sync_hooks

    original = json.loads((fake_home / ".claude/settings.json").read_text())
    otty_before = [e for e in original["hooks"]["PreToolUse"] if e.get("_otty")]

    sync_hooks([_spec("mine", ["PreToolUse"])],
               installed_tools=["claude"], apply=True, prefer="skip")

    after = json.loads((fake_home / ".claude/settings.json").read_text())
    otty_after = [e for e in after["hooks"]["PreToolUse"] if e.get("_otty")]
    assert otty_after == otty_before
    # 无标记条目也不会被误伤
    untagged_before = [e for e in original["hooks"]["PreToolUse"] if "*" in (e.get("matcher") or "")]
    untagged_after = [e for e in after["hooks"]["PreToolUse"] if e.get("matcher") == "*"]
    assert untagged_after == untagged_before


def test_event_asymmetry_skips(all_three: None) -> None:
    from harness_config_manager.hooks_write import sync_hooks

    installed = ["claude", "zcode", "cursor"]
    # cursor 独有事件：claude/zcode 行出现跳过提示，仅 cursor 计划写入
    out = sync_hooks([_spec("cur-only", ["beforeShellExecution"])],
                     installed, apply=False, prefer="skip")
    plan = [l for l in out if "[plan] cursor" in l]
    skips = [l for l in out if "无此事件" in l]
    assert plan and len(skips) == 2
    # claude 独有事件：cursor 无计划
    out2 = sync_hooks([_spec("cl-only", ["PermissionRequest"])],
                      installed, apply=False, prefer="skip")
    assert not any("[plan] cursor" in l for l in out2)
    assert sum(1 for l in out2 if "无此事件" in l) == 1


def test_cli_sync_hooks_layer(fake_home: Path, monkeypatch: pytest.MonkeyPatch,
                              capsys) -> None:
    """CLI 集成：--no-hooks 关层不产生 hooks 输出；空清单走自动收集分支。"""
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    runner = CliRunner()
    # --no-hooks：不得出现 hooks 字样段落
    res = runner.invoke(app, ["sync"])
    assert res.exit_code == 0
    assert "hooks 清单为空" in res.output          # 空清单触发自动收集提示


def test_doctor_does_not_report_unexpanded_hook_paths_as_dead(fake_home: Path) -> None:
    from harness_config_manager.doctor import run_doctor

    path = fake_home / ".cursor/hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "hooks": {
        "beforeSubmitPrompt": [{"command": "$CLAUDE_PROJECT_DIR/hook.sh"}],
    }}), encoding="utf-8")
    results = run_doctor()
    assert not any("死配置" in message for _, _, message in results)
