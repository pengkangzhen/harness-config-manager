"""Failure-safe local configuration I/O helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import tomlkit
from tomlkit.exceptions import ParseError


def parse_manifest_toml(path: Path):
    """读取并解析 halter 自身清单 TOML；语法错误以可读消息终止而非裸 traceback。

    SystemExit(str) 由解释器顶层打印到 stderr（退出码 1），CLI、测试
    CliRunner 与远端子进程三层的呈现保持一致。
    """
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except ParseError as err:
        raise SystemExit(
            f"halter: {path} TOML 语法错误（{err}）；请修复或移走该文件后重试"
        ) from err


def atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Replace *path* atomically; a crash never exposes a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(mode)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, data: object, *, mode: int = 0o600) -> None:
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        mode=mode,
    )


def load_json_object(path: Path) -> dict:
    """Load a JSON object; only a missing file is treated as empty."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot parse JSON config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON config is not an object: {path}")
    return data


def version_key(value: str) -> tuple[int | str, ...]:
    """A small semantic-ish ordering key for dotted version directories."""
    parts: list[int | str] = []
    for part in value.strip(".").split("."):
        if part.isdigit():
            parts.append(int(part))
        else:
            match = part.lower().split("-", 1)[0]
            parts.extend([0, match] if match else [0])
    return tuple(parts)
