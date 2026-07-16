# 06 - 工具与 Application 边界

本章沿每个 LangChain tool 的参数追到最终 capability。核心区分是：`tools/` 拥有“模型看到的调用 schema 和序列化形式”，`application/` 拥有“业务命令、domain model、port 和用例”，`infrastructure/` 才拥有“怎么读写外部系统”。

## ToolBundle 是稳定能力目录

位置：[`tools/bundle.py`](../../tech_doc_agent/app/tools/bundle.py)。

`ToolBundle` 固定 11 个字段，`names()` 顺序也是测试 contract：

```text
web_search
read_docs
save_docs
search_related_docs
read_learning_history
read_all_learning_history
read_user_memory
upsert_learning_history
upsert_learning_state
read_user_profile
update_user_profile
```

Agent definition 依赖字段，不通过字符串全局查找工具。重命名 tool 不只是改 Python 函数名，还会影响 prompt、tool policy、dependency label、SSE/前端、eval 和历史 checkpoint 中未完成的 tool call。

## `ToolDependencies` 为什么不是 `AppResources`

位置：[`tools/dependencies.py`](../../tech_doc_agent/app/tools/dependencies.py)。

它只暴露工具真正需要的 capability：

```python
document_store: DocumentStorePort
document_retriever: DocumentRetrieverPort
learning_store: LearningRecordReaderPort
memory_store: MemoryReaderPort
learning_state_service: LearningStateCommandPort
profile_service: UserProfileServicePort
web_search: WebSearchPort
```

`ToolDependencies.from_container(resources)` 是 composition 边界的适配。tool 函数闭包捕获这个实例，所以不同 Runtime 可以拥有隔离的 fake/real dependencies；没有隐藏的 module singleton。

若工具直接依赖完整 `AppResources`，它可以无意访问 model price table、settings 或 concrete store 私有字段，测试也必须构造不相关对象。窄依赖让函数签名就是能力清单。

## 文档工具

位置：[`tools/documents.py`](../../tech_doc_agent/app/tools/documents.py)。

### `web_search(query: str) -> str`

调用：

```text
WebSearchPort.search(query)
  -> list[dict]
  -> json.dumps(..., ensure_ascii=False)
```

它不决定 Tavily/DDG fallback 和 retry；那是 concrete `WebSearchBackend` 的职责。

### `read_docs(query, category=None, tags=None, source=None) -> str`

输入先经 `_build_filters` 删除空值，然后：

```python
documents = document_retriever.retrieve(
    SearchQuery(query=query, filters=filters)
)
```

`SearchQuery` 默认 `mode="hybrid"`、`top_k=None`。工具不会把 `category="RAG"` 自己改成 tag；taxonomy normalization 在 retriever 内统一执行。

输出是 `list[SearchResult.to_dict()]` 的 JSON 字符串，模型能看到 title/content/source/metadata、融合 score、每路 signal 和最多两个 matched chunks。

### `search_related_docs(query, k, filters...) -> str`

与 `read_docs` 共用 retriever，但显式构造：

```python
SearchQuery(query=query, top_k=k, mode="vector", filters=filters)
```

vector mode semantic 失败时不会降级到 BM25，会向上抛错误；这符合“明确请求语义相关”的工具语义。

### `save_docs(title, content, source="", category=None, tags=None) -> str`

按固定顺序执行：

```text
document_store.add_documents([doc])
document_store.save()
document_retriever.refresh()
return success message with added_chunks
```

三步不能任意删：

- add 只更新当前内存 FAISS/index state；
- save 发布持久 generation；
- refresh 重建 HybridRetriever 的 normalized document/BM25 cache。

当前这三步不是跨 FAISS save 与 cache refresh 的事务：save 成功而 refresh 失败时，文档已持久化，下一次 runtime load 会恢复。错误处理/重试要按这个事实设计，不能简单重复 add，否则可能产生重复文档。

## 学习读取工具

位置：[`tools/learning.py`](../../tech_doc_agent/app/tools/learning.py)。

这些工具接收 `RunnableConfig`，LangChain 在调用时注入，不由模型填写。

### `read_learning_history(query, config) -> str`

```text
tenant_from_config(config)
  -> LearningRecordReaderPort.query_records(query, user_id, namespace)
  -> domain LearningRecord.to_payload()
  -> JSON string
```

### `read_all_learning_history(config) -> str`

同样按 tenant 调 `list_records`。它返回轻量 record，不包含知识正文；relation 若需要详细技术内容还要调用 `read_docs`。

### `read_user_memory(config, query="", limit=5) -> str`

调用 `MemoryReaderPort.query_memories`，返回学习过程观察。它与 profile 的稳定偏好不同。

## 学习写入工具与 injected tool call ID

`upsert_learning_history` 和 `upsert_learning_state` 都额外声明：

```python
tool_call_id: Annotated[str, InjectedToolCallId]
```

模型 schema 不需要生成它；LangChain 用当前 tool call ID 注入。工具再从 config 取 session/tenant，构造 `UpdateLearningStateCommand`。

命令的幂等 identity 是：

```text
(user_id, namespace, session_id, tool_call_id) -> SHA-256 key
```

所以同一次敏感调用在网络/审批恢复中被重复执行，UoW 可返回原结果；同 key 但参数 fingerprint 不同会报 conflict。

### `upsert_learning_history(...) -> str`

只提供 record 字段，仍调用统一 `learning_state_service.update(command)`，返回 `result.learning_message`。它没有直接 `learning_store.upsert_record()` 或 `.save()`。

### `upsert_learning_state(...) -> str`

除 record 外可带一个 memory：kind/topic/content/confidence。返回 `result.message`，即 learning message + memory message。

这两个 tool 复用同一 command/use case，区别只在模型 schema 和返回文案；事务规则没有复制。

## Profile 工具

位置：[`tools/profiles.py`](../../tech_doc_agent/app/tools/profiles.py)。

### `read_user_profile(config) -> str`

config tenant -> `UserProfileServicePort.get_profile` -> typed `UserProfile` -> `to_payload()` -> JSON。

### `update_user_profile(...) -> str`

LangChain tool 对外名通过 `@tool("update_user_profile")` 固定，Python 内部函数名是 `update_user_profile_tool`。它把可选更新字段原样传给 service，service 负责 merge、resolved weak topics、timestamp 和“无变化不写盘”。

`evidence` 不是 telemetry 原始证据，而是用户画像的 `last_update_reason` 文本；调用仍需遵循 prompt 中“用户明确要求更新”的规则并经过 sensitive tool approval。

## Application 层的四类内容

### Domain models

- [`learning_models.py`](../../tech_doc_agent/app/application/learning_models.py)：`LearningRecord`、`MemoryFragment`；
- [`profile_models.py`](../../tech_doc_agent/app/application/profile_models.py)：`UserProfile`、`UserProfileUpdate`、result。

它们负责 normalize、不可变更新和 payload 边界。例如 `LearningRecord.reviewed` 保持旧 score（当新 score 为 None）并将 reviewtimes +1。

### Commands/results

[`learning_commands.py`](../../tech_doc_agent/app/application/learning_commands.py) 的 `UpdateLearningStateCommand` 校验执行上下文和有限数值，生成 idempotency key、fingerprint、owner key。它描述“一次要做的更新”，不执行存储。

### Ports

- [`learning_ports.py`](../../tech_doc_agent/app/application/learning_ports.py)：record/memory reader、command service、mutation helper；
- [`profile_ports.py`](../../tech_doc_agent/app/application/profile_ports.py)：profile repository/service；
- [`retrieval.py`](../../tech_doc_agent/app/application/retrieval.py)：SearchQuery/SearchResult/DocumentRetrieverPort；
- [`approval_models.py`](../../tech_doc_agent/app/application/approval_models.py)：ApprovalRepository。

Protocol 放在 consumer/use-case 所在 application，而不是 concrete adapter 旁边。这样 tools、API 和 graph 可依赖稳定能力而不 import infrastructure。

### Use cases / policies

- `LearningStateService.update`：在一个 UoW mutation 中准备 record 和可选 memory；
- `UserProfileService`：读取、合并更新、构造 Agent context summary；
- `ApprovalService`：tenant-scoped pending request/get/pop 和日志；
- `evaluate_input_guardrail`：一次 risk evaluation + application disposition；
- `ExtractiveConversationSummarizer`：无 provider/persistence 的确定性摘要。

## 为什么 tool 返回 JSON 字符串而不是 domain object

LangChain tool result 最终进入 ToolMessage，模型需要稳定文本。domain object 留在 application/infrastructure 边界内，到 tool delivery 才序列化：

- 防止模型绑定到 dataclass/Pydantic 内部表示；
- 输出可 `ensure_ascii=False`；
- ToolMessage/SSE/history 更容易安全处理；
- application tests 可继续断言 typed object。

反方向同理：tool 参数先由 LangChain/Pydantic 校验，再构造 typed command/query，不把任意 dict 一路传到底。

## Tool error 在哪里转换

工具函数一般不 catch concrete dependency error。异常向上到 `graph/tool_nodes.py`：

1. `classify_error(exc, dependency=_tool_dependency(tool_name), tool=...)`；
2. 创建 error ToolMessage + artifact；
3. reflection policy 判断能否修参数；
4. SSE translator 只暴露安全字段。

`TOOL_DEPENDENCIES` 是 telemetry/error fallback 映射。新增外部依赖工具时要同步它，否则 error 可能缺少准确 dependency 名。

## 新增工具的完整修改路径

详版见 [11](11-change-recipes.md)，最小路径是：

1. 确认已有 application port 是否足够；不够先在 application 定义窄 capability；
2. infrastructure 实现 port，并在 `AppResources` 创建；
3. 扩展 `ToolResourceContainer` / `ToolDependencies`；
4. 在对应 `build_*_tools` 闭包中定义 schema 和序列化；
5. 加到领域 tools dataclass、`ToolBundle` 和 build mapping；
6. 明确分配给哪些 Agent，是 safe 还是 sensitive；
7. 若是外部依赖，补 error dependency/retry usage；
8. 同步 prompt、tests、eval 和文档。

## 常见坑

### 在工具里直接打开 JSON/Redis

这会把 storage schema、锁、错误映射和测试 fake 都泄漏到 tool adapter。应先有 application port/use case，再注入 concrete adapter。

### 用 trace ContextVar 取 tenant，忽略 RunnableConfig

tool 可能在线程中执行；必须优先 `tenant_from_config(config)`。这也是 config metadata 同时携带 tenant 的原因。

### 写工具没有 `InjectedToolCallId`

会失去稳定幂等键。用户批准重放、transport retry 或重复提交时可能重复写状态。

### `save_docs` 后忘记 retriever refresh

FAISS store 已更新但 HybridRetriever 的 BM25/index snapshot 仍可能使用旧 signature/cache，本进程马上读不到新增文档或使用旧排序。

### 把读工具标为 sensitive 或写工具标为 safe

前者会产生无意义审批、降低可用性；后者绕过人工确认。分类属于角色能力设计，不是工具函数自己能修正的。

## 对应测试

- [`tests/test_tool_bundle.py`](../../tests/test_tool_bundle.py)：稳定名称、实例隔离、typed query、save+refresh、config tenant；
- [`tests/test_primary_assistant_tools.py`](../../tests/test_primary_assistant_tools.py)：primary 读写边界；
- [`tests/test_user_profile_tools.py`](../../tests/test_user_profile_tools.py)：profile schema/service 委托；
- [`tests/test_learning_state_transaction.py`](../../tests/test_learning_state_transaction.py)：合并写、幂等和原子发布；
- [`tests/test_retrieval_contracts.py`](../../tests/test_retrieval_contracts.py)：SearchQuery/Result/port contract；
- [`tests/test_architecture_dependencies.py`](../../tests/test_architecture_dependencies.py)：tools 不 import infrastructure/runtime/services，application 不 import adapter/delivery。
