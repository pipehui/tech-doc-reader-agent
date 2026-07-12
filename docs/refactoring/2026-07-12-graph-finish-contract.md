# 2026-07-12：Graph finish 节点行为契约与 CompletionPolicy 收口

## 本批结论

本批完成 B0 中缺失的 `finish_*` 行为刻画，并修复审计中发现的配置耦合：

- 五个 sub-agent 的 finish node 已确认共用同一个 `create_finish_node()`，没有再做重复抽取；
- `CompletionPolicy` 作为完整值对象传给 finish factory，不再由 builder 拆成两个松散参数；
- completion result key 收窄为 state 真正支持的三个字段；
- result key 与 structured kind 只允许四种有效组合，非法组合在 graph composition 阶段立即失败；
- 参数化测试锁定五个 Agent 对 dialog stack、plan index 与结果字段的差异化更新。

本批不改变 graph topology、node 名称、route target 或正常 state update。

## 审计结论：原 TODO 的“重复 finish 实现”已不成立

当前 `register_subagent()` 已为每个 `AgentSpec` 注册同一个 finish factory：

```text
finish_parser       -> create_finish_node(parser completion policy)
finish_relation     -> create_finish_node(relation completion policy)
finish_explanation  -> create_finish_node(empty completion policy)
finish_examination  -> create_finish_node(examination completion policy)
finish_summary      -> create_finish_node(empty completion policy)
```

因此继续抽“公共 finish 函数”只会制造第二层无收益封装。真正缺失的是：测试没有完整证明五种 policy 的行为，而 builder 把 `CompletionPolicy` 拆回 `result_key` 和 `structured_kind` 两个参数，削弱了值对象原本应该提供的约束。

## 重构前的风险

原 `CompletionPolicy` 允许任意 `str` result key 和任意 `ResultKind` 组合，例如：

```text
result_key="parser_result", structured_kind="relation"
result_key="examination_context", structured_kind="parser"
result_key=None, structured_kind="parser"
```

这些配置不会在启动时失败：finish node 可能把 RelationResult schema 写进 `parser_result`，把 dict 写进声明为 string 的 `examination_context`，或者直接忽略孤立的 structured kind。错误随后会跨越 state、message scoping 与 SSE translator 才暴露，定位成本很高。

## 实施方案

### 1. 收窄 completion state contract

新增 `CompletionResultKey`：

```text
parser_result | relation_result | examination_context
```

`CompletionPolicy.__post_init__()` 只接受：

| result_key | structured_kind | 语义 |
|---|---|---|
| `None` | `None` | explanation/summary 只结束步骤 |
| `parser_result` | `parser` | 解析并写 ParserResult |
| `relation_result` | `relation` | 解析并写 RelationResult |
| `examination_context` | `None` | 保存原始考试上下文字符串 |

这与 `State` 的字段类型、当前生产 `GraphSpec` 和 SSE structured-result 边界一致。

### 2. 保持 policy 内聚

`create_finish_node()` 现在接收一个 `CompletionPolicy`，builder 直接传 `spec.completion`。finish factory 不再重新组合两个可能不一致的 primitive 参数。

无结果 Agent 调用默认空 policy；默认值是 frozen dataclass，不引入可变共享状态。

### 3. 锁定五个 Agent 的终态差异

参数化测试逐一验证：

- 所有 Agent 返回 `dialog_state="pop"`；应用真实 `update_dialog_stack` reducer 后只弹出当前 sub-agent，保留下层 primary；
- 所有 Agent 将 `plan_index` 从 2 更新到 3；字段缺省时从 0 语义更新到 1；
- parser 只写 `parser_result` 且保留 `raw_text/parsed`；
- relation 只写 `relation_result` 且保留 `raw_text/parsed`；
- examination 只把最终文本写入 `examination_context`；
- explanation 与 summary 不写 parser/relation/examination 任一结果字段。

测试使用已有 `graph_spec` fixture，因此同时验证生产式 AgentSpec completion 配置，而不是另造一套测试映射。

## 实施中遇到的问题

### 1. 不能按 TODO 字面再抽一层 factory

TODO 来自较早代码快照；当前 AgentSpec 重构已经消除了五份 finish 注册代码。实际代码审计优先于机械执行清单，因此本批把目标改为“锁定已有单一实现 + 收紧配置入口”。

### 2. raw fallback 与 state 类型不是同一件事

B7 仍计划保留 parser/relation 对模型文本的 regex/section fallback，但 fallback 的产物依然是带 `raw_text` 和 `parsed=false` 的结构化 dict，不意味着可以让 `parser_result` 直接存 raw string。因此要求 parser/relation completion 必须带对应 structured kind，不会破坏当前迁移 fallback。

### 3. dialog stack update 不能只断言字符串

finish node 返回的是 reducer command `"pop"`，不是最终 list。测试同时调用 `update_dialog_stack()` 验证实际结果，避免把“写了 pop 字符串”误当成“stack 行为已锁定”。

## 验证状态

| 验证 | 结果 |
|---|---|
| finish/structured/router/topology targeted tests | 46 passed，2 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 修改范围 Ruff | passed |
| specs/nodes/builder mypy | 3 source files，0 issues |
| 全量 backend pytest | 606 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 全量 Ruff / mypy | passed；mypy 137 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未出现在 HEAD、origin/main 差异或待提交文件中 |

## 下一步

1. `CompletionPolicy` 新增结果类型时，必须先扩展 `State`、message scoping、SSE contract 与参数化 finish test，不能只加任意 result key。
2. B7 provider spike 仍应比较 structured submit 与当前解析 fallback；本批只保证 fallback 结果写入正确 state 字段。
3. 保持 finish factory 单一实现，不为每个 Agent 新建只转发参数的 wrapper。
