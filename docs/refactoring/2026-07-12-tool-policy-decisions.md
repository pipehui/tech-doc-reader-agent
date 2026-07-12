# Tool policy 配置注入与显式决策

## 本批目标

原 `graph/tool_policy.py` 把连续相同调用阈值 `2` 和 parser 检索预算 `6` 隐藏在函数默认参数中，
`ToolNode` 再通过两个 `dict | None` 返回值猜测是允许还是阻断。这样配置入口、策略判断、Graph update
和 telemetry 混在调用约定里：阈值不能按部署环境调整，新增策略时也容易漏掉同步或异步分支。

本批完成 B2 剩余项：建立 `Settings -> composition -> ToolExecutionPolicy -> ToolPolicyDecision -> ToolNode`
的单向依赖链，并确认工具错误分类已由统一错误模型覆盖。

## 最终边界

### 1. 生产默认值只存在于 Settings

新增两个非负整数配置：

```text
MAX_IDENTICAL_TOOL_REPEATS=2
PARSER_MAX_RETRIEVAL_CALLS=6
```

- 前者允许两次连续相同的 `tool + args`，第三次阻断；
- 后者允许一个 parser step 内合计六次 `read_docs + web_search`，第七次阻断；
- `Settings` 使用 `Field(ge=0)` 拒绝负值；
- `.env.example` 和 `docs/development.md` 记录配置含义。

`ToolExecutionPolicy` 不再定义另一组 2/6 默认值，`GraphSpec` 也不静默创建默认策略。生产 composition
必须从当前 `AppResources.settings` 显式构造策略，测试 GraphSpec 则显式声明测试阈值，避免两份默认值漂移。

### 2. 图层只接收不可变策略值

`graph/specs.py` 中的 frozen `ToolExecutionPolicy` 只保存已经校验的阈值，不 import Settings。
composition root 负责把外部配置转换成图层值对象；builder 把同一个实例传给 primary 和所有 sub-agent 的
safe/sensitive ToolNode。

新增架构测试禁止 `builder.py/specs.py/tool_nodes.py/tool_policy.py` 在执行期 import
`core.settings`。以后新增配置也必须沿 composition root 注入，不能在 node 内重新读取全局配置。

### 3. policy 始终返回 ToolPolicyDecision

原两个 `maybe_block_*()` 改为 evaluator：

- `evaluate_parser_tool_budget()`；
- `evaluate_repeated_tool_calls()`；
- `evaluate_tool_policy()` 负责固定组合顺序，仍先判断 parser budget，再判断重复调用。

每次判断都返回 frozen `ToolPolicyDecision`。允许结果是 `action="allow"`；阻断结果包含：

- 稳定的 `reason`；
- `tool_name`；
- `observed_calls` 与实际 `limit`；
- 要返回给图的结构化 error `ToolMessage`。

decision 自身校验 allow/block 不变量，只有 block decision 可以转换成 Graph update。两个阻断分支共用
`_blocked_decision()` 组装 `Conflict + ToolMessage`，不再复制错误载荷逻辑。原有错误码
`repeated_tool_call_blocked`、`tool_budget_exceeded` 和非 retryable 语义保持不变。

### 4. sync/async 共用决策与日志

同步和异步 ToolNode wrapper 都先调用 `_blocked_tool_call_update(state, policy)`。该函数只评估一次 decision，
并用同一组字段记录 `tool_call.blocked`：

```text
policy_action, reason, observed_calls, configured_limit
```

阻断时目标工具不会执行；允许时才进入原 ToolNode 与既有 fallback。工具执行异常仍由统一
`classify_error()` 映射为 retryable/validation/permission/unknown 等安全错误。因此 B2 的错误分类项已经由
[统一错误模型](2026-07-12-unified-error-model.md) 完成，本批通过调用链和现有测试复核，没有再建立第二套分类器。

## 实施中遇到的问题

### 问题 A：把阈值移进 policy 后仍可能保留两套默认值

第一版让 `Settings` 和 `ToolExecutionPolicy` 都默认 2/6，并让 `GraphSpec` 使用 default factory。虽然运行正常，
但以后只修改其中一处就会让生产组合、测试或其他 builder 调用方产生不同默认行为。

处理：2/6 只由 Settings 定义；`ToolExecutionPolicy` 和 `GraphSpec` 改为必填依赖。composition test 同时验证
自定义配置和 Settings 默认值都正确进入各自 GraphSpec。

### 问题 B：显式 decision 不等于让 graph adapter 消失

LangGraph node 最终仍需要 `{"messages": [...]}`，若强行让所有调用方直接处理 decision，会把 Graph update
细节扩散到 sync/async wrapper。

处理：policy 始终返回 decision；只有 tool-node adapter 调用 `decision.to_graph_update()`。因此 policy API
不再是 `dict | None`，同时 LangGraph 细节仍停留在单一适配点。

### 问题 C：阻断原因与阈值日志原先分支各写一遍

原实现分别记录 `parser_tool_budget` 和 `repeated_tool_call`，但不记录实际计数或配置值。新增更多策略时，
同步/异步或不同分支很容易出现 telemetry 字段漂移。

处理：reason、observed_calls、limit 都来自同一个 decision；ToolNode 只使用一处日志字段构造。新增真实
RunnableLambda 测试验证第三次调用被阻断且日志中的 `3 / 2` 与决策一致。

### 问题 D：静态检查误把 .env.example 当成 Python

首轮聚焦 Ruff 命令把 `.env.example` 一并传入，Ruff 按 Python 解析 URL 和空赋值，产生 26 个 syntax error。
这不是源码错误。

处理：Ruff 只检查 Python 文件；`.env.example` 由 Settings 构造测试、文本审查和完整应用测试覆盖。

### 问题 E：格式化器扩大了架构测试差异

对明确文件列表运行 formatter 时，它仍重排了 `test_architecture_dependencies.py` 的旧断行，产生与本批
无关的机械 diff。

处理：逐块恢复既有格式，只保留新增的 Settings 依赖方向门禁；最终 diff 再次确认没有无关文件变化。

## 测试与门禁

新增/扩展测试覆盖：

- 默认与自定义 Settings 值、负数拒绝；
- composition root 到两个独立 GraphSpec 的配置传播；
- allow/block decision、decision 不变量和 Graph update 转换；
- parser budget 优先于重复调用策略；
- 原第三次重复调用和 parser budget 错误载荷不变；
- ToolNode 阻断 telemetry 包含 decision 的实际计数与阈值；
- graph 执行模块不得 import Settings；
- Graph topology、compile、router 与 interrupt 行为保持不变。

| 验证 | 结果 |
|---|---|
| graph/settings/composition/architecture 聚焦 pytest | 62 passed |
| 全量后端 pytest（禁用本机不可写 cache） | 378 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，6 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式变化，因此不重复浏览器视觉 smoke。三条 pytest warning 仍来自
LangGraph/Starlette 的既有弃用提示。

## 保持不变与后续工作

保持不变：默认阈值、第三次重复调用/第七次 parser retrieval 的阻断时机、parser-first 判断顺序、
ToolMessage 错误码和 retryable 状态、safe/sensitive 工具分流、interrupt 集合、同步/异步执行结果。

后续如果需要按 Agent、工具或租户配置不同预算，应扩展 policy value object 和 composition mapping；不要把
条件默认值重新塞回 evaluator 或 ToolNode。动态运行期调参还需要明确 snapshot/reload 语义，本批不引入。
