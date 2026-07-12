# Document Index Infrastructure 边界

## 本批结论

本批把文档向量索引的闭合依赖切片整体迁到 `infrastructure/retrieval`：

- `services/embedding.py` -> `infrastructure/retrieval/embedding.py`；
- `services/vectordb/faiss_store.py` -> `infrastructure/retrieval/faiss_store.py`；
- `services/vectordb/chunkenizer.py` -> `infrastructure/retrieval/chunking.py`，同时修正历史拼写；
- FaissStore 使用同包相对 import 组合 embedding、chunking 与 metadata normalization；
- resource factory、metadata migration script、tests 与 retry wiring allowlist 改用新路径；
- 新增 physical ownership gate，明确旧 services 路径不得重新出现；
- `infrastructure.retrieval.__init__` 不 eager re-export FaissStore，避免普通 HybridRetriever import 提前加载 native FAISS。

除给 chunking helper 补充类型标注和格式清理外，索引构建、查询、snapshot generation、embedding retry 与 metadata 规则均未改变。

## 为什么三者必须同批迁移

FaissStore 的直接依赖是：

```text
FaissStore
  -> embedding.generate_embedding
  -> chunking.recursive_character_splitting
  -> retrieval.normalization
  -> persistence.FaissSnapshotRepository
```

前一批已经把 normalization 迁到 infrastructure。如果这次只移动 `faiss_store.py`，新 infrastructure 文件仍会反向 import `services.embedding` 和 `services.vectordb.chunkenizer`，递归 infrastructure contract 应当失败。为它增加 allowlist 会把错误依赖合法化。

embedding 目前只被 FaissStore 使用，chunking 也只有这一个 production caller，因此三者构成最小闭合 slice。移动后 infrastructure retrieval 自己拥有“文本切块 -> embedding -> FAISS index -> normalized search”的 concrete adapter chain。

## 最终依赖方向

```text
services.resources / metadata migration script
  -> infrastructure.retrieval.faiss_store.FaissStore

FaissStore
  -> .chunking
  -> .embedding
  -> .normalization
  -> infrastructure.persistence.FaissSnapshotRepository
  -> core settings/errors

infrastructure index adapters
  -X services / graph / runtime / API / tools / agents
```

HybridRetriever 通过内部 `RetrievalStorePort` 使用 FaissStore 的 structural capability，application contract 和 tools 不依赖 concrete FAISS class。

## 保持不变的行为

- `FaissStore.build_index/add_documents/save/load/search_related` 方法与参数保持；
- build candidate 成功前不替换现有内存 index；
- snapshot repository 先发布完整 generation，再原子切换 manifest；
- load 校验 index/document/chunk count 与引用一致性；
- document/chunk metadata 继续走唯一 normalization 规则；
- embedding client 仍禁用 SDK 隐式 retry，并通过统一 RetryExecutor 记录 attempt ledger；
- string 与 list embedding 输入/输出 shape、typed error mapping 不变；
- chunk size、overlap、separator 顺序和输出保持；
- seed-on-empty 与 migration script 行为不变。

## Import Compatibility 与 Native 依赖

旧 `services.embedding`、`services.vectordb.faiss_store` 和拼写错误的 `services.vectordb.chunkenizer` 没有保留 facade。它们是 concrete implementation 路径，仓内 production、scripts 与 tests 已完整迁移；稳定调用方应依赖 application/tool port，只有 composition/migration 才直接选择 FaissStore。

新 package 也没有在 `infrastructure/retrieval/__init__.py` 中 re-export FaissStore。Python 会先执行 package init；若 init 导入 FaissStore，任何 `from infrastructure.retrieval import HybridRetriever` 都会加载 `faiss`/NumPy native runtime，即使调用方只运行 BM25。保持显式子模块 import 可以维持依赖按需加载：

```python
from tech_doc_agent.app.infrastructure.retrieval.faiss_store import FaissStore
```

## 实施中遇到的问题

### 问题 A：按单文件迁移会制造 infrastructure -> services 倒置

FaissStore 原来从两个 services 模块获取 embedding 和 chunking。只移动主类看似 diff 更小，实际新层仍依赖旧混合层。

处理：先检查真实 caller 数量，再移动最小闭合依赖集；FaissStore 对同包模块使用相对 import，不增加 architecture 例外。

### 问题 B：测试 monkeypatch 保存的是完整物理路径

FAISS persistence/resource tests 为避免真实 provider 调用，patch `faiss_store.generate_embedding`。类 import 更新后，字符串 patch 仍会在运行时寻找已删除模块。

处理：迁移所有 patch target 到新 FaissStore module，并运行 build/add/search、故障注入、resource seed 和 retry ledger tests，而不改成更宽泛的全局 mock。

### 问题 C：Embedding retry policy 有一份路径型安全门禁

`test_retry_executor_is_not_wired_around_tool_nodes_or_write_paths` 用允许模块集合阻止 retry 被随意包在非幂等写路径。移动 embedding 后若只让测试放宽为目录匹配，会降低原保护。

处理：精确更新 allowlist 到 `app/infrastructure/retrieval/embedding.py`，其他允许项保持不变。

### 问题 D：旧文件名拼写错误会继续污染新层

`chunkenizer.py` 不是通用术语，继续原名迁移会让新 architecture 固化历史 typo。

处理：物理移动时改为 `chunking.py`，函数名和切块参数不变；补全输入/返回类型并保持同一 splitter/separator 配置。

### 问题 E：Package init 可能无意加载 native FAISS

之前 persistence snapshot 已遇到 package init 扩大 native dependency surface 的风险。将 concrete store 加入 retrieval `__all__` 会重现同类问题。

处理：不在 package init import/re-export FaissStore；architecture 文档和 resource source 都使用具体子模块路径。

## 验证范围

定向验证覆盖 embedding validation/error/retry usage、FAISS generation round-trip/atomic failure/load validation、resource seeding、HybridRetriever、document tools、seed script、retry allowlist 与 architecture contracts。

| 验证 | 结果 |
|---|---|
| embedding/FAISS/resources/retry/retrieval/architecture targeted pytest | 117 passed；3 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 701 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 151 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- Embedding 仍使用 Settings 默认构造与 OpenAI-compatible concrete client；若出现第二种 index/provider，应先提取 narrow provider port，再做 routing；
- FaissStore 的进程内 lock 不提供 multi-worker single-writer 保证，process lock/外部 index service 仍是独立决策；
- generation retention/orphan GC 继续等待 single-writer 或跨进程协调前置条件；
- 真实版本化 corpus 仍缺失，本批不声明 retrieval quality/latency 改善；
- `services/vectordb` 现在只剩 WebSearchBackend concrete implementation，下一批应按 provider/fallback/cache 职责归位；
- `services/resources.py` 仍是 concrete resource factory，需等 web provider 归位后再迁移。
