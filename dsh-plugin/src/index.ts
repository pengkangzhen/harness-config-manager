import { execFile } from 'node:child_process'
import Schema from '@deepseek-ai/schemastery'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
// type-only, but loading the module also brings the `ctx.commands` augmentation
import type { CommandResult } from '@deepseek-ai/dsh-commands'

export const name = 'dsh-halter'
export const inject = ['tools', 'commands']

export interface Config {
  binary: string
  timeoutMs: number
  maxOutputChars: number
}

export const Config: Schema<Config> = Schema.object({
  binary: Schema.string().default('halter').description('Path to the halter executable.'),
  timeoutMs: Schema.number().default(120000).description('Subprocess timeout in milliseconds.'),
  maxOutputChars: Schema.number().default(8000).description('Truncate rendered tool output to this many characters.'),
})

function resolveConfig(config: Partial<Config> = {}): Config {
  return {
    binary: config.binary ?? 'halter',
    timeoutMs: config.timeoutMs ?? 120000,
    maxOutputChars: config.maxOutputChars ?? 8000,
  }
}

interface HalterResult {
  ok: boolean
  command: string
  stdout: string
  stderr: string
}

function runHalter(binary: string, args: string[], signal: AbortSignal | undefined, timeoutMs: number): Promise<HalterResult> {
  return new Promise((resolve) => {
    execFile(
      binary,
      args,
      { encoding: 'utf8', timeout: timeoutMs, maxBuffer: 16 * 1024 * 1024, signal },
      (error, stdout, stderr) => {
        // A failing subcommand (non-zero exit, timeout, abort, missing binary) is a
        // business result the model should see, not a tool crash.
        resolve({
          ok: !error,
          command: `${binary} ${args.join(' ')}`,
          stdout: stdout ?? '',
          stderr: stderr || (error ? String((error as Error).message ?? error) : ''),
        })
      },
    )
  })
}

// The model-facing tool is read-only by design: scan / assess and the read side of
// sessions. Mutating commands (sync --apply, sessions install, run, tasks) belong to
// the human-invoked /halter slash command, which is as if the user typed them in a shell.
const READ_ONLY_HEADS = new Set(['scan', 'assess'])
const READ_ONLY_SESSIONS_SUBS = new Set(['list', 'show', 'context', 'search'])

function isReadOnly(argv: string[]): boolean {
  const [head, sub] = argv
  if (head && READ_ONLY_HEADS.has(head)) return true
  if (head === 'sessions' && sub && READ_ONLY_SESSIONS_SUBS.has(sub)) return true
  return false
}

// Simple whitespace splitting: halter flag values (paths, ids, search phrases without
// spaces) don't need quoting. The slash command documents the same limitation.
function splitArgv(line: string): string[] {
  return line.trim().split(/\s+/).filter(Boolean)
}

function truncate(text: string, maxChars: number): string {
  return text.length > maxChars
    ? `${text.slice(0, maxChars)}\n… (output truncated at ${maxChars} characters)`
    : text
}

export function apply(ctx: Context, config: Partial<Config> = {}) {
  const cfg = resolveConfig(config)

  ctx.tools.register(
    defineTool({
      name: 'halter_cli',
      description:
        'Inspect the user\'s AI coding harnesses via the halter CLI. Read-only subcommands only: ' +
        '`scan` (which tools are installed, what each has, health issues), `assess` (per-layer usefulness ' +
        'for the current project), `sessions list|show|context|search` (cross-assistant session history). ' +
        'Pass a command line such as "scan --json" or "sessions list". Mutating operations are not available ' +
        'to the model; the user runs them via the /halter slash command.',
      parameters: {
        command: {
          type: 'string',
          required: true,
          description: 'halter command line, e.g. "scan --json", "assess -l mcp", "sessions list --json"',
        },
      },
      output: {
        schema: {
          type: 'object',
          additionalProperties: false,
          properties: {
            ok: { type: 'boolean', required: true, description: 'Whether the subcommand exited zero.' },
            command: { type: 'string', required: true, description: 'The halter command line that ran.' },
            stdout: { type: 'string', required: true, description: 'Command stdout.' },
            stderr: { type: 'string', required: true, description: 'Command stderr or failure reason; empty on success.' },
          },
        },
        render: (_args, value) => {
          const text = value.ok
            ? value.stdout || '(no output)'
            : `command failed: ${value.command}\n${value.stderr || '(no error output)'}`.trim()
          return [{ type: 'text', text: truncate(text, cfg.maxOutputChars) }]
        },
      },
      timeoutMs: cfg.timeoutMs + 5000,
      async execute(args, exec) {
        const argv = splitArgv(args.command ?? '')
        if (argv.length === 0) {
          return { ok: false, command: cfg.binary, stdout: '', stderr: 'empty command' }
        }
        if (!isReadOnly(argv)) {
          return {
            ok: false,
            command: argv.join(' '),
            stdout: '',
            stderr:
              `"${argv[0]}" is not on the read-only allowlist (scan, assess, sessions list|show|context|search). ` +
              'Mutating halter commands (sync, sessions install, run, tasks) are reserved for the user via the /halter slash command.',
          }
        }
        return runHalter(cfg.binary, argv, exec.signal, cfg.timeoutMs)
      },
    }),
  )

  const dispose = ctx.commands.register({
    name: 'halter',
    description: 'Run any halter CLI subcommand and show its output (scan / assess / sync / sessions / tasks …)',
    input: { hint: 'e.g. scan --json', attachments: false },
    handler: async ({ rawInput, signal }): Promise<CommandResult> => {
      const argv = splitArgv(rawInput ?? '')
      if (argv.length === 0) {
        return { kind: 'error', text: 'usage: /halter <subcommand> [args] — e.g. /halter scan --json' }
      }
      const result = await runHalter(cfg.binary, argv, signal, cfg.timeoutMs)
      if (!result.ok) {
        return { kind: 'error', text: `${result.command}\n${result.stderr || '(no error output)'}` }
      }
      return { kind: 'success', text: result.stdout || '(no output)' }
    },
  })
  ctx.effect(() => dispose)
}
