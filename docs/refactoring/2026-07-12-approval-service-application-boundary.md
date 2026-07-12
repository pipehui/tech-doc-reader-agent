# Phase 4 重构日志：Approval Application Service 与 Runtime Projection 边界

## 1. 重构范围

`runtime/approvals.py` 原来的 `ApprovalService` 同时处理两类不同职责：

- application use case：tenant thread key、request/get/has/pop、requested/resolved telemetry；
- runtime projection：把拒绝的 guardrail approval 转为带 `AIMessage` 的 LangGraph update part。

前一类只依赖 approval repository/domain model 和 core tenant/observability；后一类必须依赖 LangChain message。共置会迫使纯 repository 用例位于 runtime，并让 application 归位只完成一半。

本批形成以下边界：

```text
application/approval_models.py
  -> application/approval_service.py       # repository use case + telemetry

runtime/execution.py
  -> application/approval_service.py       # pop/log resolved
  -> runtime/approval_projection.py        # rejected request -> AIMessage update

runtime/approvals.py                        # old import/method compatibility only
```

`ChatRuntime` 与 `GraphExecutionService` 主路径都直接 import application `ApprovalService`，不再经过 runtime compatibility module。

## 2. 职责归属

### Application ApprovalService

- 使用共享 `tenant_thread_id()` 生成 repository key；
- 通过 `GuardrailApprovalRequest.create()` 建模；
- request/get/has/pop pending guardrail approval；
- 记录 requested/resolved event，日志只含 risk/finding/feedback length 等安全字段；
- 不 import LangChain、LangGraph、FastAPI、runtime 或 infrastructure。

### Runtime approval projection

`guardrail_rejection_part()` 只负责把已解析的 request + feedback 映射为既有：

```text
("updates", {"guardrail": {"messages": [AIMessage(...)]}})
```

它不读写 repository、不解析 tenant、不决定 approve/reject 流程。是否 replay 原输入、是否继续 tool interrupt、何时结束 operation 仍由 `GraphExecutionService` 统一编排。

## 3. 实际遇到的问题与解决

### 问题 A：兼容的不只是 model type，还有 service method

历史日志明确承诺 `runtime.approvals.ApprovalRepository` / `GuardrailApprovalRequest` 暂时可 import；同时旧 `ApprovalService` 公开了 `rejection_part()`。若直接把 service 移到 application 并删除 runtime 文件，仓内主路径虽可通过，潜在仓外调用却会同时失去 constructor path 和 method。

解决：`runtime/approvals.py` 缩成 compatibility wrapper：

- 继承 application `ApprovalService`，constructor 与 repository methods 保持；
- `rejection_part()` 只委托 `runtime.approval_projection.guardrail_rejection_part()`；
- 继续 re-export 既有 request/repository type 名；
- production ChatRuntime/execution 与仓内 service 测试全部改用新事实源。

新增兼容测试真实构造旧 service path、创建 request 并调用 `rejection_part()`，不是只检查 import 字符串。

### 问题 B：不能把 AIMessage 送进 application

把整个旧 class 原样移动到 application 会使 application 依赖 `langchain_core.messages.AIMessage`，违反递归 dependency contract，也会把 graph state shape 变成 repository service 的职责。

解决：application source 由 architecture test 明确禁止 `AIMessage`/`langchain_core`；projection source 必须拥有 `guardrail_rejection_part` 和 `AIMessage`。这使边界可执行，而不只依赖文档。

### 问题 C：Approval 与 tool HITL 不能因为共用 endpoint 就合并 resume strategy

Guardrail approval 通过 repository pop 取回原始输入；tool HITL 则读取 LangGraph snapshot 的 pending node，并写入 ToolMessage 后 resume。两者在 `/chat/approve` 共享传输入口，但 domain state 和恢复策略不同。

解决：本批只移动 repository use case 与 projection，不设计一个 optional 字段泛滥的通用 approval command。`GraphExecutionService` 仍按 pending guardrail 优先、否则 graph interrupt 的既有顺序处理。

## 4. 兼容与不变项

- Redis key、TTL envelope、GET/GETDEL 和一次性 resolve 不变；
- in-memory/Redis repository contract 不变；
- tenant isolation、request payload 与 telemetry 字段不变；
- approve 时原输入 replay、reject 时 agent message、tool rejection ToolMessage 均不变；
- sync/async stream 使用同一个 `_stream_approval` 状态机；
- `runtime.approvals` 兼容 wrapper 不允许新增业务逻辑，删除需明确 deprecation 或仓外 import 审计。

## 5. 架构守卫

- application service 必须定义 `ApprovalService` 且不得出现 LangChain 类型；
- runtime projection 必须独占 `AIMessage` rejection mapping；
- ChatRuntime/execution 必须直接 import application service；
- production source 不得 import `runtime.approvals`；
- Redis/InMemory adapters 继续只依赖 application model/port，不依赖 runtime。

## 6. 验证结果

| 检查 | 结果 |
|---|---|
| Approval/Redis/runtime/SSE/architecture 定向测试 | 99 passed，4 warnings |
| Approval/runtime targeted mypy | passed，5 个 source files |
| 全量 pytest | 716 passed，4 warnings |
| 全仓 Ruff | passed |
| app + evals mypy | passed，159 个 source files |
| 前端 Vitest | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | passed，2042 modules transformed |
| npm audit | 0 vulnerabilities |

四条 pytest warning 中三条是 LangGraph/LangChain/Starlette 第三方弃用提示，另一条是本机 `.pytest_cache` 无写权限；测试用例自身全部通过。

## 7. 后续约束

- API-facing pending approval view 可以统一 response shape，但 guardrail/tool resume strategy 继续独立。
- Application service 不得开始返回 SSE、LangGraph part 或 `AIMessage`。
- 若未来增加其他 approval kind，先定义明确 domain model/repository ownership，不把所有状态塞入当前 guardrail request。
- Runtime compatibility wrapper 只减不增。

本批提交主题：`refactor: separate approval service and projection`。
