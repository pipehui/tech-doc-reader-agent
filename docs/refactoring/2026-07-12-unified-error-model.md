# 统一错误模型与安全错误边界

## 本批目标

此前系统只有 `ToolMessage.status`，但没有稳定的错误分类。工具异常会进入自然语言文本，`search_related_docs`、Tavily 和 DuckDuckGo 还会把任意异常变成空数组；SSE、health 与 telemetry 多处直接发送或记录 `str(exc)`。这会同时造成三类问题：

- Agent、前端和后续 retry/circuit breaker 无法区分 validation、限流、超时与依赖不可用；
- “依赖故障”和“确实没有搜索结果”表现相同；
- provider URL、文件路径、凭据片段等原始异常文本可能穿过 SSE 或日志边界。

本批完成可靠性清单 R0：建立一个与 provider SDK 解耦的错误模型，并让 file、Redis、LLM、embedding、FAISS、Web search、ToolMessage、SSE 和前端共享显式语义。

## 最终错误协议

核心错误都继承 `ApplicationError`，最小分类为：

- `ValidationError`
- `PermissionDenied`
- `RateLimited`
- `Timeout`
- `DependencyUnavailable`
- `Conflict`
- `UnknownDependencyError`

每个错误都能产生固定形状的安全载荷：

```json
{
  "status": "error",
  "code": "dependency_timeout",
  "retryable": true,
  "safe_message": "A dependency timed out. Try again.",
  "dependency": "embedding",
  "tool": "search_related_docs",
  "cause_type": "APITimeoutError"
}
```

原始 exception message 不进入该载荷。`cause_type` 只保留异常类型；`dependency` 表示失败边界；`tool` 仅在工具链存在时填写。HTTP status、标准异常类型及 SDK exception class name 的映射集中在 `core/errors.py`，各适配器只负责补充 dependency/tool 上下文。

Python 的 `ERROR_DETAIL_FIELDS` 与 TypeScript 的同名常量共同声明 `code/retryable/safe_message/dependency/cause_type`，contract test 只解析对应常量范围并校验集合相等。

## 实际改动

### 1. Provider 与 repository 边界不再伪装空结果

- atomic JSON read/write 将 JSON、序列化、权限和 OS 错误映射为 file repository 错误，仍保留临时文件清理和旧文件不被半写覆盖的原子性；
- embedding 在缺配置、非法输入和 provider 调用失败时返回 typed error；
- FAISS add/search/save/load 分别标记 embedding、vector index 或 file repository 边界；
- Redis approval repository 的 connect/get/set/getdel/close 映射 Redis transport error；checkpointer 最终失败也映射为 Redis dependency error；
- Assistant 只在真实 runnable invoke/ainvoke 失败时映射 LLM transport error，空响应重试耗尽仍保持独立业务错误；
- Tavily 与 DuckDuckGo 单 provider 失败显式抛错，Tavily 失败仍可降级到 DuckDuckGo；两者都失败时汇总为 `web_search_unavailable`；
- Web search usage JSON 在加载时验证完整 schema，避免错误值延迟到 quota 比较时才以 `TypeError/KeyError` 失败；
- `search_related_docs` 和 user-profile memory load 删除 broad catch + empty list。

纯向量检索不允许静默降级；hybrid 检索则只捕获已经类型化的 `ApplicationError`，记录安全字段后降级到 exact/BM25。未知编程错误继续抛出，不被“可用性降级”掩盖。

### 2. ToolMessage 成为结构化错误载体

Tool fallback 现在构造真实 `ToolMessage(status="error")`：

- `content` 是安全 JSON，供 Agent 根据 code/retryable 决策；
- `artifact.error` 保存相同的结构化 dict，供 SSE translator 使用；
- raw exception text 不再拼进 prompt；
- repeated-call、parser budget 和用户拒绝也分别带稳定 code，而不是只有自然语言。

Tool dependency 映射集中在 tool node 边界，避免每个工具重复生成消息协议。

### 3. SSE 与前端只消费协议字段

- stream-level `error` 固定发送 `status/code/retryable/message/safe_message/dependency/cause_type/session_id`；
- `tool_result` 除既有 status/error 外，发送公共错误细节字段；
- translator 优先读取 `ToolMessage.artifact.error`，对旧的无 artifact error message 使用保守兼容值；
- TypeScript reducer 将错误元数据保存到 `ToolCall`，不解析 content；
- ToolCallCard 单独显示安全 error metadata，原始 result 仍作为 result 展示；
- success event 不残留旧 error metadata，缺少新字段的滚动部署 payload 仍可处理。

### 4. 日志与 health 不再输出 exception message

node timing、runtime operation、tool call、Langfuse lifecycle、approval repository close、Redis readiness、FAISS seed fallback 和 SSE error 均改用 `safe_error_fields`。现有源码中只剩 Redis BusyLoading retry predicate 在进程内读取 `str(exc)` 做兼容判断；它不写日志、不进 SSE，也不返回客户端。

## 实施中遇到的问题

### 问题 A：ToolNode 默认吞错，外层 fallback 实际没有运行

原实现把 `ToolNode` 再包一层 `with_fallbacks(handle_tool_error)`，看起来异常会到统一 handler。真实执行和日志却显示 `ToolNode` 默认 `handle_tool_errors=True`：它自己捕获工具异常并返回 ToolMessage，外层 runnable 被视为成功。因此旧测试虽然看到了 error ToolMessage，却没有证明自定义 fallback 被调用，`artifact` 也无法存在。

处理：显式构造 `ToolNode(..., handle_tool_errors=False)`，让异常到达唯一的应用错误边界；真实 exploding tool 测试同时断言 status、code、cause_type、artifact 和 raw text 不泄露。

### 问题 B：错误日志辅助函数覆盖了原始异常

关闭内置吞错后的首轮测试得到 `validation_error/TypeError`，而不是预期的 `RuntimeError`。原因不是分类器，而是 `_log_tool_calls` 已固定传 `tool=`，`safe_error_fields` 又包含同名字段，Python 在调用 `log_event` 前抛出 duplicate keyword `TypeError`，掩盖了真实工具异常。

处理：tool name 仍由逐调用 logger 负责；传入公共 error fields 前移除重复 `tool` 字段。回归测试随后看到真实 `RuntimeError`，同时 node/tool telemetry 都只记录安全字段。

### 问题 C：测试读取了 `.env` 的 placeholder embedding 配置

缺配置测试最初使用 `Settings()`，本地 `.env` 中的占位 key/model 被 Pydantic 自动加载，测试意外发起真实 OpenAI 请求并得到 401。这不是生产逻辑失败，而是测试没有隔离配置源。

处理：fixture 显式传空 `EMBEDDING_API_KEY/EMBEDDING_MODEL`，provider failure 测试则注入 fake client。所有测试都不再访问网络。

### 问题 D：SSE contract test 扫描整个 TypeScript 文件

旧测试用“独占一行的字符串”正则提取 event names。新增 `ERROR_DETAIL_FIELDS` 后，五个字段被误识别为 SSE event，导致全量 pytest 唯一失败。

处理：测试先定位 `SSE_EVENT_TYPES = [...]` 声明，再只解析该数组；status 和 error fields 采用同样的常量范围解析，消除文件布局耦合。

### 问题 E：降级与吞错不能用同一条规则

完全删除 semantic catch 会让一次 embedding outage 破坏本可由 BM25 完成的 hybrid 查询；保留原 catch 又会让纯 vector 查询把故障伪装成零命中。

处理：将 `degrade_on_failure` 明确传入 ranker。vector mode 为 false 并传播 typed error；hybrid mode 为 true，但只降级已类型化的 dependency error。降级是调用方策略，不再是 adapter 的隐式行为。

## 验证范围

新增/加强的失败样例覆盖：

- 七类核心错误的 code/retryable/safe payload 和 raw secret 非泄露；
- atomic JSON serialization/replace/corrupt JSON；
- embedding 缺配置、限流与单条/批量 shape；
- semantic typed degrade 与 vector propagate；
- Tavily -> DuckDuckGo fallback、单 provider 错误、双 provider outage、usage schema；
- 真实 ToolNode exception 到自定义 fallback；
- policy/rejection artifact；
- Redis repository transport error 与 readiness payload；
- LLM sync invoke transport mapping；
- tool_result 和 stream error 的结构化 SSE；
- TypeScript reducer、ToolCallCard 和 Python/TypeScript contract parity。

| 验证 | 结果 |
|---|---|
| 全量后端 pytest | 284 passed，4 个既有第三方/本机 cache warning |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，11 source files |
| 本批扩展 direct mypy（`--follow-imports=skip`） | passed，39 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| in-app browser production preview | Landing/Studio/Inspector/Learner rendered；console warning/error 0 |
| `git diff --check` | passed |
| `docs/todo` tracked/history/diff 隔离 | 三项均为空 |

浏览器 smoke 只验证 production frontend 路由与渲染，没有启动或修改后端数据；Tool error metadata 的实际 DOM 展示由 React component test 覆盖。预览 tab 与 4173 进程均已清理。第三方 LangGraph/Starlette warning 与本机 `.pytest_cache` 权限 warning 仍是既有环境提示，不属于本批回归。

## 保持不变与后续工作

保持不变：SSE event names、工具 success content、Tavily 优先/日配额、DuckDuckGo fallback、hybrid exact/BM25 降级、前端 pending/done/error 三态。

后续 R1 才实现按 `retryable` 驱动的有限 transport retry；本批只建立可靠前置，不偷偷增加重试或写操作重放。R4 仍需独立 telemetry redactor 来处理业务字段中的 token/邮箱/手机号；本批保证 exception message 不再进入这些出口，但不宣称所有业务 telemetry 已完成脱敏。
