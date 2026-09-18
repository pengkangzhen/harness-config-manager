"""多 Harness 任务调度：@路由 -> 无头执行 -> 本地留档。

设计要点：
- 每个 harness 一条 RunnerSpec（解析可执行文件 + 组装无头 argv）；
- 提示词作为独立 argv 元素传递，绝不经过 shell；
- 前台模式并发流式回传（输出前缀区分 harness），Ctrl-C / 超时整组终止；
- 后台（--detached）模式直接落盘 output.log，状态由 pid 存活推导；
- 任务目录 ~/.config/halter/tasks/<id>/ 私有权限（0700/0600）。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .sessions import redact_text

# ---------------------------------------------------------------------------
# Runner 声明

ZCODE_APP_GLOBS = (
    "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs",
    "/System/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs",
)


@dataclass(frozen=True)
class RunnerSpec:
    key: str
    display: str
    color: str = "#8b96b8"

    def resolve(self) -> list[str] | None:
        """返回可执行 argv 前缀（找不到则 None）。"""
        if self.key == "zcode":
            return _resolve_zcode()
        name = {"claude": "claude", "codex": "codex", "opencode": "opencode"}[self.key]
        found = shutil.which(name)
        return [found] if found else None

    def build_argv(
        self,
        prefix: Sequence[str],
        prompt: str,
        project: Path,
        mode: str = "safe",
        model: str | None = None,
    ) -> list[str]:
        """组装无头执行 argv。

        mode: safe（默认，权限受控）/ yolo（完全放开）。
        model: 指定 LLM（claude --model / codex -m / opencode -m；
               zcode 无公开无头模型开关，v1 仅记录不映射）。
        """
        argv: list[str]
        if self.key == "claude":
            argv = [*prefix]
            if model:
                argv += ["--model", model]
            argv += ["-p", prompt]
            if mode == "yolo":
                argv.append("--dangerously-skip-permissions")
        elif self.key == "codex":
            argv = [*prefix, "exec"]
            if model:
                argv += ["-m", model]
            argv.append(prompt)
            if mode == "yolo":
                argv.append("--dangerously-bypass-approvals-and-sandbox")
        elif self.key == "zcode":
            argv = [
                *prefix,
                "--prompt", prompt,
                "--cwd", str(project),
                "--mode", "yolo" if mode == "yolo" else "build",
            ]
        elif self.key == "opencode":
            argv = [*prefix, "run"]
            if model:
                argv += ["-m", model]
            argv.append(prompt)
        else:  # pragma: no cover - 注册表受控
            raise ValueError(f"unknown runner: {self.key}")
        return argv


def _resolve_zcode() -> list[str] | None:
    # 1. 显式环境变量（脚本或自定义 CLI）
    env_cli = os.environ.get("ZCODE_CLI", "").strip()
    if env_cli:
        path = Path(env_cli).expanduser()
        if path.is_file():
            # 可执行脚本直接用；.cjs 需要 node 解释器
            if os.access(path, os.X_OK) and path.suffix != ".cjs":
                return [str(path)]
            return _node_prefix(path)
        found = shutil.which(env_cli)
        if found:
            return [found]
    # 2. PATH 上的 zcode
    found = shutil.which("zcode")
    if found:
        return [found]
    # 3. macOS App 内置 CLI
    for pattern in ZCODE_APP_GLOBS:
        path = Path(pattern)
        if path.is_file():
            return _node_prefix(path)
    return None


def _node_prefix(cjs: Path) -> list[str] | None:
    node = shutil.which("node")
    return [node, str(cjs)] if node else None


RUNNERS: dict[str, RunnerSpec] = {
    spec.key: spec
    for spec in (
        RunnerSpec("claude", "Claude Code", "#d97757"),
        RunnerSpec("codex", "Codex", "#10a37f"),
        RunnerSpec("zcode", "ZCode", "#a78bfa"),
        RunnerSpec("opencode", "OpenCode", "#4cc2ff"),
    )
}

RUNNER_KEYS = set(RUNNERS)

# @别名 -> runner key
MENTION_ALIASES = {
    "claude": "claude", "claudecode": "claude", "cc": "claude",
    "codex": "codex", "cx": "codex",
    "zcode": "zcode", "z": "zcode",
    "opencode": "opencode", "oc": "opencode",
}

_MENTION_RE = re.compile(r"(?<![\w@])@([a-z][a-z0-9-]*(?:/[^\s@]+)?)", re.IGNORECASE)


def parse_mentions(text: str) -> tuple[list[tuple[str, str | None]], str]:
    """提取消息中的 @harness 与 @harness/model 提及（去重保序）。

    返回 (targets, 剩余提示词)；target = (runner_key, 内联模型或 None)。
    模型部分以第一个 "/" 分割，其余原样保留（opencode 的 provider/model
    形如 @opencode/zhipu/glm-4.7）。未知的 @token 原样保留。
    """
    targets: list[tuple[str, str | None]] = []

    def _collect(m: re.Match[str]) -> str:
        raw = m.group(1)
        name, _, inline_model = raw.partition("/")
        key = MENTION_ALIASES.get(name.lower())
        if key:
            target = (key, inline_model or None)
            if target not in targets:
                targets.append(target)
            return ""          # 已识别（含重复）一律移除
        return m.group(0)      # 未知 @token 原样保留

    rest = _MENTION_RE.sub(_collect, text)
    prompt = re.sub(r"\s+", " ", rest).strip()
    return targets, prompt


# ---------------------------------------------------------------------------
# 任务模型与存储

@dataclass
class TaskInfo:
    task_id: str
    tool: str
    prompt: str
    project: str
    argv: list[str]
    pid: int | None = None
    status: str = "running"          # running / done / failed / cancelled
    exit_code: int | None = None
    started_at: str = field(default_factory=lambda: _now_iso())
    ended_at: str | None = None
    mode: str = "safe"
    model: str | None = None
    detached: bool = False

    def to_dict(self, include_tail: int = 0) -> dict[str, object]:
        d: dict[str, object] = {
            "task_id": self.task_id,
            "tool": self.tool,
            "display": RUNNERS[self.tool].display if self.tool in RUNNERS else self.tool,
            "prompt": redact_text(self.prompt),
            "project": self.project,
            "argv": [redact_text(a) for a in self.argv],
            "pid": self.pid,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "mode": self.mode,
            "model": self.model,
            "detached": self.detached,
        }
        if include_tail:
            d["output_tail"] = read_output_tail(self.task_id, include_tail)
        return d


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def tasks_root(home: Path | None = None) -> Path:
    base = (home or Path.home()) / ".config" / "halter" / "tasks"
    base.mkdir(parents=True, exist_ok=True)
    try:
        base.chmod(0o700)
    except OSError:
        pass
    return base



def validate_task_id(task_id: str) -> str:
    """Validate a task id before it is joined into the private tasks root."""
    if (
        not isinstance(task_id, str)
        or task_id in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task_id)
    ):
        raise ValueError(f"invalid task id: {task_id!r}")
    return task_id


def _atomic_write_private(path: Path, data: str) -> None:
    """Write private task metadata without exposing a partially written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

def task_dir(task_id: str, home: Path | None = None) -> Path:
    root = tasks_root(home)
    validate_task_id(task_id)
    d = root / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def save_task(info: TaskInfo) -> Path:
    d = task_dir(info.task_id)
    meta = d / "task.json"
    _atomic_write_private(
        meta, json.dumps(info.to_dict(), ensure_ascii=False, indent=2) + "\n"
    )
    return meta


def load_task(task_id: str) -> TaskInfo:
    validate_task_id(task_id)
    meta = tasks_root() / task_id / "task.json"
    if not meta.is_file():
        raise FileNotFoundError(f"task not found: {task_id}")
    raw = json.loads(meta.read_text(encoding="utf-8"))
    raw.pop("output_tail", None)
    raw.pop("display", None)  # to_dict 的派生字段，不属于构造参数
    return TaskInfo(**raw)


def list_tasks(limit: int = 50) -> list[TaskInfo]:
    root = tasks_root()
    items: list[TaskInfo] = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta = d / "task.json"
        if not meta.is_file():
            continue
        try:
            items.append(load_task(d.name))
        except (ValueError, OSError, json.JSONDecodeError, TypeError):
            continue
        if len(items) >= limit:
            break
    return items


def read_output_tail(task_id: str, max_chars: int) -> str:
    validate_task_id(task_id)
    log = tasks_root() / task_id / "output.log"
    if not log.is_file() or max_chars <= 0:
        return ""
    data = log.read_bytes()[-max_chars:].decode("utf-8", errors="replace")
    return redact_text(data)


def _reap_if_child(pid: int | None) -> int | None:
    """若 pid 是本进程的子进程且已退出，收割并返回退出码；否则 None。

    后台任务由同一进程启动时（CLI --detached 后立刻查询），子进程退出后
    会停留为僵尸态；waitpid(WNOHANG) 可以收割并拿到真实退出码。
    """
    if not pid:
        return None
    try:
        waited, status = os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        return None  # 不是本进程的子进程（如桌面端轮询）
    if waited != pid:
        return None  # 仍在运行
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return None


def _pid_alive(pid: int | None) -> bool:
    """判定 pid 是否仍在运行（僵尸视为已退出）。"""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # kill(0) 对僵尸进程也返回成功；用 ps 判定进程状态
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
        return out.returncode == 0 and not out.stdout.strip().startswith("Z")
    except (OSError, subprocess.SubprocessError):
        return True  # 无法判定时保守视为存活


def refresh_task(info: TaskInfo) -> TaskInfo:
    """后台任务状态由 pid 存活推导；结束时补写终态。

    退出码：同进程启动可收割到真实值；跨进程轮询（桌面 App）拿不到，
    记 null，状态统一为 done。
    """
    if info.status != "running" or not info.detached:
        return info
    reaped = _reap_if_child(info.pid)
    if reaped is not None or not _pid_alive(info.pid):
        info.exit_code = reaped
        info.status = "done" if reaped in (None, 0) else "failed"
        info.ended_at = _now_iso()
        save_task(info)
    return info


# ---------------------------------------------------------------------------
# 执行

class HarnessRunError(RuntimeError):
    pass


def _spawn(argv: list[str], project: Path, log_path: Path | None) -> subprocess.Popen[bytes]:
    """启动 harness 进程。独立进程组；log_path 为空时捕获 stdout 供流式回传。"""
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    stdout: object = subprocess.PIPE if log_path is None else log_path.open("ab")
    return subprocess.Popen(
        argv,
        cwd=str(project),
        env=env,
        stdout=stdout,  # type: ignore[arg-type]
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def start_detached(
    tool: str,
    prompt: str,
    project: Path,
    mode: str = "safe",
    model: str | None = None,
) -> TaskInfo:
    """后台启动一个 harness 任务，立即返回任务元数据。"""
    spec = RUNNERS.get(tool)
    if spec is None:
        raise HarnessRunError(f"unknown harness @{tool}（可选：{', '.join(sorted(RUNNERS))}）")
    prefix = spec.resolve()
    if prefix is None:
        raise HarnessRunError(f"@{tool}（{spec.display}）在本机不可用，未找到可执行文件")
    argv = spec.build_argv(prefix, prompt, project, mode, model)
    info = TaskInfo(
        task_id=new_task_id(),
        tool=tool,
        prompt=prompt,
        project=str(project),
        argv=argv,
        mode=mode,
        model=model,
        detached=True,
    )
    d = task_dir(info.task_id)
    log = d / "output.log"
    log.touch()
    try:
        log.chmod(0o600)
    except OSError:
        pass
    proc = _spawn(argv, project, log)
    info.pid = proc.pid
    save_task(info)
    return info


def run_foreground(
    targets: Sequence[tuple[str, str | None]],
    prompt: str,
    project: Path,
    mode: str = "safe",
    timeout: float = 3600.0,
    on_line: Callable[[str, str], None] | None = None,
) -> list[TaskInfo]:
    """并发运行多个 harness 任务，流式回传输出行，直到全部结束。

    targets: [(runner_key, model)]；model 为 None 时由 harness 自身默认。
    on_line(tool, line) 用于上层接管展示；默认打印到 stdout。
    超时或 KeyboardInterrupt 时终止整个进程组并标记 cancelled。
    """
    specs: list[tuple[RunnerSpec, list[str], TaskInfo]] = []
    for tool, model in targets:
        spec = RUNNERS.get(tool)
        if spec is None:
            raise HarnessRunError(f"unknown harness @{tool}（可选：{', '.join(sorted(RUNNERS))}）")
        prefix = spec.resolve()
        if prefix is None:
            raise HarnessRunError(f"@{tool}（{spec.display}）在本机不可用，未找到可执行文件")
        argv = spec.build_argv(prefix, prompt, project, mode, model)
        info = TaskInfo(
            task_id=new_task_id(), tool=tool, prompt=prompt,
            project=str(project), argv=argv, mode=mode, model=model,
        )
        specs.append((spec, argv, info))

    procs: dict[str, subprocess.Popen[bytes]] = {}
    logs: dict[str, Path] = {}
    for spec, argv, info in specs:
        d = task_dir(info.task_id)
        log = d / "output.log"
        log.touch()
        try:
            log.chmod(0o600)
        except OSError:
            pass
        logs[info.task_id] = log
        proc = _spawn(argv, project, None)
        procs[info.task_id] = proc
        info.pid = proc.pid
        save_task(info)
        if on_line:
            on_line(tool, f"$ {' '.join(shlex.quote(redact_text(a)) for a in argv)}")

    def _pump(tool_key: str, task_id: str, proc: subprocess.Popen[bytes]) -> None:
        stream = proc.stdout
        assert stream is not None
        with logs[task_id].open("ab") as sink:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                sink.write((line + "\n").encode("utf-8"))
                if on_line:
                    on_line(tool_key, line)

    threads = []
    for spec, _argv, info in specs:
        t = threading.Thread(target=_pump, args=(spec.key, info.task_id, procs[info.task_id]), daemon=True)
        t.start()
        threads.append(t)

    deadline = time.monotonic() + timeout
    interrupted = False
    try:
        while any(p.poll() is None for p in procs.values()):
            if time.monotonic() > deadline:
                interrupted = "timeout"
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        interrupted = "interrupt"

    if interrupted:
        for proc in procs.values():
            _kill_group(proc)
        reason = "超时" if interrupted == "timeout" else "收到中断"
        if on_line:
            on_line("*", f"[halter] {reason}，已终止全部任务进程组")

    results: list[TaskInfo] = []
    for spec, _argv, info in specs:
        proc = procs[info.task_id]
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            _kill_group(proc)
            code = proc.wait()
        info.exit_code = code
        info.status = "cancelled" if interrupted else ("done" if code == 0 else "failed")
        info.ended_at = _now_iso()
        save_task(info)
        results.append(info)
    for t in threads:
        t.join(timeout=2)
    return results


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def dispatch(
    message: str,
    project: Path | None = None,
    mode: str = "safe",
    detached: bool = False,
    timeout: float = 3600.0,
    on_line: Callable[[str, str], None] | None = None,
    config_models: dict[str, str] | None = None,
) -> list[TaskInfo]:
    """解析 @提及并派发任务。无 @ 提及时报错。

    模型优先级：@harness/model 内联 > config_models[harness] > 工具默认。
    """
    from .config import load_config

    targets, prompt = parse_mentions(message)
    if not targets:
        raise HarnessRunError(
            f"消息中未找到 @harness 提及（可用：{', '.join('@' + k for k in RUNNERS)}）"
        )
    if not prompt:
        raise HarnessRunError("@提及之外还需要任务描述")
    defaults = dict(config_models) if config_models is not None else dict(load_config().models)
    resolved = [(tool, inline or defaults.get(tool)) for tool, inline in targets]
    project = (project or Path.cwd()).expanduser().resolve(strict=False)
    if detached:
        return [start_detached(tool, prompt, project, mode, model)
                for tool, model in resolved]
    return run_foreground(resolved, prompt, project, mode, timeout, on_line)
