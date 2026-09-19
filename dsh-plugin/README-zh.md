# dsh-halter

一个 [DeepSeek Harness](https://www.deepseek.com/harness/en) 插件：把
[halter](https://github.com/pengkangzhen/harness-config-manager) CLI —— 管理所有 AI 编程
harness 的 skills / MCP servers / plugins / hooks / subagents 的唯一事实源 —— 以 agent 工具
和斜杠命令的形式接进 dsh。

## 提供什么

- **`halter_cli` 工具**（模型可调，只读）：让 agent 执行 `halter scan`、`halter assess`、
  `halter sessions list|show|context|search`（例如在继续工作前查看 harness 配置清单、翻历史
  会话）。**写操作（`sync --apply`、`sessions install`、`run`、`tasks`）不对模型开放。**
- **`/halter <子命令>` 斜杠命令**（人类调用，完整 CLI）：等同在 shell 里直接敲——
  包括 `sync` 的 dry-run 预览与 `--apply`。

## 前置要求

halter 本体不随插件分发，需单独安装（Python 3.12+ / [uv](https://docs.astral.sh/uv/)）：

```bash
uv tool install harness-config-manager
halter scan   # 自检
```

若 `halter` 不在 `PATH`，通过插件的 `binary` 配置指定路径。

## 安装

```bash
dsh plugin --profile web add dsh-halter
```

需要提供 `commands` 服务的 dsh 基座（标准 dsh 基座及其 Web 客户端均提供；无 UI 的
demo 骨架与 ACP 自动化组合不提供）。

## 配置

可选，走 dsh 常规配置分层：

| 键 | 默认值 | 含义 |
|---|---|---|
| `binary` | `halter` | halter 可执行文件路径 |
| `timeoutMs` | `120000` | 子进程超时（毫秒） |
| `maxOutputChars` | `8000` | 工具渲染输出截断长度 |

## 安全说明

- 面向 agent 的工具通过白名单限定为只读子命令；任何写操作都会被拒绝并说明原因。
- halter 自身的安全语义原样生效：所有写操作默认 dry-run，须显式 `--apply`；输出自动脱敏。
- 命令经 `execFile` 执行（无 shell），参数不会被拼接进 shell 字符串。简单分词器不支持
  含空格的参数值——请使用不含引号的参数。

## 开发

```bash
cd dsh-plugin
npm install
npm run typecheck
npm run build     # 产出 lib/（随 npm 发布）
```

不发包的本地试用：把 `cordis.patch.yml` 的 `name` 指向 `src/index.ts` 的绝对路径，
运行 `dsh web --patch ./cordis.patch.yml`。

## 许可

MIT（与主仓库一致）。
