# LearningRecord / MemoryFragment 领域模型与序列化边界

## 本批目标

LearningState 的 generation/manifest、事务提交和幂等处理已经完成，但 snapshot、store、tool port 与 HTTP route
仍主要传递 `dict[str, Any]`。同一组 `knowledge / reviewtimes / tenant`、`kind / confidence / timestamp` 归一化规则
散落在 store 的读、写、查询路径；调用方还可以通过公开 list/dict 引用绕过 unit of work 修改活动状态。

本批完成 D7 的 learning / memory 子范围：引入不可变 `LearningRecord` 与 `MemoryFragment`，让 application service、
unit of work、repository、store typed 主路径、tool port 和 API route 在序列化边界前传递领域对象。profile 与 approval
尚未迁移，因此 D7 总项保持未完成。

## 最终边界

### 1. 归一化与状态变化归属领域模型

`application/learning_models.py` 定义两个 frozen、slots dataclass：

- `LearningRecord` 负责知识点、时间、有限 score、非负 reviewtimes 与 tenant；`reviewed()` 返回新对象，不原地修改；
- `MemoryFragment` 负责稳定 id、受限 kind、trim 后的 topic/content、`[0, 1]` confidence、来源 session 与时间；
  `updated_from()` 保留原 id/created_at，并返回新版本；
- `from_payload()` 是 legacy JSON 的兼容入口，允许缺少 tenant/计数/时间并按既有规则补默认值；
- `to_payload()` 是明确的 JSON adapter，不把 dataclass 泄漏到磁盘、tool 或 HTTP 输出。

当前 legacy tenant 仍走 `normalize_tenant()`，请求、RunnableConfig 与 HTTP 输入继续走严格 `parse_tenant()`；两种语义
没有重新混合。

### 2. Unit of Work 只持有领域对象

`LearningStateSnapshot.records / memories` 分别变为 `list[LearningRecord]` 与 `list[MemoryFragment]`。更新 port 接收
`Sequence` 并返回领域对象；`LearningStateService` 直接读取 `memory.id`，不再了解持久化字段 key。

两个模型不可变，因此 snapshot clone 只需复制 list 容器，不再 deep-copy 每条 dict。unit of work 对外返回 tuple；
`replace_records / replace_memories` 也复制传入 sequence，调用方无法通过保留的 list 引用修改已发布 snapshot。

### 3. Repository 是 JSON 与领域对象的唯一持久化翻译层

`LearningStateSnapshotRepository` 在 load 时将 generation 或 legacy JSON rows 构造成领域对象，在 save 时逐项调用
`to_payload()`。磁盘仍保持 schema version 1 以及原有字段：

```text
records: knowledge, timestamp, score, reviewtimes, user_id, namespace
memories: id, user_id, namespace, kind, topic, content, confidence,
          source_session_id, created_at, updated_at
```

没有隐式批量 migration，也没有改变 manifest/counts/processed_commands。候选 generation 会先写入、重新读取并验证
成领域对象，再原子发布 manifest；损坏 row 导致 load 失败时，unit of work 不替换当前内存 snapshot。

### 4. Store 提供 typed 主路径与薄兼容 facade

`LearningStore.query_records / list_records` 返回 `LearningRecord`；`MemoryStore.query_memories / recent_memories` 返回
`MemoryFragment`。查询、tenant 比较、upsert 和排序都直接读取属性，不再在每次循环中反复 normalize/copy dict。

原 `records / memories` 属性以及 `read_by_query / read_overview / read_recent` 暂时保留为兼容边界：getter 每次生成新的
payload list，setter 立即把 mapping 转成领域对象。这样仓外旧调用方仍收到原 JSON-like schema，但对返回 dict 的修改不会
影响活动状态。退化为 no-op 的 `normalize_records()` / `normalize_memories()` 已删除；repository、setter 和 model factory
是唯一归一化入口。

### 5. Tool 与 HTTP 只在交付出口序列化

`LearningStorePort / MemoryStorePort` 改为 typed return。三个读取工具调用 typed query，并在 `_serialize_records /
_serialize_memories` 中一次性生成 JSON。Learning API 同样从 typed store 读取，在构造 Pydantic response model 时才调用
`to_payload()`。

工具 JSON 和 HTTP 响应字段没有变化。架构测试禁止 learning tools 退回 legacy `read_*` facade，并固定 application
snapshot 的领域类型；输出测试固定 tool/API 的字段集合。

## 实施中遇到的问题

### 问题 A：直接改掉 dict API 会破坏兼容调用方

仓库内测试、UserProfileService 以及潜在仓外脚本仍使用 `read_by_query()` 和 `store.records`。一次性删除会把本批范围
扩大到 profile 领域模型，也会造成不必要的外部破坏。

处理：新增 typed 主路径并让 production tool/API 使用；旧方法变成只做 `to_payload()` 的薄 facade。profile 在后续
D7 子批迁移后，再单独评估兼容 facade 的弃用窗口。

### 问题 B：frozen dataclass 不等于整个状态边界不可变

模型本身 frozen，但如果 unit of work 继续返回内部 list，调用方仍能 append/remove；如果 compatibility getter 缓存并
返回同一 payload dict，调用方也能绕过事务修改共享数据。

处理：unit of work 返回 tuple，replace 时复制 sequence；兼容 getter 每次创建全新 payload。测试故意修改并 clear
返回的 list/dict，验证领域状态仍保持原值。

### 问题 C：领域对象不能直接写入现有 JSON schema

把 snapshot 类型改成 dataclass 后，原 `_snapshot_payload()` 会把对象交给 JSON writer，既无法序列化，也可能诱使人
为了省事改用 `asdict()`，把内部 `TenantContext` 嵌套结构写进磁盘，造成 schema 漂移。

处理：只允许领域模型自己的 `to_payload()` 定义外部字段；增加 generation 文件逐字段断言，并在 reload 后比较
`model.to_payload()` 与原 JSON row 完全一致。

### 问题 D：旧 normalize 生命周期变成了重复逻辑

旧流程在 repository load 后，resources 和 store.load/save 还会再次 normalize。模型 factory 已在 load/setter 时完成
归一化后，这些调用只会复制同一批对象，继续保留会让“究竟哪里负责修复 legacy row”变得不清楚。

处理：删除两个 normalize 方法及所有调用。组合根对 seed payload 显式调用 `LearningRecord.from_payload()` 一次，随后
只操作 typed unit of work。

### 问题 E：不能把 D7 总项提前勾完

learning 与 memory 已完成 typed chain，但 UserProfileService、approval envelope/repository 仍有 dict contract；migration
dry-run/backup/summary、通用 repository contract test 和 retention 也属于独立工作。

处理：本地 TODO 只增加 learning/memory 已完成的子项，父项和 profile/approval 子项继续保持未完成。

## 测试与门禁

新增/扩展覆盖：

- legacy learning/memory payload 归一化、非法数值回退与 confidence clamp；
- reviewed/update 返回新对象并保留稳定 identity 字段；
- generation JSON schema 不变，reload 后为领域对象；
- compatibility dict/list 修改不能污染活动 snapshot；
- 损坏 current row 的 reload 失败且不替换内存状态；
- transaction rollback、manifest publish failure、幂等 replay 与 legacy migration 继续通过；
- tool JSON 与 Learning API response 字段集合不变；
- application/tools/API 使用 typed 主路径的架构门禁。

| 验证 | 结果 |
|---|---|
| learning/memory/transaction/API/tool/architecture 聚焦 pytest | 75 passed |
| 全量后端 pytest（禁用本机不可写 cache） | 398 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy | passed，9 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式变化，因此不重复浏览器视觉 smoke。三条 pytest warning 仍来自 LangGraph/Starlette 的既有
弃用提示。

## 保持不变与后续工作

保持不变：learning/memory JSON schema version 1、manifest/counts/processed command 格式、tenant 隔离、query matching、
memory sort/limit、same-kind/topic upsert identity、tool 文本/JSON、HTTP response、transaction 与 idempotency 语义。

后续优先把 UserProfile 与 durable approval envelope 迁到领域模型，再评估移除 store compatibility facade；另行设计
versioned migration command（dry-run、backup、summary、幂等重跑）、JSON/未来 SQLite 共用的 repository contract tests、
processed command/approval/profile retention 与 multi-worker writer 约束。本批没有把 tolerant legacy factory 用于新的
HTTP 或授权输入。
