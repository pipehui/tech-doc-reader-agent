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
│   ├── core
│   ├── graph.py
│   ├── main.py
│   └── services
│       ├── assistants
│       ├── tools
│       ├── vectordb
│       └── chat_runtime.py
└── data
```

## 关键文件

### `app/graph.py`

定义主工作流图，包括：

- 用户信息注入
- primary assistant
- parser / relation / explanation / examination / summary 子助手
- safe / sensitive tool 路由
- interrupt 节点

### `app/services/chat_runtime.py`

运行时封装层，负责：

- 创建 Redis checkpointer
- 构建 graph
- 发消息
- 审批恢复
- 获取历史与状态

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
