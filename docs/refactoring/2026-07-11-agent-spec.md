# Phase 1 重构日志：Graph package 与 AgentSpec

## 1. 重构范围

将原 492 行 `app/graph.py` 拆为：

```text
app/graph/
├── __init__.py   # 兼容导出
├── builder.py    # composition 与 graph compile
├── nodes.py      # assistant/user-info nodes
├── routing.py    # next-step、primary、sub-agent router factory
├── specs.py      # AgentSpec/ToolPolicy/CompletionPolicy
└── state.py      # 兼容 re-export State/WorkflowStep
```

五个 sub-agent 改为声明 `SUBAGENT_SPECS`，由 `register_subagent` 注册共同 topology；primary supervisor 保持显式实现。

## 2. 消除的重复

原实现为每个 sub-agent 重复以下代码：

- entry/assistant/safe tool/sensitive tool/leave/finish node 注册。
- entry、tool return、leave、finish next-step edges。
- finish/leave/safe/sensitive route 判断。
- next-step conditional edge map。

现在共同 topology 和 route 优先级各只有一个实现。Agent 差异显式保留在 policy：

- safe/sensitive tools。
- scoped messages。
- result key 与 structured kind。
- display name。

## 3. 保持不变的边界

- primary 仍是显式 supervisor，不强行放入 AgentSpec。
- node 名称、所有 edge target、conditional source、interrupt node 不变。
- summary 继续读取完整 messages；另外四个 sub-agent 继续使用 scoped state。
- parser/relation/examination 的 finish result 行为不变。
- 旧 `from tech_doc_agent.app.graph import ...` 调用保持可用。
- display name 中原有的 `Assitant` 拼写暂时保留，避免在纯结构重构中混入 prompt/message 变化。

## 4. 实际遇到的问题

### 问题 A：闭包 router 丢失 Literal 路径推断

旧 router 分别写死 `Literal[...]` 返回类型，LangGraph 可以据此推断 conditional edge。router factory 返回闭包后，静态返回类型只能是 `str`，框架无法推断每个 Agent 的具体 path。

解决：`register_subagent` 根据 spec 显式构造 `path_map`。这不仅保证运行时路由，也保证 `get_graph()` 拓扑和可视化完整。

### 问题 B：不能用新的全局可变绑定替换旧重复

初版迁移曾考虑在 routing 模块 import 后再动态写入 `route_parser` 等全局变量。虽然兼容旧 import，但引入了初始化顺序和可变全局状态。

解决：builder 创建不可变 `SUBAGENT_SPECS` 和 route map，并在 package `__init__` re-export 兼容名称。graph builder 本身每次调用都创建新的 `StateGraph`，不再复用模块级 builder。

### 问题 C：空 tool policy 需要真实支持

如果 register 总是创建 safe ToolNode，未来没有 safe tool 的 Agent 会得到空 ToolNode；如果 router 默认返回 safe node，还会指向不存在或无意义的节点。

解决：只有非空 tool tuple 才注册对应节点和 path。为保持现有 relation/explanation 行为，没有 sensitive policy 时仍回到已有 safe ToolNode 处理；只有完全没有声明任何 tool policy 的 Agent 却产生 tool call 时才明确抛错。

### 问题 D：扩大 mypy 范围暴露旧依赖债务

直接对 graph package 跑 mypy 会跟随 import 检查旧 assistants/utils/message_scope，并暴露它们已有的 ChatOpenAI 参数、message union 等错误。这些不是本批新增，但会遮住新 graph 自己的问题。

解决：

- 修正新 graph 的 Hashable path map、Literal target 和 message tool_calls narrowing。
- 对新 package 使用 `mypy --follow-imports=skip` 验证直接代码，共 6 个 source files 通过。
- 旧依赖类型债务不加全局 ignore，保留到后续 staged mypy 重构。

## 5. 验证结果

| 检查 | 结果 |
|---|---|
| graph characterization 定向测试 | 28 passed |
| 全量 pytest | 164 passed，3 个第三方 deprecation warnings |
| 全仓 ruff | passed |
| graph direct mypy | passed，6 个 source files |
| 当前既有扩展 mypy 范围 | passed，18 个 source files |
| frontend typecheck | passed |

本批提交主题：`refactor: register subagents from explicit specs`。

## 6. 后续工作

- 将 `services/utils.py` 的 graph nodes/tool policy 移入 graph package。
- prompt/model construction 从 import-time global 迁到 bootstrap。
- primary route 的 tool dispatch 进一步表驱动，但保持 supervisor 显式。
- structured result 终态协议单独验证，不与本批混合。
