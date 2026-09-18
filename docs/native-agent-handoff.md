# halter Native Agent — 交接与后续推进计划

更新时间：2026-09-18  
基线状态：`a98f1ff` 时 `141 passed`；Milestone 1（`00438b4`）、Milestone 2（`8322aa3`）、
Milestone 3（provider health，`c6beb26`）与 Milestone 4（audit search）已完成，
全量 `173 passed`。

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
comparison.files[].classification: identical / conflicting / overlapping / unique
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

### 2.7 测试沙箱

`run_tests` 目前在一次性 git clone sandbox 中执行，不直接修改真实 workspace。

固定命令族：

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

### 缺口 E：真实 workspace 最终测试流缺失

当前 `run_tests` 在 sandbox 中执行。还缺少一个显式流程：

```text
sandbox 测试通过
  -> 展示建议
  -> 用户再次批准
  -> 在真实 workspace 复跑最小必要命令
  -> 记录最终验证结果
```

### 缺口 F：MCP tools 尚未接入 native tool loop

halter 已能管理 MCP 配置，但原生 Agent 还不能把 MCP server tools 作为受控工具调用。

### 缺口 G：桌面端自动化验证不足

现有 Python 测试强，但 desktop UI 主要靠：

```bash
node --check
cargo check
```

缺少浏览器 / Tauri UI e2e 测试。

### 缺口 H：打包与 sidecar 新鲜度风险

需要确保 release 中 bundled `halter` sidecar 与 Python 源码同步构建，避免 UI 使用旧 runtime。

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
2. 文件分类 `identical / conflicting / overlapping / unique`；report 级
   `unrelated`（≥2 provider 且文件互不相交）；冲突条目含每 provider 的
   规范化 hunk header（`@@ -a,b +c,d @@`）与截断后的 removed/added 行
3. `AgentRuntime.run` 维护本 run 各 provider 最新 delegate diff，≥2 个时
   把 `compare_provider_diffs(...).to_dict()` 注入每个 delegate 工具结果
   （模型、audit、AHP toolResult 均可见）
4. 桌面对比面板显示合并策略与每文件分类 chip；conflicting hunk 在各
   provider 的 diff 中行级高亮（header 精确匹配）
5. 空 diff / 非 diff 文本（含非 git workspace 的失败输出）解析为无文件，
   不抛异常
6. 注意：`semantic_equivalent` 未实现——指纹是逐字节的，格式化差异会被
   判为 conflicting；如需语义级等价判定，留给后续 milestone

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

## Milestone 5 — Real-workspace final verification flow

建议新增 approval type：

```text
run_tests_workspace
```

流程：

1. sandbox 测试结果返回
2. Agent 提出需要在真实 workspace 复跑的最小命令
3. UI 展示 sandbox 结果与差异
4. 用户再次批准
5. 真实 workspace 执行 fixed argv
6. 输出写入 transaction / audit
7. UI 显示最终验证状态

验收标准：

- [ ] 与 sandbox `run_tests` 分离
- [ ] 二次审批必需
- [ ] 命令仍固定 argv
- [ ] 真实执行结果入 audit
- [ ] UI 明确标识 sandbox vs workspace

## Milestone 6 — Native MCP tool bridge

目标：把 halter 已管理的 MCP servers 挂进 native tool loop。

分两阶段：

### Phase 1 read-only MCP

- 只允许 declared read-only tools
- tool name 前缀：

```text
mcp_<server>_<tool>
```

- 参数 schema 转换
- timeout
- output truncation
- audit
- permission mode

### Phase 2 state-changing MCP

- 需要 approval
- approval request 展示 server / tool / arguments
- 禁止 secret 输出
- 每个工具单独 allowlist

验收标准：

- [ ] MCP tool schema 可进入 model API
- [ ] MCP result 进入同一 tool event / audit 流
- [ ] read-only 与 write 权限分明
- [ ] secrets redacted
- [ ] MCP server crash 不影响 AHP host

## Milestone 7 — Desktop e2e and release hardening

建议：

1. Playwright static UI tests
2. Tauri smoke test
3. AHP auth negative tests in desktop bridge
4. sidecar version contract check
5. release build自动确认 bundled sidecar 来自当前源码
6. Desktop UI 状态机测试：
   - approval
   - deny
   - rollback
   - cancel
   - restore session
   - model provider save

验收标准：

- [ ] desktop CI 可跑
- [ ] release artifact 不携带旧 sidecar
- [ ] UI 能自动发现 runtime version mismatch
- [ ] 安装包启动后 AHP health / auth / native provider 检查通过

---

## 8. 推荐的完成定义

最终可以说目标完成，至少要满足：

### 可控

- [ ] 默认 read-only
- [ ] 写入 / 真实测试均需显式审批
- [ ] 所有工具参数与结果可追溯
- [ ] patch 可回滚
- [ ] 外部 Harness 只能改 sandbox
- [ ] MCP write 工具同样受审批约束

### 可审计

- [ ] 每次 model response 有记录
- [ ] 每次 tool call / result 有记录
- [ ] 每次 approval decision 有记录
- [ ] 每次 rollback 有记录
- [ ] plan step 可关联证据
- [ ] audit viewer 可搜索并定位事件
- [ ] session / transaction / audit 三者可互相关联

### 多模型

- [ ] OpenAI-compatible streaming 可用
- [ ] custom provider 可配置
- [ ] provider health 可检查
- [ ] API key 不落盘
- [ ] AHP catalog 正确反映可用性
- [ ] model switching 不破坏 durable session

### 多 Harness

- [ ] Claude / Codex / ZCode / OpenCode 可并发委派
- [ ] sandbox snapshot 包含 tracked + bounded untracked
- [ ] diff 可结构化比较
- [ ] 冲突可解释
- [ ] 合并建议仍由 halter 原生 Agent 统一生成
- [ ] 真实 workspace 修改仍走 apply_patch 审批

### 本地 AI 编码助手

- [ ] Desktop workbench 可完成一个真实小型代码修改任务
- [ ] plan / tools / diff / approval / tests / rollback 全流程可视
- [ ] App 重启后可恢复上下文
- [ ] release sidecar 与源码一致
- [ ] 全量测试与 desktop smoke 通过

---

## 9. 建议的下一个 PR

Milestone 1-4 已完成（evidence-linked plan、patch comparison、provider
health、audit search）。

下一个最小但高价值的 PR：

```text
feat: real-workspace final verification flow
```

即 Milestone 5（`run_tests_workspace` approval type + 二次审批 + 真实
workspace 最小复跑）。范围以 Milestone 5 验收标准为准。

不要在同 PR 里同时做：

- MCP bridge
- desktop e2e

否则风险和 review 面都会过大。

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
