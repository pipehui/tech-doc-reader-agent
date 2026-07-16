# 05 - Agent、Prompt 与模型

本章说明六个 Agent 并不是六套独立运行时：它们共用同一个 `Assistant` 执行模板和 model provider，但拥有不同 prompt、tool policy、name 和 completion 行为。

## 从 registry 开始读

位置：[`agents/registry.py`](../../tech_doc_agent/app/agents/registry.py)。

```python
build_assistant_registry(
    models: AssistantModelProvider,
    tools: ToolBundle,
    prompts: PromptRegistry,
) -> AssistantRegistry
```

它按固定顺序创建 `primary, parser, relation, explanation, examination, summary`。`AssistantRegistry.identities()` 也按同一顺序输出 execution identity；runtime identity fingerprint 依赖这个顺序，不能随便改成 set/dict 遍历。

## 一个 `AssistantDefinition` 包含什么

位置：[`agents/definition.py`](../../tech_doc_agent/app/agents/definition.py)。

```python
@dataclass(frozen=True)
class AssistantDefinition:
    assistant: Assistant
    safe_tools: tuple[Any, ...]
    identity: AssistantExecutionIdentity
    sensitive_tools: tuple[Any, ...] = ()
```

`build_assistant_definition` 接收 prompt artifact、models、role name 和 safe/sensitive/control tools。它做三件事：

1. 强制 `name == prompt.role`；
2. 创建包含 prompt hash + model route 的 identity；
3. 构造 `prompt.template | models.bind_tools(...)` runnable，并包成统一 `Assistant`。

control tools（Plan/handoff/CompleteOrEscalate）会绑定给模型，但不加入 safe/sensitive execution tuple。它们由 graph router/node 自己消费，不应进入普通 ToolNode。

## 六个角色的工具表

角色定义分别位于 `agents/*_assistant.py`。

| 角色 | safe tools | sensitive tools | control tools / 特殊行为 |
|---|---|---|---|
| primary | `read_user_profile`, `read_learning_history`, `read_all_learning_history`, `read_user_memory`, `PlanWorkflow`, 五种 `To*Assistant` | `upsert_learning_history`, `update_user_profile` | Plan/handoff 当前被放入 safe tuple供 graph 路由，不由 ToolNode 真执行 |
| parser | `read_docs`, `web_search` | `save_docs` | `CompleteOrEscalate`；finish 写 `parser_result` |
| relation | `read_all_learning_history`, `search_related_docs`, `read_docs` | 无 | `CompleteOrEscalate`；finish 写 `relation_result` |
| explanation | `read_docs` | 无 | `CompleteOrEscalate`；面向用户输出 |
| examination | `read_learning_history`, `read_docs` | `upsert_learning_history` | `CompleteOrEscalate`；finish 写 `examination_context` |
| summary | `read_learning_history`, `read_user_memory` | `upsert_learning_state` | `CompleteOrEscalate`；使用 full messages |

几个有意的限制：

- primary 没有 `web_search`，复杂资料获取应交给 parser；
- relation/explanation 全只读，不产生 HITL interrupt；
- parser 保存文档、examination 写分数、summary 合并写学习状态都必须审批；
- summary 不直接 `save_docs`，学习总结返回给用户而不是写共享知识库。

## Prompt 不在 Python 字符串里

资源目录：[`agents/prompts`](../../tech_doc_agent/app/agents/prompts)。

[`manifest.json`](../../tech_doc_agent/app/agents/prompts/manifest.json) 为每个 role 固定：

- 稳定 prompt ID；
- SHA-256；
- 按顺序组合的资源文件；
- required placeholders。

primary 被拆成 `primary/00-role.md` 到 `15-runtime-context.md`，因为职责规则可独立修改/审查；加载时仍按 manifest 顺序拼成一个 system template。

## `PromptRegistry` 加载时做哪些硬检查

位置：[`agents/prompt_registry.py`](../../tech_doc_agent/app/agents/prompt_registry.py)。

`build_prompt_registry()`：

1. 用 `importlib.resources` 读取 package 内 manifest；
2. JSON 必须是 object，schema version 必须为 1；
3. role 集合必须与 `ASSISTANT_ROLES` 完全一致；
4. 每个 resource path 必须是安全的相对 POSIX path，不能有 `..`、反斜杠或绝对路径；
5. 按声明顺序读取 sections，用两个换行连接；
6. 实际 SHA-256 必须等于 manifest；
7. template 中实际 placeholders 加 `messages` 必须与声明集合完全相等；
8. 构造 `ChatPromptTemplate`，把 `time` partial 到 `_current_time` callable。

因此手改 prompt 后若不更新 hash，服务在构建 runtime identity 或 graph 时会立即失败。这是有意的 provenance 保护，不应通过删除 hash 校验“修复”。

## Placeholder 从哪里来

所有 prompt 都有 `{messages}` 和 `{time}`。primary 还使用 `{user_info}`；examination/summary 使用 `{learning_target}`。

LangChain prompt runnable 收到的是 graph state mapping，所以 `user_info`、`learning_target` 必须由前置节点写入。新增 placeholder 时必须确认每条进入该 Agent 的路径都已填字段，并同步 manifest 的 required placeholders。

## Prompt 规则与硬代码规则的边界

Prompt 负责高层行为，例如：

- primary 选择 direct/single/multi-agent；
- parser 本地文档优先、何时保存；
- relation 提供类比边界；
- examination 区分出题/评分；
- summary 只更新少量核心学习状态。

代码负责不能只靠模型自觉的约束：

- tool 是否需要审批；
- parser 总检索次数；
- 相同 tool+args 重复调用；
- reflection 最大轮数；
- workflow/request budget；
- structured result 写入哪个 state key；
- examination 答题续接的硬路由。

若一条规则关系到安全、幂等、成本或图合法性，应有代码/测试兜底，不能只写 prompt。

## Model provider 构造

位置：[`agents/model_factory.py`](../../tech_doc_agent/app/agents/model_factory.py)。

`build_assistant_model_provider(settings)`：

- 主模型使用 `ChatOpenAI(model=PRIMARY_MODEL or "gpt-4o-mini", temperature=0, max_retries=0)`；
- base URL 空字符串变 `None`；空 API key 变 SecretStr placeholder，允许静态构造但在线调用会安全失败；
- 只有 `BACKUP_MODEL` 和 `BACKUP_API_KEY` 同时存在才创建 backup；
- provider transport 的内置 retry 关闭，由项目的 `RetryExecutor` 管理；
- 返回 provider/model IDs 供 identity 使用。

`AssistantModelProvider.bind_tools` 分别给 primary/backup 绑定同一 tool schema；有 backup 时通过 Runnable `with_fallbacks` 组合。这个 fallback 与 transport retry 是两个概念：

- retry executor：同一 runnable 的可重试 provider 调用；
- model fallback：primary bound runnable 最终失败后尝试 backup runnable。

## `Assistant` 执行模板

位置：[`agents/assistant_base.py`](../../tech_doc_agent/app/agents/assistant_base.py)。

公开 sync/async 方法逻辑相同：

```text
for empty-output attempt in 0..max_empty_response_retries:
  1. 调 transport（可能内部多次 retry）
  2. 每次 transport attempt 前检查 workflow/request budget
  3. 收集成功/失败 attempt 的 LlmUsage
  4. 若 budget decision，返回内部 _budget_decision update
  5. 若结果为空，追加 HumanMessage("Respond with a real output.") 再试
  6. 否则退出循环
给结果补 Agent name
返回 {"messages": result, "_llm_usage": tuple(...)}
```

“空输出”定义：没有 tool_calls，且 content 为空、空字符串或 list 中没有非空 text block。有 tool call 即使 content 为空也算有效。

`_llm_usage` 和 `_budget_decision` 是 assistant execution wrapper 内部的临时键，`graph/budgeting.py` 记录后会 pop，不写入最终 State contract。

## 三层 retry 不要混淆

1. **空响应 retry**：模型成功返回但无内容/工具；最多默认 3 次重试；
2. **transport retry**：超时/限流/依赖错误；由 `RetryExecutor` 按幂等策略退避；
3. **backup model fallback**：primary runnable 失败后由 LangChain fallback 尝试备用模型。

每个 transport attempt 都可能消耗预算。失败 attempt 没有 token usage 时仍记为 unreported LLM call，避免成本账本把失败请求当免费。

## Graph wrapper 如何调用 `Assistant`

位置：[`graph/assistant_execution.py`](../../tech_doc_agent/app/graph/assistant_execution.py)。

`assistant_node` 在真正调用前：

1. 构造 scoped/full assistant state；
2. context tracker 测量 checkpoint 与实际 prompt 大小；
3. 读取当前 budget usage；
4. 生成 `before_llm_attempt(local_usages)` callback。

调用完成后：

1. context tracker 消费 `_llm_usage` 记录 input token/bytes；
2. budget tracker 消费 usage/decision；
3. 如果 reflection 正在 repair/finalize 且本次已无 tool call，重置 active reflection；
4. 返回可写入 graph 的 state update。

这解释了为什么 `Assistant` 本身不 import graph tracker：Agent 执行可独立测试，graph wrapper 才负责状态账本。

## Structured output 不是模型 JSON mode

parser/relation 仍输出带标题的 Markdown。finish node 用 [`core/structured_outputs.py`](../../tech_doc_agent/app/core/structured_outputs.py) 的 heading alias parser 转为 typed Pydantic model，再 `model_dump()` 写 state。

结果同时含：

- 规范化字段，例如 `key_concepts`；
- `raw_text`；
- `parsed` bool。

只要识别到任一标题，`parsed=True`；它不保证每个字段都有内容。下游必须把空 list/字段视为允许状态。解析完全失败时保留 raw_text，scope 不会丢掉原输出。

## Execution identity 解决什么问题

位置：[`agents/identity.py`](../../tech_doc_agent/app/agents/identity.py)。

每个 role identity 包含：role、prompt ID/hash、provider ID、primary/backup model ID。`RuntimeExecutionIdentity` 再加入 deployment commit status，并对排序后的 payload 做 SHA-256 fingerprint。

这个 identity 被：

- 写入 Runnable metadata；
- 写入每次 LangGraph config metadata；
- 可选通过 `/runtime/identity` 暴露；
- 用于 eval 判断运行结果是否与 prompt/model/commit 对应。

它不是鉴权信息，不应包含 API key/base URL。

## 修改 Prompt/Agent 时的常见坑

### 只改 prompt 文件，不改 manifest hash/ID

registry 会拒绝启动。预期流程是有意升级 ID/hash并运行 prompt tests；prompt 行为变化不应伪装成纯重构。

### 给模型绑定工具但没加到 safe/sensitive policy

模型能生成 call，subagent router 却无法正确归类。对有 safe tuple 的只读 Agent，未知 tool 目前会落入 safe node，随后 ToolNode 通常报工具不存在；对其他组合可能进入 sensitive/fallback。不要依赖这种兜底，应让 definition 与 graph policy一致。

### 把 control tool 加入 ToolNode

`CompleteOrEscalate`、handoff、PlanWorkflow 都由 graph 控制面消费。若作为普通工具执行，会得到错误 ToolMessage，而不会完成路由/计划写入。

### 改 role 名称

role 同时出现在 manifest、identity 顺序、Agent name、State dialog、SSE metadata、前端 union/颜色和测试。不是只改文件名即可。

### 开启 parallel tool calls

现有 router、审批和 tool result association 假设单次模型只发一个控制方向。开启前必须定义 mixed safe/sensitive、Plan+普通 tool 同时出现时的语义。

## 对应测试

- [`tests/test_assistant_registry.py`](../../tests/test_assistant_registry.py)：六角色装配和工具组合；
- [`tests/test_primary_assistant_tools.py`](../../tests/test_primary_assistant_tools.py)：primary 能力边界；
- [`tests/test_summary_assistant_tools.py`](../../tests/test_summary_assistant_tools.py)：summary 使用合并写工具；
- [`tests/test_prompt_registry.py`](../../tests/test_prompt_registry.py)：manifest、hash、path、placeholder；
- [`tests/test_assistant_base.py`](../../tests/test_assistant_base.py)：空响应、retry usage、budget callback、sync/async；
- [`tests/test_assistant_identity.py`](../../tests/test_assistant_identity.py)：role/model/deployment/fingerprint；
- [`tests/test_structured_outputs.py`](../../tests/test_structured_outputs.py)：Markdown heading 解析与 fallback。
