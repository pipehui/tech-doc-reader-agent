# Development

## Quality Gates

当前 CI 覆盖后端 lint、app/evals 类型检查、pytest 与 deterministic context-compaction regression gate，以及前端类型检查、Vitest 单元测试、生产构建和 FastAPI dist/asset smoke。本地完整检查：

```bash
python -m ruff check .
python -m mypy tech_doc_agent/app evals
python -m pytest -q
cd frontend && npm test && npm run check && npm run build && npm audit
cd ..
python -m pytest tests/test_frontend_static.py -q
```

Windows 已有环境使用 `conda activate agent`；若终端输出遇到 GBK 编码问题，可先设置 `$env:PYTHONUTF8="1"`。Agent/retrieval/context-compaction/concurrency 的可复现命令、密钥/corpus 前置条件和当前无密钥 baseline 见 [evaluation.md](evaluation.md)。`npm audit` 当前是本地显式检查，尚未配置为 CI blocking job。

全量 pytest 同时执行递归 architecture dependency contracts。门禁基于 AST，不会启动模型、Redis 或应用资源；新增 app package/import 时无需维护手写文件清单，但若跨越稳定层级会报告具体相对路径、行号和目标 module。`bootstrap.py`/`composition.py` 是唯一明确的具体组装豁免点，不应通过在普通模块增加 allowlist 绕过依赖方向。

## Local Setup

后端以 Python 3.12 为类型检查/CI 基线；前端支持的 Node.js 版本以 `frontend/package.json` 的 `engines` 为准。Windows 已有环境可先执行 `conda activate agent`。

复制环境变量模板：

```bash
cp .env.example .env
# PowerShell: Copy-Item .env.example .env
```

安装锁定范围内的后端依赖和 lockfile 对应的前端依赖：

```bash
python -m pip install -r requirements.txt
cd frontend
npm ci
cd ..
```

运行完整的在线 chat + vector retrieval 链路通常需要配置：

```bash
OPENAI_API_KEY=your_key
PRIMARY_MODEL=your_model
EMBEDDING_API_KEY=your_embedding_key
EMBEDDING_API_BASE=your_embedding_base
EMBEDDING_MODEL=your_embedding_model
REDIS_URL=redis://localhost:6379
GUARDRAIL_APPROVAL_TTL_SECONDS=900
MAX_IDENTICAL_TOOL_REPEATS=2
PARSER_MAX_RETRIEVAL_CALLS=6
```

`TAVILY_API_KEY` 可选；留空时 Web search 使用 DuckDuckGo fallback。其余 retry、budget、context compaction、RAG 与 telemetry 选项及默认值以 [`.env.example`](../.env.example) 为事实源，不必为了本地启动逐项复制。

两个 tool policy 配置必须是非负整数。前者限制连续相同 tool + args 的允许调用次数，后者限制一个 parser step 内
`read_docs` 与 `web_search` 的合计调用次数；超过阈值会产生结构化 error ToolMessage，不执行目标工具。

可选 telemetry user-id 稳定假名配置（至少 16 字节，生产环境应由 secret manager 注入）：

```bash
TELEMETRY_PSEUDONYM_KEY=replace_with_a_random_secret_of_at_least_16_bytes
```

留空时仍会做 credential/email/phone 模式脱敏，但不会使用无密钥 hash 处理普通 user id。

LangGraph checkpoint 和 medium-risk input guardrail approval 共用 Redis。approval 使用带 TTL 的独立 key，并通过原子 `GETDEL` 保证同一请求只被一个 worker 消费；部署的 Redis 版本需不低于 6.2。

## Prompt Resources

Assistant system prompts 位于 `tech_doc_agent/app/agents/prompts/`，由 `manifest.json` 固定 role、稳定 ID、
资源顺序、SHA-256 和 required placeholders。role Python 模块只声明工具组合，不应重新内联 prompt。

有意修改 prompt 时应单独提交：同步升级 prompt ID/hash，运行 `tests/test_prompt_registry.py`，并将模型、数据集或
prompt 变化与纯代码重构分开记录。primary 由 `primary/` 下的有序 section 组合，不要重新合并成单个长文件。

启动 Redis：

```bash
docker compose up -d redis
```

启动后端：

```bash
python -m uvicorn tech_doc_agent.app.api.server:app --reload
```

启动前端：

```bash
cd frontend
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
python -m uvicorn tech_doc_agent.app.api.server:app --host 0.0.0.0 --port 8000
```

访问：

```text
http://127.0.0.1:8000/
```

## Docker

```bash
DEPLOYMENT_COMMIT_SHA=$(git rev-parse HEAD) docker compose up --build
```

PowerShell：

```powershell
$env:DEPLOYMENT_COMMIT_SHA = git rev-parse HEAD
docker compose up --build
```

Compose 会把完整 commit 写入 image runtime identity，先等待 Redis healthy，再启动后端；后端容器使用 `/ready` 做 healthcheck。若不提供 commit，服务仍可启动，但 online eval compatibility 只能标记为 `unverified`。

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
    api/           request facades, delivery workflows, SSE protocol and schemas
    agents/        role definitions, prompts, model factory and execution identity
    application/   commands, ports, use cases and transaction boundaries
    composition.py explicit tools, models, assistants and graph wiring
    core/          settings, tenant, observability, guardrails
    graph/         orchestration specs, execution nodes, routing and policies
    infrastructure/
      persistence/ JSON/Redis/generation repositories and stores
      retrieval/   FAISS, embedding, BM25/vector/RRF and web provider adapters
    runtime/       lifecycle, graph execution, projections and session queries
    tools/         dependency-bound document, learning and profile tools
    services/      controlled retrieval/user-profile compatibility facades only
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

显式 legacy migration 默认 dry-run；apply 会先备份且不删除旧源：

```powershell
python scripts/migrate_legacy_persistence.py --data-path .\tech_doc_agent\data
python scripts/migrate_legacy_persistence.py --data-path .\tech_doc_agent\data --apply
```

不要手工清理非 current generation、processed command、legacy source 或 migration backup。当前生命周期和恢复策略、
以及开放用户数据删除 API 前必须完成的前置条件见 [data-lifecycle.md](data-lifecycle.md)。
