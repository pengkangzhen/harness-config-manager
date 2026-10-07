"""Harness 升级管理（desktop 更新面板与 `halter update` 子命令的数据源）。

只覆盖 npm 全局包分发的 harness（NPM_PACKAGES 登记者）：
  当前版本 = `<cli> --version` 输出中的首个语义化版本号
  最新版本 = `npm view <pkg> version`（沿用 npm 自身的代理 / 镜像 / 缓存配置）
  升级动作 = `npm install -g <pkg>@latest`
  手动安装命令 = install_commands()：有官方安装脚本的「脚本 + npm」各一条（面板分行展示），其余仅 npm

非 npm 渠道（brew / 原生安装器）装出的 CLI 同样能读出当前版本，但升级动作
始终落在 npm 全局前缀上 —— 卡片上明示 npm 包名，渠道是否一致由用户判断。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

# registry key -> npm 全局包名（也是「更新面板覆盖哪些 harness」的唯一事实源）。
# 包名与 bin 名均经 npm registry 实测核对（2026-10）；
# Cursor CLI / Goose 等非 npm 分发的 harness 不在本表。
NPM_PACKAGES: dict[str, str] = {
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "gemini": "@google/gemini-cli",
    "opencode": "opencode-ai",
    "copilot-cli": "@github/copilot",
    "kimi": "@moonshot-ai/kimi-code",
    "pi": "@earendil-works/pi-coding-agent",
    "qwen": "@qwen-code/qwen-code",
    "iflow": "@iflow-ai/iflow-cli",
    "amp": "@ampcode/cli",
}

# 有官方原生安装器的 harness -> 安装脚本 URL（与 npm 命令并列各成一条；
# mktemp 落盘 + 退出码透传的写法借鉴 ccswitch）。其余 harness 手动安装就是 npm。
NATIVE_INSTALLERS: dict[str, str] = {
    "claude": "https://claude.ai/install.sh",
    "opencode": "https://opencode.ai/install",
}

_PLATFORM_LABELS = {"darwin": "macOS", "linux": "Linux", "win32": "Windows"}

# `2.1.292 (Claude Code)` / `codex-cli 0.158.0 …` / `gemini-cli v0.16.2` 均取首个 semver
_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?)")

_VERSION_TIMEOUT = 30
_INSTALL_TIMEOUT = 600


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    executable = shutil.which(command[0])
    if executable is None:
        raise FileNotFoundError(command[0])
    argv = [executable, *command[1:]]
    # Windows 上 npm / 各 CLI 多为 .cmd / .bat shim，CreateProcess 需经 cmd.exe
    if executable.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c", *argv]
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def npm_available() -> bool:
    return shutil.which("npm") is not None


def platform_label() -> str:
    return _PLATFORM_LABELS.get(sys.platform, sys.platform)


def tool_installed(cli_names: tuple[str, ...]) -> bool:
    return any(shutil.which(name) for name in cli_names)


def cli_version(cli_name: str) -> str | None:
    try:
        proc = _run([cli_name, "--version"], timeout=_VERSION_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = _VERSION_RE.search(proc.stdout) or _VERSION_RE.search(proc.stderr)
    return match.group(1) if match else None


def install_commands(key: str) -> list[str] | None:
    """手动安装 / 升级命令（面板逐行展示用）：官方脚本与 npm 各一条，其余仅 npm。"""
    package = NPM_PACKAGES.get(key)
    if package is None:
        return None
    npm_cmd = f"npm install -g {package}@latest"
    url = NATIVE_INSTALLERS.get(key)
    if url is None:
        return [npm_cmd]
    script = (f"bash -c 'tmp=$(mktemp) && curl -fsSL {url} -o $tmp && bash $tmp; "
              f"status=$?; rm -f $tmp; exit $status'")
    return [script, npm_cmd]


def npm_latest(package: str) -> str | None:
    try:
        proc = _run(["npm", "view", package, "version"], timeout=_VERSION_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _version_key(version: str) -> tuple:
    # 混排 int/str 的 tuple 无法比较，统一编码成 (rank, value)
    return tuple(
        (0, int(piece)) if piece.isdigit() else (1, piece)
        for piece in re.split(r"[.-]", version)
    )


def _state(installed: bool, current: str | None, latest: str | None) -> str:
    if not installed:
        return "missing"
    if current is None or latest is None:
        return "unknown"
    cur, lat = _version_key(current), _version_key(latest)
    # 仅「当前 == registry 最新」判绿：更高的 current 不再并入 latest（弃更包冻结的
    # latest 会造成假安全感，见 pi @mariozechner→@earendil-works 迁移），单列 ahead
    # —— 面板红色警示，且此刻升级动作等于降级。
    if lat == cur:
        return "latest"
    if lat > cur:
        return "upgradeable"
    return "ahead"


def collect_status() -> dict:
    """并发探测所有 npm 渠道 harness 的当前 / 最新版本与升级状态。"""
    from .registry import BY_KEY

    has_npm = npm_available()
    entries: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(4, 2 * len(NPM_PACKAGES))) as pool:
        current_futures = {}
        latest_futures = {}
        for key, package in NPM_PACKAGES.items():
            spec = BY_KEY.get(key)
            if spec is None or not spec.cli_names:
                continue
            current_futures[key] = pool.submit(cli_version, spec.cli_names[0])
            if has_npm:
                latest_futures[key] = pool.submit(npm_latest, package)
        for key, package in NPM_PACKAGES.items():
            if key not in current_futures:
                continue
            spec = BY_KEY[key]
            installed = tool_installed(spec.cli_names)
            current = current_futures[key].result()
            latest = latest_futures[key].result() if key in latest_futures else None
            entries.append({
                "key": key,
                "display": spec.display,
                "npm_package": package,
                "install": install_commands(key),
                "installed": installed,
                "current": current,
                "latest": latest,
                "state": _state(installed, current, latest),
            })
    return {"platform": platform_label(), "npm_available": has_npm, "tools": entries}


def apply_update(key: str) -> subprocess.CompletedProcess:
    """npm install -g <pkg>@latest；npm 不在 PATH 时抛 FileNotFoundError。"""
    package = NPM_PACKAGES.get(key)
    if package is None:
        raise KeyError(key)
    return _run(["npm", "install", "-g", f"{package}@latest"], timeout=_INSTALL_TIMEOUT)
