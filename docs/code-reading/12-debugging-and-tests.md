# 12 - 调试、测试与 CI 定位手册

这章的目标不是列出所有 pytest 文件，而是让你从症状快速定位到真正失败的层。先判断失败发生在“进程启动、HTTP/SSE、Graph、工具/依赖、持久化、前端归约还是 CI 环境”，再运行最窄的证据链。

## 本地环境与命令基线

Windows 后端环境：

```powershell
conda activate agent
python --version
python -m pytest --version
```

非交互 shell 若 `conda` 没进 PATH，可直接使用项目已建环境：

```powershell
D:\Tools\miniconda3\envs\agent\python.exe -m pytest
```

尽量用 `python -m pytest/-m ruff/-m mypy`，它能保证工具来自当前 Python 环境，而不是 PATH 中另一个安装。

前端命令必须在 `frontend/`：

```powershell
cd frontend
npm ci
npm run check
npm run test
npm run build
```

仓库根目录没有前端 `package.json`；在根目录跑 `npm ci` 会报找不到 lock/package，而不是前端依赖坏了。

## 先读 CI 的真实门禁

CI 定义：[`/.github/workflows/ci.yml`](../../.github/workflows/ci.yml)。当前分两项 job。

### Backend job

```text
Python 3.12
pip install -r requirements.txt
ruff check tech_doc_agent tests evals
mypy tech_doc_agent/app evals
pytest
context compaction deterministic eval
baseline regression check
```

### Frontend job

```text
Node 22 + npm ci（working-directory=frontend）
npm run check
npm run test
npm run build
安装最小 FastAPI/httpx/pytest
python -m pytest tests/test_frontend_static.py -q --noconftest
```

最后的 `--noconftest` 是有意的。frontend static smoke job 只安装静态服务依赖；若 pytest 自动加载根目录 [`tests/conftest.py`](../../tests/conftest.py)，就会在收集阶段 import 完整 LangChain/LangGraph 后端并因缺依赖失败。不要为“修 CI”把全部后端依赖重新塞进前端 job。

## 推荐的验证梯度

每次修改按风险逐步扩大：

```text
1. import / 单个测试
2. 同模块测试簇
3. contract + architecture tests
4. 全量 backend pytest
5. Ruff + mypy
6. frontend check/test/build
7. deterministic eval gate
8. 必要的手工服务/SSE/审批 smoke
```

先跑目标测试能更快得到因果明确的错误；最终仍要跑全门禁，因为 import locator、eval runner、兼容 facade 和前端 contract 可能不在目标模块测试中。

## 按失败阶段判断

### 阶段 A：pytest collection/import 就失败

典型输出：

```text
ImportError / ModuleNotFoundError
NameError at module import
error during collection
```

优先检查：

1. 当前 Python 是否真是 `agent` env；
2. 移动模块后是否还有旧 import 字符串；
3. `__init__.py` compatibility re-export 是否丢失；
4. type annotation 是否在运行时求值（缺 `from __future__ import annotations`）；
5. optional SDK 是否在 module import 时无条件导入；
6. `tests/conftest.py` 是否给最小环境引入完整依赖。

命令：

```powershell
python -c "import tech_doc_agent.app.api.server"
python -m pytest --collect-only -q
rg -n "旧模块名|旧包名" tech_doc_agent tests evals scripts
```

collection failure 是阻断性的：此时没有任何测试真正执行，不能写成“只有某一条失败”。

### 阶段 B：Ruff 失败

本项目 Ruff 当前只启用 F 系列（未定义/未使用等）。常见重构问题：

- 兼容 re-export 被当 F401；应使用显式 `__all__`/有意 import 形式，而不是全局 ignore；
- 移文件漏 import，F821；
- 删除实现后留下无用 import；
- 分支中局部名字未定义。

命令：

```powershell
python -m ruff check tech_doc_agent tests evals
```

不要用格式化器掩盖 F 系列错误；它们通常反映真实导入/引用问题。

### 阶段 C：Mypy 失败

优先读第一条“拥有 contract 的模块”错误，后续可能是级联。常见原因：

- structural Protocol 与 concrete resource 少字段/签名不同；
- `None` 被当具体对象；
- dict 状态值未收窄；
- LangChain/SDK stub 与运行时参数不一致；
- 新 State/ToolBundle 字段没有全链同步。

命令：

```powershell
python -m mypy tech_doc_agent/app evals
```

不要为了让单文件变绿随意加 `Any` 或 `# type: ignore`。如果错误来自第三方 stub，ignore 要窄到具体行/错误码，并用运行时测试补证据。

### 阶段 D：单测断言失败

先判断是：

- 预期行为变了但 contract 应保持；
- 测试依赖实现细节应更新；
- fake 没跟新 port；
- 全局 cache/ContextVar/环境没有 reset；
- 时间/ID/随机数未注入导致不稳定。

阅读测试的 Arrange 和 fixture，再看 production owner。不要看到旧预期就立刻改测试；先确认外部 contract、现有文档和 API/SSE 是否要求保持。

### 阶段 E：测试通过但 eval regression 失败

算法输出可以类型正确、单元行为正确，却在数据集上质量下降。当前 CI 的 context compaction eval 是 deterministic gate，会生成 candidate manifest/results，再与版本化 baseline 和 policy 比较。

失败时检查：

1. candidate manifest 是否与 baseline 兼容；
2. dataset/prompt/settings/implementation identity 是否变了；
3. 是 completion、answer consistency、压缩率还是其他 threshold；
4. 变更是否有意，需要新 baseline review，而不是直接放宽 policy。

不要把新 candidate 结果直接覆盖 baseline 来“修绿”。baseline 更新是单独的质量决策。

## 服务启动失败怎么查

主链见 [01 - 启动与组装](01-startup-and-composition.md)。从最外层向内：

```text
FastAPI lifespan
  -> build_chat_runtime
  -> RuntimeLifecycle.start
  -> AppResources.create（内部 load/init 各 adapter）
  -> RedisSaver setup
  -> graph compile
```

### Redis 连接/BusyLoading

检查：

```powershell
docker compose ps
Test-NetConnection 127.0.0.1 -Port 6379
```

`RuntimeLifecycle` 只对 Redis `BusyLoading` 类启动错误按 settings 重试；地址错误、认证失败等会分类后失败。看日志中的 dependency/code/cause_type，不要只盯最后一行 lifespan error。

### 本地文件 snapshot 损坏

症状可能是 `learning_state_corrupt` 或 `vector_store_corrupt`。先只读检查：

```powershell
Get-ChildItem -Recurse tech_doc_agent/data/learning_state
Get-ChildItem -Recurse tech_doc_agent/data/faiss_store
Get-Content tech_doc_agent/data/learning_state/current.json
Get-Content tech_doc_agent/data/faiss_store/current.json
```

不要先删 data 目录。确认 manifest 指向的 generation 是否存在、counts 是否对应；损坏是代码问题、旧数据还是人工文件操作，需要保留证据。

### Prompt registry 启动失败

常见是 manifest role set、路径逃逸、SHA 或 placeholder 不一致。运行：

```powershell
python -m pytest tests/test_prompt_registry.py -q
```

修改 prompt 后必须计算并更新 manifest hash；不能关闭校验，因为 execution identity/eval 依赖它。

## HTTP 与 SSE 症状定位

### `/chat` 返回 JSON 而不是 SSE

可能是：

- request schema 422；
- tenant 400；
- high-risk guardrail JSON 4xx；
- route 在建立 StreamingResponse 前失败。

前端 `onopen` 会把 JSON 转为 FatalStreamError。用浏览器 Network 或 curl 查看 status、content-type、body，而不是去查 token reducer。

### SSE 一开始有数据，随后前端报 protocol error

链路：

```text
后端 payload model/translator
  -> encoder wire data
  -> sseContract event list
  -> parseSseMessage
  -> decodeSsePayload
```

开发 console 会给出 `event.field must ...`。先取该 event 的原始 data，对照 [`frontend/src/streaming/ssePayloads.ts`](../../frontend/src/streaming/ssePayloads.ts) 与后端 [`api/sse/payloads.py`](../../tech_doc_agent/app/api/sse/payloads.py)。运行：

```powershell
python -m pytest tests/test_sse_contract.py tests/test_sse_payloads.py tests/test_sse_events.py -q
cd frontend
npm run test -- src/streaming/ssePayloads.test.ts src/streaming/sseReducer.test.ts
```

### 后端日志有 token，前端最终文本重复

检查 `agent_message` 是否被当增量 append。正确语义是 token 追加，agent_message finalContent 覆盖。问题通常在 reducer/action，而不是模型重复生成。

### 流结束但 UI 一直“生成中”

`chatStream.run` finally 必须执行 `setRunning(false)`。检查 transport Promise 是否 resolve、onerror 是否正确 throw、某个 action dispatch 是否同步抛错、refresh 是否永不返回。

### 出现两条错误消息

`setError` 会添加 system message；某些 refresh failure 也单独添加系统消息。先区分“主流失败”和“流成功后状态刷新失败”，不要简单去重所有相同文本，否则会隐藏两次独立失败。

## 审批卡住怎么查

先区分两类 pending：

| 类型 | 存哪里 | 如何开始 | 如何恢复 |
| --- | --- | --- | --- |
| guardrail input | Redis approval repo | `/chat` medium risk | GETDEL 原始输入，再运行或拒绝 |
| sensitive tool | LangGraph checkpoint | interrupt_before ToolNode | `stream(None)` 或注入拒绝 ToolMessage |

排查顺序：

1. GET session state，看 `exists/pending_interrupt/current_agent`；
2. 中风险时确认 approval key/TTL 未过期；
3. 工具审批看 graph snapshot `next` 是否含 interrupt node；
4. tenant+session 是否一致，thread ID 是否串错；
5. 前端是否只丢了 local tool card，但 backend pending 仍存在；
6. `/chat/approve` 返回 `no_pending_interrupt` 还是 error。

相关测试：

```powershell
python -m pytest tests/test_runtime_approvals.py tests/test_redis_approval_repository.py tests/test_chat_runtime_execution.py -q
```

不要通过手工把前端 `pending_interrupt=false` 当修复。那只关抽屉，后端 checkpoint 仍不会接受新消息。

## Graph 行为不对怎么查

### Agent 没有被调用

检查四层：

1. primary model 是否真的产生 `To*Assistant` tool call；
2. tool 是否绑定在 primary definition；
3. route label 是否映射到正确 enter node；
4. dialog stack 和 workflow plan/index 是否允许该步骤。

命令：

```powershell
python -m pytest tests/test_primary_assistant_tools.py tests/test_graph_routes.py tests/test_graph_topology.py -q
```

### Agent 一直重复调用工具

查看 messages 中连续 AI/tool 序列、tool name/args 是否完全一致、policy limit 是否启用。若 tool result 没进入该 Agent 的 message scope，模型看不到结果，会合理地再次调用；这不是只调小 repeat limit 能解决的。

检查：

- `message_scope.py` 是否保留该 step 的 call/result；
- ToolMessage ID 是否与 AI tool call ID 对应；
- `MAX_IDENTICAL_TOOL_REPEATS`；
- parser 的 `PARSER_MAX_RETRIEVAL_CALLS`；
- reflection 是否要求 repair 后却给了相同参数。

### Workflow 提前结束/不推进

看 `finish` 与 `leave` 的区别：finish 应 pop dialog、plan_index+1、写 result；leave 是 escalation/退出并清 plan。检查 subagent 最后一条消息是普通内容还是 `CompleteOrEscalate` tool call，以及 budget/reflection 是否抢占路由。

### Structured result 丢失

parser/relation 的结果来自 markdown heading parser。先确认 Assistant 最终 content 是否符合 heading contract，再看 finish node 的 result key 和 SSE structured_result translator。不要先去改前端 JSON decoder。

## Tool/依赖失败怎么查

Tool error 应至少有：

```text
code, retryable, safe_message, dependency, tool, cause_type
```

从 `dependency` 定位 adapter，从 `retryable` 判断是否经过 retry。日志里查同 trace 的：

```text
retry.attempt
retry.scheduled
retry.final
provider_retry.usage.recorded
tool/node error event
```

### Provider 明明重试了，前端 LLM calls 没增加

embedding/web 的 RetryExecutor 使用 provider retry ledger；LLM calls 在 budget usage。它们不是同一指标。这是预期，不是统计漏记。

### Tool 参数错后没有修复

检查 error code 是否在 ReflectionPolicy `repairable_error_codes`，round 是否达到上限，repair context 是否只含安全 validation location/type。非 repairable error 会要求直接 finalization。

## 检索问题怎么查

### `read_docs` 总是空

依次确认：

1. `FaissStore.documents` 是否为空；
2. `SEED_DOC_STORE_ON_EMPTY` 是否关闭；
3. query/filter normalization 后是什么；
4. BM25 token 是否产生；
5. metadata filter 是否过窄；
6. top_k 是否为 0；
7. 运行的是共享知识库，不要误加 tenant filter。

运行：

```powershell
python -m pytest tests/test_hybrid_retriever.py tests/test_retrieval_rankers.py tests/test_retrieval_metadata.py -q
```

### hybrid 有结果，vector 报 index unavailable

hybrid 会把 typed semantic failure 降级为 exact/BM25；vector 模式不降级。查看 `retrieval.semantic.skipped`。这通常说明 FAISS 未 load/build 或 embedding 失败，而不是结果矛盾。

### `save_docs` 成功后搜不到

检查 add result `added_chunks` 是否 0、save 是否 true、retriever.refresh 是否执行、filter/category 是否匹配。内容为空或切块全空时 `add_documents` 返回 0，不会生成 index。

注意当前 add 后 save 失败可能留下进程内新文档但磁盘旧 snapshot；重启前后结果不同是这一边界的证据。

### Retrieval eval 全 0

先看 corpus 是否实际加载了 documents/chunks。空 corpus 上 runner 可以“全部 case 执行完成”但所有质量指标为 0；这是 diagnostic，不是有效 baseline。

## 持久化问题怎么查

### 学习复习次数重复增加

检查 command 的 `(tenant, session_id, tool_call_id)` 是否稳定，processed_commands 是否随 snapshot 保存，tool 是否绕过 `LearningStateService` 直接调 compatibility `upsert_record + save`。

### 写盘报错后 API 读到新值

正式 learning UoW 应在 save 成功后才替换 `_snapshot`。若出现，通常有人绕过 execute/clone，或多个 UoW 没共享。检查 `AppResources` 是否给 LearningStore、MemoryStore、Service 注入同一个 UoW。

### 重启后数据回到旧版

看 `current.json` 指向哪个 generation、失败前是否仅更新了内存、是否错误读取 legacy fallback。存在 current manifest 时不应回退 legacy。

相关测试：

```powershell
python -m pytest tests/test_learning_state_transaction.py tests/test_generation_store.py tests/test_legacy_persistence_migration.py -q
```

## 前端状态/恢复问题怎么查

### 切换 session 后闪回旧消息

通常是旧 bootstrap HTTP 请求晚到并写 store。确认 AbortController cleanup、resolve 后 `signal.aborted` 二次检查，以及 URL/store tenant+session 三元组一致。

### 同名 session 串数据

检查 transcript key 与 recent session 去重是否包含 tenant。不能只比较 session ID。

### 刷新后工具卡片消失但聊天还在

后端 history mapper 不完整重建 toolCallIds/events；local transcript 损坏、被清理或 version 变化时会退回普通 history。这不是后端 message 丢失。若产品要求完整恢复，需要扩展 history contract。

### localStorage 写失败

核心聊天仍可继续，开发 console 有 storage warning。检查 quota、隐私模式、序列化大小和循环/不可序列化字段。不要让体验缓存失败升级为流失败。

## 测试按领域索引

| 领域 | 首选测试 |
| --- | --- |
| 启动/DI | `test_bootstrap.py`, `test_composition.py`, `test_resources.py`, `test_runtime_lifecycle.py` |
| API/schema | `test_api_schemas.py`, `test_health_routes.py`, `test_sse_*` |
| Runtime/session | `test_chat_runtime_*`, `test_runtime_approvals.py` |
| Graph | `test_graph_compile.py`, `test_graph_topology.py`, `test_graph_routes.py`, `test_graph_*` |
| Agent/prompt | `test_assistant_*`, `test_prompt_registry.py`, role tool tests |
| 工具/application | `test_tool_bundle.py`, profile/learning tests |
| Persistence | atomic/generation/transaction/legacy/repository contract tests |
| Retrieval | ranker/hybrid/metadata/contracts/eval runner tests |
| 横切 | tenant/error/retry/budget/redaction/observability/context tests |
| Architecture | `test_architecture_dependencies.py`, `test_architecture_import_graph.py` |
| Frontend contract | `test_frontend_rest_contract.py`, `test_sse_contract.py`, frontend Vitest |

完整逐文件映射见 [14 - 源码与测试索引](14-source-and-test-index.md)。

## 一套可复制的本地全门禁

在仓库根目录：

```powershell
conda activate agent
python -m ruff check tech_doc_agent tests evals
python -m mypy tech_doc_agent/app evals
python -m pytest
python -m evals.run_context_compaction_eval --iterations 10 --output eval_results/context_compaction_pr.jsonl --report eval_reports/context_compaction_pr.md --manifest eval_results/context_compaction_pr.manifest.json
python -m evals.check_result_regression --baseline-manifest evals/baselines/context_compaction_v1/manifest.json --baseline-results evals/baselines/context_compaction_v1/results.jsonl --candidate-manifest eval_results/context_compaction_pr.manifest.json --candidate-results eval_results/context_compaction_pr.jsonl --policy evals/policies/context_compaction_pr_v1.json
```

前端：

```powershell
Push-Location frontend
npm ci
npm run check
npm run test
npm run build
Pop-Location
```

`npm ci` 会按 lock file 重建依赖，日常仅改源码时可复用已有 `node_modules` 直接跑后三项；提交前/CI 对齐时再跑 `npm ci`。

## 报告失败时要保留的证据

一份可行动的失败报告至少包括：

- 精确命令和 working directory；
- Python/Node 版本与环境；
- 第一条真实 error，不只贴末尾 summary；
- 是 collection、test、build 还是 eval gate；
- passed/failed/warnings 数；
- 是否可本地复现；
- 相关 trace/session/event/tool/dependency code（去敏后）；
- 工作树是否有未提交修改。

不要把 deprecation warning、pytest cache permission warning 与断言失败混成同一种问题；也不要因某个 job 绿色就宣称整个 CI 绿色。

下一章 [13 - 兼容层与迁移](13-compatibility-and-migration.md) 解释为什么重构后仍保留少数旧 import/path，以及什么证据足够让它们删除。
