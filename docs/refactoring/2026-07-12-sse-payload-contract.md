# 2026-07-12：SSE 逐事件负载契约与异常策略

## 本批结论

本批把 SSE 从“只有 event 名集合，payload 仍是任意字典”推进为可执行的逐事件协议：

- 后端为当前 17 种 SSE event 建立 Pydantic payload model，并在创建 `ServerSentEvent` 前统一校验；
- 前端在 wire parser 边界运行逐事件 decoder，只有通过校验的数据才能进入 discriminated union 和纯 reducer；
- Python 与 TypeScript 共同读取 `contracts/sse_v1_examples.json`，保证 event 集合和最小合法 payload 同步；
- synthetic LangGraph parts 的确定性 event 序列成为 golden contract，sync/async stream 必须产生完全相同的 payload；
- 未知未来 event、已知 event 的畸形 payload、LangGraph 内部未知 part/node/message 分别采用明确且有测试的策略。

这批没有修改 event 名、URL 或正常 payload 的既有字段语义。

## 重构前的真实问题

`api/sse/contract.py` 已经约束 event 名、tool result status 和部分错误字段，但 `sse_event()` 接受任意 `dict`。因此下面几类错误只能等到浏览器 reducer 运行时才暴露：

- producer 漏发必需字段；
- 字段类型漂移，例如 token text 变成数字；
- 拼错字段名或意外附加后端内部字段；
- 前端把“已知事件但 payload 已损坏”和“未来版本新增的未知事件”都当成宽松对象处理。

前端原 `SsePayloadMap` 的字段大多是 optional `unknown`。它虽然能约束 switch 是否穷尽 event 名，却不能证明 reducer 收到的字段可用，导致字段检查、默认值和容错逻辑重复分散在各个 reducer 分支。

## 后端边界

新增 `api/sse/payloads.py`，其职责只有两项：逐事件 payload model 与统一验证函数。共同 trace 字段由 `SsePayload` 基类维护，业务字段由各 event model 声明。

关键约束：

1. `extra="forbid"`。后端 producer 多发未知字段通常意味着拼写错误或内部结构泄漏，应在测试/开发阶段立即失败。
2. trace context 先注入、后校验。这样 `trace_id/session_id/user_id/namespace` 与业务 payload 走同一契约，而不是绕开验证。
3. 未知输出 event 是 programmer error。后端不能悄悄发送不在公开 event 集合中的类型。
4. `plan_update` 至少包含 `plan/plan_index/learning_target` 之一，避免产生没有语义的空更新。
5. `usage/budget/context` 的嵌套对象本批只校验对象边界；它们已有各自版本化领域结构，后续若收紧应复用其 schema，而不是在 SSE 层复制第三份模型。

## 前端边界

新增 `streaming/ssePayloads.ts`，将 wire object 解码成精确的 `SsePayloadMap[EventType]`。`parseSseMessage()` 现在产生三种结果：

- `event`：已知 event 且 payload 合法，可进入 reducer；
- `unknown`：未来版本 event，保持 forward-compatible；
- `invalid`：当前版本已知 event，但 payload 违反契约。

前端与后端故意采用不同的新增字段策略：后端 producer 拒绝 extra field，前端 consumer 保留并允许 additive field。这支持滚动发布时“新后端先增加可选字段、旧前端仍可消费”，同时仍能尽早发现后端内部拼装错误。

`session_snapshot` 没有重新实现一套 session decoder，而是复用 REST 边界已有的 `decodeSessionState()`；SSE 与 REST 因此共享 session 字段语义。wire 中为 `null` 的可选 budget/context 状态会被规范化为 Store 使用的缺省字段，避免类型声明为 optional、运行时却偷偷保留 `null`。

## 异常策略矩阵

| 位置 | 情况 | 策略 | 用户流是否中断 |
|---|---|---|---|
| 后端 event producer | 未知公开 event 或已知 event 的非法 payload | 抛出验证错误，由 stream error 边界转为安全 `error` event | 是 |
| 后端 LangGraph translator | 未知/畸形 part、node update、message | 忽略并记录 `sse.translation.ignored`，只记录 reason/type/node 等安全诊断字段 | 否 |
| 后端 LangGraph translator | `RemoveMessage` | 视为已知内部控制消息，不转成公开 SSE，也不记异常 | 否 |
| 前端 parser | 未知未来 event | 产生 `protocol_warning(reason=unknown_event)`；开发模式告警，业务状态不变 | 否 |
| 前端 parser | 已知 event 的畸形 payload | 产生 `protocol_warning(reason=invalid_payload)` 和显式 `stream_error` | 是 |

后端 translator 的 telemetry 不记录原始 part/message 内容，测试用私密哨兵值验证其不会进入日志字段。

## 实施中遇到的问题

### 1. 原测试依赖“不可能的部分 payload”

旧 reducer 测试经常只传当前断言需要的一个字段，例如空 token 或缺少 node 的 tool result。启用 runtime decoder 后，这些测试首先失败。最终没有放松生产契约，而是让每个测试以共享合法 example 为基底，再覆盖当前场景字段；畸形输入改为独立 protocol-error 测试。

这也暴露了一个真实语义：tool result 的 `node` 是执行工具的 LangGraph node，例如 `parser_assistant_safe_tools`，不能在测试里凭空改写为业务 agent 名。

### 2. 提前解构破坏 TypeScript discriminated-union 收窄

在 reducer switch 前写 `const { type, data } = envelope` 后，TypeScript 不能稳定保留 `type` 与 mapped payload 的关联。最终把 `const data = envelope.data` 放进每个 case，让编译器依据 `envelope.type` 精确收窄；没有使用 `any` 或为每个分支手工断言。

### 3. 测试辅助代码使用了虚构 event 名

trace wrapper 测试曾用 `first/second` 作为无业务意义的 event。后端开始拒绝未知 event 后，这类测试不再合法，改用最小合法的 `done/no_pending_interrupt`，仍只验证 trace context 生命周期。

### 4. “静默忽略”原本无法区分兼容与数据损坏

LangGraph stream 可能出现框架内部控制消息或版本新增 part。全部抛错会让兼容性过脆，全部静默忽略又会隐藏升级回归。最终采用“内部未知输入安全忽略 + 原因 telemetry；公开已知协议损坏显式失败”的分层策略。

## Golden contract

契约分为两层：

1. `contracts/sse_v1_examples.json`：17 种 event 的最小合法 payload，由后端 Pydantic 测试和前端 Vitest 同时读取；任一端缺 event 或字段不兼容都会失败。
2. synthetic stream golden test：固定 messages/updates parts，精确比较 token、plan、transition、agent message、tool result、done 的顺序与完整 payload，并要求 sync/async 输出完全一致。

共享 JSON 是可执行兼容样例，不是完整 JSON Schema。当前没有加入新的 wire `schema_version` 字段，以免把内部重构变成协议迁移；如果未来需要自动 codegen，应独立设计版本协商与兼容窗口。

## 验证状态

| 验证 | 结果 |
|---|---|
| 后端 SSE payload/translator/contract targeted tests | 29 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 后端 SSE 范围 Ruff | passed |
| 后端 SSE 范围 mypy | 8 files，0 issues |
| 前端 payload/parser/reducer/store-adapter targeted tests | 3 files / 29 tests passed |
| 前端 typecheck | passed |
| 全量 backend pytest | 593 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 全量 Ruff / mypy | passed；mypy 137 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未出现在 HEAD、origin/main 差异或待提交文件中 |

## 下一步

1. 若继续收紧 `usage/budget/context` 嵌套结构，优先从其领域模型生成/复用 schema，不在 transport 层复制字段。
2. 新增 SSE event 时，必须同时增加后端 model、共享 example、前端 decoder/reducer 和异常用例。
3. 若要从共享样例升级到 schema/codegen，先定义 protocol version 与 additive/breaking change 规则，再替换当前手写双端模型。
