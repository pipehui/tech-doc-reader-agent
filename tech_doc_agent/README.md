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
│   ├── bootstrap.py
│   ├── core
│   ├── graph
│   │   ├── builder.py
│   │   ├── nodes.py
│   │   ├── routing.py
│   │   ├── specs.py
│   │   └── tool_policy.py
│   ├── infrastructure
│   │   └── persistence
│   │       ├── approval_repository.py
│   │       └── atomic_json.py
│   ├── runtime
│   │   ├── approvals.py
│   │   ├── config.py
│   │   ├── execution.py
│   │   ├── lifecycle.py
│   │   ├── serialization.py
│   │   ├── sessions.py
│   │   └── telemetry.py
│   ├── main.py
│   └── services
│       ├── assistants
│       ├── tools
│       ├── vectordb
│       └── chat_runtime.py
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

`builder.py` 负责图组装，`routing.py` 负责条件路由，`nodes.py` 负责 graph lifecycle node，`tool_policy.py` 负责重复调用和 parser 检索预算。

### `app/bootstrap.py` 与 `app/infrastructure`

`bootstrap.py` 是 production composition root：FastAPI lifespan 和 CLI 从这里显式组装 settings、Redis approval repository 与 `ChatRuntime`。`infrastructure/persistence/approval_repository.py` 实现带 TTL、schema envelope 和原子 `GETDEL` 的 Redis adapter；runtime domain 不依赖该具体实现。

### `app/runtime`

运行时内聚组件：`config.py` 构造 tenant-scoped LangGraph config，`serialization.py` 负责消息 API 投影，`sessions.py` 读取 checkpoint 并形成 history/state view，`execution.py` 统一 send/resume 与 sync/async bridge，`approvals.py` 定义审批用例和 repository port，`telemetry.py` 统一 operation 日志，`lifecycle.py` 管理 resources/checkpointer/graph 的 start/retry/close。组件通过窄接口获取具体实现，不反向依赖 `ChatRuntime` 或 API。

### `app/services/chat_runtime.py`

兼容 facade，负责：

- 委托 `RuntimeLifecycle` 管理 resources/checkpointer/graph
- 委托 `app/runtime` 发消息和审批恢复
- 委托 `app/runtime` 获取历史与状态

### `app/api/routes/chat.py`

定义当前外部接口：

- `POST /chat`
- `POST /chat/approve`
- `GET /sessions/{id}/history`
- `GET /sessions/{id}/state`

### `app/services/assistants`

包含当前系统的全部助手：

- `primary_assistant.py`
- `parser_assistant.py`
- `relation_assistant.py`
- `explanation_assistant.py`
- `examination_assistant.py`
- `summary_assistant.py`

### `app/services/tools`

包含当前项目的业务工具：

- `doc_store.py`
- `learning_store.py`

### `app/services/vectordb`

包含当前仍在使用的数据后端：

- `faiss_store.py`
- `learning_store_backend.py`
- `web_search_backend.py`

## 当前状态

这个模块当前只服务于技术文档研读助手场景。所有代码应围绕：

- 技术文档解析
- 技术知识讲解
- 学习检测
- 学习总结
- 学习记录持久化

来理解和维护。
