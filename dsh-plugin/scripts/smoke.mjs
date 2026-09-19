// Smoke test: load the built plugin with a mock cordis ctx, then exercise the
// tool allowlist, a real halter invocation, the render path, and the slash
// command. Run: node scripts/smoke.mjs  (after `npm run build`)
import { apply, name, inject } from '../lib/index.js'

const registered = { tools: [], commands: [] }
const ctx = {
  tools: { register: (tool) => registered.tools.push(tool) },
  commands: { register: (def) => {
    registered.commands.push(def)
    return () => {}
  } },
  effect: () => {},
}

apply(ctx, {})
console.log(`plugin ${name}, inject=[${inject.join(', ')}]`)
console.log('tools:', registered.tools.map((t) => t.name).join(', '))
console.log('commands:', registered.commands.map((c) => `/${c.name}`).join(', '))

const tool = registered.tools[0]
const freshSignal = () => new AbortController().signal

// 1) mutating command must be rejected by the read-only allowlist
const blocked = await tool.execute({ command: 'sync --apply' }, { signal: freshSignal() })
console.log('[blocked sync --apply] ok=' + blocked.ok, '| stderr=' + blocked.stderr.slice(0, 70) + '…')

// 2) blocked head not on allowlist at all
const blocked2 = await tool.execute({ command: 'run @claude do something' }, { signal: freshSignal() })
console.log('[blocked run] ok=' + blocked2.ok)

// 3) real read-only call through the actual halter binary
const scan = await tool.execute({ command: 'scan --json' }, { signal: freshSignal() })
console.log('[scan --json] ok=' + scan.ok, '| stdout bytes=' + scan.stdout.length)

// 4) render path produces a text content block
const blocks = tool.output.render({ command: 'x' }, scan)
console.log('[render] ' + blocks.length + ' block(s), type=' + blocks[0].type + ', chars=' + blocks[0].text.length)

// 5) slash command with empty input -> usage error; with real input -> runs
const cmd = registered.commands[0]
const usage = await cmd.handler({ rawInput: '', signal: freshSignal(), commandId: 'smoke', agent: {}, attachments: [] })
console.log('[/halter (empty)] kind=' + usage.kind)
const ran = await cmd.handler({ rawInput: 'scan --json', signal: freshSignal(), commandId: 'smoke', agent: {}, attachments: [] })
console.log('[/halter scan --json] kind=' + ran.kind, '| text bytes=' + (ran.text || '').length)

const failures = [
  blocked.ok !== false, blocked2.ok !== false, scan.ok !== true,
  blocks.length !== 1, usage.kind !== 'error', ran.kind !== 'success',
].filter(Boolean).length
console.log(failures === 0 ? 'SMOKE OK' : `SMOKE FAILED (${failures} assertion(s))`)
process.exit(failures === 0 ? 0 : 1)
