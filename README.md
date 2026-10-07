# halter

Detect, inventory, and distribute user-level **skills / MCP servers / plugins / hooks / subagents / global memory (CLAUDE.md, AGENTS.md…)**, and recall project-scoped **session history** across all your AI coding harnesses — from a single source of truth.

[中文文档](README.zh-CN.md)

## Why

A serious AI-assisted developer typically runs several coding harnesses side by side — Claude Code, Codex, Cursor, VS Code Copilot, Gemini CLI, OpenCode, ZCode… Each one keeps its **own** user-level skills, MCP server configs, plugins, hooks (commands that run automatically before/after specific agent actions), subagents (per-agent Markdown definitions), global memory files (CLAUDE.md / AGENTS.md / GEMINI.md — the instructions every session starts from, which drift apart the fastest), and session transcripts, in its own format (JSON with `mcpServers` vs `servers` vs `.mcp`, TOML `[mcp_servers.*]`, array-style commands…). They drift apart silently: a skill lands in one tool but never reaches the others, an MCP server gets registered twice with different definitions, a config pointing at a deleted project keeps failing on every startup.

`halter` treats those six layers as one managed state, plus a read-only session-continuity layer:

- **Detect** which harnesses are installed (CLI presence + config directories, 13 tools known).
- **Inventory** what each one has — as coverage matrices (tool × skill, tool × MCP server, tool × plugin, tool × hook, tool × subagent, tool × memory), plus health checks (broken symlinks, unparseable configs, dead MCP entries whose command no longer exists, unresolved secret placeholders).
- **Distribute** from a single source of truth to every tool: skills and subagents as per-entry symlinks, global memory as one symlinked file (edit anywhere, consistent everywhere), MCP definitions translated across six config dialects, plugins installed via each family's native mechanism, hooks translated across three dialects (claude / zcode / cursor).
- **Continue work across harnesses**: list every assistant's sessions for the current project, inspect one transcript on demand, and generate a deterministic handoff without rewriting vendor session stores.

## How it compares

| | **halter** | [nesjett/acm](https://github.com/nesjett/agent-config-manager) | [agentsync](https://github.com/dallay/agentsync) | [AgentsMesh](https://samplexbro.github.io/agentsmesh/) | [Bridle](https://github.com/neiii/bridle) |
|---|---|---|---|---|---|
| Core model | single source of truth → all tools, repeatable | point-to-point copy `--from A --to B` | symlink sync | single `.agentsmesh` dir convention | install from GitHub repos |
| Scope | **user-level** global config | project-level files (`CLAUDE.md`, `.cursorrules`…) | user-level | user-level | user-level |
| Layers | skills + MCP + plugins + mods + hooks + **subagents** + memory + statusline + read-only project sessions | instructions, rules, skills, MCP | config + MCP | rules + MCP + skills | skills + agents + commands + MCP |
| Detection / inventory / health checks | ✅ (13 tools, coverage matrices, doctor) | ❌ | ❌ | ❌ | ❌ |
| Multi-machine | ✅ ssh: auto-discover ~/.ssh/config hosts, remote scan/sync, per-entry push/pull | ❌ | ❌ | ❌ | ❌ |
| Secret handling | `${VAR}` placeholders + 0600 secrets file, redacted output | ❌ | ❌ | ❌ | ❌ |
| Tech | Python + uv | TypeScript (Deno) | Rust | — | Rust |

## Screenshots

**Overview** — every detected harness with its five-layer counts, plus doctor health checks:

![Overview](docs/images/app-overview-en.png)

**Config matrix** — harness × skills / subagents / MCP / plugins / mods / hooks coverage at a glance (skill families collapse into one row, expandable; mods = plugins with in-process JS/TS event handlers, inventoried read-only — installs ride the plugins layer). Click any not-yet-synced dot to sync just that entry to that tool (CLI equivalent: `halter sync --tool <tool> --item <name> --apply`; clicking a family row's dot syncs the whole family). The toolbar also carries the full sync: preview the dry-run plan or apply everything (sessions-skill layer toggleable) with a two-step confirm:

![Config matrix](docs/images/app-matrix-en.png)

**Cross-assistant sessions** — unified project / harness / timeline facets, full-text search, redacted transcripts, and one-click handoff generation:

![Session browser](docs/images/app-sessions-en.png)

## Install

Requires Python 3.12+. The PyPI distribution is **`halter-cli`** (the bare `halter` name was taken by an unrelated project) — it still installs the `halter` command:

```bash
pipx install halter-cli        # or: uv tool install halter-cli
```

Until the first PyPI release propagates, install straight from the repository:

```bash
uv tool install git+https://github.com/pengkangzhen/harness-config-manager.git
```

**Desktop app (Windows)**: NSIS installer + portable exe are attached to each [GitHub release](https://github.com/pengkangzhen/harness-config-manager/releases). macOS/Linux desktop builds arrive in a later release; the CLI covers those platforms fully.

**Platform support**: the CLI is a first-class citizen on macOS and Linux. On Windows, the core distribution mechanism (per-entry symlinks) requires developer mode and is not yet tested — treat Windows as desktop-app-only for now. The `library` channel's remote takeover needs a POSIX remote (macOS/Linux); Windows remotes are not supported.

## Quick start

Three commands. That's it.

```bash
halter scan                   # look: which tools are installed, what each has, any health issues
halter scan -d skills -d mcp  # per-layer detail; --json for machine-readable output

halter assess                 # assess per-layer usefulness against the current project
halter assess -p ../my-paper  # any project; -l skills -l mcp to pick layers; --json supported

halter sync                   # sync: manifest/library -> all tools (dry-run by default)
halter sync --apply           # actually write (conflicts skipped by default)
halter sync --no-skills --no-plugins --no-mcp   # only hooks + sessions (all layers on by default)
halter sync --prefer library  # on conflict, override the tool-side copy from the manifest
halter sync --tool zcode --item paper-polishing --apply   # narrow to one matrix cell (what a dot click does)

halter push skills paper-polishing --to desktop --apply   # one entry, this machine -> another machine
halter pull mcp zotero --from lab --apply                 # ...or the other way round
halter push statusline claude --to desktop --apply        # a tool's statusline fragment + script, ditto

halter sessions list          # current project's sessions across local AI coding assistants
halter sessions install       # distribute the halter-sessions lookup skill (dry-run)
halter sessions install --apply
```

The `sessions` layer only installs a lookup skill. It never copies, rewrites, or "adopts" session transcripts. When a manifest is empty, `sync` auto-collects from your best-equipped tool (the one with the most MCP servers / most enabled plugins / most hooks; override with `--from`). Third-party-injected hook entries are adopted too — keep specific ones out via `exclude_hooks`. Skills that exist nowhere in the library are adopted automatically; same-name divergent copies are reported for you to adjudicate.

## Multiple machines (ssh)

The same drift problem exists *between* machines: the desktop runs Claude Code, the laptop runs Claude Code too, and the two sides silently diverge. halter addresses this over plain ssh — the remote machine only needs halter installed and passwordless ssh (public key / agent); no passwords are ever stored.

**Zero-registration, VS Code Remote-SSH style**: every non-wildcard `Host` alias in `~/.ssh/config` (Include directives followed recursively) is automatically a machine — connection parameters (HostName / User / Port / ProxyJump) are resolved by ssh itself. `halter machines add` remains available to override an alias or pin a custom `halter_path`:

```bash
halter machines list             # manual (machines.toml) + auto-discovered (~/.ssh/config)
halter machines test desktop     # connectivity + remote halter check, with diagnostics

# Inspect and operate a remote machine exactly like the local one
halter scan --machine desktop --json
halter sync --machine desktop --apply
halter sessions list --machine desktop --all-projects   # same for memory / providers
halter memory show --machine desktop
halter providers list --machine desktop

# Move a single entry between machines (skills / agents / mcp / hooks)
halter push skills paper-polishing --to desktop --apply
halter push mcp zotero --to desktop --with-secrets --apply   # carry real key values too (ssh-encrypted stdin)
halter push hooks my-gate --to desktop --apply               # warns if the command embeds machine-local paths
halter pull agents reviewer --from desktop --apply           # = push --from desktop, target is this machine
```

Semantics: entries are transferred through an idempotent export/ingest pair — same name and identical content is a no-op; divergent same-name entries are reported as conflicts (skip by default, `--prefer replace` backs up the destination side first, then overwrites). On arrival the entry lands in the remote library/manifest **and** is distributed to that machine's installed tools by its own halter — dialect translation never happens twice. MCP definitions travel with `${VAR}` placeholders; real secret values only move when `--with-secrets` is given, and they merge into the remote `secrets.toml` (0600). The memory layer is deliberately not pushed — a single global memory file has merge semantics that belong to git/syncthing, not point-to-point copies.

**Zero-install remotes** — the library channel. The push/pull commands above need halter on both ends. For machines where you don't want to install anything, the library channel moves the whole `~/.agents` truth (skills / subagents / memory / statusline) over plain ssh + git bundles, and "adopts" the remote with nothing but POSIX sh and python3 (both ship with macOS/Linux dev machines):

```bash
halter library init --apply            # git-ify ~/.agents once (idempotent, snapshots current state)
halter library adopt mac --apply       # remote: build the library from this machine's + take over statusline
                                       # (settings' statusLine.command -> ~/.agents/... direct path; the
                                       #  remote's old ~/.agents is tar-backed-up first — data is never lost)
halter library push --to mac --apply   # the daily command: commit local changes, ship, refresh the takeover
halter library pull --from mac --apply # remote-edited library content comes back (fast-forward)
```

Editing `~/.agents/statusline/claude/statusline.py` on either machine and pushing makes both statuslines change — the direct-path form is also what halter's adopter expects (`~` is expanded), so a remote can switch to full halter management later with zero migration. Divergent histories fail fast with a pull hint instead of silently overwriting. Declarative statusline dialects (codex / gemini / kimi) still need real halter on the remote to write their config files.

The desktop app rides on the same channel, machine-first: a machine picker at the top of the sidebar fixes the scope, and all five views — overview, matrix, sessions, memory, providers — load under the selected machine (remote requests are forwarded over ssh to that machine's halter). The choice persists across launches. Inside the matrix, entries that exist locally but are missing remotely appear as ghost rows (click to push), and remote-only entries get a pull button.

## Session continuity

Switching from Claude Code to Codex (or any other assistant) should not erase the project's working history.

```bash
# From any assistant with shell access, or after installing halter-sessions:
halter sessions list --project . --json

# Read only the selected session, never ingest every transcript:
halter sessions show claude:<session-id> --transcript --tail 80

# Produce a private, deterministic, redacted handoff:
halter sessions context claude:<session-id>
halter sessions context claude:<session-id> --output .halter/HANDOFF.md

# Search current-project history:
halter sessions search "authentication migration" --project .
```

Native metadata readers cover Claude Code, ZCode, Codex CLI, OpenCode, and Pi. If [ctx](https://ctx.rs) is installed and initialized, `halter sessions list` also includes ctx-indexed providers (Cursor, Gemini CLI, Copilot CLI, Continue, and many more) without duplicating native entries. `ctx` is optional; halter invokes only its read-only `list events` and `show session` surfaces.

The built-in `halter-sessions` skill teaches every detected assistant the same lookup workflow. `halter sync --sessions` (on by default) or `halter sessions install --apply` distributes it through the normal skills library. Transcripts are read only when `--transcript`, `search`, or `context` is explicitly requested; obvious credentials are redacted, and prior commands are presented as historical evidence rather than executable instructions.

## Model provider switching

Claude Code and Codex can run against any Anthropic-/OpenAI-compatible endpoint (GLM, DeepSeek, local gateways, relays…), but the wiring lives in different files per tool. halter keeps a provider manifest (`~/.config/halter/providers.toml`) and switches a tool's active provider in one command. Providers are **machine-local by design** — they are identities, not distributable config, so `push`/`pull` never carries them.

```bash
# Adopt what already exists (e.g. a CC Switch setup) into the manifest:
halter providers adopt --apply

# List providers + each tool's current provider (probed from the real files):
halter providers list

# Add one from a built-in preset (its endpoint merges under --def; the model
# is yours to pick) and switch. For claude, --def is the flat managed env of
# settings.json (ANTHROPIC_* / CLAUDE_CODE_*) — the same shape as CC Switch's
# "配置JSON", so existing config blocks paste straight in. The token goes to
# secrets.toml (0600), never the manifest — tokens are deliberately absent
# from the def JSON (keeps secrets out of argv):
echo "$KEY" | halter providers add zhipu --tool claude --preset zhipu \
  --def '{"ANTHROPIC_MODEL": "glm-5.3", "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.3[1M]"}' \
  --token-stdin
halter providers switch zhipu --tool claude

# Back to vendor defaults:
halter providers switch official --tool codex

# Edit a provider in place (the def JSON wholly replaces the tool block —
# keys absent from the JSON are removed; editing the active provider
# rewrites the tool's live config from the new definition):
halter providers edit zhipu --tool claude \
  --def '{"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic", "ANTHROPIC_MODEL": "glm-5.3[1M]"}'
```

Built-in presets (`halter providers presets`) currently cover zhipu (claude + codex endpoints), deepseek and moonshot — each endpoint backed by the vendor's own docs; only endpoints are pinned, model names always stay yours. Every switch first copies the target file to `~/.config/halter/backups/providers/`, so a bad switch is one file copy away from undone.

Write scope: on the claude side, cleanup only targets the managed `ANTHROPIC_*` / `CLAUDE_CODE_*` env keys of `~/.claude/settings.json` (switching away never deletes anything else), while `--def` / the config JSON accepts **any** env key-value pair (`API_TIMEOUT_MS` etc. enter the manifest and are written on switch); codex gets `model_provider` / `model` plus a `[model_providers.halter_<id>]` section while third-party sections are preserved verbatim. `doctor` flags external endpoints not in the manifest and cc-switch leftovers, so dual management can't hide. The desktop app has a Providers panel with one-click switching, a CC Switch-style config-JSON editor (live validation, presets merge their endpoint in; pasting a config block that includes a token strips it into secrets.toml automatically), in-place editing, double-click adoption, and removal.

**Where this sits relative to CC Switch / claude-code-router**: halter is the *static configuration layer* — it writes which provider a tool points at, keeps tokens safe, and never runs a daemon. CC Switch does the same switching job as a standalone app (halter `adopt` can ingest its config). [claude-code-router](https://github.com/musistudio/claude-code-router) is a *runtime routing layer* — a resident gateway doing per-request routing, fallback and observability. The two layers compose: point a halter provider entry at a local gateway endpoint and switch to it like any other provider.

## The seven managed config layers

| Layer | Source of truth | Distribution |
|---|---|---|
| skills | skills library dir (default `~/.agents/skills`; config `library` overrides; legacy `~/.config/halter/library/skills` is auto-migrated once) | per-entry symlinks; same-name conflicts skipped by default, `--prefer library` to override |
| subagents | subagents library dir (default `~/.agents/agents`; config `agents_library` overrides; legacy path auto-migrated once) | per-entry symlinks (each agent is one `.md` file); same conflict semantics as skills; frontmatter (`model: opus`, …) is distributed as-is — aliases may not resolve in non-Claude-family tools |
| memory | one memory file (default `~/.agents/memory/MEMORY.md`; config `memory_file` overrides) | symlinked to each tool's user-level memory file (claude `~/.claude/CLAUDE.md`, zcode/codex/opencode `AGENTS.md`, pi `~/.pi/agent/AGENTS.md`, gemini `GEMINI.md`); editing on any tool side edits the library, so copies can't drift; an empty library is auto-adopted from tool side (divergent copies need `--from <tool>`); same conflict semantics as skills |
| statusline | statusline library dir (default `~/.agents/statusline`; config `statusline_library` overrides): `manifest.json` holds one **fragment per tool** (the shapes are not inter-translatable), plus per-tool script dirs — claude/zcode/cursor `{"statusLine": {...command path as a `{script}` placeholder...}, "script": "statusline.py"}`, qwen the same nested under `ui`, codex `{"tui": {"status_line": [...], "status_line_use_colors": bool}}`, gemini `{"footer": {"items": [...]}}`, kimi `{"status_line": {"items": [...], "command": "… {script} …"}}` | script families (claude / zcode / cursor / qwen): script symlinked into the tool's config dir and the `statusLine` region rendered with that per-machine absolute path (read-modify-write, whole file backed up first) — the embedded absolute path is exactly why statuslines drift between machines, and the placeholder rendering is what fixes it; declarative families: codex `[tui]` managed keys (`status_line` / `status_line_use_colors`) via tomlkit read-modify-write (theme/pet/keymap never touched), gemini `ui.footer` region only, kimi `[status_line]` mixed (items + command); fragments are adopted per tool (single source each, no cross-tool conflict); a divergent tool-side script conflicts (skip by default, `--prefer library` to override); commands with no local script (`npx ccstatusline`) sync the settings key only |
| MCP | `~/.config/halter/mcp.toml` (canonical: stdio/http, env, headers) | eight dialect writers: claude (read-modify-write of `~/.claude.json`), zcode, codex (TOML, comments preserved), cursor, vscode (`servers` key), gemini, opencode (array-style command), pi (`~/.pi/agent/mcp.json`); http-type servers only go to tools that support them |
| plugins | `~/.config/halter/plugins.toml` (families: claude / codex / vscode) | claude via `claude plugin install -y`; zcode mirrors the claude-side cache + registers the same-origin manifest; codex via TOML toggle; vscode via `code --install-extension` |
| hooks | `~/.config/halter/hooks.toml` (per registration: id, events, matcher, command, timeout in seconds) | three dialect writers: claude (top-level `hooks` of `settings.json`), zcode (`hooks.events` in `cli/config.json`, timeouts converted ms→s), cursor (flat two-level `hooks.json`); every entry halter writes carries an `"halter": "<id>"` ownership tag — **entries without it (injected by Otty, Orca, …) are never touched**; tool-unsupported events (cursor-only like `beforeShellExecution`, claude-only like `PermissionRequest`) are skipped with a note |

## Safety design

- **Every write is dry-run by default**; `--apply` is required. Replacements are backed up to `~/.config/halter/backups/<timestamp>/`.
- **Secrets never enter the manifest**: values that look like keys become `${VAR}` placeholders; real values live in `~/.config/halter/secrets.toml` (mode 0600). At sync time placeholders resolve from the environment first, then the secrets file; a server with an unresolvable secret is skipped entirely, never half-written. Terminal output is always redacted.
- `~/.claude.json` (a large state file) is only ever read-modify-written key-by-key, atomically.
- Hook sync only ever touches entries carrying halter's own `halter` ownership tag — hooks written by third-party managers (Otty, Orca, …) are never modified or deleted; zcode's global `hooks.enabled` switch stays yours, halter won't toggle it.

## Configuration

`~/.config/halter/config.toml` (optional):

```toml
library = "~/.agents/skills"     # skills source of truth; this path is also the default
exclude_skills = []              # skills to never distribute
exclude_mcp = []
exclude_hooks = []               # hook ids to never distribute (see hooks.toml / scan -d hooks)
agents_library = ""              # subagents source of truth; defaults to ~/.agents/agents
exclude_agents = []              # agent names to never distribute
memory_file = ""                 # user-level memory source of truth; defaults to ~/.agents/memory/MEMORY.md
statusline_library = ""          # statusline source of truth; defaults to ~/.agents/statusline
```

## Supported tools

Claude Code · ZCode · OpenAI Codex · Cursor · VS Code (Copilot) · Gemini CLI · OpenCode · GitHub Copilot CLI · Continue · Cline · Trae · Aider Desktop · Windsurf

Adding a tool = one `ToolSpec` entry in `registry.py` (detection + skills layer work immediately); MCP/plugin support needs the tool's dialect reader/writer; hooks likewise live in `hooks.py` / `hooks_write.py`; subagents need just an `agents_dirs` entry, memory a `memory_files` entry, statusline an entry in the `DIALECTS` table (`statusline.py`).

## Limitations (v1)

- Cursor plugins are inventoried but not installed (marketplace mechanism is opaque).
- Provider switching covers claude and codex only (ZCode has no file-based model config; Gemini CLI has none here) and is deliberately machine-local: provider identities never ride `push`/`pull`.
- Continue / Cline / Trae / Aider / Windsurf are covered on the skills layer only.
- The zcode plugin target requires the plugin to be installed on the claude side first (mirror source).
- The hooks layer covers claude / zcode / cursor only (Codex has just a weak `notify` callback; the other tools have no equivalent); Cursor's prompt-type hooks distribute to cursor only.
- The subagents layer covers claude / zcode / cursor only; frontmatter fields like `model: opus` are Claude-family aliases and are distributed as-is (they may not resolve elsewhere).
- The memory layer covers tools with a single user-level Markdown instructions file (claude / zcode / codex / gemini / opencode); Cursor's rules need `.mdc` frontmatter and Windsurf / VS Code Copilot global-rule paths move between versions, so they are not wired up yet.
- The statusline layer covers seven tools in five dialect shapes: claude / zcode / cursor CLI (root `statusLine` key + script; zcode's support of the key is a same-origin inference — writing it is harmless), qwen (`ui.statusLine`, deliberately nested — root-level paste from Claude Code does not work there), codex (`[tui] status_line` declarative items, the `/statusline` TUI setting), gemini (`ui.footer.items`, the `/footer` setting), kimi (`tui.toml [status_line]`, items + command mixed). Copilot CLI has a two-layer mechanism (`footer.*` booleans documented, but the script `statusLine` key exists only in issues/community docs with a known macOS bug) — deferred until officially documented; OpenCode has no statusline key yet (official script-command PR pending), Pi customizes via TypeScript extensions, Amp only an experimental plugin API, iFlow has none (project shut down 2026-04). Like memory it stays out of the desktop matrix's cross-machine push UI (its single summary row doesn't map to a pushable item), but rides the CLI channel directly: `halter push statusline claude --to <machine> --apply` / `halter pull statusline claude --from <machine> --apply` — the `{script}` placeholder makes each fragment machine-portable, the target machine's library gets the fragment + script, and its own `halter sync` distributes to that machine's tools.
- Session continuity natively covers Claude Code, ZCode, Codex CLI, and OpenCode; install optional ctx for broader provider coverage. halter itself does not yet implement native Gemini or Cursor transcript parsers.
- Dead-hook detection is conservative: existence is only checked for commands that are a single script path; compound shell expressions are neither checked nor false-positived.
- MCP servers injected by a harness at runtime for its own plugins (e.g. ZCode's `node_repl` via browser-use) rely on a small built-in mapping table.

## Development

```bash
uv sync
uv run pytest               # 231 tests, all against a fake $HOME — never touches your real config
```

**Releasing** (maintainer): push a `v*` tag — `release.yml` builds sdist/wheel, attaches them plus the Windows installers to the GitHub release, and publishes to PyPI via trusted publishing. One-time setup on [pypi.org](https://pypi.org): register the project `halter-cli`, then add a *pending publisher* for this repo (workflow `release.yml`, environment `pypi`); until then the publish job fails harmlessly while release assets still attach. Version numbers must agree across `pyproject.toml` / `tauri.conf.json` / `Cargo.toml` (a contract test enforces it, and the sidecar must be rebuilt via `scripts/build_sidecar.sh`).

## License

MIT

## Use inside DeepSeek Harness (dsh)

A community plugin **`dsh-halter`** wraps this CLI for [DeepSeek Harness](https://www.deepseek.com/harness/en):

```bash
dsh plugin --profile web add dsh-halter
```

It registers `halter_cli`, a read-only agent tool (scan / assess / sessions list·show·context·search), plus a `/halter` slash command exposing the full CLI to the human — mutating commands (`sync --apply`, `sessions install`) are never model-callable. Source lives in [`dsh-plugin/`](dsh-plugin/); halter itself must be installed separately (`uv tool install git+https://github.com/pengkangzhen/halter.git`).

## Desktop App (Tauri + halter sidecar)

`desktop/` ships a Tauri v2 desktop app:

- **Overview dashboard**: detected tools, per-layer counts (skills / subagents / memory / statusline / MCP / plugins / mods / hooks), doctor issues
- **Memory panel**: view and edit the global memory source of truth, inspect each tool-side copy with a unified diff, save with automatic backup
- **Cross-assistant session browser**: project session list, redacted transcripts, full-text search, one-click handoff generation
- **Sync**: per-layer toggles + dry-run preview; Apply requires two-step confirmation and keeps the CLI's safety semantics
- **Multiple machines**: machine picker on the matrix toolbar — `~/.ssh/config` Host aliases appear automatically (zero registration), manual registration via `halter machines add`; ghost rows push local-only entries over, remote-only entries get a pull button
- **Bilingual UI**: zh / EN switch in the sidebar, remembered per device

The frontend is plain static files (no build step) talking to Rust commands over Tauri IPC; the Rust side executes `halter --json` as a sidecar. Sidecar resolution order: `HALTER_BINARY` env var → bundled `halter` executable next to the app → `halter` on PATH.

### Development

```bash
cd desktop
npm install
HALTER_BINARY=../devbin/halter npm run dev   # use the repo's .venv halter instead of the global one
```

Requires Node 18+, a Rust toolchain, and Xcode Command Line Tools (macOS).

### Build

```bash
cd desktop
npm run build
```

For release distribution, place a PyInstaller-built `halter` binary at `desktop/src-tauri/binaries/halter-<target-triple>` and declare it as `externalBin` in `tauri.conf.json` so it ships inside the app bundle.
