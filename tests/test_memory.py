"""memory 层：事实源解析、扫描、收养、symlink 分发与冲突语义。"""

from __future__ import annotations

import json as _json
from pathlib import Path

from harness_config_manager.config import HalterConfig
from harness_config_manager.memory import (
    DEFAULT_MEMORY_FILE,
    memory_target,
    pick_adopt_source,
    plan_adopt_memory,
    plan_sync_memory,
    resolve_memory_file,
    run_adopt_memory,
    run_sync_memory,
    scan_memory,
)
from harness_config_manager.model import MemoryInfo, ToolReport
from harness_config_manager.registry import BY_KEY


def make_tool_dir(home: Path, key: str, memory: str | None = None) -> ToolReport:
    """在 fake home 下创建一个工具的配置目录（及可选记忆文件），返回填好的 ToolReport。"""
    spec = BY_KEY[key]
    pattern = spec.config_dirs[0]
    d = home / pattern
    d.mkdir(parents=True, exist_ok=True)
    report = ToolReport(tool=key, display=spec.display, installed=True)
    if memory is not None:
        target = memory_target(spec)
        assert target is not None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(memory, encoding="utf-8")
    info, _ = scan_memory(spec)
    report.memory = info
    return report


def make_library(home: Path, body: str = "# shared memory\n") -> Path:
    lib = home / DEFAULT_MEMORY_FILE
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_text(body, encoding="utf-8")
    return lib


# ---------------------------------------------------------------------------
# 事实源解析


def test_resolve_default_and_override(fake_home: Path):
    assert resolve_memory_file(HalterConfig()) == fake_home / ".agents/memory/MEMORY.md"
    cfg = HalterConfig(memory_file="~/custom/mem.md")
    assert resolve_memory_file(cfg) == Path("~/custom/mem.md").expanduser()


def test_memory_target_prefers_existing_parent(fake_home: Path):
    # 未创建任何目录：无可用目标
    assert memory_target(BY_KEY["claude"]) is None
    (fake_home / ".config/opencode").mkdir(parents=True)
    # 多候选取父目录存在者（opencode 的 .config 优先于 .opencode）
    assert memory_target(BY_KEY["opencode"]) == fake_home / ".config/opencode/AGENTS.md"


# ---------------------------------------------------------------------------
# 扫描


def test_scan_memory_states(fake_home: Path):
    spec = BY_KEY["claude"]
    # 目录都不存在
    assert scan_memory(spec)[0] is None
    (fake_home / ".claude").mkdir()
    # 目录存在但文件缺失
    info, _ = scan_memory(spec)
    assert info is not None and not info.present and not info.linked
    # 真实文件
    (fake_home / ".claude/CLAUDE.md").write_text("hi", encoding="utf-8")
    info, _ = scan_memory(spec)
    assert info.present and not info.linked and info.size == 2


# ---------------------------------------------------------------------------
# adopt


def test_adopt_plan_divergence(fake_home: Path):
    r1 = make_tool_dir(fake_home, "claude", "same")
    r2 = make_tool_dir(fake_home, "zcode", "same")
    plan = plan_adopt_memory([r1, r2])
    assert not plan.divergent and len(plan.sources) == 2
    (fake_home / ".zcode/AGENTS.md").write_text("different", encoding="utf-8")
    r2.memory = scan_memory(BY_KEY["zcode"])[0]
    plan = plan_adopt_memory([r1, r2])
    assert plan.divergent


def test_pick_adopt_source(fake_home: Path):
    r1 = make_tool_dir(fake_home, "claude", "a")
    r2 = make_tool_dir(fake_home, "zcode", "b")
    plan = plan_adopt_memory([r1, r2])
    # divergent 且未指定 --from：不裁决
    assert pick_adopt_source(plan, None) is None
    # --from 指定生效
    assert pick_adopt_source(plan, "zcode")[0] == "zcode"
    # --from 指向没有记忆文件的工具：不裁决
    assert pick_adopt_source(plan, "codex") is None
    # 内容一致：取第一个
    (fake_home / ".zcode/AGENTS.md").write_text("a", encoding="utf-8")
    r2.memory = scan_memory(BY_KEY["zcode"])[0]
    assert pick_adopt_source(plan_adopt_memory([r1, r2]), None) is not None


def test_run_adopt_copies_source(fake_home: Path):
    make_tool_dir(fake_home, "claude", "# my memory\n")
    lib = fake_home / DEFAULT_MEMORY_FILE
    run_adopt_memory(fake_home / ".claude/CLAUDE.md", lib, apply=True)
    assert lib.read_text(encoding="utf-8") == "# my memory\n"


# ---------------------------------------------------------------------------
# sync


def test_plan_sync_all_states(fake_home: Path):
    lib = make_library(fake_home, "LIB")
    # claude：缺失 → link；zcode：内容同 → replace；codex：目录缺失 → 跳过
    r_claude = make_tool_dir(fake_home, "claude")
    (fake_home / ".claude/CLAUDE.md").symlink_to(fake_home / ".agents/skills")  # 指向别处的链接
    r_zcode = make_tool_dir(fake_home, "zcode", "LIB")
    r_codex = ToolReport(tool="codex", display="Codex", installed=True)

    actions = plan_sync_memory(lib, [r_claude, r_zcode, r_codex])
    by_tool = {a.tool: a for a in actions}
    assert set(by_tool) == {"claude", "zcode"}
    assert by_tool["claude"].kind == "relink"
    assert by_tool["zcode"].kind == "replace"


def test_run_sync_links_and_backs_up(fake_home: Path):
    lib = make_library(fake_home, "LIB")
    r_claude = make_tool_dir(fake_home, "claude")          # 缺失 → link
    r_gemini = make_tool_dir(fake_home, "gemini", "OLD")   # 内容不同 → conflict
    actions = plan_sync_memory(lib, [r_claude, r_gemini])

    lines = run_sync_memory(actions, lib, apply=True)
    target = fake_home / ".claude/CLAUDE.md"
    assert target.is_symlink() and target.read_text(encoding="utf-8") == "LIB"
    # 默认 prefer=skip：gemini 侧不动
    assert (fake_home / ".gemini/GEMINI.md").read_text(encoding="utf-8") == "OLD"
    assert any("跳过" in ln for ln in lines)

    # prefer=library：备份后覆盖
    run_sync_memory(plan_sync_memory(lib, [r_gemini]), lib, apply=True, prefer="library")
    g = fake_home / ".gemini/GEMINI.md"
    assert g.is_symlink() and g.read_text(encoding="utf-8") == "LIB"
    backups = list((fake_home / ".config/halter/backups").glob("*/memory/gemini/GEMINI.md"))
    assert any(Path(p).read_text(encoding="utf-8") == "OLD" for p in backups)


def test_run_sync_replace_backs_up_identical_copy(fake_home: Path):
    lib = make_library(fake_home, "LIB")
    r_zcode = make_tool_dir(fake_home, "zcode", "LIB")
    run_sync_memory(plan_sync_memory(lib, [r_zcode]), lib, apply=True)
    z = fake_home / ".zcode/AGENTS.md"
    assert z.is_symlink()
    backups = list((fake_home / ".config/halter/backups").glob("*/memory/zcode/AGENTS.md"))
    assert any(Path(p).read_text(encoding="utf-8") == "LIB" for p in backups)


def test_synced_state_reports_ok(fake_home: Path):
    lib = make_library(fake_home, "LIB")
    r_claude = make_tool_dir(fake_home, "claude")
    run_sync_memory(plan_sync_memory(lib, [r_claude]), lib, apply=True)
    r_claude.memory = scan_memory(BY_KEY["claude"])[0]
    actions = plan_sync_memory(lib, [r_claude])
    assert [a.kind for a in actions] == ["ok"]


def test_plan_sync_skips_tool_side_library(fake_home: Path):
    """memory_file 直接配在工具侧路径上时，库即目标，不得自链接。"""
    lib = fake_home / ".claude/CLAUDE.md"
    lib.parent.mkdir(parents=True)
    lib.write_text("LIB", encoding="utf-8")
    r_claude = make_tool_dir(fake_home, "claude")
    r_claude.memory = scan_memory(BY_KEY["claude"])[0]
    assert plan_sync_memory(lib, [r_claude]) == []


# ---------------------------------------------------------------------------
# CLI 端到端：分歧报告 -> --from 收养 -> 冲突跳过 -> prefer library 全量链接


def test_memory_cli_end_to_end(fake_home: Path):
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    runner = CliRunner()
    off = ["--no-skills", "--no-agents", "--no-mcp", "--no-plugins",
           "--no-hooks", "--no-sessions"]
    make_tool_dir(fake_home, "claude", "# claude memory v1\n")
    make_tool_dir(fake_home, "zcode", "# zcode older memory\n")

    # 1. 库缺失 + 两工具内容不一致：报告并要求 --from
    out = runner.invoke(app, ["sync", *off]).output
    assert "内容不一致" in out and "--from" in out
    assert not (fake_home / DEFAULT_MEMORY_FILE).exists()

    # 2. --from claude --apply：收养 claude 侧为库；zcode 内容不同 → conflict 跳过
    out = runner.invoke(app, ["sync", *off, "--from", "claude", "--apply"]).output
    lib = fake_home / DEFAULT_MEMORY_FILE
    assert lib.read_text(encoding="utf-8") == "# claude memory v1\n"
    c = fake_home / ".claude/CLAUDE.md"
    z = fake_home / ".zcode/AGENTS.md"
    assert c.is_symlink() and c.read_text(encoding="utf-8") == "# claude memory v1\n"
    assert not z.is_symlink() and z.read_text(encoding="utf-8") == "# zcode older memory\n"
    assert "conflict" in out

    # 3. --prefer library：zcode 备份后被链接，全部一致
    runner.invoke(app, ["sync", *off, "--prefer", "library", "--apply"])
    assert z.is_symlink() and z.read_text(encoding="utf-8") == "# claude memory v1\n"
    backups = list((fake_home / ".config/halter/backups").glob("*/memory/zcode/AGENTS.md"))
    assert any(Path(p).read_text(encoding="utf-8") == "# zcode older memory\n" for p in backups)

    # 4. scan 明细与 --json 均能看到 memory 状态
    out = runner.invoke(app, ["scan", "-d", "memory"]).output
    assert "用户级记忆" in out and "已同步" in out
    out = runner.invoke(app, ["scan", "--json"]).output
    assert '"memory"' in out and '"linked": true' in out


# ---------------------------------------------------------------------------
# memory show / memory write（desktop 记忆面板数据源契约）


def test_memory_show_json_contract(fake_home: Path):
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    make_library(fake_home, "LIB v1\n")
    make_tool_dir(fake_home, "claude", "TOOL v1\n")   # 分歧 → 带 diff
    make_tool_dir(fake_home, "zcode", "LIB v1\n")     # 内容一致 → diff 为空
    runner = CliRunner()
    payload = _json.loads(runner.invoke(app, ["memory", "show", "--json"]).output)
    assert payload["library"]["exists"] and payload["library"]["content"] == "LIB v1\n"
    by_tool = {t["tool"]: t for t in payload["tools"]}
    assert by_tool["claude"]["present"] and not by_tool["claude"]["linked"]
    assert by_tool["claude"]["content"] == "TOOL v1\n"
    assert "-LIB v1" in by_tool["claude"]["diff"] and "+TOOL v1" in by_tool["claude"]["diff"]
    assert by_tool["zcode"]["diff"] is None
    # 无记忆层的工具不出现在快照里
    assert "cursor" not in by_tool


def test_memory_show_library_missing(fake_home: Path):
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    make_tool_dir(fake_home, "claude", "TOOL only\n")
    runner = CliRunner()
    payload = _json.loads(runner.invoke(app, ["memory", "show", "--json"]).output)
    assert payload["library"]["exists"] is False
    by_tool = {t["tool"]: t for t in payload["tools"]}
    assert by_tool["claude"]["content"] == "TOOL only\n"
    assert by_tool["claude"]["diff"] is None  # 库缺失无从 diff


def test_memory_write_stdin_backs_up(fake_home: Path):
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    lib = make_library(fake_home, "OLD\n")
    runner = CliRunner()
    result = runner.invoke(app, ["memory", "write", "--json"], input="NEW content\n")
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    assert lib.read_text(encoding="utf-8") == "NEW content\n"
    assert payload["path"] == str(lib) and payload["size"] == len("NEW content\n")
    backups = list((fake_home / ".config/halter/backups").glob("*/memory/MEMORY.md"))
    assert any(Path(p).read_text(encoding="utf-8") == "OLD\n" for p in backups)


def test_memory_write_creates_missing_library(fake_home: Path):
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    lib = fake_home / DEFAULT_MEMORY_FILE
    runner = CliRunner()
    payload = _json.loads(runner.invoke(app, ["memory", "write", "--json", "--content", "fresh"]).output)
    assert lib.read_text(encoding="utf-8") == "fresh"
    assert payload["backup"] is None          # 库原本不存在，无旧文件可备份
    payload = _json.loads(runner.invoke(app, ["memory", "write", "--json", "--content", "x"]).output)
    assert payload["backup"] is not None      # 第二次写入备份了 "fresh"
