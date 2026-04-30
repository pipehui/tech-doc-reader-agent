# Learning State

系统把学习状态拆成三层，避免把“本轮观察”和“长期偏好”混在一起。

| Layer | Storage | Updated By | Purpose |
|---|---|---|---|
| 学习记录 | `tech_doc_agent/data/learning_store` | `summary` / `examination` / 用户显式记录请求，经审批写入 | 记录学过什么、最近学习时间、掌握度和复习次数 |
| 学习轨迹 memory | `tech_doc_agent/data/memory_store` | `summary` 在有明确证据时，经审批写入 | 记录卡点、误解、已掌握点和复习提示，供后续相关问题检索 |
| 长期用户画像 | `tech_doc_agent/data/user_profiles` | 仅当用户主动要求更新能力或偏好时，由 `primary` 读取学习记录和 memory 后，经审批写入 | 记录经验水平、解释风格、解释深度、熟悉主题和薄弱主题 |

## Boundaries

- `summary` 可以沉淀本轮学习记录和学习轨迹 memory，但不会自动修改长期用户画像。
- `primary` 只有在用户明确提出“更新我的能力信息 / 用户画像 / 解释偏好”时，才会调用 `update_user_profile`。
- `update_user_profile` 是 sensitive tool，会触发 `interrupt_required`，用户批准后才落盘。
- 会话、学习记录和 memory 按 `user_id + namespace` 隔离。
- 长期用户画像按 `user_id` 保存，同一个用户在不同 namespace 下共享画像。
- 文档库是共享知识库，不按租户隔离。

## Tools

| Tool | Type | Notes |
|---|---|---|
| `read_learning_history` | safe | 查询轻量学习记录 |
| `read_all_learning_history` | safe | 读取当前用户全部学习记录概览 |
| `read_user_memory` | safe | 查询长期学习轨迹 memory |
| `upsert_learning_history` | sensitive | 写入或更新学习记录 |
| `upsert_learning_state` | sensitive | 合并更新学习记录和一条学习轨迹 memory |
| `read_user_profile` | safe | 读取长期用户画像 |
| `update_user_profile` | sensitive | 更新长期用户画像 |

## Typical Flow

用户完成一次学习后：

1. `summary` 读取已有学习记录和 memory。
2. 如果有明确证据，`summary` 调用 `upsert_learning_state`。
3. 系统进入 HITL interrupt，等待用户批准。
4. 批准后更新 learning store 和 memory store。

用户主动要求更新画像时：

1. `primary` 读取 `read_user_profile`。
2. 如需依据最近学习情况，读取 `read_all_learning_history` 和 `read_user_memory`。
3. `primary` 生成保守的画像更新，不因一次对话夸大能力。
4. 调用 `update_user_profile` 并等待用户审批。

## API

- `GET /learning/overview`
- `GET /learning/records`
- `GET /learning/memory`
- `GET /learning/profile`

完整字段见 [api.md](api.md)。
