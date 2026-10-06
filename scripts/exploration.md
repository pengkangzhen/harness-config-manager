# 探索式多 agent 测试规范（halter）

夜间探索任务的单一事实源：场景分工、环境隔离、bug 报告格式与复现规则。
执行者（主会话）按本文件派发场景 agent、收集报告、复核复现、汇总输出。

## 目标

像真实用户一样操作 halter（CLI + 桌面 UI 真通道），发现 mock 测试与
单元测试覆盖不到的真实问题。探索**只报告、不修复**：测试与修复分离，
修复留给白天会话（由回归套件护航）。

## 环境隔离（硬性，违反即报告作废）

- 每个场景 agent 一切操作必须在自己 `mktemp -d /tmp/halter-exp-<场景>-XXXX` 的
  fake home 下（HOME 环境变量注入），绝不动真实 `~`。
- 需要远端机器的场景先跑 `scripts/fake-remote.sh setup <fake-home>`，
  结束后 `teardown`。
- UI 真通道（启动时记 PID，关闭按 PID kill；**禁止 pkill -f**——它会
  匹配到自身命令行把整条命令链静默杀掉）：
  ```bash
  HOME=<fake-home> HALTER_BINARY=<repo>/.venv/bin/halter \
    node desktop/devbin/ui-server.mjs <port> &
  UI_PID=$!   # 结束时 kill $UI_PID
  ```
  UI 驱动用 playwright **库模式**（临时脚本放 /tmp，不依赖 test runner、
  不在仓库留文件）。注意 ESM 按脚本自身路径解析依赖，/tmp 下必须用
  绝对路径 import（Node 22 的 .mjs 没有 require）：
  ```js
  // /tmp/<场景>.mjs —— <REPO> 替换为仓库绝对路径
  import { chromium } from "<REPO>/desktop/node_modules/@playwright/test/index.mjs";
  const b = await chromium.launch({ channel: "chromium-headless-shell" });
  const p = await b.newPage();
  const errs = [];
  p.on("pageerror", (e) => errs.push(String(e)));
  await p.goto("http://127.0.0.1:<port>");
  // …像用户一样点击/输入/观察…
  await b.close();
  ```
  在仓库任意目录 `node /tmp/<场景>.mjs` 执行。
- 临时脚本与报告都放 fake home 或 /tmp，**不在仓库里留任何文件**。

## 场景分工（每轮并行派发，端口固定）

| 场景 | 端口 | 覆盖 |
|---|---|---|
| S1 providers | 4781 | add/adopt/switch/官方切回/预设填充/备份生成/token 只进 secrets.toml(0600)且不进 argv/detect 三态；破坏输入：坏 URL、空 token、超长 id |
| S2 多机同步 | 4782 | fake-remote 往返 push/pull、幽灵行、scan --machine、双端同条目不同内容的冲突行为、--with-secrets 是否只按显式携带 |
| S3 矩阵同步语义 | 4783 | dry-run 与 apply 的一致性、单元格定向同步、conflict 默认跳过与 --prefer library、备份文件真实生成、adopt 收集语义、工具侧已有同名旧版本时的 apply 分发与备份行为 |
| S4 破坏性输入 | 4784 | 坏 TOML/JSON 配置、只读目录、空/超长/unicode 条目名、HOME 下目录缺失、CLI 错误信息是否可读且不崩溃 |
| S5 CLI-UI 一致性 | 4785 | 同一 fake home 下 CLI 文案与 UI 渲染一致（doctor、providers 状态、machines 列表）、中英文切换完整、JSON 输出形状 UI 能消费 |

每个场景 agent 的动作：搭建隔离环境 → 按场景清单操作（CLI 直接跑
`HOME=<fake-home> .venv/bin/halter …`；UI 用 Playwright 驱动真通道）→
对可疑行为按下方格式记录 → 清理环境 → 交报告。单场景限时 15 分钟，
到点即交已发现的内容。

## bug 报告格式（缺一项不收）

```markdown
### [S<场景>-<序号>] <一句话标题>
- 严重度: P0 崩溃/丢数据 · P1 功能错误 · P2 体验/文案
- 复现步骤: 逐条命令或 UI 操作（可整段复制执行）
- 期望: <正确行为>
- 实际: <观察到的行为 + 关键报错原文>
- 证据: 相关文件路径（fake home 内）/ 截图 / stdout 摘录
```

## 报告纪律（防假 bug 与证实偏差）

1. 场景 agent 不知道"预期实现细节"，只依据 CLI --help、README 与常识
   判断期望行为；拿不准的标为"存疑"而不是硬定结论。
2. 主会话收到候选后：P0/P1 必须派一个**全新的独立 agent**（只给复现
   步骤，不给原报告结论）复核复现；复现成功才算确认。P2 抽查。
3. 环境 prepared 引起的问题（如缺目录）标注为环境问题，不算产品 bug，
   除非产品本应优雅处理。
4. 探索全程禁止修改仓库内任何文件、禁止 git 写操作、禁止跑全量回归
  （那是每 2 小时回归任务的职责）。

## 主会话汇总输出

每轮结束输出：各场景执行状态（完成/超时）、候选问题数、确认数
（附报告全文）、误报数及排除理由、环境备注（如 sshd 不可用）。
落盘 `/tmp/halter-explore-<YYYYMMDD-HHMM>/summary.md` 并在会话里复述要点。
