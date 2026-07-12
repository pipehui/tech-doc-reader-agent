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

## 本地任务单策略

`docs/todo/` 是本地执行清单，受 `.gitignore` 保护，不进入待推送提交。可共享、可审计的实施事实统一记录在本目录，避免远端文档索引指向不存在的本地文件。
