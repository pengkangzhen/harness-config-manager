# 记忆层（memory）设计前研究

> 状态：调研完成，待设计评审
> 日期：2026-09-18
> 范围：为 halter 新增第六个托管层「全局记忆」提供问题定义、社区方案盘点与落地建议

---

## 1. 问题定义

### 1.1 用户痛点

在 Claude Code 中工作时积累的全局记忆（偏好、工作习惯、项目决策、踩坑经验），切换到 Codex / Gemini CLI / OpenCode 后完全不可见。工具切换 = 记忆清零，工作不连续。

### 1.2 本机实证（2026-09-18 快照）

| 文件 | 状态 |
|---|---|
| `~/.claude/CLAUDE.md` | **90 行 / 6046 字节**，持续增长 |
| `~/.codex/AGENTS.md` | 10 行 / 824 字节 |
| `~/.gemini/GEMINI.md` | 10 行 / 824 字节 |
| `~/.config/opencode/AGENTS.md` | 10 行 / 824 字节 |

后三个文件 md5 完全相同，说明曾做过一次性手动拷贝；之后 Claude 侧继续增长，其余三个停留在 824 字节。**分叉不是未来风险，是既成事实**——一次性 copy 无法解决持续记忆的迁移，需要共享事实源或周期性收编。

---

## 2. 记忆层与现有五层的本质差异

| 维度 | skills / MCP / 插件 / hooks / subagents | 记忆层 |
|---|---|---|
| 谁写入 | 用户（人工策展清单） | **Agent 在运行时自行追加**（如 Claude Code 的 `#` 快捷键） |
| 加载方式 | 按需加载（skills 渐进披露） | **每个会话全量注入上下文** |
| 内容形态 | 结构化（TOML / JSON / frontmatter） | 非结构化散文，持续增长 |
| 质量走势 | 稳定 | 会腐烂（陈旧事实、过期路径、重复条目） |
| 同步方向 | 单向分发即可 | **必须双向**（分发 + 收编 write-back） |

结论：记忆层适合作为 halter 的第六层，但它是唯一一个「被管理对象自己会写自己」的层，同步语义需要重新设计，不能照搬现有 dialect 转换。

---

## 3. 概念拆解：「记忆」是三类东西

1. **指令 / 规则型记忆**（"回答用中文"、"用 uv 管理依赖"、"测试全绿再交付"）——纯 markdown，跨工具语义几乎一致，**应同步**。
2. **习得型记忆**（项目决策、踩坑记录、用户偏好，由 agent 追加）——大部分通用，**应同步**，但需密钥扫描与陈旧度治理。
3. **会话历史 / transcript**（`~/.claude/projects/*.jsonl`、Codex sessions）——体积大、高度私密、无稳定读取语义，**不纳入本层同步**。工作连续性真正需要的是从历史中提炼出的第 1、2 类记忆，而非原始对话。

---

## 4. 社区方案盘点（2026-09-18 调研）

### 4.1 直接同类：规则 / 指令文件同步工具

| 项目 | 形态 | 支持目标 | 成熟度 | 关键局限 |
|---|---|---|---|---|
| [agentsync](https://github.com/obielin/agentsync)（PyPI 包名 `rulesync`，v1.0.0，2026-04-22） | canonical `.agentsync/rules.md` → 生成 9 种规则文件；有 `init / sync --dry-run / status / adopt` | AGENTS.md、CLAUDE.md、.cursorrules、.cursor/rules/*.mdc、copilot-instructions.md、GEMINI.md、.windsurfrules、aider、OpenCode | **1 star**；建仓与最后提交相隔一天 | 概念验证级；纯项目级；单向生成；无全局记忆、无 write-back、无冲突语义 |
| [glooit](https://github.com/nikuscs/glooit)（v0.6.7，2026-02-15） | 跨工具同步 rules / commands / skills / agents / MCP / hooks / settings | Claude Code、Cursor、Codex、OpenCode、Roo/Cline | 25 stars；~8.1k npm 月下载；2026-05 最后提交 | 范围与 halter 五层高度重叠；记忆仍是项目级规则分发，无收编语义 |
| [ai-rules-sync / AIS](https://github.com/lbb00/ai-rules-sync)（v0.10.0，2026-08-14） | Git 仓库资产的 symlink 联邦，明确自称「不做格式转换」 | Cursor、Claude、Copilot、Codex、Trae、Gemini、Warp 等 20+ | 38 stars；~7.1k 月下载；2026-08 仍活跃 | 分发哲学与 halter skills 层同构；不处理记忆分叉 / 合并 |
| `cursor2claude` 等长尾微工具 | 单向转换器 | 个别工具对 | 一次性 | 无生态 |

**要点**：agentsync 的 README 描述的痛点与 halter 记忆层诉求一致（多个规则文件内容几乎相同、各自维护、逐渐漂移），命令设计也与 halter 相似（`adopt` 收编最完整文件为 canonical）。但它验证了需求，没有成为答案。整个赛道没有统治级成熟方案。

### 4.2 标准收敛路线：AGENTS.md

- AGENTS.md 已是事实标准，Codex、OpenCode、Cursor 及大量云端 agent 原生支持。
- **Claude Code 是最大缺口**：官方文档确认其记忆体系完全围绕 CLAUDE.md（用户级 `~/.claude/CLAUDE.md`、项目级、嵌套、`CLAUDE.local.md`、`.claude/rules/`、`@import` 语法），未见 AGENTS.md 支持。
- Gemini CLI 文档以 GEMINI.md 为一等公民。

**Claude Code 文档中影响 halter 设计的两个事实**：

1. 存在用户级 `~/.claude/rules/` 规则目录——比改写 `~/.claude/CLAUDE.md` 更干净的分发落点（每个 canonical 记忆条目一个文件，不碰用户手写的 CLAUDE.md）。
2. **Cowork 会话会跳过指向工作目录之外的 symlink 型 `~/.claude/CLAUDE.md` 与 symlink 型 rules 文件**——直接否定「全部工具 symlink 到同一 canonical 文件」的激进方案。

### 4.3 另一条技术路线：共享记忆服务（MCP）

| 方案 | 数据 | 语义 |
|---|---|---|
| `@modelcontextprotocol/server-memory`（官方参考实现，知识图谱记忆） | **约 45.5 万 npm 月下载** | agent 需主动调用 MCP 工具查询——是"情景记忆"，不是每会话自动注入的 ambient 指令 |
| [basic-memory](https://github.com/basicmachines-co/basic-memory) | 3,986 stars；本周仍活跃 | MCP 知识库路线，同上 |

这条路成熟度最高，但解决的是另一层问题：MCP 记忆是**按需查询**，CLAUDE.md / AGENTS.md 是**每会话强制注入**的工作契约，前者替代不了后者。对 halter 而言它们是可选集成项（halter 已有 MCP 层，一条配置即可把记忆服务铺到所有工具），而非竞争者。

### 4.4 相邻玩家：mem0ai / OpenMemory

[mem0ai/openmemory](https://github.com/mem0ai/openmemory)（35 stars，2026-07 建仓，背靠 mem0 组织）走**会话搬运**路线：在 Claude Code / Codex / OpenCode 之间导入导出会话记录，TUI 选择要搬哪些对话，`openmemory port --from claude --to codex --id <session-id>`。

其 roadmap 明确包含 "Port Skills, MCPs, Plugins, and CLAUDE.md / AGENTS.md"——正从会话历史方向切入，目标覆盖 halter 全部领土 + 记忆层。目前规模尚小，但验证了两件事：

1. 「切 harness 不丢上下文」是被反复确认的真实需求；
2. halter 已有的 `halter-sessions`（项目级会话复用）与之正面重叠，值得互相借鉴 session 数据模型。

---

## 5. 空档分析

社区现有方案没有一个同时覆盖以下四件事：

1. **全局记忆**：用户级 `~/.claude/CLAUDE.md` ↔ `~/.codex/AGENTS.md` ↔ `~/.gemini/GEMINI.md`，而非项目级规则；
2. **双向收编**：agent 运行时写入各工具记忆文件的新增内容回流 canonical；
3. **冲突语义**：managed block、`--prefer`、分叉裁决；
4. **安全扫描**：密钥脱敏、提示注入面控制。

同时，Claude Code 的反 symlink 政策与 AGENTS.md 缺位意味着 CLAUDE.md ↔ AGENTS.md 这座桥在可见未来仍需外置工具来修——halter 的窗口真实存在。

---

## 6. 方案空间对比

| 策略 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A. 一次性拷贝 | 手动 cp | 零成本 | **已被本机实证证伪**——必然分叉 |
| B. 共享文件（symlink） | 各工具记忆文件 symlink 到 canonical（如 `~/.agents/memory/MEMORY.md`） | 读写连续性最好 | Claude Cowork 明确跳过外指 symlink；并发追加丢更新；工具专属指令互相污染 |
| C. 托管区块 + 收编 | 在各工具记忆文件内维护 `<!-- halter:memory:start/end -->` 区块（同 hooks 层归属标记，只碰自家电位），区块外留给工具；`halter sync` 时 diff 收编新增行 | 与 halter 安全哲学一致；支持工具特定内容过滤；不破坏工具自身的记忆写入 | 语义复杂；需冲突裁决（可复用 `--prefer`）；非即时 |

**推荐**：全局记忆以 C 为主、B 为可选激进模式（需在文档中标注 Claude Cowork 限制）；Claude 侧优先考虑写入 `~/.claude/rules/` 而非改写 CLAUDE.md。项目级记忆（repo 根 AGENTS.md / CLAUDE.md / GEMINI.md）中 symlink 是社区常见做法，因项目级文件较少被 agent 运行时整写，可作为项目级子模式的默认。

---

## 7. 记忆层特有风险

- **注入面放大**：记忆内容在每个会话被当作指令执行，同步会把单点提示注入风险乘以工具数。收编（adopt）需与 MCP 层同级的审查。
- **密钥泄漏**：CLAUDE.md 中常出现 token、内网主机名、私有路径。应复用 `adopt --mcp` 的密钥检测 / `${VAR}` 占位逻辑。
- **工具特定内容串味**："用 `claude plugin install`"、"运行 /doctor" 等指令进入其它工具的 AGENTS.md 即为噪音。需要 section 级标记（如 `## universal` / `## claude-only`），分发时过滤。
- **上下文预算**：记忆每会话全量注入（skills 是按需加载），无限增长直接消耗 token。Claude 官方建议单个 CLAUDE.md 控制在 200 行以内。`halter doctor` 应增加记忆体检：体积阈值、陈旧度、重复段落。
- **并发写**：两个 agent 同时追加会丢更新。托管区块模式下冲突可见（下次 sync 报 diff），symlink 模式下会静默丢失——这也是推荐 C 的原因之一。

---

## 8. 建议落地路径

### v1（解决核心痛点）

- 新增 memory 层，canonical：`~/.agents/memory/MEMORY.md`（与现有 `~/.agents/skills`、`~/.agents/agents` 生态路径一致）。
- `halter adopt --memory --from claude`：从最丰富的记忆文件收编（启发式同 skills：行数 / 字节数 / 最近修改），密钥扫描脱敏。
- 分发目标：
  - Claude：`~/.claude/rules/`（每条记忆一个文件）或 `~/.claude/CLAUDE.md` 托管区块；
  - Codex：`~/.codex/AGENTS.md`；
  - Gemini：`~/.gemini/GEMINI.md`；
  - OpenCode：`~/.config/opencode/AGENTS.md`。
- 项目级：repo 根以 `AGENTS.md` 为 canonical，其余生成 shim 或 symlink。

### v2

- section 级工具过滤（universal / claude-only / codex-only）。
- `halter doctor` 记忆体检（体积、陈旧度、重复）。
- `--mode link` 激进模式（文档标注 Claude Cowork 限制）。

### 明确不做

- 会话 transcript 同步（体积 + 隐私 + 无读取对称性）。
- 向量库 / embedding 型记忆（格式不透明，等稳定后再评估）。
- 自建记忆 MCP 服务——社区已有成熟方案（官方 server-memory、basic-memory），halter 通过现有 MCP 层集成即可。

---

## 9. 调研方法与数据来源

- 调研日期：2026-09-18。
- 数据源：PyPI JSON API、npm registry / downloads API、GitHub（raw README + ungh.cc 镜像元数据）、Anthropic Claude Code 官方文档、Gemini CLI 官方 README、各项目 README。
- 本机快照：`~/.claude/CLAUDE.md` 与 `~/.codex/AGENTS.md` 等文件的行数 / 字节数 / md5。
- 已知未覆盖：Cursor 官方 changelog 的 AGENTS.md 支持版本号（文档站为 JS 渲染，未取到静态文本）；agents.md 官网采纳列表（网络原因未完整加载）。
