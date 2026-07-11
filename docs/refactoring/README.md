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

## 本地任务单策略

`docs/todo/` 是本地执行清单，受 `.gitignore` 保护，不进入待推送提交。可共享、可审计的实施事实统一记录在本目录，避免远端文档索引指向不存在的本地文件。
