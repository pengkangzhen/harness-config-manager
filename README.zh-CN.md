# harness-config-manager (`hcm`) // [中文文档](README.zh-CN.md)

统一检测、盘点、分发 AI 编码工具的用户级 **skills / MCP servers / 插件 / hooks / subagents**，并可调取当前项目的跨助手历史会话。

一台机器上往往装着多个 AI 编码工具（Claude Code、ZCode、Codex、Cursor、VS Code Copilot、Gemini CLI、OpenCode……），每个工具各管一套用户级 skills、MCP 配置、插件、hooks（钩子：在工具执行特定动作前后自动运行的 shell 命令）、subagents（子代理：每代理一个 Markdown 定义文件）和 session 记录，格式互不相同（JSON 的 `mcpServers`/`servers`/`.mcp`、TOML 的 `[mcp_servers.*]`、command 数组……），配置渐渐各自为政。`hcm` 用单一事实源统一管理这五层配置：先检测与盘点，再按需分发；session 记录则作为只读的项目连续性能力单独调取。

## 界面预览

**配置矩阵** —— Harness × Skills / Subagents / MCP / 插件 / Hooks，谁装了、谁缺了一目了然（skill 家族自动聚合，可展开明细）：

![配置矩阵](docs/images/app-matrix.png)

**跨助手会话** —— 项目 / 助手 / 时间线三个维度统一筛选，支持内容搜索、脱敏 transcript 与一键生成交接上下文：

![会话浏览器](docs/images/app-sessions.png)

## 安装

```bash
uv tool install .          # 从本仓库安装，得到 hcm 命令
```

依赖：Python 3.12+，typer / rich / tomlkit。

## 快速上手

核心是三个命令：

```bash
hcm scan                   # 看一眼：装了哪些工具、各配置了什么 skills/MCP/插件/hooks/subagents、有无健康问题
hcm scan -d skills -d mcp  # 查看某层明细；--json 输出机器可读格式

hcm sync                   # 同步一下：清单/库 -> 所有工具（默认 dry-run）
hcm sync --apply           # 实际执行（冲突默认跳过保护）
hcm sync --no-skills --no-plugins --no-mcp   # 只保留 hooks + sessions（各层默认全开，按需关闭）
hcm sync --prefer library  # 冲突时以清单覆盖（默认 skip）

hcm sessions list          # 列出当前项目在所有本地 AI 编码助手中的历史会话
hcm sessions install       # 分发 hcm-sessions 查询 skill（默认 dry-run）
hcm sessions install --apply
```

sessions 层只安装查询 skill，不复制、不改写、不“收编”任何原生 session 文件。清单为空时 `sync` 会自动从最全的工具收集（MCP 选 server 最多的，插件选启用最多的 claude 系工具，hooks 选条目最多的目标工具；第三方注入的 hook 条目也一并收编，排除名单走 `exclude_hooks`；`--from` 可覆盖）；skills 与 subagents 库外独有的会自动入库，同名分叉会列出让你裁决。

## Session 连续性

从 Claude Code 切到 Codex，或从任意一个助手切到另一个助手时，不需要丢失当前项目的工作历史：

```bash
# 在任意有 shell 权限的助手中，或安装 hcm-sessions skill 后：
hcm sessions list --project . --json

# 只读取选中的会话，不把所有 transcript 塞进上下文：
hcm sessions show claude:<session-id> --transcript --tail 80

# 生成私有、确定性、脱敏的交接上下文：
hcm sessions context claude:<session-id>
hcm sessions context claude:<session-id> --output .hcm/HANDOFF.md

# 搜索当前项目历史：
hcm sessions search "authentication migration" --project .
```

原生 metadata 读取器覆盖 Claude Code、ZCode、Codex CLI、OpenCode。如果安装并初始化了 [ctx](https://ctx.rs)，`hcm sessions list` 还会纳入 ctx 已索引的 Cursor、Gemini CLI、Copilot CLI、Continue 等更多来源，并与原生结果去重。ctx 是可选依赖；hcm 只调用其只读的 `list events` / `show session` 接口。

内置 `hcm-sessions` skill 会教所有已检测助手同一套查询流程。`hcm sync --sessions`（默认开启）或 `hcm sessions install --apply` 通过正常 skills library 分发。只有显式使用 `--transcript`、`search` 或 `context` 时才读取 transcript；明显凭据会脱敏，历史命令只作为证据展示，不作为可执行指令。

## 五个配置层

| 层 | 事实源 | 分发方式 |
|---|---|---|
| skills | skills 库目录（三层回退：配置指定 > `~/.agents/skills`（cc-switch 用户兼容）> `~/.config/hcm/library/skills`） | 条目级 symlink；同名冲突默认跳过，`--prefer library` 备份后覆盖 |
| subagents | subagents 库目录（三层回退：配置 `agents_library` > `~/.agents/agents` > `~/.config/hcm/library/agents`） | 条目级 symlink（每代理一个 `.md` 文件）；冲突语义与 skills 相同；frontmatter 字段（如 `model: opus`）原样分发，Claude 系别名在其它工具可能无效 |
| MCP | `~/.config/hcm/mcp.toml`（canonical：stdio/http、env、headers） | 六方言转换写入：claude（`.claude.json` 读改写）、zcode、codex（TOML 保注释）、cursor、vscode（`servers` 键）、gemini、opencode（command 数组）；http 类型只分发支持的工具 |
| 插件 | `~/.config/hcm/plugins.toml`（family: claude / codex / vscode） | claude 用 `claude plugin install -y`；zcode 镜像 claude 缓存 + 登记同源清单；codex 写 TOML 开关；vscode 用 `code --install-extension` |
| hooks | `~/.config/hcm/hooks.toml`（每条注册：id、事件列表、matcher、命令、超时秒） | 三方言转换写入：claude（`settings.json` 顶层 `hooks`）、zcode（`cli/config.json` 的 `hooks.events`，超时自动换算毫秒）、cursor（`hooks.json` 扁平结构）；写入条目带 `"hcm": "<id>"` 归属标记，**只增删改自家电位，第三方注入的无标记条目一律不碰**；仅 cursor 支持的事件（如 `beforeShellExecution`）或 claude 独有事件（如 `PermissionRequest`）分发到无此事件的工具时跳过并提示 |

## 安全设计

- **一切写操作默认 dry-run**，`--apply` 才执行；skills 替换前自动备份到 `~/.config/hcm/backups/<时间戳>/`。
- **密钥永不进清单**：`adopt --mcp` 把疑似密钥的值换成 `${VAR}` 占位符，真实值写入 `~/.config/hcm/secrets.toml`（权限 0600）；同步时按 环境变量 > secrets 展开，缺变量的 server 整体跳过，不写半截配置；终端输出永远脱敏。
- `~/.claude.json` 是大状态文件，只做读-改-写合并（保留其余键），原子替换落盘。
- hooks 同步只触碰自己打标（`hcm` 键）的条目——Otty、Orca 等第三方管理器写入的 hook 永不被修改或删除；zcode 的 `hooks.enabled` 全局开关由你手动控制，hcm 不代管。

## 配置

`~/.config/hcm/config.toml`（可选）：

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
- Session 连续性原生覆盖 Claude Code / ZCode / Codex / OpenCode；如需更多 provider，可安装可选 ctx。hcm 自身暂未实现 Gemini / Cursor 原生 transcript parser。
- hook command 的死配置检测是保守的：仅对「单一脚本路径」形式的命令判定存在性，复合 shell 表达式不误报也不检查。

## 支持新工具

在 `registry.py` 的 `TOOLS` 中追加一条 `ToolSpec`（CLI 命令名、配置目录、skills 目录），检测与 skills 层即刻生效；MCP / 插件层需在 `mcp.py` / `mcp_write.py` / `plugins.py` / `plugin_sync.py` 中补充该工具的方言读写器；hooks 层同理见 `hooks.py` / `hooks_write.py`；subagents 只需加 `agents_dirs` 条目。

## 测试

```bash
uv run pytest               # 63 项单测，全部使用假 HOME，绝不触碰真实配置
```

## 桌面 App（Tauri + hcm sidecar）

`desktop/` 内置一个 Tauri v2 桌面应用：

- **总览仪表盘**：检测到的工具、五层配置（skills / subagents / MCP / 插件 / hooks）计数、健康检查问题
- **跨助手会话浏览器**：项目会话列表、脱敏 transcript、内容搜索、一键生成交接上下文
- **同步**：分层勾选 + dry-run 预览；Apply 需两步确认，保留 CLI 的安全语义

前端为纯静态文件（无构建步骤），通过 Tauri IPC 调用 Rust 命令；Rust 侧以 sidecar 方式执行 `hcm --json`。sidecar 解析顺序：`HCM_BINARY` 环境变量 → 应用旁打包的 `hcm` 可执行文件 → PATH 上的 `hcm`。

### 开发

```bash
cd desktop
npm install
HCM_BINARY=../devbin/hcm npm run dev   # 使用本仓库 .venv 中的 hcm，而非全局安装版
```

依赖：Node 18+、Rust toolchain、Xcode Command Line Tools（macOS）。

### 构建

```bash
cd desktop
npm run build
```

正式打包时可把 PyInstaller 产出的 `hcm` 二进制放入 `desktop/src-tauri/binaries/hcm-<target-triple>` 并在 `tauri.conf.json` 的 `bundle` 中声明 `externalBin`，使其随 App 分发。
