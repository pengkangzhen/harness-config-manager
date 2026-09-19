"""harness-config-manager (halter): 检测 + 盘点 + 分发 AI 编码工具的用户级 skills / MCP / 插件配置，并调取项目级跨助手历史会话。"""

import sys

from .cli import app


def _force_utf8_stdio() -> None:
    """Windows 遗留代码页（charmap/cp1252/cp936）编码不了中文帮助与表格输出。

    无论 stdout 是控制台还是管道，统一按 UTF-8 写出、不可编码字符替换而非崩溃。
    Python 3.15 起 UTF-8 模式是默认；这里为 3.12/3.13 显式兜底。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # 非常规流（已被替换/关闭）时静默跳过
            pass


def main() -> None:  # noqa: D401 - typer 入口
    _force_utf8_stdio()
    app()


def cli_main() -> None:
    _force_utf8_stdio()
    app()
