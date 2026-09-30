"""sync --tool/--item 单元格范围过滤（desktop 矩阵点一下圆点的同步语义）。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from harness_config_manager.cli import app

from conftest import make_skill

runner = CliRunner()

OFF = ["--no-skills", "--no-mcp", "--no-plugins", "--no-hooks",
       "--no-agents", "--no-memory", "--no-sessions"]


def only(layer: str) -> list[str]:
    """除 layer 外全部关闭的层开关。"""
    return [a for a in OFF if a != f"--no-{layer}"]


# ---------------------------------------------------------------------------
# --tool 校验


def test_unknown_tool_rejected(fake_home: Path) -> None:
    result = runner.invoke(app, ["sync", *only("skills"), "--tool", "nope"])
    assert result.exit_code == 2
    assert "未安装或未知" in result.output


# ---------------------------------------------------------------------------
# skills 单元格：--tool + --item 收窄到一格


def test_skills_cell_scopes_to_one_tool_and_item(fake_home: Path) -> None:
    # 目标工具用 codex/gemini（skills 目录不含 .agents/skills 共享路径，避免库被
    # 扫描成工具自有条目）；claude/zcode 会把库目录当自己的 skills，不适合此用例
    lib = fake_home / ".agents/skills"
    make_skill(lib, "alpha")
    make_skill(lib, "beta")
    for d in (".codex/skills", ".gemini/skills"):
        (fake_home / d).mkdir(parents=True)

    result = runner.invoke(app, ["sync", *only("skills"),
                                 "--tool", "codex", "--item", "alpha", "--apply"])
    assert result.exit_code == 0
    assert (fake_home / ".codex/skills/alpha").is_symlink()
    assert not (fake_home / ".codex/skills/beta").exists()      # 条目过滤
    assert not (fake_home / ".gemini/skills/alpha").exists()    # 工具过滤


def test_skills_cell_adopts_then_links(fake_home: Path) -> None:
    """条目只在工具侧时：先收养入库，再分发到目标格。"""
    make_skill(fake_home / ".claude/skills", "solo")
    (fake_home / ".codex/skills").mkdir(parents=True)

    result = runner.invoke(app, ["sync", *only("skills"),
                                 "--tool", "codex", "--item", "solo", "--apply"])
    assert result.exit_code == 0
    lib_skill = fake_home / ".agents/skills/solo"
    assert (lib_skill / "SKILL.md").is_file()
    link = fake_home / ".codex/skills/solo"
    assert link.is_symlink() and link.resolve() == lib_skill.resolve()


def test_skills_cell_multiple_items(fake_home: Path) -> None:
    lib = fake_home / ".agents/skills"
    make_skill(lib, "alpha")
    make_skill(lib, "beta")
    make_skill(lib, "gamma")
    (fake_home / ".codex/skills").mkdir(parents=True)

    result = runner.invoke(app, ["sync", *only("skills"), "--tool", "codex",
                                 "--item", "alpha", "--item", "beta", "--apply"])
    assert result.exit_code == 0
    assert (fake_home / ".codex/skills/alpha").is_symlink()
    assert (fake_home / ".codex/skills/beta").is_symlink()
    assert not (fake_home / ".codex/skills/gamma").exists()


# ---------------------------------------------------------------------------
# memory 单元格：--tool 收窄（单行层，无 --item 维度）


def test_memory_cell_scopes_to_one_tool(fake_home: Path) -> None:
    lib = fake_home / ".agents/memory/MEMORY.md"
    lib.parent.mkdir(parents=True)
    lib.write_text("LIB\n", encoding="utf-8")
    claude_md = fake_home / ".claude/CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_text("LIB\n", encoding="utf-8")
    z_md = fake_home / ".zcode/AGENTS.md"
    z_md.parent.mkdir(parents=True)
    z_md.write_text("LIB\n", encoding="utf-8")

    result = runner.invoke(app, ["sync", *only("memory"), "--tool", "zcode", "--apply"])
    assert result.exit_code == 0
    assert z_md.is_symlink()
    assert not claude_md.is_symlink()
    assert claude_md.read_text(encoding="utf-8") == "LIB\n"


# ---------------------------------------------------------------------------
# mcp 单元格：清单条目 + 目标工具双重收窄


def test_mcp_cell_scopes_to_one_server_and_tool(fake_home: Path) -> None:
    from harness_config_manager.mcp_manifest import McpSpec, save_manifest

    save_manifest([
        McpSpec(name="srv1", command="uvx", args=["srv1"]),
        McpSpec(name="srv2", command="uvx", args=["srv2"]),
    ])
    cursor_cfg = fake_home / ".cursor/mcp.json"
    cursor_cfg.parent.mkdir(parents=True)
    cursor_cfg.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["sync", *only("mcp"),
                                 "--tool", "cursor", "--item", "srv1", "--apply"])
    assert result.exit_code == 0
    data = json.loads(cursor_cfg.read_text(encoding="utf-8"))
    assert list(data["mcpServers"]) == ["srv1"]


# ---------------------------------------------------------------------------
# hooks 单元格：矩阵行名（label 基名，可带 #N）匹配 manifest id


def test_hooks_cell_matches_label_base(fake_home: Path) -> None:
    from harness_config_manager.hooks_manifest import HookSpec, save_manifest

    save_manifest([
        HookSpec(id="gate", events=["PreToolUse"], command="echo gate"),
        HookSpec(id="other", events=["PreToolUse"], command="echo other"),
    ])
    (fake_home / ".cursor").mkdir()

    result = runner.invoke(app, ["sync", *only("hooks"),
                                 "--tool", "cursor", "--item", "gate", "--apply"])
    assert result.exit_code == 0
    hooks_cfg = json.loads((fake_home / ".cursor/hooks.json").read_text(encoding="utf-8"))
    ids = [e["halter"] for entries in hooks_cfg["hooks"].values() for e in entries]
    assert ids == ["gate"]
