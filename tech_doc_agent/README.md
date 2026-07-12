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
│   ├── agents
│   │   ├── definition.py
│   │   ├── identity.py
│   │   ├── model_factory.py
│   │   ├── prompt_registry.py
│   │   ├── prompts
│   │   └── registry.py
│   ├── bootstrap.py
│   ├── composition.py
│   ├── core
│   ├── graph
│   │   ├── builder.py
│   │   ├── commands.py
│   │   ├── nodes.py
│   │   ├── routing.py
│   │   ├── specs.py
│   │   └── tool_policy.py
│   ├── infrastructure
│   │   └── persistence
│   │       ├── approval_repository.py
│   │       └── atomic_json.py
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
│       ├── vectordb
│       ├── embedding.py
│       └── resources.py
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

`builder.py` 只消费注入的 `GraphSpec` 并负责图组装，`routing.py` 负责条件路由，`nodes.py` 负责 graph lifecycle node，`tool_policy.py` 负责重复调用和 parser 检索预算。它们不创建真实模型、工具或存储。

### `app/bootstrap.py` 与 `app/infrastructure`

`bootstrap.py` 是 production 入口，FastAPI lifespan 和 CLI 从这里显式组装 settings、Redis approval repository 与 `ChatRuntime`。`composition.py` 再把 runtime resources 组合为 `ToolBundle`、`AssistantRegistry`、`GraphSpec` 和最终 graph。`infrastructure/persistence/approval_repository.py` 实现带 TTL、schema envelope 和原子 `GETDEL` 的 Redis adapter；runtime domain 不依赖该具体实现。

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

### `app/services/vectordb`

包含当前仍在使用的数据后端：

- `faiss_store.py`
- `learning_store_backend.py`
- `web_search_backend.py`

### `app/services/retrieval`

`HybridRetriever` 保持统一 facade，只负责 mode、cache、settings 和 telemetry。内部按职责拆为 metadata taxonomy/filter/inference/normalization，以及 BM25、semantic、exact、RRF、formatter；ranker 通过 typed candidates 与窄 store ports 协作，可独立测试且不反向依赖 facade。

跨层 contract 不放在实现目录：`app/application/retrieval.py` 定义 `SearchQuery`、`SearchResult` 和 `DocumentRetrieverPort`。tools/eval 依赖该 contract；filter normalization 的所有权只在 retrieval 实现层。`app/services/retrieval` 仅为已有 package facade re-export 同一个类型对象，不复制 model 定义。

## 当前状态

这个模块当前只服务于技术文档研读助手场景。所有代码应围绕：

- 技术文档解析
- 技术知识讲解
- 学习检测
- 学习总结
- 学习记录持久化

来理解和维护。
