# Conversation Summarizer Application 边界

## 本批结论

本批将确定性摘要实现从 `services/conversation_summarizer.py` 迁到 `application/conversation_summarizer.py`：

- `core/conversation_summary.py` 继续拥有 `ConversationSummary` model 与 `ConversationSummarizer` port；
- `application/conversation_summarizer.py` 提供 `ExtractiveConversationSummarizer` 纯策略实现；
- `graph/context_compaction.py` 继续只消费注入的 port；
- `composition.py` 从 application 构造并注入具体策略；
- offline eval、pytest fixture 与 compaction tests 全部迁到新路径；
- 新增物理 ownership 断言，递归 `APPLICATION_CONTRACT` 自动扫描迁入实现。

摘要算法、`generator_id=extractive-closed-turns-v1`、裁剪 marker、message projection 和默认关闭策略均未改变。

## 为什么属于 Application

该实现没有调用模型 provider、外部 API、数据库或文件系统。它执行的是确定性用例策略：把已经由 graph 判定为“闭合”的消息投影成受限、可审计的摘要文本。

```text
core
  ConversationSummary + ConversationSummarizer Protocol

application
  ExtractiveConversationSummarizer implements Protocol

graph
  ContextCompactor consumes Protocol and updates graph state

composition
  chooses and injects concrete summarizer
```

放在 services 会暗示它是 provider 或远端 summarization service，也使未来代码容易从 graph 直接 import 具体实现。迁入 application 后，现有 contract 会阻断它依赖 graph/runtime/services/tools/infrastructure/API。

## 保持不变的安全边界

Extractive summarizer 仍按以下规则生成文本：

- human 只保留受 `max_entry_chars` 限制的可见文本；
- AI 保留可见文本和请求的 tool 名，不复制 tool args；
- ToolMessage 只保留 tool 名和 status，不复制 raw tool payload；
- 未识别的内部 message 不进入摘要；
- previous summary 与新 section 按固定 head/tail 策略压缩；
- summary ID、source ranges 与覆盖消息数仍由 core model 计算和验证。

这次目录移动没有解决离线 eval 已发现的 raw-tool-only 信息损失，因此没有顺手开启 context compaction，也没有把 provider-backed summarizer 引入 production。

## Compatibility 策略

仓内所有 production、eval 和测试调用方已迁到 application 路径。旧 `services.conversation_summarizer` 没有 re-export：它不是已发布的 delivery/API contract，保留 facade 会继续给 graph/application 之外的调用方提供错误 ownership。

外部若直接 import 旧内部路径，需要迁移到：

```python
from tech_doc_agent.app.application.conversation_summarizer import (
    ExtractiveConversationSummarizer,
)
```

算法级兼容由定向和全量测试保护；模块路径变化是本批明确的内部重构面。

## 实施中遇到的问题

### 问题 A：Port 已在 core，但 implementation 的目录仍表达错误

Graph 早已依赖 `ConversationSummarizer` Protocol，没有直接 import services，因此原 architecture test 一直绿色。但 production composition 仍从 services 构造具体实现，目录层面混合职责没有被现有 contract 指出。

处理：移动实现，并增加 composition source 断言，证明真实装配路径使用 application；application recursive contract 再保证新实现不能反向依赖外层。

### 问题 B：Eval 与 test fixture 也是正式调用方

只迁 production import 会让 offline context-compaction runner 或全局 graph fixture 在运行时失败；这些路径不一定被单个 focused unit test覆盖。

处理：全仓检索 class/module，迁移 eval、`tests/conftest.py` 与 compaction tests，并显式运行离线 eval tests、graph context metrics 和全量 pytest。

### 问题 C：不能把 ownership 改动包装成 compaction 质量改进

实现内容没有变化，离线 recall/size/token proxy 也没有新增数据。如果同时修改摘要文本或开启阈值，回归无法归因于代码移动还是策略变化。

处理：保持文件 blob 的算法主体、generator ID 和 settings 不变；记录只声明依赖方向改善，不声明 recall、cost 或 latency 收益。

## 验证范围

| 验证 | 结果 |
|---|---|
| compaction/eval/context/architecture targeted pytest | 62 passed；3 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 699 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 150 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- Context compaction 继续默认关闭，必须先解决 raw-tool-only recall 与版本化真实长会话基线；
- provider-backed summarizer 需要独立的 privacy、prompt identity、budget、retry 和 factuality 设计；
- `ConversationSummarizer` 当前使用 `Sequence[Any]` 兼容 LangChain messages，未来若收紧成内部 projection DTO，应单独验证所有 message content variants；
- `services` 仍包含 resource factory、retrieval/provider adapter 和 user-profile compatibility facade，继续按职责逐批迁移。
