# Learning / Memory / Profile Application Ports

## 本批结论

本批将跨 tools/API/profile 的 Learning capability 收敛到 application 单一事实源：

- `application/learning_state.py` 新增：
  - `LearningRecordReaderPort`；
  - `MemoryReaderPort`；
  - `LearningStateCommandPort`；
- `application/profile_service.py` 新增 `UserProfileServicePort`；
- 原 `ProfileMemoryReaderPort` 改为 `MemoryReaderPort` compatibility alias，不再复制 Protocol；
- `tools/dependencies.py` 删除本地 `LearningStorePort / MemoryStorePort / LearningStateServicePort / UserProfilePort`；
- ToolResourceContainer 与 ToolDependencies 直接引用 application ports；
- learning tool serializer 接受 `Sequence`，与只读 reader port 返回契约一致；
- Learning API 新增窄 `LearningApiResources` structural view；
- `_runtime_resources()` 只在动态 app state 边界做一次 non-null check + cast；
- API 后续 learning/memory/profile 属性访问全部有静态类型；
- architecture gate 阻止业务 capability ports 重新定义回 tools 或 API 裸资源访问回归。

Repository、store、application service、tool schema、HTTP response 与 tenant 行为均未改变。

## 原来的重复与倒置

移动前存在三套相关契约：

```text
application.profile_service.ProfileMemoryReaderPort
tools.dependencies.MemoryStorePort
Learning API untyped resources.memory_store
```

LearningRecord reader 与 LearningState command 也只在 tools 定义，尽管 API 同样读取 learning store。API contract 明确禁止 import tools，因此它不能复用这些 ports，只能让 `_runtime_resources()` 返回未标注对象，后续全部由 `Any` 穿透。

这既是类型缺口，也是 ownership 错误：记录/记忆/画像的读取/命令 capability 属于应用用例边界，不属于 LangChain tool adapter。

## Canonical Application Ports

### LearningRecordReaderPort

```text
query_records(query, tenant) -> Sequence[LearningRecord]
list_records(tenant) -> Sequence[LearningRecord]
```

### MemoryReaderPort

```text
query_memories(query, tenant, limit) -> Sequence[MemoryFragment]
recent_memories(tenant, limit) -> Sequence[MemoryFragment]
```

### LearningStateCommandPort

```text
update(UpdateLearningStateCommand) -> UpdateLearningStateResult
```

### UserProfileServicePort

集中 get/update/context summary capability，参数使用 `Sequence[str]` 等只读输入 contract；concrete UserProfileService 接受同等或更宽输入。

Reader ports 返回 `Sequence` 而不是强制 `list`，因为 consumer 只迭代/序列化。Concrete JSON stores 当前仍返回 list，但未来 database cursor snapshot、tuple fake 或 immutable collection 不必为满足 port 额外复制。

## Learning API Resource View

FastAPI app state 是动态对象，request 进入时 runtime/resources 也可能尚未初始化。Learning route 定义：

```text
LearningApiResources
  learning_store  -> LearningRecordReaderPort
  memory_store    -> MemoryReaderPort
  profile_service -> UserProfileServicePort
```

`_runtime_resources()` 仍通过 `getattr` 从 app state 读取 runtime，缺失时返回现有 503；确认非空后 cast 到窄 view。Cast 只存在这一处，之后 `_read_records`、memory 和 profile handlers 均由 mypy 检查。

Readiness route 没有强行复用该 view。它的职责是诊断部分初始化、缺字段、store 内部列表/index 是否存在，动态 `getattr` 是刻意的容错探测，不是普通业务数据访问。

## 实施中遇到的问题

### 问题 A：把 API port 放在 tools 会违反既有 architecture contract

最小改动似乎是让 Learning API import `tools.dependencies.MemoryStorePort`，但 API delivery contract 禁止 API -> tools，且业务 capability 被 tool adapter 拥有并不合理。

处理：下沉 application，tools/API/profile 共同向内依赖。

### 问题 B：ProfileMemoryReaderPort 与 MemoryStorePort 签名近似但返回容器不同

前者返回 `Sequence[MemoryFragment]`，后者返回 `list[MemoryFragment]`。直接选择 list 会把 concrete adapter 容器泄漏到 application。

处理：canonical reader port 使用 Sequence；tool serializers 改为接受 Sequence，现有 list 行为保持。

### 问题 C：Compatibility type name 可能有仓外 import

`ProfileMemoryReaderPort` 在 application module 的 `__all__` 中，services user-profile facade 也 import 该名字。直接删除会制造无必要的 type import break。

处理：保留 `ProfileMemoryReaderPort = MemoryReaderPort` alias；只有一个 Protocol definition，旧 type import 仍可用。

### 问题 D：Runtime resources 仍是 Any transport

ChatRuntime/RuntimeLifecycle 需要容纳测试 fake 与不同 resource factories，且不读取具体 learning 属性。把整条 runtime 立即泛型化会扩大改动。

处理：在真正消费业务属性的 API delivery 边界建立窄 view；runtime 继续 opaque transport。Composition concrete conformance 已由上一批 typed factory 检查。

### 问题 E：第一次定向 pytest 命令引用了不存在文件

初次验证列表包含历史猜测路径 `tests/test_learning_api_tenant_integration.py`。Pytest 报 file not found，后续同一 shell 中 Ruff 成功使组合命令最终退出码为 0。

处理：明确不计该次 pytest；通过 `rg --files tests` 定位当前 learning/profile 测试，重新独立运行真实列表并得到 87 passed。最终全量 pytest 另行通过 708 项。

## 验证范围

定向验证覆盖 learning overview/state transaction、tool bundle、profile service/facade/repository、tool-to-API tenant integration 与 architecture contracts；targeted mypy 包含 application ports、tools、Learning API、concrete resources 与 bootstrap conformance。

| 验证 | 结果 |
|---|---|
| learning/profile/tools/API/architecture targeted pytest（有效重跑） | 87 passed；4 个既有第三方/pytest-cache warning |
| targeted mypy | 11 source files，0 issues |
| 全量后端 pytest | 708 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 150 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- RuntimeLifecycle/ChatRuntime resources 仍是 opaque `Any` transport；只有需要具体属性的 consumer 应定义窄 view；
- Readiness health 保留动态诊断，不把部分初始化状态伪装成完整 Protocol；
- DocumentStorePort 与 WebSearchPort 仍在 tools，因为目前只有 tool adapter consumer；出现 API/其他 use case 后再下沉；
- ProfileMemoryReaderPort alias 暂时保留，删除需先确认仓外 type imports；
- Learning API response 仍在 delivery 边界将 domain models 投影为 Pydantic schemas，application ports 不依赖 API models。

后续同日批次把本日志中暂时共置于 `learning_state.py` 的 command/result、capability ports 和 UoW 拆到独立 application 模块；本日志仍准确描述 ports 首次归位时的阶段状态。当前所有权见 [2026-07-12-learning-application-boundaries.md](2026-07-12-learning-application-boundaries.md)。
