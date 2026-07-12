# Learning / Memory Persistence Adapter 归位

## 本批结论

本批将三份被错误归类为 vector database 的代码迁入 `infrastructure/persistence`：

- `services/vectordb/learning_store_backend.py` -> `infrastructure/persistence/learning_store.py`；
- `services/vectordb/memory_store_backend.py` -> `infrastructure/persistence/memory_store.py`；
- `services/vectordb/text_match.py` -> `infrastructure/persistence/text_match.py`；
- 对应测试文件去掉 `_backend` 命名，并统一从 infrastructure 路径导入；
- `services/resources.py` 显式组装新的 concrete persistence adapters；
- 新增物理归属架构断言，现有递归 `INFRASTRUCTURE_CONTRACT` 同时覆盖三个迁入模块。

没有修改 learning/memory 领域模型、snapshot schema、tenant 隔离、query matching、共享 Unit of Work、兼容 JSON view 或写入事务。

## 为什么它们不属于 Vectordb

移动前的 `services/vectordb` 同时包含四类完全不同的职责：

| 模块 | 实际职责 | 是否向量数据库 |
|---|---|---|
| `faiss_store.py` / chunking | 文档向量索引与 generation snapshot | 是 |
| `web_search_backend.py` | 外部搜索 provider 与本地 fallback cache | 否 |
| `learning_store_backend.py` | learning snapshot 的 tenant query/legacy projection | 否 |
| `memory_store_backend.py` | memory snapshot 的 tenant query/legacy projection | 否 |

LearningStore 与 MemoryStore 共享 `LearningStateUnitOfWork` 和 `LearningStateSnapshotRepository`。它们查询的是不可变 `LearningRecord` / `MemoryFragment`，没有 embedding、vector index、semantic search 或 FAISS 依赖。目录名让维护者误以为学习状态也由向量库持久化，并把未来 SQLite/Postgres adapter 的演进方向带偏。

## 最终依赖方向

```text
services.resources (concrete resource factory)
  -> infrastructure.persistence.LearningStore / MemoryStore
  -> shared application LearningStateUnitOfWork
  -> infrastructure LearningStateSnapshotRepository

infrastructure.persistence stores
  -> application learning models / UoW
  -> core tenant/settings
  -> local text_match helper
  -X services / runtime / graph / API / tools / agents
```

迁移后的文件自动进入递归 infrastructure contract。与只改 README 相比，这会在任何深层文件重新 import services 时直接使架构测试失败。

## 保持不变的行为

以下接口只改变 import path，不改变签名或返回值：

- `LearningStore.record_models / records / query_records / list_records`；
- `LearningStore.read_by_query / read_overview / prepare_upsert_record / upsert_record`；
- `MemoryStore.memory_models / memories / query_memories / recent_memories`；
- `MemoryStore.read_by_query / read_recent / prepare_upsert_memory / upsert_memory`；
- `load()`、`save()` 与共享 UoW 注入；
- 空 query、英文/CJK token、stop-token 与 substring matching；
- tenant strict parse、namespace 隔离、排序、limit 和 JSON payload projection。

这里保留的 compatibility 是数据/API view。旧 `services.vectordb.*_store_backend` Python 路径没有 re-export；仓内 production/tests 已完整迁移，继续暴露旧路径会让“非向量 store 属于 vectordb”的错误模型永久存在。

## 实施中遇到的问题

### 问题 A：类名是 Store，但 source of truth 已经是共享 Repository/UoW

如果只看 `LearningStore` 名字，容易把它当成独立写盘对象。实际 composition 会给 LearningStore 与 MemoryStore 注入同一个 `LearningStateUnitOfWork`，原子事务和 generation 发布都由 snapshot repository 负责。

处理：归类为 persistence query/compatibility adapters，不把它们误放 application，也不复制 repository 写入逻辑。

### 问题 B：共用 helper 也必须一起迁移

若只移动两个 Store，新的 infrastructure 文件仍会反向 import `services.vectordb.text_match`，直接违反 infrastructure contract，也留下错误 package ownership。

处理：先确认 `text_match.py` 只有这两个调用方，再作为同一 cohesive slice 移入 persistence，Store 使用相对 import。

### 问题 C：不能通过扩大 architecture allowlist 让移动“变绿”

移动后的模块会被 `INFRASTRUCTURE_CONTRACT` 递归扫描。保留旧 helper import 时测试应当失败；把 services 加入 allowlist 会掩盖真实倒置。

处理：修正依赖方向，并新增 resource factory 的路径断言，证明 production 组装确实使用新 adapter，而不是只让测试走新路径。

### 问题 D：package `__init__` 不应成为隐式依赖聚合器

`infrastructure.persistence.__init__` 目前只暴露轻量 atomic JSON helper。把所有 repository/store 都 eager re-export 会让任意子模块 import 先加载不需要的 adapter，并重复之前 FAISS package-init 的问题。

处理：调用方直接 import `infrastructure.persistence.learning_store` 或 `memory_store`，不扩大 package init。

### 问题 E：源码、测试名和维护文档必须同步

只更新 production import 会留下 `test_*_backend.py` 与 README 的旧分类，下一位维护者仍会把新代码放回 vectordb。

处理：测试文件同步改名，模块树、关键目录说明、架构文档和本地 D7 任务状态一并更新；历史重构日志保留当时路径事实。

## 验证范围

定向验证覆盖 learning/memory store、共享事务、resource composition、tool bundle、learning overview 与 architecture contracts；全量门禁覆盖后端、前端和静态分析。

| 验证 | 结果 |
|---|---|
| learning/memory/resources/tools/architecture targeted pytest | 76 passed；4 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 698 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 150 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- `services/resources.py` 仍是 concrete resource factory，且组合 persistence/retrieval/provider adapter；需要等其依赖全部归位后再决定迁到 bootstrap wiring 还是 infrastructure resource container；
- FAISS store/chunking 仍在 `services/vectordb`，后续应整体迁到 infrastructure retrieval/index adapter，不能只移动主文件留下 embedding/normalization 反向依赖；
- WebSearchBackend 与 embedding provider 仍在 services，适合形成独立 provider adapter 批次；
- LearningStore/MemoryStore 仍保留 Settings 默认构造器和 JSON-like compatibility view，删除前需检查仓外脚本与迁移工具；
- 本批只纠正物理 ownership，不宣称吞吐、延迟或查询质量变化。
