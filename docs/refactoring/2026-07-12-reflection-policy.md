# 有限 Reflection 与参数修复状态机

## 本批目标

Transport Retry 已经能在 provider 原始异常边界恢复 timeout、限流和 5xx，但工具错误进入 LangGraph 后仍只有一条无条件回边：ToolNode 产生 error `ToolMessage`，随后再次调用同一个 Assistant。系统没有记录这是 transport failure、参数修复还是普通推理，也没有 reflection round 上限。现有 repeated-call guard 只能阻止完全相同的参数；模型每次略改 query/arguments 就能继续循环，最终只能依赖全图 recursion limit。

本批完成 R1 的 Reflection 部分：把“能否修复、是否还有轮次、失败后如何收尾”建模为显式状态机。默认一个 graph request（包括 interrupt/resume）全局只允许 1 次 argument-repair reflection；新用户请求从 START 进入时重置。

## 最终策略

| Tool 结果 | 是否计入 reflection round | 下一步 |
|---|---:|---|
| success | 否 | 清理 active reflection，正常回到 Assistant |
| `validation_error`，仍有额度 | 是 | 回到原 Assistant，按公开 tool schema 修正 name/arguments 一次 |
| `validation_error`，额度已用完 | 否 | 回到原 Assistant 做一次 tool-free partial result/finish/escalate |
| timeout、rate limit、5xx/connection、permission、unknown | 否 | transport 已结束，不让 LLM“猜修复”；只允许一次 tool-free 收尾 |
| repeated-call/parser budget 等 policy block | 否 | 保留已有证据，做一次 tool-free 收尾 |
| 收尾状态仍产生普通 tool call | 否 | router 在进入 ToolNode 或 sensitive interrupt 前关闭 call protocol，终止当前工具链 |
| 收尾状态调用 `CompleteOrEscalate` | 否 | 允许 subagent 正常退出到 primary |

`ReflectionPolicy` 默认 repairable code whitelist 只有 `validation_error`。它是显式 immutable set，不依据 exception 文本、`retryable` 字段或 LLM 自己的判断扩大；将来某个领域错误确实可修复时，必须先定义稳定 code 和安全 repair context，再显式加入 policy。

## State 与生命周期

LangGraph `State` 新增：

- `reflection_rounds_used`：当前用户请求累计 argument-repair round；
- `reflection_status`：`idle | repairing | finalizing | terminal`；
- `reflection_tool` / `reflection_error_code`：安全定位当前链；
- `reflection_terminal_reason`：`non_repairable_error`、`max_rounds_exhausted` 或防御性终止原因。

`fetch_user_info` 只在新 graph request 从 START 进入时将累计值重置为 0；approve/reject 使用 `graph_input=None` 从 checkpoint resume，不经过 START，所以不能通过多次审批绕过上限。entry/exit/finish、成功 tool result、PlanWorkflow 成功和 tool-free Assistant output 只清理 active status，不删除本请求已经消费的累计 round。

当前字段已进入 durable checkpoint state，但尚未接入 R2 `ExecutionBudget`；本批不把“写入 state”冒充为 token/cost budget 完成。

## 实际改动

### 1. Tool result 成为 ReflectionPolicy 的唯一入口

`graph/reflection.py` 集中完成：

- 从 `ToolMessage.artifact.error` 读取稳定 code；
- 判定 repair/finalize/terminate；
- 装饰安全的 reflection instruction；
- 更新 state；
- 输出 `reflection.started`、`reflection.finalization_required`、`reflection.terminated` telemetry；
- 为 ToolNode 后置 conditional edge 提供 route。

ToolNode 的同步、异步、policy block、真实 success 和 exception fallback 全部经过同一函数。Graph builder 不再对所有工具节点写死 `tool -> assistant`：subagent tool node 可以继续原 agent 或离开到 primary，primary tool node可以继续 primary 或进入 deterministic failure node。

### 2. 参数修复只暴露公开 schema 线索

统一错误模型此前有意删除原始 provider/tool exception message，这保证安全，却也让 Pydantic 参数错误只剩泛化的 “request invalid”，模型不知道哪个字段错了。

现在只从 Pydantic `errors()` 提取最多 8 个 issue 的：

- public field location；
- normalized error type，例如 `int_parsing`。

不复制 input、message、context、URL、exception string 或 stack。动态/异常 location segment 会替换为 `<field>`。真实 `count="private-invalid-count"` 故障测试确认模型可见 `location=["count"] / type="int_parsing"`，但 content、artifact 和 telemetry 都不含输入值。

### 3. Finalization 不是第二种 Reflection

首版设计对 non-repairable error 立即从 subagent 离开、primary 直接输出固定失败消息。这虽然能停循环，却会丢掉已经成功检索的证据，也破坏现有 parser budget block 的原意——该 block 明确要求“停止搜索并用已有材料完成结果”。

最终方案增加 `finalizing` 状态：

- 不增加 `reflection_rounds_used`；
- Assistant 只获得一次根据安全 error/已有证据生成 partial result、finish 或 escalate 的机会；
- 如果输出不含 tool call，`assistant_node` 立即清理 active state；
- 如果仍请求普通工具，subagent router 直接走 leave，primary router 直接走 `primary_tool_failure`，不会进入 safe ToolNode，更不会先触发 sensitive approval interrupt；
- exit/failure node 为未执行的每个 tool call 补一个匹配的 error `ToolMessage`，再退出或生成安全 primary answer，保持 LangChain tool-call/result 协议完整。

这次额外 LLM 调用是结果收尾，不记录为 argument-repair reflection；未来 R2 仍会像其他 LLM call 一样计入 token/cost budget。

### 4. Fault-injection recovery metrics

新增 `evals/recovery_metrics.py`，对同一组结构化 telemetry 独立汇总：

- recovered/exhausted transport operations；
- transport retries；
- argument repair rounds；
- reflection tool-chain terminations；
- task success；
- `additional_attempts = transport retries + argument repair rounds`。

测试使用真实 `RetryExecutor` timeout -> success 事件和真实 `apply_reflection_policy` validation 事件，而不是手写同名结果，确认 transport recovery、argument repair、task success 与额外 attempt 不混为一个“重试成功率”。`additional_attempts` 只是故障注入成本代理；真实 token 与货币成本仍由 R2 model usage/price table 完成。

## 实施中遇到的问题

### 问题 A：Reflection 根本没有显式入口

代码中没有 reflection 函数或 node；它只是 `tool_node -> assistant` 的普通边。仅搜索 `reflect/retry` 容易误以为空响应循环就是 Reflection，但空响应发生在 transport 成功且没有 tool call/result 的另一层。

处理：从真实 message sequence 和 graph topology 反推边界，把 ToolMessage error 后的决策收敛到 `apply_reflection_policy`，并保持 Assistant empty-response repair、Transport Retry、Tool argument repair 三套计数互不复用。

### 问题 B：按“每个 tool 一次”仍可绕过上限

如果 success、切换 tool 或 subagent handoff 后重置累计 round，模型可以在同一用户请求里让 A/B 工具和多个 agent 各自修复一次。审批 resume 也可能重开计数。

处理：`reflection_rounds_used` 是 request-global 累计，只在 START 重置；active state 可清理，累计值不能。默认 1 表示整条 request/resume chain 一次，而不是每个工具一次。

### 问题 C：立即终止与无限回环都不合适

non-repairable error 不能再调用 provider，但 Assistant 可能已经有足够资料给出部分结果。立即 leave 会丢结果；继续普通回边又可能发新工具调用。

处理：加入 tool-free finalization，并在 Assistant router 而非 ToolNode 内执行禁止规则。这样 safe 和 sensitive tool 都在执行/审批之前被截住，同时允许无工具最终输出和 `CompleteOrEscalate`。

### 问题 D：直接丢弃 AI tool call 会破坏消息协议

router 可以把 finalizing Assistant 直接送到 leave/END，但最后一条 `AIMessage.tool_calls` 若没有对应 `ToolMessage`，下一轮模型调用或消息校验会报 orphan tool call。

处理：exit 与 primary failure node 生成 `reflection_tool_chain_closed` ToolMessage，逐一复用原 `tool_call_id`，随后再 pop/生成 primary answer。测试同时断言 call id、error status、safe artifact 和参数非泄露。

### 问题 E：状态完成后仍可能显示 finalizing

primary 没有单独 finish node；若 finalization Assistant 返回普通文本后直接 END，checkpoint 会残留 `reflection_status=finalizing`，虽然下一次 START 会重置，但状态视图不准确。

处理：`assistant_node` 在 repair/finalize/terminal 状态拿到 tool-free output 时就合并 active reset；若输出仍有 tool call 则保留状态供 router 强制关闭。

## 配置

新增 `MAX_REFLECTION_ROUNDS=1`，Pydantic 约束为非负整数，并同步 `.env.example` 与 Docker Compose。设为 0 会关闭 argument repair，但仍保留一次 tool-free finalization；这适合对成本/确定性要求更高的环境。

## 验证范围

新增/加强的失败样例覆盖：

- validation first repair、global max exhausted、transport non-repairable 分流；
- Pydantic safe location/type 与 input/stack 非泄露；
- sync/async ToolNode exception、policy block 和 success reset；
- compiled conditional edge 的 repair/finalize 路径；
- finalizing subagent/primary router 在 ToolNode 和 sensitive interrupt 前阻断；
- orphan tool call closure 与 deterministic primary failure；
- request START reset、active reset、approval-resume 可持久化字段；
- ReflectionPolicy/Settings 边界；
- 真实 Retry + Reflection event 的 fault-injection metrics；
- graph topology 与 architecture dependency gates。

| 验证 | 结果 |
|---|---|
| 本批 targeted pytest | 92 passed，2 个既有 LangGraph warning |
| 全量后端 pytest（frontend build 后串行、禁用本机不可写 cache） | 484 passed，3 个既有第三方 deprecation warning |
| 全仓 Ruff | passed |
| 既有 CI mypy gate | passed，13 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，10 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` tracked/history/diff 隔离 | 提交前复核 |

## 保持不变与后续工作

保持不变：provider Transport Retry、Assistant empty-response repair、ToolMessage safe error contract、Tavily/LLM fallback、sensitive approval、parser retrieval budget、identical-call guard、全图 recursion limit。

后续 R2 需要把 `reflection_rounds_used`、所有 LLM/tool call、RetryUsage、token 和 estimated cost 纳入统一 request/workflow budget；R6 可将 recovery metrics 接入可回放 trace artifact。真实 online fault injection 仍需受控 provider 凭据，本批所有失败均为 offline deterministic fake，不访问网络。

本批没有修改前端源码或样式，因此不重复浏览器视觉 smoke；production dist contract 已由全量 pytest 覆盖，前端 typecheck、Vitest 和 production build 也全部通过。三条全量 pytest warning 仍来自 LangGraph/Starlette 依赖弃用提示，使用 `-p no:cacheprovider` 后没有本机 `.pytest_cache` 权限 warning。
