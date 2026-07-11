# Phase 1 重构日志：SSE contract、翻译与编码边界

## 1. 重构范围

将 `api/routes/chat.py` 中与 HTTP endpoint 无关的 SSE 职责迁入 `api/sse/`：

```text
api/sse/
├── contract.py      # event name contract
├── events.py        # trace/session metadata 注入
├── encoder.py       # ServerSentEvent wire encoding/StreamingResponse
├── context.py       # sync/async trace context iterator
├── translators.py   # LangGraph messages/updates -> SSE events
└── streaming.py     # part stream 收束为 done/interrupt/error
```

`chat.py` 从 902 行降到 462 行，现在主要保留 tenant/trace 解析、guardrail use case、chat/approval workflow 和四个 endpoint。

## 2. 消除的重复与耦合

- sync/async part stream 共用 `events_from_stream_part`，不再各自解析 message/update part。
- endpoint 不再 import LangChain message/chunk 类型或直接理解 node update payload。
- SSE wire encoding 与业务 route 分离。
- event name 在后端和前端各有一个明确 contract 文件，并由跨端测试校验集合一致。
- `chat.py` 暂时 re-export 原测试使用的 helper，避免一次性破坏内部兼容；真实实现只存在于 `api/sse`。

## 3. 实际遇到的问题

### 问题 A：原 Roadmap 的 dispatch table 输入模型不正确

原建议按 `ai_message/tool_call/plan_update` 做 EVENT_TRANSLATORS，但这些是输出事件。LangGraph 输入实际分为 `messages` 和 `updates`；一个 update node 还能按顺序产生 transition、plan、structured result、AI message、tool call/result 多个事件。

解决：先按 part type 分流，再对 updates 运行有序 translator pipeline，不使用会丢失多事件顺序的一对一输出 dispatch。

### 问题 B：拆分时暴露前后端协议漂移

后端已经发送 `structured_result`，但前端 `EVENT_TYPES` 和 `applySseEvent` 都没有该事件，Inspector 会静默丢弃结果。

解决：

- 新增前端 `sseContract.ts`。
- Inspector event list 加入 `structured_result` 和 `guardrail_blocked`。
- stream handler 显式记录 structured result。
- Python contract test 读取前端 contract，后端/前端 event set 不一致时 CI 失败。
- 同步更新 `docs/api.md`。

### 问题 C：兼容 re-export 被 ruff 判定为未使用

测试仍从 `routes.chat` import SSE helpers。迁移后这些 import 只用于兼容，不被 route 本身调用，ruff F401 会失败。

解决：在 route module 的 `__all__` 明确声明兼容 public names；不复制 wrapper 实现。

### 问题 D：typing Literal 的运行时枚举方式

直接读取 `SseEventName.__args__` 能运行，但 mypy 将 Literal 视为 special form 并报错。

解决：使用标准 `typing.get_args(SseEventName)` 构造运行时 event set。

## 4. 验证结果

| 检查 | 结果 |
|---|---|
| SSE/observability/contract 定向测试 | 17 passed |
| 全量 pytest | 169 passed，3 个第三方 deprecation warnings |
| 全仓 ruff | passed |
| SSE/route direct mypy | passed，8 个 source files |
| frontend typecheck | passed |
| frontend production build | passed，2013 modules transformed |

本批提交主题：`refactor: separate sse protocol from chat routes`。

## 5. 后续工作

- 将 event payload 从裸 dict 提升为真正的 discriminated TypedDict/Pydantic model。
- 将 guardrail/chat/approval workflow 从 route 继续移到 application service。
- tool_result 增加显式 status/error code，前端停止解析自然语言判断失败。
- 前端 `applySseEvent` 改为可单测 pure reducer。
