# Halter native agent runtime

Halter now has two kinds of AHP backends:

1. **External harness adapters** — `claude`, `codex`, `zcode`, and `opencode` are launched through their existing headless CLI contracts. Their stdout is streamed as AHP chat events and each invocation/output is written to the private agent audit log.
2. **Native `halter` provider** — Halter owns the model/tool decision loop itself. This is the first step toward a controllable, auditable, multi-model local coding harness rather than only a dispatcher.

The native runtime defaults to **read-only**. It can also perform workspace writes through `apply_patch`, but only when `workspace-write` mode is explicitly selected and the user approves the exact unified diff in the desktop UI. There is no arbitrary shell tool yet.

## Model providers

Configure a default model in `~/.config/halter/config.toml`:

```toml
[models]
halter = "openai/gpt-5"
```

The model prefix selects an OpenAI-compatible endpoint:

| Prefix | API key | Default base URL |
|---|---|---|
| `openai/...` | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| `zhipu/...` | `ZHIPUAI_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` |

Custom OpenAI-compatible providers are declarative and keyless in config; the real key, when required, is read from the named environment variable:

```toml
[model_providers.local]
base_url = "http://127.0.0.1:11434/v1"
api_key_env = ""            # local endpoints may omit this

[model_providers.company]
base_url = "https://models.example.com/v1"
base_url_env = "COMPANY_MODEL_BASE_URL"
api_key_env = "COMPANY_MODEL_API_KEY"

[models]
halter = "local/qwen-coder"
```

Both can be overridden with `OPENAI_BASE_URL` or `ZHIPU_BASE_URL`. An unprefixed model with `OPENAI_BASE_URL` can target any OpenAI-compatible local server (vLLM, Ollama compatibility endpoints, LM Studio, llama.cpp); localhost endpoints may omit `OPENAI_API_KEY`. A request can override the default with the normal AHP `message.model.id` field (for example, `zhipu/glm-4.7`). Credentials are used only for the outbound request and are never written to AHP events or audit records.

## Automatic workspace bootstrap

Before each native model call, halter injects a bounded, read-only `halter_workspace_context` system message containing:

- manifest excerpts (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`)
- a README excerpt
- the first 80 top-level entries
- git branch/status
- up to five recent workspace-scoped session titles

The context is redacted and written to the audit log as `context.workspace`. It replaces stale bootstrap contexts from earlier turns rather than accumulating them.

## Tool surface

The native provider advertises these tools through the model API:

- `update_plan(steps, note?)`
- `read_file(path, offset?, limit?)`
- `list_dir(path, limit?)`
- `search_files(query, path?, regex?, max_results?)`
- `git_status()`
- `git_diff(staged?, path?)`
- `list_project_sessions(limit?)`
- `read_project_session(ref, tail?)`
- `delegate_harness(provider, prompt, model?, timeout_seconds?)`
- `run_tests(command, timeout_seconds?)` — **fixed command list plus user approval**
- `apply_patch(patch, summary?)` — **requires `workspace-write` plus user approval**

Every path argument is resolved and confined to the session's first working directory. Symlink targets outside that workspace are rejected. Git tools only invoke fixed `git` argument vectors inside the workspace; user text is never assembled into a shell command.

`apply_patch` additionally:

- accepts a standard unified diff, not free-form shell or Python code;
- limits patch size;
- parses `---` / `+++` headers and rejects absolute, parent-relative, symlink-escaping, and internal excluded-directory targets before invoking git;
- runs `git apply --check` before `git apply`;
- emits a `halter/approvalRequest` containing the exact patch and waits for `halter/approvalResponse`;
- records both the request and the user's decision in the audit log;
- writes exact before/after state and reverse-patch metadata to a private transaction record.

Approved transactions are stored under `~/.config/halter/agent-transactions/<session-hash>/` with directory `0700` and JSON files `0600`. These records intentionally contain the exact patch required for rollback and are never redacted into a lossy form; access to the user's home directory therefore remains the security boundary.

Project-session tools reuse halter's existing session inventory and deterministic handoff generator. They list/read only sessions already associated with the selected workspace; transcripts are fetched on demand and pass through the same redaction pipeline.

`delegate_harness` runs Claude Code, Codex, ZCode, or OpenCode in a disposable local git clone that has the workspace's current tracked diff and a bounded snapshot of untracked files applied. Halter's own config/audit state is excluded from that snapshot. The real workspace is never passed to the external process. Any diff produced in the clone is returned to the native model, which must separately request `apply_patch` and obtain user approval before the real workspace changes. The external runner always uses halter's `safe` mode and a fixed argv (never a shell string). When one model response requests several read-only or delegate tools, halter executes them concurrently and the desktop shows their resulting diffs side by side; any write/test approval forces that batch sequential to prevent patch and test conflicts.

`run_tests` accepts only fixed argv families (`pytest`, `npm test`, `cargo test`, `go test`), never a free-form shell command. Like `apply_patch`, it requires `workspace-write` mode and a user approval action. Tests run in the same disposable git-clone sandbox used by `delegate_harness`; any files changed by the test process remain in that clone and are reported as `sandboxChanges`.

In read-only mode the model API is not even shown `apply_patch` or `run_tests`; a forged tool call is returned as a permission error and the workspace/test process remains unchanged.

## Durable sessions

Approval history is persisted as part of that chat state and rendered by the desktop. AHP session/chat state is persisted locally under:

```text
~/.config/halter/agent-sessions/<session-hash>/session.json
```

The directory is mode `0700`, the JSON file is mode `0600`, writes are atomic, and secret-like values are redacted. Native history is bounded to 12 complete prior turns or roughly 100 KiB; overflow is replaced by a system summary that tells the model current workspace evidence wins. Compaction is recorded as `context.compacted` in the audit log. A restarted host restores session/chat directories and native model/tool history. If the previous host stopped while a turn was active, that turn is restored as a `HostInterrupted` error rather than presented as still runnable.

## Audit trail

Each AHP session has an append-only JSONL log at:

```text
~/.config/halter/agent-audit/<session-hash>/audit.jsonl
```

The directory is mode `0700` and the file mode `0600`. Native runs record:

- `turn.started` — selected model, workspace, prompt (redacted), and advertised tools
- `model.response` — assistant text (redacted) and requested tool calls
- `tool.call` / `tool.result` — arguments and outputs (redacted/truncated)
- `approval.requested` / `approval.response` — exact patch or fixed test command and the user's decision
- `transaction.rolledBack` / `transaction.rollbackFailed` — explicit reverse-patch operations
- `context.compacted` — number of complete prior turns omitted to bound context
- `turn.completed` or `turn.failed`

External adapters additionally record `runner.started`, `runner.finished`, and `runner.failed` with their argv and output. Secret-like values are redacted using the same conservative matcher as the session reader.

## AHP event extensions

Native tool activity is represented as structured actions as well as markdown deltas:

```text
chat/responsePart
chat/delta
halter/toolCall
halter/toolResult
halter/approvalRequest
halter/approvalResult
halter/planChanged
halter/rollbackResult
chat/turnComplete | chat/error
```

The desktop Dispatch view renders the current durable plan, compares sandbox delegate diffs side by side, separates assistant output from structured tool events, renders patch/test-specific Approve/Reject controls, durable approval history, and transaction rollback controls, and supports `chat/turnCancelled`. Follow-up native messages reuse the same AHP chat; after an app/host restart, the desktop lists persisted halter sessions and reconnects to the matching project chat so the model sees prior user/assistant/tool history. A later phase will separate plan, tool calls, diffs, tests, and approval requests into dedicated panels.

## Current limits and next milestones

- Native model/tool history survives host restart through the private session store; a turn active at crash/shutdown time is marked `HostInterrupted` instead of being resumed.
- Native OpenAI-compatible model calls stream SSE deltas directly into AHP `chat/delta`; a JSON response fallback is also accepted.
- The implemented permission modes are `read-only` and approval-gated `workspace-write`. `shell-allowlist` and `full` remain intentionally unimplemented.
- `run_tests` is approval-gated, limited to fixed test commands, and executes in a disposable clone; a general guarded `run_command` is still intentionally not exposed.
- `apply_patch` supports explicit per-transaction rollback through a fixed reverse-patch operation; rollback refuses transactions from another workspace or non-applied state.
- `delegate_harness` copies at most 5,000 untracked files / 200 MiB into the disposable clone and reports skipped paths in the tool result.
- Direct external-harness adapters remain black-box processes; halter audits their invocation/output but cannot mediate their internal permission prompts through the current CLI adapters.
