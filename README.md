# Tech Doc Reader Agent

[![CI](https://github.com/pipehui/tech-doc-reader-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/pipehui/tech-doc-reader-agent/actions/workflows/ci.yml)

一个基于 LangGraph 的多智能体技术文档研读助手。它把“读完一份陌生技术文档”拆成解析、关联、讲解、测验、沉淀的协作流程，并用 Studio / Inspector / Learner 三种前端视角展示同一条会话状态。

项目最初参考了 LangGraph 多助手教程中的对话状态管理思路；当前的 PlanWorkflow、Adaptive 三档路由、tool budget 守卫、SSE Inspector 和 Learner 视角为本项目围绕技术文档研读场景的自主扩展。

![Landing page](docs/images/landing.png)

## Highlights

- **Multi-agent orchestration**: `primary` 自适应路由，按任务复杂度选择 direct response、single agent 或 parser -> relation -> explanation 链式研读。
- **SSE streaming UI**: FastAPI 通过 SSE 返回 token、tool、plan、agent transition 等事件，前端实时渲染。
- **HITL approval**: 敏感工具调用前暂停，用户审批后继续执行。
- **Session recovery**: Redis checkpointer + 状态接口支持刷新后恢复会话。
- **Traceable runtime**: 内部 `trace_id` 贯穿 SSE / JSON 日志，并可选接入 Langfuse 记录 LangGraph/LangChain 调用链路。
- **Learning memory**: 学习记录、掌握度和复习次数会沉淀到 learner 视角。
- **Three product views**: Studio 面向日常对话，Inspector 面向事件可观测性，Learner 面向学习复盘。

## Quality Gates

当前 CI 覆盖后端 lint、基础类型检查、pytest，以及前端类型检查和生产构建：

```bash
python -m ruff check tech_doc_agent tests evals
python -m mypy tech_doc_agent/app/core tech_doc_agent/app/api/schemas.py
python -m pytest
cd frontend && npm run check && npm run build
```

## Evaluation

`evals/` 提供在线评测基线，默认只运行单轮可完成的 direct、single-agent 和标准 multi-agent 链路。先启动后端，再运行：

```bash
python -m evals.run_eval --cases evals/cases.json --timeout 240 --output eval_results/latest.jsonl --report eval_reports/latest.md
```

评测会自动收集 predicted plan、learning target、最终回答、延迟和工具调用数，并输出 JSONL 原始结果与 Markdown 报告。`enabled=false` 的多轮用例默认跳过，后续会单独用 multi-turn runner 评测。`eval_results/` 和 `eval_reports/` 默认不提交到 Git。

检索链路提供离线评测，直接调用 Hybrid RAG，不需要启动后端：

```bash
python -m evals.run_retrieval_eval --cases evals/retrieval_cases.json --k 5 --output eval_results/retrieval_latest.jsonl --report eval_reports/retrieval_latest.md
```

检索报告会统计 Recall@K、MRR、关键词覆盖率、延迟和每个 case 的 top-k 命中文档。

完成知识库初始化后，使用 full cases 跑正式检索评测。建议分别跑 BM25-only、Vector-only 和 Hybrid，对比 Hit@1、MRR：

```bash
python -m evals.run_retrieval_eval --cases evals/retrieval_cases_full.json --mode bm25 --k 5 --output eval_results/retrieval_bm25.jsonl --report eval_reports/retrieval_bm25.md
python -m evals.run_retrieval_eval --cases evals/retrieval_cases_full.json --mode vector --k 5 --output eval_results/retrieval_vector.jsonl --report eval_reports/retrieval_vector.md
python -m evals.run_retrieval_eval --cases evals/retrieval_cases_full.json --mode hybrid --k 5 --output eval_results/retrieval_hybrid.jsonl --report eval_reports/retrieval_hybrid.md
```

检索 metadata filter 有单独的混淆用例集，用来验证 `category` / `tags` 过滤是否能限制召回范围：

```bash
python -m evals.run_retrieval_eval --cases evals/retrieval_filter_cases.json --mode hybrid --k 5 --output eval_results/retrieval_filter.jsonl --report eval_reports/retrieval_filter.md
```

当前 full retrieval baseline（2026-04-29，60 cases，Top K=5，未显式使用 metadata filter）：

| Mode | Recall@5 | Hit@1 | MRR | Keyword Coverage | E2E p50 | E2E p95 |
|---|---:|---:|---:|---:|---:|---:|
| BM25-only | 0.85 | 0.37 | 0.56 | 0.97 | 0.020s | 0.021s |
| Vector-only | 0.88 | 0.52 | 0.65 | 0.97 | 0.927s | 1.609s |
| Hybrid | 0.93 | 0.53 | 0.70 | 0.98 | 1.209s | 2.148s |

这组结果显示 Hybrid 在 Recall@5 和 MRR 上最好；Vector-only 的 Hit@1 接近 Hybrid 且延迟更低；BM25-only 主要作为低成本、低延迟检索基线。

当前 metadata filter eval（2026-04-29，8 filtered-confusable cases，Top K=5，显式传入 category filter）：

| Mode | Recall@5 | Hit@1 | MRR | Keyword Coverage | E2E p50 | E2E p95 |
|---|---:|---:|---:|---:|---:|---:|
| Hybrid + metadata filter | 1.00 | 1.00 | 1.00 | 1.00 | 1.145s | 2.833s |

## Knowledge Base Seeding

可以用脚本批量通过 parser 写入文档库。脚本会为每个 topic 创建新 session，等待 `save_docs` 审批请求并自动批准：

```bash
python scripts/seed_doc_store.py --topics-file scripts/doc_seed_topics.example.txt --api-url http://127.0.0.1:8000/chat --timeout 600
```

默认只自动批准 `save_docs`，运行前需要先启动后端，并确保 `.env` 里的 embedding 配置可用。

已有文档库可以无损补齐 metadata，不会重新调用 parser 或重新生成 embedding：

```bash
python scripts/migrate_doc_metadata.py --dry-run
python scripts/migrate_doc_metadata.py
```

### Baseline

Online single-turn eval before async/runtime/RAG optimization:

| Date | Cases | Done | Error | Plan Match | Keyword | E2E p50 | E2E p95 | Tool Results Avg |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-04-28 | 11 | 11 | 0 | 1.00 | 1.00 | 64.67s | 176.01s | 5.64 |

这组 baseline 只覆盖单轮可完成任务，不包含需要第二轮用户回答的总结、测验评估等多轮链路。

## Observability

每次 `/chat` 和 `/chat/approve` 请求都会生成或接收一个 `trace_id`。该 ID 会贯穿：

- SSE 事件 payload
- 后端结构化 JSON 日志
- LangGraph config metadata
- 可选 Langfuse trace metadata

启用 Langfuse tracing：

```bash
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=your_public_key
LANGFUSE_SECRET_KEY=your_secret_key
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

启用后，`ChatRuntime` 会把 Langfuse `CallbackHandler` 注入 LangGraph/LangChain config，并在日志中输出对应的 `langfuse_trace_url`。本地如果需要请求结束后立即刷新 trace，可设置 `LANGFUSE_FLUSH_ON_REQUEST=true`。

![Langfuse trace view](docs/images/langfuse-trace.png)

## Frontend

当前前端是 Vite + React + TypeScript。

主要路由：

- `/`：Landing page，项目介绍和入口分流
- `/studio?session=xxx`：日常研读工作台
- `/inspector?session=xxx`：事件流追踪台
- `/learner?session=xxx`：学习记录与复习台
- `/studio?session=xxx&prompt=xxx`：从首页快速体验卡片进入，自动预填 prompt

三种视角：

- **Studio**：对话、计划推进、agent 切换、tool 调用、HITL 审批。
- **Inspector**：swim lane、事件列表、事件详情、trace JSON 导出。
- **Learner**：知识卡片、复习队列、测验模式。

## Architecture

![Multi-agent technical document reader architecture](graphs/tech_doc_reader_agent_architecture.svg)

核心路径：

- `Client + FastAPI` 作为统一入口，通过 `POST /chat` 返回 SSE 流。
- `primary assistant` 负责用户意图理解、自适应路由和 `PlanWorkflow`。
- 多 agent 链路主要是 `parser -> relation -> explanation`。
- `examination` 和 `summary` 处理测验、评估、总结与学习记录更新。
- 底层共享 FAISS 文档向量库、Learning store、Web search 和 Redis checkpointer。

## Agents

- `primary assistant`：理解用户意图，直接回答或规划研读流程
- `parser assistant`：读取文档，提取结构化信息
- `relation assistant`：补充相关知识、类比和上下文
- `explanation assistant`：把知识点解释给用户
- `examination assistant`：出题、评估和更新掌握度
- `summary assistant`：总结学习过程并沉淀复习记录

## API

常用接口：

- `POST /chat`：发送用户消息，返回 SSE 流
- `POST /chat/approve`：批准或拒绝待审批工具调用
- `GET /sessions/{id}/history`：获取前端友好的会话历史
- `GET /sessions/{id}/state`：获取当前会话状态
- `GET /learning/overview`：获取学习记录概览

SSE 事件包括：

- `session_snapshot`
- `agent_transition`
- `plan_update`
- `token`
- `agent_message`
- `tool_call`
- `tool_result`
- `interrupt_required`
- `no_pending_interrupt`
- `done`
- `error`

更完整的接口说明见 [docs/api.md](docs/api.md)。

## Local Development

### 1. Environment

复制 `.env.example` 为 `.env` 或本地 `.dev.env`，至少包含：

```bash
OPENAI_API_KEY=your_key
PRIMARY_MODEL=your_model
EMBEDDING_API_KEY=your_embedding_key
EMBEDDING_API_BASE=your_embedding_base
EMBEDDING_MODEL=your_embedding_model
TAVILY_API_KEY=your_tavily_key
REDIS_URL=redis://localhost:6379
```

### 2. Start Redis

```bash
docker compose up -d redis
```

### 3. Start Backend

```bash
PYTHONPATH=. uvicorn tech_doc_agent.app.api.server:app --reload
```

Backend:

```text
http://127.0.0.1:8000
```

### 4. Start Frontend Dev Server

```bash
cd frontend
npm install
npm run dev
```

Frontend dev server:

```text
http://127.0.0.1:5173
```

Vite 会把 `/chat`、`/sessions`、`/learning`、`/graphs` 代理到 `http://127.0.0.1:8000`。

## Production Build

```bash
cd frontend
npm run build
```

构建产物会生成到 `frontend/dist/`。FastAPI 会优先服务 `frontend/dist/index.html` 和 `/assets`。

然后启动后端：

```bash
PYTHONPATH=. uvicorn tech_doc_agent.app.api.server:app --host 0.0.0.0 --port 8000
```

访问：

```text
http://127.0.0.1:8000/
```

## Docker

当前 `docker-compose.yml` 启动 Redis 和 FastAPI 后端：

```bash
docker compose up --build
```

访问生产构建形态：

```text
http://127.0.0.1:8000/
```

注意：`docker compose up --build` 不会启动 Vite dev server，所以不会开放 `5173`。开发时如果需要 `5173`，请另开终端执行 `cd frontend && npm run dev`。

## Project Structure

```text
tech_doc_agent/
  app/
    api/          FastAPI routes and schemas
    core/         settings
    services/
      assistants/  LangGraph agent implementations
      tools/       document, learning and web-search tools
      vectordb/    FAISS vector store
  data/           runtime data
docs/
  api.md
frontend/
  src/            React + TypeScript frontend
  styles.css
graphs/
  tech_doc_reader_agent_architecture.svg
scripts/
```

## Runtime Data

运行时数据默认位于：

- `tech_doc_agent/data/faiss_store`
- `tech_doc_agent/data/learning_store`
- `tech_doc_agent/data/web_search`
- `tech_doc_agent/data/redis`

这些目录通常不应提交到 Git。
