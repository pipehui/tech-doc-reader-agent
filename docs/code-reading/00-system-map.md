# 00 - 系统地图与一条请求的全貌

## 先用一句话理解项目

浏览器把用户消息发给 FastAPI；Runtime 把消息和 tenant 信息交给一个可恢复的 LangGraph；图中的 primary 选择直接回答或安排专职 Agent，Agent 通过注入的工具访问文档、学习状态和网页；LangGraph 产出的 message/update part 被翻译成带类型校验的 SSE，前端 reducer 再把事件分别写进会话状态、聊天记录、工具卡和 Inspector 时间线。

这句话里的每个动词都由不同文件负责，后面章节就是把这些动词逐一展开。

## 两条真正的主链

### 后端请求链

```text
POST /chat
  -> api/routes/chat.py::chat
  -> api/chat_delivery.py::chat_response
  -> runtime/chat_runtime.py::ChatRuntime.astream_user_message
  -> runtime/execution.py::GraphExecutionService.astream_user_message
  -> graph.stream(..., stream_mode=["messages", "updates"], version="v2")
  -> api/sse/streaming.py::events_from_stream_part
  -> api/sse/events.py::sse_event
  -> api/sse/encoder.py::event_source_response
```

前四步准备执行，`graph.stream` 才真正推进节点；后四步不改变业务状态，只把 graph 输出投影为外部协议。

### 前端消费链

```text
useChatStream().send(message)
  -> streaming/chatStream.ts::run
  -> fetchEventSource
  -> streaming/sseEnvelope.ts::parseSseMessage
  -> streaming/ssePayloads.ts::decodeSsePayload
  -> streaming/sseReducer.ts::reduceSseMessage / reduceSseEvent
  -> streaming/storeAdapter.ts::dispatchStreamActions
  -> store/*Slice.ts
  -> features/chat | approval | inspector | learner
```

解析、校验、决策和写 store 被刻意分开。这样协议解析可以做纯函数测试，reducer 不需要浏览器或 Zustand，store adapter 也不需要理解每种 payload 的业务含义。

## 目录所有权：看到问题先去哪里

| 目录 | 它拥有的具体事实 | 它不应该做的事 |
|---|---|---|
| `api/` | HTTP 路径、Pydantic 请求/响应、guardrail 的 HTTP/SSE 投影、SSE wire contract | 构造 Redis/FAISS/模型；决定 graph 拓扑 |
| `runtime/` | graph 的启动/关闭、config、send/resume、checkpoint 查询、API view 投影 | import 具体 RedisSaver、FAISS、Agent registry |
| `graph/` | state 字段如何流转、节点、边、路由、tool policy、预算/反思/压缩挂载点 | 创建真实模型、存储或 HTTP response |
| `agents/` | 六个角色、prompt 文件、prompt hash、模型绑定、角色可用工具集合 | 读取具体 repository；处理 HTTP/SSE |
| `tools/` | LangChain tool schema；把 tool 参数转换为 port/use-case 调用 | 自己打开 JSON 文件或创建全局 resource |
| `application/` | command、domain model、port、用例、事务边界、纯策略 | 依赖 FastAPI、LangGraph、FAISS、Redis 实现 |
| `infrastructure/` | Redis/JSON/FAISS/embedding/web-search 的具体实现和资源聚合 | 反向调用 route、graph 或 Agent |
| `core/` | 无业务适配器依赖的基础状态、错误、tenant、settings、预算模型、日志与脱敏 | import 其他 app layer |
| `composition.py` | 把 resources -> tools -> assistants -> GraphSpec 串起来 | 实现 repository 或业务规则 |
| `bootstrap.py` | 生产环境选择哪些具体 adapter，并创建 `ChatRuntime` | 承担请求执行逻辑 |
| `services/` | 旧 import 的兼容转发 | 放新实现；被 production 主链依赖 |

源码入口：[`tech_doc_agent/app`](../../tech_doc_agent/app)。这些方向由 [`tests/test_architecture_dependencies.py`](../../tests/test_architecture_dependencies.py) 的 AST import graph 门禁自动检查，不只是文档约定。

## 一条普通消息到底发生什么

假设请求是：

```json
{
  "session_id": "demo-1",
  "message": "帮我理解 LangGraph checkpoint",
  "user_id": "alice",
  "namespace": "tech_docs"
}
```

### 1. API 收口外部输入

[`ChatRequest`](../../tech_doc_agent/app/api/schemas.py) 校验长度与字符格式。`routes/chat.py::resolve_trace_id` 按 body、`x-trace-id` header、新 ID 的顺序选择 trace。`resolve_request_tenant` 按 body 优先、header 其次选择 tenant，非法值返回 422，不做静默修正。

### 2. delivery 只评估一次输入风险

`chat_delivery.py::chat_response` 调用 `evaluate_input_guardrail(message, source="chat.message")`：

- `high`：直接 `JSONResponse(400)`，不打开 SSE，不运行 graph；
- `medium`：把原始消息写入 Redis pending approval，SSE 只发 `session_snapshot` 和 `interrupt_required`；
- `none/low`：进入 `_astream_chat_events`。

### 3. Runtime 创建 tenant-scoped graph config

`GraphExecutionService._stream_user_message` 把输入变为：

```python
{
    "messages": [("user", user_input)],
    "user_id": tenant.user_id,
    "namespace": tenant.namespace,
}
```

`SessionConfigFactory.build` 生成的关键配置是：

```python
{
    "configurable": {
        "thread_id": "alice:tech_docs:demo-1",
    },
    "metadata": {
        "session_id": "demo-1",
        "user_id": "alice",
        "namespace": "tech_docs",
        "runtime_operation": "chat",
        # trace、identity、request budget metadata...
    },
    "run_name": "tech_doc_agent.chat",
    "recursion_limit": settings.LANGGRAPH_RECURSION_LIMIT,
}
```

`thread_id` 是 Redis checkpoint 的隔离键。只传相同 `session_id`、但 `user_id` 或 `namespace` 不同，会读到不同会话。

### 4. 图在每个请求起点重置“本次请求账本”

图的固定前两站是：

```text
START -> fetch_user_info -> compact_context
```

`fetch_user_info` 外面叠了三个 wrapper。按源码的嵌套结构从外到内、也就是实际进入时的执行顺序：

1. `provider_retry_usage_request_start_node` 重置 provider retry ledger；
2. `budgeted_request_start_node` 创建 budget usage；
3. `context_metrics_request_start_node` 重置本请求 context metrics；
4. 最后才调用真正的 `user_info_node`。

节点本体 `create_user_info_node(...).user_info` 读取 profile + memory，写入 `user_info`，并重置 reflection request state。

### 5. primary 决定直接回答还是调用控制工具

primary 的模型输出仍是一条 `AIMessage`。差别在于它可能：

- 有文字、没有 tool call：路由到 `END`；
- 调 `PlanWorkflow`：进入 `store_plan`，把 steps 和 `learning_target` 写进 state；
- 调 `ToDocParserAssistant` 等 handoff：进入相应 `enter_*`；
- 调普通 safe tool：进入 `primary_assistant_tools`；
- 调敏感 tool：在 `primary_assistant_sensitive_tools` 之前中断。

具体分支由 `graph/routing.py::make_primary_router` 判断，不是由 FastAPI route 判断。

### 6. 子 Agent 只看到受控任务视图

parser/relation/explanation/examination 默认先经过 `graph/message_scope.py::build_scoped_messages`。它们收到一条新构造的 `HumanMessage`，内容包括当前 query、学习目标、plan、handoff args、允许看到的上游 structured result，以及自己本轮已有的 tool history；不会直接看到 primary 的全部原始消息或其他 Agent 的原始 tool result。

summary 例外：它的 `scoped_messages=False`，使用完整近期消息，并在存在 conversation summary 时把摘要作为 system message 放在前面。

### 7. graph 产生两类 part

Runtime 请求：

```python
graph.stream(
    graph_input,
    config,
    stream_mode=["messages", "updates"],
    version="v2",
)
```

- `messages` part：模型流式 `AIMessageChunk`，主要转成 `token`；
- `updates` part：节点完成后的 state delta，转成 transition、plan、tool、structured result、usage 等事件。

一个 `updates` part 可能投影出多个 SSE event；SSE event 不是 graph part 的一一映射。

### 8. 前端把事件分流到四种状态

- `token` / `agent_message` -> transcript message；
- `tool_call` / `tool_result` -> `toolCalls`，同时挂到对应 assistant message；
- `session_snapshot` / plan / budget / context / retry -> `session`；
- 除 `token` 外的协议事件 -> Inspector `events`。

流结束后 `chatStream.run` 还会并行请求 session state 和 learning overview，以 checkpoint 的最终值校正流中增量状态。

## 四类状态不要混淆

| 状态 | 存在哪里 | 生命周期 | 例子 |
|---|---|---|---|
| LangGraph checkpoint state | RedisSaver | 跨请求、按 tenant+session 恢复 | messages、plan、structured result、budget |
| Guardrail pending approval | Redis 独立 key | TTL 内一次性 `GETDEL` | medium-risk 原始输入 |
| 业务长期状态 | generation JSON / profile JSON / FAISS snapshot | 跨会话 | learning record、memory、profile、docs |
| 浏览器展示状态 | Zustand + localStorage transcript | 当前浏览器 | 消息卡、Inspector events、主题、最近 session |

敏感 tool interrupt 属于 checkpoint 的 `snapshot.next`；medium-risk input approval 属于独立 approval repository。两者在 UI 上都表现为 `pending_interrupt=true`，但恢复路径不同，详见 [03](03-runtime-sessions-and-approval.md)。

## 为什么重构后文件变多却更容易安全修改

拆分依据不是“每个文件尽量短”，而是变化原因不同：

- HTTP schema 变化不应迫使 graph 节点变化；
- graph 路由变化不应迫使 Redis repository 变化；
- tool schema 变化与业务事务变化要能分别测试；
- SSE wire contract 必须与内部 LangGraph part 解耦；
- 前端协议 reducer 必须能在没有 React、网络和 localStorage 的情况下测试。

代价是第一次阅读时跳转较多。解决方法不是重新合并文件，而是沿调用链读，并用 [11 - 修改手册](11-change-recipes.md) 做联动清单。

## 最容易形成的五个错误理解

1. **`ChatRuntime` 会构造所有资源。** 实际构造发生在 `bootstrap.py` 和 `RuntimeLifecycle`；`ChatRuntime` 只持有并委托。
2. **SSE token 就是最终 assistant message。** token 用于即时显示，最终 `agent_message` 来自 node update，可覆盖同一 response/agent 的累计 token 内容。
3. **所有审批都从 Redis approval record 恢复。** 只有 medium-risk input 如此；敏感工具由 LangGraph checkpoint interrupt 恢复。
4. **`read_docs` 只查向量。** 默认是 exact + BM25 + semantic，再做 RRF；`search_related_docs` 才显式用 vector mode。
5. **浏览器 localStorage 是会话事实源。** 它只是显示缓存；后端 checkpoint 的 state/history 才是恢复事实源。
