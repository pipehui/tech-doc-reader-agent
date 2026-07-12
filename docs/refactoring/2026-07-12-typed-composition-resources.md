# Typed Composition Resource Protocols

## 本批结论

本批消除 composition 对 concrete resource attributes 的 `Any` 访问：

- `tools.dependencies.ResourceContainer` 明确重命名为 `ToolResourceContainer`；
- ToolResourceContainer 以只读 properties 暴露七个 tool ports；
- `UserProfilePort` 补齐 composition 实际需要的 `context_summary` capability；
- `composition.py` 新增 `CompositionResources(ToolResourceContainer, Protocol)`；
- CompositionResources 只额外要求 core `Settings` 与 `ModelPriceTable`；
- `build_graph_spec`、`_graph_spec_from_registry`、`build_application_graph` 的 resources 参数不再是 `Any`；
- composition 不 import concrete `infrastructure.resources.AppResources`；
- bootstrap 增加 typed `_create_app_resources() -> CompositionResources` adapter；
- mypy 现在会实际验证 concrete AppResources 满足 structural Protocol；
- architecture test 阻止 `resources: Any` 或 composition -> infrastructure concrete import 回归。

运行时对象、resource 创建流程、tool binding、graph topology 和 settings 读取均未改变。

## 两层 Resource Capability

### ToolResourceContainer

只包含构造 `ToolDependencies` 所需的 capability：

```text
faiss_store             -> DocumentStorePort
hybrid_retriever        -> DocumentRetrieverPort
learning_store          -> LearningStorePort
memory_store            -> MemoryStorePort
learning_state_service  -> LearningStateServicePort
profile_service         -> UserProfilePort
web_search_backend      -> WebSearchPort
```

它位于 tools adapter 边界，因为这些字段的唯一目的就是绑定 LangChain tools。`ToolDependencies.from_container()` 只复制 port references，不知道 AppResources、Settings、FAISS concrete class 或 repository。

### CompositionResources

Graph composition 在上述 tool capabilities 之外只使用：

- `settings`：model provider、execution/tool/reflection/context policy；
- `model_price_table`：WorkflowBudgetTracker；
- profile service 的 `context_summary`：构造 user-info node。

因此 CompositionResources 继承 ToolResourceContainer，只补两个 core properties；没有复制七个字段，也没有创建一个包含所有 AppResources 成员的“万能接口”。

## Typed Concrete Conformance

只给 composition 函数加 Protocol annotation 仍不够：`RuntimeLifecycle.GraphFactory` 原来接受 `Any` resources，bootstrap 把 `AppResources.create` 与 `build_application_graph` 传入时，mypy 不会把二者连接起来验证。

本批增加：

```python
def _create_app_resources(settings: Settings) -> CompositionResources:
    return AppResources.create(settings)
```

该函数位于 bootstrap composition root，可以同时看到 concrete AppResources 与抽象 CompositionResources。Mypy 会检查 return value 的 structural compatibility；composition 和 runtime 都不需要 import infrastructure class。

## 为什么 Protocol 属性必须只读

最初形式是：

```python
class ToolResourceContainer(Protocol):
    faiss_store: DocumentStorePort
```

普通 Protocol attribute 被视为可写。若 concrete AppResources 用更具体的 `FaissStore` 实现，调用方理论上可以通过 Protocol reference 把另一个仅满足 DocumentStorePort 的对象赋给 `faiss_store`，破坏 AppResources 的具体类型不变量。因此可写属性不能安全协变。

Composition/tools 只读取 container，不会替换字段。本批改成：

```python
@property
def faiss_store(self) -> DocumentStorePort: ...
```

只读 property 允许 concrete dataclass attribute 返回更具体类型，准确表达真实使用方式，也不增加 runtime wrapper 或继承要求。

## 实施中遇到的问题

### 问题 A：已有 UserProfilePort 不完整

Tool 使用 profile 的 get/update，但 composition 还调用 `profile_service.context_summary`。如果 CompositionResources 只继承旧 ResourceContainer，mypy 仍无法证明该方法存在。

处理：把 context summary 作为同一 UserProfilePort capability 补齐，签名包含 tenant、memory query 与 limit；application UserProfileService 的更宽可选参数实现满足该协议。

### 问题 B：复制一个更大的 Protocol 会产生字段双源

可以在 composition 重新声明所有七个 tool fields 加两个额外字段，但以后 ToolDependencies 新增 capability 时两份接口容易漂移。

处理：CompositionResources 继承 ToolResourceContainer，只声明增量能力。

### 问题 C：GraphFactory 的 Any 会掩盖 concrete mismatch

即使 composition 内部类型正确，bootstrap/lifecycle callable alias 的 Any 仍可能允许传入缺字段的 resource factory，错误只在 graph startup 才出现。

处理：bootstrap typed adapter建立 concrete -> Protocol 的静态检查点；保留 RuntimeLifecycle 的通用 factory，不让 runtime 依赖具体 resource schema。

### 问题 D：不能通过 import AppResources 获得“简单类型安全”

把 `build_graph_spec(resources: AppResources)` 写进 composition 最直接，但会使 composition root helper 绑定 infrastructure concrete class，测试 fake 也必须继承或伪装该类。

处理：使用 structural Protocol；AppResources 与测试 fake 只需具备实际属性，不需要 nominal inheritance。

### 问题 E：本批没有泛型化整个 RuntimeLifecycle

Lifecycle 只保存、传递和清空 resources，本身不读取具体属性。将其与 ChatRuntime/API 全链路改成 `Generic[ResourceT]` 会扩大改动面，而对当前 composition 属性错误没有额外保护。

处理：本批在 concrete factory 与 concrete consumer 两端建立检查，保留 lifecycle `Any` 作为真正的 opaque transport；是否泛型化需结合 ChatRuntime/API resource access 单独审计。

## 验证范围

定向验证覆盖 ToolDependencies adaptation、AppResources instance isolation、bootstrap typed factory、graph compile、composition ownership 与全部 architecture contracts；direct mypy 显式包含 composition/tools/infrastructure resources/bootstrap。

| 验证 | 结果 |
|---|---|
| composition/tools/bootstrap/graph/architecture targeted pytest | 47 passed；3 个既有第三方/pytest-cache warning |
| targeted mypy concrete conformance | 9 source files，0 issues |
| 全量后端 pytest | 707 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 150 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- RuntimeLifecycle/ChatRuntime 的 resources transport 仍为 `Any`，但不读取 concrete attributes；泛型化需同时覆盖 API state access/fakes；
- `build_application_graph` 的 checkpointer/return 仍使用第三方边界 `Any`，可在 LangGraph concrete type 稳定后收紧；
- DocumentStorePort 的 JSON document/payload 仍含 `Any`，属于 wire schema/legacy adapter 范围；
- ToolDependencies 目前通过 container adapter 构造，也可由测试/未来 composition 直接按 ports 构造；
- Protocol 是静态 structural contract，不增加 runtime `isinstance` 检查或依赖 injection framework。
