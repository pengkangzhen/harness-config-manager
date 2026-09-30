# Handoff: memory-manage worktree

- 2026-09-30 · branch `worktree-memory-manage` · commit `66b3a99`（未合并回 main）
- 新增 halter 第六层「用户级记忆」：`src/harness_config_manager/memory.py`（库解析/扫描/收养/symlink 分发），事实源默认 `~/.agents/memory/MEMORY.md`（config `memory_file` 覆盖）。
- 机制与 subagents 层同构：symlink 分发（工具侧编辑即改库，天然一致）；冲突默认跳过，`--prefer library` 备份后覆盖；库缺失时自动收养，多工具内容分歧需 `--from <tool>` 裁决。
- 覆盖 claude/zcode/codex/gemini/opencode（registry `memory_files`）；cursor（.mdc frontmatter）、windsurf/copilot（路径不稳）列为 v1 边界。
- 已接线 scan（`-d memory` + 矩阵行）、sync（`--memory/--no-memory`）、tui（第 7 个 tab）、doctor（断链检查）、desktop（overview chip/矩阵/sync 开关 + main.rs 白名单）、README 双语。
- 测试 110 全绿（新增 tests/test_memory.py，含 CLI 端到端）；TUI 快照按合法流程更新。
- 合并本 worktree 时：主 checkout 的 .remember 由隔离机制保护未写入，本文件即 handoff。
- 后续候选：真实环境首次 `halter sync`（本机 `~/.claude/CLAUDE.md` 6123B 与 `~/.zcode/AGENTS.md` 4997B 分歧，需决定 `--from`）；cursor .mdc 包装分发。
