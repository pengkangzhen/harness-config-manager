"""跨机器条目传输：encode/apply 往返（双 fake home 模拟两台机器）与 push/pull 编排。"""

from __future__ import annotations

import json
from pathlib import Path

import tarfile
from typer.testing import CliRunner

from halter import transfer
from halter.cli import app
from halter import machines as mach

from conftest import make_agent, make_skill

runner = CliRunner()


def _switch_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: home)


def _second_home(tmp_path: Path, monkeypatch, current: Path) -> Path:
    """造第二台"机器"的 home 并切换过去（返回新 home）。"""
    other = tmp_path / "homeB"
    other.mkdir()
    _switch_home(monkeypatch, other)
    return other


# ---------------------------------------------------------------------------
# skills：tar 往返 + 幂等 + 冲突 + 分发


def test_skill_roundtrip_idempotent_and_conflict(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    make_skill(fake_home / ".agents/skills", "foo", "# v1\n")
    payload, _ = transfer.encode_entry("skills", "foo")

    home_b = _second_home(tmp_path, monkeypatch, fake_home)
    lines = transfer.apply_entry("skills", "foo", payload, apply=True)
    assert any(l.startswith("add") for l in lines)
    assert (home_b / ".agents/skills/foo/SKILL.md").read_text(encoding="utf-8") == "# v1\n"

    # 幂等：同内容再传 -> ok
    assert transfer.apply_entry("skills", "foo", payload, apply=True)[0].startswith("ok")

    # 冲突：目标侧内容漂移 -> 默认跳过；prefer=replace 备份后覆盖
    (home_b / ".agents/skills/foo/SKILL.md").write_text("# drifted\n", encoding="utf-8")
    assert transfer.apply_entry("skills", "foo", payload, apply=True)[0].startswith("conflict")
    lines = transfer.apply_entry("skills", "foo", payload, prefer="replace", apply=True)
    assert lines[0].startswith("replace")
    assert (home_b / ".agents/skills/foo/SKILL.md").read_text(encoding="utf-8") == "# v1\n"
    backups = list((home_b / ".config/halter/backups").rglob("SKILL.md"))
    assert backups and backups[0].read_text(encoding="utf-8") == "# drifted\n"


def test_skill_distribute_after_ingest(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    make_skill(fake_home / ".agents/skills", "foo")
    payload, _ = transfer.encode_entry("skills", "foo")

    home_b = _second_home(tmp_path, monkeypatch, fake_home)
    (home_b / ".codex/skills").mkdir(parents=True)
    transfer.apply_entry("skills", "foo", payload, apply=True)
    lines = transfer.distribute_item("skills", "foo", apply=True)
    link = home_b / ".codex/skills/foo"
    assert link.is_symlink() and link.resolve() == (home_b / ".agents/skills/foo").resolve()
    assert any(l.startswith("link") for l in lines)


def test_transfer_payload_is_tar_with_entry_name(fake_home: Path) -> None:
    make_skill(fake_home / ".agents/skills", "foo")
    payload, _ = transfer.encode_entry("skills", "foo")
    with tarfile.open(fileobj=__import__("io").BytesIO(payload), mode="r") as tf:
        assert tf.getnames() == ["foo", "foo/SKILL.md"]


# ---------------------------------------------------------------------------
# agents：单文件往返


def test_agent_roundtrip(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    make_agent(fake_home / ".agents/agents", "reviewer", model="bigmodel/glm-5.3")
    payload, _ = transfer.encode_entry("agents", "reviewer")

    home_b = _second_home(tmp_path, monkeypatch, fake_home)
    transfer.apply_entry("agents", "reviewer", payload, apply=True)
    got = home_b / ".agents/agents/reviewer.md"
    assert got.is_file() and "model: bigmodel/glm-5.3" in got.read_text(encoding="utf-8")
    assert transfer.apply_entry("agents", "reviewer", payload, apply=True)[0].startswith("ok")


# ---------------------------------------------------------------------------
# mcp：TOML 往返 + --with-secrets 合并 + 冲突


def test_mcp_roundtrip_merges_manifest_and_secrets(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    from halter.mcp_manifest import (McpSpec, _load_secrets,
                                                     load_manifest, save_manifest, save_secrets)

    save_manifest([
        McpSpec(name="zotero", command="uvx", args=["zotero-mcp"],
                env={"ZOTERO_KEY": "${ZOTERO_KEY}"}),
        McpSpec(name="unrelated", command="uvx"),
    ])
    save_secrets({"ZOTERO_KEY": "s3cret-value", "OTHER": "stay"})
    payload, _ = transfer.encode_entry("mcp", "zotero", with_secrets=True)
    assert b"s3cret-value" in payload          # 显式 --with-secrets 才携带
    assert transfer.encode_entry("mcp", "zotero")[0].count(b"s3cret") == 0

    home_b = _second_home(tmp_path, monkeypatch, fake_home)
    save_manifest([McpSpec(name="other", command="npx")])   # 目标机已有别的 server
    lines = transfer.apply_entry("mcp", "zotero", payload, with_secrets=True, apply=True)
    assert any("add" in l for l in lines) and any(l.startswith("secrets") for l in lines)

    names = [s.name for s in load_manifest()]
    assert names == ["other", "zotero"]        # 合并，不覆盖目标机其余条目
    got = next(s for s in load_manifest() if s.name == "zotero")
    assert got.env == {"ZOTERO_KEY": "${ZOTERO_KEY}"}   # 占位符原样，真值在 secrets
    secrets = _load_secrets()
    assert secrets["ZOTERO_KEY"] == "s3cret-value"

    # 幂等；冲突默认跳过
    assert transfer.apply_entry("mcp", "zotero", payload, with_secrets=True, apply=True)[0].startswith("ok")
    save_manifest([McpSpec(name="zotero", command="different")])
    assert transfer.apply_entry("mcp", "zotero", payload)[0].startswith("conflict")


# ---------------------------------------------------------------------------
# hooks：基名匹配多条 + 本机路径警告


def test_hooks_base_name_matches_family(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    from halter.hooks_manifest import HookSpec, load_manifest, save_manifest

    save_manifest([
        HookSpec(id="gate", events=["PreToolUse"], command="python3 /home/me/gate.py"),
        HookSpec(id="gate-2", events=["PostToolUse"], command="echo done"),
        HookSpec(id="unrelated", events=["Stop"], command="echo stop"),
    ])
    payload, warnings = transfer.encode_entry("hooks", "gate")
    assert any("本机路径" in w for w in warnings)
    assert payload.count(b"[[hook]]") == 2     # 基名 family 整体迁移，unrelated 不带

    home_b = _second_home(tmp_path, monkeypatch, fake_home)
    transfer.apply_entry("hooks", "gate", payload, apply=True)
    ids = [s.id for s in load_manifest()]
    assert ids == ["gate", "gate-2"]


# ---------------------------------------------------------------------------
# CLI：push --to / scan --machine / ingest / 校验


def _fake_remote(monkeypatch, rc=0, out=b"  [apply] add    foo\n", err=""):
    captured: dict = {}

    def fake_run(machine, args, input_bytes=None, timeout=120):
        captured.update(machine=machine, args=args, input=input_bytes, timeout=timeout)
        return rc, out, err

    monkeypatch.setattr(mach, "run_remote", fake_run)
    return captured


def test_cli_push_to_machine(fake_home: Path, monkeypatch) -> None:
    runner.invoke(app, ["machines", "add", "desktop", "--host", "10.0.0.2"])
    make_skill(fake_home / ".agents/skills", "foo")
    captured = _fake_remote(monkeypatch)

    r = runner.invoke(app, ["push", "skills", "foo", "--to", "desktop", "--apply"])
    assert r.exit_code == 0
    assert captured["machine"].name == "desktop"
    assert captured["args"] == ["machines", "ingest", "skills",
                                "foo", "--prefer", "skip", "--apply"]
    assert captured["input"] is not None and captured["input"][:1] != b"\n"


def test_cli_push_dry_run_has_no_apply_flag(fake_home: Path, monkeypatch) -> None:
    runner.invoke(app, ["machines", "add", "d", "--host", "h"])
    make_skill(fake_home / ".agents/skills", "foo")
    captured = _fake_remote(monkeypatch)
    r = runner.invoke(app, ["push", "skills", "foo", "--to", "d"])
    assert r.exit_code == 0 and "--apply" not in captured["args"]


def test_cli_push_from_machine_pulls_bytes(fake_home: Path, monkeypatch) -> None:
    runner.invoke(app, ["machines", "add", "d", "--host", "h"])
    make_skill(fake_home / ".agents/skills", "foo")
    payload, _ = transfer.encode_entry("skills", "foo")

    def fake_export(machine, args, input_bytes=None, timeout=120):
        assert args == ["machines", "export", "skills", "foo"]
        return 0, payload, ""

    import io

    def fake_ingest(machine, args, input_bytes=None, timeout=120):
        assert args[0] == "ingest" or args[0] == "machines"
        assert input_bytes == payload
        return 0, b"  [apply] add    foo\n", ""

    # pull：远端 export -> 本机 apply（不经 ssh ingest）
    monkeypatch.setattr(mach, "run_remote", fake_export)
    (fake_home / ".agents/skills/foo").exists() and (fake_home / ".agents/skills/foo/SKILL.md").write_text(
        "# overwritten\n", encoding="utf-8")
    r = runner.invoke(app, ["pull", "skills", "foo", "--from", "d", "--prefer", "replace", "--apply"])
    assert r.exit_code == 0
    assert (fake_home / ".agents/skills/foo/SKILL.md").read_text(encoding="utf-8") == "# skill\n"


def test_cli_push_validates(fake_home: Path) -> None:
    assert runner.invoke(app, ["push", "bogus", "x", "--to", "d"]).exit_code == 2
    assert runner.invoke(app, ["push", "skills", "x"]).exit_code == 2          # 缺 --from/--to
    r = runner.invoke(app, ["push", "skills", "x", "--to", "ghost"])           # 未注册机器
    assert r.exit_code == 2 and "不存在" in r.output
    runner.invoke(app, ["machines", "add", "d", "--host", "h"])
    r = runner.invoke(app, ["push", "skills", "missing", "--to", "d"])
    assert r.exit_code == 1 and "没有" in r.output                             # 源条目不存在


def test_cli_scan_machine_forwards_json(fake_home: Path, monkeypatch) -> None:
    runner.invoke(app, ["machines", "add", "d", "--host", "h"])
    captured = _fake_remote(monkeypatch, out=json.dumps({"tools_detected": [], "inventory": []}).encode())
    r = runner.invoke(app, ["scan", "--machine", "d", "--json"])
    assert r.exit_code == 0 and "tools_detected" in r.output
    assert captured["args"] == ["scan", "--json"]


def test_cli_sync_machine_forwards_flags(fake_home: Path, monkeypatch) -> None:
    runner.invoke(app, ["machines", "add", "d", "--host", "h"])
    captured = _fake_remote(monkeypatch)
    r = runner.invoke(app, ["sync", "--no-mcp", "--no-plugins", "--no-hooks",
                            "--no-agents", "--no-memory", "--no-sessions",
                            "--machine", "d", "--tool", "claude", "--item", "x", "--apply"])
    assert r.exit_code == 0
    assert captured["args"] == ["sync", "--skills", "--no-mcp", "--no-plugins",
                                "--no-hooks", "--no-agents", "--no-memory",
                                "--statusline", "--no-sessions", "--apply",
                                "--tool", "claude", "--item", "x"]


def test_cli_sessions_memory_providers_machine_forwards(fake_home: Path, monkeypatch) -> None:
    """机器优先导航的 CLI 通道：memory / sessions / providers 子命令 --machine 转发远端。"""
    runner.invoke(app, ["machines", "add", "d", "--host", "h"])
    calls: list[tuple[list[str], bytes | None]] = []

    def fake_run(machine, args, input_bytes=None, timeout=120):
        calls.append((args, input_bytes))
        return 0, json.dumps({"ok": True}).encode(), ""

    monkeypatch.setattr(mach, "run_remote", fake_run)

    r = runner.invoke(app, ["sessions", "list", "--machine", "d", "--all-projects", "--json"])
    assert r.exit_code == 0
    assert calls[-1][0] == ["sessions", "list", "--limit", "30", "--all-projects", "--json"]

    r = runner.invoke(app, ["sessions", "projects", "--machine", "d",
                            "--project", "/remote/p", "--json"])
    assert r.exit_code == 0
    assert calls[-1][0] == ["sessions", "projects", "--project", "/remote/p", "--json"]

    r = runner.invoke(app, ["sessions", "search", "--machine", "d", "--all-projects",
                            "--json", "--", "needle"])
    assert r.exit_code == 0
    assert calls[-1][0] == ["sessions", "search", "--limit", "20",
                            "--all-projects", "--json", "--", "needle"]

    r = runner.invoke(app, ["sessions", "show", "--machine", "d", "--project", "/r/p",
                            "--transcript", "--tail", "50", "--json", "--", "claude:abc"])
    assert r.exit_code == 0
    assert calls[-1][0] == ["sessions", "show", "--project", "/r/p", "--tail", "50",
                            "--transcript", "--json", "--", "claude:abc"]

    r = runner.invoke(app, ["sessions", "context", "--machine", "d", "--project", "/r/p",
                            "--tail", "20", "--", "claude:abc"])
    assert r.exit_code == 0
    assert calls[-1][0] == ["sessions", "context", "--project", "/r/p",
                            "--tail", "20", "--", "claude:abc"]

    r = runner.invoke(app, ["memory", "show", "--machine", "d", "--json"])
    assert r.exit_code == 0
    assert calls[-1][0] == ["memory", "show", "--json"]

    r = runner.invoke(app, ["providers", "list", "--machine", "d", "--json"])
    assert r.exit_code == 0
    assert calls[-1][0] == ["providers", "list", "--json"]

    r = runner.invoke(app, ["providers", "switch", "zhipu", "--machine", "d", "--tool", "claude"])
    assert r.exit_code == 0
    assert calls[-1][0] == ["providers", "switch", "zhipu", "--tool", "claude"]

    r = runner.invoke(app, ["providers", "remove", "zhipu", "--machine", "d", "--apply"])
    assert r.exit_code == 0
    assert calls[-1][0] == ["providers", "remove", "zhipu", "--apply"]


def test_cli_providers_add_machine_forwards_token_via_stdin(fake_home: Path, monkeypatch) -> None:
    """""--machine 时 token 先读本机 stdin，再经 ssh stdin 到远端，全程不进 argv。"""
    runner.invoke(app, ["machines", "add", "d", "--host", "h"])
    captured = _fake_remote(monkeypatch)

    r = runner.invoke(app, ["providers", "add", "zhipu", "--machine", "d", "--tool", "claude",
                            "--def", '{"ANTHROPIC_BASE_URL": "https://x.example"}', "--token-stdin"],
                      input="sk-secret\n")
    assert r.exit_code == 0
    assert captured["args"] == ["providers", "add", "zhipu", "--tool", "claude",
                                "--def", '{"ANTHROPIC_BASE_URL": "https://x.example"}', "--token-stdin"]
    assert captured["input"] == b"sk-secret\n"


def test_cli_ingest_reads_stdin_toml(fake_home: Path) -> None:
    payload = ('[[server]]\nname = "zotero"\ntransport = "stdio"\n'
               'command = "uvx"\nargs = ["zotero-mcp"]\n').encode("utf-8")
    r = runner.invoke(app, ["machines", "ingest", "mcp", "zotero", "--apply"], input=payload)
    assert r.exit_code == 0 and "add" in r.output
    from halter.mcp_manifest import load_manifest
    assert [s.name for s in load_manifest()] == ["zotero"]


def test_cli_ingest_rejects_name_mismatch(fake_home: Path) -> None:
    payload = b'[[server]]\nname = "other"\ncommand = "uvx"\n'
    r = runner.invoke(app, ["machines", "ingest", "mcp", "zotero"], input=payload)
    assert r.exit_code == 1 and "不符" in r.output


# ---------------------------------------------------------------------------
# statusline：片段 + 脚本跨机往返、幂等、冲突与分发


def _make_statusline_library(home: Path, body: str = "# sl v1\n") -> None:
    lib = home / ".agents/statusline"
    (lib / "claude").mkdir(parents=True)
    (lib / "claude/statusline.py").write_text(body, encoding="utf-8")
    (lib / "manifest.json").write_text(json.dumps({
        "claude": {"statusLine": {"type": "command",
                                  "command": "python3 {script}", "padding": 0},
                   "script": "statusline.py"}}), encoding="utf-8")


def test_statusline_roundtrip_idempotent_and_conflict(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    _make_statusline_library(fake_home)
    payload, warnings = transfer.encode_entry("statusline", "claude")
    assert warnings == []  # {script} 占位 -> 无本机路径警告

    home_b = _second_home(tmp_path, monkeypatch, fake_home)
    # 预置另一工具片段：apply 不得动它
    (home_b / ".agents/statusline").mkdir(parents=True)
    (home_b / ".agents/statusline/manifest.json").write_text(
        json.dumps({"codex": {"tui": {"status_line": ["model"]}}}), encoding="utf-8")

    lines = transfer.apply_entry("statusline", "claude", payload, apply=True)
    assert any(l.startswith("add") for l in lines)
    lib_b = home_b / ".agents/statusline"
    assert (lib_b / "claude/statusline.py").read_text(encoding="utf-8") == "# sl v1\n"
    manifest = json.loads((lib_b / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["claude"]["statusLine"]["command"] == "python3 {script}"
    assert manifest["codex"] == {"tui": {"status_line": ["model"]}}  # 邻居片段保留

    # 幂等：同内容再传 -> ok
    assert transfer.apply_entry("statusline", "claude", payload, apply=True)[0].startswith("ok")

    # 冲突：目标侧片段漂移 -> 默认跳过；prefer=replace 备份后覆盖
    manifest["claude"]["statusLine"]["padding"] = 9
    (lib_b / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert transfer.apply_entry("statusline", "claude", payload, apply=True)[0].startswith("conflict")
    lines = transfer.apply_entry("statusline", "claude", payload, prefer="replace", apply=True)
    assert lines[0].startswith("replace")
    fixed = json.loads((lib_b / "manifest.json").read_text(encoding="utf-8"))
    assert fixed["claude"]["statusLine"]["padding"] == 0
    backups = list((home_b / ".config/halter/backups").rglob("*/statusline/manifest.json"))
    assert backups and json.loads(backups[0].read_text(encoding="utf-8"))["claude"]["statusLine"]["padding"] == 9


def test_statusline_encode_missing_and_local_path_warning(fake_home: Path) -> None:
    with __import__("pytest").raises(LookupError):
        transfer.encode_entry("statusline", "claude")   # 无库
    _make_statusline_library(fake_home)
    manifest_path = fake_home / ".agents/statusline/manifest.json"
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc["claude"]["statusLine"]["command"] = f"python3 {fake_home}/.claude/statusline.py"
    manifest_path.write_text(json.dumps(doc), encoding="utf-8")
    _, warnings = transfer.encode_entry("statusline", "claude")
    assert any("本机路径" in w for w in warnings)


def test_statusline_distribute_after_ingest(fake_home: Path, tmp_path: Path, monkeypatch) -> None:
    _make_statusline_library(fake_home)
    payload, _ = transfer.encode_entry("statusline", "claude")

    home_b = _second_home(tmp_path, monkeypatch, fake_home)
    (home_b / ".claude").mkdir()
    (home_b / ".claude/settings.json").write_text('{"model": "keep"}', encoding="utf-8")
    transfer.apply_entry("statusline", "claude", payload, apply=True)
    lines = transfer.distribute_item("statusline", "claude", apply=True)
    link = home_b / ".claude/statusline.py"
    assert link.is_symlink() and link.resolve() == (home_b / ".agents/statusline/claude/statusline.py").resolve()
    data = json.loads((home_b / ".claude/settings.json").read_text(encoding="utf-8"))
    assert data["model"] == "keep"
    assert data["statusLine"]["command"] == f"python3 {link}"
    assert any(l.startswith("link") for l in lines)


def test_cli_push_statusline_to_machine(fake_home: Path, monkeypatch) -> None:
    _make_statusline_library(fake_home)
    runner.invoke(app, ["machines", "add", "desktop", "--host", "10.0.0.2"])
    captured = _fake_remote(monkeypatch)
    r = runner.invoke(app, ["push", "statusline", "claude", "--to", "desktop", "--apply"])
    assert r.exit_code == 0
    assert captured["args"] == ["machines", "ingest", "statusline", "claude",
                                "--prefer", "skip", "--apply"]
    assert captured["input"] == transfer.encode_entry("statusline", "claude")[0]


def test_cli_push_statusline_rejected_when_library_lacks_tool(fake_home: Path, monkeypatch) -> None:
    runner.invoke(app, ["machines", "add", "desktop", "--host", "10.0.0.2"])
    captured = _fake_remote(monkeypatch)
    r = runner.invoke(app, ["push", "statusline", "zcode", "--to", "desktop"])
    assert r.exit_code == 1
    assert "没有 zcode 的片段" in r.output
    assert "args" not in captured          # 源侧导出失败即止，未触达远端
