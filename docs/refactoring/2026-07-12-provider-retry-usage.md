# Provider retry request ledger 与跨端交付

## 本批结论

上一批已经让 LLM transport 的真实 attempt 进入 `BudgetUsage`，但 embedding 和 web search 的 `RetryUsage` 只存在于瞬时日志。一个外层 `read_docs` 或 `web_search` tool call 可能实际发出多次 provider 请求，甚至经历 Tavily exhausted 后再走 DuckDuckGo；原来的 tool count 与 request elapsed 无法精确表达这些事实。

本批完成以下链路：

- 新增不可变 `RetryUsage`/`RetryUsageLedger` 与严格 state round-trip 校验；
- production `build_retry_executor()` 通过 request-local observer 自动上报最终 operation；
- ToolNode 在 success、typed fallback 和 exhausted failure 三条路径统一收集；
- graph state 保存累计 `provider_retry_usage`，同时输出只包含本节点新增 operation 的 delta；
- request 从 `START` 开始时重置，interrupt/resume 不经过 `START` 时继续累计；
- session REST、SSE contract、前端 store/Inspector 和 online eval 使用同一份版本化事实；
- embedding 与 web fallback 的真实生产入口测试证明默认 executor 已接通，而不只是 fake executor 能工作。

这仍是观测账本，不是用户计费，也不是 `ExecutionBudget` 的第六个维度。

## 边界设计

### 1. Core 只定义事实和收集机制

`core/retry_usage.py` 包含：

- `RetryUsage`：一次逻辑 provider operation 的最终事实；
- `RetryUsageLedger`：不可变累计、schema version、summary 重算和 checkpoint 校验；
- `RetryUsageCollector`：线程安全的 request-local collector；
- `capture_retry_usage()` / `observe_retry_usage()`：基于 `ContextVar` 的动态绑定；
- `retry_usage_delta_payload()`：`reset` 与 `operations` 增量协议。

每条 operation 记录：

| 字段 | 语义 |
|---|---|
| `attempts` | 已真正进入 provider operation 的次数 |
| `retries` | 首次 attempt 之后的额外 attempt 数 |
| `waited_seconds` | retry policy 实际等待总时长 |
| `outcome` | `succeeded`、`failed` 或 `exhausted` |
| `reason` | 有限策略产生的受控终止原因 |
| `error_code` | 统一错误模型的安全 code，不含原始异常文本 |

summary 每次从 operation 重算，checkpoint 中伪造或过期的 summary 会被拒绝，不能让“明细”和“汇总”各自漂移。

### 2. Retry executor 不持有具体 request

Embedding/Web backend 通常在应用启动时构造并长期复用，不能把某个 HTTP request 的 collector 注入 singleton executor。`build_retry_executor()` 因此持有稳定的 `observe_retry_usage` 函数；该函数在 operation 最终完成时查找当前上下文中的 collector。没有 active capture 时只保留原有结构化日志，不产生全局可变累计。

直接构造 `RetryExecutor` 的既有测试/调用方默认仍不启用 collector；显式 `usage_observer` 继续优先，保持可注入能力。

### 3. Graph adapter 负责 durable state

`graph/provider_retries.py` 只处理 graph 语义：

- 从 state 校验并恢复旧 ledger；
- 追加当前 ToolNode 捕获的 operations；
- 写累计 state 与本节点 delta；
- 记录只含数字和安全分类的 telemetry；
- request-start wrapper 写空 ledger 和 `kind=reset`。

`core` 不 import LangGraph，`RetryExecutor` 不 import graph，API/eval 也不负责重新推断 attempts。

### 4. 对外协议与预算分离

新事件命名为 `provider_retry_update`，而不是扩充 `usage_update`：

- `usage_update` 是 LLM/tool/token/cost budget ledger；
- `provider_retry_update` 是 embedding/web transport operation ledger；
- 两者作用域、单位和 enforcement 语义不同，不能直接求和。

前端 reducer 把累计账本写入 `SessionState.provider_retry_usage`，Inspector 记录事件并显示本次 operation/retry 数。刷新页面后，`GET /sessions/{id}/state` 仍能恢复同一账本。

Online eval 只累计 `operations` delta，忽略 `reset` 的数值；这样 `/chat` 与 `/chat/approve` 多段 SSE 不会因为完整累计快照而重复计数。每条 row 保存完整 version 1 ledger，报告再从通过校验的 operation 重算总计。

## 实施中遇到的问题

### 问题 A：ToolNode fallback 会先消费 provider 异常

如果 collector 只包在 successful tool result 之后，exhausted retry 抛出的 typed error 会先被 `.with_fallbacks()` 转为安全 ToolMessage，调用方再也看不到该 provider operation。

处理：先构造完整的 guarded ToolNode + reflection fallback，再在最外层同时包住 sync `invoke` 与 async `ainvoke`。无论正常返回还是 fallback 返回，collector 都在 finally 退出前保留已经 finalized 的 usage。

### 问题 B：一次 web tool call 可能包含两个 provider operation

Tavily exhausted 后 DuckDuckGo success 不能压成一个“web_search retry”。两者 dependency、outcome 和 attempts 都不同。

处理：collector 保存 operation 序列，ledger summary 再按 dependency 聚合。生产测试实际得到 `web_search.tavily(exhausted, 3 attempts)` 和 `web_search.duckduckgo(succeeded, 1 attempt)` 两条记录。

### 问题 C：异步 ToolNode 可能在线程中运行同步工具

只用普通 request 对象或非线程安全 list，可能在 `ainvoke` 的线程切换后丢失记录或发生竞争。

处理：使用 `ContextVar` 作为 request 绑定、collector 内部用 lock 保护序列，并用异步 ToolNode + 同步失败工具的测试验证 exhausted usage 能回到外层 state。

### 问题 D：request reset 与 approval resume 不能混为一谈

每个 SSE 连接都重置会让 sensitive-tool interrupt 后的 `/chat/approve` 丢失之前 attempts；永不重置又会把下一条用户消息累计到旧 request。

处理：重置绑定 graph `START -> fetch_user_info`，不绑定 HTTP 连接。真正 resume checkpoint 不经过 `START`，继续累计；新用户 request 经过 `START`，产生 `reset`。

### 问题 E：eval 不能累计完整 usage snapshot

每个 ToolNode event 都携带截至当前的完整 ledger。若 eval 把每帧完整 ledger 相加，前一节点 operation 会被反复统计。

处理：SSE 同时交付累计 `usage` 与局部 `delta`；UI 用累计值恢复状态，eval 只追加 `delta.operations`，最终再生成自己的严格 ledger。

### 问题 F：retry telemetry 不能成为秘密旁路

原始异常可能含 provider endpoint、proxy password 或 API key。如果把 exception message 写入 checkpoint/SSE/eval，现有日志 redaction 不能保护所有 durable copies。

处理：ledger 只接受受控 operation/dependency/tool、policy reason 和统一安全 error code；原始异常 message 与 request 输入没有 schema 字段。embedding/web fault tests 显式断言私密文本不在 payload 中。

## 验证范围

定向验证已覆盖：

- ledger round-trip、summary 重算、tamper 和非法数值拒绝；
- built executor 的动态 request-local capture 与请求隔离；
- graph request reset、跨节点累计和局部 delta；
- ToolNode sync recovery、async exhausted fallback 与安全错误；
- production embedding retry；
- production Tavily exhausted -> DuckDuckGo fallback；
- session REST 与 SSE Python payload/golden contract；
- TypeScript decoder、reducer、Inspector 与 REST decoder；
- online eval SSE 收集、JSON row、summary/category/case report；
- 变更源文件 Ruff、mypy 与 TypeScript check。

| 验证 | 结果 |
|---|---|
| 全量后端 pytest | 686 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 148 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- LLM transport attempt 继续由既有 `BudgetUsage` 计量，本批没有复制进 provider ledger；
- embedding/web provider 明细目前是观测项，不参与 budget termination；
- 不在 ToolNode request capture 内运行的 provider operation仍只有 `retry.*` 结构化日志；
- online eval 现在能报告 retry 成本代理，但 provider fallback provenance、structured fallback 和真实货币成本仍需分别建模；
- circuit breaker、shared outage state 与 half-open probe 仍属于 R5，不因拥有 retry ledger 自动完成。
