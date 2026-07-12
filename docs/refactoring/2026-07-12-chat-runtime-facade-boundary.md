# ChatRuntime 纯注入 Facade 与 API -> Runtime 边界

## 本批结论

`ChatRuntime` 已从 `services/chat_runtime.py` 归位到 `runtime/chat_runtime.py`，但本批的核心不是移动文件，而是删除 facade 内的具体构造 fallback：

- runtime 不再 import `RedisSaver`；
- runtime 不再 import `AppResources`、graph composition 或具体 in-memory repository；
- runtime 不再 import assistants service 的 prompt/model identity builder；
- 构造时必须显式提供 settings、lifecycle、approval repository 和 execution identity factory；
- production concrete wiring 全部集中在 `bootstrap.py`；
- API routes/SSE streaming 直接依赖 runtime facade，不再依赖任何 services module；
- 新增窄 `RuntimeExecutionIdentityPort`，runtime 只消费 fingerprint/payload contract；
- 测试通过 `tests/fakes/chat_runtime.py` 组装 inert lifecycle、in-memory repository 与 identity factory，不再 patch facade 中的具体实现符号；
- 删除旧 services 路径，没有保留把依赖方向重新拉回 services 的兼容 re-export。

结果是目标依赖方向真正成立：

```text
api / CLI -> runtime.ChatRuntime -> runtime components + application/core ports

bootstrap -> Redis repository + RedisSaver + resources + graph + identity builder
```

## 原问题

上一阶段已经提取 `RuntimeLifecycle`、`GraphExecutionService`、`SessionQueryService` 和 `ApprovalService`，production 也已有 `bootstrap.build_chat_runtime()`。但 facade 自己仍保留另一套默认 wiring：

- `get_settings()`；
- `AppResources.create`；
- `RedisSaver.from_conn_string`；
- `build_application_graph`；
- `InMemoryApprovalRepository`；
- `build_runtime_execution_identity`。

这造成三个后果：

1. `services.ChatRuntime` 同时是 runtime facade 和第二个 composition root；
2. API 为获得 runtime 类型必须依赖 services；
3. 测试通过 monkeypatch facade module 中的具体 symbols 构造行为，无法证明 production composition 与 runtime core 真正分离。

## 最终边界

### `runtime/identity.py`

只定义 runtime 所需的结构协议：

- `fingerprint`；
- `to_payload()`；
- `Callable[[Settings], RuntimeExecutionIdentityPort]` factory。

具体 identity 仍由 assistant prompt registry/model route 构造，但只在 bootstrap/test composition 中作为 factory 注入。runtime config 继续只接收普通 mapping，不知道 prompt registry 或 model provider。

### `runtime/chat_runtime.py`

facade 保留原有公开行为：

- sync/async chat 与 approval；
- snapshot/history/state query；
- guardrail approval use case；
- lifecycle context manager；
- resources/checkpointer/graph property 代理；
- execution identity metadata 注入。

构造依赖全部是参数，不存在“没传就创建 concrete adapter”的分支。facade 仍负责 repository ownership：正常退出、启动失败和 Langfuse shutdown 异常路径都会执行既有 close 语义。

### `bootstrap.py`

唯一 production factory 现在显式创建/传递：

- resolved Settings；
- Redis approval repository；
- `RuntimeLifecycle(AppResources, RedisSaver, graph factory)`；
- `build_runtime_execution_identity` factory。

FastAPI lifespan 与 CLI 继续只调用 `build_chat_runtime()`，没有出现第二条生产构造路径。

repository 在 facade 成功构造后才完成 ownership transfer；若 prompt/model identity factory 等构造步骤失败，bootstrap 会关闭刚创建的 repository。cleanup 自身失败只记录安全结构化错误，不覆盖原构造异常。

### `tests/fakes/chat_runtime.py`

大部分 unit/component test 只需注入 fake graph，不应启动 lifecycle。测试 factory 因此提供一个 inert lifecycle：允许显式设置 graph/property，但若误用 context manager 启动，会立即以清晰 AssertionError 失败，不能静默创建一个与 production 不同的假 graph。

需要验证 Redis startup/cleanup 的测试则显式注入真实 `RuntimeLifecycle` 加 fake resource/checkpointer/graph factories。测试现在 patch 真正调用模块或传 callable，不再 patch 已删除的 facade 默认 symbols。

## 实施中遇到的问题

### 问题 A：直接移动文件会制造新的 runtime 倒置

若把原文件原样移动到 `runtime/`，递归 contract 会立刻报告 runtime -> services/infrastructure/composition。目录名看似正确，依赖方向反而更差。

处理：先定义 runtime 所需 ports、删除全部 concrete fallback，再移动。移动与 contract 收紧在同一批验证。

### 问题 B：Execution identity 的事实源仍在 assistants service

runtime 每次构造 config 都需要 versioned identity，settings 在兼容测试中也允许显式替换；直接 import builder 会保留 runtime -> services。

处理：runtime 注入 identity factory，只依赖 payload/fingerprint Protocol。settings setter 在 identity 未显式 override 时调用 factory 重建；显式注入的 identity 继续保持固定，保留原契约。

### 问题 C：大量测试依赖“空参数构造后再塞 fake graph”

简单把参数改为必填会在十多个测试散落 lifecycle/repository/identity 构造模板，重复逻辑从 production 转移到 tests。

处理：建立一个共享 test composition factory。它不是 runtime 默认值，也不会被 production import；所有测试依赖在一个位置可见。

### 问题 D：生命周期测试原来 patch 错误层

旧测试替换 `chat_runtime.AppResources/RedisSaver/build_application_graph`，实际上是在验证 facade 的隐藏 wiring，而不是 `RuntimeLifecycle` 的 retry/rollback contract。

处理：测试直接构造 `RuntimeLifecycle` 并注入 fake factories，再交给 ChatRuntime。失败清理、Redis busy-loading retry 和 Langfuse cleanup 断言保持不变，但依赖层真实可见。

### 问题 E：兼容 re-export 会让 API 倒退仍然通过

保留 `services.chat_runtime -> runtime.chat_runtime` 虽可避免 import 修改，却会让仓库外/新代码继续沿错误路径增长，也迫使 API contract 为 services 留例外。

处理：当前分支尚未发布该重构链，内部调用方已全量迁移，因此直接删除旧 module；API architecture contract 同批升级为禁止任何 services import。

## 验证范围

定向验证覆盖：

- production bootstrap 选择 Redis repository 并传递同一 settings/lifecycle；
- facade 构造失败时 bootstrap 关闭尚未移交的 repository；
- runtime identity 构造、settings 重建与显式 override；
- sync/async send、approval、history/state parity；
- in-memory/Redis approval 跨 runtime 行为；
- lifecycle start/retry/failure rollback/close；
- FastAPI/SSE runtime type boundary；
- runtime 禁止 services/infrastructure/composition，API 禁止全部 services；
- Ruff、mypy 与 `git diff --check`。

| 验证 | 结果 |
|---|---|
| runtime/bootstrap/approval/architecture targeted pytest | 59 passed；3 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 692 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 149 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- `services` 仍包含 assistants、providers、resource container 与 vectordb adapters，尚未形成单一层；
- concrete `RuntimeExecutionIdentity` 仍与 prompt registry 位于 assistants package，runtime 已通过 port 解耦，但领域模型是否下沉需与 model/prompt 目录重组一起决定；
- ChatRuntime 仍保留 settings setter 与 graph/property setters，服务于现有 CLI/component test；未来若去掉兼容 mutation，需先改为显式 runtime builder/test harness，不能突然冻结行为；
- sync graph + async iterator bridge 仍按 B4 计划保留；native async saver/graph API 需要独立 benchmark 批次。
