"""runner 层：@路由、无头执行、任务留档、后台模式。"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from harness_config_manager.runner import (
    HarnessRunError,
    dispatch,
    list_tasks,
    load_task,
    parse_mentions,
    refresh_task,
    run_foreground,
)


def _make_exec(dir_path: Path, name: str, body: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def fake_harnesses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> Path:
    """伪造 claude/codex/opencode/zcode CLI，隔离真实环境。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_exec(bin_dir, "claude", 'echo "claude says: $2"; exit 0')
    _make_exec(bin_dir, "codex", 'echo "codex says: $2"; exit 0')
    _make_exec(bin_dir, "opencode", 'echo "opencode says: $2"; exit 0')
    zcode = _make_exec(bin_dir, "zcode-cli", 'echo "zcode prompt=$2 cwd=$4"; exit 0')
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("ZCODE_CLI", str(zcode))
    return bin_dir


# ---------------------------------------------------------------------------
# parse_mentions


def test_parse_mentions_basic():
    targets, prompt = parse_mentions("@claude 修一下登录测试")
    assert targets == [("claude", None)]
    assert prompt == "修一下登录测试"


def test_parse_mentions_multiple_dedup_preserve_order():
    targets, prompt = parse_mentions("@codex 和 @claude @codex 一起审查这个 PR")
    assert targets == [("codex", None), ("claude", None)]
    assert prompt == "和 一起审查这个 PR"


def test_parse_mentions_aliases():
    targets, _ = parse_mentions("@cc 干活 @z 也干活")
    assert targets == [("claude", None), ("zcode", None)]


def test_parse_mentions_unknown_token_kept():
    targets, prompt = parse_mentions("参考 @某人 的意见，@claude 执行")
    assert targets == [("claude", None)]
    assert prompt == "参考 @某人 的意见， 执行"


def test_parse_mentions_none():
    targets, prompt = parse_mentions("普通消息没有提及")
    assert targets == []
    assert prompt == "普通消息没有提及"


def test_parse_mentions_with_model():
    targets, prompt = parse_mentions("@claude/opus 修测试 @codex/o3-mini 审查")
    assert targets == [("claude", "opus"), ("codex", "o3-mini")]
    assert prompt == "修测试 审查"


def test_parse_mentions_opencode_slash_model():
    """opencode 的模型本身带 /：@opencode/provider/model，第一个 / 前是 harness。"""
    targets, _ = parse_mentions("@opencode/zhipu/glm-4.7 干活")
    assert targets == [("opencode", "zhipu/glm-4.7")]


def test_parse_mentions_model_alias():
    targets, _ = parse_mentions("@cc/sonnet 干活")
    assert targets == [("claude", "sonnet")]


# ---------------------------------------------------------------------------
# argv 组装


def test_yolo_mode_flags(fake_harnesses, tmp_path):
    from harness_config_manager.runner import RUNNERS

    prefix = RUNNERS["claude"].resolve()
    argv = RUNNERS["claude"].build_argv(prefix, "hi", tmp_path, mode="yolo")
    assert "--dangerously-skip-permissions" in argv

    prefix = RUNNERS["codex"].resolve()
    argv = RUNNERS["codex"].build_argv(prefix, "hi", tmp_path, mode="yolo")
    assert "--dangerously-bypass-approvals-and-sandbox" in argv

    prefix = RUNNERS["zcode"].resolve()
    safe = RUNNERS["zcode"].build_argv(prefix, "hi", tmp_path, mode="safe")
    yolo = RUNNERS["zcode"].build_argv(prefix, "hi", tmp_path, mode="yolo")
    assert safe[safe.index("--mode") + 1] == "build"
    assert yolo[yolo.index("--mode") + 1] == "yolo"


def test_prompt_never_shell_interpolated(fake_harnesses, tmp_path):
    """提示词是单个 argv 元素，shell 元字符原样传递。"""
    from harness_config_manager.runner import RUNNERS

    prefix = RUNNERS["claude"].resolve()
    argv = RUNNERS["claude"].build_argv(prefix, "a;rm -rf / # $(x)", tmp_path)
    assert argv[argv.index("-p") + 1] == "a;rm -rf / # $(x)"


# ---------------------------------------------------------------------------
# 前台并发执行 + 留档


def test_run_foreground_multiple_tools(fake_harnesses, fake_home: Path, tmp_path: Path):
    lines: list[tuple[str, str]] = []
    results = run_foreground(
        [("claude", None), ("codex", None)], "hello", tmp_path, on_line=lambda t, l: lines.append((t, l))
    )
    assert len(results) == 2
    assert all(t.status == "done" and t.exit_code == 0 for t in results)
    tools = {t.tool for t in results}
    assert tools == {"claude", "codex"}

    merged = "\n".join(f"[{t}] {l}" for t, l in lines)
    assert "claude says: hello" in merged
    assert "codex says: hello" in merged

    # 留档可回读
    for t in results:
        meta = fake_home / ".config/halter/tasks" / t.task_id / "task.json"
        assert meta.is_file()
        data = json.loads(meta.read_text())
        assert data["status"] == "done"
        log = fake_home / ".config/halter/tasks" / t.task_id / "output.log"
        assert "says: hello" in log.read_text()
        reloaded = load_task(t.task_id)
        assert reloaded.prompt == "hello"


def test_run_foreground_failure_recorded(fake_harnesses, fake_home: Path, tmp_path: Path):
    _make_exec(fake_harnesses, "claude", 'echo boom >&2; exit 3')
    results = run_foreground([("claude", None)], "x", tmp_path)
    assert results[0].status == "failed"
    assert results[0].exit_code == 3


# ---------------------------------------------------------------------------
# dispatch / 后台模式


def test_dispatch_requires_mention(fake_harnesses):
    with pytest.raises(HarnessRunError, match="@"):
        dispatch("没有提及的消息")


def test_dispatch_requires_prompt(fake_harnesses):
    with pytest.raises(HarnessRunError, match="任务描述"):
        dispatch("@claude")


def test_dispatch_unknown_tool(fake_harnesses):
    with pytest.raises(HarnessRunError, match="未找到 @harness"):
        dispatch("@nope 干活")


def test_dispatch_detached(fake_harnesses, fake_home: Path, tmp_path: Path):
    _make_exec(fake_harnesses, "claude", 'sleep 0.2; echo done-detached')
    infos = dispatch("@claude 后台任务", project=tmp_path, detached=True)
    assert len(infos) == 1
    info = infos[0]
    assert info.detached is True
    assert info.status == "running"

    for _ in range(50):
        if refresh_task(load_task(info.task_id)).status != "running":
            break
        time.sleep(0.1)
    final = refresh_task(load_task(info.task_id))
    assert final.status == "done"
    log = fake_home / ".config/halter/tasks" / final.task_id / "output.log"
    assert "done-detached" in log.read_text()


def test_zcode_app_bundle_resolution(fake_harnesses, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from harness_config_manager import runner

    monkeypatch.delenv("ZCODE_CLI", raising=False)
    fake_node = _make_exec(tmp_path / "bin2", "node", 'echo "$3"')
    monkeypatch.setenv("PATH", str(tmp_path / "bin2"))
    monkeypatch.setattr(runner, "ZCODE_APP_GLOBS", ("/nonexistent/zcode.cjs",))
    assert runner._resolve_zcode() is None


# ---------------------------------------------------------------------------
# 任务列表


def test_list_tasks(fake_harnesses, fake_home: Path, tmp_path: Path):
    run_foreground([("claude", None)], "list-me", tmp_path)
    items = list_tasks(limit=10)
    assert any(t.prompt == "list-me" for t in items)


# ---------------------------------------------------------------------------
# 模型路由


def test_argv_model_flags(fake_harnesses, tmp_path):
    from harness_config_manager.runner import RUNNERS

    prefix = RUNNERS["claude"].resolve()
    argv = RUNNERS["claude"].build_argv(prefix, "hi", tmp_path, model="opus")
    assert argv[argv.index("--model") + 1] == "opus"

    prefix = RUNNERS["codex"].resolve()
    argv = RUNNERS["codex"].build_argv(prefix, "hi", tmp_path, model="o3")
    assert argv[argv.index("-m") + 1] == "o3"

    prefix = RUNNERS["opencode"].resolve()
    argv = RUNNERS["opencode"].build_argv(prefix, "hi", tmp_path, model="zhipu/glm-4.7")
    assert argv[argv.index("-m") + 1] == "zhipu/glm-4.7"


def test_dispatch_model_priority(fake_harnesses, fake_home, tmp_path):
    """内联 > config 默认 > 不传。"""
    from harness_config_manager.config import load_config

    # 内联优先于配置
    infos = dispatch(
        "@claude/opus 干活", project=tmp_path,
        config_models={"claude": "sonnet", "codex": "o3"},
    )
    assert len(infos) == 1
    assert infos[0].model == "opus"
    assert "--model" in infos[0].argv and "opus" in infos[0].argv

    # 无内联时用配置默认
    infos = dispatch(
        "@codex 干活", project=tmp_path,
        config_models={"claude": "sonnet", "codex": "o3"},
    )
    assert infos[0].model == "o3"

    # 两者皆无：argv 不含模型 flag
    infos = dispatch("@claude 干活", project=tmp_path, config_models={})
    assert infos[0].model is None
    assert "--model" not in infos[0].argv


def test_dispatch_reads_config_toml(fake_harnesses, fake_home, tmp_path):
    cfg_path = fake_home / ".config/halter/config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('[models]\nclaude = "sonnet"\ncodex = "o3"\n', encoding="utf-8")
    from harness_config_manager.config import load_config

    cfg = load_config()
    assert cfg.models == {"claude": "sonnet", "codex": "o3"}
    infos = dispatch("@claude 干活", project=tmp_path)
    assert infos[0].model == "sonnet"


def test_zcode_model_recorded_not_mapped(fake_harnesses, tmp_path):
    """zcode 无公开无头模型开关：model 记录在任务里，argv 不注入。"""
    infos = dispatch("@zcode/glm-4.7 干活", project=tmp_path, detached=True)
    assert infos[0].model == "glm-4.7"
    assert "--model" not in infos[0].argv and "-m" not in infos[0].argv
