# harness-config-manager (`halter`) // [中文文档](README.zh-CN.md)

统一检测、盘点、分发 AI 编码工具的用户级 **skills / MCP servers / 插件 / hooks / subagents**，并可调取当前项目的跨助手历史会话。

一台机器上往往装着多个 AI 编码工具（Claude Code、ZCode、Codex、Cursor、VS Code Copilot、Gemini CLI、OpenCode……），每个工具各管一套用户级 skills、MCP 配置、插件、hooks（钩子：在工具执行特定动作前后自动运行的 shell 命令）、subagents（子代理：每代理一个 Markdown 定义文件）和 session 记录，格式互不相同（JSON 的 `mcpServers`/`servers`/`.mcp`、TOML 的 `[mcp_servers.*]`、command 数组……），配置渐渐各自为政。`halter` 用单一事实源统一管理这五层配置：先检测与盘点，再按需分发；session 记录则作为只读的项目连续性能力单独调取。

## 界面预览

**配置矩阵** —— Harness × Skills / Subagents / MCP / 插件 / Hooks，谁装了、谁缺了一目了然（skill 家族自动聚合，可展开明细）：

![配置矩阵](docs/images/app-matrix.png)

**跨助手会话** —— 项目 / 助手 / 时间线三个维度统一筛选，支持内容搜索、脱敏 transcript 与一键生成交接上下文：

![会话浏览器](docs/images/app-sessions.png)

## 安装

```bash
uv tool install .          # 从本仓库安装，得到 halter 命令
```

依赖：Python 3.12+，typer / rich / tomlkit。

## 快速上手

核心是三个命令：

```bash
halter scan                   # 看一眼：装了哪些工具、各配置了什么 skills/MCP/插件/hooks/subagents、有无健康问题
halter scan -d skills -d mcp  # 查看某层明细；--json 输出机器可读格式

halter sync                   # 同步一下：清单/库 -> 所有工具（默认 dry-run）
halter sync --apply           # 实际执行（冲突默认跳过保护）
halter sync --no-skills --no-plugins --no-mcp   # 只保留 hooks + sessions（各层默认全开，按需关闭）
halter sync --prefer library  # 冲突时以清单覆盖（默认 skip）

halter sessions list          # 列出当前项目在所有本地 AI 编码助手中的历史会话
halter sessions install       # 分发 halter-sessions 查询 skill（默认 dry-run）
halter sessions install --apply
```

sessions 层只安装查询 skill，不复制、不改写、不“收编”任何原生 session 文件。清单为空时 `sync` 会自动从最全的工具收集（MCP 选 server 最多的，插件选启用最多的 claude 系工具，hooks 选条目最多的目标工具；第三方注入的 hook 条目也一并收编，排除名单走 `exclude_hooks`；`--from` 可覆盖）；skills 与 subagents 库外独有的会自动入库，同名分叉会列出让你裁决。

## 任务调度：在同一界面 @ 不同 Harness

不止“看历史”，还能直接派活。`halter run` 用无头模式调起对应助手，在同一终端并发执行、输出带 `[claude]` / `[codex]` 前缀流式回传，任务自动留档：

```bash
halter run "@claude 修复 tests/test_auth.py 里失败的用例"
halter run "@codex @zcode 分别给出数据库迁移方案，对比优劣"   # 多助手并发
halter run "@claude/sonnet 快速修 / @claude/opus 深度重构"      # 同一助手，不同 LLM
halter run "@opencode/zhipu/glm-4.7 给出第二意见"               # opencode 按 provider/model 路由
halter run "@claude @codex 审查当前 diff" --mode yolo          # 跳过权限确认（慎用）
halter run "@claude 长任务" --detached                          # 后台运行，立即返回 task id

halter tasks list                    # 查看已派发任务
halter tasks show <task-id>          # 查看输出末尾（已脱敏）
```

支持的路由目标与无头方式：

| @目标 | 无头调用 |
|---|---|
| `@claude`（别名 `@cc`） | `claude -p` |
| `@codex`（别名 `@cx`） | `codex exec` |
| `@zcode`（别名 `@z`） | `zcode --prompt --cwd`（PATH / `ZCODE_CLI` / macOS App 内置 CLI 自动发现） |
| `@opencode`（别名 `@oc`） | `opencode run` |

**可配置不同 LLM**：`@harness/model` 内联指定（如 `@claude/opus`、`@codex/o3`、`@opencode/zhipu/glm-4.7`——第一个 `/` 前是 harness，其余全是模型名）；也可在 `~/.config/halter/config.toml` 配置每个 harness 的默认模型，内联优先：

```toml
[models]
claude = "sonnet"
codex = "o3"
opencode = "zhipu/glm-4.7"
halter = "openai/gpt-5"       # native AHP provider
```

`halter models` 查看当前配置。模型映射：claude `--model`、codex `-m`、opencode `-m`；ZCode 无公开无头模型开关，v1 仅记录不注入（用其自身默认模型）。

安全语义：默认 `--mode safe`（各助手权限受控，能改动的范围由各自沙箱决定）；`--mode yolo` 才映射到各家的“跳过确认”开关。提示词作为独立 argv 元素传递，不经过 shell。任务记录写入 `~/.config/halter/tasks/`（0700/0600 私有权限），输出查看时自动脱敏。前台模式 Ctrl-C 或 `--timeout` 会终止整个进程组。

桌面 App 的「调度」视图提供同一能力的图形界面：@提及、可用性徽标、项目选择、任务卡片实时轮询输出。

## AHP：自建 Agent Host（协议级挂载全部 Harness）

`halter ahp serve` 启动一个符合 [Agent Host Protocol 0.9.0](https://microsoft.github.io/agent-host-protocol/) 的自建服务器（WebSocket + JSON-RPC），把 claude / codex / zcode / opencode 全部挂载为标准 AHP agent backend：

```bash
halter ahp serve --port 7433
```

任何 AHP 客户端（VS Code Agents 窗口、AHPX、官方 Rust/TS/Go/Swift/Kotlin client 库）连接后即可：

```
initialize（版本协商 + agents 目录快照）
  -> createSession(provider="claude")      # 或 codex / zcode / opencode
  -> createChat
  -> dispatchAction(chat/turnStarted)      # message.model.id 自动映射 --model/-m
  <- chat/responsePart + chat/delta...     # harness 输出逐行流式广播
  <- chat/turnComplete
```

实现要点：channel URI 路由（`ahp-root://` / `ahp-session:/<uuid>` / `ahp-chat:/<uuid>`）、服务端单调 `serverSeq` 广播、客户端 action `origin` 回显、`root/sessionAdded` 目录通知、模型路由（`[models]` 配置 + `message.model` 覆盖）。任务执行复用 runner 层的进程组管理与无头 argv 组装。

第五个 backend 是 `provider="halter"`：Halter 自己拥有 model/tool 决策循环，并持久化脱敏后的 session 与模型/工具历史。当前暴露被限制在 workspace 内的只读/项目记忆工具（`read_file`、`list_dir`、`search_files`、`git_status`、`git_diff`、`list_project_sessions`、`read_project_session`）、需要逐 diff 审批且可按事务回滚的 `apply_patch`、固定命令且需审批的 `run_tests`，以及把 Claude/Codex/ZCode/OpenCode 并发委派到一次性 git clone（含 tracked+untracked 快照）的 `delegate_harness`，支持 OpenAI / Zhipu 兼容模型流式路由，广播结构化 `halter/toolCall` / `halter/toolResult` 事件，并把每个模型响应与工具调用写入私有 JSONL 审计日志；外部 runner 的调用与输出同样留档。详见 [docs/native-agent.md](docs/native-agent.md)。

```bash
uv run pytest tests/test_agent_runtime.py tests/test_ahp_host.py   # 原生 runtime + 协议级端到端
```

**桌面 App 直连**：调度视图经 Tauri Rust 桥（`halter_ahp_connect/rpc/notify` IPC + `ahp-message` 事件流）连接 AHP host 的 HTTP+SSE 传输，任务卡由 `chat/delta`/`chat/turnComplete` action 流驱动（WebView 禁止明文 ws/http，网络由 Rust 侧代理）。GUI 环境部署用 PyInstaller onefile（绕过 venv python 在 launchd/GUI 会话的 getpath 启动问题，并自动补 homebrew PATH）：

```bash
uv run pyinstaller --onefile --name halter \
  --hidden-import websockets --hidden-import aiohttp desktop/pyinstaller_entry.py
```

## Session 连续性

从 Claude Code 切到 Codex，或从任意一个助手切到另一个助手时，不需要丢失当前项目的工作历史：

```bash
# 在任意有 shell 权限的助手中，或安装 halter-sessions skill 后：
halter sessions list --project . --json

# 只读取选中的会话，不把所有 transcript 塞进上下文：
halter sessions show claude:<session-id> --transcript --tail 80

# 生成私有、确定性、脱敏的交接上下文：
halter sessions context claude:<session-id>
halter sessions context claude:<session-id> --output .halter/HANDOFF.md

# 搜索当前项目历史：
halter sessions search "authentication migration" --project .
```

原生 metadata 读取器覆盖 Claude Code、ZCode、Codex CLI、OpenCode。如果安装并初始化了 [ctx](https://ctx.rs)，`halter sessions list` 还会纳入 ctx 已索引的 Cursor、Gemini CLI、Copilot CLI、Continue 等更多来源，并与原生结果去重。ctx 是可选依赖；halter 只调用其只读的 `list events` / `show session` 接口。

内置 `halter-sessions` skill 会教所有已检测助手同一套查询流程。`halter sync --sessions`（默认开启）或 `halter sessions install --apply` 通过正常 skills library 分发。只有显式使用 `--transcript`、`search` 或 `context` 时才读取 transcript；明显凭据会脱敏，历史命令只作为证据展示，不作为可执行指令。

## 五个配置层

| 层 | 事实源 | 分发方式 |
|---|---|---|
| skills | skills 库目录（三层回退：配置指定 > `~/.agents/skills`（cc-switch 用户兼容）> `~/.config/halter/library/skills`） | 条目级 symlink；同名冲突默认跳过，`--prefer library` 备份后覆盖 |
| subagents | subagents 库目录（三层回退：配置 `agents_library` > `~/.agents/agents` > `~/.config/halter/library/agents`） | 条目级 symlink（每代理一个 `.md` 文件）；冲突语义与 skills 相同；frontmatter 字段（如 `model: opus`）原样分发，Claude 系别名在其它工具可能无效 |
| MCP | `~/.config/halter/mcp.toml`（canonical：stdio/http、env、headers） | 六方言转换写入：claude（`.claude.json` 读改写）、zcode、codex（TOML 保注释）、cursor、vscode（`servers` 键）、gemini、opencode（command 数组）；http 类型只分发支持的工具 |
| 插件 | `~/.config/halter/plugins.toml`（family: claude / codex / vscode） | claude 用 `claude plugin install -y`；zcode 镜像 claude 缓存 + 登记同源清单；codex 写 TOML 开关；vscode 用 `code --install-extension` |
| hooks | `~/.config/halter/hooks.toml`（每条注册：id、事件列表、matcher、命令、超时秒） | 三方言转换写入：claude（`settings.json` 顶层 `hooks`）、zcode（`cli/config.json` 的 `hooks.events`，超时自动换算毫秒）、cursor（`hooks.json` 扁平结构）；写入条目带 `"halter": "<id>"` 归属标记，**只增删改自家电位，第三方注入的无标记条目一律不碰**；仅 cursor 支持的事件（如 `beforeShellExecution`）或 claude 独有事件（如 `PermissionRequest`）分发到无此事件的工具时跳过并提示 |

## 安全设计

- **一切写操作默认 dry-run**，`--apply` 才执行；skills 替换前自动备份到 `~/.config/halter/backups/<时间戳>/`。
- **密钥永不进清单**：`adopt --mcp` 把疑似密钥的值换成 `${VAR}` 占位符，真实值写入 `~/.config/halter/secrets.toml`（权限 0600）；同步时按 环境变量 > secrets 展开，缺变量的 server 整体跳过，不写半截配置；终端输出永远脱敏。
- `~/.claude.json` 是大状态文件，只做读-改-写合并（保留其余键），原子替换落盘。
- hooks 同步只触碰自己打标（`halter` 键）的条目——Otty、Orca 等第三方管理器写入的 hook 永不被修改或删除；zcode 的 `hooks.enabled` 全局开关由你手动控制，halter 不代管。

## 配置

`~/.config/halter/config.toml`（可选）：

```toml
library = "~/.agents/skills"     # skills 事实源；缺省走三层回退
exclude_skills = []              # 不分发的 skill 名单
exclude_mcp = []
exclude_hooks = []               # 不分发的 hook id 名单（id 见 hooks.toml / scan -d hooks）
agents_library = ""              # subagents 事实源；缺省走三层回退
exclude_agents = []              # 不分发的 subagent 名单
```

## v1 边界

- Cursor 插件仅盘点不安装（市场机制不透明）。
- Continue / Cline / Trae / Aider / Windsurf 仅覆盖 skills 层。
- `sync --plugins` 的 zcode 目标要求该插件在 claude 侧已安装（镜像来源）。
- hooks 层仅 claude / zcode / cursor 三家支持（Codex 只有弱 notify 回调，其余工具暂无对等机制）；Cursor 的 prompt 型 hook 仅随 cursor 目标分发。
- subagents 层仅 claude / zcode / cursor 三家支持；frontmatter 里的 `model: opus` 等是 Claude 系别名，原样分发（在其它工具可能不生效）。
- Session 连续性原生覆盖 Claude Code / ZCode / Codex / OpenCode；如需更多 provider，可安装可选 ctx。halter 自身暂未实现 Gemini / Cursor 原生 transcript parser。
- hook command 的死配置检测是保守的：仅对「单一脚本路径」形式的命令判定存在性，复合 shell 表达式不误报也不检查。

## 支持新工具

在 `registry.py` 的 `TOOLS` 中追加一条 `ToolSpec`（CLI 命令名、配置目录、skills 目录），检测与 skills 层即刻生效；MCP / 插件层需在 `mcp.py` / `mcp_write.py` / `plugins.py` / `plugin_sync.py` 中补充该工具的方言读写器；hooks 层同理见 `hooks.py` / `hooks_write.py`；subagents 只需加 `agents_dirs` 条目。

## 测试

```bash
uv run pytest               # 63 项单测，全部使用假 HOME，绝不触碰真实配置
```

## 桌面 App（Tauri + halter sidecar）

`desktop/` 内置一个 Tauri v2 桌面应用：

- **总览仪表盘**：检测到的工具、五层配置（skills / subagents / MCP / 插件 / hooks）计数、健康检查问题
- **任务调度**：同一输入框 `@claude` / `@codex` / `@zcode` / `@opencode` 派发任务，卡片式实时输出
- **跨助手会话浏览器**：项目会话列表、脱敏 transcript、内容搜索、一键生成交接上下文
- **同步**：分层勾选 + dry-run 预览；Apply 需两步确认，保留 CLI 的安全语义

前端为纯静态文件（无构建步骤），通过 Tauri IPC 调用 Rust 命令；Rust 侧以 sidecar 方式执行 `halter --json`。sidecar 解析顺序：`HALTER_BINARY` 环境变量 → 应用旁打包的 `halter` 可执行文件 → PATH 上的 `halter`。

### 开发

```bash
cd desktop
npm install
HALTER_BINARY=../devbin/halter npm run dev   # 使用本仓库 .venv 中的 halter，而非全局安装版
```

依赖：Node 18+、Rust toolchain、Xcode Command Line Tools（macOS）。

### 构建

```bash
cd desktop
npm run build
```

`npm run build` 已自动化整个流程：`build:sidecar` 脚本（`scripts/build_sidecar.sh`）先用 PyInstaller 打出 onefile 二进制到 `desktop/src-tauri/binaries/halter-<target-triple>`（源码未变时增量跳过，`FORCE_SIDECAR=1` 强制重打），`tauri.conf.json` 已声明 `externalBin`，Tauri 自动把它带进 .app/.dmg。
