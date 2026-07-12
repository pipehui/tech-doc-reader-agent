# Phase 4 重构日志：Chat Request、Guardrail Decision 与 Delivery 边界

## 1. 重构范围

本批继续收束 `api/routes/chat.py` 的混合职责。重构前，该文件同时拥有：

- 四个 FastAPI endpoint；
- tenant/trace 解析；
- input guardrail 风险评估与 disposition telemetry；
- blocked JSON 与 guardrail SSE payload；
- chat、guardrail approval、tool approval 三条 async event stream；
- trace-context iterator 与 `StreamingResponse` 组装。

重构后分为三层：

```text
api/routes/chat.py
  -> api/chat_delivery.py
       -> application/input_guardrails.py -> core guardrail/observability
       -> api/sse                       -> SSE contract/translator/encoder
       -> runtime/chat_runtime.py       -> injected runtime facade
```

- `routes/chat.py` 只保留 `get_runtime`、`resolve_trace_id` 和四个 endpoint。
- `application/input_guardrails.py` 只负责单次风险评估，以及 medium/high 的 warning/blocked disposition telemetry。
- `api/chat_delivery.py` 只公开 `chat_response` 与 `approval_response`，私有实现负责 guardrail JSON/SSE 投影、审批事件、async stream 与 trace-context response wrapping。

外部 URL、HTTP status、SSE event 名、payload、事件顺序、tenant/thread 规则和 runtime 调用顺序均未修改。

## 2. 消除的重复与耦合

### 删除 `guardrail_checked: bool`

原 route 先评估风险，再用 `guardrail_checked=True` 告诉 `astream_chat_events` / `astream_approval_events` 不要重复评估。这个布尔值没有携带实际决策，调用方可以误传 `True`，stream 也保留了第二套 high/medium 分支。

现在 `chat_response` / `approval_response` 各自只评估一次，随后在同一 delivery use case 内选择 blocked JSON、approval SSE 或正常 stream。下层 stream 不再接受“相信我检查过”的布尔旁路，也不再重复 guardrail 分支。

### Route 不再理解 SSE

上一批已经删除 route 的 SSE helper 兼容 re-export，但 route 当时仍通过 `_sse` 私有模块依赖组装 response。本批把这部分消费也移到 delivery：route 不 import `api.sse`，delivery 才是 SSE protocol 的 HTTP consumer。

### Application 不接触 HTTP/Runtime

没有把整段 chat workflow 粗暴移入 application。`ServerSentEvent`、`JSONResponse`、`StreamingResponse` 和 concrete `ChatRuntime` 都是外层能力；application 只保留可独立测试的 guardrail decision。这样满足现有递归依赖 contract：application 只向 core，API delivery 可以依赖 application 与 runtime facade。

## 3. 实际遇到的问题与解决

### 问题 A：第一次拆分只是换了参数类型，重复分支仍然存在

初版把 `guardrail_checked` 改成 `guardrail_risk: InputRisk`，route 传递真实结果，类型上比布尔值安全；但审查 diff 后发现 high/medium 分支仍同时存在于 route 和 `astream_chat_events`。这只是把重复逻辑搬到新文件，没有真正形成单一用例入口。

解决：把完整分支收口到 `chat_response` / `approval_response`，stream generator 只执行已选择的正常路径。architecture test 同时锁定 route 顶层函数集合和 delivery 仅有两个 public function，防止职责重新散回 endpoint。

### 问题 B：不能把 delivery 类型带进 application

最直接的“移动到 application service”会让 application import FastAPI SSE/Response 和 `ChatRuntime`，违反向内依赖方向，也会把协议编码与用例决策再次混合。

解决：拆出窄的 `evaluate_input_guardrail()`；它返回 core `InputRisk` 并记录安全 telemetry，不构造 payload。JSON/SSE 表示和 runtime stream 继续属于 API delivery。

### 问题 C：高风险 JSON 必须在 trace context 内构造

blocked response 会从当前 trace context 补充 `trace_id`、`user_id` 与 `namespace`。如果移动后先退出 context 再构造 JSON，返回字段会静默缺失。

解决：`chat_response` / `approval_response` 在同步 preflight context 内完成风险评估和 blocked response 构造；正常 SSE 则继续由 `aiter_with_trace_context` 在异步消费期间恢复 context。既有 route test 继续断言 blocked JSON 包含 trace ID。

### 问题 D：历史记录必须区分阶段事实

提交 `765390b` 的日志准确记录了“route 私有消费 `_sse`”；本批之后这句话不再代表当前架构。

解决：不覆写旧批次结论，而是在旧日志追加后续链接并注明对应提交，再由当前日志记录新边界。

## 4. 规模与公共表面

| 文件 | 重构前 | 重构后 | 职责 |
|---|---:|---:|---|
| `api/routes/chat.py` | 345 行 | 80 行 | request facade 与四个 endpoint |
| `api/chat_delivery.py` | 不存在 | 311 行 | 两个 delivery use case 及私有投影/stream 实现 |
| `application/input_guardrails.py` | 不存在 | 20 行 | 单次风险决策与 disposition telemetry |

本批不以总行数下降作为成功标准。拆分增加了显式接口和格式化行，但 route 的顶层函数从 15 个收敛为 6 个，delivery 对 route 只暴露 2 个入口。当前 delivery 的内部函数都服务同一 HTTP/SSE use case；若未来出现第二种 transport 或更多独立 guardrail endpoint，再按变化轴继续拆分，而不是仅按行数切文件。

## 5. 测试与架构守卫

- application guardrail test 参数化锁定 medium warning 与 high blocked telemetry，并确认不记录原始输入。
- route test 用 spy 证明普通 chat 每个请求只评估一次风险。
- architecture test 锁定 route 只能定义两个 request helper 和四个 endpoint。
- architecture test 锁定 delivery 只有 `chat_response`、`approval_response` 两个 public function，且 `guardrail_checked` 不得恢复。
- 既有 high-risk JSON、medium-risk approval、approve/reject、no-interrupt、SSE contract 与 observability 测试保持通过。

## 6. 验证结果

| 检查 | 结果 |
|---|---|
| SSE/guardrail/observability/architecture 定向测试 | 84 passed，4 warnings |
| 全量 pytest | 714 passed，4 warnings |
| 全仓 Ruff | passed |
| app + evals mypy | passed，153 个 source files |
| 前端 Vitest | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | passed，2042 modules transformed |
| npm audit | 0 vulnerabilities |

四条 pytest warning 中三条是 LangGraph/LangChain/Starlette 第三方弃用提示，另一条是本机 `.pytest_cache` 无写权限；测试用例自身全部通过。

## 7. 后续约束

- route 新增 endpoint 时只能处理 request schema、tenant/trace 和 delivery/application 调用，不在 route 内新增 stream generator 或 payload builder。
- application guardrail decision 不得 import FastAPI、SSE、runtime 或 persistence。
- delivery 可以依赖稳定 `ChatRuntime` facade，但不得直接依赖 graph、infrastructure 或 services。
- 若要统一 input guardrail approval 与 tool HITL 的 API-facing pending view，应先定义共同 response model，同时保留不同 resume strategy；不要把两种审批在 runtime 内强行合并。

本批提交主题：`refactor: separate chat delivery workflow`。
