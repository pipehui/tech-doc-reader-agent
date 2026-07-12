# 长期维护文档与运行时契约对齐

## 背景

大规模重构日志准确记录了各提交当时的阶段状态，但长期维护文档仍有部分内容停留在较早 contract：后端、前端和 golden examples 已支持完整 SSE payload，`docs/api.md` 却没有同步全部 event/field；Graph state、依赖方向和包地图也漏掉后续拆出的所有者。代码测试全绿并不能证明说明文档仍与 wire contract 一致。

本批只修改面向当前 checkout 的长期文档和导航。`docs/refactoring/` 中既有阶段日志不回写成最终状态；后续日志中的反向链接已经负责说明所有权迁移，保留原文才能继续用于追溯。

## 审计事实源

本次逐项对照以下可执行事实，而不是根据旧 TODO 猜测：

- REST request/response：`api/routes/*` 与 `api/schemas.py`；
- SSE event/field：`api/sse/contract.py`、`payloads.py`、`update_translator.py` 和 `streaming.py`；
- Graph state：`core/state.py`；
- 分层边界：`tests/test_architecture_dependencies.py` 的递归 import contracts；
- 包职责：当前 tracked source tree 与各 package public exports；
- 安装/验证命令：`requirements.txt`、`frontend/package.json`、lockfile 和 CI；
- 本地链接：README 与 `docs/**/*.md` 的相对路径解析。

## 发现与修改

### 1. API 参考落后于 SSE contract

`docs/api.md` 原来缺少 `usage_update`、`budget_started`、`budget_terminated`、`context_metrics_update` 四种已上线事件，也没有完整描述 session snapshot/state 的 budget/context 字段、显式 tool error 字段和 terminal error envelope。

处理：补齐 18 种事件及其 event-specific payload；同步历史压缩摘要、`profile_version`、memory tenant 字段和 guardrail source。删除 Pydantic `extra="forbid"` contract 中不存在的预留 `agent_transition.from`。

### 2. 文档漂移没有自动门禁

既有测试已锁定 backend、frontend 和 golden examples 三方事件集合，但 `docs/api.md` 不在 contract 内，所以新增事件时可以静默漏写文档。

处理：`tests/test_sse_contract.py` 现在同时解析 API 文档的 SSE 章节：

- event heading 集合必须与 `SSE_EVENT_NAMES` 完全相等；
- 去除四个通用 trace-context 字段后，每个 Markdown 表格字段必须与对应 Pydantic payload model 完全相等；
- 文档多写一个后端禁止的字段或少写一个现有字段都会失败。

### 3. 架构和模块地图漏掉后续拆分

`docs/architecture.md` 的 State 列表缺少 reflection、execution budget、context metrics/summary 与 provider retry ledger；依赖表也没有写出 API 对 application contract/use case 的合法方向。`tech_doc_agent/README.md` 的核心树没有覆盖新的 SSE、graph policy 和 persistence owner。

处理：按变化轴分组列出 State 字段，说明 delta 与累计账本的关系；按递归 architecture contract 修正允许方向；补齐主要模块所有者及职责说明。

### 4. 上手命令和能力表述不够可复现

Quickstart 使用 `npm install`，没有安装 backend requirements，且 `PYTHONPATH=...` 的 shell 写法不能直接用于 PowerShell。README 还把 `IndexFlatL2` 的精确距离计算错误写成“召回率 100%”。

处理：统一为 `python -m pip`、`python -m uvicorn`、`npm ci`，补充 PowerShell 复制命令和文档导航；将 FAISS 限制改为准确的 exact Top-K distance / linear latency 说明，不再把索引算法性质冒充 retrieval quality metric。

## 验证

在 Windows `agent` conda 环境执行：

- `python -m pytest -q`：718 passed，4 warnings；
- `python -m ruff check .`：passed；
- `python -m mypy tech_doc_agent/app evals`：162 source files，0 issues；
- frontend Vitest：20 files / 85 tests passed；
- frontend TypeScript check：passed；
- frontend production build：2042 modules transformed；
- `npm audit`：0 vulnerabilities；
- Markdown 本地链接检查：无失效目标；
- `git diff --check`：passed。

四条 warning 仍是三条第三方 deprecation warning 和一条本机 `.pytest_cache` permission warning，不影响测试结论。

## 后续约束

- 新增或删除 SSE event/field 时，同一提交必须更新 backend model、frontend decoder、golden example 和 `docs/api.md`。
- 当前行为/接口文档以长期维护文档为准；阶段日志只描述历史提交，不应被批量重写成当前状态。
- 架构依赖方向发生变化时，先修改可执行 import contract，再同步架构表；不能只改说明文字。
- 评测质量结论必须绑定可验证 manifest/corpus；exact vector search、runner 成功或空 corpus 全零都不能单独证明召回质量。
