# Phase 4 重构日志：SSE Message、Update 与 Part Translator 边界

## 1. 重构范围

最初的 SSE 拆分已经把 translator 从 chat route 移到 `api/sse/translators.py`，但该 13KB 文件仍同时处理：

- agent node/checkpoint/path metadata；
- streamed `AIMessageChunk` token content；
- AI/Tool message content normalization 与安全 error payload；
- LangGraph dict/tuple part envelope；
- transition、plan、structured result、usage、budget、context、provider retry、message/tool update pipeline。

`streaming.py` 需要一次从该文件 import 五个内部 helper，message/update 两种 part 的依赖仍无法独立理解或测试。本批按输入类型和共享 contract 拆为：

```text
api/sse/
├── agent_metadata.py      # metadata -> agent identity
├── message_translator.py  # chunk/content -> text
├── parts.py               # dict/tuple envelope parsing
├── update_translator.py   # ordered node-update -> SSE events
└── streaming.py           # translator selection + terminal events
```

旧 `translators.py` 删除。`api.sse.iter_update_events` package-level 入口、17 种 event contract、payload、顺序和 sync/async 输出保持不变。

## 2. 单向依赖

```text
streaming
  -> agent_metadata
  -> message_translator
  -> parts
  -> update_translator

update_translator
  -> agent_metadata.AGENT_NODE_NAMES
  -> message_translator.extract_text_from_content
  -> parts.extract_update_data
```

Update 中的 AIMessage/ToolMessage 仍需要与 streamed message 相同的 content normalization，因此 update 单向复用 message translator；message translator 不知道 update pipeline、SSE event 或 FastAPI。`parts.py` 只理解外层 dict/tuple shape，不 import core 或 FastAPI。

## 3. 实际遇到的问题与解决

### 问题 A：简单拆成 message/update 两文件会制造共享常量归属错误

`AGENT_NODE_NAMES` 同时用于 streamed metadata inference 与 update transition validation。复制集合会产生两个 agent identity 事实源；放在任一 translator 又会形成语义反向依赖。

解决：提取 `agent_metadata.py`，集中 agent name 集合与 metadata inference。Update 只消费 name contract，streaming 消费 inference。

### 问题 B：Part envelope 不属于任一 translator

dict part 使用 `type/data`，LangGraph tuple part 使用 `(type, data)`；message 与 update 都需要解析这一兼容边界。若分别实现，tuple support 很容易只在一侧漂移。

解决：`parts.py` 统一提供 `stream_part_type_and_data`、`extract_message_part_data`、`extract_update_data`。该模块没有 event 或 telemetry 责任。

### 问题 C：Update message content 不能复制 normalization

Update pipeline 生成 `agent_message`/`tool_result` 时同样要处理 string/list/dict content 和安全 tool error。把所有 message helper 都复制到 update 会恢复重复逻辑。

解决：只将无 SSE 副作用的 `extract_text_from_content()` 放在 message translator，并允许 update 单向复用；tool-result status/error mapping 仍属于 update event translator。

### 问题 D：公共 package API 与深层实现路径要区分

仓内测试和调用方通过 `tech_doc_agent.app.api.sse.iter_update_events` 使用稳定入口；只有一处 monkeypatch 指向旧实现模块的 `log_event`。没有生产调用方 import `api.sse.translators`。

解决：package `__init__` 改为从 `update_translator` re-export 同名函数，现有调用代码不变；测试 monkeypatch 改指新 owner。未保留 undocumented 深层 `translators.py` facade，因为该文件正是要删除的混合实现边界。若未来需要公开 Python SDK，应定义 versioned public package，而不是把内部文件路径当 contract。

## 4. Update pipeline 顺序保持

每个 node update 仍按以下顺序产生事件：

1. agent transition；
2. plan update；
3. parser/relation structured result；
4. usage update；
5. budget terminated / started；
6. context metrics；
7. provider retry usage；
8. AI message、tool call 或 tool result。

没有改成一对一 dict dispatch，因为单个 node update 可以产生多个事件且前端依赖稳定顺序。Malformed/unknown part、node 与 message 仍安全忽略并记录不含原始 payload 的 telemetry。

## 5. 架构守卫

- `translators.py` 不得恢复；
- streaming 必须显式组合 metadata/message/parts/update owners；
- message translator 不得依赖 `ServerSentEvent`；
- parts parser 不得依赖 FastAPI 或 core；
- package-level `iter_update_events` 必须来自 update translator。

## 6. 验证结果

| 检查 | 结果 |
|---|---|
| SSE event/payload/contract/observability/architecture 定向测试 | 82 passed，4 warnings |
| SSE package mypy | passed，11 个 source files |
| 全量 pytest | 717 passed，4 warnings |
| 全仓 Ruff | passed |
| app + evals mypy | passed，162 个 source files |
| 前端 Vitest | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | passed，2042 modules transformed |
| npm audit | 0 vulnerabilities |

四条 pytest warning 中三条是 LangGraph/LangChain/Starlette 第三方弃用提示，另一条是本机 `.pytest_cache` 无写权限；测试用例自身全部通过。

## 7. 后续约束

- 新 message part 形态进入 message translator；新 node-update event 进入 update translator。
- 新 LangGraph envelope 兼容形态只在 parts parser 添加。
- Event name/Pydantic payload contract 继续由 contract/payload modules 拥有，translator 不复制 schema。
- 不为每个 update event 再拆一个文件；当前 update pipeline 具有共同顺序和输入模型，保持内聚。

本批提交主题：`refactor: split SSE translators by part type`。
