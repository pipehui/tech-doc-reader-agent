# API 参考

默认开发地址：`http://localhost:8000`。

## 多租户约定

`user_id` 和 `namespace` 共同定义当前租户。未传时默认使用 `default` / `tech_docs`，以兼容现有本地知识库。

- `/chat`、`/chat/approve`：请求体可传 `user_id`、`namespace`，也可用 `x-user-id`、`x-namespace` header。
- `/sessions/{id}/history`、`/sessions/{id}/state`、`/learning/*`：可用 query param 或 `x-user-id`、`x-namespace` header。
- LangGraph checkpointer 的实际 `thread_id` 为 `user_id:namespace:session_id`。
- 会话状态和学习记录会按当前 `user_id + namespace` 隔离。
- 文档库是共享知识库，不按租户隔离；所有租户都能读取同一批本地技术资料。

## REST 接口

### GET /health

进程存活探针。只要 FastAPI 进程能够处理请求，就返回：

```json
{"status": "ok"}
```

### GET /ready

运行依赖就绪探针。用于 Docker healthcheck 或部署平台 readiness probe。

检查项包括：

| 检查项 | 说明 |
|---|---|
| `runtime` | `ChatRuntime` 是否已挂载到 app state |
| `graph` | LangGraph graph 是否已构建 |
| `checkpointer` | Redis checkpointer 是否已初始化 |
| `resources` | 应用资源容器是否存在 |
| `faiss_store` | 本地文档 store 是否已初始化 |
| `hybrid_retriever` | Hybrid RAG 检索器是否已初始化 |
| `learning_store` | 学习记录 store 是否已初始化 |
| `memory_store` | 长期学习轨迹 memory store 是否已初始化 |
| `web_search_backend` | Web search backend 是否已初始化 |
| `redis` | Redis 是否可 ping |

全部通过时返回 `200`：

```json
{
  "status": "ready",
  "checks": [
    {"name": "runtime", "ok": true},
    {"name": "redis", "ok": true}
  ]
}
```

任一检查失败时返回 `503`，并在 `checks` 中包含失败原因。

### POST /chat

发送用户消息并返回 SSE 事件流。

请求体：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `message` | `string` | 是 | 用户输入 |
| `trace_id` | `string` | 否 | 外部 trace ID，不传则后端生成 |
| `user_id` | `string` | 否 | 用户 ID，默认 `default` |
| `namespace` | `string` | 否 | 会话/学习记录命名空间，默认 `tech_docs` |

响应：`text/event-stream`。首帧总是 `session_snapshot`，随后可能出现 `token`、`agent_message`、`agent_transition`、`plan_update`、`tool_call`、`tool_result`，最后以 `done`、`interrupt_required` 或 `error` 结束。

### POST /chat/approve

继续或拒绝一个 pending interrupt。

请求体：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `approved` | `boolean` | 是 | 是否批准敏感工具调用 |
| `feedback` | `string` | 否 | 拒绝原因，默认空字符串 |
| `trace_id` | `string` | 否 | 外部 trace ID，不传则后端生成 |
| `user_id` | `string` | 否 | 用户 ID，默认 `default` |
| `namespace` | `string` | 否 | 命名空间，默认 `tech_docs` |

响应：`text/event-stream`。首帧总是 `session_snapshot`。如果当前没有 pending interrupt，会返回 `no_pending_interrupt` 后结束。

### GET /sessions/{id}/history

读取用于前端展示的会话历史。

查询参数：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `include_tools` | `boolean` | 否 | 是否包含 tool result，默认 `false` |
| `user_id` | `string` | 否 | 用户 ID，默认 `default` |
| `namespace` | `string` | 否 | 命名空间，默认 `tech_docs` |

响应字段：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `user_id` | `string \| null` | 否 | 用户 ID |
| `namespace` | `string \| null` | 否 | 命名空间 |
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

查询参数：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `user_id` | `string` | 否 | 用户 ID，默认 `default` |
| `namespace` | `string` | 否 | 命名空间，默认 `tech_docs` |

响应字段：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `user_id` | `string \| null` | 否 | 用户 ID |
| `namespace` | `string \| null` | 否 | 命名空间 |
| `exists` | `boolean` | 是 | 是否已有消息、学习目标或 pending interrupt |
| `pending_interrupt` | `boolean` | 是 | 是否等待用户批准 |
| `learning_target` | `string \| null` | 是 | 当前学习目标 |
| `message_count` | `number` | 是 | 状态中的消息数量 |
| `current_agent` | `string \| null` | 是 | 当前 agent，默认 `primary` |
| `workflow_plan` | `string[]` | 是 | 当前工作流计划 |
| `plan_index` | `number` | 是 | 当前执行到的计划下标 |

### GET /learning/overview

读取当前租户的学习记录和聚合统计，不经过 LangGraph。

查询参数：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `user_id` | `string` | 否 | 用户 ID，默认 `default` |
| `namespace` | `string` | 否 | 命名空间，默认 `tech_docs` |

响应字段：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `user_id` | `string \| null` | 否 | 用户 ID |
| `namespace` | `string \| null` | 否 | 命名空间 |
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
| `user_id` | `string \| null` | 否 | 用户 ID |
| `namespace` | `string \| null` | 否 | 命名空间 |

### GET /learning/records

读取原始学习记录数组。响应类型为 `LearningRecord[]`。

### GET /learning/memory

读取当前租户的长期学习轨迹记忆，不经过 LangGraph。它记录的是学习过程观察，例如卡点、误解、已掌握内容或复习提示；它不是稳定用户偏好，也不会自动修改用户画像。

查询参数：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `user_id` | `string` | 否 | 用户 ID，默认 `default` |
| `namespace` | `string` | 否 | 命名空间，默认 `tech_docs` |
| `query` | `string` | 否 | 按主题或内容过滤，默认返回最近记忆 |
| `limit` | `number` | 否 | 最大返回数量，默认 `20` |

响应字段：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `user_id` | `string \| null` | 否 | 用户 ID |
| `namespace` | `string \| null` | 否 | 命名空间 |
| `total` | `number` | 是 | 返回 memory 数量 |
| `memories` | `MemoryRecord[]` | 是 | 学习轨迹记忆数组 |

`MemoryRecord`：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `id` | `string` | 是 | memory ID |
| `kind` | `string` | 是 | `learned`、`stuck_point`、`misconception` 或 `review_hint` |
| `topic` | `string` | 是 | 相关主题 |
| `content` | `string` | 是 | 具体学习轨迹观察 |
| `confidence` | `number` | 是 | 观察置信度，范围 `0-1` |
| `source_session_id` | `string \| null` | 否 | 来源会话 ID |
| `created_at` | `string` | 是 | 创建时间 |
| `updated_at` | `string` | 是 | 更新时间 |

### GET /learning/profile

读取当前用户的长期用户画像，不经过 LangGraph。画像记录的是稳定偏好和能力信息，例如经验水平、解释风格、解释深度、熟悉主题和薄弱主题。

画像不会由 summary 自动更新。只有当用户在对话中明确要求更新能力、偏好或用户画像时，primary 才会调用敏感工具 `update_user_profile`，并等待用户审批。

查询参数：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `user_id` | `string` | 否 | 用户 ID，默认 `default` |
| `namespace` | `string` | 否 | 命名空间，默认 `tech_docs` |

响应字段：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `user_id` | `string \| null` | 否 | 用户 ID |
| `namespace` | `string \| null` | 否 | 当前命名空间 |
| `experience_level` | `string` | 是 | 经验水平 |
| `explanation_style` | `string` | 是 | 解释风格 |
| `depth` | `string` | 是 | 解释深度 |
| `language` | `string` | 是 | 语言偏好 |
| `known_topics` | `string[]` | 是 | 已掌握或熟悉主题 |
| `weak_topics` | `string[]` | 是 | 仍需巩固主题 |
| `notes` | `string` | 是 | 其他画像备注 |
| `last_update_reason` | `string \| null` | 否 | 最近一次更新依据 |
| `updated_at` | `string \| null` | 否 | 最近更新时间 |

## SSE 事件

所有 SSE payload 都会自动带上当前 `trace_id`、`session_id`、`user_id` 和 `namespace`。

### session_snapshot

每次 `/chat` 或 `/chat/approve` 的第一帧，表示处理本次请求之前的 baseline。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `user_id` | `string \| null` | 否 | 用户 ID |
| `namespace` | `string \| null` | 否 | 命名空间 |
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
