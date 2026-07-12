# API 参考

默认开发地址：`http://localhost:8000`。

## 多租户约定

`user_id` 和 `namespace` 共同定义当前租户。未传时默认使用 `default` / `tech_docs`，以兼容现有本地知识库。

- `/chat`、`/chat/approve`：请求体可传 `user_id`、`namespace`，也可用 `x-user-id`、`x-namespace` header。
- `/sessions/{id}/history`、`/sessions/{id}/state`、`/learning/*`：可用 query param 或 `x-user-id`、`x-namespace` header。
- body/query 显式值优先于 header；只有字段完全未提供时才使用默认值。显式空值、非法字符、路径分隔符或非字符串不会回退 default，而是返回 `422 invalid_tenant`。
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

### GET /runtime/identity

返回当前部署的 versioned execution identity manifest，供受信 eval/运维流程把结果绑定到实际 prompt 与 model route。默认关闭；仅当：

```dotenv
RUNTIME_IDENTITY_ENDPOINT_ENABLED=true
```

时可访问。当前项目尚未接入管理员鉴权，因此生产环境只应在受信内网或受保护网关后启用。关闭时返回 `404`。

响应只包含：

- schema version 与整体 SHA-256 fingerprint；
- deployment commit identity 的 `configured/unavailable` 状态与可选完整 Git SHA；
- 六个 Assistant 的 role、prompt ID、prompt SHA-256；
- configured provider、primary model ID 与可选 backup model ID。

它不会返回 prompt 正文、API key、provider base URL 或其他 secret。`backup_model_id` 表示 fallback 已配置，不表示本次请求实际使用了 backup；实际模型仍以 provider response usage metadata 为准。

```json
{
  "schema_version": 2,
  "fingerprint": "<sha256>",
  "deployment": {
    "status": "configured",
    "commit_sha": "<full-lowercase-git-sha>"
  },
  "assistants": [
    {
      "assistant_role": "primary",
      "prompt_id": "tech-doc-reader.primary.v1",
      "prompt_sha256": "<sha256>",
      "model_provider_id": "openai_compatible",
      "primary_model_id": "configured-model"
    }
  ]
}
```

部署流程应显式设置完整的 `DEPLOYMENT_COMMIT_SHA`。Docker build 也可通过 compose build arg 把它写入镜像的 `IMAGE_COMMIT_SHA`；运行时显式值为空时回退到镜像值，两者同时存在时必须相同。两者都为空时 endpoint 仍可返回，但 `deployment.status=unavailable`，受信 online eval 的 `--require-runtime-identity` 与 baseline compatibility gate 会拒绝把它当作已验证目标。服务端不会读取本地 `.git` 猜测 deployment commit。

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

响应：`text/event-stream`。首帧总是 `session_snapshot`，随后可能出现 `token`、`agent_message`、`agent_transition`、`plan_update`、`structured_result`、`usage_update`、`budget_started`、`budget_terminated`、`context_metrics_update`、`provider_retry_update`、`tool_call`、`tool_result`，最后以 `done`、`interrupt_required` 或 `error` 结束。

如果输入命中 high-risk prompt-injection 规则，会在进入 LangGraph 前返回 `400`，响应体包含 `error=guardrail_blocked`、`risk_level` 和 `findings`，不会触发任何 agent 或工具调用。medium-risk 输入会返回 `interrupt_required`，并等待 `/chat/approve` 显式批准；批准后才继续执行原始用户消息。pending guardrail request 保存在 Redis，并按 `GUARDRAIL_APPROVAL_TTL_SECONDS` 自动过期（默认 900 秒）；过期后该 guardrail approval 不再可用，若同一 session 也没有 graph interrupt，审批接口会返回无 pending interrupt。

### POST /chat/approve

继续或拒绝一个 pending interrupt。

请求体：

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `approved` | `boolean` | 是 | 是否批准敏感工具调用或 medium-risk 输入 |
| `feedback` | `string` | 否 | 拒绝原因，默认空字符串 |
| `trace_id` | `string` | 否 | 外部 trace ID，不传则后端生成 |
| `user_id` | `string` | 否 | 用户 ID，默认 `default` |
| `namespace` | `string` | 否 | 命名空间，默认 `tech_docs` |

响应：`text/event-stream`。首帧总是 `session_snapshot`。如果当前没有 pending interrupt，会返回 `no_pending_interrupt` 后结束。对于 medium-risk 输入审批，`approved=true` 会继续执行原始用户消息，`approved=false` 会停止执行并返回 guardrail 说明。

`feedback` 同样会经过输入侧 guardrails。命中 high-risk 规则时返回 `400 guardrail_blocked`，拒绝理由不会写回 LangGraph checkpoint。

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
| `role` | `string` | 是 | `user`、`assistant`、`system` 或 `tool` |
| `kind` | `string` | 是 | `message`、`tool_result` 或 `conversation_summary` |
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
| `exists` | `boolean` | 是 | 是否已有消息、压缩摘要、学习目标或 pending interrupt |
| `pending_interrupt` | `boolean` | 是 | 是否等待用户批准 |
| `learning_target` | `string \| null` | 是 | 当前学习目标 |
| `message_count` | `number` | 是 | 状态中的消息与压缩摘要数量 |
| `current_agent` | `string \| null` | 是 | 当前 agent；guardrail 审批 pending 时为 `guardrail`，否则默认 `primary` |
| `workflow_plan` | `string[]` | 是 | 当前工作流计划 |
| `plan_index` | `number` | 是 | 当前执行到的计划下标 |
| `budget_usage` | `object \| null` | 否 | 当前 workflow 的版本化 LLM/tool usage 累计账本 |
| `budget_status` | `"active" \| "terminating" \| "terminated" \| null` | 否 | 当前执行预算生命周期状态 |
| `budget_termination` | `object \| null` | 否 | 命中硬预算或 deadline 时的结构化终止原因 |
| `context_metrics` | `object \| null` | 否 | 当前 workflow 的 checkpoint/prompt/provider input context 累计指标 |
| `provider_retry_usage` | `object \| null` | 否 | 当前 request/workflow 内 embedding、web provider transport operation 的版本化累计账本 |

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
| `user_id` | `string \| null` | 否 | 用户 ID |
| `namespace` | `string \| null` | 否 | 命名空间 |
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
| `profile_version` | `number` | 是 | 当前画像 schema 版本 |
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
| `exists` | `boolean` | 是 | 当前 session 是否已有消息、压缩摘要、学习目标或 interrupt |
| `current_agent` | `string \| null` | 是 | 当前 agent |
| `learning_target` | `string \| null` | 是 | 当前学习目标 |
| `workflow_plan` | `string[]` | 是 | 当前计划 |
| `plan_index` | `number` | 是 | 当前计划下标 |
| `pending_interrupt` | `boolean` | 是 | 是否等待批准 |
| `message_count` | `number` | 是 | 状态中的消息与压缩摘要数量 |
| `budget_usage` | `object \| null` | 否 | 上一个已持久化 workflow 的 LLM/tool usage 累计账本 |
| `budget_status` | `"active" \| "terminating" \| "terminated" \| null` | 否 | 上一个已持久化 workflow 的预算状态 |
| `budget_termination` | `object \| null` | 否 | 上一个 workflow 的结构化预算终止原因 |
| `context_metrics` | `object \| null` | 否 | 上一个已持久化 workflow 的上下文累计指标 |
| `provider_retry_usage` | `object \| null` | 否 | 上一个已持久化 request/workflow 的 provider retry 累计账本 |

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

### plan_update

计划首次写入或计划下标推进时发送。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `plan` | `string[]` | 否 | 新工作流计划，通常只在 `store_plan` 后出现 |
| `plan_index` | `number` | 否 | 当前计划下标 |
| `learning_target` | `string` | 否 | 学习目标，通常只在 `store_plan` 后出现 |

### structured_result

parser 或 relation 的结构化结果写入 graph state 时发送。前端 Inspector 会记录该事件，但不会把它当作面向用户的聊天消息。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `node` | `string` | 是 | 产生结果的 finish node |
| `result_key` | `"parser_result" \| "relation_result"` | 是 | state 字段名 |
| `result` | `object` | 是 | 结构化结果 |
| `parsed` | `boolean` | 是 | 当前结果是否通过结构化解析 |

### usage_update

LLM 或 tool 执行量被计入 workflow budget usage 时发送。该事件同时携带本节点 delta 和更新后的累计账本，前端不应把多个累计值再次相加。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `node` | `string` | 是 | 产生本次 usage 的 graph 节点 |
| `delta` | `object` | 是 | `kind` 为 `llm` 或 `tool` 的本节点增量 |
| `usage` | `object` | 是 | 更新后的版本化累计 usage |

### budget_started

新 graph request 建立执行预算时发送。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `node` | `string` | 是 | 初始化预算的 graph 节点 |
| `status` | `"active"` | 是 | 固定为 `active` |
| `usage` | `object` | 是 | 初始化后的版本化累计 usage |

### budget_terminated

请求命中 deadline、LLM/tool 调用数、token 或估算成本硬限制并进入确定性收束节点时发送。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `node` | `string` | 是 | 预算终止节点 |
| `termination` | `object` | 是 | 版本化终止维度、限制与安全原因 |
| `usage` | `object \| null` | 否 | 终止时可用的累计 usage |

### context_metrics_update

request 起点重置上下文指标，或一次 assistant 调用完成上下文测量时发送。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `node` | `string` | 是 | 产生测量的 graph 节点 |
| `delta` | `object` | 是 | `kind` 为 `reset` 或 `assistant` 的本节点增量 |
| `metrics` | `object` | 是 | 更新后的版本化上下文累计指标 |

### provider_retry_update

Embedding 或 web search 的一次逻辑 provider operation 完成后发送；request 真正从 graph `START` 开始时也发送一次 `reset`。该事件只做精确观测，不占用或修改 `ExecutionBudget`。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `node` | `string` | 是 | 捕获 provider operation 的 ToolNode |
| `delta` | `object` | 是 | `kind` 为 `reset` 或 `operations`；后者只含本节点新完成的 operation |
| `usage` | `object` | 是 | schema version 1 的累计账本，包含 operation 明细和重算后的 summary |

每条 operation 仅包含受控字段：`operation`、`dependency`、`tool`、`idempotent`、`attempts`、`retries`、`waited_seconds`、`outcome`、`reason` 和安全 `error_code`。原始异常文本、URL、请求内容和凭据不会进入 payload。

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
| `status` | `"success" \| "error"` | 是 | 显式工具执行状态；前端不得从自然语言 content 猜测 |
| `error` | `string \| null` | 否 | 兼容字段；失败时等于安全错误消息 |
| `safe_message` | `string \| null` | 否 | 可直接展示的脱敏错误消息 |
| `code` | `string \| null` | 否 | 稳定错误码 |
| `retryable` | `boolean \| null` | 否 | 调用方是否可重试 |
| `dependency` | `string \| null` | 否 | 失败依赖的安全标识 |
| `cause_type` | `string \| null` | 否 | 受控异常类型，不含原始异常文本 |

### interrupt_required

请求结束时发现有 pending interrupt。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `pending` | `boolean` | 是 | 固定为 `true` |
| `approval_kind` | `string` | 否 | `guardrail_input` 表示 medium-risk 输入审批；敏感工具审批时可为空 |
| `source` | `string` | 否 | Guardrail 输入来源，例如 `chat.input` 或 `chat.approval_feedback` |
| `risk_level` | `string` | 否 | Guardrails 风险级别 |
| `findings` | `string[]` | 否 | Guardrails 命中的规则名 |

### guardrail_blocked

同步兼容流在输入被 guardrail 阻止时使用。当前 HTTP endpoint 会在建立 SSE 前优先返回 `400 guardrail_blocked` JSON；该事件名仍保留在跨端 contract 中，防止兼容调用方静默丢弃。

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 会话 ID |
| `source` | `string` | 是 | 被检测输入的来源 |
| `risk_level` | `string` | 是 | Guardrails 风险级别 |
| `findings` | `string[]` | 是 | 命中的规则名 |

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
| `session_id` | `string` | 是 | 会话 ID |
| `status` | `"error"` | 是 | 固定为 `error` |
| `code` | `string` | 是 | 稳定错误码 |
| `retryable` | `boolean` | 是 | 调用方是否可重试 |
| `message` | `string` | 是 | 脱敏后的兼容错误消息 |
| `safe_message` | `string` | 是 | 可直接展示的脱敏错误消息 |
| `dependency` | `string \| null` | 否 | 失败依赖的安全标识 |
| `cause_type` | `string` | 是 | 受控异常类型，不含原始异常文本 |

## 状态恢复约定

前端进入页面时推荐先并行请求：

1. `GET /learning/overview` 渲染知识库、复习队列和聚合统计。
2. `GET /sessions/{id}/history` 恢复聊天记录。
3. `GET /sessions/{id}/state` 恢复当前 agent、计划和 interrupt 状态。

用户发消息时调用 `POST /chat`。收到第一帧 `session_snapshot` 后用它作为本次流的 baseline；收到 `plan_update` 后合并更新计划字段；收到 `agent_transition` 后更新当前 agent；收到 `structured_result` 后记录到 Inspector；`usage_update`、`budget_*`、`context_metrics_update` 和 `provider_retry_update` 应以 payload 中的累计对象覆盖相应视图，并保留 delta 供 Inspector 展示；收到 `token` 时追加到正在流式输出的 assistant 文本；收到 `done`、`interrupt_required` 或 `error` 后结束本次流。
