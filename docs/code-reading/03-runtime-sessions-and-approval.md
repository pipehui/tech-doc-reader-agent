# 03 - Runtime、会话恢复与两类审批

本章解释 `ChatRuntime` 为什么看起来方法很多却不直接实现业务，以及最容易混淆的两类 pending interrupt：输入 guardrail 审批与 LangGraph 敏感工具审批。

## `ChatRuntime` 是委托门面

位置：[`runtime/chat_runtime.py`](../../tech_doc_agent/app/runtime/chat_runtime.py)。

构造输入：

```python
ChatRuntime(
    settings: Settings,
    lifecycle: RuntimeLifecycle,
    approval_repository: ApprovalRepository,
    execution_identity_factory: RuntimeExecutionIdentityFactory,
    execution_identity: RuntimeExecutionIdentityPort | None = None,
)
```

构造期间创建三个内部协作者：

```text
ApprovalService(repository)
SessionQueryService(graph_provider, config_builder, pending_guardrail_checker)
GraphExecutionService(settings_provider, graph_provider, config_builder,
                      session_queries, approvals)
```

公开方法的实际 owner：

| `ChatRuntime` 方法 | 委托对象 |
|---|---|
| `stream/astream_user_message` | `GraphExecutionService` |
| `stream/astream_approval` | `GraphExecutionService` |
| `has/ahas_pending_interrupt` | `GraphExecutionService` |
| `get/aget_snapshot` | `SessionQueryService` |
| `get_history(_view)` | `SessionQueryService` |
| `get/aget_session_state` | `SessionQueryService` |
| guardrail request/get/has | application `ApprovalService` |
| enter/exit | `RuntimeLifecycle` + repository/Langfuse cleanup |

因此新增一种 session view 应优先放 `sessions.py`；修改 resume 语义应放 `execution.py`；不要继续往 facade 堆私有实现。

## `build_config` 如何保证同一会话不串 tenant

`ChatRuntime.build_config` 每次 new 一个不可变 `SessionConfigFactory`，并附加当前 runtime execution identity。真正的 `build(...)` 做：

1. `parse_tenant(..., prefer_context=True)`；
2. 读取 trace ContextVar；
3. 按需创建 Langfuse callback；
4. 写 metadata；
5. chat/approval 操作创建 request budget window；
6. 把 `tenant_thread_id(session_id, tenant)` 写入 `configurable.thread_id`。

状态查询 operation 默认是 `state`，不带 callbacks，也不启动 request time budget。chat/approval 才用 `with_callbacks=True`。

工具读取 tenant 时优先从 RunnableConfig metadata 取，而不是仅依赖 ContextVar。原因是 LangGraph 可能在线程间执行节点，显式 config 比隐式 context copy 更可靠。

## 同步 graph 如何提供 async API

当前生产图使用同步 `graph.stream`。FastAPI 又需要 async generator，`GraphExecutionService.astream_*` 通过 `_aiter_sync_iterator` 逐个把 `next(iterator)` 放到 `asyncio.to_thread`：

```python
part = await asyncio.to_thread(_next_or_done, iterator)
```

这不是把整个 stream 一次性放进线程等待结束，而是每取一个 part 就回到 event loop，SSE 仍可逐条发送。finally 中如果 iterator 有 `close()`，也在线程中调用。

修改时的坑：

- 不能在 async route 中直接 `for part in graph.stream(...)`，会阻塞 event loop；
- 不能 `list(graph.stream(...))` 再返回，会失去流式和提前中断；
- 若未来改为真正 async graph，应重新评估这个 bridge，而不是在 bridge 外再套线程。

## 发送消息：输入和输出

`_stream_user_message(...) -> Iterator[Any]`：

输入：session、原始 user input、tenant、是否 async runtime、请求开始时刻。

主要步骤：

1. parse tenant；
2. `telemetry.start_chat` 记录 started；
3. 构造 graph input：一条 user tuple + tenant state；
4. build operation=`chat` config；
5. 在 `stream_timer` 中 yield `graph.stream` parts；
6. 异常时 telemetry.error 后原样抛给 SSE 层；
7. 正常 part 耗尽后 `_finish_operation` 查询 pending，记录 finished/interrupted，并按配置 flush Langfuse。

输出是内部 graph part，不是 SSE。Runtime 不 import API/SSE 模块。

注意：若消费方提前关闭 iterator，`_finish_operation` 可能不执行到；这与完整迭代结束不同。不要把关键业务 commit 放在 `_finish_operation`，它只做 telemetry/flush。

## `has_pending_interrupt` 合并两种来源

```python
if approvals.has_pending_guardrail_approval(...):
    return True
snapshot = session_queries.get_snapshot(...)
return bool(snapshot.next)
```

对 UI 而言两者都意味着用户必须先 approve/reject，所以 facade 合并成一个 bool。但内部恢复必须区分来源。

### 类型 A：medium-risk 输入 guardrail

存储：独立 [`RedisApprovalRepository`](../../tech_doc_agent/app/infrastructure/persistence/approval_repository.py)。

记录内容：session、原始 user input、tenant、source、risk level、finding names；有 schema version、pending status、created/expires timestamp 和 Redis TTL。

key：

```text
tech_doc_agent:guardrail_approval:<user>:<namespace>:<session>
```

消费：`GETDEL` 原子取出，确保多 worker/重复点击只有一个请求拿到 pending record。

批准后：把保存的原始输入重新交给 `_stream_user_message`，此时不会再次经过 API guardrail，因为执行发生在 runtime 层；否则同一 medium 输入会无限要求审批。

拒绝后：`guardrail_rejection_part` 构造一个 synthetic `("updates", {"guardrail": ...})`，其中是面向用户的 `AIMessage`，不运行 graph，也不写入 checkpoint history。

### 类型 B：敏感 LangGraph tool interrupt

存储：RedisSaver checkpoint 的 `snapshot.next`。

触发：graph compile 时：

```python
interrupt_before=list(spec.interrupt_nodes)
```

`interrupt_nodes` 包含所有存在 sensitive tools 的子 Agent node，以及 `primary_assistant_sensitive_tools`。图到达 tool node 前暂停，最后一条 AIMessage 已含待执行 tool call。

批准后：`graph.stream(None, config, ...)` 从 checkpoint 当前 next node 继续，真正执行工具。

拒绝后：先用 `graph.update_state` 在被中断节点位置写入 error `ToolMessage`，然后再 `graph.stream(None, ...)`。Agent 能读取“用户拒绝 + feedback”，按原角色继续收束，而不是把 feedback 当新 user turn 交给 primary。

## `_stream_approval` 的精确分支顺序

位置：[`runtime/execution.py`](../../tech_doc_agent/app/runtime/execution.py)。

```text
1. parse tenant + start approval trace
2. pop pending guardrail approval
   2a. found + approved -> _stream_user_message(saved input), return
   2b. found + rejected -> yield synthetic rejection part, return
3. read graph snapshot
   3a. snapshot.next empty -> log no_pending, return
4. build operation=approval config
5. rejected -> graph.update_state(error ToolMessage, as_node=interrupted_node)
6. graph.stream(None, config, ...)
7. normal completion -> finish telemetry
```

guardrail pending 优先于 graph snapshot。正常设计下一个 session 不应同时有两种 pending；即使异常状态同时存在，也会先消费输入审批，下一次再处理 graph interrupt。

## 拒绝敏感 tool 时 ToolMessage 的构造

`_rejection_tool_message(snapshot, feedback)` 从 checkpoint 最后一条 AI message 的第一个 tool call 取 ID，构造：

- `status="error"`；
- content 告诉 Agent 用户拒绝及原因；
- artifact 中放 `PermissionDenied(code="tool_execution_rejected")` 的安全 payload。

修改这里要保持 `tool_call_id` 与原调用一致，否则 LangChain message sequence 会成为孤立 tool result，模型或 graph 可能拒绝该历史。

当前逻辑假设敏感 node 的最后一条 AIMessage 至少有一个 tool call。这个前提由 graph router 保证；若未来允许无 tool call interrupt，必须先补防御逻辑和测试。

## 会话查询如何从 checkpoint 投影

位置：[`runtime/sessions.py`](../../tech_doc_agent/app/runtime/sessions.py)。

`SessionQueryService._read` 一次收集：

- 严格解析后的 tenant；
- pending guardrail bool；
- `graph.get_state(config)` snapshot；
- snapshot.values（非 dict 时降为 `{}`）。

封装成 `_SessionRead`，其 `pending_interrupt = pending_guardrail or bool(snapshot.next)`。

### `get_history`

输出较完整、偏调试：每条 message 含 raw type、role、name、tool_call_id 和 tool_calls。若存在 conversation summary，先投影一条 `role=system, raw_type=conversation_summary`。

### `get_history_view`

输出前端展示视图：

- human -> user message；
- 有文本的 AI -> assistant message；
- tool -> tool_result，可由 `include_tools` 过滤；
- 空 AI 与不支持类型被忽略；
- summary 投影为 system/conversation_summary。

前端 bootstrap 请求 `include_tools=true`，但 `historyToMessages` 目前不会恢复 tool card 关联，只会把 history item 变成普通 message。浏览器自己的 transcript cache 才保留完整 toolCalls/events。这是当前恢复能力的边界。

### `get_session_state`

它不返回所有 checkpoint 字段，只投影 UI 需要的摘要：exists、pending、target、message count、current agent、plan、budget/context/retry。

`current_agent` 优先级：

```text
pending guardrail -> "guardrail"
dialog_state 非空 -> 栈顶
否则 -> "primary"
```

`exists` 不只看 messages；summary、learning target 或 pending 也算已有会话。

## `MessageSerializer` 的边界

位置：[`runtime/serialization.py`](../../tech_doc_agent/app/runtime/serialization.py)。

它只把 LangChain-style message 转成稳定 dict：

- string content 原样；
- list content 只拼 text block；
- 其他 content 返回空文本；
- raw type 映射为 user/assistant/tool/system role。

不要在 API route 里直接 `model_dump()` LangChain message：provider-specific 字段会泄漏进外部 contract，前端也会绑定到第三方内部结构。

## Runtime telemetry 的边界

[`runtime/telemetry.py`](../../tech_doc_agent/app/runtime/telemetry.py) 统一 chat/approval 的 started、finished/interrupted、error 和 no-pending 日志。`OperationTrace` 保存 event prefix、phase、session、tenant、start time 和 completion fields。

这里记录的是一次 runtime operation；graph node、tool、retry 的细粒度日志由其他 tracker 记录。不要在 route、Runtime facade 和 execution service 三处重复记录同一个 started/finished，否则指标会重复计数。

## 修改时最容易踩的坑

### 把两种审批合成一个 repository

敏感 tool 的完整恢复点由 LangGraph checkpoint 管理，包含 next node 和 message history；独立 approval record 只有输入 guardrail 所需数据。强行合并会重复存储 graph resume state，且容易与 checkpoint 不一致。

### 批准 guardrail input 后再次调用 API delivery

会再次检测成 medium 并创建新 pending，形成循环。正确路径是 runtime 直接执行已经人工放行的保存输入。

### 拒绝 sensitive tool 时新建 user message

会让 primary 抢走原子助手的任务，丢失 tool_call_id 对应关系。必须作为 ToolMessage 写回原 interrupt node。

### state view 直接返回 `snapshot.values`

这会把内部 messages、reflection、可能的 provider 对象暴露给 API，并使外部 contract 随 graph state 任意变化。新增前端字段应显式投影并同步 schema/decoder。

### 在 async query 中直接调用同步 Redis graph

`aget_*` 使用 `asyncio.to_thread` 是为了不阻塞 event loop。新增 query 也应保持这一模式，除非底层 graph 提供真正 async API。

## 对应测试

- [`tests/test_chat_runtime_execution.py`](../../tests/test_chat_runtime_execution.py)：facade 委托与 stream 行为；
- [`tests/test_chat_runtime_queries.py`](../../tests/test_chat_runtime_queries.py)：history/state projection；
- [`tests/test_chat_runtime_config.py`](../../tests/test_chat_runtime_config.py)：thread ID、metadata、callback、budget window；
- [`tests/test_runtime_approvals.py`](../../tests/test_runtime_approvals.py)：guardrail/sensitive approval 分支；
- [`tests/test_redis_approval_repository.py`](../../tests/test_redis_approval_repository.py)：schema、TTL、GETDEL、错误映射；
- [`tests/test_approval_models.py`](../../tests/test_approval_models.py)：domain payload 验证；
- [`tests/test_tenant.py`](../../tests/test_tenant.py)：config/context/default 优先级；
- [`tests/test_runtime_lifecycle.py`](../../tests/test_runtime_lifecycle.py)：进入、失败清理和退出顺序。
