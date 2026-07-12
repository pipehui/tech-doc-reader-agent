# 统一 Transport Retry 策略

## 本批目标

统一错误模型已经能区分限流、超时、5xx、权限与校验错误，但调用方仍没有一致的恢复策略：OpenAI SDK 和 `ChatOpenAI` 使用各自的隐式默认重试，Embedding 与 Web provider 直接调用 SDK，Assistant 的 `max_retries` 实际只处理“模型成功返回空内容”。重试次数、等待时间和最终结果因此既不可配置，也无法进入统一 telemetry。

本批完成可靠性清单 R1 的 transport 部分：建立一个显式、有限、可观测的重试边界，只接入 LLM、Embedding、Tavily 和 DuckDuckGo 这四类外部幂等调用。Reflection、参数修复和三维 ExecutionBudget 没有混入本批。

## 最终边界

`core/retry.py` 现在集中提供：

- `RetryPolicy`：总尝试次数、初始等待、最大 backoff、倍率、jitter 与可接受的最大 `Retry-After`；
- `RetryExecutor.run/arun`：同步、异步入口共享同一个 `_RetrySession` 状态机，避免两套判断漂移；
- `RetryUsage`：operation、真实 provider attempts、实际额外 provider retries、waited seconds、outcome、reason 与安全 error code；
- `RetryUsageObserver`：为后续 R2 ExecutionBudget 预留的完成回调，但本批不假装已经实现预算扣减。

每个调用点必须显式声明 `idempotent=True/False`，没有默认值。只有统一错误模型标记为 `retryable=True` 的 rate limit、timeout、5xx/connection failure 才能进入下一次尝试；validation、permission、conflict、unknown error、护栏阻断和非幂等操作都只执行一次。

等待采用有限 exponential backoff + 对称 jitter。provider 返回数字秒或 HTTP-date 形式的 `Retry-After` 时，实际等待取 backoff 与 provider 要求的较大值；若 provider 要求超过 `TRANSPORT_RETRY_MAX_RETRY_AFTER_SECONDS`，当前请求不会提前重试，也不会无限挂起，而是以 `retry_after_exceeds_limit` 结束，让既有 fallback 或上层错误边界接管。

## 实际改动

### 1. LLM transport 与空响应修复分层

- `Assistant.max_retries` 重命名为 `max_empty_response_retries`，明确它只负责“HTTP 已成功但没有真实内容”的语义修复；
- transport retry 位于 `runnable.invoke/ainvoke` 外层，一次 transport attempt 内仍保持 primary -> backup 的既有 LangChain fallback；
- `AssistantDefinition` 从 `AssistantModelProvider` 注入共享 executor；手工构造的测试/自定义 provider 没有 executor 时仍保持原先的单次调用和错误映射；
- primary 与 backup 的 `ChatOpenAI(max_retries=0)` 关闭 SDK 隐式重试，确保应用层是唯一计数边界；
- 空响应补充消息改为类型化 `HumanMessage`，修复此前 tuple message 对 `State` 类型的偏离。

因此 transport failure 不会消耗空响应修复轮次；空响应也不会被误分类为网络故障。最坏情况下底层模型请求数仍可能是 `transport max attempts * configured model providers * empty-response rounds`，它是有限的，但只有 R2 接入 token/cost budget 后才能形成跨层成本上限。

### 2. Embedding 只重试远程 create

- OpenAI embedding client 使用 `max_retries=0`；
- executor 只包裹 `client.embeddings.create`，配置校验和 response shape 解析位于重试边界外；
- provider 返回 malformed response 时产生非重试的 `embedding_response_invalid`，不会用重复请求掩盖协议漂移；
- FAISS add/search 不需要感知 retry，也不会重复发布本地 generation。Embedding 请求本身恢复成功后，候选 generation 才进入既有原子发布流程。

### 3. Web provider retry 与 Tavily quota 对齐

- Tavily、DuckDuckGo 的 raw provider call 都使用同一 executor，既有 Tavily -> DuckDuckGo fallback 只在前者重试耗尽后发生；
- 原来的 `can_use_tavily()` 与 `consume_tavily_quota()` 是两个锁区间，并且一次高层 search 无论发出多少次请求都只记一次；
- 现在每次 Tavily provider attempt 之前都在同一进程锁内重新检查并预留额度，成功持久化后才发请求；达到本地日限额时不再重试 Tavily，直接进入 DuckDuckGo fallback；
- quota JSON 写入失败会恢复内存旧值，provider 不会被调用，且该前置写入不会被 transport executor 自动重放；
- provider 返回成功但 payload 无效时以 validation error 处理，不进行 transport retry。

该锁解决同一进程内的 check/consume race，不宣称解决多 worker 对同一 JSON 文件的跨进程一致性。若生产部署启用多个 app worker，quota counter 应迁移到具备原子自增和 TTL 的共享存储，这属于后续 persistence/部署工作。

### 4. Telemetry 与配置

每次调用产生安全的结构化事件：

- `retry.attempt`：当前 attempt 与有限上限；
- `retry.scheduled`：下一次 attempt、等待秒数、`Retry-After`、安全 error code/cause type；
- `retry.final`：attempts、retries、总等待、outcome 与 reason。

事件不记录 query、prompt、provider exception message、URL 或凭据。`RetryUsageObserver` 收到相同的最终统计，供 R2 接入 request/workflow budget；当前尚未建立 `ExecutionBudget`，因此本地 TODO 将 telemetry 与 budget 拆成两个独立状态。

新增环境项及默认值：

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `TRANSPORT_RETRY_MAX_ATTEMPTS` | 3 | 包含首次调用的总尝试数 |
| `TRANSPORT_RETRY_INITIAL_DELAY_SECONDS` | 0.25 | 第一次重试前的基础等待 |
| `TRANSPORT_RETRY_MAX_DELAY_SECONDS` | 2.0 | exponential backoff 上限 |
| `TRANSPORT_RETRY_BACKOFF_MULTIPLIER` | 2.0 | 每轮 backoff 倍率 |
| `TRANSPORT_RETRY_JITTER_RATIO` | 0.2 | 基础等待上下浮动比例 |
| `TRANSPORT_RETRY_MAX_RETRY_AFTER_SECONDS` | 30.0 | 当前请求可接受的 provider 最长等待 |

`.env.example`、Pydantic `Settings` 与 Docker Compose 使用相同默认值和约束。

## 实施中遇到的问题

### 问题 A：项目里已经存在另一种“retry”

Assistant 的旧 `max_retries` 并不是 transport retry；它在成功拿到空 `AIMessage` 后追加 “Respond with a real output.” 再调用模型。如果直接把网络重试塞进这层循环，timeout 会错误消耗 semantic repair 次数，telemetry 也无法区分两种恢复。

处理：重命名现有字段，并把 sync/async transport 调用提取成独立 helper。故障注入用例按 `timeout -> empty response -> real response` 执行，确认第一次 semantic round 内完成两次 transport attempt，随后才进入一次空响应修复。

### 问题 B：SDK 自带重试会让应用计数失真

本地检查确认 OpenAI Python client 默认 `max_retries=2`，`ChatOpenAI` 也会把重试交给底层 client。如果只在外层再包一层，配置为 3 attempts 时真实网络请求可能远超 3，日志和未来预算都只看到外层次数。

处理：primary、backup 与 embedding client 显式设置 `max_retries=0`。Tavily 当前版本使用普通 `requests.Session.post`，DuckDuckGo client 也没有项目可配置的额外重试层；它们由应用 executor 统一控制。

### 问题 C：ToolNode fallback 位于错误边界之后

Graph 的 ToolNode 会在工具调用失败后生成结构化 error `ToolMessage`。若把 retry wrapper 放到 ToolNode 外层，原始 provider exception 已经被消费，只剩一个“成功返回的错误消息”，重试器无法按 status/header 分类；如果给整个 ToolNode 重试，又可能重放 `save_docs`、profile update 等敏感写入。

处理：不修改 ToolNode。executor 只出现在 Assistant transport、Embedding 与两个 Web provider 模块；结构测试枚举允许引用 retry 的生产模块，防止以后无意把它接到 graph/tool/write path。非幂等故障注入也确认即使错误类型本身 retryable，`idempotent=False` 仍只调用一次。

### 问题 D：Tavily retry 会绕过原日配额语义

初版接入如果沿用“search 前扣一次”，一个高层调用可在额度只增加 1 的情况下发出 3 次 Tavily 请求；如果简单在 retry operation 内写 usage JSON，前置写失败又会被当作 transport failure 自动重试。

处理：executor 增加 `before_attempt` hook。它在每个远程 attempt 前执行额度预留，但 hook 本身不是 transport operation，失败会立即结束而不被重试。usage 写入失败先回滚内存状态，测试确认 provider call 数为 0；日限额为 2、transport 上限为 3 时，测试确认只发出 2 次 Tavily 请求并持久化 2。

### 问题 E：本地 read_docs 不应套网络重试

任务单最初把 `read docs`、Web、Embedding 与 LLM 并列为可重试读取。当前 `read_docs` 本身是本地 snapshot/BM25/FAISS 查询，不存在 provider `Retry-After`；其远程语义排序所需 Embedding 已在真正的 provider 边界重试。给整个工具再套一层会重复本地工作，并可能在未来扩大工具职责后误重放副作用。

处理：把 TODO 改成“仅外部幂等 transport 默认可重试”，并明确本地 read path 依靠 snapshot/typed error，不加泛化 wrapper。

## 验证范围

新增故障注入覆盖：

- timeout 两次后恢复、有限 exponential backoff 与 usage 汇总；
- validation、permission、非幂等 write 只调用一次；
- rate limit 的数字秒/HTTP-date `Retry-After`，以及超出上限时不等待；
- 5xx 与 async transport recovery；
- LLM transport 与 empty-response repair 的独立计数；
- primary/backup 与 Embedding SDK retry 显式为 0；
- Embedding transient recovery 和 malformed response 边界；
- Tavily retry 后才降级、DDG retry、每次 attempt quota、额度耗尽与 usage 写失败回滚；
- retry-aware production module allowlist，防止 ToolNode/write path 泛化接入。

| 验证 | 结果 |
|---|---|
| 本批 targeted pytest | 54 passed，2 个既有 LangGraph warning |
| 全量后端 pytest（frontend build 后串行、禁用本机不可写 cache） | 466 passed，3 个既有第三方 deprecation warning |
| 全仓 Ruff | passed |
| 既有 CI mypy gate | passed，13 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，7 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` tracked/history/diff 隔离 | 提交前复核 |

## 保持不变与后续工作

保持不变：Tavily 优先、DuckDuckGo fallback、LLM primary/backup fallback、ToolNode 结构化错误、FAISS generation 发布、所有敏感写入的现有 exactly-once/approval 边界。Redis checkpointer 的启动等待也继续使用独立 lifecycle policy：它每轮需要重建/关闭 context manager 并识别 BusyLoading，属于进程启动 readiness，不与 request transport 的 backoff/budget 混为一层。

后续工作：

- R1 Reflection 仍需把 transport recovery、argument repair 与 task success 分开统计；
- R2 建立真正的 `ExecutionBudget/BudgetUsage`，消费 `RetryUsageObserver` 与模型 token metadata；
- 明确 LLM retry + primary/backup + empty-response repair 的组合成本策略；
- 多 worker 部署时把 Tavily quota 迁移到共享原子 counter；
- 真实 provider fault injection/eval 仍需受控凭据和网络环境，本批测试全部使用 fake provider，不访问外网。

本批没有修改前端源码或样式，因此不重复浏览器视觉 smoke；production static asset contract 已包含在全量 pytest，前端自身的 typecheck、Vitest 与 production build 也全部通过。三条全量 pytest warning 仍来自 LangGraph/Starlette 依赖弃用提示，使用 `-p no:cacheprovider` 后没有本机 `.pytest_cache` 权限 warning。
