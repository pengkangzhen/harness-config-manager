"""插件层单测：manifest 读写 / zcode 镜像安装 / codex TOML / 分发协调。"""

from __future__ import annotations

import json
from pathlib import Path

import tomlkit
import pytest

from halter.plugin_sync import (
    PluginSpec,
    _install_codex,
    _install_zcode,
    load_plugin_manifest,
    save_plugin_manifest,
    sync_plugins,
)


def _mk_claude_plugin_cache(home: Path, marketplace: str, name: str, version: str) -> None:
    d = home / f".claude/plugins/cache/{marketplace}/{name}/{version}"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("{}", encoding="utf-8")


def test_zcode_mirror_install(fake_home: Path) -> None:
    _mk_claude_plugin_cache(fake_home, "claude-plugins-official", "remember", "0.8.3")

    spec = PluginSpec(plugin_id="remember@claude-plugins-official")
    line = _install_zcode(spec)
    assert "已镜像" in line and "0.8.3" in line

    # 缓存被复制
    dst = fake_home / ".zcode/cli/plugins/cache/claude-plugins-official/remember/0.8.3/plugin.json"
    assert dst.exists()

    # installed_plugins.json 登记为数组形态
    installed = json.loads(
        (fake_home / ".zcode/cli/plugins/installed_plugins.json").read_text())
    ids = [p["id"] for p in installed["plugins"]]
    assert "remember@claude-plugins-official" in ids

    # config.json 开关（嵌套在 plugins.enabledPlugins）
    cfg = json.loads((fake_home / ".zcode/cli/config.json").read_text())
    assert cfg["plugins"]["enabledPlugins"]["remember@claude-plugins-official"] is True

    # 幂等：重复安装不重复登记
    _install_zcode(spec)
    installed = json.loads(
        (fake_home / ".zcode/cli/plugins/installed_plugins.json").read_text())
    assert len([p for p in installed["plugins"]
                if p["id"] == "remember@claude-plugins-official"]) == 1


def test_zcode_mirror_without_source(fake_home: Path) -> None:
    spec = PluginSpec(plugin_id="ghost@no-such-market")
    line = _install_zcode(spec)
    assert "跳过" in line


def test_codex_enable(fake_home: Path) -> None:
    cfg = fake_home / ".codex/config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('model = "o4"\n', encoding="utf-8")
    line = _install_codex(PluginSpec(plugin_id="ctx7@openai", family="codex"))
    assert "已启用" in line
    doc = tomlkit.parse(cfg.read_text())
    assert doc["plugins"]["ctx7@openai"]["enabled"] is True
    assert doc["model"] == "o4"


def test_manifest_roundtrip(fake_home: Path) -> None:
    specs = [
        PluginSpec(plugin_id="remember@claude-plugins-official", version="0.8.3"),
        PluginSpec(plugin_id="github.copilot", family="vscode", targets=["vscode"]),
    ]
    save_plugin_manifest(specs)
    loaded = load_plugin_manifest()
    assert [s.plugin_id for s in loaded] == ["remember@claude-plugins-official", "github.copilot"]
    assert loaded[0].family == "claude"
    assert loaded[1].targets == ["vscode"]


def test_sync_plugins_plan_and_skip_installed(fake_home: Path) -> None:
    # zcode 已装 remember → 跳过；claude 未装 → plan
    zcode_installed = fake_home / ".zcode/cli/plugins/installed_plugins.json"
    zcode_installed.parent.mkdir(parents=True)
    zcode_installed.write_text(json.dumps(
        {"version": 1, "plugins": [{"id": "remember@claude-plugins-official"}]}))

    specs = [PluginSpec(plugin_id="remember@claude-plugins-official")]
    lines = sync_plugins(specs, ["claude", "zcode"], apply=False)
    text = "\n".join(lines)
    assert "已安装，跳过" in text          # zcode
    assert "claude: 安装 remember" in text  # plan（dry-run 不真装）


def test_auto_plugin_source_default(fake_home: Path) -> None:
    from halter.plugin_sync import auto_plugin_source
    assert auto_plugin_source() == "claude"  # 无配置时默认 claude


def test_vscode_only_ai_extensions(fake_home: Path, monkeypatch) -> None:
    from halter import plugins as pl

    class FakeProc:
        returncode = 0
        stdout = ("ms-python.python@2026.1.0\n"
                  "ms-toolsai.jupyter@2026.2.0\n"
                  "openai.chatgpt@1.2.3\n"
                  "nuriyev.claude-code-katex@0.1.0\n"
                  "ms-vscode.vscode-websearchforcopilot@1.0.0\n")
        stderr = ""

    monkeypatch.setattr(pl.subprocess, "run", lambda *a, **kw: FakeProc())
    got = {p.plugin_id for p in pl.read_vscode_plugins()}
    assert got == {"openai.chatgpt", "nuriyev.claude-code-katex",
                   "ms-vscode.vscode-websearchforcopilot"}


def test_plugin_source_resolution(fake_home: Path) -> None:
    """来源优先级：市场清单条目 URL > 所属市场仓库 URL；归档地址不作来源。"""
    from halter import plugins as pl

    manifest_dir = fake_home / ".claude/plugins/marketplaces/demo-market/.claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "marketplace.json").write_text(json.dumps({
        "plugins": [
            {"name": "demo", "source": {"source": "git-subdir",
                                        "url": "https://github.com/foo/demo-plugin.git"}},
            {"name": "zipped", "source": {"source": "url",
                                          "url": "https://cdn.example.com/zipped/1.0.zip"}},
            {"name": "relative", "source": "./plugins/relative"},
        ]
    }), encoding="utf-8")
    (fake_home / ".claude/plugins/known_marketplaces.json").write_text(json.dumps({
        "demo-market": {"source": {"source": "github", "repo": "anthropics/demo-market"}},
    }), encoding="utf-8")
    (fake_home / ".claude/plugins/installed_plugins.json").write_text(json.dumps({
        "plugins": {
            "demo@demo-market": [{"version": "1.0.0"}],
            "zipped@demo-market": [{"version": "1.0.0"}],
            "relative@demo-market": [{"version": "1.0.0"}],
            "unknown@demo-market": [{"version": "1.0.0"}],
        }
    }), encoding="utf-8")

    got = {p.plugin_id: p.source_url for p in pl.read_claude_plugins()}
    market_repo = "https://github.com/anthropics/demo-market"
    assert got["demo@demo-market"] == "https://github.com/foo/demo-plugin.git"
    assert got["zipped@demo-market"] == market_repo       # 归档被过滤 → 市场回退
    assert got["relative@demo-market"] == market_repo     # 相对 source → 市场回退
    assert got["unknown@demo-market"] == market_repo


def test_zcode_list_form_marketplace_repo(fake_home: Path) -> None:
    """zcode 数组形态 installed + 数组形态 known_marketplaces 的市场仓库回退。"""
    from halter import plugins as pl

    zdir = fake_home / ".zcode/cli/plugins"
    zdir.mkdir(parents=True)
    (zdir / "installed_plugins.json").write_text(json.dumps(
        {"plugins": [{"id": "plug@demo-market", "version": "0.1.0", "marketplace": "demo-market"}]}))
    (zdir / "known_marketplaces.json").write_text(json.dumps(
        {"version": 1, "marketplaces": [
            {"id": "demo-market", "source": {"source": "github", "repo": "octocat/demo"}},
            {"id": "cdn-market", "source": {"source": "url", "url": "https://cdn.example.com/m.json"}},
        ]}))

    got = pl.read_zcode_plugins()
    assert got[0].source_url == "https://github.com/octocat/demo"


def test_vscode_source_is_marketplace_page(fake_home: Path, monkeypatch) -> None:
    from halter import plugins as pl

    class FakeProc:
        returncode = 0
        stdout = "openai.chatgpt@1.2.3\n"
        stderr = ""

    monkeypatch.setattr(pl.subprocess, "run", lambda *a, **kw: FakeProc())
    got = pl.read_vscode_plugins()
    assert got[0].source_url == \
        "https://marketplace.visualstudio.com/items?itemName=openai.chatgpt"


def test_zcode_plugin_invalid_config_is_not_reset(fake_home: Path) -> None:
    from halter.plugin_sync import PluginSpec, _install_zcode

    path = fake_home / ".zcode/cli/config.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    cache = fake_home / ".claude/plugins/cache/market/demo/1.10.0"
    cache.mkdir(parents=True)
    with pytest.raises(ValueError):
        _install_zcode(PluginSpec("demo@market", targets=["zcode"]))
    assert path.read_text(encoding="utf-8") == "{broken"
