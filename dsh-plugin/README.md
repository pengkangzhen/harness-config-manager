# dsh-halter

A [DeepSeek Harness](https://www.deepseek.com/harness/en) plugin that exposes the
[halter](https://github.com/pengkangzhen/harness-config-manager) CLI — one source of truth
for skills, MCP servers, plugins, hooks and subagents across all your AI coding harnesses
— as agent tools and a slash command inside dsh.

## What you get

- **`halter_cli` tool** (model-callable, read-only): lets the agent run
  `halter scan`, `halter assess`, and `halter sessions list|show|context|search`
  (e.g. to check your harness inventory or dig up a past session before continuing work).
  Mutating commands (`sync --apply`, `sessions install`, `run`, `tasks`) are **not**
  available to the model.
- **`/halter <subcommand>` slash command** (human-invoked, full CLI): exactly as if you
  typed it in a shell — including `sync` dry-run previews and `--apply`.

## Prerequisites

halter itself is not bundled. Install it separately (Python 3.12+ / [uv](https://docs.astral.sh/uv/)):

```bash
uv tool install harness-config-manager
halter scan   # sanity check
```

If `halter` is not on `PATH`, point the plugin at it via its `binary` config.

## Install

```bash
dsh plugin --profile web add dsh-halter
```

Requires a dsh base that provides the `commands` service (the standard dsh base and its
Web client do; UI-less demo spines and ACP automation compositions do not).

## Configuration

Optional, via the usual dsh config layering:

| key | default | meaning |
|---|---|---|
| `binary` | `halter` | path to the halter executable |
| `timeoutMs` | `120000` | subprocess timeout |
| `maxOutputChars` | `8000` | truncate rendered tool output |

## Security notes

- The agent-facing tool is restricted to read-only subcommands by an allowlist;
  anything mutating is rejected with an explanation.
- halter's own safety semantics apply unchanged: every write is dry-run by default
  and requires `--apply`; terminal output is redacted.
- Commands are executed via `execFile` (no shell), so arguments are never interpolated
  into a shell string. Argument values containing whitespace are not supported by the
  simple tokenizer — keep quoting-free arguments.

## Development

```bash
cd dsh-plugin
npm install
npm run typecheck
npm run build     # emits lib/ (shipped on npm)
```

Local trial without publishing: point `cordis.patch.yml`'s `name` at an absolute path
to `src/index.ts` and run `dsh web --patch ./cordis.patch.yml`.

## License

MIT (same as the main repository).
