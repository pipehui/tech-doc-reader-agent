# 重构后源码研读手册

这套文档解决的不是“项目用了哪些架构模式”，而是下面这些更实际的问题：

- 一条用户消息从浏览器发出后，依次进入哪些函数？
- 每个函数收到什么、返回什么，谁负责把返回值交给下一层？
- 为什么一个原来较大的模块被拆成这些文件，而不是随意拆目录？
- 修改工具、SSE、Agent、状态或持久化时，哪些地方必须同步修改？
- 出现“没有回复”“一直等待审批”“刷新后状态不对”等问题时，应从哪里下断点？

文档内容以当前源码和测试为事实源。阅读时建议同时打开对应源码，不必一次把所有章节读完。

## 三条阅读路线

### 路线 A：先恢复整体理解（约 60 分钟）

1. [00 - 系统地图与一条请求的全貌](00-system-map.md)
2. [01 - 启动、资源创建与依赖装配](01-startup-and-composition.md)
3. [02 - Chat API 与 SSE 输出链](02-chat-api-and-sse.md)
4. [03 - Runtime、会话恢复与两类审批](03-runtime-sessions-and-approval.md)
5. [04 - LangGraph 图、节点和路由](04-graph-topology-and-routing.md)

读完后应能回答：后端从哪里启动、请求在哪进入图、图为什么暂停、响应如何回到浏览器。

### 路线 B：准备修改某个功能

| 要改的内容 | 先读 | 再读 |
|---|---|---|
| 新增或修改 API / SSE 事件 | [02](02-chat-api-and-sse.md) | [09](09-frontend-stream-and-state.md)、[11](11-change-recipes.md) |
| 新增 Agent 或改变路由 | [04](04-graph-topology-and-routing.md) | [05](05-agents-prompts-and-models.md)、[11](11-change-recipes.md) |
| 新增工具 | [06](06-tools-and-application-boundaries.md) | [04](04-graph-topology-and-routing.md)、[11](11-change-recipes.md) |
| 修改学习记录、memory 或画像 | [07](07-learning-profile-and-persistence.md) | [06](06-tools-and-application-boundaries.md) |
| 修改文档检索或向量库 | [08](08-retrieval-and-document-store.md) | [10](10-cross-cutting-policies.md) |
| 修改前端消息、审批或 Inspector | [09](09-frontend-stream-and-state.md) | [02](02-chat-api-and-sse.md) |
| 修改重试、预算、压缩或日志 | [10](10-cross-cutting-policies.md) | [04](04-graph-topology-and-routing.md) |
| 处理兼容代码或旧数据 | [13](13-compatibility-and-migration.md) | [07](07-learning-profile-and-persistence.md) |
| 测试失败或线上行为不明 | [12](12-debugging-and-tests.md) | [14](14-source-and-test-index.md) |

### 路线 C：边调试边读

先在下面五个位置打断点，再按一次普通聊天请求：

1. `api/routes/chat.py::chat`
2. `api/chat_delivery.py::chat_response`
3. `runtime/execution.py::GraphExecutionService._stream_user_message`
4. `graph/assistant_execution.py::assistant_node` 返回的内部 `invoke` closure
5. `api/sse/streaming.py::events_from_stream_part`

前端再观察：

1. `streaming/chatStream.ts::run`
2. `streaming/sseEnvelope.ts::parseSseMessage`
3. `streaming/sseReducer.ts::reduceSseEvent`
4. `streaming/storeAdapter.ts::dispatchStreamActions`

这条路线能直接看到 `HTTP body -> LangGraph input -> graph part -> SSE payload -> Zustand action` 的形状变化。

## 章节索引

| 章节 | 主要问题 | 关键源码 |
|---|---|---|
| [00 - 系统地图](00-system-map.md) | 目录分别拥有哪部分事实？一条请求全程如何流动？ | `app/*`、`frontend/src/*` |
| [01 - 启动与装配](01-startup-and-composition.md) | FastAPI / CLI 如何创建资源、Redis checkpoint、工具、模型和图？ | `bootstrap.py`、`composition.py`、`runtime/lifecycle.py` |
| [02 - Chat API 与 SSE](02-chat-api-and-sse.md) | 请求如何校验、guardrail 分流、翻译并编码成 SSE？ | `api/routes/chat.py`、`api/chat_delivery.py`、`api/sse/*` |
| [03 - Runtime 与审批](03-runtime-sessions-and-approval.md) | facade 委托给谁？会话如何恢复？两种审批有什么不同？ | `runtime/*`、`application/approval_*` |
| [04 - 图与路由](04-graph-topology-and-routing.md) | 节点怎样注册？primary 与子 Agent 如何选择下一节点？ | `graph/builder.py`、`routing.py`、`nodes.py` |
| [05 - Agent、Prompt 与模型](05-agents-prompts-and-models.md) | 六个角色如何绑定 prompt、模型和不同工具？ | `agents/*`、`agents/prompts/*` |
| [06 - 工具与 Application 边界](06-tools-and-application-boundaries.md) | 工具参数如何变成用例调用？Protocol 为什么放在 application？ | `tools/*`、`application/*` |
| [07 - 学习状态与持久化](07-learning-profile-and-persistence.md) | record、memory、profile 如何写入，怎样保证原子性和幂等？ | `application/learning_*`、`infrastructure/persistence/*` |
| [08 - 检索与文档库](08-retrieval-and-document-store.md) | exact、BM25、vector、RRF 怎样组合？保存文档如何更新索引？ | `infrastructure/retrieval/*` |
| [09 - 前端端到端](09-frontend-stream-and-state.md) | SSE 如何变成消息、tool card、审批抽屉和 Inspector 事件？ | `frontend/src/streaming/*`、`store/*`、`features/*` |
| [10 - 横切机制](10-cross-cutting-policies.md) | tenant、错误、重试、预算、reflection、context、telemetry 如何穿过主链？ | `core/*`、`graph/*` |
| [11 - 修改手册](11-change-recipes.md) | 常见需求应改哪些文件、按什么顺序、漏改会怎样？ | 多模块联动清单 |
| [12 - 调试与测试](12-debugging-and-tests.md) | 典型故障如何定位？每类测试守住什么？ | `tests/*`、前端 `*.test.ts(x)`、CI |
| [13 - 兼容与迁移](13-compatibility-and-migration.md) | 哪些文件只为旧 import / 旧数据存在，何时可删？ | `services/*`、`runtime/approvals.py`、migration |
| [14 - 源码与测试索引](14-source-and-test-index.md) | 想找某个函数、状态字段或测试时去哪？ | 逐目录索引 |
| [15 - 术语表](15-glossary.md) | graph part、checkpoint、interrupt、port、generation 等具体指什么？ | 项目语境词典 |

## 本手册的约定

文档中的“输入/输出”分为三类，不要混在一起：

- Python/TypeScript 函数参数和返回值，例如 `SearchQuery -> list[SearchResult]`。
- LangGraph state update，例如 `{"plan_index": 1}`；它会由 reducer 合并进 checkpoint state。
- 对外协议 payload，例如 SSE `plan_update`；它是从 state update 投影出来的，不等于完整 state。

“safe tool”只表示 LangGraph 执行前不暂停等待人工确认；并不表示它可以绕过输入校验、tenant 或错误处理。“sensitive tool”表示对应 tool node 被放进 `interrupt_before`，用户批准后才真正执行。

“Runtime”在本项目中特指 API/CLI 共用的会话执行门面和其内部协作对象，不包括 FastAPI 路由，也不包括具体 Redis、FAISS 或模型客户端的构造。

## 判断文档是否过期

出现下面任一情况时，本手册相关章节必须一起更新：

- `State` 新增或删除持久化字段；
- `SSE_EVENT_NAMES` 或前端 `SSE_EVENT_TYPES` 改变；
- `ToolBundle`、任一角色的 safe/sensitive tool 集合改变；
- `GraphSpec.subagents`、节点名或 conditional edge 改变；
- application port 的方法签名改变；
- runtime data 的 schema version、generation 布局或兼容入口改变。

同步检查入口见 [11 - 修改手册](11-change-recipes.md) 和 [12 - 调试与测试](12-debugging-and-tests.md)。
