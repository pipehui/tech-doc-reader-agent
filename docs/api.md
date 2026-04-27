# API 参考

默认开发地址：`http://localhost:8000`。

## REST 接口

### POST /chat

发送用户消息并返回 SSE 事件流。

请求体：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID，同时也是 LangGraph `thread_id` |
| `message` | `string` | 是 | 用户输入 |

响应：`text/event-stream`。首帧总是 `session_snapshot`，随后可能出现 `token`、`agent_message`、`agent_transition`、`plan_update`、`tool_call`、`tool_result`，最后以 `done`、`interrupt_required` 或 `error` 结束。

### POST /chat/approve

继续或拒绝一个 pending interrupt。

请求体：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `approved` | `boolean` | 是 | 是否批准敏感工具调用 |
| `feedback` | `string` | 否 | 拒绝原因，默认空字符串 |

响应：`text/event-stream`。首帧总是 `session_snapshot`。如果当前没有 pending interrupt，会返回 `no_pending_interrupt` 后结束。

### GET /sessions/{id}/history

读取用于前端展示的会话历史。

查询参数：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `include_tools` | `boolean` | 否 | 是否包含 tool result，默认 `false` |

响应字段：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `learning_target` | `string \| null` | 是 | 当前学习目标 |
| `pending_interrupt` | `boolean` | 是 | 是否等待用户批准 |
| `message_count` | `number` | 是 | 返回的消息条数 |
| `messages` | `HistoryViewItem[]` | 是 | 展示消息数组 |

`HistoryViewItem`：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `id` | `string \| null` | 否 | 消息 ID |
| `role` | `string` | 是 | `user`、`assistant` 或 `tool` |
| `kind` | `string` | 是 | `message` 或 `tool_result` |
| `content` | `string` | 是 | 文本内容 |
| `name` | `string \| null` | 否 | assistant/tool 名称 |
| `tool_call_id` | `string \| null` | 否 | tool call ID |

### GET /sessions/{id}/state

读取会话状态快照。

响应字段：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `exists` | `boolean` | 是 | 是否已有消息、学习目标或 pending interrupt |
| `pending_interrupt` | `boolean` | 是 | 是否等待用户批准 |
| `learning_target` | `string \| null` | 是 | 当前学习目标 |
| `message_count` | `number` | 是 | 状态中的消息数量 |
| `current_agent` | `string \| null` | 是 | 当前 agent，默认 `primary` |
| `workflow_plan` | `string[]` | 是 | 当前工作流计划 |
| `plan_index` | `number` | 是 | 当前执行到的计划下标 |

### GET /learning/overview

读取全局学习记录和聚合统计，不经过 LangGraph。

响应字段：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `total` | `number` | 是 | 总记录条数 |
| `average_score` | `number` | 是 | 平均掌握度，没有记录时为 `0` |
| `needs_review_count` | `number` | 是 | `score < 0.6` 或超过 14 天未复习的记录数 |
| `records` | `LearningRecord[]` | 是 | 学习记录数组 |

`LearningRecord`：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `knowledge` | `string` | 是 | 知识点名称 |
| `timestamp` | `string` | 是 | ISO 时间字符串 |
| `score` | `number` | 是 | 掌握度评分 |
| `reviewtimes` | `number` | 是 | 复习次数 |

### GET /learning/records

读取原始学习记录数组。响应类型为 `LearningRecord[]`。

## SSE 事件

### session_snapshot

每次 `/chat` 或 `/chat/approve` 的第一帧，表示处理本次请求之前的 baseline。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `exists` | `boolean` | 是 | 当前 session 是否已有状态 |
| `current_agent` | `string \| null` | 是 | 当前 agent |
| `learning_target` | `string \| null` | 是 | 当前学习目标 |
| `workflow_plan` | `string[]` | 是 | 当前计划 |
| `plan_index` | `number` | 是 | 当前计划下标 |
| `pending_interrupt` | `boolean` | 是 | 是否等待批准 |
| `message_count` | `number` | 是 | 状态中的消息数量 |

### token

LLM 流式输出片段。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `text` | `string` | 是 | 增量文本 |
| `agent` | `string \| null` | 是 | 推断出的 agent 名称 |

### agent_message

一个完整 AI message 写入状态时发送。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `agent` | `string` | 是 | message name 或节点名 |
| `node` | `string` | 是 | LangGraph 节点名 |
| `message_id` | `string \| null` | 否 | 消息 ID |
| `content` | `string` | 是 | 文本内容 |

### agent_transition

进入、完成或离开一个业务 agent 时发送。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `phase` | `"enter" \| "finish" \| "leave"` | 是 | 切换阶段 |
| `agent` | `string` | 是 | `parser`、`relation`、`explanation`、`examination` 或 `summary` |
| `from` | `string` | 否 | 预留字段，当前后端不发送 |

### plan_update

计划首次写入或计划下标推进时发送。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `plan` | `string[]` | 否 | 新工作流计划，通常只在 `store_plan` 后出现 |
| `plan_index` | `number` | 否 | 当前计划下标 |
| `learning_target` | `string` | 否 | 学习目标，通常只在 `store_plan` 后出现 |

### tool_call

AI message 中包含 tool call 时发送。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `agent` | `string` | 是 | 发起工具调用的 agent |
| `node` | `string` | 是 | LangGraph 节点名 |
| `tool` | `string \| null` | 是 | 工具名 |
| `args` | `object` | 是 | 工具参数 |
| `tool_call_id` | `string \| null` | 是 | tool call ID |

### tool_result

工具结果写入状态时发送。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `agent` | `string` | 是 | 节点名 |
| `node` | `string` | 是 | 节点名 |
| `tool` | `string \| null` | 否 | 工具名 |
| `tool_call_id` | `string \| null` | 否 | tool call ID |
| `content` | `string` | 是 | 工具返回内容 |

### interrupt_required

请求结束时发现有 pending interrupt。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `pending` | `boolean` | 是 | 固定为 `true` |

### no_pending_interrupt

调用 `/chat/approve` 但当前没有 pending interrupt。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |

### done

一次流式请求正常结束。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |

### error

流式处理出现异常。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `message` | `string` | 是 | 错误信息 |
| `session_id` | `string` | 是 | 会话 ID |

## 状态恢复约定

前端进入页面时推荐先并行请求：

1. `GET /learning/overview` 渲染知识库、复习队列和聚合统计。
2. `GET /sessions/{id}/history` 恢复聊天记录。
3. `GET /sessions/{id}/state` 恢复当前 agent、计划和 interrupt 状态。

用户发消息时调用 `POST /chat`。收到第一帧 `session_snapshot` 后用它作为本次流的 baseline；收到 `plan_update` 后合并更新计划字段；收到 `agent_transition` 后更新当前 agent；收到 `token` 时追加到正在流式输出的 assistant 文本；收到 `done` 或 `interrupt_required` 后结束本次流。
