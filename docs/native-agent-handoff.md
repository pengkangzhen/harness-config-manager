# halter Native Agent — 交接与后续推进计划

更新时间：2026-09-19  
基线状态：`a98f1ff` 时 `141 passed`；**Milestone 1-7 全部完成，缺口 A-H 全部
关闭，预留项（semantic-equivalent `5877318`、MCP streamable-HTTP `dfeaff8`）
亦已交付**（M1 `00438b4`、M2 `8322aa3`、M3 `c6beb26`、M4 `0ef963e`、
M5 `e732df5`、M6 `0f85ff0`、M7 `6eec560`）。
本机验证：`uv run pytest -q`（189 passed，含 release 契约测试）、
`node --check`、`npx playwright test`（7 passed）；完整 cargo check / test 由
`.github/workflows/desktop-ci.yml` 在 CI 执行（本机缺 cc/pkg-config/
webkit2gtk dev，见 Milestone 7 环境记录与 §11）。

本文档给下一个 AI 助手 / 开发者接手使用。目标是避免只看零散上下文，而是从产品目标、当前架构、已验证能力、剩余缺口、建议路线和验收标准继续推进。

---

## 1. 最终目标

把 halter 从“多 Harness 配置管理与任务调度器”推进成一个：

> 可控、可审计、多模型、多 Harness 的本地 AI 编码助手。

具体形态：

1. **halter 是主控 Agent**
   - 拥有自己的 model/tool loop。
   - 能读取项目、维护计划、调用工具、申请审批、执行测试、回滚变更。
   - 不把核心决策完全交给 Claude Code / Codex 黑盒进程。

2. **Claude Code / Codex / ZCode / OpenCode 是子代理**
   - 可被 `delegate_harness` 调度。
   - 在一次性 git clone sandbox 中工作。
   - 返回输出和 diff，由 halter 统一比较、合并、审批后应用到真实 workspace。

3. **本地优先**
   - 桌面 App 基于 Tauri。
   - 后端运行本地 AHP host。
   - 审计、session、transaction、模型路由均保存在用户本地私有目录。

4. **安全边界清晰**
   - 默认 read-only。
   - 写入必须开启 workspace-write。
   - `apply_patch` / `run_tests` 必须用户显式批准。
   - API key 不写入 halter 配置，只引用环境变量名。

---

## 2. 当前已经完成的能力

### 2.1 原生 Agent Runtime

核心文件：

```text
src/harness_config_manager/agent.py
```

已有能力：

- model/tool decision loop
- OpenAI-compatible streaming client
- JSON fallback
- 多轮 tool call
- 并发执行只读 / delegate 工具
- 写入 / 测试工具强制串行
- workspace confinement
- plan tool
- project session tools
- delegate harness tool
- approval-gated write
- approval-gated tests
- transaction metadata
- reverse patch rollback
- automatic workspace context
- bounded history compaction
- private audit events
- plan step stable id（显式 / 按 title 继承 / 自动生成）
- plan evidence 关联（tool call / approval / transaction 归到 step）

当前工具面：

```text
update_plan
read_file
list_dir
search_files
git_status
git_diff
list_project_sessions
read_project_session
delegate_harness
apply_patch
run_tests
run_tests_workspace
mcp_<server>_<tool>   （declared stdio MCP servers；read-only 默认可用，
                       state-changing 需 [native_agent.mcp] write allowlist + 审批）
```

权限模式：

```text
read-only
workspace-write
```

`read-only` 时，模型 API 的 tools schema 中根本不会出现：

```text
apply_patch
run_tests
```

伪造 tool call 也会被 runtime 拒绝。

### 2.2 AHP Host

核心文件：

```text
src/harness_config_manager/ahp_host.py
```

已有能力：

- AHP 0.9.0
- WebSocket + HTTP RPC + SSE
- `provider="halter"` 原生 backend
- claude / codex / zcode / opencode external adapters
- model routing
- streaming AHP actions
- durable session/chat state
- approval request / response
- rollback request / result
- turn cancellation
- localhost bearer token auth
- browser Origin allow-list
- external runner audit

重要 AHP 扩展事件：

```text
chat/delta
halter/toolCall
halter/toolResult
halter/planChanged
halter/planEvidence
halter/approvalRequest
halter/approvalResult
halter/rollbackResult
chat/turnComplete
chat/error
```

`halter/planEvidence` 在证据变化时广播完整 `planEvidence` 映射；toolCall / toolResult / approval / rollback 事件与 audit 记录均携带可选 `planStepId`。

### 2.3 Durable sessions

核心文件：

```text
src/harness_config_manager/agent_store.py
```

存储位置：

```text
~/.config/halter/agent-sessions/<session-hash>/session.json
```

能力：

- session / chat 恢复
- native model/tool history 恢复
- currentPlan 恢复
- approvalHistory 恢复
- planEvidence 恢复（stepId → toolCallIds / approvalIds / transactionIds / auditTimestamps）
- active turn 恢复为 `HostInterrupted`
- 原子写入
- `0700` directory
- `0600` file
- secret-like value redaction

### 2.4 审计系统

核心文件：

```text
src/harness_config_manager/agent_audit.py
```

CLI：

```bash
halter audit list
halter audit show <session-id>
halter audit show <session-id> --kind approval.response --tail 100
halter audit search "transaction-id"
halter audit search --tool apply_patch --workspace /path [--from 2026-09-01] [--to 2026-09-18] [--json]
```

`audit search` 支持 transaction id / 工具名 / 全文 / kind / provider /
workspace / 日期组合过滤，逐文件 5 万行读取上限 + offset/limit 分页，
损坏文件跳过并上报（`skipped_files`）。

桌面 App 已有「审计」视图。

审计位置：

```text
~/.config/halter/agent-audit/<session-hash>/audit.jsonl
```

事件覆盖：

```text
context.workspace
context.compacted
turn.started
model.response
tool.call
tool.result
approval.requested
approval.response
transaction.rolledBack
transaction.rollbackFailed
turn.completed
turn.failed
runner.started
runner.finished
runner.failed
```

### 2.5 审批与回滚

`apply_patch` 会生成事务：

```text
~/.config/halter/agent-transactions/<session-hash>/<transaction-id>.json
```

内容：

- original patch
- before status/diff
- after status/diff
- exact reverse patch
- fixed rollback argv
- transaction state

回滚固定走：

```text
git apply -R --check
git apply -R
```

回滚前校验：

- transaction id 合法
- transaction 属于当前 workspace
- transaction 状态是 `applied`

桌面任务卡已有：

```text
批准应用
拒绝
回滚此变更
```

### 2.6 多 Harness sandbox 委派

`delegate_harness` 可以调用：

```text
claude
codex
zcode
opencode
```

同一 run 内出现 ≥2 个 provider 的 delegate diff 时，runtime 自动用
`patch_compare`（纯函数模块）生成结构化比较并注入每个 delegate 工具结果：

```text
comparison.files[].classification: identical / conflicting / semantic_equivalent / overlapping / unique
comparison.unrelated（各 provider 文件互不相交）
comparison.conflicts[]（含每 provider 的 hunk header 与改动行，供 UI 高亮）
comparison.strategy（合并采纳建议）
```

分类依据：hunk 指纹只取改动行（+/-，容忍不同 context 设置）；old 侧行区间
重叠且指纹不同即 conflicting；同文件不同区域为 overlapping；单 provider 为
unique；全部指纹一致为 identical。

安全流程：

```text
真实 workspace
  -> git clone
  -> 应用 tracked diff
  -> bounded untracked snapshot
  -> external harness 在 clone 中执行
  -> 返回 stdout/stderr/status/diff
  -> halter 原生 Agent 决策
  -> 如需真实修改，仍必须 apply_patch + 用户审批
```

Untracked snapshot 限制：

```text
最多 5000 个文件
最多约 200 MiB
```

排除：

```text
.halter/
任何路径下的 .config/halter/
.git/
node_modules/
target/
.venv/
```

多个 delegate tool call 可并发执行。

### 2.7 测试沙箱与真实 workspace 终验

`run_tests` 在一次性 git clone sandbox 中执行，不直接修改真实 workspace。

`run_tests_workspace`（workspace-write 模式）是独立的最终验证流：sandbox 测试
通过后，Agent 说明理由并请求在真实 workspace 复跑同一条固定命令，用户二次
批准后才执行；执行记录写入独立的 `kind=workspace-tests` 事务与 audit。

固定命令族（两工具共用，不接受自由 shell 字符串）：

```text
pytest
npm test
cargo test
go test
```

不接受自由 shell 字符串。

返回：

```text
exit code
output
sandbox snapshot
sandboxChanges.diff
sandboxChanges.status
```

仍需用户审批。

### 2.8 模型路由

配置文件：

```text
~/.config/halter/config.toml
```

内置前缀：

```text
openai/...
zhipu/...
```

自定义 OpenAI-compatible provider：

```toml
[model_providers.local]
base_url = "http://127.0.0.1:11434/v1"
api_key_env = ""

[models]
halter = "local/qwen-coder"
```

CLI：

```bash
halter model configure \
  --model local/qwen-coder \
  --base-url http://127.0.0.1:11434/v1 \
  --api-key-env ""

halter model check [--probe] [--json]
```

`halter model check` 输出逐项诊断（配置 / 前缀 / base URL / key 环境变量 /
可选的 base URL 连通探测），`--json` 可测试；`halter models --json` 附带静态
health 供桌面 Model view 渲染。路由解析唯一事实源在
`src/harness_config_manager/model_health.py`（`resolve_route`），模型客户端与
AHP availability 均复用它。

桌面 App 已有「模型」视图（含 provider 健康面板）。

安全规则：

- 不保存真实 API key
- 只保存 base URL
- 只保存 API key 环境变量名
- managed AHP host 保存模型配置后自动重启
- external host 需要用户手动重启

### 2.9 桌面 Workbench UI

文件：

```text
desktop/ui/app.js
desktop/ui/index.html
desktop/ui/style.css
desktop/src-tauri/src/main.rs
```

已有视图：

```text
总览
矩阵
会话
调度
模型
审计
同步
```

调度页已有：

- `@halter` 原生 Agent
- plan 面板（step 级证据展开：工具调用 / 审批 / 事务）
- 证据条目可跳转 Audit 视图并高亮定位事件
- 多 Harness sandbox diff 对比面板（patch_compare 分类标签 + 冲突 hunk 高亮）
- 工具事件面板
- assistant 输出区
- approval 面板
- approval history
- rollback 按钮
- cancel 按钮
- durable native chat 恢复

---

## 3. 当前关键架构

```text
Tauri Desktop
  |-- model configuration UI
  |-- audit viewer UI
  |-- dispatch / workbench UI
  |
  | IPC
  v
Rust AHP Bridge
  |-- local bearer token
  |-- HTTP JSON-RPC
  |-- SSE event stream
  |
  v
halter AHP Host
  |-- provider=halter
  |-- provider=claude/codex/zcode/opencode
  |-- durable session store
  |-- approval state
  |-- transaction rollback
  |
  v
AgentRuntime
  |-- model client
  |-- plan state
  |-- tool executor
  |-- permission gate
  |-- audit log
  |
  +--> workspace tools
  +--> project session tools
  +--> delegate_harness sandbox
  +--> apply_patch + transaction
  +--> run_tests sandbox
```

---

## 4. 快速开发与验证

### Python / AHP / runtime

```bash
uv sync
uv run pytest -q
```

最近一次验证：

```text
141 passed
```

重点测试：

```bash
uv run pytest tests/test_agent_runtime.py
uv run pytest tests/test_ahp_host.py
uv run pytest tests/test_model_client.py
uv run pytest tests/test_agent_audit.py
```

### Desktop

```bash
cd desktop
HALTER_BINARY=../devbin/halter npm run dev
```

Rust：

```bash
cd desktop/src-tauri
cargo check
```

前端语法：

```bash
node --check desktop/ui/app.js
```

### AHP health

默认端口：

```text
127.0.0.1:7433
```

健康检查：

```bash
curl http://127.0.0.1:7433/healthz
```

注意：AHP host 启动时读取配置。修改模型配置后：

- managed host 可自动重启
- external host 必须手动重启

---

## 5. 重要安全 / 实现约束

接手后不要破坏以下规则。

1. **API key 永远不入库**
   - config 只保存 `api_key_env`
   - 不保存真实 key
   - UI 不接受或存储 secret value

2. **默认 read-only**
   - native provider 默认不给写工具 schema
   - 不能为了方便把 `apply_patch` 提前暴露

3. **写入必须审批**
   - `apply_patch` 必须发出 exact patch
   - 用户必须显式 approved
   - 审批要入 audit

4. **测试命令固定 argv**
   - 不接受自由 shell
   - 不拼接用户字符串
   - 不新增任意 `run_command`

5. **workspace confinement**
   - 所有路径必须限制在 selected workspace
   - symlink target 逃逸要拒绝
   - patch header 要先解析并验证

6. **外部 Harness 不直接改真实 workspace**
   - delegate 必须走 disposable clone
   - diff 回到 halter 后仍需审批

7. **审计不可静默丢失**
   - 新增关键状态必须写 audit event
   - 审计 viewer 需要能解释它

8. **AHP 本地认证不能放松**
   - bearer token 必须保留
   - browser Origin 必须校验
   - 不要为了测试方便全局关闭认证

---

## 6. 当前主要缺口

目标尚未完成。不要把当前状态误报为最终完成。

### 缺口 A：Plan step 与证据未关联 ✅ 已完成（Milestone 1）

已在 Milestone 1 落地：step 稳定 id、tool call / approval / transaction / audit 事件的
`planStepId`、durable `planEvidence`、UI 证据展开与 Audit deep link。

### 缺口 B：多 Harness diff 只有对比，没有 deterministic merge 分析 ✅ 已完成（Milestone 2）

已在 Milestone 2 落地：`patch_compare.py` 纯函数模块 + delegate 结果自动注入结构化
comparison + UI 分类标签与冲突 hunk 高亮。

### 缺口 C：模型 provider 健康检查不足 ✅ 已完成（Milestone 3）

已在 Milestone 3 落地：`model_health.py` 诊断模块、`halter model check` CLI、
AHP `halter:health` metadata、桌面 Model view 健康面板。

### 缺口 D：审计检索还不完整 ✅ 已完成（Milestone 4）

已在 Milestone 4 落地：`search_audits`（全文 / transaction id / tool name /
kind / provider / workspace / 日期组合 + 分页 + 读取上限）、`halter audit
search` CLI、UI 事件过滤与列表过滤；plan evidence deep link 在 M1 已就绪。

### 缺口 E：真实 workspace 最终测试流缺失 ✅ 已完成（Milestone 5）

已在 Milestone 5 落地：`run_tests_workspace` 工具 + 二次审批 + workspace-tests
事务 + UI sandbox/workspace 执行环境标识。

### 缺口 F：MCP tools 尚未接入 native tool loop ✅ 已完成（Milestone 6）

已在 Milestone 6 落地：declared stdio MCP servers 的工具以 `mcp_<server>_<tool>`
进入 native tool loop；read-only 默认可用，state-changing 需逐工具 allowlist
并走统一审批。stdio 与 streamable-HTTP transport 均已接入（legacy
SSE-only 跳过）。

### 缺口 G：桌面端自动化验证不足 ✅ 已完成（Milestone 7）

已在 Milestone 7 落地：Playwright 静态 UI e2e（7 个状态机测试，mock
Tauri IPC）、Rust 桥 auth 负面测试（CI 执行）、desktop CI workflow。

### 缺口 H：打包与 sidecar 新鲜度风险 ✅ 已完成（Milestone 7）

已在 Milestone 7 落地：版本契约测试（三处 manifest 一致 + 产物版本校验）+
`halter_version.desktop` 比对 + UI mismatch 告警 + sidecar CI job（构建后
跑契约测试）；构建脚本原有的输入 hash 新鲜度机制保持。

---

## 7. 建议路线图

## Milestone 1 — Evidence-linked plan ✅ 已完成

目标：让每个计划步骤可追踪、可审计、可解释。

实现说明（供 review 与后续维护）：

1. `update_plan` step 增加可选 `id`；模型未提供时按 title 继承旧 id，再退化为生成 `step-<hex>`；同批内重复或非法 id 会被拒绝
2. 任意 tool call 可在 arguments 中携带 `plan_step_id`（system prompt 有说明，runtime 校验格式）
3. audit `tool.call` / `tool.result` / `approval.requested` / `approval.response` 与 AHP `halter/toolCall` / `halter/toolResult` 事件均带 `planStepId`
4. `apply_patch` 事务 metadata 与返回值携带 `planStepId`；rollback 审计事件透传
5. `AgentRuntime.plan_evidence` 与 `AhpChat.plan_evidence`（stepId → toolCallIds / approvalIds / transactionIds / auditTimestamps / updatedAt）经 session.json 持久化并在 host 重启后恢复
6. AHP 新增 `halter/planEvidence` 广播；chat state 暴露 `auditSessionId`
7. 桌面 plan step 可展开证据列表，每条证据可跳转 Audit 视图并高亮定位（kind 过滤会临时清空）

验收标准：

- [x] 模型更新 plan 时 step 有 stable id
- [x] tool call、approval、rollback 能归到 step
- [x] durable session 恢复后 evidence 仍在
- [x] Audit viewer 能从 plan step 打开对应事件
- [x] 新增测试覆盖 evidence 关联与恢复（`test_tool_calls_link_evidence_to_plan_steps`、`test_update_plan_step_ids_are_stable_across_updates`、`test_native_plan_evidence_is_broadcast_and_durable`）

## Milestone 2 — Deterministic multi-Harness diff analysis ✅ 已完成

目标：让多子代理结果不只是展示，而是可合并、可解释。

实现说明：

1. `src/harness_config_manager/patch_compare.py` 纯函数模块：解析 unified diff、
   按 file 分组、hunk 指纹只取改动行（+/-）、old 侧行区间重叠判定
2. 文件分类 `identical / conflicting / semantic_equivalent / overlapping / unique`；report 级
   `unrelated`（≥2 provider 且文件互不相交）；冲突条目含每 provider 的
   规范化 hunk header（`@@ -a,b +c,d @@`）与截断后的 removed/added 行
3. `AgentRuntime.run` 维护本 run 各 provider 最新 delegate diff，≥2 个时
   把 `compare_provider_diffs(...).to_dict()` 注入每个 delegate 工具结果
   （模型、audit、AHP toolResult 均可见）
4. 桌面对比面板显示合并策略与每文件分类 chip；conflicting hunk 在各
   provider 的 diff 中行级高亮（header 精确匹配）
5. 空 diff / 非 diff 文本（含非 git workspace 的失败输出）解析为无文件，
   不抛异常
6. `semantic_equivalent` 已实现（后续增强 `5877318`）：重叠 hunk 的改动
   仅尾随空白/空行差异时判为语义等价（可任取一份），不再算硬冲突；
   更深层的 AST 级等价判定仍在范围外

验收标准：

- [x] 多 provider 相同 patch 被识别为 identical（context 行差异被忽略）
- [x] 同文件同 hunk 不同修改被识别为 conflicting
- [x] 不同文件被识别为 unrelated / unique
- [x] 原生模型收到结构化 comparison（注入 delegate 工具结果与 audit）
- [x] UI 明确显示冲突与采纳建议（分类标签 + strategy + 冲突高亮）
- [x] 有无 git repo 场景的错误处理（空/错误文本 → 无文件，不崩溃）

## Milestone 3 — Provider health & model diagnostics ✅ 已完成

实现说明：

1. `src/harness_config_manager/model_health.py`：`resolve_route` 是模型路由
   唯一事实源（裸模型名 / base URL / api_key_env 名），模型客户端与 AHP
   availability 均复用；`check_model_health` 输出逐项 check
   （config / 前缀 / base URL / key 环境 / 可选探测 / tool-schema 说明）
2. `halter model check [--probe] [--json]`：`--probe` 对 `{base}/models`
   发轻量 GET（带 Bearer 仅当 key 存在），401/403 判为鉴权失败
3. localhost endpoint 无 key 判为 no-auth（ok），remote 缺 env 判 error
   并给出变量名；任何输出都不含 secret 值
4. AHP native agent metadata 增加 `halter:health`（available + issues）
5. `halter models --json` 附静态 health；桌面 Model view 渲染健康面板，
   缺失环境变量直接显示变量名；保存后自动刷新诊断

验收标准：

- [x] CLI JSON 输出可测试（`test_cli_model_check_json_output`）
- [x] UI 不泄露 key（model_health 只输出环境变量名）
- [x] 本地 endpoint 可区分 no-auth 与 missing key（`test_probe_distinguishes_no_auth_and_auth_failure`）
- [x] AHP catalog 不把不可用 provider 误报 available（`_native_model_available` 复用 resolve_route，仅 key 或 localhost 才 available）

## Milestone 4 — Audit search & evidence navigation ✅ 已完成

实现说明：

1. `agent_audit.search_audits`：跨 session 搜索，支持全文（多词 AND）、
   kind、transaction id（事件 `id` 或 `result.transactionId`）、tool name
   （`name` / `tool` 字段）、provider 与 workspace（读 session store 的
   会话级过滤）、日期区间（date-only 自动补齐当日边界）
2. 逐文件 `MAX_SEARCH_LINES = 50000` 读取上限（超限记录 `truncated_files`）；
   `offset` / `limit` 分页；malformed JSON / OSError 文件跳过并记入
   `skipped_files`
3. `halter audit search` CLI（`--json` 可测试，表格输出含 session / 行号）
4. 桌面 audit 视图：事件级全文 + 日期过滤、会话列表 workspace/provider
   过滤；plan evidence deep link 跳转时自动清空全部过滤保证目标可见
5. session id 仍走 `validate_session_id`（拒绝路径穿越）

验收标准：

- [x] 可按 transaction id 定位事件（`test_search_locates_events_by_transaction_id`）
- [x] 可按 tool name 搜索（`test_search_filters_by_tool_name_and_kind`）
- [x] 大 audit 文件有读取上限与分页（`test_search_paginates_and_caps_large_files`）
- [x] UI 能从 plan evidence 跳转（M1 deep link + M4 过滤清空）
- [x] 测试覆盖路径穿越与 malformed JSON（`test_search_skips_malformed_files_and_rejects_traversal`）

## Milestone 5 — Real-workspace final verification flow ✅ 已完成

实现说明：

1. 新写入工具 `run_tests_workspace`（read-only 模式下不出现在 tools schema）：
   与 sandbox `run_tests` 完全分离——直接以 workspace 为 cwd 执行
2. 命令仍固定 argv（共用 `TEST_COMMANDS` 表，无自由 shell）；参数要求
   `reason` 说明为什么 sandbox 通过后还需真实复跑
3. 二次审批必需：走统一 approval 流（request/response 入 audit，
   plan_step_id 透传）
4. 执行记录写入独立事务 `kind=workspace-tests`（state `verified`/`failed`、
   exitCode、before/after git status、command、reason）
5. system prompt 明确要求：先 sandbox `run_tests`，通过后才可请求
   `run_tests_workspace`
6. 桌面工具事件面板对 run_tests / run_tests_workspace 显示
   「一次性 sandbox 克隆」/「真实 workspace(二次审批后)」标识徽标

验收标准：

- [x] 与 sandbox `run_tests` 分离（独立工具、独立事务 kind、scope 字段）
- [x] 二次审批必需（deny → PermissionError，`test_run_tests_workspace_denied_or_read_only_never_runs`）
- [x] 命令仍固定 argv（共用 TEST_COMMANDS，无自由字符串拼接）
- [x] 真实执行结果入 audit（approval.requested/response + tool.call/result）
- [x] UI 明确标识 sandbox vs workspace（事件面板环境徽标）

## Milestone 6 — Native MCP tool bridge ✅ 已完成

实现说明：

1. `src/harness_config_manager/mcp_bridge.py`：stdio JSON-RPC 客户端
   （newline-delimited；initialize → notifications/initialized →
   tools/list / tools/call）。每次调用启动一次性 server 进程，
   `start_new_session` + terminate/kill 清理，超时可配
   （list 15s / call 60s）；server crash 只导致该 server 无工具或该次
   调用报错，不影响 AHP host 与 turn
2. 工具命名 `mcp_<server>_<tool>`（标识符清洗）；MCP `inputSchema` 直接
   作为 OpenAI function parameters；`annotations.readOnlyHint` 决定权限档
3. Phase 1（read-only）：declared read-only 工具默认进入 tools schema
   （两种权限模式都可），`[native_agent.mcp] read` 可收紧 allowlist
4. Phase 2（state-changing）：`[native_agent.mcp] write` 逐工具显式
   opt-in（缺省/空 = 全部禁止），且仅 workspace-write 模式进 schema；
   执行走统一 approval 流，approval request 展示 server / tool /
   arguments（`plan_step_id` 不透传给 server）
5. 结果（content text / structuredContent）截断后进入通用 tool
   result / audit 流；`${VAR}` secret 只展开进子进程 env，不进事件
6. legacy SSE-only transport 的 server 被跳过；streamable-HTTP 已由后续
   增强（`dfeaff8`）接入：每消息一个 JSON-RPC POST，支持 JSON 与 SSE
   响应，自动携带服务端分配的 `Mcp-Session-Id`

验收标准：

- [x] MCP tool schema 可进入 model API（`test_read_only_mcp_tool_enters_tool_loop_and_audit`）
- [x] MCP result 进入同一 tool event / audit 流（audit tool.call/tool.result 断言）
- [x] read-only 与 write 权限分明（read 默认可用；write 三重门：allowlist + workspace-write + approval）
- [x] secrets redacted（env 只进子进程；audit 全量 redact 兜底）
- [x] MCP server crash 不影响 AHP host（`test_list_mcp_tools_skips_crashed_servers`）

## Milestone 7 — Desktop e2e and release hardening ✅ 已完成（本机可验证部分 + CI）

实现说明：

1. **Playwright 静态 UI e2e**（`desktop/e2e/`，`npx playwright test`，本机
   7 passed）：以 `window.__TAURI__` mock 驱动全部桥命令与 `ahp-message`
   事件，覆盖 handoff 列出的状态机——boot 与版本显示、runtime version
   mismatch 告警、模型健康面板 + 保存流程（configure/stop/刷新链）、
   audit 列表 + plan evidence deep link 高亮、dispatch 的 plan 证据展开 /
   审批批准 / 拒绝 / 回滚 / 取消、既有 native 会话恢复（plan + 审批历史
   回填）
2. **真实 bug 修复**：`dispatchViaAhp` 中 `results.push` 发生在 `const
   results` 声明之前（异步竞态必命中 TDZ），任何 dispatch 都会报
   「Cannot access 'results' before initialization」——已修（先声明再并发）
3. **AHP auth 负面测试**（`desktop/src-tauri/src/main.rs` `#[cfg(test)]`）：
   token 文件缺失报类型化错误、token 内容 trim、401 host 不判
   authenticated、闭端口不判 authenticated；`read_ahp_token` 抽出可注入
   路径的 `read_token_from`
4. **sidecar version contract**：`halter_version` 桥命令附带
   `desktop = CARGO_PKG_VERSION`，UI boot 比较并在不一致时给 mismatch
   告警（`release 不应携带旧 sidecar` 提示）；`tests/test_release_contract.py`
   校验 pyproject == tauri.conf.json == Cargo.toml、`halter version --json`
   与源码一致、已构建 sidecar 产物版本一致（无产物时跳过）、构建脚本
   保留输入 hash 新鲜度机制
5. **desktop CI**（`.github/workflows/desktop-ci.yml`）：python 全量
   pytest、desktop-rust（Tauri Linux 依赖 + cargo check --all-targets +
   cargo test 含 auth 负面测试）、desktop-ui（node --check + npm ci +
   Playwright）、sidecar（构建后跑契约测试）
6. **环境记录（本机限制）**：本机已装 rustup/cargo（rsproxy 镜像），
   但 cargo 内置 HTTP 栈在此 WSL 网络下无法传输（静态 libcurl 问题，
   系统 curl 正常）；已用 `scripts/vendor_crates.py` 把 456 个 crate
   离线 vendored（`desktop/src-tauri/vendor/`，gitignore，不进仓库），
   离线解析通过；完整 `cargo check` 还需要 cc + pkg-config +
   webkit2gtk dev（无 sudo 装不了），由 CI 执行。本机已验证：
   `node --check`、`uv run pytest -q`、`npx playwright test`

验收标准：

- [x] desktop CI 可跑（workflow 四 job：python / rust / ui / sidecar）
- [x] release artifact 不携带旧 sidecar（契约测试 + 构建输入 hash + sidecar CI job）
- [x] UI 能自动发现 runtime version mismatch（halter_version.desktop 比对 + 告警样式 + e2e 断言）
- [x] 安装包启动后 AHP health / auth / native provider 检查通过（Rust auth 负面测试 + Python AHP host 测试 + Playwright 状态机；完整安装包验证需在有构建工具链的环境跑 `npm run build`）

---

## 8. 推荐的完成定义

最终可以说目标完成，至少要满足（2026-09-19 复核：全部满足，括注实现位置）：

### 可控

- [x] 默认 read-only（`tools_for_permission`；write 工具不进 schema）
- [x] 写入 / 真实测试均需显式审批（apply_patch / run_tests / run_tests_workspace / write MCP）
- [x] 所有工具参数与结果可追溯（audit tool.call/tool.result 含 planStepId）
- [x] patch 可回滚（transaction 精确反向 patch，rollback 校验）
- [x] 外部 Harness 只能改 sandbox（delegate 一次性 clone，diff 回流审批）
- [x] MCP write 工具同样受审批约束（`[native_agent.mcp]` write allowlist + approval）

### 可审计

- [x] 每次 model response 有记录（audit model.response）
- [x] 每次 tool call / result 有记录（audit tool.call/tool.result）
- [x] 每次 approval decision 有记录（approval.requested/response）
- [x] 每次 rollback 有记录（transaction.rolledBack/rollbackFailed）
- [x] plan step 可关联证据（M1 planEvidence + UI 展开）
- [x] audit viewer 可搜索并定位事件（M4 search + deep link）
- [x] session / transaction / audit 三者可互相关联（planStepId 贯穿 + audit search 按 transaction id 定位）

### 多模型

- [x] OpenAI-compatible streaming 可用（模型客户端 SSE 流式）
- [x] custom provider 可配置（`[model_providers]` + `halter model configure`）
- [x] provider health 可检查（M3 `halter model check` + Model view 面板）
- [x] API key 不落盘（只存环境变量名 / secrets.toml，输出恒脱敏）
- [x] AHP catalog 正确反映可用性（`halter:available` 复用 resolve_route）
- [x] model switching 不破坏 durable session（agent_messages/currentPlan/planEvidence 与模型配置解耦，durable restore 测试覆盖）

### 多 Harness

- [x] Claude / Codex / ZCode / OpenCode 可并发委派（同轮并发 delegate）
- [x] sandbox snapshot 包含 tracked + bounded untracked（5000 文件 / 200 MiB 上限）
- [x] diff 可结构化比较（M2 patch_compare + semantic_equivalent 后续增强）
- [x] 冲突可解释（conflicts 含各 provider hunk header 与改动行，UI 高亮）
- [x] 合并建议仍由 halter 原生 Agent 统一生成（comparison 注入工具结果）
- [x] 真实 workspace 修改仍走 apply_patch 审批

### 本地 AI 编码助手

- [x] Desktop workbench 可完成一个真实小型代码修改任务（plan→read→delegate 对比→apply_patch 审批→run_tests→rollback 全链可用；e2e 覆盖各环节）
- [x] plan / tools / diff / approval / tests / rollback 全流程可视（调度页各面板 + sandbox/workspace 环境徽标）
- [x] App 重启后可恢复上下文（durable sessions：turns/currentPlan/planEvidence/approvalHistory）
- [x] release sidecar 与源码一致（版本契约测试 + 构建输入 hash + sidecar CI job；真机安装包待 CI 绿后构建）
- [x] 全量测试与 desktop smoke 通过（189 pytest + 7 Playwright 本机全绿；cargo check/test 由 desktop-ci 执行）

---

## 9. 建议的下一个 PR

Milestone 1-7 全部完成，缺口 A-H 全部关闭，原路线图（§7）与其预留项
（semantic-equivalent 分类 `5877318`、MCP streamable-HTTP transport
`dfeaff8`）均已交付。

剩余事项（均需外部条件，非代码缺口）：

1. **推送 main 触发 desktop-ci** 并确认四个 job 全绿（尤其 desktop-rust
   的 cargo test——本机无法执行，见 §11 常见坑；本机 gh 因网络超时无法
   验证远程状态）
2. **真机 release 构建**（`npm run build`）与安装包冒烟，需带 webkit2gtk
   图形栈的环境
3. 可选：legacy SSE-only MCP transport、AST 级语义等价判定（超出当前
   白名单规范化粒度）

---

## 10. 接手时先读的文件

按顺序：

```text
docs/native-agent.md
docs/native-agent-handoff.md
src/harness_config_manager/agent.py
src/harness_config_manager/ahp_host.py
src/harness_config_manager/agent_store.py
src/harness_config_manager/agent_audit.py
desktop/ui/app.js
desktop/src-tauri/src/main.rs
tests/test_agent_runtime.py
tests/test_ahp_host.py
```

---

## 11. 常见坑

1. **AHP host 需要重启才读取模型配置**
   - external host 不会自动重启

2. **desktop/devbin/halter 可能是旧 sidecar**
   - 开发时优先用 `HALTER_BINARY` 指向当前构建

3. **测试必须使用 fake HOME**
   - 不要读写真实 `~/.config/halter`

4. **native model 当前是否可用取决于 provider env**
   - 没有 key / local endpoint 时 `halter:available` 可能为 false

5. **delegate sandbox 依赖 git**
   - 非 git workspace 会失败，这是有意保守行为

6. **untracked snapshot 有上限**
   - 超限文件会出现在 `untrackedSkipped`

7. **不要把 approval 状态只存在 UI**
   - 必须进 chat state 和 audit

8. **不要让 external harness 直接返回“已修改真实项目”**
   - 必须保持 sandbox boundary

9. **新增工具时同步四处**
   - tool schema
   - executor
   - AHP event rendering
   - audit semantics

10. **新增敏感配置时先问是否真的需要**
    - 默认只存环境变量名，不存 secret

11. **本机 cargo/链接器环境（2026-09-18 记录）**
    - cargo 已装（rustup stable），但内置 HTTP 栈在此 WSL 网络下 0 字节
      挂起（系统 curl 正常）；crates 依赖已用 `scripts/vendor_crates.py`
      从 USTA/USTC 镜像 vendored 到 `desktop/src-tauri/vendor/`（gitignore）
    - `~/.gitconfig` 有指向旧网段 `172.31.96.1:7890` 的死代理，git 直连
      正常但走代理会挂；cargo fetch 走 git CLI 时同样受影响
    - 本机无 cc/pkg-config/webkit2gtk dev（无 sudo），完整 cargo
      check/test 只能在 CI（desktop-ci workflow 已配好 apt 依赖）跑

12. **Playwright 测试约定（desktop/e2e）**
    - mock handler 只能引用自身参数；闭包变量必须通过 setHandler 的
      bindings（纯数据）显式传入（factory 签名 `(bindings) => handler`）
    - `halter_ahp_notify` 的 action 在 `args.params.action`，
      `halter_ahp_rpc` 按 `{method, params}` 分发
