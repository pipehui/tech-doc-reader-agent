# ExecutionBudget 强制限额与确定性部分终止

## 本批目标

上一批已经建立 `BudgetUsage`、真实 model usage metadata、versioned price table、checkpoint 累计与 `usage_update`，但仍只“看得见成本”，不能阻止无限 LLM/tool 循环。本批完成 R2 的执行边界：

- 单次 HTTP chat/approve 使用 request-local monotonic deadline；
- 跨 sensitive-tool interrupt/approve 的 workflow 使用 durable LLM/tool/token/cost 累计；
- 每个 LLM transport attempt 与 ToolNode batch 在真实调用前检查；
- 当前原子步骤完成后再次检查实际值；
- 超限时关闭未执行 tool call，由确定性 terminal node 输出部分终止说明；
- repeated-call、parser retrieval、reflection 与预算聚合到一个 `ExecutionPolicy`；
- SSE/REST/前端 session state 能区分 budget start、usage delta 与 termination。

这不是用户计费、套餐 quota 或 AuthZ；它是单次执行和当前 workflow 的可靠性保护。

## 最终领域边界

### 1. `ExecutionBudget`

纯策略包含五个可选上限：

| 维度 | 作用域 | 默认值 | 0 的含义 |
|---|---|---:|---|
| request elapsed | 单次 HTTP chat/approve | 300 秒 | 禁用 |
| workflow LLM calls | checkpoint workflow | 32 | 禁用 |
| workflow tool calls | checkpoint workflow | 48 | 禁用 |
| workflow total tokens | checkpoint workflow | 0 | 禁用 |
| workflow estimated cost USD | checkpoint workflow | 0 | 禁用 |

domain 对象内部不用 0 sentinel，而使用 `None` 表示未启用；`build_execution_budget()` 是 Settings 到 domain 的唯一转换点。直接构造会拒绝 bool、float-as-call-count、非正数、NaN 与 infinity，防止配置“能解析但语义含糊”。

策略返回 versioned `BudgetDecision`，包含 scope、dimension、before/after/resume phase、operation、reason、observed 和 limit。它不直接操作 LangGraph message，也不写日志；图适配层负责交付。

### 2. Request 与 workflow 绝不混存

`RequestBudgetWindow` 只包含：

- `started_monotonic`；
- `deadline_monotonic`；
- `max_seconds`；
- schema version。

HTTP route 一进入就采集 monotonic start，并沿 `ChatRuntime -> GraphExecutionService -> SessionConfigFactory` 传递。这样 guardrail、session snapshot、pending-approval 查询和 graph execution 都在同一请求窗口内。guardrail approval 重新播放原消息时复用 approve 请求的同一个 start，不会在内部 replay 时重置 deadline。

窗口只放在 RunnableConfig metadata，`State/BudgetUsage/checkpoint` 没有 monotonic 字段。跨进程 approve/resume 会创建新 request window，但继续读取旧 checkpoint 的 workflow usage，避免把进程相关的 `perf_counter/monotonic` 值持久化。

当 request cap 已启用而 graph invocation 缺少该 metadata 时，系统以 `request_budget_missing` 报告内部 wiring error，不静默绕过 deadline；state/history 等只读 config 不进入 graph node，因此不需要窗口。

### 3. `ExecutionPolicy` 聚合而不揉成一个巨型判断

GraphSpec 原来平铺：

- `ToolExecutionPolicy`；
- `ReflectionPolicy`；
- budget tracker。

现在由 `ExecutionPolicy` 聚合 budget/tools/reflection 三个正交子策略。GraphSpec 校验 tracker 持有的 `ExecutionBudget` 与聚合策略相同，composition 只构造一次 budget object，避免 builder、tracker 与 runtime 使用不同上限。

聚合只统一 ownership，具体算法仍在各自模块：repeated/parser 判断没有塞进 token/cost 代码，reflection repair 状态机也没有与 deadline 判断互相引用。

## Before/after 执行语义

### LLM

`assistant_node` 为一次 node invocation 固定当前 checkpoint usage，并把 pre-check callback 传给 Assistant。Assistant 再把 callback 接到 `RetryExecutor.before_attempt`：

1. 第一次 provider operation 前检查 projected LLM call；
2. transport failure 后，下一次 retry 前把已失败 attempt 作为 token/cost unknown 的本地 usage 模拟进去；
3. empty-response semantic repair 的下一次 LLM 同样检查，并包含此前真实 response usage；
4. 成功 response、transport retry 与 empty response usage 一次性交给 graph tracker；
5. tracker 写入 checkpoint usage 后做 after-check。

因此 max LLM calls=2 时，`timeout -> timeout -> third attempt` 只发出前两次请求；第三次在 provider 调用前停止，前两次仍以 unreported calls 进入 durable usage。

### Tool

ToolNode 在原 repeated/parser policy 之前先做 ExecutionBudget pre-check。生产模型已禁用 parallel tool calls，正常 batch 为 1；若兼容输入带多个 call，则整个 ToolNode batch all-or-none，不执行“剩余额度内的一部分”，避免并发副作用形成难以审计的半批次。

成功和 exception fallback 都在真实原子步骤结束后增加 tool calls 并做 after-check。policy block、预算 pre-check block 与 router 主动关闭的 orphan call 不算真实 tool call。

### unknown token/cost

未启用 token/cost cap 时，unknown 只影响观测，不阻断执行。启用相应 cap 后：

- 一个刚完成且无需任何后续调用的最终回答可以正常结束；
- 若 workflow 还要发下一次 LLM，pre-check 因 `usage_unreported` 停止；
- tool 本身不增加 LLM token/cost，因此 unknown token/cost 不阻止当前 tool；tool 后的下一次 LLM 仍会停止；
- 永远不把 unknown 填成 0。

这是“继续花模型成本前 fail closed”，不是“第一次 provider 缺 usage 就覆盖已经完成的最终答案”。

## 确定性 partial termination

预算状态是：

`active -> terminating -> terminated`

当 pre/after decision 产生时：

1. 当前已开始的 LLM/tool 原子步骤先完成；
2. 若 Assistant 返回了尚未执行的 tool call，为每个 id 补匹配的 error `ToolMessage`，保持消息协议闭合；
3. primary/subagent/tool router 优先路由到全局 `budget_terminated`；
4. terminal node 不调用模型，生成确定性的中文说明，清空未完成 plan，并在子 Agent 内时 pop dialog stack；
5. parser/relation 等已完成结构化结果与完整 cumulative usage 保留在 checkpoint。

它不会把 multi-agent 中途状态偷偷交给 primary 再调用一次“直接回答”，因为那既消耗额外预算，也可能把不完整证据包装成完整结果。

审批恢复无需 runtime 复制一套判断：LangGraph resume 从 interrupted sensitive ToolNode 继续，ToolNode 的同一个 pre-check 位于真实写操作之前，并把 phase 标为 `resume`。达到 durable tool cap 时，approve 只会得到 closure + terminal node，目标写函数调用次数保持 0。

## 协议与前端恢复

- `budget_started`：START 重置 workflow usage 后发出，前端清除上一次 terminated 状态；
- `usage_update`：继续交付每次真实 LLM/tool delta 与 cumulative usage；
- `budget_terminated`：只在 terminal node 发一次，包含 decision 与 final usage；
- session REST 增加可选 `budget_status` 与 `budget_termination`，刷新后不依赖 SSE replay；
- TypeScript runtime decoder 校验 status enum，未知状态不会静默进入 Store。

原前端 `token_count` 仍只是 SSE 文本 chunk UI 统计，与 provider token budget 完全分离。

## 实施中遇到的问题

### 问题 A：只在 Assistant 外检查会让 retry 绕过 call cap

一次 Assistant node 内可能包含 transport retry 和 empty-response repair。若 graph adapter 只在 node 前检查一次，配置 max=2 仍可能发出 3 个以上 provider operation。

处理：复用 RetryExecutor 已有 `before_attempt` hook。Assistant 把此前已完成 response usage 和当前 transport 的失败 attempts 传给 tracker 做纯模拟；模拟不写 checkpoint，最终真实 delta 只记一次。

### 问题 B：pre-check exception 不能变成 HTTP 500

RetryExecutor 的 hook 通过异常停止 operation；若异常直接穿过 RunnableLambda，整个 SSE 会以 backend error 结束，也不会形成可恢复 checkpoint 状态。

处理：定义保留 `BudgetDecision` 的 `ExecutionBudgetExceeded`，覆盖 `with_context`，避免统一 error classifier 丢失策略数据。Assistant 和 graph adapter 都能把它转换为内部 `_budget_decision`，tracker 在同一个 node invocation 内消费并删除，Python domain object 不会进入 checkpoint。

### 问题 C：超限 Assistant 可能留下未配对 tool call

after-check 发生时 AIMessage 已经生成，可能带 tool call；直接路由 END 会留下 OpenAI/LangChain 不接受的 orphan call，后续恢复也可能误执行。

处理：termination adapter 为每个未执行 call 创建同 id ToolMessage，artifact 同时带安全 error payload 与 budget decision。SSE 仍会依次看到 tool_call/tool_result，明确显示“提出但未执行”。

### 问题 D：只新增 terminated event 会让新请求沿用旧 UI 状态

新请求 START 会把 checkpoint status 重置 active，但旧前端 session 在第一条 usage delta 前仍可能显示 terminated。

处理：新增独立 `budget_started`，由 fetch_user_info update 发一次；reducer 同时重置 termination 与 cumulative usage。

### 问题 E：request start 如果在 graph.stream 前采集会漏算 API 前置工作

最初版本在 `_stream_user_message` 内创建窗口，session snapshot、guardrail 与 approve pending check 不在 elapsed 内，名义上的 request budget 实际只是 graph budget。

处理：把 start 上移到 FastAPI route，并作为显式可选参数贯穿 runtime。sync/async parity 测试比较配置时剔除每次请求本来就不同的 monotonic metadata，避免 Windows 时钟分辨率造成偶发相等/不等。

### 问题 F：预算实现自身重新长成巨型文件

初次接线后 core policy 和 graph tracker 都接近 500 行，不符合本轮高内聚目标。

处理：拆为：

- `core/execution_budget_models.py`：request window、decision、controlled exception；
- `core/execution_budget.py`：纯 before/after limit policy 与 Settings adapter；
- `graph/budgeting.py`：usage 累计、pre/after orchestration 与 telemetry；
- `graph/budget_termination.py`：tool closure、terminal message 与状态清理。

拆分后四个文件分别约 288、335、375、146 行，依赖方向为 core model -> core policy -> graph adapter，没有 core 反向导入 graph。

## 配置

`.env.example`、Settings 与 Docker Compose 同步新增：

- `REQUEST_MAX_SECONDS=300`；
- `WORKFLOW_MAX_LLM_CALLS=32`；
- `WORKFLOW_MAX_TOOL_CALLS=48`；
- `WORKFLOW_MAX_TOTAL_TOKENS=0`；
- `WORKFLOW_MAX_ESTIMATED_COST_USD=0`。

所有值允许 0 表示关闭对应 enforcement；计量不会因关闭上限而停止。

## 验证范围

定向测试覆盖：

- domain validation、request metadata round-trip 与 checkpoint 排除；
- projected before check、exact boundary、after overshoot；
- enabled token/cost 的 unknown 语义；
- transport retry 在第三次请求前停止且失败 attempts 被计量；
- LLM token overshoot 后 tool-call closure；
- ToolNode batch all-or-none、exception/success accounting；
- approve/resume 前敏感写函数保持未调用；
- request deadline 在工具原子步骤后复核；
- compiled graph 真正进入 deterministic terminal 并清空 next nodes；
- ExecutionPolicy composition 一致性与 graph topology；
- budget_started/usage_update/budget_terminated Python/TypeScript 契约；
- REST refresh decoder 与 sync/async request-start parity。

| 验证 | 结果 |
|---|---|
| 本批 targeted Python pytest | 179 passed，3 个既有第三方 warning |
| 全量后端 pytest | 544 passed，3 个既有第三方 deprecation warning |
| 全仓 Ruff | passed |
| mypy | 既有 CI core/schema gate 17 source files；本批 direct gate 19 source files，均 passed |
| frontend typecheck / targeted Vitest | typecheck passed；2 files / 17 tests passed |
| frontend full test/build/audit | 19 files / 72 tests；2041 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed，任务单未进入 HEAD、origin/main 差异或待提交集合 |

## 明确保留到后续

- Embedding/Web provider 内部 `RetryUsage` 仍只有 retry telemetry、外层 tool count 与 request elapsed，尚未作为独立 provider attempts 写入 workflow usage；
- primary/backup composite 内部 provider span 仍无法从最终 AIMessage 精确还原；
- Assistant 在非预算原因的最终整体失败、没有 graph update 时，已消耗 usage 仍无法持久化；
- 当前预算是全局 Settings，不是按 tenant/plan/agent 动态分级；
- token/cache/reasoning token 的 provider-specific 明细仍待统一 usage schema 扩展。

这些限制均不会被填 0 或泛化为“已精确计费”。
