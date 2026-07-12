# Approval request/port 归位与 Redis adapter 解耦

## 本批目标

durable guardrail approval 已有不可变 `GuardrailApprovalRequest`、repository protocol、Redis TTL envelope 与原子 GETDEL，
但 request 和 port 定义在 `runtime/approvals.py`。结果是 infrastructure 的 Redis adapter 反向 import runtime；request
字段校验又手写在 Redis `_deserialize()` 中，进程内 repository 与 runtime 构造路径无法复用同一 payload contract。

本批完成 D7 的 approval 子范围：把 request model 与 repository port 归位到 application；集中 request factory、tenant
校验与 payload serialization；让 Redis 和 runtime 共同依赖 application。审批 service、SSE resume、TTL、envelope 和
GETDEL 行为保持不变。

## 最终边界

### 1. Application 拥有 approval domain contract

`application/approval_models.py` 定义：

- frozen/slots `GuardrailApprovalRequest`；
- `ApprovalRepository` port；
- `ApprovalRequestPayloadError`；
- `create()`：从已经严格解析的 `TenantContext` 和 findings sequence 建模；
- `from_payload()/to_payload()`：Redis/未来 adapter 共用的 request 边界；
- `tenant` property：从模型恢复经过验证的 tenant value object。

模型验证所有 request 文本字段、findings tuple 及 user/namespace。Redis 中即使出现字段类型正确但 tenant 含路径穿越
字符的旧/恶意 payload，也会作为 corrupt approval data 拒绝，不会进入 runtime。

`to_payload()` 为 findings 创建新 list；外部修改 JSON payload 不会影响 frozen request。

### 2. Runtime 只实现审批执行语义

`runtime/approvals.py` 不再定义 model/port，只保留：

- process-local `InMemoryApprovalRepository` adapter；
- `ApprovalService` 的 key、put/get/pop、rejection graph update 与 telemetry。

service 使用 `GuardrailApprovalRequest.create()`，不再逐字段构造另一个隐式版本。为兼容已有仓内/仓外 import，runtime
模块暂时 re-export `ApprovalRepository / GuardrailApprovalRequest`；定义源只有 application，架构测试禁止 class 再移回
runtime。

`ChatRuntime` 的构造参数和公开返回 annotation 直接 import application types；默认进程内 adapter 与 ApprovalService
仍从 runtime 获取。本批没有改变默认 adapter 或 production bootstrap。

### 3. Redis adapter 不再反向依赖 runtime

`infrastructure/persistence/approval_repository.py` 改为 import application model。写入 envelope 时调用
`request.to_payload()`，读取完成 lifecycle 验证后调用 `GuardrailApprovalRequest.from_payload()`；model error 映射回既有
`ApprovalRepositoryDataError`。

以下 Redis contract 保持原样：

```text
schema_version = 1
status = pending
created_at / expires_at = timezone-aware ISO timestamp
SET key payload EX ttl
GETDEL key for one-shot resolve
```

transport error 仍经统一 error model 映射，连接字符串/密码不出现在安全异常；repository 仍拥有 client close。

### 4. 架构门禁

新增门禁同时断言：

- runtime approvals 不定义 `GuardrailApprovalRequest` 或 `ApprovalRepository(Protocol)`；
- Redis adapter import application approval model；
- Redis adapter 不出现任何 `tech_doc_agent.app.runtime` dependency。

application 现有 dependency gate 也覆盖新文件，禁止 domain model 反向 import runtime/infrastructure/services/tools/api。

## 实施中遇到的问题

### 问题 A：已有 dataclass 不代表依赖方向正确

TODO 表面上要求“为 approval 定义 domain model”，而代码确实已有 frozen dataclass。若只把清单勾完，会忽略
infrastructure -> runtime 的反向依赖和 adapter 内第二套字段 contract。

处理：不重写业务流程，只移动定义源并让两侧共同依赖 application；用架构测试固定方向。D7 父项在该边界落地后
才关闭。

### 问题 B：直接删除 runtime import 会造成无必要兼容破坏

现有测试与潜在调用方通过 `runtime.approvals.GuardrailApprovalRequest` 导入类型。定义归位不要求同一提交强迫所有仓外
调用方切换路径。

处理：runtime 做显式 re-export，但源码中不重复定义。新 Redis 测试改从 application 导入，证明主路径已经迁移；
后续可以按弃用窗口移除 re-export。

### 问题 C：asdict 很方便，但会绕开模型的外部 contract

Redis 原 `_serialize()` 使用 `asdict(request)`，新增字段会自动落盘，既没有 schema 评审，也没有 detached payload
保证；deserialize 又维护一份 required field tuple。

处理：使用显式 `to_payload/from_payload`。字段变更必须修改模型 adapter 和测试，storage schema 不会因 dataclass
内部字段调整而意外漂移。

### 问题 D：全导入 mypy 暴露了本批之外的既有错误

对 `chat_runtime.py` 运行递归 mypy 时，检查继续进入未修改的 `message_scope.py` 与 `assistant_base.py`，触发现有 State/
LangChain message annotation 错误；审批四个改动文件本身无错误。

处理：CI 既有 12-file mypy gate继续完整运行；本批 direct gate 使用此前跨层改动采用的 `--follow-imports=skip` 验证
四个修改文件，另对 application/runtime approval/Redis 三个核心文件运行普通 mypy并通过。没有为让本批变绿而顺手改
无关模块。

## 测试与门禁

新增/扩展覆盖：

- create/payload round-trip、findings copy 与 tenant property；
- 非 list findings、非字符串字段、非法 tenant payload；
- Redis envelope/TTL/created/expires 字段完全不变；
- 两个 repository 对同 key 的并发 GETDEL 只有一个成功；
- 两个 runtime 共享 Redis request 并一次性 resolve；
- transport error 安全映射、未知 schema/corrupt JSON、factory lazy client 与 close；
- runtime sync/async approval、bootstrap/lifecycle 与架构方向。

| 验证 | 结果 |
|---|---|
| approval/Redis/runtime/bootstrap/lifecycle/architecture 聚焦 pytest | 50 passed |
| 全量后端 pytest（禁用本机不可写 cache） | 416 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，4 source files |
| approval 三个核心文件普通 mypy | passed，3 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式变化，因此不重复浏览器视觉 smoke。三条 pytest warning 仍来自 LangGraph/Starlette 的既有
弃用提示。

## 保持不变与后续工作

保持不变：request 字段和 Redis JSON、key prefix、tenant thread key、TTL、pending status、GET/GETDEL、跨 worker
恢复、一次性 resolve、approval/rejection stream、telemetry 字段与 safe error。新增的是 stored tenant 的严格验证。

D7 的四类 domain model 主路径已完成。仍未完成的同组工作包括：versioned migration CLI 的 dry-run/backup/summary 与
幂等重跑、JSON/未来 SQLite/其他 adapter 共用的 repository contract suite、processed command/profile/approval
retention 和备份策略，以及评估 learning/memory/profile/runtime compatibility facade 的正式弃用窗口。

后续同日批次把本日志仍共置在 runtime 的 ApprovalService repository 用例与 `AIMessage` rejection projection 分开；`runtime.approvals` 现在只保留兼容 wrapper。见 [2026-07-12-approval-service-application-boundary.md](2026-07-12-approval-service-application-boundary.md)。
