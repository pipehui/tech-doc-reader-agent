# Workflow Budget Usage 与模型定价边界

## 本批目标

R2 要实现 request/workflow 双作用域预算，但此前系统没有可供策略消费的真实 usage 数据：前端 `token_count` 只是收到的 SSE 文本 chunk 数，Assistant 丢弃空响应修复的中间 `AIMessage`，transport retry 只记录等待/attempt，ToolNode 只记录日志耗时，checkpoint 也没有 LLM/tool/token/cost 字段。

如果直接先写 `max_tokens/max_cost` 判断，只能拿 chunk 数或缺失值填 0，得到一个看似工作的伪预算。本批先完成“计量与交付”闭环，不启用强制终止：建立版本化 usage schema、真实 LangChain usage metadata 提取、可配置 price table、图内累计、telemetry、SSE delta、REST state 恢复和前端协议。下一批再基于这些事实实现 before/after checks 与 partial termination。

## 最终模型

### `LlmUsage`

每条记录包含：

- application-level LLM calls；
- provider/model identity；
- input/output/total tokens，均允许 `None`；
- token 只来自最终 `AIMessage.usage_metadata`，不读取 SSE chunk 数。

`response_metadata.model_provider/provider` 和 `model_name/model` 优先；provider 缺失时使用显式 `MODEL_PROVIDER_ID`，不从可能含凭据或租户信息的 base URL 推断。

### `BudgetUsage`

checkpoint payload `schema_version=1`，包含：

- `workflow_started_at` UTC wall-clock timestamp；
- `llm_calls`、`tool_calls`；
- reported input/output/total token subtotal；
- 每一类 unreported-token call 数；
- priced cost subtotal；
- unpriced LLM call 数；
- 对外 `input_tokens/output_tokens/total_tokens/estimated_cost_usd`。

只要相应维度存在一次未上报，聚合字段就是 `null`；只要存在一次未定价调用，`estimated_cost_usd` 就是 `null`。reported/priced subtotal 仍保留用于审计，但绝不把 unknown 填成 0。

### `ModelPriceTable`

配置文件 schema 包含 table `schema_version/table_version/effective_at`，每个条目包含：

- provider；
- model；
- price version；
- effective timestamp；
- input/output USD per million tokens。

provider/model 不允许重复，rate 必须为 finite non-negative decimal，timestamp 必须带 timezone。`MODEL_PRICE_TABLE_PATH` 为空时加载显式 empty table；所有有 token 的调用仍记入 usage，但 cost reason 为 `price_not_configured`，estimated cost 保持 unknown。项目没有内置可能过时的公共模型价格。

## 实际接入

### 1. Assistant 保留所有可观察 LLM operation

Assistant 现在为一次 node invocation 生成内部 `_llm_usage` tuple：

- 每个成功 `AIMessage` 各提取一次官方 usage，包括后续被空响应修复替换的中间消息；
- application transport retry 的失败 attempt 记为 call，但 token/model 为 unknown；
- 最终消息照常进入 graph，内部 usage 由 `assistant_node` 消费后删除，不进入 message/checkpoint 协议。

因此 `timeout -> empty response -> final response` 会记录 3 个 application LLM calls、两条 provider usage 和一条 unreported retry，而不是只看最终回答。LangChain primary/backup composite 内部究竟调用了几个 provider 仍无法从最终 `AIMessage` 完整还原；本批把它明确记录为 application-level call，后续若需要 provider-level 精确计费，应使用 SDK callback/span，而不是猜测。

### 2. ToolNode 只统计真实执行 attempt

同步/异步成功和 exception fallback 都按 pending tool call 数增加 `tool_calls`。parser budget/repeated-call/reflection router 等在调用前产生的 policy block 不计数；finalizing 阶段被 router 关闭的 orphan call 同样不计数，因为目标工具从未执行。

### 3. Request reset 与 interrupt/resume

Graph builder 用 `budgeted_request_start_node` 包装 `fetch_user_info`：进入 START 时先创建新 `BudgetUsage`，再执行原 user-info node。approval resume 使用 `graph.stream(None, config)` 从中断节点继续，不经过 START，checkpoint 中累计值保持不变。新用户 request 才重置 workflow usage。

这实现 workflow usage 的 durable scope；request-local monotonic elapsed/deadline 不写 checkpoint，仍由下一批 config/request guard 实现。

### 4. Telemetry、SSE 与 REST

每条 LLM/tool delta 写入：

- `budget.usage.llm`：safe provider/model、call/token/cost delta、price identity/reason 和 cumulative usage；
- `budget.usage.tool`：tool call delta 与 cumulative usage。

Graph update 同时带 `budget_usage` 和 `budget_usage_delta`。SSE translator 只在 delta kind 为 `llm|tool` 且 cumulative payload 存在时发送 `usage_update`：

```json
{
  "node": "parser",
  "delta": {"kind": "llm", "llm_calls": 1, "total_tokens": 120},
  "usage": {"llm_calls": 2, "tool_calls": 1, "total_tokens": 340}
}
```

TypeScript contract/reducer 将 cumulative usage 保存到可选 `SessionState.budget_usage`，Inspector 自动记录新事件。`GET /sessions/{id}/state` 也返回可选 budget payload，刷新后不依赖 SSE 重放恢复。原前端 chunk `token_count` 继续仅表示流式 UI chunk，不再与 provider tokens 混用。

## 实施中遇到的问题

### 问题 A：最终 AIMessage 会漏掉空响应与 transport recovery 成本

只在 `assistant_node` 读取最终消息，空响应修复前的 provider response 已被 Assistant 局部变量覆盖；RetryExecutor 恢复前的失败 attempt 也没有 AIMessage。

处理：计量入口下沉到 Assistant 的每次 transport operation，返回一次性内部 usage tuple；graph adapter 统一定价/累计。失败 retry 没有 provider usage 时保持 token/cost unknown，而不是套用最终消息 token。

### 问题 B：缺价格不等于免费

当前模型和 OpenAI-compatible base URL 都可由用户配置，仓库无法安全假设具体厂商与价格。内置一个“常见价格”会随时间漂移，也可能给自建模型错误计费。

处理：价格表必须显式配置且带 table/entry version/effective timestamp。空表是正常状态，estimated cost 为 unknown；已定价 subtotal 与 unpriced call count 同时保留。

### 问题 C：一个 unknown 会污染聚合语义

若第一次调用已知 100 tokens、第二次缺 usage，简单累计得到 100 会让调用方误以为 workflow 总 token 就是 100；cost 同理。

处理：reported subtotal 与 public aggregate 分开。只要存在 unreported/unpriced call，public aggregate 为 null。后续拿到补充 usage 时可审计/重算，但当前绝不低估为一个确定值。

### 问题 D：policy block 不能算 tool call

ToolNode wrapper 同时处理真实异常、parser budget 和 repeated-call block。若在统一出口无条件 `+1`，未执行的 sensitive write 也会被算入 tool calls。

处理：success/exception fallback 计数；`_blocked_tool_call_update` 直接返回 reflection update，不经过 tracker。真实 exploding tool、success 和 repeated block 三路测试分别得到 `+1/+1/+0`。

### 问题 E：内部 usage 不能泄漏为 LangGraph state key

Assistant 需要把局部计量传给 graph adapter，但 `_llm_usage` 不是 durable schema，也含 Python dataclass。若直接返回给 StateGraph，可能被 checkpoint 序列化并形成内部协议。

处理：`assistant_node` 在同一函数调用内 pop 后转换成纯 JSON `budget_usage/delta`；没有 tracker 的兼容调用也会删除内部 key。测试断言 graph update 不含 `_llm_usage`。

### 问题 F：live SSE 有 usage，刷新后却会丢失

只加 `usage_update` 能让当前 Inspector 看见数据，但前端 reload 后 state REST 原本只返回 plan/agent。

处理：runtime session query、FastAPI response schema、TypeScript runtime decoder 和 optional SessionState 同步扩展；Python/TypeScript contract tests 防止字段或 event name 单边漂移。

## 配置

| 配置 | 默认值 | 含义 |
|---|---|---|
| `MODEL_PROVIDER_ID` | `openai_compatible` | usage/price lookup 的稳定 provider identity |
| `MODEL_PRICE_TABLE_PATH` | 空 | 可选 versioned JSON price table；空表示 cost unknown |

两项已同步 `.env.example`、Pydantic Settings 与 Docker Compose。provider id 必须是非空、无首尾空格字符串。

## 验证范围

聚焦测试覆盖：

- price schema/version/effective timestamp/rate/duplicate validation；
- priced、unknown model、missing token estimate；
- corrupt/missing price file 的安全错误；
- LangChain usage metadata 与 provider/model fallback；
- BudgetUsage priced/unpriced/unreported 累计和 checkpoint round-trip；
- transport retry + empty response + final response 三次计量；
- tool success/failure/policy block；
- START reset 与 workflow timestamp；
- telemetry 不含 raw exception；
- `usage_update` Python SSE、TypeScript parser/reducer 和 REST refresh contract。

| 验证 | 结果 |
|---|---|
| 本批 targeted Python pytest | 52 passed，3 个既有第三方 warning |
| 全量后端 pytest（frontend build 后串行、禁用本机不可写 cache） | 512 passed，3 个既有第三方 deprecation warning |
| 全仓 Ruff | passed |
| 既有 CI mypy gate | passed，15 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，19 source files |
| frontend targeted Vitest | 2 files，17 tests passed |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` tracked/history/diff 隔离 | passed，任务单未进入 HEAD、origin/main 差异或待提交集合 |

## 明确保留到下一批

- `ExecutionBudget` limits 与 before/after decision；
- request-local monotonic elapsed/deadline；
- workflow LLM/tool/token/cost 超限策略和明确 partial termination；
- approve/resume 前的 budget recheck；
- provider fallback 内部真实 SDK call/span 精确计数；
- 整次 Assistant 在最终失败、没有 graph update 时的 usage 持久化；
- model price effective-date 选择/历史重算（当前一个 table snapshot 每个 provider/model 只允许一个 entry）。

本批是可执行预算的事实基础，不宣称已经限制调用或费用。
