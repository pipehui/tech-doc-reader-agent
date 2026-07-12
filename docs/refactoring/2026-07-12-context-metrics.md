# Context Metrics 观测基础与压缩前不变量

## 本批目标

R3 的风险不是“消息多”本身，而是在没有数据和协议不变量时直接截断 `messages`：可能删掉最后一条用户输入、拆散 AI tool call 与 ToolMessage、破坏 sensitive approval resume，或让 summary 编造已经丢失的学习过程。

本批只建立观测基础，不执行历史删除或自动摘要：

- 每次 Agent invocation 记录完整 checkpoint 与实际 prompt 的 message count；
- 估算完整 state 与 prompt messages 的 UTF-8 JSON bytes；
- 从已存在的 `_llm_usage` 读取 provider input tokens；
- 按 Agent 与 `full/scoped` 分桶持久化；
- telemetry、SSE、REST 与前端 session state 同步交付；
- 新 request 重置本轮指标，approve/resume 保留当前 workflow 累计。

这样下一批可以用真实 `primary/summary` 增长曲线确定阈值，而不是用任意“保留最近 N 条”猜测。

## 审计结论

### 已有受控上下文

`parser/relation/explanation/examination` 已通过 `build_scoped_state` 获得：

- 一条结构化 HumanMessage task view；
- 当前标准 learning target、plan 与 plan index；
- 当前 Agent 对应 handoff/plan arguments；
- 按职责允许的 parser/relation structured result，且 parsed result 去掉 raw markdown；
- 仅当前 Agent、仅当前用户 turn 的 AI tool call 与匹配 ToolMessage；
- examination 必要时的上一轮考试上下文。

这些 Agent 的主要风险是 checkpoint 本身继续增长，不是每次 prompt 都拿到所有历史。

### 当前无界输入

- primary 使用完整 `state.messages`；
- summary 明确 `scoped_messages=False`，使用完整学习过程；
- checkpoint 使用 `add_messages` 追加，跨用户 turn 保留全部 raw messages；
- REST history/message_count 也基于完整 checkpoint。

### 压缩时不可丢不变量

下一批任何 compaction 实现必须保留：

1. 当前最后一条用户消息；
2. 所有尚未闭合的 AI tool call，以及同 id 的 ToolMessage 配对语义；
3. `workflow_plan/plan_index/learning_target`；
4. parsed `parser_result/relation_result`；
5. `examination_context` 与继续答题判断所需输入；
6. `dialog_state` 与 interrupt 前 sensitive tool call；
7. budget/reflection/context versioned state；
8. 能解释 summary 来源范围的 durable summary metadata。

本批把这些约束写入共享记录，但尚未生成 summary 或 RemoveMessage update。

## 领域模型

### `ContextSnapshot`

一次 Assistant 调用前的不可持久化快照：

- agent；
- scope：`full|scoped`；
- checkpoint message count / estimated serialized bytes；
- prompt message count / estimated serialized bytes。

checkpoint bytes 对完整 state 估算，因此包含 messages、structured result、plan、budget/context metrics 等；prompt bytes 只对真正交给 prompt template 的 `messages` 估算。系统 prompt/template 本身不在 prompt-message bytes 内，最终 provider input tokens 才是实际模型输入规模的权威观测。

### `AgentContextMetrics`

每个 Agent 维护：

- invocations 与真实 application LLM calls；
- last/max checkpoint message count 与 bytes；
- last/max prompt message count 与 bytes；
- reported input-token subtotal；
- unreported input-token call count；
- last input tokens；
- serialization 无法估算的 invocation 数。

只要该 Agent 存在一次未上报 input usage，累计 `input_tokens` 就为 null；reported subtotal 仍保留，绝不把 unknown 填 0。

### `ContextMetrics`

checkpoint schema version 1，包含 workflow 内 measurement 总数与固定 Agent map。`measurements` 必须等于所有 Agent invocations 之和，恢复时校验；损坏 payload 返回安全的 `context_metrics_invalid`，不会用空对象掩盖 checkpoint 漂移。

## 安全序列化估算

`estimate_serialized_bytes` 只返回整数或 null，不返回序列化正文：

- BaseMessage 使用 LangChain `message_to_dict`；
- dict/list/tuple/set 递归转换；
- Decimal 与日期使用稳定文本；
- cycle 与深度上限使用类型 marker；
- 未知对象只记录 qualified type，不调用其 `str/repr`；
- 最终用紧凑 UTF-8 JSON 计算 bytes；
- 任意估算异常 fail-open 为 null，并增加 unreported counter，不阻断主工作流。

telemetry 事件只包含 agent/scope/count/bytes/token 数字，不包含 prompt、用户正文、tool args、structured result 或序列化错误文本。

这里明确称为 estimated bytes，不冒充 RedisSaver/MsgPack 最终压缩后字节数。它用于同一实现版本内的增长趋势和阈值评估。

为避免领域累计与递归序列化重新形成巨型模块，versioned metrics/validation 位于约 293 行的 `core/context_metrics.py`，纯估算器位于约 84 行的 `core/context_serialization.py`，graph orchestration 位于约 134 行的 `graph/context_metrics.py`。

## Graph 接入

`ContextMetricsTracker` 与 `WorkflowBudgetTracker` 保持独立职责：

1. assistant_node 先构建 full/scoped prompt state；
2. context tracker 在模型调用前测量并写 `context.input.measured`；
3. Assistant 返回内部 `_llm_usage`；
4. context tracker先只读该 envelope，写 cumulative metrics/delta，但不 pop；
5. budget tracker随后定价、累计并 pop internal envelope；
6. graph update 中只剩纯 JSON `context_metrics` 与 `budget_usage`。

组合测试确认两套 tracker 消费同一内部 envelope 时都只累计一次，checkpoint 不含 `_llm_usage`。

START 的 user-info node 依次应用 context reset 与 budget reset。LangGraph approval resume 使用 `graph.stream(None, config)`，不经过 START，因此 context metrics 与 workflow budget 都不会被 approve 重置。

## 协议

- `context_metrics_update`：delta kind 为 `reset|assistant`，同时携带 cumulative metrics；
- session REST 增加可选 `context_metrics`；
- TypeScript reducer 记录 Inspector event，并把 cumulative metrics 写入 SessionState；
- refresh 后无需 SSE replay；
- 未知 SSE event 仍保持原 forward-compatible warning 行为。

## 实施中遇到的问题

### 问题 A：只数 `messages` 不能代表模型输入

scoped Agent 可能有 100 条 checkpoint messages，但 prompt 只有 1 条 task view；相反，primary 的系统 prompt 很长，即使 message 数少，provider input tokens 仍可能高。

处理：同一次 invocation 同时保留 checkpoint count/bytes、prompt count/bytes 和 provider input tokens，不用其中一个冒充另一个。

### 问题 B：usage envelope 已由预算层消费

若 context tracker 排在 budget tracker 后面，`_llm_usage` 已被 pop，只能从最终 AIMessage 再猜一次；排在前面又可能把 dataclass 写进 state。

处理：context tracker只读并保留 envelope，budget tracker是唯一 pop/定价 owner。两者的职责和调用顺序由 assistant_node 固定并有组合测试。

### 问题 C：观测代码不能因陌生 state object 阻断用户任务

直接 `json.dumps(state, default=str)` 可能调用第三方对象的 `__str__`，既可能失败，也会在未来错误日志中暴露正文。

处理：未知对象只转成类型 marker；cycle/depth 显式截断；估算异常返回 null。测试使用会主动抛异常的私有对象，确认不调用字符串转换。

### 问题 D：新 request 与 approval resume 的重置范围不同

如果每次 HTTP operation 都清空 context metrics，多次 approve 会让 workflow 的 summary/primary 增长看起来始终很小。

处理：只在 graph START 重置；approval resume 不经过 START。request elapsed 仍按每次 HTTP 单独计时，两种 scope 不混用。

## 验证范围

定向测试覆盖：

- UTF-8 bytes、cycle 与未知对象安全 fallback；
- full checkpoint 与 scoped prompt 的 count/bytes 差异；
- per-Agent full/scoped 分桶；
- known/unknown provider input-token 聚合；
- schema round-trip、measurement invariant 与 corrupt payload；
- START reset、approve-style累计；
- context/budget 同 envelope 组合；
- telemetry 不含私有正文；
- SSE reset/update、REST 与 TypeScript reducer/decoder；
- composition/topology 与 dependency direction。

| 验证 | 结果 |
|---|---|
| 本批 targeted Python pytest | 72 passed，3 个既有第三方 warning |
| 全量后端 pytest | 559 passed，3 个既有第三方 deprecation warning |
| 全仓 Ruff / mypy | Ruff passed；CI core/schema 19 source files；本批 direct 10 source files |
| frontend targeted | typecheck passed；2 files / 17 tests passed |
| frontend full test/build/audit | 19 files / 72 tests；2041 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed，任务单未进入 HEAD、origin/main 差异或待提交集合 |

## 下一批

- 用离线长会话 fixture 采集 primary/summary 增长分布；
- 定义 versioned conversation summary，带 source message id/range 与 prompt hash；
- 先只压缩已闭合的旧 turn，使用 LangGraph RemoveMessage reducer semantics；
- 为 pending tool pairs、current user turn、approval interrupt 与 scoped Agent isolation 建 failing fixtures；
- 比较回答一致性、checkpoint estimated bytes、provider input tokens 与 latency 后再决定阈值。

本批不宣称已经解决 context window overflow；它建立的是可验证、可回滚的观测前提。
