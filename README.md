# Multi-Agent Technical Document Learning Assistant

这是一个基于 LangGraph 的多智能体技术文档研读助手项目。系统围绕“技术文档学习”场景构建，支持：

- 多智能体分工协作
- 本地文档读取与向量检索
- 外部网页检索补充知识
- 学习记录与复习记录管理
- Redis 会话持久化
- FastAPI + SSE 流式接口

## 当前能力

系统目前包含以下核心角色：

- `primary assistant`
  负责理解用户意图、规划学习步骤、直接处理显式学习记录请求
- `parser assistant`
  负责读取文档、提取结构化信息
- `relation assistant`
  负责补充相关知识、类比知识和上下文
- `explanation assistant`
  负责把知识点解释给用户
- `examination assistant`
  负责测验、评估和学习情况更新
- `summary assistant`
  负责总结本次学习并在合适时更新复习记录

## 架构概览

![Multi-agent technical document reader architecture](graphs/tech_doc_reader_agent_architecture.svg)

上图展示了当前系统的核心调用路径：

- `Client + FastAPI` 作为统一入口，通过 `POST /chat` 返回 SSE 流
- `primary assistant` 负责做自适应路由和 `PlanWorkflow`
- 系统根据任务复杂度走三条主路径：
  - 直接回复 / 直接工具调用
  - 单 Agent 路径：`examination` 或 `summary`
  - 多 Agent 链路：`parser -> relation -> explanation`
- 底层共享资源包括：
  - `FAISS` 文档向量库
  - `Learning store` 学习/复习记录
  - `Web search` 外部检索
  - `Redis` 会话 checkpointer

## 接口

当前后端已经提供以下接口：

- `POST /chat`
  发送用户消息，返回 SSE 流
- `POST /chat/approve`
  对敏感工具调用进行批准或拒绝
- `GET /sessions/{id}/history`
  获取前端友好的会话历史
- `GET /sessions/{id}/state`
  获取当前会话状态

## SSE 事件

目前 SSE 事件流包含这些事件类型：

- `token`
- `tool_call`
- `tool_result`
- `interrupt_required`
- `no_pending_interrupt`
- `done`
- `error`

## 项目结构

```text
customer_support_chat/
  app/
    api/
    core/
    services/
      assistants/
      tools/
      vectordb/
  data/
graphs/
images/
```

其中：

- `customer_support_chat/app/api`
  FastAPI 服务与路由
- `customer_support_chat/app/services/chat_runtime.py`
  图运行时封装，负责 Redis checkpointer、会话状态和流式执行
- `customer_support_chat/app/graph.py`
  LangGraph 工作流定义
- `customer_support_chat/app/services/tools`
  文档库、学习记录等工具接口
- `customer_support_chat/app/services/vectordb/faiss_store.py`
  本地 FAISS 文档向量存储

## 本地运行

### 1. 准备环境变量

创建 `.env`，至少包含：

```bash
OPENAI_API_KEY=your_key
PRIMARY_MODEL=your_model
EMBEDDING_API_KEY=your_embedding_key
EMBEDDING_API_BASE=your_embedding_base
EMBEDDING_MODEL=your_embedding_model
TAVILY_API_KEY=your_tavily_key
REDIS_URL=redis://localhost:6379
```

### 2. 启动 Redis

如果你想直接使用 Docker：

```bash
docker compose up -d redis
```

### 3. 启动 FastAPI

如果你本地已经安装依赖：

```bash
PYTHONPATH=. uvicorn customer_support_chat.app.api.server:app --reload
```

启动后访问：

- API: `http://127.0.0.1:8000`
- 前端: `http://127.0.0.1:8000/`
- 文档: `http://127.0.0.1:8000/docs`

## Docker

仓库提供了面向当前项目的 `Dockerfile` 和 `docker-compose.yml`：

- `docker-compose.yml`
  用于启动 Redis 和应用服务
- `Dockerfile`
  默认启动 FastAPI 服务，而不是旧的 CLI 入口

## 当前数据目录

运行时数据默认位于：

- `customer_support_chat/data/faiss_store`
- `customer_support_chat/data/learning_store`
- `customer_support_chat/data/web_search`
- `customer_support_chat/data/redis`

## 说明

这个仓库已经完成面向当前项目场景的重构，当前文档、配置和运行方式都应以“技术文档研读助手”为准。
