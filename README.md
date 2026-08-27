# harness-config-manager (`hcm`)

Detect, inventory, and distribute user-level **skills / MCP servers / plugins / hooks / subagents** across all your AI coding harnesses — from a single source of truth.

[中文文档](README.zh-CN.md)

## Why

A serious AI-assisted developer typically runs several coding harnesses side by side — Claude Code, Codex, Cursor, VS Code Copilot, Gemini CLI, OpenCode, ZCode… Each one keeps its **own** user-level skills, MCP server configs, plugins, hooks (commands that run automatically before/after specific agent actions), and subagents (per-agent Markdown definitions), in its own format (JSON with `mcpServers` vs `servers` vs `.mcp`, TOML `[mcp_servers.*]`, array-style commands…). They drift apart silently: a skill lands in one tool but never reaches the others, an MCP server gets registered twice with different definitions, a config pointing at a deleted project keeps failing on every startup.

`hcm` treats those five layers as one managed state:

- **Detect** which harnesses are installed (CLI presence + config directories, 13 tools known).
- **Inventory** what each one has — as coverage matrices (tool × skill, tool × MCP server, tool × plugin, tool × hook, tool × subagent), plus health checks (broken symlinks, unparseable configs, dead MCP entries whose command no longer exists, unresolved secret placeholders).
- **Distribute** from a single source of truth to every tool: skills and subagents as per-entry symlinks, MCP definitions translated across six config dialects, plugins installed via each family's native mechanism, hooks translated across three dialects (claude / zcode / cursor).

## How it compares

| | **hcm** | [nesjett/acm](https://github.com/nesjett/agent-config-manager) | [agentsync](https://github.com/dallay/agentsync) | [AgentsMesh](https://samplexbro.github.io/agentsmesh/) | [Bridle](https://github.com/neiii/bridle) |
|---|---|---|---|---|---|
| Core model | single source of truth → all tools, repeatable | point-to-point copy `--from A --to B` | symlink sync | single `.agentsmesh` dir convention | install from GitHub repos |
| Scope | **user-level** global config | project-level files (`CLAUDE.md`, `.cursorrules`…) | user-level | user-level | user-level |
| Layers | skills + MCP + plugins + hooks + **subagents** | instructions, rules, skills, MCP | config + MCP | rules + MCP + skills | skills + agents + commands + MCP |
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
hcm sync --apply           # actually write (conflicts skipped by default)
hcm sync --no-skills --no-plugins --no-mcp   # only the hooks layer (all five layers on by default)
hcm sync --prefer library  # on conflict, override the tool-side copy from the manifest
```

When a manifest is empty, `sync` auto-collects from your best-equipped tool (the one with the most MCP servers / most enabled plugins / most hooks; override with `--from`). Third-party-injected hook entries are adopted too — keep specific ones out via `exclude_hooks`. Skills that exist nowhere in the library are adopted automatically; same-name divergent copies are reported for you to adjudicate.

## The five layers

| Layer | Source of truth | Distribution |
|---|---|---|
| skills | skills library dir (fallback chain: config-specified > `~/.agents/skills` > `~/.config/hcm/library/skills`) | per-entry symlinks; same-name conflicts skipped by default, `--prefer library` to override |
| subagents | subagents library dir (fallback chain: config `agents_library` > `~/.agents/agents` > `~/.config/hcm/library/agents`) | per-entry symlinks (each agent is one `.md` file); same conflict semantics as skills; frontmatter (`model: opus`, …) is distributed as-is — aliases may not resolve in non-Claude-family tools |
| MCP | `~/.config/hcm/mcp.toml` (canonical: stdio/http, env, headers) | six dialect writers: claude (read-modify-write of `~/.claude.json`), zcode, codex (TOML, comments preserved), cursor, vscode (`servers` key), gemini, opencode (array-style command); http-type servers only go to tools that support them |
| plugins | `~/.config/hcm/plugins.toml` (families: claude / codex / vscode) | claude via `claude plugin install -y`; zcode mirrors the claude-side cache + registers the same-origin manifest; codex via TOML toggle; vscode via `code --install-extension` |
| hooks | `~/.config/hcm/hooks.toml` (per registration: id, events, matcher, command, timeout in seconds) | three dialect writers: claude (top-level `hooks` of `settings.json`), zcode (`hooks.events` in `cli/config.json`, timeouts converted ms→s), cursor (flat two-level `hooks.json`); every entry hcm writes carries an `"hcm": "<id>"` ownership tag — **entries without it (injected by Otty, Orca, …) are never touched**; tool-unsupported events (cursor-only like `beforeShellExecution`, claude-only like `PermissionRequest`) are skipped with a note |

## Safety design

- **Every write is dry-run by default**; `--apply` is required. Replacements are backed up to `~/.config/hcm/backups/<timestamp>/`.
- **Secrets never enter the manifest**: values that look like keys become `${VAR}` placeholders; real values live in `~/.config/hcm/secrets.toml` (mode 0600). At sync time placeholders resolve from the environment first, then the secrets file; a server with an unresolvable secret is skipped entirely, never half-written. Terminal output is always redacted.
- `~/.claude.json` (a large state file) is only ever read-modify-written key-by-key, atomically.
- Hook sync only ever touches entries carrying hcm's own `hcm` ownership tag — hooks written by third-party managers (Otty, Orca, …) are never modified or deleted; zcode's global `hooks.enabled` switch stays yours, hcm won't toggle it.

## Configuration

`~/.config/hcm/config.toml` (optional):

```toml
library = "~/.agents/skills"     # skills source of truth; defaults to the fallback chain
exclude_skills = []              # skills to never distribute
exclude_mcp = []
exclude_hooks = []               # hook ids to never distribute (see hooks.toml / scan -d hooks)
agents_library = ""              # subagents source of truth; defaults to the fallback chain
exclude_agents = []              # agent names to never distribute
```

## Supported tools

Claude Code · ZCode · OpenAI Codex · Cursor · VS Code (Copilot) · Gemini CLI · OpenCode · GitHub Copilot CLI · Continue · Cline · Trae · Aider Desktop · Windsurf

Adding a tool = one `ToolSpec` entry in `registry.py` (detection + skills layer work immediately); MCP/plugin support needs the tool's dialect reader/writer; hooks likewise live in `hooks.py` / `hooks_write.py`; subagents need just an `agents_dirs` entry.

## Limitations (v1)

- Cursor plugins are inventoried but not installed (marketplace mechanism is opaque).
- Continue / Cline / Trae / Aider / Windsurf are covered on the skills layer only.
- The zcode plugin target requires the plugin to be installed on the claude side first (mirror source).
- The hooks layer covers claude / zcode / cursor only (Codex has just a weak `notify` callback; the other tools have no equivalent); Cursor's prompt-type hooks distribute to cursor only.
- The subagents layer covers claude / zcode / cursor only; frontmatter fields like `model: opus` are Claude-family aliases and are distributed as-is (they may not resolve elsewhere).
- Dead-hook detection is conservative: existence is only checked for commands that are a single script path; compound shell expressions are neither checked nor false-positived.
- MCP servers injected by a harness at runtime for its own plugins (e.g. ZCode's `node_repl` via browser-use) rely on a small built-in mapping table.

## Development

```bash
uv sync
uv run pytest               # 57 tests, all against a fake $HOME — never touches your real config
```

## License

MIT
