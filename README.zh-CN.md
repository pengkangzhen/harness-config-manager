# harness-config-manager (`hcm`) // [中文文档](README.zh-CN.md)

Detect, inventory, and distribute user-level **skills / MCP servers / plugins** across AI coding tools.

一台机器上往往装着多个 AI 编码工具（Claude Code、ZCode、Codex、Cursor、VS Code Copilot、Gemini CLI、OpenCode……），每个工具各管一套用户级 skills、MCP 配置和插件，格式互不相同（JSON 的 `mcpServers`/`servers`/`.mcp`、TOML 的 `[mcp_servers.*]`、command 数组……），配置渐渐各自为政。`hcm` 用单一事实源统一管理这三层：先检测与盘点，再按需分发。

## 安装

```bash
uv tool install .          # 从本仓库安装，得到 hcm 命令
```

依赖：Python 3.12+，typer / rich / tomlkit。

## 快速上手

只有两个命令：

```bash
hcm scan                   # 看一眼：装了哪些工具、各配置了什么 skills/MCP/插件、有无健康问题
hcm scan -d skills -d mcp  # 查看某层明细；--json 输出机器可读格式

hcm sync                   # 同步一下：清单/库 -> 所有工具（默认 dry-run）
hcm sync --apply           # 实际执行（写前自动备份；冲突默认跳过保护）
hcm sync --no-skills --no-plugins   # 只同步 MCP 层（三层默认全开，按需关闭）
hcm sync --prefer library  # 冲突时备份工具侧后以清单覆盖（默认 skip）
```

清单为空时 `sync` 会自动从最全的工具收集（MCP 选 server 最多的，插件选启用最多的 claude 系工具，`--from` 可覆盖）；skills 库外独有的会自动入库，同名分叉会列出让你裁决。

## 三层模型

| 层 | 事实源 | 分发方式 |
|---|---|---|
| skills | skills 库目录（三层回退：配置指定 > `~/.agents/skills`（cc-switch 用户兼容）> `~/.config/hcm/library/skills`） | 条目级 symlink；同名冲突默认跳过，`--prefer library` 备份后覆盖 |
| MCP | `~/.config/hcm/mcp.toml`（canonical：stdio/http、env、headers） | 六方言转换写入：claude（`.claude.json` 读改写）、zcode、codex（TOML 保注释）、cursor、vscode（`servers` 键）、gemini、opencode（command 数组）；http 类型只分发支持的工具 |
| 插件 | `~/.config/hcm/plugins.toml`（family: claude / codex / vscode） | claude 用 `claude plugin install -y`；zcode 镜像 claude 缓存 + 登记同源清单；codex 写 TOML 开关；vscode 用 `code --install-extension` |

## 安全设计

- **一切写操作默认 dry-run**，`--apply` 才执行；skills 替换前自动备份到 `~/.config/hcm/backups/<时间戳>/`。
- **密钥永不进清单**：`adopt --mcp` 把疑似密钥的值换成 `${VAR}` 占位符，真实值写入 `~/.config/hcm/secrets.toml`（权限 0600）；同步时按 环境变量 > secrets 展开，缺变量的 server 整体跳过，不写半截配置；终端输出永远脱敏。
- `~/.claude.json` 是大状态文件，只做读-改-写合并（保留其余键），原子替换落盘。

## 配置

`~/.config/hcm/config.toml`（可选）：

```toml
library = "~/.agents/skills"     # skills 事实源；缺省走三层回退
exclude_skills = []              # 不分发的 skill 名单
exclude_mcp = []
```

## v1 边界

- Cursor 插件仅盘点不安装（市场机制不透明）。
- Continue / Cline / Trae / Aider / Windsurf 仅覆盖 skills 层。
- `sync --plugins` 的 zcode 目标要求该插件在 claude 侧已安装（镜像来源）。

## 支持新工具

在 `registry.py` 的 `TOOLS` 中追加一条 `ToolSpec`（CLI 命令名、配置目录、skills 目录），检测与 skills 层即刻生效；MCP / 插件层需在 `mcp.py` / `mcp_write.py` / `plugins.py` / `plugin_sync.py` 中补充该工具的方言读写器。

## 测试

```bash
uv run pytest               # 28 项单测，全部使用假 HOME，绝不触碰真实配置
```
