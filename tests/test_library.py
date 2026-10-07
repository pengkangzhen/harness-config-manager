"""library 层：~/.agents 库 git 化与远端零安装接管（远端逻辑以本地 sh 真实执行模拟）。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from halter import library as lib
from halter.cli import app

runner = CliRunner()


def _local_remote(monkeypatch, remote_home: Path) -> None:
    """把 run_ssh 替换为「本地以 HOME=remote_home 执行脚本」。

    真 ssh 把单参数脚本交给远端 shell；这里用 sh -c 等价执行，
    git / tar / python3 heredoc 全链路真实跑，不 mock 远端行为。
    """

    def fake_run_ssh(machine, remote_argv, input_bytes=None, timeout=120):
        env = {**os.environ, "HOME": str(remote_home)}
        proc = subprocess.run(["sh", "-c", remote_argv[0]], input=input_bytes,
                              capture_output=True, env=env, timeout=timeout,
                              check=False)
        return proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace")

    monkeypatch.setattr(lib, "run_ssh", fake_run_ssh)


def _make_local_library(home: Path, body: str = "# sl v1\n") -> None:
    sl = home / ".agents/statusline/claude"
    sl.mkdir(parents=True)
    (sl / "statusline.py").write_text(body, encoding="utf-8")
    (home / ".agents/statusline/manifest.json").write_text(
        json.dumps({"claude": {"statusLine": {"type": "command",
                                              "command": "python3 {script}"},
                               "script": "statusline.py"}}), encoding="utf-8")
    assert runner.invoke(app, ["library", "init", "--apply"]).exit_code == 0


def _add_machine() -> None:
    assert runner.invoke(app, ["machines", "add", "mac", "--host", "10.0.0.9"]).exit_code == 0


# ---------------------------------------------------------------------------
# init


def test_library_init_idempotent(fake_home: Path):
    assert runner.invoke(app, ["library", "init"]).exit_code == 0
    assert not (fake_home / ".agents/.git").exists()          # dry-run 不落盘
    r = runner.invoke(app, ["library", "init", "--apply"])
    assert r.exit_code == 0 and (fake_home / ".agents/.git").is_dir()
    r = runner.invoke(app, ["library", "init", "--apply"])
    assert "已是 git 仓库" in r.output                          # 幂等


# ---------------------------------------------------------------------------
# adopt / push / pull 全链路


def test_adopt_push_pull_roundtrip(fake_home: Path, tmp_path: Path, monkeypatch):
    _make_local_library(fake_home)
    _add_machine()
    remote = tmp_path / "remote-home"
    remote.mkdir()
    # 远端预置已有 statusline（接管时应有整库备份，不裸丢数据）
    (remote / ".claude").mkdir()
    (remote / ".claude/settings.json").write_text(
        json.dumps({"statusLine": {"type": "command",
                                   "command": "python3 ~/.claude/statusline.py"}}),
        encoding="utf-8")
    (remote / ".claude/statusline.py").write_text("# old remote sl\n", encoding="utf-8")
    _local_remote(monkeypatch, remote)

    # adopt：建库 + 拉入 + settings 直连接管 + 旧库整包备份
    r = runner.invoke(app, ["library", "adopt", "mac", "--tool", "claude", "--apply"])
    assert r.exit_code == 0, r.output
    got = (remote / ".agents/statusline/claude/statusline.py").read_text(encoding="utf-8")
    assert got == "# sl v1\n"
    settings = json.loads((remote / ".claude/settings.json").read_text(encoding="utf-8"))
    assert settings["statusLine"]["command"] == "python3 ~/.agents/statusline/claude/statusline.py"
    assert list(remote.glob(".agents.prehalter-*.tgz")), "接管前未整包备份远端旧库"

    # push：本机脚本演进 → 远端库与工作树更新；settings 幂等（不再重复备份）
    (fake_home / ".agents/statusline/claude/statusline.py").write_text("# sl v2\n", encoding="utf-8")
    r = runner.invoke(app, ["library", "push", "--to", "mac", "--tool", "claude", "--apply"])
    assert r.exit_code == 0, r.output
    assert (remote / ".agents/statusline/claude/statusline.py").read_text(encoding="utf-8") == "# sl v2\n"
    assert len(list(remote.glob(".claude/settings.json.prehalter-*"))) == 1  # 只在首接管备份过一次

    # pull：远端修改记忆文件 → 拉回本机
    (remote / ".agents").mkdir(exist_ok=True)
    (remote / ".agents/NOTES.md").write_text("from remote\n", encoding="utf-8")
    r = runner.invoke(app, ["library", "pull", "--from", "mac", "--apply"])
    assert r.exit_code == 0, r.output
    assert (fake_home / ".agents/NOTES.md").read_text(encoding="utf-8") == "from remote\n"


def test_push_reports_divergence(fake_home: Path, tmp_path: Path, monkeypatch):
    _make_local_library(fake_home)
    _add_machine()
    remote = tmp_path / "remote-home"
    remote.mkdir()
    _local_remote(monkeypatch, remote)
    assert runner.invoke(app, ["library", "adopt", "mac", "--apply"]).exit_code == 0

    # 两边各自演进 → push 被 ff-only 拒绝并提示先 pull
    (fake_home / ".agents/statusline/claude/statusline.py").write_text("# local\n", encoding="utf-8")
    assert lib.library_commit("local tweak") is not None
    env = {**os.environ, "HOME": str(remote)}
    (remote / ".agents/statusline/claude/statusline.py").write_text("# remote\n", encoding="utf-8")
    subprocess.run(["sh", "-c", 'cd ~/.agents && git add -A && git commit -qm remote'],
                   env=env, check=True)
    r = runner.invoke(app, ["library", "push", "--to", "mac", "--apply"])
    assert r.exit_code != 0 and "pull" in r.output


def test_adopt_rejects_declarative_tools(fake_home: Path):
    _make_local_library(fake_home)
    _add_machine()
    r = runner.invoke(app, ["library", "adopt", "mac", "--tool", "codex", "--apply"])
    assert r.exit_code != 0 and "不支持无 halter 接管" in r.output


def test_adopt_requires_initialized_library(fake_home: Path):
    _add_machine()
    r = runner.invoke(app, ["library", "adopt", "mac", "--apply"])
    assert r.exit_code != 0 and "library init" in r.output
