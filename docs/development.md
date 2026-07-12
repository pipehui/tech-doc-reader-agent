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
MAX_IDENTICAL_TOOL_REPEATS=2
PARSER_MAX_RETRIEVAL_CALLS=6
```

两个 tool policy 配置必须是非负整数。前者限制连续相同 tool + args 的允许调用次数，后者限制一个 parser step 内
`read_docs` 与 `web_search` 的合计调用次数；超过阈值会产生结构化 error ToolMessage，不执行目标工具。

可选 telemetry user-id 稳定假名配置（至少 16 字节，生产环境应由 secret manager 注入）：

```bash
TELEMETRY_PSEUDONYM_KEY=replace_with_a_random_secret_of_at_least_16_bytes
```

留空时仍会做 credential/email/phone 模式脱敏，但不会使用无密钥 hash 处理普通 user id。

LangGraph checkpoint 和 medium-risk input guardrail approval 共用 Redis。approval 使用带 TTL 的独立 key，并通过原子 `GETDEL` 保证同一请求只被一个 worker 消费；部署的 Redis 版本需不低于 6.2。

## Prompt Resources

Assistant system prompts 位于 `tech_doc_agent/app/services/assistants/prompts/`，由 `manifest.json` 固定 role、稳定 ID、
资源顺序、SHA-256 和 required placeholders。role Python 模块只声明工具组合，不应重新内联 prompt。

有意修改 prompt 时应单独提交：同步升级 prompt ID/hash，运行 `tests/test_prompt_registry.py`，并将模型、数据集或
prompt 变化与纯代码重构分开记录。primary 由 `primary/` 下的有序 section 组合，不要重新合并成单个长文件。

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

FAISS 文档库使用 generation snapshot。`faiss_store/current.json` 只会在新 generation 的 index、documents 和 chunk metadata 全部写入并回读校验成功后原子切换；不要直接修改 generation 内的单个文件。旧版根目录三件套仍可读取，下一次正常保存或 metadata migration 会发布首个 generation，旧文件不会在启动时被隐式删除。

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
- `tech_doc_agent/data/learning_state`（learning records、memories 与幂等 outcome 的 generation snapshot）
- `tech_doc_agent/data/user_profiles`
- `tech_doc_agent/data/web_search`
- `tech_doc_agent/data/redis`

这些目录通常不应提交到 Git。

旧版 `learning_store/records.json` 和 `memory_store/memories.json` 仍作为只读兼容输入；第一次 learning-state 保存会发布新 generation。不要直接编辑 `learning_state/generations/*/state.json`，也不要单独删除 `current.json`。
