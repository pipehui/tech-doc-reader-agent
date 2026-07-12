# Learning State 组合事务与 Tool Call 幂等

## 本批目标

原 `upsert_learning_state` 的顺序是：修改 learning 内存 -> 保存 `records.json` -> 修改 memory 内存 -> 保存 `memories.json`。memory 归一化或第二次写文件失败时，learning 已经提交，调用方却收到失败；LangGraph 从 checkpoint 恢复并重放同一个 tool call 时，`upsert_learning_history` 和 `upsert_learning_state` 还会再次增加 `reviewtimes`。

本批完成 D3：把 tool schema、application command/use case、Unit of Work 和 generation repository 分层；learning records、memories、已处理 tool-call outcome 作为一个快照发布。

## 最终边界

### 1. Tool 只负责适配运行上下文

`tools/learning.py` 不再直接调用 `learning_store.upsert/save` 或 `memory_store.upsert/save`。两个写工具只完成：

1. 从 `RunnableConfig` 读取 tenant 和 session。
2. 通过 `InjectedToolCallId` 获取 LangGraph 当前 tool call id。
3. 创建 `UpdateLearningStateCommand`。
4. 调用 `LearningStateService.update()` 并格式化结果。

`tool_call_id` 不出现在发给模型的 `tool_call_schema`，只能由执行框架注入。普通 dict 直接调用写工具会被拒绝；测试必须像 ToolNode 一样传完整 ToolCall，避免测试路径绕开生产约束。

### 2. Application service 在候选状态上完成整个用例

新增 `application/learning_state.py`：

- `UpdateLearningStateCommand`：tenant、session、tool call、learning 和可选 memory 输入；
- `LearningStateService`：组合 learning + memory 领域更新；
- `LearningStateUnitOfWork`：持有活动快照、线程锁和 commit boundary；
- `UpdateLearningStateResult`：可持久化并在重放时恢复的稳定 outcome。

Unit of Work 深复制当前 records、memories 和 processed commands。learning 更新成功后，memory 更新仍只发生在 candidate；任一阶段抛错都不会替换活动状态，也不会调用 repository。repository 完整发布后，活动状态才一次替换。

Application 目录有依赖门禁，禁止反向 import API、tools、services 或 infrastructure。具体 store 实现提供纯 `prepare_upsert_*` 操作，application service 只依赖 protocol。

### 3. 三类数据共享一个 generation snapshot

新布局：

```text
learning_state/
  current.json
  generations/
    <generation-id>/
      state.json
```

`state.json` 是一个带 `schema_version` 的 envelope，包含：

- `records`
- `memories`
- `processed_commands`

Repository 先写新 generation、回读并校验 envelope/manifest count/processed outcome，再原子切换 `current.json`。LearningStore 和 MemoryStore 仍是独立查询/归一化 facade，但在 composition root 中共享同一个 Unit of Work，因此不再拥有两个可独立提交的 source of truth。

旧 `learning_store/records.json` 和 `memory_store/memories.json` 仍可单独或成对读取；普通启动只在内存加载，不删除、不覆盖。第一次实际保存或组合事务才发布新 generation。旧文件只保留迁移前备份，不做双写；回滚到不认识新快照的旧版本只能看到迁移前状态，不能把它误称为无损应用版本回滚。

### 4. Idempotency 同时覆盖两个写工具

Idempotency identity 使用：

```text
user_id + namespace + session_id + tool_call_id
```

结构化 identity 经过稳定 SHA-256 生成磁盘 key；command 全 payload 另生成 fingerprint。首次成功提交时，result、fingerprint 和 completed time 与业务数据写入同一快照。

- 相同 identity + 相同 fingerprint：直接返回已提交 result，不创建 generation、不增加 `reviewtimes`；
- 相同 identity + 不同 fingerprint：返回 `learning_idempotency_conflict`，防止错误复用 call id 静默覆盖；
- 不同 tenant、session 或 tool call：视为不同命令。

Tool call outcome 与 records/memories 同一次 manifest 发布，因此“业务已提交但幂等标记未提交”的窗口被消除。进程在响应前退出后，新进程加载 outcome，重放仍返回原 memory id 和消息。

## 通用 generation 基元

FAISS 和 learning state 都需要“新代草稿 -> 校验 -> manifest 发布 -> 失败清理”。本批将 UUID generation、路径校验、draft 生命周期和 manifest 原子切换提取为 `infrastructure/persistence/generations.py`，避免两套恢复协议逐渐漂移。

`GenerationDraft` 在 manifest 发布开始前可以安全清理；一旦开始 `os.replace`，就不再自动删除该 generation。原因是进程可能恰好在 replace 成功、内存 `_published` 标志更新前中断。此时宁可留下 orphan，也不能误删 current 指向的数据。Orphan/历史代 GC 必须等 single-writer 或 process lock 约束明确后实现。

## 实施中遇到的问题

### 问题 A：半事务不只存在于带 memory 的工具

任务单最初聚焦 `upsert_learning_state` 的两次文件写入。审计 ToolNode 恢复路径后发现，`upsert_learning_history` 即使只写一个 store，也会在同一 tool call 重放时重复累计 `reviewtimes`。

处理：两个写工具都构造同一种 command 并走同一 application service；无 memory 的 command 仍记录 outcome。

### 问题 B：框架内部 schema 与模型侧 schema 不同

首个测试用 `BaseTool.args` 断言 injected id 不可见，结果失败，因为该属性展示完整运行时 schema，包含 `tool_call_id`。真正绑定到模型的是 `tool_call_schema`，LangChain 会从其中过滤 `InjectedToolCallId`。

处理：检查当前安装版本的 `BaseTool` 注入实现，测试改为验证 `tool_call_schema.model_json_schema()`；同时用完整 ToolCall 做两次真实 invoke，确认 id 被注入且第二次不提交。

### 问题 C：只在 manifest 成功后设置标志仍有删除 current 的窄窗口

初版通用 helper 在 `write_json_atomic()` 返回后才设置 `_published=True`。异步中断可能发生在 replace 已完成、标志尚未更新时，context cleanup 会把 current 指向的新 generation 删除。

处理：增加 `_publication_started` 边界。manifest write 一旦开始，自动 cleanup 让位于数据安全；失败可能留下 orphan，但 loader 只认 current，后续由显式 GC 处理。

### 问题 D：持久化幂等 outcome 不能晚于业务数据

如果先提交 records/memories，再单独写 processed-call cache，进程可以在两步之间退出，重放仍会重复执行。

处理：outcome 是 `state.json` 的组成部分，和 records/memories 共用同一个 generation/manifest，不另设 cache 文件。

### 问题 E：批量 formatter 扩大了无关 diff

一次对 `tech_doc_agent/app` 目录运行 formatter 机械改动了 43 个本批未涉及的文件。虽然行为不变，但会污染审查范围并掩盖事务改动。

处理：依据本批开始时已确认的 clean worktree，精确恢复所有无关 formatter diff，只保留显式修改文件；后续 formatter 仅传入本批文件列表。缩小 diff 时第一次手工恢复尾逗号改错了列表分隔符，导致全量 pytest 在 collection 阶段报 SyntaxError；修正准确位置后重新执行完整门禁，354 项全部通过。

### 问题 F：memory 时间不能信任模型提供值

为了让候选重试看似确定，曾考虑复用 command 的 learning timestamp 作为 memory 创建时间。但 timestamp 是 tool 参数，来自模型，不应冒充服务器落库时间。

处理：learning record 保持原契约；memory 的 created/updated time 仍由 application service 使用 UTC server time 生成。已成功命令的 replay 读取持久化 outcome，不会再次生成时间或 memory id。

## 测试与门禁

新增测试覆盖：

- learning + memory + processed outcome 同一 generation 提交；
- 同进程重复调用和进程重建后的重放；
- 相同 idempotency identity 的 payload 冲突；
- tenant/session/tool-call 三个 key 维度；
- learning candidate 已更新后，memory 阶段失败的完整回滚；
- manifest 发布失败时活动状态和旧 current 都不变；
- interrupted orphan generation 不会被加载；
- 两份 legacy JSON 的无损读取和首事务迁移；
- `InjectedToolCallId` 模型 schema 隐藏与真实 ToolCall invoke；
- application 依赖方向和 tool 不得恢复直接 store write 的架构门禁。

| 验证 | 结果 |
|---|---|
| generation/learning transaction/FAISS focused pytest | 33 passed |
| learning/resources/tools/architecture focused pytest | 56 passed |
| 全量后端 pytest（禁用本机不可写 cache） | 354 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，9 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities；本轮网络响应较慢但在命令超时前正常完成 |
| `git diff --check` | passed |

本批没有前端源码或样式改动，因此不重复浏览器视觉 smoke。pytest 剩余三条 warning 来自 LangGraph/Starlette 依赖弃用提示；使用 `-p no:cacheprovider` 后没有本机 `.pytest_cache` 权限 warning。

## 保持不变与后续工作

保持不变：learning/memory 对外查询 API、tenant 隔离语义、HITL sensitive-tool 策略、learning record 的复习累计规则、memory kind/topic upsert 规则，以及旧 JSON 可读取性。

本批不声称解决：

- 多进程 writer 的 lost update；当前 Unit of Work lock 只覆盖单进程；
- generation/history 与 processed command 的 retention/GC；
- 真实数据库事务。若进入 multi-worker 生产部署，应将同一 repository port 切换为 SQLite/Postgres，而不是继续叠加本地文件锁；
- 历史 legacy 文件的自动删除。删除必须由显式、可回滚 migration 命令完成。
