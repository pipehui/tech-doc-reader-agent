# 10 - Settings、Tenant、错误、重试、预算与上下文

前几章按业务链路阅读；本章解释那些“每条链路都经过、却不属于某个业务模块”的机制。它们最容易被误放进工具、route 或 Agent prompt，最后形成重复判断。

横切机制的正确读法不是背类名，而是回答三个问题：

1. contract 在哪里定义；
2. 谁在 composition/runtime 边界把它注入；
3. 哪些层只能消费结果，不能自行发明一套规则。

## Settings：一次解析，多处注入

位置：[`core/settings.py`](../../tech_doc_agent/app/core/settings.py)。

`Settings` 基于 Pydantic Settings，从环境和 `.env`、`.dev.env` 读取配置，未知字段忽略。主要分组：

| 分组 | 代表字段 | 消费者 |
| --- | --- | --- |
| LLM | `OPENAI_*`, `PRIMARY_MODEL`, backup model | model provider / registry |
| embedding/RAG | `EMBEDDING_*`, `HYBRID_RAG_*` | embedding、FaissStore、HybridRetriever |
| persistence | `DATA_PATH`, `REDIS_URL` | resources/lifecycle/repositories |
| retry | `TRANSPORT_RETRY_*` | `build_retry_executor` |
| guardrail | `GUARDRAIL_APPROVAL_TTL_SECONDS` | approval repository/service |
| workflow budget | `REQUEST_MAX_SECONDS`, `WORKFLOW_MAX_*` | runtime config / graph budget tracker |
| loop policy | repeated tool、parser retrieval、reflection | GraphSpec execution policy |
| context | compaction thresholds/keep turns/summary chars | ContextCompactor |
| telemetry | pseudonym key、Langfuse 配置 | observability / tracing |
| deployment identity | commit SHA fields | runtime identity |
| API | `ALLOWED_ORIGINS` | FastAPI server |

`get_settings()` 使用 `@lru_cache`。同一进程正常只解析一次，避免每个 log/tool 都重新读 env。测试修改环境变量后若调用全局 getter，要 `get_settings.cache_clear()`；更推荐直接构造 `Settings(...)` 并通过 composition 注入。

### `0` 是否表示“关闭”取决于具体 policy

`build_execution_budget` 把 budget 数值 `0` 转成 `None`；context compaction 两个 threshold 都为 0 时 disabled：

```text
WORKFLOW_MAX_TOTAL_TOKENS=0 -> 不启用 token 上限
CONTEXT_COMPACTION_MAX_MESSAGES=0 -> 不按消息数触发
```

但 loop policy 不是这个语义：`PARSER_MAX_RETRIEVAL_CALLS=0` 会阻止第一次 parser 检索，`MAX_IDENTICAL_TOOL_REPEATS=0` 会阻止任何单工具调用，`MAX_REFLECTION_ROUNDS=0` 表示不允许参数修复轮次。不能只看 `Field(ge=0)` 猜含义；应跟到对应的 policy builder/evaluator。若产品要统一零值语义，应改 contract、测试和文档，而不是在某个调用点特判。

### Secret 与校验

`TELEMETRY_PSEUDONYM_KEY` 是 `SecretStr`，配置时至少 16 bytes。部署/image commit SHA 必须是完整小写 Git SHA，二者同时存在时必须一致。`ALLOWED_ORIGINS` 同时支持 JSON array 与逗号分隔字符串。

新增 setting 时至少要同步 `.env.example`、部署/docker 配置、相关文档和 validator 测试。不要直接在业务函数里 `os.getenv`，否则配置来源和校验会分叉。

## Tenant：严格解析和宽松归一化不是一回事

位置：[`core/tenant.py`](../../tech_doc_agent/app/core/tenant.py)。

默认值：

```text
user_id = default
namespace = tech_docs
```

有效 ID 必须匹配：

```regex
^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$
```

### `parse_tenant`

这是信任边界的严格入口。类型错误、空白/非法字符都会抛 typed `invalid_tenant`；`None` 才使用默认值。API、runtime config 和工具命令应该使用它，不能把攻击性/拼错的 tenant 静默归入 default。

### `normalize_tenant`

这是兼容/展示边界的宽松入口。缺失、空白或非法值回退默认 tenant。它适合恢复旧 metadata 或构造安全默认对象，不适合验证外部写请求。

### ContextVar 与 RunnableConfig 的优先级

`parse_tenant(..., prefer_context=True)` 可优先取当前 trace ContextVar；但 LangGraph tool 应调用：

```python
tenant_from_config(config)
```

优先级是 `config["metadata"] -> ContextVar -> default`。原因是 graph/thread 执行可能跨线程；显式 RunnableConfig 比依赖隐式 context copy 更可靠。

同理，`session_id_from_config` 先读 metadata，再读 trace context。

checkpoint thread ID：

```text
<user_id>:<namespace>:<session_id>
```

tenant 同时存在于 API body/header、runtime metadata、graph state、learning/profile key、前端 URL/localStorage。修改规则必须做全链检查，不能只改 `tenant.py`。

## Typed error：内部原因不能直接穿过边界

位置：[`core/errors.py`](../../tech_doc_agent/app/core/errors.py)。

`ApplicationError` 的公开字段固定为：

```text
status=error
code
retryable
safe_message
dependency
tool
cause_type
```

子类表达稳定类别：Validation、PermissionDenied、RateLimited、Timeout、DependencyUnavailable、Conflict 和 UnknownDependencyError。

`classify_error(raw, dependency?, tool?)`：

- 已是 `ApplicationError`：保留 code/message，只补 context；
- 根据 HTTP status、Python exception 类和类名 marker 分类；
- 从异常 module/type 推断 redis、tavily、duckduckgo、faiss、llm、file repository；
- 从不把 `str(raw_exception)` 当 safe message。

这使 SSE、ToolMessage、日志和 API 能共享错误结构，同时不泄漏 provider response、文件路径、凭证或用户内容。

### `cause_type` 不是原始异常文本

它通常是异常类型/受控标签，适合聚合排障，不应包含消息内容。需要完整 stack trace 时留在受保护的内部异常链/开发环境，不要塞进 SSE payload。

### 捕获边界

推荐模式：

```python
try:
    ... concrete dependency ...
except ApplicationError:
    raise
except Exception as exc:
    raise classify_error(exc, dependency="...") from exc
```

只在 infrastructure/provider 边界做 raw exception 映射。domain/application 不应捕获所有异常后统一返回字符串；route 也不应每个 endpoint 重复推断 provider 类型。

## Redaction：记录结构前先去敏

位置：[`core/redaction.py`](../../tech_doc_agent/app/core/redaction.py)。

`RedactionPolicy.redact` 递归处理 mapping/list/tuple/set，并识别：

- Authorization/Cookie/credential 字段；
- API key、JWT、password、token、带密码 URI；
- email 与电话号码；
- bytes 和循环引用。

设置 pseudonym key 后，`user_id` 使用带 key 的 HMAC 生成稳定 pseudonym。这是可关联化，不宣称匿名化。

`input_token/output_token` 不因 `_token` 后缀被当凭证，否则预算指标会被抹掉。新增敏感字段时应扩展统一 policy，不要只在某一个 log call 手动替换。

## Observability：ContextVar 传播请求身份

位置：[`core/observability.py`](../../tech_doc_agent/app/core/observability.py)。

`trace_context(**fields)` 在 `ContextVar` 中叠加 trace/session/tenant/operation 等字段，并在退出时恢复上一层。`log_event(event, **fields)`：

1. 加 UTC timestamp 和当前 context；
2. 合并本次 fields；
3. 用 telemetry redaction policy 递归清洗；
4. 输出单行 JSON。

`timed_node(name)` 统一记录 `node.started/node.finished`；异常时记录 elapsed 与 `safe_error_fields` 后重新抛出。

异步 SSE 迭代不能只在创建 generator 时进入 context；项目在每次 `anext` 周围恢复 trace context，详见 [02 - Chat API 与 SSE](02-chat-api-and-sse.md)。否则 generator 真正执行时可能已经离开 route 的 context manager。

### Langfuse 是可选观测，不是业务依赖

位置：[`core/langfuse_tracing.py`](../../tech_doc_agent/app/core/langfuse_tracing.py)。

只有 enabled 且 public/secret key 都存在时才构造 trace。SDK 未安装、trace URL 或 flush/shutdown 失败会记安全日志，不应让正常聊天失败。Langfuse mask 复用同一 redaction policy。

如果新增 telemetry backend，也应遵守“业务结果不依赖观测成功”和“先 redaction 再外发”两条约束。

## Transport retry：只重试明确幂等的操作

位置：[`core/retry.py`](../../tech_doc_agent/app/core/retry.py)。

`RetryExecutor.run/arun` 接受：

```text
operation
operation_name
dependency
idempotent
tool?
before_attempt?
```

一次失败先经 `classify_error`。只有同时满足以下条件才继续：

- mapped error `retryable=True`；
- 调用方声明 `idempotent=True`；
- 尚未达到 max attempts；
- `Retry-After` 没超过本地上限。

延迟采用有上限的指数 backoff + jitter，并尊重更大的合法 Retry-After。每次 attempt、scheduled retry 和 final outcome 都有结构化 event。

### `before_attempt` 也在 retry contract 内

Tavily 每次实际 attempt 前预留额度。如果 hook 自己失败，记 `before_attempt_failed`，provider attempt 数不虚增，mapped error 直接上抛。

### 不要给写操作随便标幂等

“HTTP POST”不自动意味着非幂等，“函数名字 read”也不自动保证无副作用。调用方必须根据实际 provider/command identity 判断。学习写入能安全重放是因为有 tool_call_id 幂等 ledger；普通文件 append 没有这个保证。

## Provider retry usage 与 LLM usage 是两条账

位置：

- [`core/retry_usage.py`](../../tech_doc_agent/app/core/retry_usage.py)
- [`graph/provider_retries.py`](../../tech_doc_agent/app/graph/provider_retries.py)

`capture_retry_usage()` 用单独 ContextVar 收集 `RetryUsage`，目前覆盖经过 `RetryExecutor` 的 embedding/web 等 provider operation。tool node 完成后 `ProviderRetryUsageTracker` 把增量和累计 ledger 写入 graph state，再经 SSE `provider_retry_update` 发给前端。

LLM transport attempts 由 Assistant/`LlmUsage` 与 workflow budget 跟踪，不要把两份指标相加成“LLM 调用次数”。名称里的 provider retry 是通用外部 provider 账，不等于 ChatModel retry。

## Execution budget：在操作前后都检查

核心位置：

- [`core/execution_budget.py`](../../tech_doc_agent/app/core/execution_budget.py)
- [`graph/budgeting.py`](../../tech_doc_agent/app/graph/budgeting.py)
- [`graph/budget_termination.py`](../../tech_doc_agent/app/graph/budget_termination.py)

预算维度：

```text
request elapsed seconds
workflow LLM calls
workflow tool calls
workflow total tokens
workflow estimated cost USD
```

请求时限 window 在 runtime config 创建并放入 RunnableConfig metadata，使用 monotonic time；workflow usage 存 graph state，能随 checkpoint 恢复。

### 为什么 before 和 after 都要检查

- before：已知下一次调用一定越限时不启动昂贵操作；
- after：provider 实际返回的 token/cost 可能一次跨过限额，需要阻止下一跳；
- resume：审批等待后重新进入时，请求 window/预算仍需验证。

tool before-check 按即将执行的 tool call 数投影。若预算阻止一批 tool，系统会为未执行 call 生成成对的 error ToolMessage，保持 LangGraph message protocol 闭合，而不是直接丢掉 AI tool calls。

### token/cost 未上报时的安全行为

如果配置了 token 或成本上限，但 usage 为 `None`，继续调用就无法证明仍在预算内。before LLM 会产生 `usage_unreported` 决策并安全终止。这是 fail-closed，不是计为 0。

### 终止状态机

```text
active
  -> terminating（某个 wrapper/节点发现 decision）
  -> budget_terminated 节点
  -> terminated
  -> END
```

路由优先检查 budget terminating，使正常 handoff/finish 不会绕过终止。SSE 分别发 started、usage update、terminated，前端保存完整 decision。

## Tool policy：预算之外的循环保险

位置：[`graph/tool_policy.py`](../../tech_doc_agent/app/graph/tool_policy.py)。

它在真正 ToolNode 之前检查两类局部问题：

1. parser 当前 step 对 `read_docs + web_search` 的总调用数超过上限；
2. 单 tool call 的 `name + 稳定序列化 args` 连续重复超过上限。

block 结果不是 Python exception，而是 status=error 的 ToolMessage，带 typed `Conflict` artifact。模型能看到受控说明并继续用已有证据收尾；graph message 也保持 call/result 对应。

只在“最后 AIMessage 恰有一个 tool call”时应用这些规则。并行 tool calls 的预算由 execution budget 计数，但 repeated signature policy 当前不会逐个处理。扩展并行调用时要显式设计，不要假设已有保护自动覆盖。

## Reflection：工具错误后的有限修复

位置：[`graph/reflection.py`](../../tech_doc_agent/app/graph/reflection.py)。

ToolNode 返回 error messages 后，`apply_reflection_policy` 只根据公开 error payload 判断：

- code 在 repairable set 且 rounds 未用完：`repairing`，允许模型修改参数再试一次；
- 不可修复或次数耗尽：`finalizing`，要求不再用 tool、基于已有证据给部分结果/退出；
- finalizing 后又产生 tool error：`terminal`，关闭当前链。

repair context 只提取 Pydantic validation 的公开 location/type，最多 8 条，不包含输入值或异常消息。这样模型能修参数但看不到敏感 provider details。

预算 terminating 的路由优先于 reflection terminal。预算是全局硬限制，不能因“再修一次”被绕过。

## Context metrics 与 message scope

位置：

- [`graph/context_metrics.py`](../../tech_doc_agent/app/graph/context_metrics.py)
- [`graph/message_scope.py`](../../tech_doc_agent/app/graph/message_scope.py)
- [`core/context_serialization.py`](../../tech_doc_agent/app/core/context_serialization.py)

每次 Assistant 调用前测量：

```text
checkpoint message count/serialized bytes
真正传给 prompt 的 message count/serialized bytes
agent + scope
```

调用后结合 LlmUsage 记录 input tokens。这样能区分“checkpoint 很大”与“某 Agent 经过 scoped view 实际只看一段”。parser/relation/explanation/examination 使用局部 message scope，summary 看完整 history；不要仅根据 checkpoint message 数评估每次模型成本。

## Context compaction 只在安全边界执行

位置：

- [`core/context_compaction.py`](../../tech_doc_agent/app/core/context_compaction.py)
- [`graph/context_compaction.py`](../../tech_doc_agent/app/graph/context_compaction.py)
- [`application/conversation_summarizer.py`](../../tech_doc_agent/app/application/conversation_summarizer.py)
- [`core/conversation_summary.py`](../../tech_doc_agent/app/core/conversation_summary.py)

compactor 位于每次请求获取用户信息之后、进入主 Agent 之前。触发条件是 message count 或 serialized bytes 超 threshold，但还必须同时满足：

- 当前最后一条是 HumanMessage，即新 turn 边界；
- 不在 active dialog；
- workflow 已完成或为空；
- reflection 不在 repairing/finalizing/terminal；
- 足够多的 closed turns，保留最近 N turns；
- 被移除区间的所有 tool call 都有唯一对应 ToolMessage。

任何条件不满足就 skip 并给出受控 reason。尤其不能截断一个开放 tool exchange，否则 checkpoint 下次恢复会遇到 AI tool call 没 result。

### 摘要不是再调用一次 LLM

`ExtractiveConversationSummarizer` 是确定性的：

- Human -> 截断后的用户文本；
- AI -> agent 文本 + 请求的 tool names；
- Tool -> 只记工具名与状态，不复制原始 tool payload；
- 累积摘要有字符上限，过长保留头尾并插入 marker。

`ConversationSummary` 保存 source message ID 范围、序列化 SHA、generator ID、前任 summary ID 和 covered count，summary ID 由内容和来源稳定哈希。加载时严格验证，损坏不会静默当正常上下文。

实际 graph update 使用：

```python
RemoveMessage(id=REMOVE_ALL_MESSAGES), *retained_messages
conversation_summary = summary.to_state()
```

也就是先清除全部 checkpoint messages，再放回计划保留的最近消息；不是逐条删除 source。摘要由 prompt composition 作为独立上下文使用。

## 改横切机制时的依赖顺序

一个比较安全的修改顺序：

```text
typed core model/policy
  -> application/runtime/graph wrapper
  -> composition 注入
  -> state/API/SSE projection
  -> frontend decoder/store/view
  -> settings/env/docs/tests
```

例如新增 workflow max embedding calls，不应先在 `web_search` tool 里读 env；应先定义 usage/decision，再让 provider wrapper记录、graph state投影、SSE/前端显示，最后接 setting。

## 常见误区

### “所有失败都 retry 三次”

Validation、permission、conflict 和非幂等 operation 不应 retry。盲重试会重复写入、放大限流并拖慢安全失败。

### “日志只在本机，所以能打原始 prompt”

部署后日志通常会汇聚到外部系统。统一 redaction 是边界要求，不是可选美化。

### “ContextVar 里已经有 tenant，tool 不用 config”

跨线程/graph executor 时隐式上下文可能不可靠；工具应优先 RunnableConfig metadata。

### “预算终止直接 raise 就行”

直接异常会丢失成对 ToolMessage、状态 projection 和 `budget_terminated` SSE。预算是正常的可解释控制流，不是未知故障。

### “压缩就是保留最后 N 条 message”

turn、tool exchange 和 active workflow 都会跨多条 message。按条数硬切会破坏协议；当前 planner 是按 Human turn boundary 切，并验证闭合工具交换。

### “加一个 setting 只改 Settings class”

没有 composition 消费、env 示例、部署注入与测试的 setting 是死配置；直接散落读取又会形成多份事实源。

## 建议测试搜索入口

```powershell
rg -n "tenant|classify_error|redact|RetryExecutor|ExecutionBudget|tool_policy|reflection|compaction|ContextMetrics" tests
```

至少覆盖：

- strict tenant 拒绝非法值、compat normalization 回退；
- error 分类不暴露 raw message；
- recursive redaction、pseudonym 稳定性和 secret suffix；
- non-idempotent/non-retryable/max attempt/Retry-After 行为；
- request/workflow budget 的 before、after、resume 和 usage-unreported；
- 被预算阻止的 tool call 仍有 error ToolMessage；
- repeated/parser tool policy 计数边界；
- reflection repair/finalize/terminal 有限状态；
- compaction disabled、threshold、安全 skip reason、closed exchange、摘要 hash/chain；
- ContextVar 在 async iterator/thread bridge 中的传播与恢复。

下一章 [11 - 修改手册](11-change-recipes.md) 不再按模块讲解，而是给“新增 Agent/工具/SSE/字段/存储”等常见需求列出逐文件改动清单。
