# tech_doc_agent

`tech_doc_agent` 是当前项目的主应用模块，负责多智能体技术文档研读助手的核心运行逻辑。

## 模块职责

这个模块主要负责：

- 定义 LangGraph 工作流
- 组织多智能体协作
- 维护对话状态与学习状态
- 提供 FastAPI + SSE 接口
- 管理文档检索、学习记录和网页检索工具

## 核心目录

```text
tech_doc_agent
├── app
│   ├── api
│   │   ├── chat_delivery.py
│   │   ├── routes
│   │   │   └── chat.py
│   │   └── sse
│   ├── agents
│   │   ├── definition.py
│   │   ├── identity.py
│   │   ├── model_factory.py
│   │   ├── prompt_registry.py
│   │   ├── prompts
│   │   └── registry.py
│   ├── application
│   │   ├── conversation_summarizer.py
│   │   ├── input_guardrails.py
│   │   ├── learning_commands.py
│   │   ├── learning_ports.py
│   │   ├── learning_state.py
│   │   ├── learning_unit_of_work.py
│   │   ├── profile_ports.py
│   │   ├── profile_service.py
│   │   └── retrieval.py
│   ├── bootstrap.py
│   ├── composition.py
│   ├── core
│   ├── graph
│   │   ├── assistant_execution.py
│   │   ├── builder.py
│   │   ├── commands.py
│   │   ├── nodes.py
│   │   ├── routing.py
│   │   ├── specs.py
│   │   └── tool_policy.py
│   ├── infrastructure
│   │   ├── resources.py
│   │   ├── persistence
│   │   │   ├── approval_repository.py
│   │   │   ├── atomic_json.py
│   │   │   ├── learning_store.py
│   │   │   └── memory_store.py
│   │   └── retrieval
│   │       ├── chunking.py
│   │       ├── embedding.py
│   │       ├── faiss_store.py
│   │       ├── hybrid.py
│   │       ├── filters.py
│   │       ├── semantic.py
│   │       ├── fusion.py
│   │       └── web_search.py
│   ├── runtime
│   │   ├── chat_runtime.py
│   │   ├── approvals.py
│   │   ├── config.py
│   │   ├── execution.py
│   │   ├── identity.py
│   │   ├── lifecycle.py
│   │   ├── serialization.py
│   │   ├── sessions.py
│   │   └── telemetry.py
│   ├── tools
│   │   ├── bundle.py
│   │   ├── dependencies.py
│   │   ├── documents.py
│   │   ├── learning.py
│   │   └── profiles.py
│   ├── main.py
│   └── services
│       ├── retrieval
│       └── user_profile.py
└── data
```

## 关键文件

### `app/graph`

定义主工作流图，并通过 `AgentSpec` 注册同构子 Agent，包括：

- 用户信息注入
- primary assistant
- parser / relation / explanation / examination / summary 子助手
- safe / sensitive tool 路由
- interrupt 节点

`builder.py` 只消费注入的 `GraphSpec` 并负责图组装，`assistant_execution.py` 统一 assistant sync/async invocation、budget/context 记录与 reflection completion，`routing.py` 负责条件路由，`nodes.py` 只负责 user-info/entry/exit/finish/failure/plan lifecycle node，`tool_policy.py` 负责重复调用和 parser 检索预算。它们不创建真实模型、工具或存储。

### `app/application`

保存不依赖 delivery 和具体 adapter 的用例、port 与纯策略：learning/profile 状态编排、retrieval 跨层 contract、输入 guardrail decision，以及确定性的 `ExtractiveConversationSummarizer`。`input_guardrails.py` 只评估一次输入风险并记录 application-level warning/blocked disposition，不构造 HTTP/SSE response。

Learning application slice 按变化轴拆分：`learning_commands.py` 拥有 update command/result 与幂等 identity，`learning_ports.py` 拥有 reader/command/updater capability，`learning_unit_of_work.py` 拥有 snapshot、repository port 和原子 commit boundary，`learning_state.py` 只保留 mutation service。Profile 的 repository/service/memory ports 同样由 `profile_ports.py` 统一拥有，`profile_service.py` 只实现画像 use case 与 Agent context formatter；旧 type import 仅保留为受控兼容 re-export。Tools/API/persistence 分别 import 所需事实源，不再通过厚 service 模块取得无关类型。Learning API 只在 runtime 边界 cast 成窄 `LearningApiResources`，后续不裸读 `Any`。摘要策略只消费 core `ConversationSummary`，由 composition 注入 graph compactor，不读取 settings、provider 或 persistence。

### `app/bootstrap.py` 与 `app/infrastructure`

`bootstrap.py` 是 production 入口，FastAPI lifespan 和 CLI 从这里显式选择 `infrastructure/resources.py` 的 concrete resource factory、Redis approval repository 与 `ChatRuntime`。`composition.py` 通过 `CompositionResources` structural Protocol 组合 `ToolBundle`、`AssistantRegistry`、`GraphSpec` 和最终 graph，不 import `AppResources`；bootstrap 的 typed factory adapter 由 mypy 验证 concrete container 满足该协议。`infrastructure/persistence/approval_repository.py` 实现带 TTL、schema envelope 和原子 `GETDEL` 的 Redis adapter；`learning_store.py` 与 `memory_store.py` 是共享 snapshot UoW 的查询/兼容 adapter。runtime domain 不依赖这些具体实现。

### `app/runtime`

运行时内聚组件：`config.py` 构造 tenant-scoped LangGraph config，`serialization.py` 负责消息 API 投影，`sessions.py` 读取 checkpoint 并形成 history/state view，`execution.py` 统一 send/resume 与 sync/async bridge，`approvals.py` 定义审批用例和 repository port，`telemetry.py` 统一 operation 日志，`lifecycle.py` 管理 resources/checkpointer/graph 的 start/retry/close。组件通过窄接口获取具体实现，不反向依赖 API。

### `app/runtime/chat_runtime.py`

API/CLI 共用 facade，负责：

- 委托 `RuntimeLifecycle` 管理 resources/checkpointer/graph
- 委托 `app/runtime` 发消息和审批恢复
- 委托 `app/runtime` 获取历史与状态
- 持有 approval repository 的关闭责任

facade 不构造 RedisSaver、repository、resources、graph 或 assistant identity。`bootstrap.py` 是 production concrete factory；测试使用显式 fake lifecycle/repository factory，不依赖隐藏默认 wiring。

### `app/api/routes/chat.py`

定义当前外部接口：

- `POST /chat`
- `POST /chat/approve`
- `GET /sessions/{id}/history`
- `GET /sessions/{id}/state`

该 route 只负责请求参数、tenant、trace ID 和 delivery use case 调用；顶层函数集合由 architecture test 锁定，不再定义 guardrail payload、stream generator 或 response wrapper。

### `app/api/chat_delivery.py`

提供 route 唯一使用的 `chat_response` 与 `approval_response`。模块内部负责 guardrail 的 JSON/SSE 投影、审批暂停事件、chat/approval stream 编排和 trace-context response wrapping；私有细节不从 route re-export。SSE contract、translator、iterator 与 encoder 仍统一由 `app/api/sse/` 拥有，delivery 仅通过私有 `_sse` 依赖消费。

### `app/agents`

内聚当前系统的 role 定义、prompt、执行身份与模型绑定工厂：

- `primary_assistant.py`
- `parser_assistant.py`
- `relation_assistant.py`
- `explanation_assistant.py`
- `examination_assistant.py`
- `summary_assistant.py`

`model_factory.py` 是模型客户端的唯一构造位置，`definition.py` 统一绑定 safe/sensitive/control tools，`prompt_registry.py` 从同包资源加载并校验 prompt，`registry.py` 组装六个 role。该包不依赖 `services`、runtime、API 或 infrastructure；具体实例只在 composition root 组装。import assistant 基类不会读取 settings 或创建模型客户端。

### `app/tools`

包含按资源实例绑定的业务工具：

- `dependencies.py` 定义窄端口；
- `documents.py`、`learning.py`、`profiles.py` 分别构造各领域工具；
- `bundle.py` 提供稳定命名的 `ToolBundle`。

工具函数不使用全局 resource locator。每个 runtime/composition 都拥有独立工具实例，测试可直接注入 fake ports。

### `app/infrastructure/retrieval`

`HybridRetriever` 保持统一 facade，只负责 mode、cache、settings 和 telemetry。内部按职责拆为 metadata taxonomy/filter/inference/normalization，以及 BM25、semantic、exact、RRF、formatter；`FaissStore`、chunking 与 embedding adapter 共同提供 concrete document index；`web_search.py` 提供 Tavily/DuckDuckGo fallback、usage cache 与 retry telemetry。ranker/provider 通过 typed candidates 与窄 ports 协作，可独立测试且不反向依赖 facade。

跨层 contract 不放在实现目录：`app/application/retrieval.py` 定义 `SearchQuery`、`SearchResult` 和 `DocumentRetrieverPort`。tools 依赖该 contract，resource factory/eval 直接使用 infrastructure implementation；filter normalization 的所有权只在 retrieval 实现层。`app/services/retrieval/__init__.py` 仅作为已有 package facade re-export 同一个 contract 与 `HybridRetriever`，不保留深层实现或复制 model 定义。

### `app/services`（仅兼容）

该 namespace 不再承载 production implementation，只允许三个 Python 文件：空的根 `__init__.py`、`retrieval/__init__.py` package facade、`user_profile.py` legacy constructor/free-function facade。production app 与 composition roots 禁止 import services；递归 compatibility contract 也禁止 facade 依赖 agents、API、graph、runtime、tools 或组装入口。新增 concrete provider/store/use case 不应放回此目录。

## 当前状态

这个模块当前只服务于技术文档研读助手场景。所有代码应围绕：

- 技术文档解析
- 技术知识讲解
- 学习检测
- 学习总结
- 学习记录持久化

来理解和维护。
