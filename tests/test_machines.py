"""机器注册表与 ssh 执行器：machines.toml 读写、命令拼装、CLI 增删查。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from halter.cli import app
from halter import machines as mach

runner = CliRunner()


# ---------------------------------------------------------------------------
# machines.toml 读写


def test_load_missing_manifest_returns_empty(fake_home: Path) -> None:
    assert mach.load_machines() == []


def test_save_and_load_roundtrip(fake_home: Path) -> None:
    mach.save_machines([
        mach.MachineSpec(name="desktop", host="192.168.1.10", user="pk", port=2222,
                         halter_path="/home/pk/.local/bin/halter"),
        mach.MachineSpec(name="lab", host="lab.example.org"),
    ])
    got = mach.load_machines()
    assert [(m.name, m.host, m.user, m.port, m.halter_path) for m in got] == [
        ("desktop", "192.168.1.10", "pk", 2222, "/home/pk/.local/bin/halter"),
        ("lab", "lab.example.org", None, 22, "halter"),
    ]
    # 默认值不落盘，文件保持精简（lab 段无 port/user/halter_path）
    text = (fake_home / ".config/halter/machines.toml").read_text(encoding="utf-8")
    lab_section = text.split("[[machine]]")[2]
    assert "port" not in lab_section and "halter_path" not in lab_section
    assert mach.get_machine("desktop").destination == "pk@192.168.1.10"
    assert mach.get_machine("lab").destination == "lab.example.org"
    assert mach.get_machine("nope") is None


# ---------------------------------------------------------------------------
# run_ssh：argv 拼装与本地失败路径


class FakeProc:
    def __init__(self, rc: int = 0, out: bytes = b"", err: bytes = b"") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def test_run_remote_builds_ssh_argv(fake_home: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return FakeProc(0, b'{"version": "1.0"}')

    monkeypatch.setattr(mach.subprocess, "run", fake_run)
    m = mach.MachineSpec(name="d", host="h", user="u", port=2222,
                         halter_path="/opt/halter")
    rc, out, err = mach.run_remote(m, ["scan", "--json"], input_bytes=b"x")
    assert rc == 0 and out == b'{"version": "1.0"}' and err == ""
    cmd = captured["cmd"]
    assert "ConnectTimeout=10" in cmd and "BatchMode=yes" in cmd  # 无凭据立即失败，绝不挂起
    assert cmd[cmd.index("BatchMode=yes") + 1:cmd.index("BatchMode=yes") + 3] == ["-p", "2222"]
    assert cmd[-4:] == ["u@h", "/opt/halter", "scan", "--json"]
    assert captured["kw"]["input"] == b"x"   # 二进制 stdin（tar 流）


def test_run_ssh_local_failures(fake_home: Path, monkeypatch) -> None:
    m = mach.MachineSpec(name="d", host="h")
    monkeypatch.setattr(mach.shutil, "which", lambda _n: None)
    rc, out, err = mach.run_remote(m, ["version"])
    assert rc is None and out == b"" and "ssh" in err

    monkeypatch.setattr(mach.shutil, "which", lambda _n: "/usr/bin/ssh")

    def raise_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(mach.subprocess, "run", raise_timeout)
    rc, _, err = mach.run_remote(m, ["version"], timeout=1)
    assert rc is None and "超时" in err


def test_probe_remote_halter_diagnoses_missing_path(fake_home: Path, monkeypatch) -> None:
    m = mach.MachineSpec(name="d", host="h")

    def fake_run(cmd, **kw):
        remote = cmd[cmd.index(m.destination) + 1:]
        if remote[:1] == ["halter"]:
            return FakeProc(127, b"", b"halter: command not found")
        if remote[:2] == ["sh", "-lc"]:
            return FakeProc(0, b"/home/u/.local/bin/halter\n")
        return FakeProc(1, b"", b"")

    monkeypatch.setattr(mach.subprocess, "run", fake_run)
    ok, detail = mach.probe_remote_halter(m)
    assert not ok
    assert "/home/u/.local/bin/halter" in detail and "--halter-path" in detail

    def fake_ok(cmd, **kw):
        remote = cmd[cmd.index(m.destination) + 1:]
        if remote[:1] == ["halter"]:
            return FakeProc(0, b'{"name": "halter", "version": "1.2.3"}')
        return FakeProc(1, b"", b"")

    monkeypatch.setattr(mach.subprocess, "run", fake_ok)
    ok, detail = mach.probe_remote_halter(m)
    assert ok and "1.2.3" in detail


# ---------------------------------------------------------------------------
# CLI：add / list / remove / test


def test_cli_add_list_remove(fake_home: Path) -> None:
    assert runner.invoke(app, ["machines", "list", "--json"]).exit_code == 0

    r = runner.invoke(app, ["machines", "add", "desktop", "--host", "10.0.0.2",
                            "--user", "pk", "--port", "2222"])
    assert r.exit_code == 0
    # 重名拒绝
    r = runner.invoke(app, ["machines", "add", "desktop", "--host", "10.0.0.3"])
    assert r.exit_code == 2 and "已存在" in r.output

    r = runner.invoke(app, ["machines", "add", "lab", "--host", "lab.example.org"])
    assert r.exit_code == 0

    r = runner.invoke(app, ["machines", "list", "--json"])
    assert r.exit_code == 0
    import json
    data = json.loads(r.output)
    assert data["count"] == 2
    by_name = {m["name"]: m for m in data["machines"]}
    assert by_name["desktop"]["port"] == 2222
    assert by_name["lab"]["halter_path"] == "halter"

    r = runner.invoke(app, ["machines", "remove", "lab"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["machines", "remove", "lab"])
    assert r.exit_code == 2 and "不存在" in r.output
    assert [m.name for m in mach.load_machines()] == ["desktop"]


def test_cli_add_rejects_bad_name(fake_home: Path) -> None:
    r = runner.invoke(app, ["machines", "add", "my machine", "--host", "h"])
    assert r.exit_code == 2


def test_machines_local_flag(fake_home: Path) -> None:
    # 回环条目 = 本机：is_local 判定 + json 契约带 local 字段（下拉框据此折叠进「本机」）
    assert mach.MachineSpec(name="a", host="127.0.0.1").is_local
    assert mach.MachineSpec(name="b", host="LOCALHOST").is_local
    assert mach.MachineSpec(name="c", host="::1").is_local
    assert not mach.MachineSpec(name="d", host="10.0.0.2").is_local

    mach.save_machines([
        mach.MachineSpec(name="desktop-wsl", host="127.0.0.1", user="pk", port=2222),
        mach.MachineSpec(name="mac", host="100.122.3.64"),
    ])
    r = runner.invoke(app, ["machines", "list", "--json"])
    import json
    data = json.loads(r.output)
    by_name = {m["name"]: m for m in data["machines"]}
    assert by_name["desktop-wsl"]["local"] is True
    assert by_name["mac"]["local"] is False
    # 表格输出对回环条目标注「即本机」
    r = runner.invoke(app, ["machines", "list"])
    assert "即本机" in r.output


def test_probe_and_info_cache(fake_home: Path, monkeypatch) -> None:
    # ssh 探测远端主机名/环境 → 缓存 → list --json 合并显示；本机信息随 list 输出
    m = mach.MachineSpec(name="mac", host="100.122.3.64", user="pk")
    monkeypatch.setattr(
        mach, "run_ssh",
        lambda mc, cmd, **kw: (0, b"MacBook-Air-3.local\nDarwin\n24.0.0\n", b""))
    assert mach.probe_remote_info(m) == {"host_name": "MacBook-Air-3.local", "os": "macos"}
    # WSL 内核标识 → wsl
    monkeypatch.setattr(
        mach, "run_ssh",
        lambda mc, cmd, **kw: (0, b"DESKTOP-ABC\nLinux\n6.6-microsoft-standard-WSL2\n", b""))
    assert mach.probe_remote_info(m)["os"] == "wsl"
    # 探测失败不抛错
    monkeypatch.setattr(mach, "run_ssh", lambda mc, cmd, **kw: (255, b"", b""))
    assert mach.probe_remote_info(m) is None

    mach.save_info_cache("mac", {"host_name": "MacBook-Air-3.local", "os": "macos"})
    assert mach.load_info_cache()["mac"] == {"host_name": "MacBook-Air-3.local", "os": "macos"}
    mach.save_machines([m])
    import json
    data = json.loads(runner.invoke(app, ["machines", "list", "--json"]).output)
    assert data["machines"][0]["host_name"] == "MacBook-Air-3.local"
    assert data["machines"][0]["os"] == "macos"
    assert data["local"]["host_name"] and data["local"]["os"] in ("wsl", "linux", "macos", "windows")


def test_cli_test_json_caches_host_info(fake_home: Path, monkeypatch) -> None:
    # machines test --json：成功时探测远端主机名并写入缓存（UI 下拉显示名的来源）
    mach.save_machines([mach.MachineSpec(name="mac", host="100.122.3.64")])
    monkeypatch.setattr(mach, "probe_remote_halter",
                        lambda m: (True, '{"version": "0.1.0"}'))
    monkeypatch.setattr(mach, "run_ssh",
                        lambda mc, cmd, **kw: (0, b"MacBook-Air-3.local\nDarwin\n24.0.0\n", b""))
    r = runner.invoke(app, ["machines", "test", "mac", "--json"])
    assert r.exit_code == 0
    import json
    payload = json.loads(r.output)
    assert payload == {"ok": True, "version": "0.1.0",
                       "host_name": "MacBook-Air-3.local", "os": "macos"}
    assert mach.load_info_cache()["mac"]["host_name"] == "MacBook-Air-3.local"


def test_cli_test_reports_unreachable(fake_home: Path, monkeypatch) -> None:
    runner.invoke(app, ["machines", "add", "d", "--host", "h"])
    monkeypatch.setattr(mach, "run_remote", lambda m, a, **kw: (255, b"", "Permission denied"))
    monkeypatch.setattr(mach, "run_ssh", lambda m, a, **kw: (1, b"", ""))
    r = runner.invoke(app, ["machines", "test", "d"])
    assert r.exit_code == 1 and "不通" in r.output


# ---------------------------------------------------------------------------
# ~/.ssh/config 自动发现（VS Code Remote-SSH 式零注册）


def write_ssh_config(home: Path, text: str) -> None:
    d = home / ".ssh"
    d.mkdir(exist_ok=True)
    (d / "config").write_text(text, encoding="utf-8")


def test_discover_skips_wildcards_and_reads_fields(fake_home: Path) -> None:
    write_ssh_config(fake_home, """\
# 注释行
Host desktop lab
  HostName 10.0.0.2
  User pk
  Port 2222

Host *
  Compression yes

Host git-*
  User git
""")
    hosts = {m.name: m for m in mach.discover_ssh_hosts()}
    assert set(hosts) == {"desktop", "lab"}      # 通配与模式别名不进列表
    assert hosts["desktop"].source == "ssh"
    assert hosts["desktop"].user == "pk" and hosts["desktop"].port == 2222
    assert hosts["lab"].port == 2222          # 同一 Host 块的别名共享块内配置（ssh 语义）
    assert hosts["desktop"].destination == "pk@desktop"


def test_discover_include_and_first_match(fake_home: Path) -> None:
    (fake_home / ".ssh/conf.d").mkdir(parents=True)
    (fake_home / ".ssh/conf.d/work.conf").write_text(
        "Host office\n  HostName 10.1.1.1\n", encoding="utf-8")
    write_ssh_config(fake_home, """\
Include conf.d/*.conf

Host desktop
  User first

Host desktop
  User second
""")
    hosts = {m.name: m for m in mach.discover_ssh_hosts()}
    assert set(hosts) == {"desktop", "office"}   # Include 递归生效
    assert hosts["desktop"].user == "first"      # ssh 语义：first-match


def test_list_machines_manual_wins(fake_home: Path) -> None:
    write_ssh_config(fake_home, "Host desktop\n  HostName 9.9.9.9\nHost lab\n")
    mach.save_machines([mach.MachineSpec(name="desktop", host="manual-host")])
    got = mach.list_machines()
    assert [(m.name, m.source) for m in got] == [("desktop", "manual"), ("lab", "ssh")]
    desktop = mach.get_machine("desktop")
    assert desktop is not None and desktop.host == "manual-host"
    assert mach.get_machine("lab") is not None and mach.get_machine("lab").source == "ssh"


def test_run_ssh_ssh_source_lets_config_resolve(fake_home: Path, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(mach.subprocess, "run",
                        lambda cmd, **kw: (captured.update(cmd=cmd), FakeProc())[1])
    m = mach.MachineSpec(name="lab", host="lab", user="pk", port=2222, source="ssh")
    mach.run_remote(m, ["version"])
    cmd = captured["cmd"]
    assert "-p" not in cmd                        # 端口/账号交给 ~/.ssh/config
    assert cmd[cmd.index("BatchMode=yes") + 1] == "lab"  # 目标 = 别名
    assert "pk@lab" not in cmd


def test_cli_remove_rejects_ssh_source(fake_home: Path) -> None:
    write_ssh_config(fake_home, "Host lab\n")
    r = runner.invoke(app, ["machines", "remove", "lab"])
    assert r.exit_code == 2 and "~/.ssh/config" in r.output


def test_cli_add_over_ssh_alias_warns_and_wins(fake_home: Path) -> None:
    write_ssh_config(fake_home, "Host desktop\n  User pk\n")
    r = runner.invoke(app, ["machines", "add", "desktop", "--host", "manual-host"])
    assert r.exit_code == 0 and "自动识别" in r.output
    got = mach.get_machine("desktop")
    assert got is not None and got.source == "manual" and got.host == "manual-host"
    # json 契约带 source 字段（desktop 下拉据此区分可否移除）
    r = runner.invoke(app, ["machines", "list", "--json"])
    import json
    data = json.loads(r.output)
    by_name = {m["name"]: m for m in data["machines"]}
    assert by_name["desktop"]["source"] == "manual"
    assert [n for n in by_name if n != "desktop"] == []  # ssh 同名被 manual 覆盖
