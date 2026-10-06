"""机器注册表与远程执行：~/.config/halter/machines.toml + ~/.ssh/config 自动发现。

前提：远端机器装有 halter，且 ssh 免密可用（公钥 / ssh-agent）。
machines.toml 只存 host / user / port / halter_path，绝不存密码或密钥；
所有 ssh 调用带 BatchMode=yes，无凭据时立即失败而不是挂起等待输入。

机器来源两类：
- ``ssh`` —— ~/.ssh/config 里的 Host 别名（含 Include 递归），自动发现、零注册；
  连接参数（HostName/User/Port/ProxyJump）完全交给 ssh 自己解析。
- ``manual`` —— machines.toml 显式注册，用于覆盖别名或指定 halter_path；
  与 ssh 别名同名时 manual 优先生效。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from .io_utils import atomic_write_text
from .registry import expand

MACHINES_MANIFEST = lambda: expand(".config/halter/machines.toml")  # noqa: E731
SSH_CONFIG = lambda: expand(".ssh/config")  # noqa: E731

_MAX_INCLUDE_DEPTH = 5


@dataclass
class MachineSpec:
    name: str
    host: str
    user: str | None = None
    port: int = 22
    halter_path: str = "halter"   # 远端 halter：PATH 名或绝对路径（非交互 ssh 常缺 ~/.local/bin）
    source: str = "manual"        # manual = machines.toml；ssh = ~/.ssh/config 自动发现

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host


def load_machines(path: Path | None = None) -> list[MachineSpec]:
    path = path or MACHINES_MANIFEST()
    if not path.exists():
        return []
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    machines: list[MachineSpec] = []
    for tbl in doc.get("machine", []):
        machines.append(MachineSpec(
            name=str(tbl["name"]),
            host=str(tbl["host"]),
            user=tbl.get("user"),
            port=int(tbl.get("port", 22)),
            halter_path=str(tbl.get("halter_path", "halter")),
        ))
    return machines


def save_machines(machines: list[MachineSpec], path: Path | None = None) -> None:
    path = path or MACHINES_MANIFEST()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    aot = tomlkit.aot()
    for m in machines:
        tbl = tomlkit.table()
        tbl["name"] = m.name
        tbl["host"] = m.host
        if m.user:
            tbl["user"] = m.user
        if m.port != 22:
            tbl["port"] = m.port
        if m.halter_path != "halter":
            tbl["halter_path"] = m.halter_path
        aot.append(tbl)
    doc["machine"] = aot
    atomic_write_text(path, tomlkit.dumps(doc))


def get_machine(name: str) -> MachineSpec | None:
    for m in list_machines():
        if m.name == name:
            return m
    return None


# ---------------------------------------------------------------------------
# ~/.ssh/config 自动发现：Host 别名即机器（Include 递归，first-match 语义）


_PLAIN_ALIAS = re.compile(r"^[A-Za-z0-9._-]+$")


def _parse_ssh_config_file(path: Path, seen: dict[str, MachineSpec]) -> list[str]:
    """解析一个 ssh config 文件：登记 Host 别名（先出现的块优先），返回 Include 目标。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    includes: list[str] = []
    aliases: list[str] = []
    fields: dict[str, str] = {}

    def flush() -> None:
        for alias in aliases:
            if alias in seen:
                continue  # ssh 语义：同键 first-match，先出现的 Host 块赢
            port_raw = fields.get("port", "22")
            seen[alias] = MachineSpec(
                name=alias,
                host=alias,
                user=fields.get("user"),
                port=int(port_raw) if port_raw.isdigit() else 22,
                source="ssh",
            )

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"(\S+?)\s*[=\s]\s*(.*)$", line)
        if not m:
            continue
        keyword, rest = m.group(1).lower(), m.group(2).strip()
        if keyword == "host":
            flush()
            aliases = [a for a in rest.split() if _PLAIN_ALIAS.fullmatch(a)]
            fields = {}
        elif keyword == "include":
            includes.extend(rest.split())
        elif aliases and keyword in ("hostname", "user", "port") and keyword not in fields:
            values = rest.split()
            if values:
                fields[keyword] = values[0]
    flush()
    return includes


def discover_ssh_hosts() -> list[MachineSpec]:
    """枚举 ~/.ssh/config（含 Include 递归）里的非通配 Host 别名。

    仅 name 用于连接（ssh 自行解析 config）；user/port 只是展示用的尽力解析。
    """
    import glob as _glob

    seen: dict[str, MachineSpec] = {}
    stack: list[tuple[Path, int, frozenset[Path]]] = [(SSH_CONFIG(), 0, frozenset())]
    while stack:
        path, depth, visiting = stack.pop()
        if depth > _MAX_INCLUDE_DEPTH or path in visiting:
            continue
        for inc in _parse_ssh_config_file(path, seen):
            expanded = Path(inc).expanduser()
            if not expanded.is_absolute():
                expanded = path.parent / expanded
            for matched in sorted(_glob.glob(str(expanded))):
                p = Path(matched)
                if p.is_file():
                    stack.append((p, depth + 1, visiting | {path}))
    return list(seen.values())


def list_machines() -> list[MachineSpec]:
    """全部可用机器：machines.toml（文件序，优先）+ ssh config 别名（字典序）。"""
    manual = load_machines()
    taken = {m.name for m in manual}
    discovered = [m for m in discover_ssh_hosts() if m.name not in taken]
    return manual + sorted(discovered, key=lambda m: m.name)


# ---------------------------------------------------------------------------
# ssh 执行：run_ssh（任意远端 argv）/ run_remote（远端 halter 子命令）


def _ssh_target_args(machine: MachineSpec) -> list[str]:
    """连接目标参数：ssh 来源只给别名（config 全权解析）；
    manual 显式注册时 user/port 才拼进命令行（port 22 缺省不传，
    这样 host 填 ssh 别名的场景不会被 -p 22 破坏 config 里的端口）。"""
    if machine.source == "ssh":
        return [machine.host]
    args: list[str] = []
    if machine.port != 22:
        args += ["-p", str(machine.port)]
    return args + [machine.destination]


def run_ssh(
    machine: MachineSpec,
    remote_argv: list[str],
    input_bytes: bytes | None = None,
    timeout: int = 120,
) -> tuple[int | None, bytes, str]:
    """在远端执行 argv。返回 (returncode, stdout, stderr_text)。

    returncode 为 None 表示本地即失败（无 ssh / 超时 / 无法连接），
    此时 stderr_text 是给人看的错误说明。
    """
    exe = shutil.which("ssh")
    if exe is None:
        return None, b"", "未找到 ssh 可执行文件（请安装 openssh 客户端）"
    cmd = [
        exe,
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        *_ssh_target_args(machine),
        *remote_argv,
    ]
    try:
        proc = subprocess.run(cmd, input=input_bytes, capture_output=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, b"", f"ssh 超时（>{timeout}s）：{machine.name}"
    except OSError as exc:
        return None, b"", f"ssh 执行失败：{exc}"
    return proc.returncode, proc.stdout, (proc.stderr or b"").decode("utf-8", errors="replace")


def run_remote(
    machine: MachineSpec,
    halter_args: list[str],
    input_bytes: bytes | None = None,
    timeout: int = 120,
) -> tuple[int | None, bytes, str]:
    """执行远端 halter 子命令（scan / sync / machines export|ingest …）。"""
    return run_ssh(machine, [machine.halter_path, *halter_args], input_bytes, timeout)


def probe_remote_halter(machine: MachineSpec) -> tuple[bool, str]:
    """诊断远端 halter 可达性：version --json 成功即通。

    失败时用 login shell 探测绝对路径，返回给用户的建议文本。
    """
    rc, out, err = run_remote(machine, ["version", "--json"], timeout=30)
    if rc == 0 and b'"version"' in out:
        return True, out.decode("utf-8", errors="replace").strip()
    hint = err.strip() or f"halter 退出码 {rc}"
    rc2, out2, _ = run_ssh(
        machine,
        ["sh", "-lc", "command -v halter || command -v harness-config-manager"],
        timeout=30,
    )
    if rc2 == 0:
        found = out2.decode("utf-8", errors="replace").strip().splitlines()
        if found:
            hint += f"；login shell 找到 {found[-1]}，可用 --halter-path 指定"
    return False, hint
