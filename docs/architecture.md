# Architecture

Tech Doc Reader Agent 是一个围绕“技术概念学习”设计的多智能体系统。它不把所有任务都交给一个聊天模型，而是先判断任务复杂度，再选择直接回答、单 agent 或多 agent 链路。

![Multi-agent technical document reader architecture](../graphs/tech_doc_reader_agent_architecture.svg)

## Request Path

1. 前端通过 `POST /chat` 发起请求，FastAPI 返回 SSE 事件流。
2. `ChatRuntime` 构建 LangGraph config，注入 `thread_id`、`trace_id`、`user_id` 和 `namespace`。
3. `fetch_user_info` 读取长期用户画像和相关学习轨迹 memory。
4. `primary assistant` 选择 direct response、工具调用或 `PlanWorkflow`。
5. LangGraph 根据计划进入 `parser`、`relation`、`explanation`、`examination` 或 `summary`。
6. 敏感工具节点使用 `interrupt_before` 暂停，等待 `/chat/approve` 继续。

## Agents

| Agent | Responsibility |
|---|---|
| `primary` | 理解用户目标，决定 direct / single-agent / multi-agent 路径 |
| `parser` | 读取文档、本地知识库或 Web search，提取结构化信息 |
| `relation` | 检索相关知识、类比和边界，辅助解释 |
| `explanation` | 面向用户生成最终概念解释 |
| `examination` | 出题、评估掌握情况，并可更新学习记录 |
| `summary` | 总结本轮学习过程，沉淀学习记录和学习轨迹 memory |

## Routing

`primary` 使用三档策略：

- direct response：打招呼、能力介绍、简单学习状态查询、明确但简单的记录管理请求。
- single-agent：只需要一个专职 agent，例如单独出题或总结。
- multi-agent：学习新概念或机制时，通常使用 `parser -> relation -> explanation`。

复杂任务会生成 `PlanWorkflow`，其中包含：

- `steps`
- `goal`
- `learning_target`

`learning_target` 会被用于学习记录、检索上下文和后续 eval。

## State And Data

LangGraph state 保存：

- `messages`
- `user_id`
- `namespace`
- `user_info`
- `dialog_state`
- `learning_target`
- `workflow_plan`
- `plan_index`
- `parser_result`
- `relation_result`

运行时数据层：

- FAISS document store：共享技术知识库
- Hybrid retriever：BM25 + Vector + RRF
- Learning store：轻量学习记录
- Memory store：长期学习轨迹片段
- User profile：长期用户画像
- Web search backend：Tavily + DuckDuckGo fallback
- Redis checkpointer：会话恢复

## Frontend Views

- Studio：日常对话、计划推进、agent 切换、tool 调用和 HITL 审批。
- Inspector：SSE 事件流、swim lane、trace JSON 和调试视图。
- Learner：学习记录、复习队列和测验入口。
