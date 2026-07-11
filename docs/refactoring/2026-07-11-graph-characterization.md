# Phase 0 重构日志：Graph 行为刻画

## 1. 为什么先写 characterization tests

`graph.py` 的五个子 Agent 注册结构高度重复，但它们并不完全相同。如果直接抽 `AgentSpec`，很容易在减少行数的同时改变 safe/sensitive tool 分流、finish state、interrupt 或 next-step edge。

本批不修改 graph 实现，只把当前可观察行为固定下来，作为下一批 graph package/AgentSpec 重构的对照基线。

## 2. 固定的行为

### Graph topology

- START -> fetch_user_info。
- fetch_user_info -> primary 或 examination continuation。
- primary 的 plan/tool/handoff/end 分支。
- parser/relation/explanation/examination/summary 的 entry、assistant、safe tool、可选 sensitive tool、leave、finish。
- 每个 finish/store_plan 到五个 next-step entry 或 END。
- safe/sensitive ToolNode 执行后回到所属 Agent。

### Interrupt contract

当前只在四个 sensitive tool node 前中断：

- parser
- examination
- summary
- primary

relation/explanation 没有 sensitive node。

### Router contract

五个 sub-agent 都固定以下顺序：

1. 无 tool call -> finish。
2. `CompleteOrEscalate` -> leave。
3. safe tool -> safe ToolNode。
4. 有 sensitive policy 的 Agent 遇到写工具 -> sensitive ToolNode。

同时固定 workflow step -> entry node 的映射和完成/未知 plan 的 END 行为。

## 3. 实际遇到的问题

### 问题 A：只测“能 compile”无法保护重构

原 `test_graph_compile.py` 只断言 graph 对象存在且有 `astream`。删掉某个 interrupt、接错 finish edge，测试仍可能通过。

处理：读取 compiled graph 的公开 `get_graph()` 视图，比较每个 source 的 target set、conditional source 和 interrupt node。

### 问题 B：完整 edge 文本快照可读性很差

当前 graph 有大量重复 next-step edge。把所有 edge 写成一个长字符串 snapshot 虽然严格，但后续 review 很难判断变化含义。

处理：测试按 sub-agent policy 结构生成预期关系；仍覆盖全部 edge 语义，同时直接展示哪个 Agent 的哪个阶段不匹配。

## 4. 下一步使用方式

AgentSpec 重构必须先让本批测试保持全绿，再检查节点数/重复代码是否下降。若 topology 确实需要变化，应单独修改 characterization contract 并解释行为迁移，不能在“纯重构”中悄悄更新快照。

## 5. 验证结果

| 检查 | 结果 |
|---|---|
| graph topology/routes/compile 定向测试 | 26 passed |
| 全量 pytest | 162 passed，3 个第三方 deprecation warnings |
| 全仓 ruff | passed |
| 当前扩展 mypy 范围 | passed，18 个 source files |

本批提交主题：`test: characterize graph topology and routes`。

## 6. 完成状态

- [x] 固定 sub-agent topology。
- [x] 固定 conditional source 与 next-step targets。
- [x] 固定 sensitive interrupt nodes。
- [x] 参数化 safe/sensitive/finish/leave 路由。
- [x] 固定 workflow plan step mapping。
- [x] 全量验证通过。
