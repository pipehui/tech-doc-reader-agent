# 显式资源、工具与模型依赖注入

## 本批目标

上一批已经把 resources、Redis checkpointer 和 graph 的启动/关闭收敛到 `RuntimeLifecycle`，但 lifecycle 仍需把 `AppResources` 发布到模块级 `_current_resources`。工具函数、用户画像和 import-time assistant 再从全局位置取回依赖，形成了以下隐藏链路：

```text
bootstrap
  -> RuntimeLifecycle
  -> global AppResources locator
  -> module-level tools / assistants / graph
```

这会导致测试必须替换进程级全局对象、并行测试互相影响，以及仅 import assistant 就创建真实模型客户端。本批把链路改为：

```text
bootstrap
  -> RuntimeLifecycle(resources, checkpointer)
  -> composition root
  -> ToolDependencies -> ToolBundle
  -> AssistantModelProvider + ToolBundle -> AssistantRegistry
  -> AssistantRegistry -> GraphSpec -> graph builder
```

依赖只沿这一方向传递，不再从业务函数内部反向查找 composition root。

## 实际改动

### 1. 移除全局 Resource Locator

`services/resources.py` 删除以下进程级状态和兼容函数：

- `_current_resources`
- `get_app_resources`
- `set_app_resources`
- `reset_app_resources`
- `override_app_resources`

`RuntimeLifecycle` 不再接收 publisher/resetter，也不再判断全局资源所有权。它只持有自己创建的 resources、checkpointer 和 graph，并在 `close()` 时释放自己的引用。

learning API 和 readiness 路径都从 `request.app.state.runtime.resources` 读取已经启动的组件。若 runtime 尚未初始化，learning API 明确返回 `503`，不会为了完成一次请求而偷偷创建 FAISS、JSON store 或 web search backend。

### 2. 将工具改为资源绑定工厂

旧 `services/tools/` 中的模块级 LangChain tool 已删除，替换为独立的 `app/tools/` package：

| 模块 | 职责 |
|---|---|
| `dependencies.py` | 定义 document、retrieval、learning、memory、profile、web search 窄端口 |
| `documents.py` | 构造文档读取、写入、向量检索和外部搜索工具 |
| `learning.py` | 构造 tenant-scoped 学习记录和学习轨迹工具 |
| `profiles.py` | 构造 tenant-scoped 用户画像工具 |
| `bundle.py` | 汇总稳定命名的 `ToolBundle` |

每次 `build_tool_bundle(dependencies)` 都生成只绑定当前 resources 的工具实例。测试可直接传 fake ports，不再需要全局 override；两个 bundle 的 store、retriever 和 profile service 彼此隔离。

用户画像原本是最后一个残留的隐式具体依赖：profile tool 虽然不再调用 resource getter，却仍直接 import 文件型 service 函数。为避免只把耦合“换个位置”，本批增加资源作用域内的 `UserProfileService`，并通过 `UserProfilePort` 注入工具、API 和 `fetch_user_info` 节点。

### 3. 移除 import-time 模型和 Assistant 单例

`assistant_base.py` 现在只保留 assistant 的重试、空响应处理和消息命名行为，不再读取 settings，也不再创建 `ChatOpenAI`。

新增的 assistant 组合层包括：

- `model_factory.py`：唯一允许创建 `ChatOpenAI` 的模块；
- `definition.py`：把 prompt、模型和 safe/sensitive/control tools 组合成 `AssistantDefinition`；
- `registry.py`：一次构造六个 role 的 `AssistantRegistry`；
- 每个 role 模块只保留 prompt 与 `build_*_assistant()` 工厂。

`services.assistants.__init__` 不再 eager import 全部 role。单独 import `assistant_base` 不会读取配置、加载 `langchain_openai` 或创建客户端。

### 4. Graph builder 只消费声明，不认识具体实现

新增 `PrimarySpec` 和 `GraphSpec`。`graph/builder.py` 只读取 assistant、tool policy、subagent spec 和 user-info node，不再 import 具体 assistant 或业务工具。

敏感工具名从注入的 tool 实例计算，primary router 不再依赖静态模块级工具列表。`interrupt_before` 同样由 `GraphSpec.interrupt_nodes` 产生，拓扑仍保持原节点名与边集合。

生产组合集中到 `app/composition.py`：它负责从 `AppResources` 构造 tool bundle、model provider、assistant registry、user-info provider 和最终 graph。测试则直接构造 fake `GraphSpec`，无需模型密钥、Redis、FAISS 或网络。

### 5. 模型可见 schema 保持兼容

本批移动了 Pydantic handoff/plan command 和 LangChain tools，但保留了原 tool 名、参数 schema、Field description 与 docstring。它们不仅是代码注释，也是模型看到的工具契约；随意缩写会造成行为变化，不能当作普通重命名处理。

## 实施中遇到的问题

### 问题 A：隐藏耦合不只在 resource getter

最初只删除 getter 后，graph builder 仍 import 模块级 assistant，assistant 又 import 模块级 tool 和模型。结果只是让全局状态从一个文件迁到另一个文件，无法用 fake graph 完成真正离线的 composition test。

处理：同一批完成 `ToolBundle -> AssistantRegistry -> GraphSpec` 三层显式组装，并增加依赖方向门禁。graph builder 与工具 package 都不能再引用旧 `services.tools` 或 resource locator。

### 问题 B：fallback 的 tool binding 顺序

旧代码先对 primary model 调用 `with_fallbacks()`，再对返回的 fallback runnable 调用 `bind_tools()`。不同 LangChain runnable 类型并不都保证保留 model 的 `bind_tools` 能力。

处理：primary 与 backup 分别绑定完全相同的工具和 `parallel_tool_calls=False`，然后把两个已绑定 runnable 组合为 fallback。registry contract test 同时检查两侧的 tool schema。

### 问题 C：LangGraph compile 边界要求 list

`GraphSpec.interrupt_nodes` 使用不可变 tuple 表达声明，但当前 LangGraph compile 内部会把 `interrupt_before` 与 list 拼接。直接传 tuple 导致 topology/compile 测试报 `TypeError`。

处理：领域声明保持 tuple，在 `build_multi_agentic_graph()` 的第三方适配边界转换为 list。没有为了迎合第三方 API 把内部声明改成可变结构。

### 问题 D：全量测试发现 eval runner 的旧 locator 残留

定向 graph/tool/runtime 测试已经通过后，整仓 pytest 在 collection 阶段仍因 `evals/run_retrieval_eval.py` import 已删除的 `reset_app_resources` 失败。

处理：删除 eval runner 的全局 reset 清理逻辑。runner 创建的 resources 现在只在自己的调用栈中存在，不需要复位进程级状态。这也说明删除架构兼容层后必须跑全量 collection，不能只跑应用测试目录。

### 问题 E：测试不能再靠进程级 override

旧工具测试通过 `override_app_resources` 临时替换全局对象。直接删除后，多个测试缺少 document/profile/learning 依赖。

处理：测试改为显式构造 `ToolDependencies`、fake ports 或真实的临时目录 `UserProfileService`。另增加两个 AppResources 的离线 composition 测试，确认工具实例与数据目录不会串用。

## 新增架构门禁

`tests/test_architecture_dependencies.py` 现在会阻止：

- graph builder import 具体 assistants 或旧 tools；
- 新 tools import resource locator、旧 tools 或具体 user-profile service；
- application package 重新出现任一 global locator symbol；
- assistants package `__init__` eager import role；
- `assistant_base` import settings、model factory 或 `langchain_openai`；
- `ChatOpenAI` 在 `model_factory.py` 之外被构造。

## 验证结果

| 验证 | 结果 |
|---|---|
| 全量后端测试 | `207 passed` |
| Ruff 全仓检查 | passed |
| 本批直接源码 mypy（`--follow-imports=skip`） | passed，27 个 source files |
| production graph 离线 composition | passed，无 Redis/LLM/API 调用 |
| assistant import side-effect check | passed，未加载 model factory 或 `langchain_openai` |
| 前端 production build | passed，2013 modules transformed |
| `git diff --check` | passed |

pytest 仍报告 3 个第三方 deprecation warning；另有 `.pytest_cache` 写入权限 warning。它们不影响测试结果，也不是本批业务代码产生的失败。

## 保留行为与有意变化

保持不变：

- 六个 assistant 的 prompt、工具名、safe/sensitive 分类；
- graph 节点名、关键边、路由优先级和 interrupt 节点；
- tool 参数 schema 和模型可见描述；
- FastAPI/CLI 默认仍由同一 production bootstrap 启动。

有意变化：

- 未启动 runtime 时，learning API 返回 `503`，不再隐式创建资源；
- 不保留 global getter 的 deprecation 兼容期。当前改动尚未推送且所有调用方可一次迁移，继续保留会让新代码重新依赖旧边界；
- graph factory 由单参数 `(checkpointer)` 改为显式 `(checkpointer, resources)`。

## 后续工作

- `AssistantModelProvider` 已支持显式 primary/backup 注入，但 role-specific model、timeout 与 usage metadata 仍未实现；
- prompt 仍在 Python role 模块中，后续需建立可组合 section、稳定 ID/hash 和启动校验；
- `search_related_docs` 的 broad exception 仍属于 retrieval B5，不能在依赖注入批次顺手改变错误语义；
- `upsert_learning_state` 的 learning + memory 半事务仍属于数据一致性 D3；
- 本批完成的是资源所有权与构造边界，不代表 auth/tenant authorization 已生产化。
