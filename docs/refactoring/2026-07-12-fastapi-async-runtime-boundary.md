# 2026-07-12：FastAPI async runtime 边界收口

## 本批结论

本批完成 B4 的调用面收口：

- CLI 保留并继续使用 `ChatRuntime` 的同步 facade；
- FastAPI 的 chat、approve、history、state 四个 endpoint 全部为 async；
- `api/routes/chat.py` 只调用 runtime 的 `astream_* / aget_* / ahas_*` surface；
- 删除 route 中三套无人调用的同步 guardrail/chat/approval SSE 编排；
- `SessionQueryService` 与 `ChatRuntime` 增加 `aget_history_view()`，补齐 history endpoint 所需的 async query facade；
- 新增 AST architecture gate，防止 FastAPI 路由重新调用同步 runtime 方法。

对外 URL、请求/响应 schema、SSE event 与 CLI 交互流程均未修改。

## 重构前的真实问题

`ChatRuntime` 早期同时服务 CLI 与 FastAPI，因此保留同步和异步两组 facade 是合理的；不合理的是 `chat.py` 自己也各维护一份同步和异步 HTTP use-case 编排：

- `stream_guardrail_approval_events` 与 `astream_guardrail_approval_events`；
- `stream_chat_events` 与 `astream_chat_events`；
- `stream_approval_events` 与 `astream_approval_events`。

当前 FastAPI endpoint 已全部走异步版本，三套同步 route helper 在生产代码和测试里都没有调用。继续保留只会让后续 guardrail、snapshot、approval 与 telemetry 规则存在两个修改点。

另外，state endpoint 已有 `aget_session_state()`，history endpoint 却只有同步 `get_history_view()`，导致两个同类查询在 delivery layer 使用不同调用面。

## 实施方案

### 1. 删除 delivery layer 的重复同步编排

删除三个仅存在于 `chat.py` 的同步生成器，不删除以下能力：

- `ChatRuntime.stream_user_message()` 与 `stream_approval()`：CLI 仍需；
- `api/sse/stream_parts_as_sse()`：保留 sync/async translator parity 与内部兼容 re-export；
- runtime/execution 内的同步规范实现：当前 async surface 仍通过受控 thread bridge 复用它。

因此这不是“把所有同步代码一刀切掉”，而是让同步能力只存在于真正有同步消费者的 facade/runtime 层，不再复制到 FastAPI delivery layer。

### 2. 补齐 async history query

`SessionQueryService.aget_history_view()` 使用 `asyncio.to_thread()` 调用唯一的同步序列化实现，`ChatRuntime.aget_history_view()` 只负责 facade 委托。history/state 两个 GET endpoint 现在分别 await：

```text
runtime.aget_history_view(...)
runtime.aget_session_state(...)
```

这保持 history 序列化单一来源，也与当前 `aget_snapshot/aget_session_state` 的短期 bridge 策略一致。

### 3. 用结构测试固定边界

新增架构测试解析 `chat.py` AST，并验证：

1. router 暴露的函数集合仍是 chat、approve、history、state；
2. 四个 route handler 都是 `AsyncFunctionDef`；
3. 整个 chat route 模块不得调用 `runtime.stream_user_message/stream_approval/get_history_view/get_session_state/has_pending_interrupt`；
4. CLI 仍显式调用同步 `ChatRuntime` facade，避免误删合法同步消费者。

相比只搜索 `async def`，AST gate 能区分方法调用与注释/字符串，也能在未来新增 route 时立即暴露集合变化。

## 实施中遇到的问题

### 1. async surface 目前不等于 native async graph

当前 LangGraph/checkpointer 主执行路径仍是同步实现，async facade 通过 iterator/thread bridge 复用。把本批描述成“完成原生异步迁移”会夸大事实。

本批只收敛 delivery 调用面；是否切换真正 async saver/graph API 仍需独立 benchmark，切换后应删除 bridge，而不是保留第三套路径。

### 2. history query 缺少对称 async facade

直接在 endpoint 写 `asyncio.to_thread(runtime.get_history_view, ...)` 会让线程策略泄漏到 API 层。最终把 bridge 放在 `SessionQueryService`，再由 `ChatRuntime` 委托；路由只表达“异步查询 history view”。

### 3. 不能顺手删除 SSE compatibility re-export

`routes.chat` 仍暂时 re-export 早期测试/内部调用使用的 SSE helper，真实实现已在 `api/sse`。本批目标是 runtime 调用面，不把兼容 import 删除混入同一变化；删除兼容层应先审计外部使用并单独记录。

## 代码结果

`chat.py` 从本批开始前的 506 行降到 395 行。减少的 111 行主要是重复控制流，不是把逻辑搬到另一个新文件；新增代码集中在 async history facade 和可执行架构约束。

## 验证状态

| 验证 | 结果 |
|---|---|
| architecture + runtime query + SSE route targeted tests | 44 passed，含 history/state endpoint component test；3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 修改范围 Ruff | passed |
| route/runtime/session mypy | 3 source files，0 issues |
| sync/async history view parity | passed，含 `include_tools=false/true` |
| 全量 backend pytest | 595 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 全量 Ruff / mypy | passed；mypy 137 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未出现在 HEAD、origin/main 差异或待提交文件中 |

## 下一步

1. 保持 CLI sync facade 和 FastAPI async surface 的消费者边界；新增 delivery layer 不应直接选择 graph sync API。
2. 若迁移 native async graph/checkpointer，先记录吞吐、首 token、取消传播与关闭语义 benchmark，再替换当前 thread bridge。
3. 单独审计 `routes.chat` 的 SSE compatibility re-export；确认无仓外消费者或提供迁移窗口后再删除。
