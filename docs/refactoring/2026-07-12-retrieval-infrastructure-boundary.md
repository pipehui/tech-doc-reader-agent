# Retrieval Infrastructure 实现边界

## 本批结论

本批将 retrieval 具体实现从 `services/retrieval` 整体迁到 `infrastructure/retrieval`：

- 迁移 BM25、semantic、exact、RRF、formatter、metadata taxonomy/filter/inference/normalization、internal models 与 `HybridRetriever`；
- package 内部 absolute import 全部改为相对 import，implementation 不绑定旧物理根路径；
- production resource factory、offline eval、FAISS normalization 调用和 implementation tests 改用 infrastructure 路径；
- `application/retrieval.py` 继续作为唯一跨层 `SearchQuery / SearchResult / DocumentRetrieverPort` contract；
- `services/retrieval` 只重建一个 package-level `__init__.py` compatibility facade；
- facade re-export 同一个 application type object 与同一个 infrastructure `HybridRetriever`，没有复制 class 或转换 wrapper；
- architecture tests 锁定旧目录只能存在 `__init__.py`，迁入模块由递归 `INFRASTRUCTURE_CONTRACT` 覆盖。

没有修改检索 mode、cache key/发布策略、BM25/semantic/exact/RRF 排序、metadata precedence、filter normalization、telemetry 或输出 schema。

## Contract 与 Implementation 为什么要分开

跨层稳定部分只有：

- `SearchQuery`：query、mode、limit 与 filter；
- `SearchResult`：content、score、metadata、match type；
- `DocumentRetrieverPort`：retrieve/refresh capability。

这些继续位于 application，tools 可以构造查询并消费结果而不知道具体实现。下列内容则是可替换 adapter 细节：

- BM25 token statistics 与 snapshot；
- semantic store 调用和 typed degradation；
- exact ranking、RRF tie-break 与 formatter；
- taxonomy alias、metadata inference/normalization/filter；
- HybridRetriever cache、lock、refresh 和 telemetry。

因此最终方向是：

```text
tools
  -> application.retrieval contract

infrastructure.retrieval
  -> implements application contract
  -> owns internal candidates/rankers/metadata rules/cache

composition resources / offline eval
  -> choose infrastructure HybridRetriever

services.retrieval
  -> compatibility re-export only
```

## Compatibility Facade

上一批已明确记录 `services.retrieval` package facade 可能存在仓外调用，因此本批没有直接删除包级 import：

```python
from tech_doc_agent.app.services.retrieval import HybridRetriever, SearchQuery
```

仍可工作，且对象 identity 满足：

```text
services.retrieval.SearchQuery is application.retrieval.SearchQuery
services.retrieval.HybridRetriever is infrastructure.retrieval.HybridRetriever
```

但旧的深层 implementation 路径不再保留，例如 `services.retrieval.bm25`、`services.retrieval.filters` 和 `services.retrieval.hybrid`。它们是仓内实现细节；为每个文件创建 re-export 会保留第二棵伪实现树，并迫使 architecture contract 接受错误 ownership。

删除 package facade 的条件是完成仓外调用审计或经过明确 deprecation 周期。当前测试固定旧目录只有 `__init__.py`，防止兼容层重新生长业务代码。

## 实施中遇到的问题

### 问题 A：直接移动会让 infrastructure 继续硬编码 services

原包内部大量使用 `tech_doc_agent.app.services.retrieval.*` absolute import。物理移动后如果只批量替换根路径，implementation 仍与项目 package layout 强耦合。

处理：内部模块统一改为相对 import；application/core imports 保持 absolute，清楚区分包内协作与跨层依赖。

### 问题 B：Facade 与 production wiring 必须是两条路径

如果 `services/resources.py` 继续通过 compatibility facade 构造 HybridRetriever，生产仍把旧层级当成事实源，facade 永远无法删除。

处理：resource factory 与 eval 直接 import infrastructure implementation；只有兼容测试/仓外旧调用允许经过 services facade，并用架构断言锁定 production source。

### 问题 C：FAISS 尚未迁移，但需要 metadata normalization

`services/vectordb/faiss_store.py` 写入文档时使用 retrieval normalization。移动 retrieval 后保留旧 import 会立即失效，也会让新实现和 FAISS 使用两套规则。

处理：FAISS 暂时直接 import `infrastructure.retrieval.normalization`，继续共享单一规则；FAISS/chunking/embedding 的完整归位留给后续 cohesive batch。

### 问题 D：Package facade 不能复制 contract type

重新声明 dataclass、Protocol 或转换 wrapper 会破坏 fake retriever、eval 与 runtime 的 type identity，也可能导致缓存/序列化分叉。

处理：facade 仅 import/re-export，并扩展 identity test 同时覆盖 `SearchQuery`、`SearchResult` 和 `HybridRetriever`。

### 问题 E：递归相对 import 必须由真实 analyzer 验证

改成相对 import 后，旧的首层 AST 字符串检查会漏报。当前 `PythonImportGraph` 会解析 importer package 与 relative level，能够把 `.models` 还原成完整 infrastructure module。

处理：metadata 单向依赖、extracted component 禁止 hybrid/settings，以及整个 infrastructure contract 都继续通过同一 import graph 执行。

## 验证范围

定向验证覆盖 HybridRetriever mode/cache/refresh/concurrency、BM25/semantic/exact/RRF、metadata normalization/filter、FAISS persistence、resource wiring、package identity、retrieval eval/corpus、seed script 与 architecture contracts。

| 验证 | 结果 |
|---|---|
| retrieval/FAISS/resources/eval/architecture targeted pytest | 111 passed；3 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 700 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 151 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- 真实版本化 corpus 仍未准备，不能以这次路径迁移声明 Recall/MRR/latency 改善；
- FAISS store、chunking 与 embedding provider 仍在 `services`，应作为同一 index adapter slice 迁移，避免 infrastructure 反向依赖；
- WebSearchBackend 仍在 `services/vectordb`，其 provider/fallback/cache 职责应迁到独立 provider adapter；
- `services/resources.py` 仍是 concrete resource factory，需等上述 adapter 归位后再选择最终 wiring 位置；
- package-level `services.retrieval` facade 暂时保留，深层旧实现路径不恢复；
- metadata taxonomy/normalization 目前随 concrete retrieval implementation 放在 infrastructure；若未来多个 adapter 共享且形成稳定领域规则，再评估独立 domain package，不提前搬进 application。
