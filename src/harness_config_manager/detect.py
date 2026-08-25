"""工具探测：CLI 可执行命令 + 配置目录存在性 → 已安装判定。"""

from __future__ import annotations

import shutil

from .model import Detection
from .registry import TOOLS, expand


def detect_tools() -> list[Detection]:
    results: list[Detection] = []
    for spec in TOOLS:
        evidence: list[str] = []
        for cli in spec.cli_names:
            found = shutil.which(cli)
            if found:
                evidence.append(f"CLI `{cli}` -> {found}")
        for d in spec.config_dirs:
            if expand(d).exists():
                evidence.append(f"配置目录 ~/{d}")
        results.append(
            Detection(
                tool=spec.key,
                display=spec.display,
                installed=bool(evidence),
                evidence=evidence,
            )
        )
    return results
