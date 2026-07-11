# Tool Result 显式 success/error 协议

## 本批目标

后端 `tool_result` SSE 过去只发送 `content`。前端 reducer 和 Inspector 用 `/error|exception|traceback/i` 扫描自然语言结果来推断失败：正常文档若包含 “Traceback” 会被误标红，不含这些英文词的中文/业务失败又会被当成功。

本批完成前端 SSE TODO F3 的最后一项：工具执行状态由产生 ToolMessage 的后端决定，并通过 SSE `status/error` 显式传递；前端只消费协议字段，不再解析结果文本。

## 协议

`tool_result` payload 现在稳定包含：

```json
{
  "tool": "read_docs",
  "tool_call_id": "call-1",
  "content": "request rejected",
  "status": "error",
  "error": "request rejected"
}
```

- `status`: `success | error`；
- `error`: error 时为错误文本，success 时为 `null`；
- `content`: 保持原工具结果展示/上下文内容；
- agent/node/tool/tool_call_id 保持不变。

后端 `ToolResultStatus` 与前端 `TOOL_RESULT_STATUSES` 都定义相同枚举，Python contract test 解析前端声明并与后端集合比较。

## 实际改动

### 1. 状态在 ToolMessage 产生处确定

当前 LangChain `ToolMessage` 原生支持 `status: Literal["success", "error"]`。本批显式标记三类失败：

- ToolNode exception fallback 生成的每个 dict message：`status="error"`；
- repeated call / parser retrieval budget policy block：`ToolMessage(status="error")`；
- 用户拒绝 sensitive tool：`ToolMessage(status="error")`。

普通工具返回、plan stored、agent entry/exit handoff 等成功 control result 保持 ToolMessage 默认 `success`。

### 2. Translator 统一映射

`_tool_result_payload` 成为 tool result 唯一翻译函数：提取 content，读取 ToolMessage.status，只允许 `error` 映射为 error，其余/旧 message 回退 success，并生成 error/null 字段。

sync/async runtime 共用同一个 `iter_update_events` translator，因此不新增镜像逻辑。

### 3. 前端完全删除文本启发式

- `SsePayloadMap.tool_result` 增加 status/error；
- reducer 用 `data.status === "error"` 映射 UI `error/done`；
- `inferToolStatus` 删除；
- Inspector lane marker 只看 `event.data.status`；
- event summary 对 error result 显示 `<tool> error`。

成功结果即使内容包含 “Traceback documentation” 也保持 done；失败内容即使没有任何 error 关键词也标为 error。

## 实施中遇到的问题

### 问题 A：status 应在 translator 推断还是在执行处产生

若 translator 继续根据 exception 文本或 node 名推断，只是把脆弱规则从 TypeScript 搬到 Python，协议仍没有真正 source of truth。

处理：使用 ToolMessage 原生 status；fallback、policy 与 rejection 在构造 ToolMessage 时决定语义，translator 只做受限映射。

### 问题 B：ToolNode fallback 先返回 dict，不是 ToolMessage

`handle_tool_error` 返回 message dict，随后由 LangGraph message conversion 转成 ToolMessage。只对 translator fixture 手工构造 error ToolMessage，不能证明真实 fallback 保留 status。

处理：除 direct dict test 外，新增真实 `create_tool_node_with_fallback` 测试，让工具抛 RuntimeError，断言最终 result 是 `ToolMessage` 且 status/error call id/content 均保留。

### 问题 C：policy block 与用户拒绝是否算 tool error

它们不是 Python exception，但对应工具没有执行成功；若标 success，UI 会显示绿色“完成”，与实际流程相反。

处理：两类均标 error。agent handoff/plan 等已成功完成的 control ToolMessage 仍为 success，避免把所有非工具业务文本一律标错。

### 问题 D：部署滚动期间可能收到旧 payload

新前端可能短暂连接到未带 status 的旧后端。强制把缺字段视为 error 会制造大量误报。

处理：reducer 对明确 `error` 才标 error，缺失/未知 status 兼容映射为 done；新后端 contract/tests 保证正常部署始终发送字段。

## 测试与门禁

新增/加强：

- translator success payload 包含 `status=success,error=null`；
- translator error payload 完整字段；
- ToolNode exception fallback 经过 LangGraph conversion 后仍为 error；
- repeated/budget policy ToolMessage status；
- approval rejection sync/async update status；
- reducer 以无关键词失败文本验证 error；
- reducer 以包含 Traceback 的成功文本验证 done；
- Inspector class/summary 只看 status；
- Python/TypeScript status enum 同步；
- architecture gate 禁止 reducer/Inspector 恢复 `error|exception|traceback` 启发式。

## 验证结果

| 验证 | 结果 |
|---|---|
| `npm run check` | passed |
| `npm test` | 16 files，66 tests passed |
| `npm run build` | passed，2040 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| tool/SSE focused pytest | 29 passed（含真实 ToolNode fallback） |
| 全量后端 pytest | 254 passed，4 warnings |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，10 source files |
| in-app browser production preview | Inspector/tool_result filter rendered；console warning/error 0 |
| `git diff --check` | passed |

浏览器 tab 与 4173 preview 已清理。pytest warning 仍为既有第三方弃用和本机 `.pytest_cache` 权限提示。

## 保持不变与后续工作

保持不变：SSE event name、tool content/call id、ToolCall UI 的 pending/done/error 三态、sync/async translator 入口和 Inspector filter。

后续：component tests 验证 ToolCallCard 三态展示；fake SSE integration 覆盖 tool success/error/interrupt/approve/done。
