"""statusline 层：方言解析、扫描、逐工具收养、脚本 symlink + 配置键分发与冲突语义。"""

from __future__ import annotations

import json as _json
from pathlib import Path

from halter.config import HalterConfig
from halter.io_utils import load_json_object
from halter.model import ToolReport
from halter.registry import BY_KEY, expand
from halter.statusline import (
    DEFAULT_STATUSLINE_LIBRARY,
    command_script,
    load_manifest,
    plan_adopt_statusline,
    plan_sync_statusline,
    resolve_statusline_library,
    run_adopt_statusline,
    run_sync_statusline,
    scan_statusline,
    statusline_settings_path,
)


def make_tool_statusline(home: Path, key: str, script: str = "SL v1\n",
                         extra: dict | None = None) -> ToolReport:
    """在 fake home 下给工具配置 statusLine（脚本写在工具配置目录根），返回填好的 ToolReport。"""
    spec = BY_KEY[key]
    settings = statusline_settings_path(key)
    settings.parent.mkdir(parents=True, exist_ok=True)
    script_path = expand(spec.config_dirs[0]) / "statusline.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    sl = {"type": "command",
          "command": f"python3 {script_path}", "padding": 0}
    if extra:
        sl.update(extra)
    data = load_json_object(settings)
    data["statusLine"] = sl
    settings.write_text(_json.dumps(data), encoding="utf-8")
    report = ToolReport(tool=key, display=spec.display, installed=True)
    info, _ = scan_statusline(spec)
    report.statusline = info
    return report


def make_codex_statusline(home: Path, items: list[str] | None = None,
                          colors: bool = True) -> ToolReport:
    """codex：config.toml [tui] status_line 声明式条目（附非托管键验证隔离）。"""
    items = ["model", "context-remaining", "git-branch"] if items is None else items
    path = statusline_settings_path("codex")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# codex config\n[tui]\nstatus_line = " + _json.dumps(items).replace('"', '"')
        + f"\nstatus_line_use_colors = {str(colors).lower()}\ntheme = \"dark\"\n",
        encoding="utf-8")
    report = ToolReport(tool="codex", display="OpenAI Codex", installed=True)
    info, _ = scan_statusline(BY_KEY["codex"])
    report.statusline = info
    return report


def make_library(home: Path, script: str = "SL v1\n", name: str = "statusline.py",
                 command: str | None = "python3 {script}",
                 tools: tuple[str, ...] = ("claude", "zcode")) -> Path:
    """库：per-tool 片段清单 + 可选脚本本体（库内按工具分目录）。command=None 表示无脚本片段。"""
    lib = home / DEFAULT_STATUSLINE_LIBRARY
    lib.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    for tool in tools:
        if command is not None:
            manifest[tool] = {"statusLine": {"type": "command", "command": command,
                                             "padding": 0},
                              "script": name}
            (lib / tool).mkdir(exist_ok=True)
            (lib / tool / name).write_text(script, encoding="utf-8")
        else:
            manifest[tool] = {"statusLine": {"type": "command",
                                             "command": "npx -y ccstatusline",
                                             "padding": 0},
                              "script": None}
    (lib / "manifest.json").write_text(_json.dumps(manifest), encoding="utf-8")
    return lib


# ---------------------------------------------------------------------------
# 事实源解析


def test_resolve_default_and_override(fake_home: Path):
    assert resolve_statusline_library(HalterConfig()) == fake_home / ".agents/statusline"
    cfg = HalterConfig(statusline_library="~/custom/sl")
    assert resolve_statusline_library(cfg) == Path("~/custom/sl").expanduser()


def test_load_manifest_filters_unknown_tools(fake_home: Path):
    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    assert load_manifest(lib) == {}                     # 目录缺失
    lib.mkdir(parents=True)
    (lib / "manifest.json").write_text("not json", encoding="utf-8")
    assert load_manifest(lib) == {}                     # manifest 损坏
    (lib / "manifest.json").write_text(
        _json.dumps({"claude": {"statusLine": {}, "script": None},
                     "iflow": {"whatever": 1}}),        # 非方言工具不入清单
        encoding="utf-8")
    assert set(load_manifest(lib)) == {"claude"}


# ---------------------------------------------------------------------------
# command 解析与扫描


def test_command_script_variants(fake_home: Path):
    script = fake_home / "sl.py"
    script.write_text("# x\n", encoding="utf-8")
    assert command_script(f"python3 {script}") == script
    assert command_script(f'bash "{script}" --arg') == script  # 引号 token
    assert command_script("npx -y ccstatusline") is None       # 无本地脚本
    assert command_script("python3 /no/such/file.py") is None  # 引用不存在
    assert command_script("python3 ~/no/such.py") is None


def test_scan_statusline_states(fake_home: Path):
    spec = BY_KEY["claude"]
    assert scan_statusline(spec)[0] is None                    # 目录不存在 → 无此层
    (fake_home / ".claude").mkdir()
    info, _ = scan_statusline(spec)
    assert info is not None and not info.present               # settings 无 statusLine 键
    make_tool_statusline(fake_home, "claude")
    info, _ = scan_statusline(spec)
    assert info.present and info.command.startswith("python3 ")
    assert info.script == fake_home / ".claude/statusline.py"
    assert not info.linked and info.synced is None             # 库缺失无从判定
    # 脚本换成指向库的 symlink + 建库 → synced / linked
    make_library(fake_home, tools=("claude",))
    info.script.unlink()
    info.script.symlink_to(fake_home / ".agents/statusline/claude/statusline.py")
    info2, _ = scan_statusline(spec)
    assert info2.linked and info2.synced is True
    # 库有片段而工具侧未配置：synced=False（可同步的缺口），非 None
    (fake_home / ".claude/settings.json").write_text("{}", encoding="utf-8")
    info3, _ = scan_statusline(spec)
    assert not info3.present and info3.synced is False and not info3.linked


def test_scan_codex_declarative_items(fake_home: Path):
    assert scan_statusline(BY_KEY["codex"])[0] is None         # 无 config.toml → 无此层
    r = make_codex_statusline(fake_home)
    info = r.statusline
    assert info.present and info.command is None and info.script is None
    assert info.items == ["model", "context-remaining", "git-branch"]
    assert info.payload == {"tui": {"status_line": info.items,
                                    "status_line_use_colors": True}}
    # 库无 codex 片段 → synced None；有且一致 → True
    assert info.synced is None
    make_library(fake_home, tools=("codex",), command=None)
    # codex 家族片段形态不同，手工构造对齐后再扫
    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    manifest = _json.loads((lib / "manifest.json").read_text(encoding="utf-8"))
    manifest["codex"] = {"tui": {"status_line": info.items, "status_line_use_colors": True}}
    (lib / "manifest.json").write_text(_json.dumps(manifest), encoding="utf-8")
    info2, _ = scan_statusline(BY_KEY["codex"])
    assert info2.synced is True and info2.linked


# ---------------------------------------------------------------------------
# adopt：逐工具片段，各自唯一来源


def test_plan_adopt_collects_present_tools(fake_home: Path):
    assert plan_adopt_statusline([]) == []
    r1 = make_tool_statusline(fake_home, "claude", "a")
    r2 = make_tool_statusline(fake_home, "zcode", "b")   # 两边内容不同也互不冲突
    r3 = make_codex_statusline(fake_home)
    adopted = {t for t, _ in plan_adopt_statusline([r1, r2, r3])}
    assert adopted == {"claude", "zcode", "codex"}


def test_run_adopt_copies_script_and_manifest(fake_home: Path):
    r = make_tool_statusline(fake_home, "claude", "# my statusline\n")
    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    run_adopt_statusline("claude", r.statusline, lib, apply=True)
    assert (lib / "claude/statusline.py").read_text(encoding="utf-8") == "# my statusline\n"
    manifest = load_manifest(lib)
    # command 中本机绝对路径已模板化为 {script}
    assert manifest["claude"]["statusLine"]["command"] == "python3 {script}"
    assert manifest["claude"]["script"] == "statusline.py"


def test_run_adopt_codex_fragment(fake_home: Path):
    r = make_codex_statusline(fake_home, items=["model", "usage"])
    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    run_adopt_statusline("codex", r.statusline, lib, apply=True)
    manifest = load_manifest(lib)
    assert manifest["codex"] == {"tui": {"status_line": ["model", "usage"],
                                         "status_line_use_colors": True}}


def test_run_adopt_merges_into_existing_manifest(fake_home: Path):
    r1 = make_tool_statusline(fake_home, "claude", "A\n")
    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    run_adopt_statusline("claude", r1.statusline, lib, apply=True)
    r2 = make_codex_statusline(fake_home)
    run_adopt_statusline("codex", r2.statusline, lib, apply=True)
    assert set(load_manifest(lib)) == {"claude", "codex"}   # 收养不覆盖既有片段


# ---------------------------------------------------------------------------
# sync：command 驱动家族（claude / zcode）


def test_plan_sync_all_states(fake_home: Path):
    lib = make_library(fake_home, "LIB\n")
    r_claude = make_tool_statusline(fake_home, "claude")          # 真实文件同内容 → replace
    r_claude.statusline.script.write_text("LIB\n", encoding="utf-8")
    r_zcode = make_tool_statusline(fake_home, "zcode", "OTHER\n")  # 内容不同 → conflict
    r_codex = ToolReport(tool="codex", display="Codex", installed=True)  # 无片段 → 跳过
    (fake_home / ".codex").mkdir()

    actions = plan_sync_statusline(lib, [r_claude, r_zcode, r_codex])
    by_tool = {a.tool: a for a in actions}
    assert set(by_tool) == {"claude", "zcode"}
    assert by_tool["claude"].kind == "replace"
    assert by_tool["zcode"].kind == "conflict"


def test_plan_sync_relink_update_ok(fake_home: Path):
    lib = make_library(fake_home, "LIB\n")
    # claude：脚本是指向别处的 symlink → relink
    r_claude = make_tool_statusline(fake_home, "claude")
    r_claude.statusline.script.unlink()
    r_claude.statusline.script.symlink_to(fake_home / ".agents/skills")
    # zcode：脚本已正确链接但 settings 键漂移 → update
    r_zcode = make_tool_statusline(fake_home, "zcode")
    r_zcode.statusline.script.unlink()
    r_zcode.statusline.script.symlink_to(lib / "zcode/statusline.py")
    zsettings = fake_home / ".zcode/cli/config.json"
    data = load_json_object(zsettings)
    data["statusLine"]["padding"] = 10
    zsettings.write_text(_json.dumps(data), encoding="utf-8")

    actions = {a.tool: a for a in plan_sync_statusline(lib, [r_claude, r_zcode])}
    assert actions["claude"].kind == "relink"
    assert actions["zcode"].kind == "update" and actions["zcode"].settings_update

    # update 落地：settings 键恢复库期望，其余键不碰，写入前有备份
    run_sync_statusline(list(actions.values()), lib, apply=True)
    fixed = load_json_object(zsettings)
    assert fixed["statusLine"]["padding"] == 0
    assert fixed["statusLine"]["command"] == f"python3 {fake_home}/.zcode/statusline.py"
    backups = list((fake_home / ".config/halter/backups/statusline").glob("*-zcode-*"))
    assert backups and _json.loads(backups[0].read_text(encoding="utf-8"))["statusLine"]["padding"] == 10


def test_run_sync_links_and_backs_up(fake_home: Path):
    lib = make_library(fake_home, "LIB\n", tools=("claude", "zcode"))
    r_claude = make_tool_statusline(fake_home, "claude")          # 真实文件同内容 → replace
    r_claude.statusline.script.write_text("LIB\n", encoding="utf-8")
    r_zcode = make_tool_statusline(fake_home, "zcode", "OLD\n")   # 内容不同 → conflict

    lines = run_sync_statusline(plan_sync_statusline(lib, [r_claude, r_zcode]), lib, apply=True)
    c = fake_home / ".claude/statusline.py"
    assert c.is_symlink() and c.read_text(encoding="utf-8") == "LIB\n"
    assert (fake_home / ".zcode/statusline.py").read_text(encoding="utf-8") == "OLD\n"
    assert any("跳过" in ln for ln in lines)
    # claude 侧 settings 键同时就位（command 指向本机工具目录脚本）
    assert load_json_object(fake_home / ".claude/settings.json")["statusLine"]["command"] \
        == f"python3 {c}"

    run_sync_statusline(plan_sync_statusline(lib, [r_zcode]), lib, apply=True, prefer="library")
    z = fake_home / ".zcode/statusline.py"
    assert z.is_symlink() and z.read_text(encoding="utf-8") == "LIB\n"
    backups = list((fake_home / ".config/halter/backups").glob("*/statusline/zcode/statusline.py"))
    assert any(Path(p).read_text(encoding="utf-8") == "OLD\n" for p in backups)


def test_synced_state_reports_ok(fake_home: Path):
    lib = make_library(fake_home, "LIB\n", tools=("claude",))
    r = make_tool_statusline(fake_home, "claude", "LIB\n")  # 内容同库 → replace → 链接
    run_sync_statusline(plan_sync_statusline(lib, [r]), lib, apply=True)
    r.statusline = scan_statusline(BY_KEY["claude"])[0]
    assert r.statusline.synced is True
    assert [a.kind for a in plan_sync_statusline(lib, [r])] == ["ok"]


def test_no_script_fragment_syncs_settings_only(fake_home: Path):
    """command 不引用本地脚本（如 npx ccstatusline）：只同步 settings 键。"""
    lib = make_library(fake_home, command=None, tools=("claude",))
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude/settings.json").write_text(
        _json.dumps({"model": "keep"}), encoding="utf-8")
    r = ToolReport(tool="claude", display="Claude Code", installed=True)
    r.statusline = scan_statusline(BY_KEY["claude"])[0]

    actions = plan_sync_statusline(lib, [r])
    assert [a.kind for a in actions] == ["update"] and actions[0].script is None
    run_sync_statusline(actions, lib, apply=True)
    data = load_json_object(fake_home / ".claude/settings.json")
    assert data["model"] == "keep"                      # 非托管键不碰
    assert data["statusLine"]["command"] == "npx -y ccstatusline"


def test_plan_sync_skips_uninitialized_library(fake_home: Path):
    r = make_tool_statusline(fake_home, "claude")
    assert plan_sync_statusline(fake_home / ".agents/statusline", [r]) == []


# ---------------------------------------------------------------------------
# sync：声明式家族（codex）


def test_codex_plan_and_run_preserves_unmanaged(fake_home: Path):
    """[tui] 只动托管键：theme 等非托管键与注释原样保留，写入前有备份。"""
    r = make_codex_statusline(fake_home, items=["model", "git-branch"])
    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    run_adopt_statusline("codex", r.statusline, lib, apply=True)
    # 工具侧漂移（模拟另一台机器手调）
    path = statusline_settings_path("codex")
    path.write_text('# codex config\n[tui]\nstatus_line = ["usage"]\ntheme = "dark"\n',
                    encoding="utf-8")
    r.statusline = scan_statusline(BY_KEY["codex"])[0]
    actions = plan_sync_statusline(lib, [r])
    assert [a.kind for a in actions] == ["update"]

    run_sync_statusline(actions, lib, apply=True)
    text = path.read_text(encoding="utf-8")
    assert "# codex config" in text                       # 注释保留
    assert 'theme = "dark"' in text                       # 非托管键不碰
    import tomllib
    tui = tomllib.loads(text)["tui"]
    assert tui["status_line"] == ["model", "git-branch"]
    assert tui.get("status_line_use_colors") is True
    backups = list((fake_home / ".config/halter/backups/statusline").glob("*-codex-*"))
    assert backups and tomllib.loads(backups[0].read_text(encoding="utf-8"))["tui"]["status_line"] == ["usage"]

    # 再同步 → ok（幂等）
    r.statusline = scan_statusline(BY_KEY["codex"])[0]
    assert [a.kind for a in plan_sync_statusline(lib, [r])] == ["ok"]


# ---------------------------------------------------------------------------
# CLI 端到端：逐工具收养 -> 分发 -> 漂移修复 -> codex 家族全链路


def test_statusline_cli_end_to_end(fake_home: Path):
    from typer.testing import CliRunner

    from halter.cli import app

    runner = CliRunner()
    off = ["--no-skills", "--no-agents", "--no-mcp", "--no-plugins",
           "--no-hooks", "--no-memory", "--no-sessions"]
    make_tool_statusline(fake_home, "claude", "# claude v1\n")
    make_tool_statusline(fake_home, "zcode", "# zcode own\n")   # 与 claude 不同也互不冲突

    # 1. dry-run：报告逐工具收养，不落盘
    out = runner.invoke(app, ["sync", *off]).output
    assert "逐工具收养" in out
    assert not (fake_home / DEFAULT_STATUSLINE_LIBRARY / "manifest.json").exists()

    # 2. --apply：两个片段独立入库；各自脚本 replace 为链接（内容各自相同）
    runner.invoke(app, ["sync", *off, "--apply"])
    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    manifest = load_manifest(lib)
    assert set(manifest) == {"claude", "zcode"}
    c = fake_home / ".claude/statusline.py"
    z = fake_home / ".zcode/statusline.py"
    assert c.is_symlink() and c.read_text(encoding="utf-8") == "# claude v1\n"
    assert z.is_symlink() and z.read_text(encoding="utf-8") == "# zcode own\n"

    # 3. 工具侧脚本漂移（unlink 后写成不同内容的真实文件）→ conflict 跳过；--prefer library 修复
    z.unlink()
    z.write_text("# drifted\n", encoding="utf-8")
    out = runner.invoke(app, ["sync", *off, "--apply"]).output
    assert "conflict" in out and z.read_text(encoding="utf-8") == "# drifted\n"
    runner.invoke(app, ["sync", *off, "--prefer", "library", "--apply"])
    assert z.is_symlink() and z.read_text(encoding="utf-8") == "# zcode own\n"
    backups = list((fake_home / ".config/halter/backups").glob("*/statusline/zcode/statusline.py"))
    assert any(Path(p).read_text(encoding="utf-8") == "# drifted\n" for p in backups)

    # 4. scan 明细与 --json 均能看到 statusline 状态
    out = runner.invoke(app, ["scan", "-d", "statusline"]).output
    assert "状态栏" in out and "已同步" in out
    out = runner.invoke(app, ["scan", "--json"]).output
    assert '"statusline"' in out and '"linked": true' in out


def test_statusline_cli_codex_end_to_end(fake_home: Path):
    from typer.testing import CliRunner

    from halter.cli import app

    runner = CliRunner()
    off = ["--no-skills", "--no-agents", "--no-mcp", "--no-plugins",
           "--no-hooks", "--no-memory", "--no-sessions"]
    make_codex_statusline(fake_home, items=["model", "usage"])
    (fake_home / ".claude").mkdir()  # 让 claude 可探测但无 statusline（不收养）

    result = runner.invoke(app, ["sync", *off, "--apply"])
    assert result.exit_code == 0
    manifest = load_manifest(fake_home / DEFAULT_STATUSLINE_LIBRARY)
    assert set(manifest) == {"codex"}
    # scan --json：codex 声明式家族 linked/synced 判定
    out = runner.invoke(app, ["scan", "--json"]).output
    assert '"items": ["model", "usage"]' in out and '"synced": true' in out


# ---------------------------------------------------------------------------
# 新方言：cursor（根级 statusLine）/ qwen（嵌套 ui.statusLine）/ gemini（ui.footer）


def test_cursor_root_statusline_same_family_as_claude(fake_home: Path):
    """cursor CLI 的 cli-config.json 根级 statusLine 与 claude 同构同语义。"""
    settings = statusline_settings_path("cursor")
    settings.parent.mkdir(parents=True)
    script = fake_home / ".cursor/sl.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    settings.write_text(_json.dumps({
        "statusLine": {"type": "command", "command": f"bash {script}", "padding": 2}}),
        encoding="utf-8")
    spec = BY_KEY["cursor"]
    info, _ = scan_statusline(spec)
    assert info.present and info.script == script

    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    run_adopt_statusline("cursor", info, lib, apply=True)
    manifest = load_manifest(lib)
    assert manifest["cursor"]["statusLine"]["command"] == "bash {script}"
    assert (lib / "cursor/sl.sh").read_text(encoding="utf-8") == "#!/bin/sh\necho hi\n"

    r = ToolReport(tool="cursor", display="Cursor", installed=True)
    r.statusline = info
    run_sync_statusline(plan_sync_statusline(lib, [r]), lib, apply=True)
    assert script.is_symlink() and script.read_text(encoding="utf-8") == "#!/bin/sh\necho hi\n"
    fixed = _json.loads(settings.read_text(encoding="utf-8"))
    assert fixed["statusLine"]["command"] == f"bash {script}"


def test_qwen_nested_ui_statusline(fake_home: Path):
    """qwen：statusLine 必须嵌在 ui 下；ui 内其它键与顶层键永不碰。"""
    settings = statusline_settings_path("qwen")
    settings.parent.mkdir(parents=True)
    script = fake_home / ".qwen/sl.js"          # qwen 无 config_dirs → 脚本随 settings 父目录
    script.write_text("// qw\n", encoding="utf-8")
    settings.write_text(_json.dumps({
        "model": "keep",
        "ui": {"statusLine": {"type": "command", "command": f"node {script}",
                              "refreshInterval": 10},
               "hideBanner": True}}), encoding="utf-8")
    spec = BY_KEY["qwen"]
    info, _ = scan_statusline(spec)
    assert info.present and info.script == script and info.payload["statusLine"]["refreshInterval"] == 10

    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    run_adopt_statusline("qwen", info, lib, apply=True)
    fragment = load_manifest(lib)["qwen"]
    assert fragment["statusLine"]["command"] == "node {script}"
    assert fragment["script"] == "sl.js"

    r = ToolReport(tool="qwen", display="Qwen Code", installed=True)
    r.statusline = info
    run_sync_statusline(plan_sync_statusline(lib, [r]), lib, apply=True)
    assert script.is_symlink()
    data = _json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "keep"                                  # 顶层键不碰
    assert data["ui"]["hideBanner"] is True                         # ui 内邻居键不碰
    assert data["ui"]["statusLine"]["command"] == f"node {script}"  # 渲染本机路径
    assert data["ui"]["statusLine"]["refreshInterval"] == 10        # 扩展键随片段走


def test_gemini_ui_footer_declarative(fake_home: Path):
    """gemini：/footer 的 ui.footer.items 声明式片段，无脚本。"""
    settings = statusline_settings_path("gemini")
    settings.parent.mkdir(parents=True)
    settings.write_text(_json.dumps({
        "theme": "auto",
        "ui": {"footer": {"items": ["model", "cwd"]}, "hideFooter": False}}),
        encoding="utf-8")
    spec = BY_KEY["gemini"]
    info, _ = scan_statusline(spec)
    assert info.present and info.command is None and info.script is None
    assert info.items == ["model", "cwd"]
    assert info.payload == {"footer": {"items": ["model", "cwd"]}}

    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    run_adopt_statusline("gemini", info, lib, apply=True)
    assert load_manifest(lib)["gemini"] == {"footer": {"items": ["model", "cwd"]}}

    # 工具侧漂移（另一台机器调过 /footer）→ update 恢复，邻居键保留
    data = _json.loads(settings.read_text(encoding="utf-8"))
    data["ui"]["footer"]["items"] = ["tokens"]
    settings.write_text(_json.dumps(data), encoding="utf-8")
    r = ToolReport(tool="gemini", display="Gemini CLI", installed=True)
    r.statusline = scan_statusline(spec)[0]
    actions = plan_sync_statusline(lib, [r])
    assert [a.kind for a in actions] == ["update"] and actions[0].script is None
    run_sync_statusline(actions, lib, apply=True)
    fixed = _json.loads(settings.read_text(encoding="utf-8"))
    assert fixed["theme"] == "auto"
    assert fixed["ui"]["hideFooter"] is False
    assert fixed["ui"]["footer"]["items"] == ["model", "cwd"]
    r.statusline = scan_statusline(spec)[0]
    assert r.statusline.synced is True and r.statusline.linked


def test_kimi_toml_mixed_family(fake_home: Path):
    """kimi：tui.toml [status_line] 混合家族——items 声明式 + command 脚本式。"""
    settings = statusline_settings_path("kimi")
    settings.parent.mkdir(parents=True)
    script = fake_home / ".kimi-code/statusline.sh"
    script.write_text("#!/bin/sh\necho kimi\n", encoding="utf-8")
    settings.write_text(
        "# kimi tui prefs\n[status_line]\nitems = [\"mode\", \"model\", \"cwd\"]\n"
        f"command = \"bash {script}\"\n\n[keymap]\nleader = \"space\"\n",
        encoding="utf-8")
    spec = BY_KEY["kimi"]
    info, _ = scan_statusline(spec)
    assert info.present and info.items == ["mode", "model", "cwd"]
    assert info.script == script

    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    run_adopt_statusline("kimi", info, lib, apply=True)
    fragment = load_manifest(lib)["kimi"]
    assert fragment["status_line"]["command"] == "bash {script}"
    assert fragment["script"] == "statusline.sh"

    # 工具侧漂移 → 首次分发为 replace（脚本真实文件内容同库，备份后链接 +
    # 托管键恢复）；注释与非托管表保留
    settings.write_text(
        "# kimi tui prefs\n[status_line]\nitems = [\"goal\"]\ncommand = \"\"\n"
        "\n[keymap]\nleader = \"space\"\n", encoding="utf-8")
    r = ToolReport(tool="kimi", display="Kimi Code", installed=True)
    r.statusline = scan_statusline(spec)[0]
    actions = plan_sync_statusline(lib, [r])
    assert [a.kind for a in actions] == ["replace"]
    run_sync_statusline(actions, lib, apply=True)
    assert script.is_symlink()
    text = settings.read_text(encoding="utf-8")
    assert "# kimi tui prefs" in text and '[keymap]' in text
    import tomllib
    data = tomllib.loads(text)
    assert data["status_line"]["items"] == ["mode", "model", "cwd"]
    assert data["status_line"]["command"] == f"bash {script}"
    assert data["keymap"] == {"leader": "space"}

    # 再同步 → ok；脚本侧漂移走 conflict 语义
    r.statusline = scan_statusline(spec)[0]
    assert [a.kind for a in plan_sync_statusline(lib, [r])] == ["ok"]
    script.unlink()
    script.write_text("# drifted\n", encoding="utf-8")
    r.statusline = scan_statusline(spec)[0]
    assert [a.kind for a in plan_sync_statusline(lib, [r])] == ["conflict"]


def test_kimi_declarative_only_when_no_command(fake_home: Path):
    """command 为空串（默认）：退化为纯声明式，无脚本动作。"""
    settings = statusline_settings_path("kimi")
    settings.parent.mkdir(parents=True)
    settings.write_text('[status_line]\nitems = ["mode"]\ncommand = ""\n',
                        encoding="utf-8")
    info, _ = scan_statusline(BY_KEY["kimi"])
    assert info.present and info.command is None and info.script is None  # 空 command 视同未设

    lib = fake_home / DEFAULT_STATUSLINE_LIBRARY
    run_adopt_statusline("kimi", info, lib, apply=True)
    fragment = load_manifest(lib)["kimi"]
    assert fragment["script"] is None
    r = ToolReport(tool="kimi", display="Kimi Code", installed=True)
    r.statusline = info
    actions = plan_sync_statusline(lib, [r])
    assert [a.kind for a in actions] == ["ok"]  # 收养后即一致，无脚本可链


# ---------------------------------------------------------------------------
# doctor：死配置与库状态


def test_doctor_dead_command_and_library(fake_home: Path):
    from halter.doctor import run_doctor

    # 库缺失 → warn；statusLine 指向不存在的绝对路径 → dead_command
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude/settings.json").write_text(_json.dumps({
        "statusLine": {"type": "command", "command": "python3 /no/such/sl.py"}}),
        encoding="utf-8")
    codes = [i for i in run_doctor() if "statusline" in i["where"]]
    assert any(i["code"] == "statusline_library_missing" and i["level"] == "warn" for i in codes)
    assert any(i["code"] == "dead_command" and i["level"] == "error" for i in codes)

    make_library(fake_home, tools=("claude",))
    codes = [i["code"] for i in run_doctor() if "statusline" in i["where"]]
    assert "statusline_library_ok" in codes
