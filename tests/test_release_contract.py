"""Release 契约：版本号三方一致 + 已构建 sidecar 产物与源码同步。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.MULTILINE).group(1)


def _tauri_conf_version() -> str:
    doc = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    return str(doc["version"])


def _cargo_version() -> str:
    text = (ROOT / "desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.MULTILINE).group(1)


def test_version_numbers_agree_across_manifests() -> None:
    """pyproject == tauri.conf.json == Cargo.toml(halter_version 的 desktop 期望)。"""
    assert _pyproject_version() == _tauri_conf_version() == _cargo_version()


def test_halter_version_json_matches_pyproject() -> None:
    """`halter version --json` 必须报告 pyproject 声明的版本(release 不带旧 runtime)。"""
    from harness_config_manager.cli import app

    result = CliRunner().invoke(app, ["version", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["version"] == _pyproject_version()
    assert payload["name"] == "halter"


def test_bundled_sidecar_artifacts_match_source_version() -> None:
    """release 产物目录中已存在的 sidecar 二进制必须与源码版本一致。

    本地/CI 构建前该目录为空则跳过；release 流水线在构建后运行此测试，
    防止把旧 sidecar 打进安装包（handoff 文档缺口 H）。
    """
    import subprocess

    binaries = ROOT / "desktop/src-tauri/binaries"
    artifacts = sorted(
        path for path in binaries.glob("halter-*")
        if path.is_file() and not path.name.endswith(".sha256")
    ) if binaries.is_dir() else []
    if not artifacts:
        return  # 构建前：没有产物可校验
    expected = _pyproject_version()
    for artifact in artifacts:
        proc = subprocess.run(
            [str(artifact), "version", "--json"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert proc.returncode == 0, f"{artifact.name}: {proc.stderr[:200]}"
        payload = json.loads(proc.stdout)
        assert payload.get("version") == expected, (
            f"{artifact.name} 报告 {payload.get('version')}，源码为 {expected}："
            "release 不应携带旧 sidecar，请重跑 scripts/build_sidecar.sh"
        )


def test_sidecar_build_script_records_input_hash() -> None:
    """构建脚本必须以输入内容 hash（而非 mtime）判定 sidecar 新鲜度。"""
    script = (ROOT / "scripts/build_sidecar.sh").read_text(encoding="utf-8")
    assert "shasum -a 256" in script
    assert "FORCE_SIDECAR" in script
