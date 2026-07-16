# 02 - Chat API 与 SSE 输出链

本章从 `POST /chat` 开始，追到浏览器收到的 UTF-8 SSE bytes。重点是三种输入风险分支、LangGraph part 到 SSE event 的多对多翻译，以及修改协议时必须同步的后端/前端事实源。

## Chat route 只做六件事

位置：[`api/routes/chat.py`](../../tech_doc_agent/app/api/routes/chat.py)。

`chat(body: ChatRequest, request: Request)`：

1. 记录 `request_started_monotonic`；
2. 从 `app.state` 取 `ChatRuntime`；
3. body trace ID > header > 新 ID；
4. body tenant > header tenant；
5. 将字段展开传给 `chat_delivery.chat_response`；
6. 直接返回 delivery 生成的 `Response`。

`approve` 同理，只把 `approved` 和 `feedback` 交给 `approval_response`。history/state route 则调用 runtime 的 async query 方法并用 Pydantic response model 收口。

route 不拥有 guardrail、SSE generator 或 graph resume 逻辑。这样 HTTP endpoint 的签名变化与 streaming workflow 的变化能分别测试。

## 请求模型

位置：[`api/schemas.py`](../../tech_doc_agent/app/api/schemas.py)。

| 模型 | 必填输入 | 关键限制 |
|---|---|---|
| `ChatRequest` | `session_id`, `message` | session 最长 128；message 1..8000；tenant/trace 使用受限字符集 |
| `ApproveRequest` | `session_id`, `approved` | feedback 最长 2000；相同 tenant/trace 规则 |

Pydantic 校验失败由 FastAPI 返回 422，调用不到 `chat_response`。tenant 可能来自 header，所以 `resolve_request_tenant` 还会再严格 parse；非法 tenant 不会被换成 `default`。

## `chat_response` 的三条分支

位置：[`api/chat_delivery.py`](../../tech_doc_agent/app/api/chat_delivery.py)。

函数签名明确要求 route 已经解析好的字段：

```python
chat_response(
    runtime,
    *,
    session_id: str,
    message: str,
    trace_id: str,
    user_id: str,
    namespace: str,
    request_started_monotonic: float,
) -> Response
```

它先建立 `trace_context(operation="chat")`，再调用一次：

```python
risk = evaluate_input_guardrail(message, source="chat.message")
```

### high risk：同步 JSON 400

`_guardrail_blocked_response` 返回：

```json
{
  "error": "guardrail_blocked",
  "message": "Input was blocked by prompt-injection guardrails.",
  "session_id": "...",
  "source": "chat.message",
  "risk_level": "high",
  "findings": ["..."],
  "trace_id": "...",
  "user_id": "...",
  "namespace": "..."
}
```

它不是 SSE。前端 `chatStream.onopen` 会发现 non-2xx，读取 JSON，并显示“输入被安全策略拦截”。

### medium risk：写 pending record，再开短 SSE

`_request_guardrail_approval` 调 runtime 把完整原始输入写进 approval repository。随后 `_astream_guardrail_approval_events` 只产生：

1. 当前 `session_snapshot`；
2. `interrupt_required`，并带 `approval_kind="guardrail_input"`、source、risk、findings。

这里不会调用 `graph.stream`。用户批准后，Runtime 才取出 pending input 并按普通聊天执行。

### none / low：正常聊天 SSE

`_astream_chat_events`：

1. 先 `await runtime.aget_session_state(...)`；
2. yield `session_snapshot`；
3. 获取 `runtime.astream_user_message(...)`；
4. 交给 `astream_parts_as_sse` 翻译；
5. 最终产生 `done` 或 `interrupt_required` / `error`。

先发 snapshot 的目的是让前端在新 token 前先对齐当前 checkpoint，包括恢复后已有的 pending/plan/budget 状态。

## `approval_response` 与 feedback guardrail

只有非空 feedback 才再次做输入 guardrail：

- high feedback：直接 JSON 400；
- medium feedback：当前实现只记录 warning，继续交给 approval stream；
- 无 feedback：直接处理审批。

原因是 feedback 可能被写回 ToolMessage 供 Agent 读取，因此也属于模型输入。注意这里并没有为 medium feedback 再套一层审批，否则会形成“审批反馈还要再审批”的循环。

`_astream_approval_events` 先发 snapshot，然后调用 `ahas_pending_interrupt`：

- 没有 pending：发 `no_pending_interrupt` 并结束；
- 有 pending：调用 `runtime.astream_approval`，再走同一个 SSE translator。

## 为什么 trace context 要包住每一次迭代

`StreamingResponse` 返回后，真正迭代 async generator 发生在 route 函数退出以后。如果只在 `chat_response` 外层使用 context manager，生成后续 token 时 ContextVar 已恢复。

所以 `_stream_response` 使用：

```python
aiter_with_trace_context(events, trace_id, session_id, operation, ...)
```

[`api/sse/context.py`](../../tech_doc_agent/app/api/sse/context.py) 在每次 `await anext(iterator)` 时重新进入 `trace_context`。这样由后续事件创建、日志和错误都能带上同一 trace/session/tenant。

## LangGraph part 的两种形状

`api/sse/parts.py` 兼容两种 envelope：

```python
{"type": "messages", "data": (...)}
("messages", (...))
```

同理 updates 既可能是 v2 dict，也可能是二元 tuple/list。解析失败不会猜测业务含义，而是返回 `None`/空 dict，并记录 `sse.translation.ignored`。

### messages part -> `token`

`events_from_stream_part` 只接受 `msg_chunk.type == "AIMessageChunk"`。`extract_text_from_chunk` 支持：

- content 是字符串；
- content 是 list，item 为 `{"type":"text","text":"..."}` 或含 `text` 字段。

非 AI chunk、空文本或畸形 part 被忽略并记录原因。agent 从 LangGraph metadata 的 node/agent 信息推断。

### updates part -> 多种事件

`iter_update_events` 对每个 `node_name -> node_update` 按固定顺序处理：

1. node name 是否代表 `enter_` / `finish_` / `leave_` transition；
2. plan update；
3. parser/relation structured result；
4. usage update；
5. budget terminated；
6. budget started；
7. context metrics；
8. provider retry usage；
9. AI message、tool calls、tool results。

因此一个 `finish_parser` update 当前会依次产生 `agent_transition`、`plan_update` 和 `structured_result`；parser assistant 节点自己的 update 才可能产生 `agent_message` 或 `tool_call`。前端不能假设一个 graph node 只对应一个 event，也不能把相邻两个 node 的事件合并成同一次 update。

## SSE 事件表

后端事件名的唯一集合在 [`api/sse/contract.py`](../../tech_doc_agent/app/api/sse/contract.py)，payload model 在 [`api/sse/payloads.py`](../../tech_doc_agent/app/api/sse/payloads.py)。

| Event | 产生位置/条件 | 前端主要用途 |
|---|---|---|
| `session_snapshot` | 每个 chat/approval stream 开头 | 覆盖 session state |
| `token` | AIMessageChunk 有文本 | 增量拼接 assistant message |
| `agent_message` | node update 含非空 AIMessage | 用最终内容校正流式消息 |
| `agent_transition` | node 名为 enter/finish/leave + 已知角色 | current agent、Inspector |
| `plan_update` | `store_plan` 或 `finish_*` 更新 plan 字段 | plan stepper |
| `structured_result` | parser/relation result 是 dict | Inspector / 后续扩展 |
| `tool_call` | AIMessage 含 tool_calls | 创建 tool card |
| `tool_result` | update 含 ToolMessage | 完成/报错 tool card |
| `usage_update` | 有合法 `budget_usage_delta` + 累计 usage | 更新预算显示 |
| `budget_started` | request-start node 写 active | 初始化预算状态 |
| `budget_terminated` | terminal node 写 terminated | 显示安全停止原因 |
| `context_metrics_update` | reset/assistant delta | Inspector/context state |
| `provider_retry_update` | reset/operations delta | 重试观测 |
| `interrupt_required` | graph/guardrail 尚有 pending | 阻塞 composer，打开审批 |
| `no_pending_interrupt` | approve 时已无 pending | 清 pending |
| `done` | part 耗尽且无 pending | 结束本轮 |
| `error` | 迭代 part 期间抛错 | 安全错误展示 |
| `guardrail_blocked` | 有 model，但当前 chat high 走 JSON 分支 | 保留的协议能力 |

## payload 在后端就会强校验

`sse_event(event, data)`：

1. 从 trace context 补齐缺少的 trace/session/user/namespace；
2. 调 `validate_sse_payload`；
3. Pydantic `extra="forbid"` 验证对应 model；
4. 返回 `ServerSentEvent`。

这意味着 translator 多传一个未声明字段也会在后端失败，不会悄悄泄漏到浏览器。`plan_update` 还有额外 validator，至少必须包含 plan、plan_index、learning_target 之一。

## tool error 如何变成安全 payload

`_tool_result_payload` 首先看 `ToolMessage.status`。error 时优先读取 `message.artifact["error"]`：

```text
code, retryable, safe_message, dependency, cause_type
```

如果没有结构化 artifact，content 会被替换成固定 `Tool execution failed.`，而不是把任意异常文本透传给前端。成功结果才正常保留 content。

这条安全边界不能通过“为了调试直接把 `str(exc)` 放到 SSE”来绕开。详细异常只应进受脱敏的 server log。

## 最终终止事件如何决定

`astream_parts_as_sse` 正常遍历完 parts 后再次查询 runtime：

- 仍有 pending interrupt -> `interrupt_required`，然后 return；
- 没有 pending -> `done`。

异常不会让 HTTP 流突然无说明断开，而是：

1. `classify_error`；
2. 记录 `sse.stream.error`；
3. yield 带安全字段的 `error` event。

但 HTTP status 通常已经是 200，因为 SSE headers 已发出；前端必须处理协议内 `error`，不能只看 `response.ok`。

## 编码到 wire format

[`api/sse/encoder.py`](../../tech_doc_agent/app/api/sse/encoder.py) 把 `ServerSentEvent` 编成：

```text
event: token
data: {"text":"...","agent":"primary",...}

```

多行字段逐行加前缀；JSON 使用 `ensure_ascii=False`，最后编码 UTF-8。`event_source_response` 设置：

- `Content-Type: text/event-stream`；
- `Cache-Control: no-cache`；
- `X-Accel-Buffering: no`。

最后一个 header 对 Nginx 等代理很重要，否则代理可能缓冲到请求结束才一次性发给浏览器。

## 修改 SSE 事件时的同步清单

新增 event 至少要改：

1. 后端 `api/sse/contract.py::SseEventName`；
2. 后端 `api/sse/payloads.py` 新 model + `SSE_PAYLOAD_MODELS`；
3. translator/streaming 的产生逻辑；
4. 前端 `sseContract.ts::SSE_EVENT_TYPES`；
5. 前端 `streaming/ssePayloads.ts::SsePayloadMap` + decoder；
6. 前端 `streaming/sseReducer.ts` switch；
7. store/UI（若 event 改变展示状态）；
8. 后端/前端协议测试和 `tests/test_sse_contract.py` 跨语言一致性测试；
9. [`docs/api.md`](../api.md) 和本章事件表。

只改数组而不加 decoder，前端会把事件判为 known 但 invalid；只改后端，前端会记录 unknown event 并忽略业务状态。

## 对应测试

- [`tests/test_api_schemas.py`](../../tests/test_api_schemas.py)：请求/响应字段边界；
- [`tests/test_guardrails.py`](../../tests/test_guardrails.py)：risk 检测和 disposition；
- [`tests/test_sse_events.py`](../../tests/test_sse_events.py)：context 注入与 event 构造；
- [`tests/test_sse_payloads.py`](../../tests/test_sse_payloads.py)：Pydantic payload contract；
- [`tests/test_sse_contract.py`](../../tests/test_sse_contract.py)：后端与前端事件/错误字段一致；
- [`frontend/src/streaming/ssePayloads.test.ts`](../../frontend/src/streaming/ssePayloads.test.ts)：浏览器侧 decoder；
- [`frontend/src/streaming/sseReducer.test.ts`](../../frontend/src/streaming/sseReducer.test.ts)：每种 event 的 action；
- [`frontend/src/streaming/chatStream.integration.test.ts`](../../frontend/src/streaming/chatStream.integration.test.ts)：HTTP/SSE/refresh 的整体行为。
