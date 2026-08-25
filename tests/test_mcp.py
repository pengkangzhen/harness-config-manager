"""MCP 层单测：canonical 转换 / 占位展开 / 方言写入器 / 冲突协调。"""

from __future__ import annotations

import json
from pathlib import Path

import tomlkit

from harness_config_manager.mcp_manifest import (
    McpSpec,
    _load_secrets,
    adopt_mcp,
    expand_placeholders,
    load_manifest,
    spec_from_tool_entry,
)
from harness_config_manager.mcp_write import (
    _normalize_for_compare,
    sync_mcp,
    write_claude,
    write_codex,
    write_opencode,
)


# ---------------------------------------------------------------------------
# canonical 转换


def test_spec_from_tool_entry_secret_placeholder(fake_home: Path) -> None:
    spec = spec_from_tool_entry("zotero", {
        "type": "stdio", "command": "uvx", "args": ["zotero-mcp"],
        "env": {"ZOTERO_API_KEY": "sk-123", "ZOTERO_LOCAL": "true"},
    })
    assert spec.env["ZOTERO_API_KEY"] == "${ZOTERO_API_KEY}"
    assert spec.env["ZOTERO_LOCAL"] == "true"
    assert spec.command == "uvx"


def test_spec_from_tool_entry_opencode_array(fake_home: Path) -> None:
    spec = spec_from_tool_entry("node", {"type": "local", "command": ["/bin/node", "--repl"]})
    assert spec.command == "/bin/node"
    assert spec.args == ["--repl"]


def test_spec_from_tool_entry_header_secret(fake_home: Path) -> None:
    spec = spec_from_tool_entry("web", {"type": "http", "url": "https://x",
                                        "headers": {"Authorization": "Bearer z"}})
    assert spec.headers["Authorization"] == "${AUTHORIZATION}"


# ---------------------------------------------------------------------------
# 占位展开


def test_expand_env_over_secrets(fake_home: Path, monkeypatch) -> None:
    monkeypatch.setenv("MY_KEY", "from-env")
    spec = McpSpec(name="x", env={"MY_KEY": "${MY_KEY}", "OTHER": "${OTHER}"})
    out, missing = expand_placeholders(spec, {"OTHER": "from-secrets", "UNUSED": "v"})
    assert out.env == {"MY_KEY": "from-env", "OTHER": "from-secrets"}
    assert missing == []


def test_expand_missing(fake_home: Path, monkeypatch) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    spec = McpSpec(name="x", env={"NOPE": "${NOPE}"})
    out, missing = expand_placeholders(spec, {})
    assert missing == ["NOPE"]


# ---------------------------------------------------------------------------
# adopt --mcp


def test_adopt_mcp_from_claude(fake_home: Path) -> None:
    cfg = fake_home / ".claude.json"
    cfg.write_text(json.dumps({
        "otherState": {"keep": True},
        "mcpServers": {
            "zotero": {"type": "stdio", "command": "uvx", "args": ["zotero-mcp"],
                       "env": {"ZOTERO_API_KEY": "sk-123"}},
            "web": {"type": "http", "url": "https://x",
                    "headers": {"Authorization": "Bearer tok"}},
        },
    }), encoding="utf-8")
    lines = adopt_mcp("claude", apply=True)
    assert any("zotero" in l for l in lines)

    specs = {s.name: s for s in load_manifest()}
    assert specs["zotero"].env["ZOTERO_API_KEY"] == "${ZOTERO_API_KEY}"
    assert specs["web"].headers["Authorization"] == "${AUTHORIZATION}"

    secrets = _load_secrets()
    assert secrets.get("ZOTERO_API_KEY") == "sk-123"
    assert secrets.get("AUTHORIZATION") == "Bearer tok"


# ---------------------------------------------------------------------------
# 写入器


def test_write_claude_preserves_other_keys(fake_home: Path) -> None:
    cfg = fake_home / ".claude.json"
    cfg.write_text(json.dumps({"otherState": {"keep": True}, "mcpServers": {}}), encoding="utf-8")
    spec = McpSpec(name="drawio", transport="stdio", command="npx", args=["-y", "drawio"])
    write_claude([spec])
    data = json.loads(cfg.read_text())
    assert data["otherState"] == {"keep": True}
    assert data["mcpServers"]["drawio"]["command"] == "npx"


def test_write_codex_keeps_comments(fake_home: Path) -> None:
    cfg = fake_home / ".codex/config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("# 我的注释\nmodel = \"gpt\"\n", encoding="utf-8")
    spec = McpSpec(name="synapse", transport="stdio", command="python", args=["-m", "synapse"],
                   env={"A": "1"})
    write_codex([spec])
    text = cfg.read_text()
    assert "# 我的注释" in text
    doc = tomlkit.parse(text)
    assert doc["mcp_servers"]["synapse"]["command"] == "python"
    assert doc["model"] == "gpt"


def test_write_opencode_command_array(fake_home: Path) -> None:
    cfg = fake_home / ".config/opencode/opencode.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{}", encoding="utf-8")
    spec = McpSpec(name="node", transport="stdio", command="/bin/node", args=["--repl"])
    write_opencode([spec])
    data = json.loads(cfg.read_text())
    assert data["mcp"]["node"]["command"] == ["/bin/node", "--repl"]


# ---------------------------------------------------------------------------
# sync 协调


def _mk_claude_cfg(home: Path, servers: dict) -> Path:
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return cfg


def test_sync_mcp_skip_equivalent_and_conflict(fake_home: Path) -> None:
    # claude 已有同定义 zotero（等价）与不同定义 drawio（冲突）
    _mk_claude_cfg(fake_home, {
        "zotero": {"type": "stdio", "command": "uvx", "args": ["zotero-mcp"]},
        "drawio": {"type": "stdio", "command": "OLD"},
    })
    specs = [
        McpSpec(name="zotero", transport="stdio", command="uvx", args=["zotero-mcp"]),
        McpSpec(name="drawio", transport="stdio", command="npx", args=["drawio"]),
        McpSpec(name="web-reader", transport="http", url="https://example.com"),
    ]
    lines = sync_mcp(specs, {}, ["claude", "codex"], apply=False, prefer="skip")
    text = "\n".join(lines)
    assert "claude" in text and "web-reader" in text  # 新的会写入
    assert "drawio" in text and "跳过" in text or "conflict" in text

    # apply 后：zotero 未被改写、drawio 保持 OLD（prefer=skip）、web-reader 新增
    sync_mcp(specs, {}, ["claude"], apply=True, prefer="skip")
    data = json.loads((fake_home / ".claude.json").read_text())
    assert data["mcpServers"]["drawio"]["command"] == "OLD"
    assert data["mcpServers"]["web-reader"]["url"] == "https://example.com"

    # prefer=library 时覆盖
    sync_mcp(specs, {}, ["claude"], apply=True, prefer="library")
    data = json.loads((fake_home / ".claude.json").read_text())
    assert data["mcpServers"]["drawio"]["command"] == "npx"


def test_sync_mcp_http_skip_unsupported(fake_home: Path) -> None:
    _mk_claude_cfg(fake_home, {})
    (fake_home / ".cursor").mkdir()
    (fake_home / ".cursor/mcp.json").write_text("{}")
    spec = McpSpec(name="web", transport="http", url="https://x")
    lines = sync_mcp([spec], {}, ["claude", "cursor"], apply=False, prefer="skip")
    text = "\n".join(lines)
    assert "cursor" in text and "不支持" in text
    assert "claude" in text  # claude 支持 http，会写入


def test_normalize_compare(fake_home: Path) -> None:
    assert _normalize_for_compare("opencode", {"command": ["a", "b", "c"]}) == \
        {"command": "a", "args": ["b", "c"]}
    assert _normalize_for_compare("claude", {"command": "a", "args": ["b"]}) == \
        {"command": "a", "args": ["b"]}


def test_auto_mcp_source(fake_home: Path) -> None:
    from harness_config_manager.mcp_manifest import auto_mcp_source
    # claude 有 1 个，cursor 0 个 -> 选 claude
    _mk_claude_cfg(fake_home, {"zotero": {"type": "stdio", "command": "x"}})
    assert auto_mcp_source(["claude", "cursor"]) == "claude"
    # 无任何配置时回退 claude
    assert auto_mcp_source(["cursor", "gemini"]) == "claude"


def test_plugin_provided_mcp(fake_home: Path) -> None:
    from harness_config_manager.mcp import plugin_provided_mcp

    # zcode 已启用 browser-use（宿主内置 node_repl）与 context7（.mcp.json 声明）
    cache = fake_home / ".zcode/cli/plugins/cache"
    (cache / "zcode-plugins-official/browser-use/0.3.1").mkdir(parents=True)
    (cache / "claude-plugins-official/context7/0.0.0").mkdir(parents=True)
    (cache / "claude-plugins-official/context7/0.0.0/.mcp.json").write_text(json.dumps(
        {"mcpServers": {"context7": {"type": "stdio", "command": "npx", "args": ["-y", "@upstash/context7-mcp"]}}}),
        encoding="utf-8")
    installed = fake_home / ".zcode/cli/plugins/installed_plugins.json"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_text(json.dumps({"version": 1, "plugins": [
        {"id": "browser-use@zcode-plugins-official", "marketplace": "zcode-plugins-official"},
        {"id": "context7@claude-plugins-official", "marketplace": "claude-plugins-official"},
    ]}), encoding="utf-8")
    cfg = fake_home / ".zcode/cli/config.json"
    cfg.write_text(json.dumps({"plugins": {"enabledPlugins": {
        "browser-use@zcode-plugins-official": True,
        "context7@claude-plugins-official": True}}}), encoding="utf-8")

    servers = {m.name: m for m in plugin_provided_mcp("zcode")}
    assert "node_repl" in servers                       # 宿主内置注入
    assert "context7" in servers                        # .mcp.json 声明
    assert "宿主内置注入" in servers["node_repl"].extra["via"]


def test_doctor_dead_mcp_detection(fake_home: Path) -> None:
    from harness_config_manager.doctor import run_doctor

    _mk_claude_cfg(fake_home, {
        "dead": {"type": "stdio", "command": "/no/such/binary"},
        "disabled": {"type": "stdio", "command": "/no/such/binary", "enabled": False},
        "relative": {"type": "stdio", "command": "./local/server"},
    })
    (fake_home / ".claude").mkdir()
    issues = [(lvl, where) for lvl, where, _ in run_doctor() if lvl == "error"]
    dead = [w for _, w in issues if "dead" in w]
    assert dead and all("disabled" not in w and "relative" not in w for w in dead)
