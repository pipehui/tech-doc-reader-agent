# 07 - 学习状态、用户画像与持久化

本章回答一个很具体的问题：当模型说“记住用户已经学会了 RAG”时，数据究竟经过哪些函数、最终写到哪里、失败后内存里会不会留下半份修改。

先不要把所有 JSON、Redis 和 checkpoint 都叫作“记忆”。项目里至少有六类状态，它们的所有者和一致性要求不同。

## 六类状态不要混在一起

| 状态 | 主要内容 | 真正所有者 | 持久化位置 | 写入入口 |
| --- | --- | --- | --- | --- |
| 学习记录 | 知识点、分数、复习次数 | `LearningStateUnitOfWork` | `learning_state/generations/*/state.json` | `upsert_learning_history` / `upsert_learning_state` |
| 长期记忆片段 | 已掌握、卡点、误解、复习提示 | 同一个 `LearningStateUnitOfWork` | 与学习记录同一份 snapshot | `upsert_learning_state` |
| 用户画像 | 经验等级、解释偏好、强弱项 | `UserProfileService` | `user_profiles/<user>/<namespace>.json` | `update_user_profile` |
| 图运行状态 | messages、dialog stack、当前计划等 | LangGraph checkpointer | Redis | graph invoke/stream |
| 中风险输入审批 | 尚未运行的原始问题 | `ApprovalRepository` | Redis + TTL | `/chat` guardrail 分支 |
| 浏览器展示缓存 | transcript、tool card、Inspector event | 前端 store/repository | `localStorage` | SSE reducer + adapter |

这张表直接决定修改边界。例如，“把复习次数也显示在前端”需要读 learning state API 和前端 learning slice，不应去改 LangGraph checkpoint；“恢复工具审批”需要看 checkpoint，不应从浏览器 transcript 推断。

## 学习记录和记忆片段的 domain model

位置：[`application/learning_models.py`](../../tech_doc_agent/app/application/learning_models.py)。

### `LearningRecord`

字段：

```python
knowledge: str
timestamp: str
score: float
reviewtimes: int
tenant: TenantContext
```

主要构造函数及其输入输出：

- `LearningRecord.create(...) -> LearningRecord`：去掉字符串首尾空白；非法、无穷或 NaN 分数变成 `0.0`；复习次数至少为 `0`。
- `LearningRecord.from_payload(mapping, fallback_tenant=...) -> LearningRecord`：把旧 JSON 行恢复成 domain model，并补齐 tenant。
- `record.reviewed(timestamp, score) -> LearningRecord`：返回新对象，复习次数加一；`score=None` 时保留旧分数。
- `record.to_payload() -> dict`：只在持久化/API 边界重新转成 JSON-like 数据。

`LearningRecord` 是 frozen dataclass。更新不是原地改字段，而是产生新值。这样 `LearningStateUnitOfWork` 才能先在候选 snapshot 上修改，写盘成功后再替换当前状态。

### `MemoryFragment`

字段包括：

```text
id, tenant, kind, topic, content, confidence,
source_session_id, created_at, updated_at
```

`kind` 只有四种有效值：

- `learned`：已掌握或稳定理解；
- `stuck_point`：卡点；
- `misconception`：已识别的误解；
- `review_hint`：后续复习提示。

`MemoryFragment.create(...)` 会把 confidence 限制在 `0.0..1.0`，未知 kind 回退为 `learned`。`updated_from(...)` 保留原 `id` 和 `created_at`，更新内容、置信度和 `updated_at`。

注意：这里的“容错归一化”是读取旧数据和接收模型输出时的防御，不代表 API 可以随意传错。新增严格输入校验时，应放在 command/API schema 边界，而不是让 domain model 到处抛 HTTP 相关异常。

## 一次学习状态写入的完整调用链

入口主要是 [`tools/learning.py`](../../tech_doc_agent/app/tools/learning.py) 的 `upsert_learning_state`。调用链如下：

```text
LangGraph ToolNode
  -> upsert_learning_state(..., config, tool_call_id)
  -> 从 RunnableConfig 解析 tenant/session
  -> UpdateLearningStateCommand(...)
  -> LearningStateService.update(command)
  -> LearningStateUnitOfWork.execute(command, mutate)
       1. 检查幂等键
       2. clone 当前 snapshot
       3. LearningStore.prepare_upsert_record(...)
       4. 可选 MemoryStore.prepare_upsert_memory(...)
       5. 记录 command 结果
       6. repository.save(candidate)
       7. 写盘成功后替换当前 snapshot
  -> UpdateLearningStateResult.message
  -> ToolMessage
```

### command 为什么必须带 `tool_call_id`

位置：[`application/learning_commands.py`](../../tech_doc_agent/app/application/learning_commands.py)。

`UpdateLearningStateCommand` 的必填上下文是：

```python
tenant
session_id
tool_call_id
knowledge
timestamp
```

`tool_call_id` 由 LangChain 的 `InjectedToolCallId` 注入，不让模型自己生成。command 生成两个不同摘要：

```python
idempotency_key = sha256((user_id, namespace, session_id, tool_call_id))
fingerprint     = sha256(command 的全部字段)
```

二者用途不同：

- 同一个 key、同一个 fingerprint：说明相同 tool call 被重放，直接返回第一次的结果，`replayed=True`；
- 同一个 key、不同 fingerprint：说明有人复用了 tool call ID 却改变参数，抛 `learning_idempotency_conflict`；
- 不同 key：正常执行新命令。

如果只存 `tool_call_id`，不同用户或不同 session 可能碰撞。如果只存 fingerprint，同样参数的两次合法复习会被错误去重。因此身份和内容必须分开。

### `LearningStateService.update`

位置：[`application/learning_state.py`](../../tech_doc_agent/app/application/learning_state.py)。

它定义业务动作，但不直接打开文件：

1. 调 `LearningStore.prepare_upsert_record` 生成新的 records 列表和提示文本；
2. 仅当 `memory_content` 非空时调 `MemoryStore.prepare_upsert_memory`；
3. 返回 `UpdateLearningStateResult`，由 UoW 连同 fingerprint 一起保存。

学习记录的匹配键是 `tenant + knowledge`，目前 `knowledge` 是大小写敏感的精确相等。记忆片段的匹配键是 `tenant + kind + topic`，同样是精确相等。若要改成大小写不敏感或别名合并，必须同时考虑旧数据迁移和幂等测试，不能只在读查询里改。

### `prepare_*` 为什么不自己保存

[`infrastructure/persistence/learning_store.py`](../../tech_doc_agent/app/infrastructure/persistence/learning_store.py) 与 [`memory_store.py`](../../tech_doc_agent/app/infrastructure/persistence/memory_store.py) 的 `prepare_upsert_*` 都只接受一个序列并返回候选序列：

```python
records, message = learning_store.prepare_upsert_record(records, ...)
memories, memory = memory_store.prepare_upsert_memory(memories, ...)
```

这样一次 tool call 同时更新学习记录和记忆片段时，不会先保存 records、再在 memories 失败后留下半次提交。真正 commit 只发生一次。

`upsert_record()`、`upsert_memory()` 和 `records`/`memories` 的 dict view 仍存在，主要服务兼容调用和测试。它们只替换 UoW 内存候选；业务 tool 的正式写路径应走 command service。

## Unit of Work 如何避免半提交

位置：[`application/learning_unit_of_work.py`](../../tech_doc_agent/app/application/learning_unit_of_work.py)。

核心对象 `LearningStateSnapshot` 一次包含：

```python
records: list[LearningRecord]
memories: list[MemoryFragment]
processed_commands: dict[str, dict]
generation: str | None
```

`LearningStateUnitOfWork.execute` 在一个 `threading.Lock` 内执行：

```text
当前 snapshot A
  -> clone 为候选 B
  -> mutation(B)
  -> B.processed_commands[key] = result
  -> repository.save(B) 返回已重新读取并验证的 C
  -> self._snapshot = C
```

如果 mutation 或 save 抛异常，最后一步不会发生，进程内的活动状态仍是 A。幂等结果和业务数据也在同一 snapshot，因此不会出现“数据已写、幂等记录没写”或反过来的情况。

这里的锁是**进程内锁**。如果未来用多个后端进程共享同一个本地 `DATA_PATH`，不同进程各有自己的 UoW 和锁，不能靠它获得跨进程串行化。届时应切到支持事务/乐观版本检查的共享存储，而不是再套一层 Python lock。

## generation snapshot 怎么发布

位置：

- [`infrastructure/persistence/learning_state_repository.py`](../../tech_doc_agent/app/infrastructure/persistence/learning_state_repository.py)
- [`infrastructure/persistence/generations.py`](../../tech_doc_agent/app/infrastructure/persistence/generations.py)
- [`infrastructure/persistence/atomic_json.py`](../../tech_doc_agent/app/infrastructure/persistence/atomic_json.py)

目录形状：

```text
DATA_PATH/
  learning_state/
    current.json
    generations/
      <32位十六进制 generation>/
        state.json
```

一次 `LearningStateSnapshotRepository.save(candidate)`：

1. `GenerationStore.draft()` 创建全新 generation 目录；
2. 构造带 schema version、generation、创建时间和三类 count 的 manifest；
3. 校验内存 candidate；
4. `write_json_atomic(.../state.json)` 写 snapshot；
5. 从新 generation 重新读取、反序列化并校验 count；
6. 原子替换 `current.json`，让新 generation 成为当前版本；
7. 返回重新读取的 typed snapshot。

`write_json_atomic` 先在目标目录写临时文件，`flush + fsync` 后用 `os.replace` 替换目标。读者只会看到旧完整文件或新完整文件，不会看到写了一半的 JSON。

如果在发布 `current.json` 之前失败，未发布 draft 会被清理。如果原子替换可能已经开始，代码宁愿保留目录也不冒险删除一个可能已经被 manifest 引用的 generation。多余 generation 可通过 `GenerationStore.inventory()` 识别，但清理策略不在请求写链里自动执行。

### manifest 为什么还要存 count

`state.json` 有 schema version，`current.json` 还保存 records、memories、processed_commands 数量。加载时二者必须匹配。它不是密码学完整性校验，但能捕获常见的错 generation、截断、人工误改和发布步骤不一致。

## 启动加载和旧格式兼容

`LearningStateSnapshotRepository.load()` 的顺序是：

1. 有 `learning_state/current.json`：只读取 manifest 指向的新 snapshot；
2. 没有 manifest，但有旧文件：读取 `learning_store/records.json` 与 `memory_store/memories.json`；
3. 两者都没有：返回 `None`，UoW 保持空 snapshot。

旧文件只是**读取 fallback**。下一次保存会生成新的 generation snapshot，不会继续双写旧文件。这种设计避免长期维护两套写协议。

迁移时容易踩的坑：

- 不要在已有 manifest 损坏时静默退回旧文件，否则会把较新的数据伪装成“消失”；
- 不要删掉 `processed_commands`，否则 checkpoint 重放旧 tool call 时可能重复增加复习次数；
- schema 变更要升级版本并提供显式转换，不能让 `from_payload` 猜所有未来格式；
- 手工复制 generation 时要连同正确 manifest 一起处理。

## 查询适配器的行为

### `LearningStore`

正式读取方法：

- `query_records(query, tenant...) -> list[LearningRecord]`：先按 tenant 隔离，再对 knowledge 做 `query_matches`；
- `list_records(tenant...) -> list[LearningRecord]`：列出该 tenant 全部记录；
- `read_by_query` / `read_overview`：兼容 JSON-like 输出。

同一 `AppResources` 里的 `LearningStore`、`MemoryStore` 和 `LearningStateService` 必须共享同一个 UoW。若各自默认构造 UoW，它们会各有内存 snapshot，读写彼此看不见。这个共享关系由 [`infrastructure/resources.py`](../../tech_doc_agent/app/infrastructure/resources.py) 负责组装。

### `MemoryStore`

`query_memories(query, tenant..., limit=5)`：

1. tenant 过滤；
2. 在 kind、topic、content 上做文本匹配；
3. 按 `updated_at` 字符串倒序；
4. 最多返回 `max(1, limit)` 条。

正式的 `LearningStateService` 写路径用 `datetime.now(UTC).isoformat()` 产生时间，因此这些新记录可按字符串倒序。不过 legacy payload 和兼容 `upsert_memory(timestamp=...)` 目前没有强制验证 ISO 8601/UTC；只要混入不同格式或时区，字典序就不再可靠。若要开放外部 timestamp，应在写入边界统一格式，或排序时解析 datetime。

## 用户画像是独立聚合

位置：

- [`application/profile_models.py`](../../tech_doc_agent/app/application/profile_models.py)
- [`application/profile_service.py`](../../tech_doc_agent/app/application/profile_service.py)
- [`infrastructure/persistence/user_profile_repository.py`](../../tech_doc_agent/app/infrastructure/persistence/user_profile_repository.py)

画像和学习 snapshot 分开，原因是它们的更新语义不同：学习记录是一次 tool call 的复习事实；画像是较稳定的偏好与主题集合。把画像塞进每次学习 command 会扩大冲突域，也会让单独修改语言偏好必须发布整份学习历史。

`UserProfileService.update_profile(...)` 的调用步骤：

```text
parse_tenant
  -> repository.get(tenant)，不存在则 UserProfile.default
  -> UserProfileUpdate.create(...)
  -> profile.apply(update, timestamp)
  -> changed=True 才 repository.save
  -> UserProfileUpdateResult
```

`apply` 会归一化主题列表、做不区分大小写的合并，并处理 known/weak/resolved 之间的关系。业务规则应留在 `profile_models.py`，repository 只负责 envelope 和文件路径。

新路径为：

```text
DATA_PATH/user_profiles/<URL编码 user_id>/<URL编码 namespace>.json
```

默认 namespace 下若新路径不存在，会读取旧的 `user_profiles/<user_id>.json`。和 learning state 一样，旧路径只读兼容；下一次发生实际变化时写入新路径。

`context_summary()` 会把画像摘要与最近/查询命中的 MemoryFragment 拼接成给 Agent 的用户上下文，但不会把二者存成一个对象。这里是读取组合，不是数据所有权合并。

## 失败时应该看到什么

| 失败点 | 内存活动 snapshot | `current.json` | 调用者看到的结果 |
| --- | --- | --- | --- |
| command 校验失败 | 不变 | 不变 | `learning_command_invalid` |
| mutation 失败 | 不变 | 不变 | typed application error |
| 写 `state.json` 失败 | 不变 | 仍指旧 generation | file repository error |
| 新文件回读/校验失败 | 不变 | 仍指旧 generation | `learning_state_corrupt` 或 file error |
| 发布 manifest 失败 | 不替换 | 通常仍指旧 generation；异步中断时需按磁盘核查 | file repository error |
| 相同 command 重放 | 不变 | 不写新 generation | 返回旧结果，`replayed=True` |

API/SSE 层会把 `ApplicationError` 转成安全 payload；不要为了调试把文件路径、原始异常或用户内容直接塞进对外错误消息。

## 修改时的高风险点

### 新增学习字段

至少同步检查：

1. `LearningRecord` 或 `MemoryFragment`；
2. `to_payload/from_payload`；
3. snapshot schema/version 和校验；
4. command、service 与 tool schema；
5. API response/front-end type（若展示）；
6. 旧 snapshot 迁移测试；
7. 幂等 fingerprint 是否应包含该字段。

### 改 tenant 规则

不能只改查询条件。command identity、owner key、profile path、session thread ID、API URL 和前端 localStorage key 都含 tenant。详见 [10 - 横切机制](10-cross-cutting-policies.md)。

### 给 UoW 增加“自动保存”

不要在 `prepare_upsert_record` 或 setter 中偷偷保存。否则 service 一次逻辑操作会产生多个 generation，失败时失去原子性。兼容方法若需要持久化，应由调用者明确再调 `save()`。

### 清理旧 generation

先读取 inventory，确认 manifest 指向的 generation 存在，再删除非当前 generation。不要在 request save path 里顺手清理，因为 Windows 文件锁、杀毒软件和异步取消都可能把辅助清理错误升级成主业务失败。

## 建议跟着跑的测试

先用测试名定位，因为具体文件可能继续细分：

```powershell
rg -n "LearningState|idempotency|generation|legacy|UserProfile|MemoryStore" tests
```

重点场景应包括：

- 新增记录、复习已有记录、`score=None` 保留分数；
- 学习记录和 memory 同一 snapshot 提交；
- repository 保存失败时内存不变；
- 相同 tool call 重放不重复更新；
- 同 key 不同参数冲突；
- manifest/count/schema 损坏时明确失败；
- 无 manifest 时读取旧记录和旧 memory；
- profile 默认值、无变化不保存、namespace 路径隔离和旧路径 fallback；
- 两个 tenant 的同名知识点互不影响。

读完本章后，可继续 [08 - 文档检索、FAISS 与外部搜索](08-retrieval-and-document-store.md)，那里解释“保存文档后为什么还必须 refresh retriever”。
