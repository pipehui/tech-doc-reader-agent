# 重构记录

本目录记录大规模重构的实际过程，不只保存最终设计。每个阶段都应回答：

1. 重构了什么，以及为什么先处理这里。
2. 实施中遇到了哪些与预期不同的问题。
3. 最终采用什么方案，放弃了什么方案。
4. 运行了哪些验证，哪些验证因为环境或外部依赖未执行。
5. 对后续阶段新增了哪些约束或 TODO。

## 日志索引

| 日期 | 阶段 | 状态 | 文档 |
|---|---|---|---|
| 2026-07-11 | Phase 0：基线、依赖方向、User Profile namespace 隔离 | 首批完成，Phase 0 继续 | [2026-07-11-phase-0.md](2026-07-11-phase-0.md) |
| 2026-07-11 | Phase 0：统一原子 JSON 持久化 | 完成 | [2026-07-11-atomic-json.md](2026-07-11-atomic-json.md) |
| 2026-07-11 | Phase 0：Graph topology/router/interrupt 行为刻画 | 完成 | [2026-07-11-graph-characterization.md](2026-07-11-graph-characterization.md) |
| 2026-07-11 | Phase 1：Graph package 与 AgentSpec 注册 | 完成 | [2026-07-11-agent-spec.md](2026-07-11-agent-spec.md) |
| 2026-07-11 | Phase 1：拆分 graph nodes/tool policy/tool execution | 完成 | [2026-07-11-graph-utils-split.md](2026-07-11-graph-utils-split.md) |
| 2026-07-11 | Phase 1：SSE contract/translator/encoder 与 chat route 解耦 | 完成 | [2026-07-11-sse-boundary.md](2026-07-11-sse-boundary.md) |
| 2026-07-11 | Git：本地任务单与待推送历史隔离 | 完成 | [2026-07-11-local-todo-policy.md](2026-07-11-local-todo-policy.md) |
| 2026-07-11 | Phase 2：runtime config/serialization/session query 拆分 | 完成 | [2026-07-11-runtime-query-split.md](2026-07-11-runtime-query-split.md) |
| 2026-07-11 | Phase 2：execution/telemetry/approval service 与 sync/async 收敛 | 完成 | [2026-07-11-runtime-execution-split.md](2026-07-11-runtime-execution-split.md) |
| 2026-07-11 | Phase 2：Redis durable guardrail approval 与 production bootstrap | 核心恢复/原子消费完成，安全审计继续 | [2026-07-11-durable-guardrail-approval.md](2026-07-11-durable-guardrail-approval.md) |
| 2026-07-11 | Phase 2：resources/checkpointer/graph lifecycle 拆分 | 完成，global resource locator 继续 | [2026-07-11-runtime-lifecycle.md](2026-07-11-runtime-lifecycle.md) |
| 2026-07-11 | Phase 2：显式 resources/tools/models/assistants/graph 依赖注入 | 完成 | [2026-07-11-resource-injection.md](2026-07-11-resource-injection.md) |
| 2026-07-11 | Phase 3：retrieval taxonomy/normalization/filter/inference 拆分 | 完成，60-case corpus baseline 待准备 | [2026-07-11-retrieval-metadata-split.md](2026-07-11-retrieval-metadata-split.md) |
| 2026-07-11 | Phase 3：BM25/semantic/exact/RRF/formatter 拆分 | 完成，真实 corpus 质量评测待补 | [2026-07-11-retrieval-rankers-split.md](2026-07-11-retrieval-rankers-split.md) |
| 2026-07-11 | Phase 3：legacy frontend 与 production dist 静态边界 | 完成，Docker image build 待有 daemon 环境复核 | [2026-07-11-frontend-static-boundary.md](2026-07-11-frontend-static-boundary.md) |
| 2026-07-11 | Phase 3：前端类型化 SSE parser/reducer/store adapter | 完成，tool status 协议化继续 | [2026-07-11-frontend-sse-reducer.md](2026-07-11-frontend-sse-reducer.md) |
| 2026-07-11 | Phase 3：前端 transcript repository 与安全 storage port | 完成，Store slices 继续 | [2026-07-11-frontend-transcript-repository.md](2026-07-11-frontend-transcript-repository.md) |
| 2026-07-11 | Phase 3：前端 session/preference repositories | 完成，Store raw storage policy 清零 | [2026-07-11-frontend-session-preference-repositories.md](2026-07-11-frontend-session-preference-repositories.md) |
| 2026-07-11 | Phase 3：前端 Zustand session/transcript/trace/learning/ui slices | 完成，根 Store 收敛为 composition facade | [2026-07-11-frontend-store-slices.md](2026-07-11-frontend-store-slices.md) |
| 2026-07-12 | Phase 3：前端可取消 session bootstrap、路由模型与 Topbar 边界 | 完成，App feature components 继续拆分 | [2026-07-12-frontend-session-bootstrap.md](2026-07-12-frontend-session-bootstrap.md) |
| 2026-07-12 | Phase 3：AppRouter 与 Landing/Studio/Chat/Approval/Learner/Inspector feature 边界 | 完成，根 App 收敛为 6 行 facade | [2026-07-12-frontend-feature-boundaries.md](2026-07-12-frontend-feature-boundaries.md) |
| 2026-07-12 | Phase 3：shared REST client、运行时 response decoder 与 FastAPI schema 漂移门禁 | 完成，F4 API/session bootstrap 收敛完成 | [2026-07-12-frontend-rest-api-boundary.md](2026-07-12-frontend-rest-api-boundary.md) |
| 2026-07-12 | Phase 3：ToolMessage -> SSE -> reducer/Inspector 显式 success/error 协议 | 完成，删除自然语言错误启发式 | [2026-07-12-explicit-tool-result-status.md](2026-07-12-explicit-tool-result-status.md) |
| 2026-07-12 | Phase 3：React component、tenant router 与 fake SSE/HITL integration 测试层 | 完成，F5 六层测试门禁完成 | [2026-07-12-frontend-component-integration-tests.md](2026-07-12-frontend-component-integration-tests.md) |
| 2026-07-12 | Phase 3：CSS tokens/shell/chat/approval/learner/inspector/landing/responsive 边界 | 完成，2023 行规则与 production CSS hash 不变 | [2026-07-12-frontend-css-boundaries.md](2026-07-12-frontend-css-boundaries.md) |
| 2026-07-12 | Phase 4：统一错误分类、provider/repository 映射与安全 ToolMessage/SSE 边界 | 完成，R1 retry 与 R4 业务字段脱敏继续 | [2026-07-12-unified-error-model.md](2026-07-12-unified-error-model.md) |
| 2026-07-12 | Phase 4：日志、Langfuse、eval/benchmark artifact 共享 redaction 与 keyed pseudonym | 当前出口完成，R6 replay 接入继续 | [2026-07-12-shared-redaction-policy.md](2026-07-12-shared-redaction-policy.md) |
| 2026-07-12 | Phase 4：FAISS generation snapshot、原子 manifest 与内存候选发布 | 完成，multi-worker lock 与 generation GC 继续 | [2026-07-12-faiss-snapshot-generations.md](2026-07-12-faiss-snapshot-generations.md) |
| 2026-07-12 | Phase 4：LearningState command/service/UoW、组合 generation 与 tool-call 幂等 | 完成，multi-worker 与 retention 继续 | [2026-07-12-learning-state-transaction.md](2026-07-12-learning-state-transaction.md) |
| 2026-07-12 | Phase 4：Tenant strict parse、legacy normalize 与共享 HTTP resolver | 完成，真实 AuthN/AuthZ 仍属 D6 | [2026-07-12-strict-tenant-parsing.md](2026-07-12-strict-tenant-parsing.md) |
| 2026-07-12 | Phase 4：ToolExecutionPolicy 配置注入、显式 decision 与统一阻断 telemetry | 完成，动态/分级预算仍需独立设计 | [2026-07-12-tool-policy-decisions.md](2026-07-12-tool-policy-decisions.md) |
| 2026-07-12 | Phase 4：Typed SearchQuery/SearchResult、兼容 facade 与原子 BM25 snapshot | 完成，真实 corpus 基线仍待准备 | [2026-07-12-typed-retrieval-snapshot.md](2026-07-12-typed-retrieval-snapshot.md) |
| 2026-07-12 | Phase 4：Package-resource PromptRegistry、manifest 校验与 primary 分段 | 完成，model ID/eval identity 仍待接入 | [2026-07-12-prompt-registry.md](2026-07-12-prompt-registry.md) |
| 2026-07-12 | Phase 4：LearningRecord/MemoryFragment 领域模型与 JSON 交付边界 | learning/memory 完成，profile/approval 继续 | [2026-07-12-learning-domain-models.md](2026-07-12-learning-domain-models.md) |
| 2026-07-12 | Phase 4：UserProfile 领域模型、application service 与 versioned repository | profile 完成，approval 分层归位继续 | [2026-07-12-user-profile-domain.md](2026-07-12-user-profile-domain.md) |
| 2026-07-12 | Phase 4：Approval request/port 归位与 Redis adapter 解耦 | domain model 子项完成，migration/retention 继续 | [2026-07-12-approval-domain-boundary.md](2026-07-12-approval-domain-boundary.md) |
| 2026-07-12 | Phase 4：显式 legacy persistence dry-run/backup/migration/report | 完成，repository contract/retention 继续 | [2026-07-12-explicit-legacy-migration.md](2026-07-12-explicit-legacy-migration.md) |
| 2026-07-12 | Phase 4：Learning/Profile/Approval 可复用 repository contract suites | 完成，retention 策略继续 | [2026-07-12-repository-contract-suites.md](2026-07-12-repository-contract-suites.md) |
| 2026-07-12 | Phase 4：数据生命周期、GenerationInventory 与 processed-command ownership | D7 完成，Auth/GC/恢复演练继续 | [2026-07-12-data-lifecycle-policy.md](2026-07-12-data-lifecycle-policy.md) |
| 2026-07-12 | Phase 4：统一有限 Transport Retry、Retry-After 与 provider attempt telemetry | transport 完成，Reflection/ExecutionBudget 继续 | [2026-07-12-transport-retry-policy.md](2026-07-12-transport-retry-policy.md) |
| 2026-07-12 | Phase 4：有限 Reflection、参数修复状态机与 recovery fault metrics | Reflection 完成，ExecutionBudget 继续 | [2026-07-12-reflection-policy.md](2026-07-12-reflection-policy.md) |
| 2026-07-12 | Phase 4：Workflow BudgetUsage、真实 token metadata、versioned price table 与 usage SSE | 计量完成，强制预算继续 | [2026-07-12-budget-usage-accounting.md](2026-07-12-budget-usage-accounting.md) |
| 2026-07-12 | Phase 4：ExecutionBudget、request deadline、resume recheck 与确定性 partial termination | 强制预算完成，provider-level retry 明细继续 | [2026-07-12-execution-budget-enforcement.md](2026-07-12-execution-budget-enforcement.md) |
| 2026-07-12 | Phase 4：ContextMetrics、checkpoint/prompt bytes 与 provider input-token 分桶 | 观测基础完成，安全 compaction 继续 | [2026-07-12-context-metrics.md](2026-07-12-context-metrics.md) |
| 2026-07-12 | Phase 4：闭合历史压缩、版本化 ConversationSummary 与历史投影 | 机制完成且默认关闭，长会话 eval 后再决定启用 | [2026-07-12-safe-context-compaction.md](2026-07-12-safe-context-compaction.md) |
| 2026-07-12 | Phase 4：长会话 compaction off/on 离线 recall/size/token-proxy 评估 | 离线基线完成，发现 raw-tool-only 信息损失，继续默认关闭 | [2026-07-12-context-compaction-eval.md](2026-07-12-context-compaction-eval.md) |
| 2026-07-12 | Phase 4：Mypy check-untyped-defs 与 app/evals 全范围 CI gate | 136 个源文件全绿，strict 分项继续 | [2026-07-12-mypy-full-app-gate.md](2026-07-12-mypy-full-app-gate.md) |
| 2026-07-12 | Phase 4：SSE 逐事件 Pydantic/TypeScript payload contract、golden parity 与异常策略 | 17 种 event 双端运行时校验完成，schema/codegen 后续评估 | [2026-07-12-sse-payload-contract.md](2026-07-12-sse-payload-contract.md) |
| 2026-07-12 | Phase 4：FastAPI async runtime surface 与 CLI sync facade 分界 | route 同步重复编排删除，native async graph benchmark 后续独立执行 | [2026-07-12-fastapi-async-runtime-boundary.md](2026-07-12-fastapi-async-runtime-boundary.md) |
| 2026-07-12 | Phase 4：Graph finish 行为契约与 CompletionPolicy 值对象收口 | 五个 Agent 终态更新锁定，B7 provider spike 继续 | [2026-07-12-graph-finish-contract.md](2026-07-12-graph-finish-contract.md) |
| 2026-07-12 | Phase 4：Assistant execution identity 与 prompt/model trace metadata | trace 子项完成，远程 eval identity manifest 继续 | [2026-07-12-assistant-execution-identity.md](2026-07-12-assistant-execution-identity.md) |
| 2026-07-12 | Phase 4：Versioned runtime identity manifest 与 default-off 诊断端点 | 服务端事实源完成，eval artifact 消费继续 | [2026-07-12-runtime-identity-manifest.md](2026-07-12-runtime-identity-manifest.md) |
| 2026-07-12 | Phase 4：Online eval run manifest、远端 identity 握手与安全 settings fingerprint | online eval 完成，offline runners 继续 | [2026-07-12-online-eval-run-manifest.md](2026-07-12-online-eval-run-manifest.md) |
| 2026-07-12 | Phase 4：Retrieval/context-compaction shared run manifest 与 `not_applicable` identity | offline runners 完成，corpus fingerprint 继续 | [2026-07-12-offline-eval-run-manifest.md](2026-07-12-offline-eval-run-manifest.md) |
| 2026-07-12 | Phase 4：Eval baseline compatibility、retrieval corpus 内容指纹与 retrieval-only composition | 基础门禁完成，真实 corpus/metrics threshold 继续 | [2026-07-12-eval-baseline-compatibility.md](2026-07-12-eval-baseline-compatibility.md) |
| 2026-07-12 | Phase 4：Runtime schema v2 deployment commit identity 与 Docker build metadata | 代码/契约完成，实际部署需注入 commit | [2026-07-12-runtime-deployment-identity.md](2026-07-12-runtime-deployment-identity.md) |
| 2026-07-12 | Phase 4：Context-compaction tracked baseline、双阈值 regression comparator 与 PR CI gate | deterministic gate 完成，retrieval/live gate 继续 | [2026-07-12-context-compaction-pr-regression-gate.md](2026-07-12-context-compaction-pr-regression-gate.md) |
| 2026-07-12 | Phase 4：Embedding/Web provider retry request ledger、SSE/REST/Inspector 与 online eval | provider attempt 明细完成，LLM 继续使用既有 budget usage | [2026-07-12-provider-retry-usage.md](2026-07-12-provider-retry-usage.md) |
| 2026-07-12 | Phase 4：递归 Python import graph、分层 architecture contracts 与 message_scope 归位 | 六组稳定边界已阻断，ChatRuntime/services 收口继续 | [2026-07-12-recursive-architecture-contracts.md](2026-07-12-recursive-architecture-contracts.md) |
| 2026-07-12 | Phase 4：ChatRuntime 纯注入 facade 归位、runtime identity port 与 API -> runtime | facade 边界完成，services 其他混合职责继续 | [2026-07-12-chat-runtime-facade-boundary.md](2026-07-12-chat-runtime-facade-boundary.md) |
| 2026-07-12 | Phase 4：Retrieval application contract、内部 ranker DTO 分离与 tools -> services 清零 | 查询/结果 port 下沉完成，真实 corpus 基线继续 | [2026-07-12-retrieval-application-contract.md](2026-07-12-retrieval-application-contract.md) |
| 2026-07-12 | Phase 4：Agents 顶层 package、prompt 资源无损迁移与双向依赖门禁 | role/prompt/model/identity 已迁出 services，旧路径不保留 | [2026-07-12-agents-package-boundary.md](2026-07-12-agents-package-boundary.md) |

## 本地任务单策略

`docs/todo/` 是本地执行清单，受 `.gitignore` 保护，不进入待推送提交。可共享、可审计的实施事实统一记录在本目录，避免远端文档索引指向不存在的本地文件。
