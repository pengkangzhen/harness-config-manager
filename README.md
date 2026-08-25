# harness-config-manager (`hcm`)

Detect, inventory, and distribute user-level **skills / MCP servers / plugins** across all your AI coding harnesses — from a single source of truth.

[中文文档](README.zh-CN.md)

## Why

A serious AI-assisted developer typically runs several coding harnesses side by side — Claude Code, Codex, Cursor, VS Code Copilot, Gemini CLI, OpenCode, ZCode… Each one keeps its **own** user-level skills, MCP server configs, and plugins, in its own format (JSON with `mcpServers` vs `servers` vs `.mcp`, TOML `[mcp_servers.*]`, array-style commands…). They drift apart silently: a skill lands in one tool but never reaches the others, an MCP server gets registered twice with different definitions, a config pointing at a deleted project keeps failing on every startup.

`hcm` treats those three layers as one managed state:

- **Detect** which harnesses are installed (CLI presence + config directories, 13 tools known).
- **Inventory** what each one has — as coverage matrices (tool × skill, tool × MCP server, tool × plugin), plus health checks (broken symlinks, unparseable configs, dead MCP entries whose command no longer exists, unresolved secret placeholders).
- **Distribute** from a single source of truth to every tool: skills as per-entry symlinks, MCP definitions translated across six config dialects, plugins installed via each family's native mechanism.

## How it compares

| | **hcm** | [nesjett/acm](https://github.com/nesjett/agent-config-manager) | [agentsync](https://github.com/dallay/agentsync) | [AgentsMesh](https://samplexbro.github.io/agentsmesh/) | [Bridle](https://github.com/neiii/bridle) |
|---|---|---|---|---|---|
| Core model | single source of truth → all tools, repeatable | point-to-point copy `--from A --to B` | symlink sync | single `.agentsmesh` dir convention | install from GitHub repos |
| Scope | **user-level** global config | project-level files (`CLAUDE.md`, `.cursorrules`…) | user-level | user-level | user-level |
| Layers | skills + MCP + **plugins** | instructions, rules, skills, MCP | config + MCP | rules + MCP + skills | skills + agents + commands + MCP |
| Detection / inventory / health checks | ✅ (13 tools, coverage matrices, doctor) | ❌ | ❌ | ❌ | ❌ |
| Secret handling | `${VAR}` placeholders + 0600 secrets file, redacted output | ❌ | ❌ | ❌ | ❌ |
| Tech | Python + uv | TypeScript (Deno) | Rust | — | Rust |

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```bash
uv tool install harness-config-manager
```

Or from source:

```bash
git clone https://github.com/pengkangzhen/harness-config-manager.git
uv tool install --editable ./harness-config-manager
```

## Quick start

Two commands. That's it.

```bash
hcm scan                   # look: which tools are installed, what each has, any health issues
hcm scan -d skills -d mcp  # per-layer detail; --json for machine-readable output

hcm sync                   # sync: manifest/library -> all tools (dry-run by default)
hcm sync --apply           # actually write (backs up before replacing; conflicts skipped by default)
hcm sync --no-skills --no-plugins   # only the MCP layer (all three layers on by default)
hcm sync --prefer library  # on conflict, back up the tool-side copy and override from the manifest
```

When a manifest is empty, `sync` auto-collects from your best-equipped tool (the one with the most MCP servers / most enabled plugins; override with `--from`). Skills that exist nowhere in the library are adopted automatically; same-name divergent copies are reported for you to adjudicate.

## The three layers

| Layer | Source of truth | Distribution |
|---|---|---|
| skills | skills library dir (fallback chain: config-specified > `~/.agents/skills` > `~/.config/hcm/library/skills`) | per-entry symlinks; same-name conflicts skipped by default, `--prefer library` to override |
| MCP | `~/.config/hcm/mcp.toml` (canonical: stdio/http, env, headers) | six dialect writers: claude (read-modify-write of `~/.claude.json`), zcode, codex (TOML, comments preserved), cursor, vscode (`servers` key), gemini, opencode (array-style command); http-type servers only go to tools that support them |
| plugins | `~/.config/hcm/plugins.toml` (families: claude / codex / vscode) | claude via `claude plugin install -y`; zcode mirrors the claude-side cache + registers the same-origin manifest; codex via TOML toggle; vscode via `code --install-extension` |

## Safety design

- **Every write is dry-run by default**; `--apply` is required. Replacements are backed up to `~/.config/hcm/backups/<timestamp>/`.
- **Secrets never enter the manifest**: values that look like keys become `${VAR}` placeholders; real values live in `~/.config/hcm/secrets.toml` (mode 0600). At sync time placeholders resolve from the environment first, then the secrets file; a server with an unresolvable secret is skipped entirely, never half-written. Terminal output is always redacted.
- `~/.claude.json` (a large state file) is only ever read-modify-written key-by-key, atomically.

## Configuration

`~/.config/hcm/config.toml` (optional):

```toml
library = "~/.agents/skills"     # skills source of truth; defaults to the fallback chain
exclude_skills = []              # skills to never distribute
exclude_mcp = []
```

## Supported tools

Claude Code · ZCode · OpenAI Codex · Cursor · VS Code (Copilot) · Gemini CLI · OpenCode · GitHub Copilot CLI · Continue · Cline · Trae · Aider Desktop · Windsurf

Adding a tool = one `ToolSpec` entry in `registry.py` (detection + skills layer work immediately); MCP/plugin support needs the tool's dialect reader/writer.

## Limitations (v1)

- Cursor plugins are inventoried but not installed (marketplace mechanism is opaque).
- Continue / Cline / Trae / Aider / Windsurf are covered on the skills layer only.
- The zcode plugin target requires the plugin to be installed on the claude side first (mirror source).
- MCP servers injected by a harness at runtime for its own plugins (e.g. ZCode's `node_repl` via browser-use) rely on a small built-in mapping table.

## Development

```bash
uv sync
uv run pytest               # 31 tests, all against a fake $HOME — never touches your real config
```

## License

MIT
