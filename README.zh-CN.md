# harness-config-manager (`hcm`) // [中文文档](README.zh-CN.md)

Detect, inventory, and distribute user-level **skills / MCP servers / plugins / hooks / subagents** across AI coding tools.

一台机器上往往装着多个 AI 编码工具（Claude Code、ZCode、Codex、Cursor、VS Code Copilot、Gemini CLI、OpenCode……），每个工具各管一套用户级 skills、MCP 配置、插件、hooks（钩子：在工具执行特定动作前后自动运行的 shell 命令）和 subagents（子代理：每代理一个 Markdown 定义文件），格式互不相同（JSON 的 `mcpServers`/`servers`/`.mcp`、TOML 的 `[mcp_servers.*]`、command 数组……），配置渐渐各自为政。`hcm` 用单一事实源统一管理这五层：先检测与盘点，再按需分发。

## 安装

```bash
uv tool install .          # 从本仓库安装，得到 hcm 命令
```

依赖：Python 3.12+，typer / rich / tomlkit。

## 快速上手

只有两个命令：

```bash
hcm scan                   # 看一眼：装了哪些工具、各配置了什么 skills/MCP/插件/hooks/subagents、有无健康问题
hcm scan -d skills -d mcp  # 查看某层明细；--json 输出机器可读格式

hcm sync                   # 同步一下：清单/库 -> 所有工具（默认 dry-run）
hcm sync --apply           # 实际执行（冲突默认跳过保护）
hcm sync --no-skills --no-plugins --no-mcp   # 只同步 hooks 层（五层默认全开，按需关闭）
hcm sync --prefer library  # 冲突时以清单覆盖（默认 skip）
```

清单为空时 `sync` 会自动从最全的工具收集（MCP 选 server 最多的，插件选启用最多的 claude 系工具，hooks 选条目最多的目标工具；第三方注入的 hook 条目也一并收编，排除名单走 `exclude_hooks`；`--from` 可覆盖）；skills 与 subagents 库外独有的会自动入库，同名分叉会列出让你裁决。

## 五层模型

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
- hook command 的死配置检测是保守的：仅对「单一脚本路径」形式的命令判定存在性，复合 shell 表达式不误报也不检查。

## 支持新工具

在 `registry.py` 的 `TOOLS` 中追加一条 `ToolSpec`（CLI 命令名、配置目录、skills 目录），检测与 skills 层即刻生效；MCP / 插件层需在 `mcp.py` / `mcp_write.py` / `plugins.py` / `plugin_sync.py` 中补充该工具的方言读写器；hooks 层同理见 `hooks.py` / `hooks_write.py`；subagents 只需加 `agents_dirs` 条目。

## 测试

```bash
uv run pytest               # 57 项单测，全部使用假 HOME，绝不触碰真实配置
```
