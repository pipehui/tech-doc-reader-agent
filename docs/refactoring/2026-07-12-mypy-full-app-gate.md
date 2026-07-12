# 2026-07-12：Mypy `check_untyped_defs` 与全应用 CI Gate

## 本批结论

本批把 mypy 从：

```text
tech_doc_agent/app/core
tech_doc_agent/app/api/schemas.py
```

扩大为：

```text
tech_doc_agent/app
evals
```

并将：

```toml
check_untyped_defs = false
```

改为：

```toml
check_untyped_defs = true
```

最终 136 个源文件通过，没有为本批错误增加 `type: ignore`、`# noqa` 或新的全局 `Any` 消音。

这意味着：即使函数本身尚未完整标注，mypy 也会检查函数体中的调用、返回值和容器类型；CI 同时覆盖 graph、runtime、API、retrieval、persistence、tools、services 与 eval runners。

## 为什么这是结构重构，而不只是改 CI 命令

原 gate 只有 21 个 core/schema 文件。graph/runtime 虽然是当前最关键的编排边界，但未进入 CI mypy 范围；`check_untyped_defs=false` 还会跳过大量未完整标注函数体。

直接扩大范围前先运行诊断：

```powershell
D:\Tools\miniconda3\envs\agent\python.exe -m mypy --check-untyped-defs \
  tech_doc_agent/app/core tech_doc_agent/app/api tech_doc_agent/app/runtime tech_doc_agent/app/graph
```

59 个文件只暴露 4 个错误，说明可以修真实边界，不需要通过缩小范围或 ignore 维持假绿。修复后继续检查整个 app，再把 evals 纳入，最终范围为 136 个文件。

## 修复的真实类型边界

### 1. ToolCall 不是任意 `dict[str, Any]`

`AIMessage.tool_calls` 的上游类型是 LangChain `ToolCall` TypedDict。原代码把它声明为 `list[dict[str, Any]]`，因为 mutable list 不协变，mypy 正确拒绝了这种替换。

修复：

- `budget_closed_tool_messages()` 接受 `Iterable[ToolCall]`；
- `last_ai_tool_calls()` 返回 `list[ToolCall]`；
- 继续通过 TypedDict 的 `id/name/args` 契约访问，不复制一个本地近似类型。

### 2. Prompt state 只需要 Mapping

full Agent 可能直接使用 `State` TypedDict，scoped/full-summary 路径可能构造普通 dict。`ContextMetricsTracker.snapshot()` 原来要求具体 `dict[str, Any]`，与真实只读用途不一致。

修复：参数改为 `Mapping[str, Any]`，与 core `measure_context()` 的只读边界一致，不为了类型检查额外复制 state。

### 3. Telemetry fallback literal 不属于 dialog-state enum

`dialog_state` 的元素只允许 primary 或五个 workflow step；异常日志在空 stack 时使用诊断标签 `subagent`。把两者直接放在同一个 list 表达式中，会迫使 mypy 把 `subagent` 当作 dialog enum 成员。

修复：先读取 dialog stack，再把日志字段显式声明为普通 `str`。状态 schema 没有被错误扩宽，诊断标签也保持不变。

### 4. LangGraph node callable 与 StateGraph 泛型

`create_budget_termination_node()` 返回精确的 `Callable[[State], dict[str, Any]]`。LangGraph `StateGraph.add_node()` 使用多组 callback Protocol overload，裸 callable 与未指定 node input schema 的组合在 mypy 中被推导为 `Never`。

修复：

- 定义 `GraphBuilder = StateGraph[State, None, State, State]`；
- 为 budget terminal node 显式设置 `input_schema=State`；
- 用 `RunnableLambda` 适配 LangGraph 的公开 Runnable 协议。

这不是 cast 或 ignore，并且与 LangGraph 运行时原本会做的 callable-to-runnable 适配一致。topology、budget termination 和 sync/async graph 测试负责证明行为不变。

### 5. Eval runner 的 list invariance

`add_messages()` 可以接受单个 BaseMessage，但 `list[HumanMessage]` 或 `list[BaseMessage]` 不能自动替代它声明的 mutable union list。

修复：synthetic session 逐条把 BaseMessage 交给 reducer。它同时更贴近 graph 每次接收单条消息 update 的语义，没有引入 cast。

## CI 与本地命令

CI：

```yaml
- name: Mypy
  run: mypy tech_doc_agent/app evals
```

本地：

```powershell
python -m mypy tech_doc_agent/app evals
```

`tests/test_typecheck_gate.py` 同时验证：

- `check_untyped_defs` 必须为 true；
- CI 必须使用完整 app/evals scope；
- `docs/development.md` 必须给出同一命令；
- workflow 不得静默退回旧 core/schema-only command。

## 本批没有宣称 strict

当前仍保留：

```toml
ignore_missing_imports = true
```

也没有开启：

- `disallow_untyped_defs`；
- `disallow_any_generics`；
- `warn_return_any`；
- 全局 `strict = true`。

因此准确结论是“所有当前 app/eval 函数体进入 staged mypy gate”，不是“全仓 strict typing 已完成”。后续应根据错误密度逐项开启，而不是一次制造大量无关注解改动。

## 验证状态

| 验证 | 结果 |
|---|---|
| 初始 core/api/runtime/graph audit | 59 files，4 errors |
| 修复后 core/api/runtime/graph | 59 files，0 errors |
| full app audit | 128 files，0 errors |
| final app + evals gate | 136 files，0 errors |
| targeted graph/typecheck tests | 35 passed，4 个既有 warning |
| 全量 backend pytest | 585 passed；3 个既有 deprecation warning 与 1 个本机 pytest cache 权限 warning |
| frontend test/build/audit | 19 files / 74 tests；2041 modules；0 vulnerabilities |
| Ruff / `git diff --check` | passed |
| `docs/todo` 隔离 | passed，任务单未进入 HEAD、origin/main 差异或待提交集合 |

## 下一步

1. 保持 136-file gate 稳定后，再评估 `disallow_untyped_defs` 的错误密度。
2. 对第三方 adapter 继续优先使用窄 Protocol/TypedDict，不用全局 `Any`。
3. scripts 与 tests 是否进入 mypy 应按实际收益单独评估；不要让辅助 fixture 注解淹没生产边界问题。
