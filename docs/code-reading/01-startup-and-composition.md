# 01 - 启动、资源创建与依赖装配

本章回答：服务启动时究竟创建了什么、创建顺序为何不能随便换、FastAPI/CLI 为什么共用 Runtime，以及增加一个新依赖时应该在哪一层接线。

## 两个可执行入口

### FastAPI 入口

[`api/server.py`](../../tech_doc_agent/app/api/server.py) 在模块 import 时完成静态 app 配置：

1. `settings = get_settings()`；
2. 创建 `FastAPI(lifespan=lifespan)`；
3. 安装 CORS；
4. include chat/health/learning router；
5. 安装 production frontend 静态路由。

真正的运行时资源不在 import 时创建，而是在：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    with build_chat_runtime() as runtime:
        app.state.runtime = runtime
        yield
```

`yield` 前完成资源启动，`yield` 后通过 context manager 完成关闭。route 只从 `request.app.state.runtime` 取现成实例。

这种安排避免 `import tech_doc_agent.app.api.server` 就连接 Redis、创建模型和加载 FAISS，也让 FastAPI 的 shutdown 能走完整清理路径。

### CLI 入口

[`main.py`](../../tech_doc_agent/app/main.py) 同样执行：

```python
with build_chat_runtime() as runtime:
    ...
```

CLI 和 FastAPI 共享 `ChatRuntime`，差别只在 delivery：CLI 遍历 graph part 并打印 message token；FastAPI 把 part 翻译成 SSE。若修改 graph 执行语义，应优先改 runtime/graph，而不是分别改两套入口。

## `build_chat_runtime` 的输入与输出

位置：[`bootstrap.py`](../../tech_doc_agent/app/bootstrap.py)。

```python
def build_chat_runtime(settings: Settings | None = None) -> ChatRuntime
```

输入：

- 显式 `Settings`，便于测试、脚本或不同部署注入；
- `None` 时调用缓存的 `get_settings()`，从 `.env` / `.dev.env` / 环境变量读取。

输出：

- 一个**尚未 start** 的 `ChatRuntime`；
- 构造时已创建 Redis guardrail approval repository 和 `RuntimeLifecycle`；
- `with runtime` 进入时才创建 AppResources、RedisSaver 和 compiled graph。

内部步骤：

```text
Settings
  -> RedisApprovalRepository.from_url(...)
  -> build_runtime_lifecycle(settings)
  -> ChatRuntime(settings, lifecycle, approval_repository,
                 execution_identity_factory)
```

如果 `ChatRuntime(...)` 构造失败，`build_chat_runtime` 会尝试关闭已经创建的 approval repository；关闭本身失败只记录安全错误字段，不覆盖原始构造异常。

## `RuntimeLifecycle.start` 的严格顺序

位置：[`runtime/lifecycle.py`](../../tech_doc_agent/app/runtime/lifecycle.py)。

`start()` 的顺序是：

```text
1. resources = resource_factory(settings)
2. checkpointer = RedisSaver.from_conn_string(REDIS_URL).__enter__()
3. checkpointer.setup()
4. graph = graph_factory(checkpointer, resources)
5. _started = True
```

输入来自构造时注入的三个 callable：

| 参数 | 生产值 | 返回值 |
|---|---|---|
| `resource_factory` | `_create_app_resources` | `AppResources`，但对外按 `CompositionResources` 使用 |
| `checkpointer_context_factory` | `RedisSaver.from_conn_string` | RedisSaver context manager |
| `graph_factory` | `build_application_graph` | compiled LangGraph |

任何步骤抛错都会调用 `close()`：先退出 checkpointer context，再清空 graph/resources 和 started 标记。这样不会留下“resources 已创建但 graph 是半成品”的 Runtime。

### Redis startup retry 只处理加载中状态

`_setup_checkpointer_with_retry` 最多尝试 `REDIS_SETUP_MAX_ATTEMPTS`。它只对以下情况重试：

- `BusyLoadingError`；
- 错误文本包含 `redis is loading`；
- 错误文本包含 `loading the dataset`。

连接拒绝、认证失败等错误不会盲目循环，直接经 `classify_error(..., dependency="redis")` 抛出。修改此处时不要把所有异常都标成可重试，否则配置错误会被隐藏几十秒。

## `AppResources.create` 创建哪些具体对象

位置：[`infrastructure/resources.py`](../../tech_doc_agent/app/infrastructure/resources.py)。

返回的 `AppResources` 包含：

| 属性 | 具体类型 | 主要消费者 |
|---|---|---|
| `settings` | `Settings` | composition、health |
| `faiss_store` | `FaissStore` | document save、semantic ranker、health |
| `hybrid_retriever` | `HybridRetriever` | `read_docs` / `search_related_docs` |
| `learning_store` | `LearningStore` | read tools、learning API |
| `memory_store` | `MemoryStore` | read tools、profile context、learning API |
| `learning_state_service` | `LearningStateService` | learning write tools |
| `profile_service` | `UserProfileService` | profile tools、user-info node、learning API |
| `web_search_backend` | `WebSearchBackend` | `web_search` tool |
| `model_price_table` | `ModelPriceTable` | workflow budget tracker |

### 文档资源初始化

`RetrievalResources.create` 调 `_initialize_faiss_store(settings)`：

1. `FaissStore.load()` 成功：使用当前 generation snapshot；
2. load 失败且 `SEED_DOC_STORE_ON_EMPTY=false`：保持空库；
3. 允许 seed 但 embedding 未配置：只在内存放 `SEED_DOCS`，没有 vector index；
4. 允许 seed 且 embedding 可用：build index + save；
5. embedding/index 建立产生已分类的 `ApplicationError`：退化为只有 seed documents 的无向量状态。

这解释了为什么 ready check 可能显示 `documents > 0` 但 `indexed=false`，也解释了 hybrid mode 在 semantic 不可用时仍可通过 exact/BM25 返回结果。

### 学习状态初始化

`_initialize_learning_state` 只创建**一个** `LearningStateUnitOfWork`，再把它同时传给 `LearningStore` 和 `MemoryStore`：

```text
LearningStateSnapshotRepository
  -> LearningStateUnitOfWork
       -> LearningStore
       -> MemoryStore
       -> LearningStateService(records=LearningStore, memories=MemoryStore)
```

共享 UoW 是关键：一次 `upsert_learning_state` 可以把 record、memory 和 processed command outcome 作为一个 snapshot 发布。若分别 new 两个 UoW，读写会落到不同内存快照，原子性就消失。

## `composition.py` 做的四段装配

[`build_graph_spec`](../../tech_doc_agent/app/composition.py) 的输入是 `CompositionResources` Protocol，而不是 concrete `AppResources`。它按以下顺序工作：

### 1. resources -> `ToolDependencies`

`ToolDependencies.from_container(resources)` 只取工具需要的七个 capability：document store/retriever、learning reader、memory reader、learning command service、profile service、web search。

### 2. dependencies -> `ToolBundle`

`build_tool_bundle` 创建 11 个绑定当前资源实例的 LangChain tools。没有模块级 singleton，因此测试可以给不同 composition 注入不同 fake port。

### 3. settings + tools + prompts -> `AssistantRegistry`

```text
build_assistant_model_provider(settings)
build_prompt_registry()
build_assistant_registry(models, tools, prompts)
```

每个 assistant definition 同时带：assistant runnable、safe tools、sensitive tools、execution identity。工具是否敏感在角色定义阶段决定，图只消费这个结果。

### 4. registry + policies -> `GraphSpec`

`_graph_spec_from_registry` 建立：

- primary spec；
- parser/relation/explanation/examination/summary 五个 `AgentSpec`；
- user info node；
- execution budget、tool policy、reflection policy；
- budget/context/provider-retry trackers；
- context compactor。

最后 `build_application_graph(checkpointer, resources)` 才调用 `build_multi_agentic_graph` compile。

## 为什么 `bootstrap.py` 和 `composition.py` 分开

两者回答不同问题：

- `bootstrap.py`：生产部署选择 RedisApprovalRepository、RedisSaver、AppResources 和 runtime identity factory；
- `composition.py`：给定一组满足 Protocol 的资源，工具/Agent/graph 应怎样组合。

这允许 composition 测试完全使用 fake resources，不连接 Redis/模型；也让 runtime 层不 import concrete infrastructure。两者都是明确 composition root，architecture test 对它们采用不同规则。

## Settings 如何影响启动与请求

[`core/settings.py`](../../tech_doc_agent/app/core/settings.py) 中配置大致分为：

- **启动资源**：`DATA_PATH`、embedding、Tavily、Redis、seed；
- **模型路由**：primary/backup 模型和 provider ID；
- **请求执行**：recursion、retry、budget、tool policy、reflection、compaction；
- **delivery/telemetry**：CORS、Langfuse、runtime identity、pseudonym key。

`get_settings()` 有 `@lru_cache`。测试或运行时若修改环境变量后希望重新读取，必须清 cache，不能假设再次调用就得到新值。普通业务代码也不要在深层反复调用 `get_settings()` 来绕过显式注入。

## 关闭顺序

`ChatRuntime.__exit__`：

1. `shutdown_langfuse(settings)`；
2. `RuntimeLifecycle.close(...)`，退出 RedisSaver context 并清空 graph/resources；
3. `_close_approval_repository()`。

即使前一步失败，后面的资源仍通过嵌套 `finally` 尝试关闭。新增长期连接资源时，应明确由哪个 owner 关闭；不能仅依赖进程退出。

## 修改时的常见坑

### 新增一个资源却只加到 `AppResources`

如果工具或 graph 需要它，还要按用途更新：

1. `ToolResourceContainer` 或 `CompositionResources`；
2. `ToolDependencies`；
3. 对应 build function；
4. fake resources 和 composition tests；
5. readiness check（若它是启动必要项）。

不要把 `resources: Any` 加回 composition 来省掉类型错误；当前 mypy 正是用 structural Protocol 检查装配是否完整。

### 在 route 中临时 new repository

这会绕过 lifecycle、测试注入、tenant 一致性和关闭责任。route 应只访问 `request.app.state.runtime` 或窄 runtime resources view。

### 在模块 import 时加载外部服务

会让静态测试、mypy、CLI import 和前端 smoke 都意外需要完整后端依赖。具体外部资源应在 lifespan/context manager 进入后创建。

### 改启动顺序

graph compile 依赖 resources 和 checkpointer；先 compile 再 setup Redis 会产生不可用图。若资源创建失败，应保持 graph 为 `None`，health 才能准确报告未初始化。

## 对应测试

- [`tests/test_bootstrap.py`](../../tests/test_bootstrap.py)：生产依赖注入与构造失败清理；
- [`tests/test_runtime_lifecycle.py`](../../tests/test_runtime_lifecycle.py)：start/retry/close 顺序；
- [`tests/test_resources.py`](../../tests/test_resources.py)：load/seed/退化行为；
- [`tests/test_composition.py`](../../tests/test_composition.py)：Tool/Agent/GraphSpec 装配；
- [`tests/test_graph_compile.py`](../../tests/test_graph_compile.py)：可 compile 与 interrupt nodes；
- [`tests/test_health_routes.py`](../../tests/test_health_routes.py)：部分初始化和 ready 投影；
- [`tests/test_architecture_dependencies.py`](../../tests/test_architecture_dependencies.py)：concrete dependency 没有泄漏回 runtime/graph/application。
