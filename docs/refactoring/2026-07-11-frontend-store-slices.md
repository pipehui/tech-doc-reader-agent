# 前端 Zustand Store slices 拆分

## 本批目标

持久化策略移入 repositories 后，`store.ts` 仍有 401 行，集中承载 session、transcript、trace、learning、run/UI 五组状态与 action。任何一组变化都要修改根 Store，跨 action 的 `get()` 调用也没有结构约束。

本批保持单一 Zustand 实例和全部现有 action API，把定义与实现按职责拆成 typed slices；根文件只负责创建依赖和组合 slices。

## 实际改动

### 1. 建立集中 contracts 与 defaults

新增 `store/contracts.ts`，分别定义：

- `SessionSlice`：session identity/directory/switch/reset/delete；
- `TranscriptSlice`：messages、streaming response、tool calls、transcript persistence；
- `TraceSlice`：events、filter、selection、recording/Inspector state；
- `LearningSlice`：learning overview 与 learner-plan visibility；
- `UiSlice`：run/error、theme、expanded tool ids。

`AppStore` 是五个接口的交集。`store/defaults.ts` 统一初始 SessionState、LearningOverview 和 Inspector event types，避免 slice/测试重复构造默认值。

### 2. 五个 slice creators 独立拥有行为

新增：

- `sessionSlice.ts`，122 行；
- `transcriptSlice.ts`，172 行；
- `traceSlice.ts`，57 行；
- `learningSlice.ts`，20 行；
- `uiSlice.ts`，41 行。

每个 creator 接收自己需要的 repository/clock/id port，不 import `useAppStore`，也不 import 其他 slice 实现。slice 只通过共享的 `AppStore` contract 看到允许协作的 state/action。

### 3. 根 Store 收敛为 composition facade

`store.ts` 从 401 行降到 78 行，只保留：

- browser storage 与三个 repositories 的默认 composition；
- `now/createId/createSessionId` 默认实现和测试注入；
- 五个 slice creator 的组合；
- `useAppStore`、`createAppStore` 与兼容类型 re-export。

`App.tsx`、`useChatStream.ts`、Store adapter 等调用方仍 import `./store`，不需要知道文件拆分。

### 4. 限制跨 slice action 链

当前 `get().action()` 调用集合固定为：

| owning slice | 允许调用 |
|---|---|
| session | `rememberSession`、`resetForContext`（同 slice） |
| transcript | `persistTranscript`、`addToolCall`（同 slice） |
| trace | `persistTranscript`（trace -> transcript） |
| ui | `addSystemMessage`（ui error -> transcript） |
| learning | 无 |

Python architecture test 用源码解析固定该白名单；新增任意链式 action 必须先修改架构约束并说明依赖方向。所有 slice 还禁止 import Store singleton 或其他 slice implementation。

session context reset 需要原子清空 messages/events/toolCalls/selection。它直接提交一份跨 slice state delta，而不调用 `setMessages([])`：后者会在 session identity 切换前把空 transcript 持久化到旧 context，改变原行为。

### 5. 显式化非持久化 UI 状态

`filters`、`expandedToolIds` 是 Set，只属于当前页面 UI；`recording/selected/replay` 等 Inspector 状态也不进入 transcript。测试在改变 Set 后主动 persist，并断言 JSON 顶层只有 `version/messages/events/toolCalls`。

## 实施中遇到的问题

### 问题 A：状态分类不能只按页面分类

`hasNewMessageContent` 看似 UI flag，但由 user/stream/tool message action 共同维护；放入 UiSlice 会让 TranscriptSlice 每次写 UI slice。`showLearnerPlan` 同样只服务 learning flow，而不是通用 UI。

处理：按“谁维护不变量”而非“谁渲染”分类。`hasNewMessageContent` 归 TranscriptSlice，`showLearnerPlan` 归 LearningSlice，Inspector controls 归 TraceSlice，theme/expanded drawer/run status 才归 UiSlice。

### 问题 B：把代码分文件不等于降低耦合

Zustand 的 `get()` 可以从任何 slice 调任何 action。如果不限制，拆分后仍会形成隐式调用网，只是更难搜索。

处理：先列出既有语义所需的最小调用，再建立精确白名单 architecture gate。跨 slice 只剩 trace persistence 和 error system-message 两条；其余调用均在 owning slice 内。

### 问题 C：context reset 不能复用普通 transcript action

普通 `setMessages` 有“立即 persist”副作用。session reset 若为了复用 action 而调用它，会把空 messages 写到旧 session/tenant，随后才切新 identity，造成历史被意外覆盖。

处理：SessionSlice 将 context switch 视为 orchestrator action，用一次 `set` 同时替换 session 并清空相关内存 state，不触发旧 context persistence。

### 问题 D：注入的 session ID/clock 需要传到默认 repository

初次 composition 时，Store actions 使用注入的 `createSessionId/now`，但默认 SessionRepository 仍使用自己的真实随机数和时间。测试 Store action 可确定，首次无 context 初始化却不可确定。

处理：先解析三个 runtime functions，再用同一实例创建默认 SessionRepository 和各 slices。现在 composition root 是唯一 clock/id wiring 点。

### 问题 E：旧 architecture gate 跟着文件位置而不是责任走

第一次运行 Python gate 时，测试仍要求 transcript load/save 和 session loadSessions 出现在根 `store.ts`，因此在正确迁移后失败。

处理：更新 gate，让 root 只验证 composition，repository delegation 在 owning slice 中验证。架构测试检查“责任是否唯一”，不固定所有逻辑必须留在旧文件。

## 测试覆盖

新增 7 个 slice 行为测试：

- 相同 session id 的 tenant 隔离、context reset、new session；
- session delta normalization、delete transcript/context fallback；
- deterministic user/system/streaming/final message；
- tool call 去重、tool result、hydrate token filtering；
- trace token/recording filtering、seq/id/timestamp、filter immutability；
- learning state 与 plan visibility；
- run error -> system transcript、theme/expanded state 和 Set 不持久化规则。

Architecture tests 从 2 个扩展为 5 个，增加 facade 行数/职责、slice import 方向和 action chaining 白名单。

## 验证结果

| 验证 | 结果 |
|---|---|
| `npm run check` | passed |
| `npm run test` | 7 files，42 tests passed |
| `npm run build` | passed，2021 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| frontend architecture focused pytest | 5 passed |
| 全量后端 pytest | 241 passed，4 warnings |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，10 source files |
| in-app browser production preview | landing/Studio entry rendered，theme dark，warning/error 0 |
| `git diff --check` | passed |

浏览器 tab 和 4173 preview 已清理。pytest warning 仍是既有第三方弃用与本机 `.pytest_cache` 权限提示。

## 保持不变与有意变化

保持不变：

- 单一 Zustand Store 和 `useAppStore` import path；
- 所有对外 state/action 名；
- session switch/delete/reset、message/tool/trace、run/error/theme 行为；
- transcript payload、写入时机和 token event 过滤。

有意变化：

- Store contracts 与实现按五个 owning slices 组织；
- clock/id 成为 composition dependencies；
- action chaining 与 slice import 方向由 CI gate 约束；
- Set/临时 Inspector state 明确不持久化。

## 后续工作

- 把 `App.tsx` 的 session bootstrap/switch orchestration 移入 `useSessionBootstrap`；
- 按 chat/approval/learner/inspector 提取 feature components 和 selectors；
- 为快速 tenant/session 切换增加 AbortController 与 stale-response guard；
- fake SSE integration 覆盖 send -> tool -> interrupt -> approve -> done。
