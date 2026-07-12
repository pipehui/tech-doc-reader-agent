# Runtime 执行、遥测与审批服务拆分记录

## 1. 本批目标

完成 query 拆分后，`ChatRuntime` 最大的重复仍是四条镜像路径：

- `stream_user_message` / `astream_user_message`
- `stream_approval` / `astream_approval`

每一对都重复 tenant 解析、config、graph stream、异常日志、pending interrupt 查询和 Langfuse flush；approval 还重复 guardrail 分支、tool rejection `update_state` 和 resume。同步/异步任一路径单独修复时，另一条路径很容易漂移。

本批引入：

| 组件 | 单一职责 |
|---|---|
| `GraphExecutionService` | send/resume 的规范执行生成器、pending interrupt、flush 与 async bridge |
| `RuntimeOperationTelemetry` | started/error/interrupted/finished/no-pending event 与 graph stream timer |
| `ApprovalService` | tenant key、guardrail approval request/get/pop/rejection/log resolved |
| `ApprovalRepository` | 审批存取窄接口 |
| `InMemoryApprovalRepository` | 当前单进程兼容 adapter，使用锁保护 put/get/pop |
| `ChatRuntime` | lifecycle 与兼容 facade；执行、审批、查询均委托 runtime 组件 |

## 2. 先建立 sync/async 等价基线

拆实现前，在同构 fake graph 上分别运行同步与异步 surface，并比较输出 parts、graph config/input、stream call、`update_state` 节点和 rejection ToolMessage：

1. 普通 user message。
2. tool interrupt approved。
3. tool interrupt rejected。
4. guardrail approval approved，重新投递原消息。
5. guardrail approval rejected，生成 guardrail update。

拆分前 runtime 相关测试 16 passed；拆分后的扩大测试集 31 passed。原有 async trace-context 测试继续验证 `trace_id` 能进入在线程中构造的 config。

## 3. sync/async 收敛方式

当前 Redis checkpointer 和 compiled graph 使用同步 API。为了避免同时引入第三套执行模型，本批选择：

- `_stream_user_message(..., async_runtime=...)` 是 send 的唯一规范生成器。
- `_stream_approval(..., async_runtime=...)` 是 guardrail/tool resume 的唯一规范生成器。
- sync surface 直接 `yield from`。
- async surface 通过 `_aiter_sync_iterator`，每次 `next()` 使用 `asyncio.to_thread`，保持 event loop 不被同步 graph iterator 阻塞。
- iterator 被取消或提前关闭时，在 worker 中调用底层 `close()`。

`async_runtime` 只控制既有 telemetry 标记和 timer 名称，不维护第二份业务流程。send/resume 完成后的 pending 查询、finish/interrupted event 和按需 Langfuse flush 统一由 `_finish_operation()` 定义。

这仍是同步 graph 的线程桥接，不宣称已迁移到原生 async saver/graph。若未来引入原生 async，需要单独 benchmark 并删除该 bridge，而不是叠加第三条长期路径。

## 4. 审批边界

原 `_guardrail_approvals` dict 已从 execution/facade 移到 repository adapter。`ApprovalService` 负责完整 tenant thread key，execution 只调用窄审批用例；`ChatRuntime` 支持构造时注入 `ApprovalRepository`，默认保持现有内存行为。

guardrail approval 与 LangGraph tool interrupt 暂不强行合成同一种持久化记录：

- guardrail approval 在 graph 运行前产生，需要保存原始输入并在批准后重新 send。
- tool interrupt 已在 checkpoint 中，批准时 resume，拒绝时先写 ToolMessage 再 resume。

两者共享 API-facing execution 流，但保留不同 resume strategy，避免抽象丢失真实语义。

## 5. 实际问题与解决方案

### 问题 A：只让 async 调用 sync public method 会丢失既有 telemetry 差异

原异步路径使用 `async_runtime=True` 和 `graph.stream.thread`。若 async facade 直接桥接 public sync 方法，日志会被标成 sync。

解决：sync/async 都调用同一个私有规范生成器，只通过显式 `async_runtime` 参数选择遥测标签；业务分支完全共享。

### 问题 B：ContextVar 必须在线程边界后仍可见

新 async 规范生成器的 tenant/config 构造发生在第一次 worker `next()` 中。若 `asyncio.to_thread` 不复制 context，trace/user/namespace 会回退默认值。

验证：保留并运行既有 async trace test，同时 parity test 固定相同 trace id 后比较完整 graph stream call。Python 3.12 的 `to_thread` 会复制当前 context，本环境测试通过。

### 问题 C：不能把“有 repository interface”误写成“审批已 durable”

`InMemoryApprovalRepository` 加锁后只改善同一进程内的并发访问，进程重启、多 worker 共享、TTL 和跨实例原子 resolve 仍未解决。

处理：本批只建立 port、service 与注入点，不勾选 durable approval 任务。后续 Redis adapter 必须增加进程 A 写/进程 B resolve、TTL、重复 resolve 和 tenant isolation 测试。

### 问题 D：通用 Command 对象会掩盖 send/resume 差异

send 需要 user message graph input；tool resume 使用 `None`；rejection 还必须从 snapshot 提取 tool call id 并指定 interrupted node；guardrail approve 则重新 send 原消息。把这些压成一个充满 optional 字段的 command dataclass 只会把分支从方法移到字段校验。

处理：保留两个有业务含义的规范方法，共享 config port、telemetry、pending/finish/flush 与 async adapter，不以“只有一个函数”为去重目标。

### 问题 E：原 guardrail early-return 遥测不完整

既有行为在 guardrail approve/reject 分支 `return`，不会写 `chat.approval.finished`；approved 分支会嵌套一组 `chat.request.*`，rejected 分支也不会触发 request flush。

处理：本批以执行结构搬移为主，保留该可观察行为，避免同时改变日志/flush 契约。后续 approval use-case 批次应明确设计“审批操作终态事件”并增加事件序列测试后再修正。

### 问题 F：async timer 的计时边界得到统一

旧 sync timer 包含 graph/config 准备，旧 async timer 从 graph/config 之后开始，两者数值不可直接比较。规范生成器现在对两种 surface 使用相同 timer scope，event 名仍分别为 `graph.stream` / `graph.stream.thread`。这会让 async duration 的统计口径更完整，但与旧历史值不是严格同口径，分析趋势时需要以本次重构为分界。

## 6. 架构门禁

新增 AST 依赖测试：`app/runtime/*.py` 不得 import `app/api` 或旧 `app/services`。当前依赖方向为：

```text
api / CLI -> services.ChatRuntime facade
                    |
                    +-> runtime.execution -> runtime.sessions / runtime.approvals
                    +-> runtime.config
                    +-> lifecycle（暂仍在 facade）
```

## 7. 结果

- `ChatRuntime` 从 query 拆分后的 730 行进一步降到 327 行；剩余主体是 lifecycle 和 public facade。
- send 和 resume 各只有一个业务执行源，async 不再复制 graph/approval 分支。
- runtime telemetry 可注入 event logger、timer 和 clock，测试不依赖真实日志或时间。
- approval repository 可替换，但默认 adapter 仍是进程内实现。

## 8. 验证

| 检查 | 结果 |
|---|---|
| runtime/SSE targeted tests | 31 passed |
| architecture + execution + approval targeted tests | 10 passed |
| Ruff（本批文件） | passed |
| mypy（7 个 runtime/facade source，`--follow-imports=skip`） | passed |
| 全量 pytest | 181 passed，3 个第三方 deprecation warnings |
| 全量 Ruff | passed |
| frontend production build | passed，2013 modules transformed |

后续 2026-07-12 批次进一步把 ApprovalService 的 repository/tenant/telemetry 用例归位 application，并将 `AIMessage` rejection update 提取为 runtime projection；本日志保留的是首次从 ChatRuntime 拆出 service 时的阶段事实。见 [2026-07-12-approval-service-application-boundary.md](2026-07-12-approval-service-application-boundary.md)。
