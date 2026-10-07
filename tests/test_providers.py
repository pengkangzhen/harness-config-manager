"""providers 层测试：清单往返、方言写入、detect 推断、adopt 收编、official（全部跑在 fake $HOME）。"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# fixture：按真实配置的方言结构伪造 claude env 与 codex provider 段


@pytest.fixture
def claude_settings(fake_home: Path) -> Path:
    d = fake_home / ".claude"
    d.mkdir(parents=True)
    p = d / "settings.json"
    p.write_text(json.dumps({
        "model": "opus",
        "env": {
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "sk-live-secret",
            "ANTHROPIC_MODEL": "glm-5.3-flash[1M]",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3-flash[1M]",
            "ENABLE_TOOL_SEARCH": "true",
        },
        "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "/bin/x"}]}]},
    }), encoding="utf-8")
    return p


@pytest.fixture
def codex_config(fake_home: Path) -> Path:
    d = fake_home / ".codex"
    d.mkdir(parents=True)
    p = d / "config.toml"
    p.write_text(
        "# managed by hand\n"
        'model_provider = "ZAI"\n'
        'model = "glm-5.3"\n'
        'model_reasoning_effort = "high"\n'
        'model_context_window = 1000000\n'
        "\n[model_providers.ZAI]\n"
        'name = "ZAI"\n'
        'base_url = "http://127.0.0.1:8787/api/v1"\n'
        'wire_api = "responses"\n'
        "requires_openai_auth = false\n"
        'experimental_bearer_token = "codex-live-secret"\n'
        "\n[mcp_servers.zotero]\n"
        'command = "zotero-mcp"\n',
        encoding="utf-8",
    )
    return p


def _spec(pid: str = "zhipu"):
    from halter.providers_manifest import ProviderClaude, ProviderCodex, ProviderSpec

    return ProviderSpec(
        id=pid,
        label="Zhipu GLM",
        claude=ProviderClaude(
            base_url="https://open.bigmodel.cn/api/anthropic",
            model="glm-5.3[1M]",
            env={"ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3[1M]"},
        ),
        codex=ProviderCodex(
            base_url="http://127.0.0.1:8787/api/v1",
            model="glm-5.3",
            wire_api="responses",
            reasoning_effort="high",
            context_window=1000000,
        ),
    )


# ---------------------------------------------------------------------------
# 清单往返与密钥隔离


def test_manifest_roundtrip(fake_home: Path) -> None:
    from halter.providers_manifest import load_manifest, save_manifest

    save_manifest([_spec()])
    path = fake_home / ".config/halter/providers.toml"
    text = path.read_text(encoding="utf-8")
    assert "sk-" not in text and "secret" not in text.lower()
    specs = load_manifest()
    assert len(specs) == 1
    s = specs[0]
    assert s.id == "zhipu" and s.label == "Zhipu GLM"
    assert s.claude.base_url == "https://open.bigmodel.cn/api/anthropic"
    assert s.claude.model == "glm-5.3[1M]"
    assert s.claude.env == {"ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3[1M]"}
    assert s.codex.wire_api == "responses" and s.codex.context_window == 1000000
    assert s.tools() == ["claude", "codex"]


def test_managed_env_key_predicate() -> None:
    from halter.providers_manifest import is_managed_env_key

    assert is_managed_env_key("ANTHROPIC_BASE_URL")
    assert is_managed_env_key("ANTHROPIC_DEFAULT_OPUS_MODEL")
    assert is_managed_env_key("ANTHROPIC_DEFAULT_OPUS_MODEL_NAME")
    assert is_managed_env_key("CLAUDE_CODE_SUBAGENT_MODEL")
    assert is_managed_env_key("CLAUDE_CODE_EFFORT_LEVEL")
    assert not is_managed_env_key("ENABLE_TOOL_SEARCH")
    assert not is_managed_env_key("CLAUDE_CODE_AUTO_COMPACT_WINDOW")
    assert not is_managed_env_key("max_tokens")


# ---------------------------------------------------------------------------
# claude 写入器


def test_write_claude_preserves_unmanaged(fake_home: Path, claude_settings: Path) -> None:
    from halter.providers_write import write_claude

    write_claude(_spec(), "sk-new-token")
    data = json.loads(claude_settings.read_text(encoding="utf-8"))
    env = data["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://open.bigmodel.cn/api/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-new-token"
    assert env["ANTHROPIC_MODEL"] == "glm-5.3[1M]"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "glm-5.3[1M]"
    # 旧 provider 遗留的托管键被清，非托管键与顶层 hooks 不动
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in env
    assert env["ENABLE_TOOL_SEARCH"] == "true"
    assert "hooks" in data and data["model"] == "opus"


def test_write_claude_creates_missing_settings(fake_home: Path) -> None:
    from halter.providers_write import write_claude

    write_claude(_spec(), "tok")
    path = fake_home / ".claude/settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["env"]["ANTHROPIC_BASE_URL"].startswith("https://open.bigmodel")
    assert (stat.S_IMODE(path.stat().st_mode) & 0o077) == 0


def test_write_claude_official_clears_managed_only(
        fake_home: Path, claude_settings: Path) -> None:
    from halter.providers_manifest import OFFICIAL_ID, ProviderSpec
    from halter.providers_write import write_claude

    write_claude(ProviderSpec(id=OFFICIAL_ID), None)
    data = json.loads(claude_settings.read_text(encoding="utf-8"))
    assert data["env"] == {"ENABLE_TOOL_SEARCH": "true"}
    assert "hooks" in data


# ---------------------------------------------------------------------------
# codex 写入器


def test_write_codex_preserves_comments_and_third_party(
        fake_home: Path, codex_config: Path) -> None:
    from halter.providers_write import write_codex

    write_codex(_spec(), "codex-tok")
    text = codex_config.read_text(encoding="utf-8")
    assert "# managed by hand" in text                 # 注释保留
    assert "[model_providers.ZAI]" in text              # 第三方段保留
    assert "[mcp_servers.zotero]" in text               # mcp 层不碰
    import tomllib
    doc = tomllib.loads(text)
    assert doc["model_provider"] == "halter_zhipu"
    assert doc["model"] == "glm-5.3"
    assert doc["model_providers"]["halter_zhipu"]["base_url"] == "http://127.0.0.1:8787/api/v1"
    assert doc["model_providers"]["halter_zhipu"]["experimental_bearer_token"] == "codex-tok"
    assert doc["model_providers"]["halter_zhipu"]["requires_openai_auth"] is False


def test_write_codex_removes_stale_halter_sections(fake_home: Path, codex_config: Path) -> None:
    from halter.providers_write import write_codex

    write_codex(_spec("a"), "ta")
    write_codex(_spec("b"), "tb")
    import tomllib
    doc = tomllib.loads(codex_config.read_text(encoding="utf-8"))
    assert doc["model_provider"] == "halter_b"
    assert set(doc["model_providers"]) == {"ZAI", "halter_b"}   # 旧 halter_a 段（含 token）被清


def test_write_codex_official(fake_home: Path, codex_config: Path) -> None:
    from halter.providers_manifest import OFFICIAL_ID, ProviderSpec
    from halter.providers_write import write_codex

    write_codex(_spec("a"), "ta")
    write_codex(ProviderSpec(id=OFFICIAL_ID), None)
    import tomllib
    doc = tomllib.loads(codex_config.read_text(encoding="utf-8"))
    assert "model_provider" not in doc and "model" not in doc
    assert set(doc["model_providers"]) == {"ZAI"}               # 第三方段保留
    assert doc["mcp_servers"]["zotero"]["command"] == "zotero-mcp"


# ---------------------------------------------------------------------------
# detect_current 推断


def test_detect_claude_states(fake_home: Path, claude_settings: Path) -> None:
    from halter.providers_manifest import save_manifest, set_token
    from halter.providers_write import detect_current

    spec = _spec()
    spec.claude.base_url = "https://relay.example.com/api"
    save_manifest([spec])
    set_token("zhipu", "claude", "sk-live-secret")

    # base_url + token 均匹配 -> halter
    got = detect_current("claude")
    data = json.loads(claude_settings.read_text(encoding="utf-8"))
    data["env"]["ANTHROPIC_BASE_URL"] = "https://relay.example.com/api"
    claude_settings.write_text(json.dumps(data), encoding="utf-8")
    assert got["status"] == "external"                  # 改文件前的状态
    assert got["provider"] == "Zhipu GLM"               # 外部端点命中内置预设 -> 厂商真名
    got = detect_current("claude")
    assert got == {"status": "halter", "provider": "zhipu",
                   "base_url": "https://relay.example.com/api",
                   "model": "glm-5.3-flash[1M]", "detail": "",
                   "env": {"ANTHROPIC_BASE_URL": "https://relay.example.com/api",
                           "ANTHROPIC_MODEL": "glm-5.3-flash[1M]",
                           "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3[1M]",
                           "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3-flash[1M]",
                           "ENABLE_TOOL_SEARCH": "true"}}   # 非托管键全量下发（收编整块带入）
    # 端点相同但 token 不同 -> external（同端点不同账号）
    data["env"]["ANTHROPIC_AUTH_TOKEN"] = "sk-other"
    claude_settings.write_text(json.dumps(data), encoding="utf-8")
    got = detect_current("claude")
    assert got["status"] == "external" and got["provider"] == ""   # 未知端点无真名可显示
    # 无自定义端点 -> official
    del data["env"]["ANTHROPIC_BASE_URL"]
    claude_settings.write_text(json.dumps(data), encoding="utf-8")
    assert detect_current("claude")["status"] == "official"


def test_detect_codex_states(fake_home: Path, codex_config: Path) -> None:
    from halter.providers_manifest import save_manifest
    from halter.providers_write import detect_current

    save_manifest([_spec()])
    assert detect_current("codex")["status"] == "external"
    assert detect_current("codex")["provider"] == "ZAI"          # 未知端点退回段名

    # 第三方段端点命中内置预设 -> 厂商真名（段名可能是 CC Switch 起的代号）
    codex_config.write_text(codex_config.read_text(encoding="utf-8").replace(
        'base_url = "http://127.0.0.1:8787/api/v1"',
        'base_url = "https://open.bigmodel.cn/api/codex"'), encoding="utf-8")
    got = detect_current("codex")
    assert got["status"] == "external" and got["provider"] == "Zhipu GLM"

    import tomllib
    from halter.providers_write import write_codex
    write_codex(_spec(), "codex-tok")
    got = detect_current("codex")
    assert got["status"] == "halter" and got["provider"] == "zhipu"
    assert got["base_url"] == "http://127.0.0.1:8787/api/v1"


# ---------------------------------------------------------------------------
# adopt 收编


def test_adopt_two_distinct_hosts(fake_home: Path, claude_settings: Path,
                                  codex_config: Path) -> None:
    from halter.providers_manifest import (
        PROVIDER_MANIFEST, adopt_providers, load_manifest, load_tokens,
    )

    lines = adopt_providers(apply=False)
    assert any("dry-run" in ln for ln in lines)
    assert not PROVIDER_MANIFEST().exists()

    lines = adopt_providers(apply=True)
    specs = {s.id: s for s in load_manifest()}
    # bigmodel.cn（claude）与 127.0.0.1（codex）不同注册域 -> 两个 provider
    assert "bigmodel" in specs and "local" in specs
    bm = specs["bigmodel"]
    assert bm.claude is not None and bm.claude.base_url.endswith("/anthropic")
    assert bm.claude.model == "glm-5.3-flash[1M]"
    assert bm.claude.env == {"ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3[1M]",
                             "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3-flash[1M]"}
    assert bm.codex is None
    lo = specs["local"]
    assert lo.codex is not None and lo.codex.base_url == "http://127.0.0.1:8787/api/v1"
    assert lo.codex.reasoning_effort == "high" and lo.codex.context_window == 1000000
    # token 只进 secrets.toml（0600），不进清单
    tokens = load_tokens()
    assert tokens["provider/bigmodel/claude"] == "sk-live-secret"
    assert tokens["provider/local/codex"] == "codex-live-secret"
    assert "sk-live-secret" not in PROVIDER_MANIFEST().read_text(encoding="utf-8")
    secrets_path = fake_home / ".config/halter/secrets.toml"
    assert (stat.S_IMODE(secrets_path.stat().st_mode) & 0o077) == 0


def test_adopt_same_host_merges(fake_home: Path, claude_settings: Path) -> None:
    from halter.providers_manifest import adopt_providers, load_manifest

    # codex 端点与 claude 同注册域 -> 合并为一个 provider
    d = fake_home / ".codex"
    d.mkdir(parents=True)
    (d / "config.toml").write_text(
        'model_provider = "ZAI"\nmodel = "glm-5.3"\n\n[model_providers.ZAI]\n'
        'name = "ZAI"\nbase_url = "https://open.bigmodel.cn/api/codex"\n'
        'wire_api = "responses"\n', encoding="utf-8")
    adopt_providers(apply=True)
    specs = load_manifest()
    assert len(specs) == 1
    assert specs[0].id == "bigmodel"
    assert specs[0].claude is not None and specs[0].codex is not None


def test_adopt_nothing(fake_home: Path) -> None:
    from halter.providers_manifest import adopt_providers

    lines = adopt_providers(apply=True)
    assert len(lines) == 1 and "未发现" in lines[0]


# ---------------------------------------------------------------------------
# 内置预设（借鉴 CC Switch provider presets）与切换备份


def test_presets_shape_and_add_preset(fake_home: Path) -> None:
    """预设只固化端点；--preset 作底模板与 --def 合并，模型名仍需自填。"""
    from typer.testing import CliRunner

    from halter.cli import app
    from halter.providers_manifest import PRESETS, load_manifest

    assert PRESETS["zhipu"]["codex"]["base_url"].endswith("/codex")
    assert "base_url" in PRESETS["deepseek"]["claude"]

    runner = CliRunner()
    result = runner.invoke(app, ["providers", "presets", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == len(PRESETS)
    zhipu = next(p for p in payload["presets"] if p["name"] == "zhipu")
    assert zhipu["tools"] == ["claude", "codex"]

    r = runner.invoke(app, [
        "providers", "add", "zhipu", "--tool", "claude", "--preset", "zhipu",
        "--def", '{"ANTHROPIC_MODEL": "glm-5.3"}', "--token-env", "NOPE", ])
    assert r.exit_code == 0
    spec = load_manifest()[0]
    assert spec.id == "zhipu" and spec.label == "Zhipu GLM"
    assert spec.claude.base_url == "https://open.bigmodel.cn/api/anthropic"
    assert spec.claude.model == "glm-5.3"

    # --def 覆盖预设键：端点被显式值替换
    r = runner.invoke(app, [
        "providers", "add", "relay", "--tool", "claude", "--preset", "zhipu",
        "--def", '{"ANTHROPIC_BASE_URL": "https://relay.example/api"}'])
    assert r.exit_code == 0
    assert load_manifest()[1].claude.base_url == "https://relay.example/api"

    # 无预设时 --def 自带端点（扁平 env 键，同 settings.json / ccswitch 配置JSON，
    # 白名单已放开：API_TIMEOUT_MS 等非托管键一并收进 env）；@file 读文件
    deffile = fake_home / "def.json"
    deffile.write_text(json.dumps({
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_MODEL": "deepseek-chat",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro",
        "API_TIMEOUT_MS": "3000000"}),
        encoding="utf-8")
    r = runner.invoke(app, ["providers", "add", "deepseek", "--tool", "claude",
                            "--def", f"@{deffile}"])
    assert r.exit_code == 0
    spec = next(s for s in load_manifest() if s.id == "deepseek")
    assert spec.claude.base_url == "https://api.deepseek.com/anthropic"
    assert spec.claude.model == "deepseek-chat"
    assert spec.claude.env == {"ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro",
                               "API_TIMEOUT_MS": "3000000"}

    # 未知预设 / 不含该工具块 / 坏 JSON / 端点缺失 / token 入 def 均退出码 2
    assert runner.invoke(app, [
        "providers", "add", "x", "--tool", "claude", "--preset", "nope",
        "--def", "{}"]).exit_code == 2
    assert runner.invoke(app, [
        "providers", "add", "x", "--tool", "codex", "--preset", "moonshot",
        "--def", "{}"]).exit_code == 2
    assert runner.invoke(app, [
        "providers", "add", "x", "--tool", "claude", "--def", "{oops"]).exit_code == 2
    assert runner.invoke(app, [
        "providers", "add", "x", "--tool", "claude",
        "--def", '{"ANTHROPIC_MODEL": "m"}']).exit_code == 2
    assert runner.invoke(app, [
        "providers", "add", "x", "--tool", "claude",
        "--def", '{"ANTHROPIC_BASE_URL": "u", "ANTHROPIC_AUTH_TOKEN": "sk-x"}']).exit_code == 2


def test_edit_updates_block_and_keeps_token(fake_home: Path) -> None:
    """providers edit：--def 整体替换工具块、token 不给则保留；缺失块可补充。"""
    from typer.testing import CliRunner

    from halter.cli import app
    from halter.providers_manifest import get_token, load_manifest

    runner = CliRunner()
    runner.invoke(app, [
        "providers", "add", "zhipu", "--tool", "claude", "--preset", "zhipu",
        "--def", '{"ANTHROPIC_MODEL": "glm-5.3"}', "--token-stdin",
    ], input="sk-keep\n")

    r = runner.invoke(app, [
        "providers", "edit", "zhipu", "--tool", "claude",
        "--def", json.dumps({
            "ANTHROPIC_BASE_URL": "https://relay.example/api",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3[1M]"}),
    ])
    assert r.exit_code == 0 and "更新 zhipu 的 claude 块" in r.output
    spec = load_manifest()[0]
    assert spec.claude.base_url == "https://relay.example/api"
    assert spec.claude.model is None                              # 整体替换：没写的键就是没有
    assert spec.claude.env == {"ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3[1M]"}
    assert spec.label == "Zhipu GLM"                              # 未给 --label 保持不变
    assert get_token("zhipu", "claude") == "sk-keep"              # 未给 token 保留

    # codex 块缺失 → edit 补充；list --json 行带 effort/ctx 供编辑表单预填
    r = runner.invoke(app, [
        "providers", "edit", "zhipu", "--tool", "codex",
        "--def", json.dumps({"base_url": "https://open.bigmodel.cn/api/codex",
                             "reasoning_effort": "high", "context_window": 200000}),
    ])
    assert r.exit_code == 0 and "补充 zhipu 的 codex 块" in r.output
    spec = load_manifest()[0]
    assert spec.codex.reasoning_effort == "high" and spec.codex.context_window == 200000
    # 再 edit claude：整体替换，旧 env 键消失
    r = runner.invoke(app, [
        "providers", "edit", "zhipu", "--tool", "claude",
        "--def", json.dumps({"ANTHROPIC_BASE_URL": "https://relay.example/api",
                             "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3"}),
    ])
    assert r.exit_code == 0
    assert load_manifest()[0].claude.env == {"ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3"}
    payload = json.loads(runner.invoke(app, ["providers", "list", "--json"]).output)
    zhipu = next(p for p in payload["providers"] if p["id"] == "zhipu")
    assert zhipu["claude"]["env"] == {"ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3"}
    assert zhipu["codex"]["reasoning_effort"] == "high"
    assert zhipu["codex"]["context_window"] == 200000
    assert zhipu["codex"]["wire_api"] == "responses"

    # 校验口径：未知 pid / 坏工具 / 坏 JSON / token 入 def 均退出码 2
    assert runner.invoke(app, ["providers", "edit", "nope", "--tool", "claude",
                               "--def", '{"ANTHROPIC_BASE_URL": "u"}']).exit_code == 2
    assert runner.invoke(app, ["providers", "edit", "zhipu", "--tool", "x",
                               "--def", '{"ANTHROPIC_BASE_URL": "u"}']).exit_code == 2
    assert runner.invoke(app, ["providers", "edit", "zhipu", "--tool", "claude",
                               "--def", "{oops"]).exit_code == 2
    assert runner.invoke(app, ["providers", "edit", "zhipu", "--tool", "claude",
                               "--def", '{"ANTHROPIC_BASE_URL": "u", "ANTHROPIC_AUTH_TOKEN": "sk"}']).exit_code == 2
    # 非托管键合法（白名单已放开）：edit 带入 env
    r = runner.invoke(app, ["providers", "edit", "zhipu", "--tool", "claude",
                            "--def", '{"ANTHROPIC_BASE_URL": "u", "FOO": "bar"}'])
    assert r.exit_code == 0
    assert load_manifest()[0].claude.env == {"FOO": "bar"}


def test_edit_active_provider_rewrites_live(fake_home: Path, claude_settings: Path) -> None:
    """编辑当前激活的供应商：清单保存后按新定义重写 ~/.claude/settings.json（非托管键不动）。"""
    from typer.testing import CliRunner

    from halter.cli import app

    runner = CliRunner()
    runner.invoke(app, [
        "providers", "add", "zhipu", "--tool", "claude",
        "--def", json.dumps({"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                             "ANTHROPIC_MODEL": "glm-5.3-flash[1M]"}),
        "--token-stdin",
    ], input="sk-live-secret\n")

    r = runner.invoke(app, [
        "providers", "edit", "zhipu", "--tool", "claude",
        "--def", json.dumps({"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                             "ANTHROPIC_MODEL": "glm-5.4[1M]"}),
    ])
    assert r.exit_code == 0
    assert "已按新定义重写激活配置" in r.output
    env = json.loads(claude_settings.read_text(encoding="utf-8"))["env"]
    assert env["ANTHROPIC_MODEL"] == "glm-5.4[1M]"
    assert env["ANTHROPIC_BASE_URL"] == "https://open.bigmodel.cn/api/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-live-secret"
    assert env["ENABLE_TOOL_SEARCH"] == "true"          # 非托管 env 键原样保留


def test_list_bad_manifest_toml_fails_readably(fake_home: Path) -> None:
    """清单 TOML 语法坏时给一行可读错误，绝不吐裸 traceback（S4-1）。"""
    from typer.testing import CliRunner

    from halter.cli import app

    manifest = fake_home / ".config/halter/providers.toml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("[[provider\nbroken", encoding="utf-8")

    result = CliRunner().invoke(app, ["providers", "list"])
    assert result.exit_code not in (0, None)
    message = str(result.exception or result.output)
    assert "TOML 语法错误" in message
    assert str(manifest) in message
    assert "Traceback" not in result.output


def test_switch_backs_up_target_configs(fake_home: Path, claude_settings: Path,
                                        codex_config: Path) -> None:
    """切换前把目标文件副本存入 backups/providers/（CC Switch 式安全垫）。"""
    from halter.providers_manifest import save_manifest
    from halter.providers_write import switch_provider

    save_manifest([_spec("a"), _spec("b")])
    switch_provider("a", "claude")
    switch_provider("a", "codex")
    switch_provider("b", "claude")
    backups = fake_home / ".config/halter/backups/providers"
    names = sorted(p.name for p in backups.iterdir())
    assert len(names) == 3                       # a.claude、a.codex、b.claude
    assert sum(1 for n in names if n.endswith("-claude-settings.json")) == 2
    assert sum(1 for n in names if n.endswith("-codex-config.toml")) == 1
    # 备份内容 = 切换前的原文件；最早那份保留了 fixture 里的原始 token
    claude_backups = [p for p in backups.iterdir()
                      if p.name.endswith("claude-settings.json")]
    tokens = {json.loads(p.read_text(encoding="utf-8"))["env"].get("ANTHROPIC_AUTH_TOKEN")
              for p in claude_backups}
    assert "sk-live-secret" in tokens


def test_switch_provider_end_to_end(fake_home: Path, claude_settings: Path,
                                    codex_config: Path) -> None:
    from halter.providers_manifest import save_manifest, set_token
    from halter.providers_write import detect_current, switch_provider

    save_manifest([_spec()])
    set_token("zhipu", "claude", "sk-live-secret")
    set_token("zhipu", "codex", "codex-live-secret")

    lines = switch_provider("zhipu", "claude")
    assert any("claude:" in ln for ln in lines)
    assert detect_current("claude") == {
        "status": "halter", "provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/anthropic",
        "model": "glm-5.3[1M]", "detail": "",
        "env": {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                "ANTHROPIC_MODEL": "glm-5.3[1M]",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.3[1M]",
                "ENABLE_TOOL_SEARCH": "true"}}

    switch_provider("zhipu", "codex")
    assert detect_current("codex")["provider"] == "zhipu"

    # unknown / 未声明工具块
    assert any("未找到" in ln for ln in switch_provider("nope", "claude"))
    spec_only_claude = _spec()
    spec_only_claude.codex = None
    save_manifest([spec_only_claude])
    assert any("未声明" in ln for ln in switch_provider("zhipu", "codex"))

    # official 切回：两工具均回默认，detect 报 official
    switch_provider("official", "claude")
    switch_provider("official", "codex")
    assert detect_current("claude")["status"] == "official"
    assert detect_current("codex")["status"] == "official"
