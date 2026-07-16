# 09 - 前端状态、SSE 归约与会话恢复

本章从用户在 Studio 点“发送”开始，追踪一条消息如何成为聊天文本、工具卡片、Inspector event 和 Learner 数据。前端最重要的边界是：

```text
网络协议解析 != 事件业务归约 != Zustand 状态修改 != React 展示
```

这四层分开后，SSE contract 能做纯函数测试，组件也不需要理解原始 wire data。

## 应用入口与共享状态

入口：

```text
frontend/src/main.tsx
  -> <BrowserRouter><App /></BrowserRouter>
  -> App.tsx
  -> app/AppRouter.tsx
```

[`app/AppRouter.tsx`](../../frontend/src/app/AppRouter.tsx) 定义四个页面：

| 路由 | 组件 | 主要读取的状态 |
| --- | --- | --- |
| `/` | `Landing` | 入口/导航，不 bootstrap 会话 |
| `/studio` | `Studio` | messages、tool calls、session、sessions |
| `/inspector` | `Inspector` | events、filters、selected event、运行状态 |
| `/learner` | `Learner` | learning overview、计划、会话消息 |

非 Landing 页面都会调用 `useSessionBootstrap(true)`。三个工作页面没有各自复制一套请求状态，而是订阅同一个 Zustand store，所以一次 SSE 更新能同时反映到 Studio 与 Inspector。

## Store 是五个 slice 的组合

位置：[`frontend/src/store.ts`](../../frontend/src/store.ts)。

`createAppStore(dependencies)` 组合：

| Slice | 源文件 | 拥有的数据/动作 |
| --- | --- | --- |
| session | [`store/sessionSlice.ts`](../../frontend/src/store/sessionSlice.ts) | 当前 session/tenant、最近会话、切换与重置 |
| transcript | [`store/transcriptSlice.ts`](../../frontend/src/store/transcriptSlice.ts) | messages、toolCalls、流式消息、浏览器 transcript |
| trace | [`store/traceSlice.ts`](../../frontend/src/store/traceSlice.ts) | Inspector events、筛选、录制/暂停/选择 |
| learning | [`store/learningSlice.ts`](../../frontend/src/store/learningSlice.ts) | learning overview 与 Learner UI 开关 |
| UI | [`store/uiSlice.ts`](../../frontend/src/store/uiSlice.ts) | running、label、error、theme、工具展开状态 |

`createAppStore` 可注入 storage、三个 repository、ID/session ID/时钟函数和 storage failure handler。生产环境末尾创建 `useAppStore = createAppStore()`；测试则传 fake storage 和固定时钟。不要在 slice 内重新直接读 `window.localStorage` 或 `Date.now()`，否则会绕开这层可测试边界。

## 点击发送后的完整调用链

React 使用的 hook 在 [`frontend/src/useChatStream.ts`](../../frontend/src/useChatStream.ts)。模块加载时它把 concrete dependencies 注入 [`streaming/chatStream.ts`](../../frontend/src/streaming/chatStream.ts) 的 `createChatStream`：

```text
fetchEventSource
API_BASE / tenantHeaders
useAppStore.getState
refreshSessionContext
uid / current time
```

`send(message)` 的步骤：

1. `trim`，空文本直接返回；
2. 若 `store.running` 或 `session.pending_interrupt`，拒绝重复发送；
3. `addUserMessage(text)`，立即显示并保存本地 transcript；
4. `rememberSession(session_id, tenant)`；
5. `run("/chat", {session_id, message}, "生成中")`。

`run` 在发请求前固定以下上下文：

```python
sessionId   = 当前 session.session_id
tenant      = sessionTenant(session)
responseId  = createId()
reducerState = createStreamReducerState(
    responseId,
    当前 agent,
    当前 toolCalls
)
```

同一次后端响应的多个 Agent 消息和工具卡片共享 `responseId`，它是前端关联键，不是后端 LangChain message ID。

随后：

```text
setRunning(true)
  -> POST /chat，header 和 body 都带 tenant
  -> onopen 校验 HTTP status 与 text/event-stream
  -> 每个 onmessage: parse -> reduce -> dispatch
  -> stream 正常结束: finishResponse -> refresh session + learning
  -> 异常: finishResponse -> setError
  -> finally: setRunning(false)
```

`openWhenHidden: true` 表示页面切到后台也继续接收流。浏览器隐藏不等于用户取消请求。

### 为什么先校验 response content type

高风险 guardrail 会返回普通 JSON 4xx，而不是 SSE。`onopen` 先用 `streamErrorFromResponse` 解析 JSON/text，并把 guardrail findings 变成用户可读错误。如果后端 200 却错误返回 JSON，也会抛“非 SSE 响应”，避免把整份 JSON 当 token。

## SSE 处理被拆成四层

```text
fetch-event-source MessageEvent
  -> sseEnvelope.parseSseMessage
  -> ssePayloads.decodeSsePayload
  -> sseReducer.reduceSseMessage
  -> storeAdapter.dispatchStreamActions
  -> Zustand slice methods
```

### 第一层：wire data 解析

位置：[`streaming/sseEnvelope.ts`](../../frontend/src/streaming/sseEnvelope.ts)。

`parseSseData(raw)`：

- 空 data -> `{}`；
- 正常 JSON -> object/`{value: primitive}`；
- JSON 解出来仍是以 `{`/`[` 开头的字符串 -> 再解一次，兼容历史双编码；
- 非 JSON -> `{raw, message: raw}`。

`parseSseMessage(event, data)` 返回 discriminated union：

```typescript
{ kind: "event", envelope }
{ kind: "unknown", event, data }
{ kind: "invalid", event, data, error }
```

未知事件和已知事件的坏 payload 必须区分。未知事件可用于前后端滚动升级时向前兼容；已知事件字段坏了说明 contract 破裂，不能假装没事。

### 第二层：运行时 payload 校验

位置：[`streaming/ssePayloads.ts`](../../frontend/src/streaming/ssePayloads.ts)。

TypeScript interface 在运行时会消失，因此 `decodeSsePayload` 对每种事件检查 required string、literal、object、array 和 nullable 字段。公共 context 中出现的 `trace_id/session_id` 必须是字符串，`user_id/namespace` 必须是字符串或 null。

例如 `tool_result` 不只校验 `content`，还检查：

```text
status: success | error
error/safe_message/code/dependency/cause_type: string | null
retryable: boolean | null
```

`session_snapshot` 复用 shared API 的 `decodeSessionState`，避免 REST 与 SSE 对同一个 state 维护两套宽松规则。

### 第三层：纯 reducer

位置：[`streaming/sseReducer.ts`](../../frontend/src/streaming/sseReducer.ts)。

`reduceSseMessage(state, parsed, {now, createId})` 不直接调用 Zustand。它返回：

```typescript
{
  state: 下一份 StreamReducerState,
  actions: StreamAction[]
}
```

`StreamReducerState` 只保存本次流需要的短期关联信息：responseId、activeAgent、各 Agent token meta 和 toolCalls snapshot。纯 reducer 可用事件序列直接做单元测试，不需要 React/jsdom/store mock。

### 第四层：action adapter

位置：[`streaming/storeAdapter.ts`](../../frontend/src/streaming/storeAdapter.ts)。

`dispatchStreamActions` 把 action 翻译成 store method：record event、set session、update streaming message、add/update tool call。`stream_error` 在这里抛异常，统一进入 `chatStream.run` 的 catch；protocol warning 只在开发模式打印。

如果 reducer 直接 `useAppStore.setState`，它会同时负责协议语义和副作用，事件序列测试就很难定位错误发生在哪一层。

## 每种 SSE 事件如何改变前端

后端事件全集由 [`frontend/src/sseContract.ts`](../../frontend/src/sseContract.ts) 定义，并与后端 contract 测试同步。

| 事件 | reducer 主要动作 | 用户看到的影响 |
| --- | --- | --- |
| `token` | 追加对应 Agent 的流式文本；只更新 stream meta | Studio 文本逐字增长，不进 Inspector |
| `session_snapshot` | record + 合并 session state | 计划、agent、审批、预算等恢复 |
| `agent_message` | record + 用 final content 收口消息 + current agent | 流式草稿被后端最终消息覆盖 |
| `agent_transition` | record；enter 时更新 current agent | Agent 状态/颜色切换 |
| `plan_update` | record + 更新 plan/index/target | workflow/Learner 计划变化 |
| `structured_result` | record | Inspector 可查看解析/关联结构化结果 |
| `usage_update` | record + budget usage | 预算统计更新 |
| `budget_started` | record + active/status/usage | 请求预算开始 |
| `budget_terminated` | record + termination/status | 展示终止状态，后续路由结束 |
| `context_metrics_update` | record + context metrics | Inspector/状态显示压缩指标 |
| `provider_retry_update` | record + retry usage | provider 重试统计 |
| `tool_call` | record + 建 pending tool card | Studio 出现工具参数；PlanWorkflow 同步计划 |
| `tool_result` | record + 按 tool_call_id 完成/报错卡片 | 工具结果与安全错误 metadata |
| `interrupt_required` | record + pending=true | ApprovalDrawer 打开 |
| `guardrail_blocked` | record + stream error | 系统错误消息 |
| `no_pending_interrupt` | record + pending=false | 关闭审批状态 |
| `done` | record + pending=false | 正常收尾 |
| `error` | record + stream error | 结束流并添加安全错误消息 |

### token 为什么不进入 Inspector

每个 token 一个 event 会迅速淹没事件列表和 localStorage。`TraceSlice.recordEvent` 明确忽略 `token`，transcript hydrate 也再次过滤历史 token；流式统计通过 reducer 的 meta 汇总后附到 `agent_message` event。

Inspector 最多保留最近 3000 个事件。`seq` 当前按内存数组长度加一；达到上限后继续添加时并不是全局永久递增 ID，所以不要把它当后端 trace 序号。

### `agent_message` 为什么会覆盖 token 文本

[`store/transcriptSlice.ts`](../../frontend/src/store/transcriptSlice.ts) 的 `updateStreamingMessage`：

```typescript
content = finalContent ?? current.content + text
```

token 用追加；`agent_message` 传 `finalContent`，用后端最终 AIMessage 内容覆盖同一 responseId + agent 的草稿。这样 provider 的 token 拼接差异、空 chunk 或后处理不会让最终显示与 checkpoint 中消息不一致。

同一个 response 中若多个 Agent 发言，会各有一条 assistant message，因为查找键还包含 normalized agent。

## 工具调用如何挂到聊天消息

`tool_call` 事件优先使用后端 `tool_call_id`；缺失时才生成本地 ID。reducer 在自己的 `toolCalls` map 中存 pending 对象，并发出：

```text
add_tool_call(toolCall, responseId)
```

TranscriptSlice 会找到或新建该 `responseId + agent` 的 assistant message，把 ID 加入 `message.toolCallIds`，同时把完整对象放进全局 `toolCalls[id]`。

`tool_result` 用同一 ID 查 existing call，保留原 agent/args/createdAt，写 result、status、updatedAt 和 typed error metadata。即使先收到 result 或缺 ID，也能构造降级卡片，但正常后端 contract 应保证 call/result ID 对应。

因此工具卡片不是通过解析 assistant 文本生成的。改 tool SSE 字段时要同步 reducer、ToolCall type、卡片组件和 transcript version。

### `PlanWorkflow` 的特殊处理

`PlanWorkflow` 虽然也是 tool call，但前端会读取 `args.steps` 并立即更新 `workflow_plan`、`plan_index=0` 和可选 learning target。后续正式 `plan_update/session_snapshot` 仍能覆盖它。这个乐观显示逻辑依赖工具名和参数 schema，重命名工具时必须同步。

## 正常完成、错误和刷新

流 transport resolve 后：

1. `finishResponse(responseId)` 把相关 assistant messages 的 `streaming=false`；
2. 删除既没文本也没 tool card 的空 assistant placeholder；
3. 保存 transcript；
4. `refreshSessionContext` 并行拉 session state 与 learning overview。

刷新**不重新拉 history**。当前消息和工具卡片已经由 SSE 构建；若完成后再用后端 history 覆盖，可能丢失前端保存的 tool association 和 Inspector events。

异常路径也先 `finishResponse`，随后 `setError(message)`。UI slice 的 `setError` 同时：

- running=false；
- run label 回“就绪”；
- 保存 error 字符串；
- `addSystemMessage(message)`，所以错误会在对话中持久可见。

`refreshSessionContext` 自己捕获错误并添加“状态刷新失败”系统消息；这不会把已经成功接收的对话流标成失败。

## 审批恢复如何工作

[`features/approval/ApprovalDrawer.tsx`](../../frontend/src/features/approval/ApprovalDrawer.tsx) 只由 `session.pending_interrupt` 决定显示。它从 toolCalls 中找最新的 pending 工具：

- 找到：显示“某 Agent 请求执行某工具”；
- 找不到：显示通用“后端正在等待批准”，适用于 guardrail input approval 或页面刷新后只有 session state、没有本地 tool card 的情况。

点击批准/拒绝调用：

```text
approve(approved, feedback)
  -> run("/chat/approve", {session_id, approved, feedback})
  -> 复用完全相同的 SSE parse/reduce/dispatch 链
```

拒绝反馈只在拒绝按钮传递；批准按钮不发送 textarea 内容。审批的后端两种来源详见 [03 - Runtime、会话与审批](03-runtime-sessions-and-approval.md)。

## 首次进入/切换会话的恢复链

位置：

- [`features/session/useSessionBootstrap.ts`](../../frontend/src/features/session/useSessionBootstrap.ts)
- [`features/session/sessionBootstrap.ts`](../../frontend/src/features/session/sessionBootstrap.ts)

URL 是可分享的当前上下文入口：

```text
?session=<id>&user_id=<id>&namespace=<namespace>
```

`useSessionBootstrap` 有三段 effect：

1. URL 指向不同上下文时，`resetForContext(urlSession, urlTenant)` 清空当前 messages/events/tools；
2. URL 缺字段时，用 store context 补齐并 `navigate(..., replace=true)`；一致时记住最近 session；
3. URL/store 一致后，启动实际 load；cleanup 时 abort。

React StrictMode 开发环境可能让 effect setup/cleanup 多执行一次，因此 load 边界必须可取消且不能把过期结果写入新会话。

### `loadSessionContext`

```text
hydrateTranscript(session, tenant)
  -> Promise.all(
       GET session state,
       GET history?include_tools=true,
       GET learning overview
     )
  -> 再检查 signal.aborted
  -> setSessionState
  -> 没有有效 cached transcript 时才 setMessages(historyToMessages)
  -> setLearning
```

先 hydrate local transcript 是为了立刻恢复工具卡片和 Inspector events。后端 history 是事实来源，但当前 API 映射为普通 messages，`historyToMessages` 给每条消息的 `toolCallIds=[]`，无法完整重建之前的卡片/trace。因此有有效本地 transcript 时不覆盖它。

这也意味着浏览器 localStorage 丢失后，聊天文本可从后端恢复，但工具卡片和 Inspector 细节可能不完整。这不是 Redis 数据丢失，而是两种持久化 contract 的粒度不同。

即使某些 HTTP adapter 不遵守 AbortSignal，Promise.all resolve 后还会再次检查 `signal.aborted`，避免用户已经切到 session B 时，session A 的慢响应覆盖 store。

## 浏览器持久化键与版本

### Session repository

位置：[`storage/sessionRepository.ts`](../../frontend/src/storage/sessionRepository.ts)。

它保存当前 `{session_id, user_id, namespace}` 和最多 32 个最近 session。读取不到新版 context 时，会用旧 `tech-doc-agent.session` ID 加默认 tenant 恢复；保存时仍兼容写 legacy session ID。

session identity 实际是三元组，不是只有 session ID。删除或去重时都同时比较 tenant。

### Transcript repository

位置：[`storage/transcriptRepository.ts`](../../frontend/src/storage/transcriptRepository.ts)。

key：

```text
tech-doc-agent.react.transcript.<tenantKey>::<sessionId>
```

snapshot：

```typescript
{
  version: 2,
  messages,
  events,
  toolCalls
}
```

版本不等于当前值或 JSON 损坏时返回 null，不尝试猜结构。新增/改变持久字段时要决定是兼容读取还是提升 `TRANSCRIPT_VERSION`；只改 TypeScript interface 不会迁移用户已有 localStorage。

storage 读写失败不会让核心对话崩溃；repository 返回 false 并交给 failure handler，开发环境打印 warning。localStorage 是体验缓存，不是服务端事务的一部分。

## Session reset 的边界

`resetForContext` 会重置：

```text
session, messages, events, toolCalls,
selectedEventId, hasNewMessageContent
```

它不会清空 learning、theme 或全局 preferences。learning 会随后由 bootstrap 用新 tenant overview 覆盖；在网络返回前可能短暂显示旧值，若产品不允许这种闪烁，应在 reset 时显式重置 learning/loading 状态。

`newSession()` 保留当前 tenant，只生成新 session ID。`deleteSession` 同时删除该 tenant+session 的本地 transcript；若删的是当前 context，它先选同 tenant 的最近会话，没有时会选任意 tenant 的最近剩余会话，只有完全没有剩余项时才为原 tenant 生成新 ID。它当前不调用后端删除 checkpoint。若产品要求“删除后绝不跨 tenant”，需要修改这个 fallback，而不能只改界面文案。

## 增加一个新 SSE 事件时要改哪里

按 contract 顺序操作：

1. 后端 payload model/event emitter；
2. [`frontend/src/sseContract.ts`](../../frontend/src/sseContract.ts) 的事件名；
3. `SsePayloadMap` 与运行时 decoder；
4. reducer 分支与 `assertNever`；
5. 如需副作用，新增 `StreamAction` 和 store adapter；
6. 对应 slice/type/组件；
7. backend/frontend contract tests；
8. reducer 单测、stream integration test；
9. 若进入 transcript，评估版本和 Inspector filter。

不要只把事件名加进 union 来消除 TypeScript 报错。没有 decoder 时坏数据进入 store；没有 reducer 分支时 exhaustive check 应阻止构建。

## 修改时容易踩的坑

### 在 `onmessage` 里直接 setState

这样会绕过 payload 验证、纯 reducer 和测试。新行为应该先表示成 typed action，再由 adapter 调 slice。

### 把 unknown event 当 fatal

前后端滚动部署时，新后端可能先发送旧前端不认识但非关键的事件。当前策略是开发 warning 并忽略；已知事件 payload 无效才产生 stream error。若某事件是协议关键路径，应通过版本协商处理，而不是笼统杀死所有 unknown。

### 用 `agent_message` 继续 append

它是最终内容，不是增量。append 会把已经收到的 tokens 重复一遍。

### 用 session ID 作为唯一 local key

不同 tenant 可能使用同名 session。必须保留 tenantKey，否则会话、tool card 和消息互相串数据。

### 页面刷新后认为 pending tool 一定在本地

后端 checkpoint 可能显示 pending interrupt，但浏览器 transcript 已清空/版本失效。ApprovalDrawer 的 generic fallback 必须保留；真正批准哪个 tool 由后端 checkpoint 决定。

### 每个 token 都持久化

当前 `updateStreamingMessage` 不在每个 token 后 `persistTranscript`。user/system message、Inspector event 和 `finishResponse` 等边界会保存；tool action 本身不单独保存，通常由相邻 event 或最终 finish 一并落盘。逐 token 写 localStorage 会严重放大同步 I/O。若要提高崩溃中恢复粒度，应做节流，并明确 tool mutation 后的持久化顺序。

### Bootstrap 覆盖缓存 transcript

后端 history 目前不能重建 toolCallIds/events。只有没有有效 cache 时才 `setMessages(history)`。如果以后 API 能返回完整 transcript，应先升级 response contract 和映射，再改变优先级。

## 建议跟着跑的前端测试

在 `frontend/`：

```powershell
npm run test
npm run build
```

重点文件：

- [`streaming/ssePayloads.test.ts`](../../frontend/src/streaming/ssePayloads.test.ts)：每个 payload 的运行时 contract；
- [`streaming/sseReducer.test.ts`](../../frontend/src/streaming/sseReducer.test.ts)：事件到 action 的纯归约；
- [`streaming/storeAdapter.test.ts`](../../frontend/src/streaming/storeAdapter.test.ts)：action 副作用适配；
- [`streaming/chatStream.integration.test.ts`](../../frontend/src/streaming/chatStream.integration.test.ts)：HTTP/SSE/错误/刷新完整链；
- [`features/session/sessionBootstrap.test.ts`](../../frontend/src/features/session/sessionBootstrap.test.ts)：cache、并行 load、abort；
- [`features/session/useSessionBootstrap.test.ts`](../../frontend/src/features/session/useSessionBootstrap.test.ts)：URL/store 路由同步；
- [`storage/sessionRepository.test.ts`](../../frontend/src/storage/sessionRepository.test.ts) 与 [`storePersistence.test.ts`](../../frontend/src/storePersistence.test.ts)：tenant key、版本和恢复；
- [`store/slices.test.ts`](../../frontend/src/store/slices.test.ts)：message/tool/event/session 状态转换。

前后端共同 contract 还要运行根目录的 [`tests/test_sse_contract.py`](../../tests/test_sse_contract.py)。它能防止后端已经新增/删字段而前端列表仍停在旧版本。

下一章 [10 - 横切机制](10-cross-cutting-policies.md) 会集中解释 settings、tenant、错误、重试、预算、观测和上下文压缩如何穿过上述所有层。
