# Infrastructure Resource Container 归位

## 本批结论

本批将 concrete resource aggregate 从 `services/resources.py` 迁到 `infrastructure/resources.py`：

- `AppResources` 与 retrieval-only `RetrievalResources` 物理迁移；
- production bootstrap 显式选择 `AppResources.create`；
- runtime `RuntimeLifecycle` 继续只依赖 `ResourceFactory = Callable[[Settings], Any]`；
- composition 继续只消费已经创建的 resource container；
- retrieval eval、composition/resources/learning transaction tests 使用新路径；
- architecture tests 的重复物理路径收敛为单一 `RESOURCES_PATH`；
- 新增 ownership gate，禁止 `services/resources.py` 回归；
- 迁入模块由递归 `INFRASTRUCTURE_CONTRACT` 覆盖，且不再需要 services allowlist。

资源字段、启动 load/seed、共享 Unit of Work、profile service、web provider、model price table 与 graph composition 行为均未改变。

## 为什么现在才移动

原 Resources 组合：

```text
FaissStore + HybridRetriever + WebSearchBackend
LearningStore + MemoryStore + LearningStateService
JsonUserProfileRepository + UserProfileService
ModelPriceTable
```

在前几批之前，这些 concrete classes 大量位于 `services`。若那时直接把 `resources.py` 改到 infrastructure，新文件会反向 import `services.retrieval`、`services.vectordb` 和 `services.embedding`；为了让 architecture test 通过只能增加例外，实际耦合没有下降。

本批开始前，persistence/retrieval/FAISS/embedding/web provider 已全部归位 infrastructure，conversation summarizer 已归位 application，agents/runtime 也有独立边界。因此 Resources 现在只依赖：

- application models/services/UoW；
- core settings/errors/telemetry/pricing types；
- infrastructure persistence/retrieval/pricing implementations。

这正好符合现有 infrastructure contract，不需要任何反向依赖豁免。

## Factory、Lifecycle 与 Composition 的分工

```text
bootstrap
  -> chooses AppResources.create
  -> injects it into RuntimeLifecycle

infrastructure.resources
  -> constructs concrete adapters and application services
  -> performs configured load/seed initialization

RuntimeLifecycle
  -> invokes ResourceFactory
  -> owns start/failure cleanup state
  -> does not import AppResources

composition
  -> consumes created resources
  -> binds tools/models/agents/GraphSpec
```

`resources.py` 没有并入 `bootstrap.py`，因为 retrieval offline eval 也需要复用 `RetrievalResources`，且 200 行 concrete construction/load/seed 逻辑会让 bootstrap 再次成为厚模块。Bootstrap 是“选择哪个 factory”的 composition root，infrastructure resources 是被选择的 concrete factory implementation。

## Compatibility 策略

旧 `tech_doc_agent.app.services.resources` 没有 re-export。全局 resource locator 已在更早批次删除，production/eval/tests 的显式调用方数量有限且已一次迁移。保留 facade 会让新代码继续从 services 选择 concrete infrastructure，并阻碍 services 收缩为纯兼容区。

稳定运行时边界不是 AppResources 的物理路径，而是：

- `RuntimeLifecycle.resource_factory` callable；
- composition 所需的 structural resource attributes；
- tools 的 `ToolDependencies`/narrow ports。

需要构造生产 concrete resources 的代码使用 `infrastructure.resources.AppResources`；测试仍可直接注入 fake lifecycle/resources，无需依赖该类。

## 实施中遇到的问题

### 问题 A：移动顺序决定是否真正降低耦合

Resources 是依赖聚合点，最容易暴露尚未归位的 concrete modules。提前移动只会把错误 import 带进新层。

处理：先完成 learning/memory、retrieval、FAISS/embedding/chunking、web provider 归位，再移动 aggregate；通过 infrastructure contract 证明新文件 services import 为零。

### 问题 B：Architecture tests 重复硬编码旧资源路径

Profile、learning store、retrieval facade、FAISS 与 web provider 的 ownership tests 各自读取 `APP_DIR/services/resources.py`。逐处字符串替换容易漏掉，未来再次调整也会重复。

处理：新增单一 `RESOURCES_PATH = APP_DIR / "infrastructure" / "resources.py"`，所有资源 source assertions 共用它，再增加 bootstrap/旧路径存在性 gate。

### 问题 C：测试 monkeypatch 了 private initializer 的完整路径

RetrievalResources focused test patch `_initialize_learning_state`，用来证明 retrieval-only factory 不初始化 learning/profile/web 全栈。类 import 更新后，旧 patch target 会在运行时失败。

处理：patch 指向新 concrete module，保留原“retrieval eval 不构造无关资源”测试语义。

### 问题 D：RuntimeLifecycle 不应改成依赖 AppResources

移动到 infrastructure 后，容易顺手给 lifecycle 增加 concrete type annotation，造成 runtime -> infrastructure 倒置。

处理：保持 `ResourceFactory` callable 与 `resources: Any` 现状；本批只移动 concrete implementation。未来若收紧类型，应定义 runtime/application 可见的 narrow container Protocol，而不是 import AppResources。

### 问题 E：Seed 数据仍属于启动策略

SEED_DOCS/SEED_LEARNING_HISTORY 与 load/seed 分支目前和 concrete factory 同文件。拆成 domain fixture 或 migration 容易改变 seed-on-empty、embedding missing degradation 和首次 snapshot 行为。

处理：本批保持字节级业务内容与控制流，先纠正 ownership；seed source/configuration 可在独立有行为基线的批次评估。

## 验证范围

定向验证覆盖 resources load/seed/degradation、retrieval-only initialization、共享 learning UoW、composition instance isolation、runtime lifecycle start/retry/cleanup、bootstrap construction failure cleanup、retrieval eval 与 architecture contracts。

| 验证 | 结果 |
|---|---|
| resources/composition/UoW/lifecycle/bootstrap/eval/architecture targeted pytest | 73 passed；3 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 703 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 150 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- `RuntimeLifecycle.resources` 与 composition resource 参数仍是 `Any`；可从真实属性集合提取窄 Protocol，但不应让 runtime import infrastructure；
- seed documents/history 仍是模块常量，真实生产是否允许 seed 由 `SEED_DOC_STORE_ON_EMPTY` 控制；
- AppResources.create 仍同步初始化所有 concrete adapters，重型/异步启动或 lazy resources 需以启动时间数据另行设计；
- `services/user_profile.py` 与 `services/retrieval/__init__.py` 是明确 compatibility facades，删除需仓外审计/deprecation；
- services 根 `__init__.py` 仍为空；是否保留 compatibility namespace 与上述两个 facade 一起决定。
