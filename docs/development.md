# Development

## Quality Gates

当前 CI 覆盖后端 lint、基础类型检查、pytest，以及前端类型检查、Vitest 单元测试、生产构建和 FastAPI dist/asset smoke：

```bash
python -m ruff check tech_doc_agent tests evals
python -m mypy tech_doc_agent/app/core tech_doc_agent/app/api/schemas.py
python -m pytest
cd frontend && npm run check && npm run test && npm run build
python -m pytest tests/test_frontend_static.py -q
```

## Local Setup

复制环境变量模板：

```bash
cp .env.example .env
```

至少需要配置：

```bash
OPENAI_API_KEY=your_key
PRIMARY_MODEL=your_model
EMBEDDING_API_KEY=your_embedding_key
EMBEDDING_API_BASE=your_embedding_base
EMBEDDING_MODEL=your_embedding_model
TAVILY_API_KEY=your_tavily_key
REDIS_URL=redis://localhost:6379
GUARDRAIL_APPROVAL_TTL_SECONDS=900
```

可选 telemetry user-id 稳定假名配置（至少 16 字节，生产环境应由 secret manager 注入）：

```bash
TELEMETRY_PSEUDONYM_KEY=replace_with_a_random_secret_of_at_least_16_bytes
```

留空时仍会做 credential/email/phone 模式脱敏，但不会使用无密钥 hash 处理普通 user id。

LangGraph checkpoint 和 medium-risk input guardrail approval 共用 Redis。approval 使用带 TTL 的独立 key，并通过原子 `GETDEL` 保证同一请求只被一个 worker 消费；部署的 Redis 版本需不低于 6.2。

启动 Redis：

```bash
docker compose up -d redis
```

启动后端：

```bash
PYTHONPATH=. uvicorn tech_doc_agent.app.api.server:app --reload
```

启动前端：

```bash
cd frontend
npm install
npm run dev
```

访问：

```text
http://127.0.0.1:5173
```

Vite 会把 `/chat`、`/sessions`、`/learning`、`/graphs` 代理到 `http://127.0.0.1:8000`。

## Production Build

```bash
cd frontend
npm run build
```

构建产物会生成到 `frontend/dist/`。FastAPI 只服务该目录的 `index.html` 和 `/assets`；如果 dist 缺失，页面路由返回明确 `503`，不会回退到不可执行的 `/src/main.tsx` 源码入口。

启动后端：

```bash
PYTHONPATH=. uvicorn tech_doc_agent.app.api.server:app --host 0.0.0.0 --port 8000
```

访问：

```text
http://127.0.0.1:8000/
```

## Docker

```bash
docker compose up --build
```

Compose 会先等待 Redis healthy，再启动后端；后端容器使用 `/ready` 做 healthcheck。

手动检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

注意：`docker compose up --build` 不会启动 Vite dev server，所以不会开放 `5173`；Docker multi-stage build 会生成 production dist，并由容器内 FastAPI 在 `8000` 提供。

## Knowledge Base Seeding

可以用脚本批量通过 parser 写入文档库。脚本会为每个 topic 创建新 session，等待 `save_docs` 审批请求并自动批准：

```bash
python scripts/seed_doc_store.py --topics-file scripts/doc_seed_topics.example.txt --api-url http://127.0.0.1:8000/chat --timeout 600
```

默认只自动批准 `save_docs`。文档库是共享知识库；如需让批量写入过程使用指定会话租户，可追加：

```bash
python scripts/seed_doc_store.py --topics-file scripts/doc_seed_topics.example.txt --user-id user-a --namespace tech_docs
```

已有文档库可以无损补齐 metadata，不会重新调用 parser 或重新生成 embedding：

```bash
python scripts/migrate_doc_metadata.py --dry-run
python scripts/migrate_doc_metadata.py
```

## Project Structure

```text
tech_doc_agent/
  app/
    api/           FastAPI routes, SSE protocol and schemas
    composition.py explicit tools, models, assistants and graph wiring
    core/          settings, tenant, observability, guardrails
    graph/         dependency-free graph specs, nodes, routing and builder
    runtime/       lifecycle, execution, approvals and session queries
    tools/         dependency-bound document, learning and profile tools
    services/
      assistants/ prompts, model factory and assistant registry
      retrieval/  hybrid retrieval and metadata helpers
      vectordb/   FAISS, learning, memory and web-search stores
  data/           runtime data
docs/
frontend/
graphs/
scripts/
evals/
tests/
```

## Runtime Data

运行时数据默认位于：

- `tech_doc_agent/data/faiss_store`
- `tech_doc_agent/data/learning_store`
- `tech_doc_agent/data/memory_store`
- `tech_doc_agent/data/user_profiles`
- `tech_doc_agent/data/web_search`
- `tech_doc_agent/data/redis`

这些目录通常不应提交到 Git。
