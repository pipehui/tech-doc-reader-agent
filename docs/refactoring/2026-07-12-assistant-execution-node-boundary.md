# Assistant Execution 与 Graph Lifecycle Node 分离

## 本批结论

本批把后续增长进 `graph/nodes.py` 的 assistant invocation 模板迁到独立 `graph/assistant_execution.py`：

- 移动 `_prepare_assistant_call`；
- 移动 `_complete_assistant_call`；
- 移动 execution-budget stop update；
- 移动 sync/async `assistant_node` RunnableLambda factory；
- 移动 assistant tool-free output 后的 reflection completion；
- builder 直接从新模块导入 assistant_node；
- budget/context/reflection tests 直接使用新事实源；
- `graph/nodes.py` 不保留 re-export；
- settings/runtime dependency gate 纳入新文件；
- 新增 ownership test，阻止 assistant execution 与 lifecycle factories 再次混合。

节点名称、RunnableLambda sync/async surface、GraphSpec、topology、budget/context usage、reflection 与 structured finish 行为均未改变。

## 两类 Node Lifecycle

### Assistant execution lifecycle

`assistant_execution.py` 负责一次 assistant 调用前后：

```text
State
  -> build scoped/full assistant state
  -> capture prompt/context metrics snapshot
  -> read current workflow budget
  -> build before-LLM-attempt guard
  -> invoke / ainvoke assistant
  -> map ExecutionBudgetExceeded to deterministic update
  -> record context metrics delta
  -> record token/cost/budget delta
  -> finish active reflection when output has no tool call
```

这些步骤围绕 LLM execution、accounting 和 reflection completion，和 graph entry/exit node 的状态切换不是同一职责。

### Graph lifecycle nodes

`nodes.py` 现在只包含：

- fetch user-info node；
- sub-agent entry node；
- sub-agent exit node；
- completion/structured finish node；
- primary tool-failure node；
- store-plan node。

它们负责 dialog stack、ToolMessage handoff、result key、plan 和 lifecycle state reset，不调用模型或 context/budget tracker。

拆分后 `nodes.py` 从 404 行降到 247 行，新 `assistant_execution.py` 为 165 行。行数只是结果，真正 gate 是两类依赖和生命周期各有唯一实现位置。

## Sync / Async 策略

Assistant node 仍保留两个短执行分支：

- sync 调用 `assistant(...)`；
- async 调用 `await assistant.ainvoke(...)`。

它们共同调用 `_prepare_assistant_call`、`_budget_stopped_update` 和 `_complete_assistant_call`，因此 scoped state、attempt guard、budget/context delta 与 reflection completion 只有一份规则。没有为了消除最后几行镜像而把 sync 强行在线程/事件循环间桥接，避免改变 LangGraph 调度和 cancellation 语义。

## Compatibility 策略

旧 `tech_doc_agent.app.graph.nodes.assistant_node` 没有 re-export。全仓调用只有 builder 和三个 focused test modules；它不是 package root public API，也没有外部协议承诺。所有调用方同批迁移后保留 shim 只会让 assistant execution 继续看似属于 lifecycle nodes。

Graph 对外构建入口、node names 与 topology snapshot 不变，因此运行时/checkpoint compatibility 不依赖该内部 Python import path。

## 实施中遇到的问题

### 问题 A：不能简单按前 170 行机械切割 imports

`reflection_active_reset` 同时被 assistant completion 和 exit/finish/failure/plan lifecycle nodes 使用。如果把 shared reset helper 随前半文件迁入新模块，nodes 会反向 import assistant execution，重新耦合两个职责。

处理：policy helper继续由 `graph/reflection.py` 拥有，两个 node 模块各自依赖它；只有 `_complete_reflection_state` 这一 assistant-output-specific 编排迁移。

### 问题 B：Budget/context tests 使用旧物理模块路径

Focused tests 直接 import assistant_node，以便注入 stub assistant/tracker 并验证 retry、usage、context metrics。只改 builder 会让测试收集失败，也可能留下旧路径被误认为兼容 API。

处理：三个 test modules 全部迁到新路径，并增加 source ownership test；全仓搜索确认旧 import 为零。

### 问题 C：新文件必须进入 settings-free execution gate

Execution policy 通过 GraphSpec/Composition 注入，不允许 node 在运行时重新读取 Settings。原架构测试列出相关 graph files，新增模块若未加入文件集合会形成检查盲区。

处理：把 `assistant_execution.py` 加入 `test_graph_execution_policy_does_not_read_settings_at_runtime` 的精确列表。

### 问题 D：不能借移动修改 sync/async control flow

Assistant execution 同时处理 provider retry usage、before-attempt budget stop 与 async assistant call。重写为抽象 executor 可能影响 exception timing 或 coroutine behavior。

处理：本批逐段无损迁移，保持 try/except 和 pre/post helper 调用顺序；sync/async 收敛只保留已存在的共享模板。

### 问题 E：Topology 绿色不等于 accounting 行为绿色

节点名和边完全不变时 topology test 会通过，但 budget/context/reflection delta 仍可能因 import/move遗漏改变。

处理：定向组合运行 graph budgeting、context metrics、reflection、compile/topology、finish 与 structured output tests，而不只跑 topology snapshot。

## 验证范围

| 验证 | 结果 |
|---|---|
| budgeting/context/reflection/compile/topology/finish/structured/architecture targeted pytest | 81 passed；3 个既有第三方/pytest-cache warning |
| graph mypy | 18 source files，0 issues |
| 全量后端 pytest | 709 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 151 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- Assistant/execution call/update 参数仍有第三方 `Any`/裸 dict，需等 LangChain runnable output contract 稳定后收紧；
- sync/async assistant 调用保留最小镜像分支，不用线程桥接追求形式去重；
- `nodes.py` 仍有 247 行，但其中 lifecycle factories 共享 dialog/reflection/result semantics；如继续拆，应按 user-info、handoff、completion 的真实变化频率，而不是行数；
- Graph `builder.py` 仍显式组装 node/edge，符合 topology 可审计目标，不改成动态 plugin graph；
- assistant_node 内部 import path 变更不影响 checkpoint node names，外部如果直接 import 私有路径需迁移。
