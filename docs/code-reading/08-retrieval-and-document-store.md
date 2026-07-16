# 08 - 文档检索、FAISS 与外部搜索

本章沿两条容易混淆的链路展开：

1. `read_docs/search_related_docs` 查询本地共享知识库；
2. `web_search` 请求 Tavily 或 DuckDuckGo。

二者都给模型返回“资料”，但本地检索有文档、chunk、三路排序和 snapshot；网络搜索有 provider fallback、重试和每日额度。不要在同一个类里把它们揉成“搜索”。

## 本地检索的输入输出 contract

Application 层 contract 在 [`application/retrieval.py`](../../tech_doc_agent/app/application/retrieval.py)。

### `SearchQuery`

```python
SearchQuery(
    query: str,
    top_k: int | None = None,
    mode: Literal["bm25", "vector", "hybrid"] = "hybrid",
    filters: dict[str, Any] = {},
)
```

- `top_k=None` 表示使用 settings 中的默认值；小于等于 0 时直接返回空列表。
- `mode` 构造时校验，不能传任意字符串。
- `filters` 会被复制，调用方之后修改原 dict 不会悄悄改变 query。

### `SearchResult`

typed 结果包含完整文档和检索解释：

```text
doc_id, title, content, source, metadata,
match_types, score, signals, matched_chunks
```

`to_dict()` 输出给 tool/API 的形状中：

- `match_type` 是 `exact+bm25+semantic` 这类组合字符串；
- 顶层 `score` 是 RRF 融合分，不是余弦相似度；
- `retrieval.score_type == "rrf"`；
- `retrieval.signals` 保留每一路的 rank 和原始路内 score；
- `matched_chunks` 只在语义路命中 chunk 时出现。

调用方若要做阈值判断，必须先确认用的是哪种 score。把 RRF 分当成 0..1 的相似度是常见错误。

## 工具如何进入 retriever

位置：[`tools/documents.py`](../../tech_doc_agent/app/tools/documents.py)。

### `read_docs`

```text
read_docs(query, category?, tags?, source?)
  -> _build_filters
  -> DocumentRetrieverPort.retrieve(SearchQuery(..., mode="hybrid"))
  -> list[SearchResult]
  -> JSON string
```

它走默认 hybrid 模式，适合一般知识库查询。

### `search_related_docs`

```text
search_related_docs(query, k, filters...)
  -> SearchQuery(query, top_k=k, mode="vector", filters=...)
  -> retriever.retrieve(...)
```

它明确要求语义检索。向量索引或 embedding 不可用时会失败，不会像 hybrid 一样静默退化为关键词结果。这个差异是有意的：工具名承诺的是“语义相关”，不能在依赖失效时伪装成功。

### `save_docs`

```text
save_docs(title, content, source, category?, tags?)
  -> document_store.add_documents([...])
  -> document_store.save()
  -> document_retriever.refresh()
  -> success text
```

三个步骤缺一不可：

- `add_documents` 更新进程内 FAISS/index + documents；
- `save` 把一致 snapshot 发布到磁盘；
- `refresh` 让 HybridRetriever 重建规范化文档和 BM25 cache。

如果只 `add_documents + save`，下一次 hybrid 查询最终通常也能因 signature 改变而重建，但显式 refresh 保证写工具返回成功时关键词索引已同步。以后若 store/retriever 分进程，这个 refresh contract 还需要改成真正的失效通知，而不是直接函数调用。

知识文档目前是**共享知识库**。metadata 虽含默认 `user_id/namespace`，`read_docs` 并不会自动按当前 tenant 加过滤条件。不要看到字段就误以为已经完成租户隔离；若要改为私有知识库，需要显式设计共享/私有范围、写入身份、查询默认过滤和旧文档迁移。

## `HybridRetriever.retrieve` 主流程

位置：[`infrastructure/retrieval/hybrid.py`](../../tech_doc_agent/app/infrastructure/retrieval/hybrid.py)。

```text
SearchQuery
  -> normalize_filter
  -> _ensure_index_snapshot
  -> 可选 filter_documents
  -> _rankings_for_mode
       bm25:  BM25
       vector: semantic
       hybrid: exact + BM25 + semantic
  -> reciprocal_rank_fusion
  -> format_result
  -> list[SearchResult]
```

### `_IndexSnapshot` 缓存了什么

它包含：

```python
signature
documents: tuple[IndexedDocument, ...]
bm25_index: BM25Index
```

signature 由每个 raw document 的 id、title、content、source 和 metadata signature 组成。查询时若 signature 未变，就复用规范化文档和 BM25Index；变化或 `refresh(force_rebuild=True)` 时重建。

这不是向量 index snapshot。FAISS 状态由 `FaissStore` 持有；这里缓存的是面向融合检索的文档视图和 BM25 数据结构。

带 filters 时，BM25 会针对过滤后的文档临时构造索引；不带 filters 时复用完整 cache。否则先在全库排名再过滤，可能导致 top-k 全被不相关 category 占满。

## 三路排序分别做什么

### Exact：字面子串优先

位置：[`infrastructure/retrieval/exact.py`](../../tech_doc_agent/app/infrastructure/retrieval/exact.py)。

`rank_exact(query, documents)` 对 query 做 strip/lower，然后判断它是否是 title/content 的子串：

- title 命中得 2 分；
- content 命中再加 1 分；
- 按分数降序、title 升序稳定排序。

它不是 token exact match，也没有模糊匹配。例如 query 中多个词的完整字符串没有连续出现在文档里，就不会命中 exact 路。

### BM25：关键词相关性

位置：[`infrastructure/retrieval/bm25.py`](../../tech_doc_agent/app/infrastructure/retrieval/bm25.py) 与 [`tokenization.py`](../../tech_doc_agent/app/infrastructure/retrieval/tokenization.py)。

索引文本为 `title + newline + content`。`BM25Index` 预计算 token、文档长度、term frequency 与 IDF，查询时用 `k1=1.5, b=0.75` 评分。中文/CJK 和英文 token 化规则集中在 tokenization 模块，若改分词应跑中英文检索回归，不要只测一条英文 query。

### Semantic：chunk 语义相关性

位置：[`infrastructure/retrieval/semantic.py`](../../tech_doc_agent/app/infrastructure/retrieval/semantic.py)。

`SemanticRanker.rank(...)`：

1. 调 `store.search_related(query, k)` 得到 chunk；
2. 通过 `doc_id` 优先、title 次之，把 chunk 映射回完整文档；
3. 有 filters 时再次校验文档 metadata；
4. 同一文档只保留第一次命中的 chunk candidate；
5. FAISS distance 转成 `1 / (1 + max(distance, 0))` 作为路内解释分。

有 filters 时会向 FAISS 请求 `top_k * 5` 个 chunk，再过滤到目标文档。原因是当前 `IndexFlatL2` 本身不做 metadata filter。这个倍率只是候选扩展，不保证稀有 filter 一定凑满 top-k；数据规模变大后可考虑带 filter 的向量数据库或按 partition 建索引。

语义路失败策略由 mode 决定：

- `mode="hybrid"`：捕获 `ApplicationError`，记录 `retrieval.semantic.skipped`，继续 exact/BM25；
- `mode="vector"`：`degrade_on_failure=False`，错误上抛。

只捕获 typed `ApplicationError` 很重要。编程错误不应被“降级成功”吞掉。

## RRF 如何融合

位置：[`infrastructure/retrieval/fusion.py`](../../tech_doc_agent/app/infrastructure/retrieval/fusion.py)。

每一路按排名贡献：

```text
贡献分 = 1 / (rrf_k + rank)
文档总分 = 所有命中路线贡献分之和
```

假设 `rrf_k=60`：

| 文档 | exact 排名 | BM25 排名 | semantic 排名 | 融合分 |
| --- | ---: | ---: | ---: | ---: |
| A | 1 | 2 | - | `1/61 + 1/62` |
| B | - | 1 | 1 | `1/61 + 1/61` |
| C | 2 | - | - | `1/62` |

B 会优先，因为它被两路共同认可。RRF 使用“名次”而非直接相加各路原始 score，避免 BM25、exact 和 L2 派生分的量纲不兼容。

融合对象同时记录：

- `match_types`：哪些路线命中；
- `signals[route]`：rank、路内 score 与干净 metadata；
- semantic 的 chunk text/index/distance。

最终排序先看 RRF 分，再看该文档最好的一路 rank，最后按 title 稳定排序。[`formatting.py`](../../tech_doc_agent/app/infrastructure/retrieval/formatting.py) 固定 match type 顺序为 exact、bm25、semantic，并最多输出两个 matched chunks。

## metadata 归一化与过滤

相关位置：

- [`infrastructure/retrieval/normalization.py`](../../tech_doc_agent/app/infrastructure/retrieval/normalization.py)
- [`infrastructure/retrieval/filters.py`](../../tech_doc_agent/app/infrastructure/retrieval/filters.py)
- [`infrastructure/retrieval/inference.py`](../../tech_doc_agent/app/infrastructure/retrieval/inference.py)
- [`infrastructure/retrieval/taxonomy.py`](../../tech_doc_agent/app/infrastructure/retrieval/taxonomy.py)

每份文档最终拥有：

```python
metadata = {
    "user_id": ...,
    "namespace": ...,
    "category": ...,
    "tags": [...],
}
```

没有 category 时，`infer_category` 先看 title 前缀/关键词，再看 content 前 800 字，最后为 `uncategorized`。没有 tags 时，从 category 和标题关键词推断。

`normalize_filter` 会：

- 删除 `None`、空字符串、空列表；
- 展开嵌套 `metadata`；
- 规范化/合并 tags；
- 处理 category 别名；
- 把广义 `RAG`、`LangGraph` category 查询转成对应 tag 查询。

为什么广义 category 变 tag：taxonomy 的真实 category 是 `rag_basic`、`rag_advanced`、`langgraph_core` 等细类。用户说“RAG”通常想搜所有 RAG 文档，而不是一个并不存在的精确 category。

tags 过滤要求期望 tags 是实际 tags 的子集；其他字段用不区分大小写的精确值比较。这里没有 substring filter。

taxonomy 是产品语义，不只是工具函数。增删 category 时至少同步：推断规则、别名、tag、已有 metadata 迁移、过滤测试和评测集。

## `FaissStore` 如何构建和追加索引

位置：[`infrastructure/retrieval/faiss_store.py`](../../tech_doc_agent/app/infrastructure/retrieval/faiss_store.py)。

### 文档到向量

默认 chunk 参数：

```text
chunk_size = 300
chunk_overlap = 20
```

`_prepare_documents` 分配递增整型 ID 并规范化 metadata；`_prepare_chunks` 用 [`chunking.py`](../../tech_doc_agent/app/infrastructure/retrieval/chunking.py) 切内容，为每个非空 chunk 保存 doc_id、title、source、chunk_text、chunk_index 和 metadata。

`_index_with_chunks`：

1. `generate_embedding(chunks)`；
2. 转为连续 `float32` 二维数组；
3. 校验行数和 dimension；
4. 新建 `faiss.IndexFlatL2(dimension)`，或 clone 当前 index；
5. 检查新 embedding dimension 与旧 index 一致；
6. 把 vectors 加入候选 index。

追加时 clone 现有 FAISS index，embedding 或 add 失败不会损坏活动 index。`build_index` 也在隔离候选上构建，成功后才一起替换 index、documents、chunk_metadata。

`_state_lock` 保护发布和查询时的状态抓取。`search_related` 在锁内取得 index/reference 后释放锁，再做远程 embedding 和 FAISS search，避免长时间占锁。不过 collections 按“替换而非原地修改”约定发布；改这里时不要把 append 原地操作重新带回来。

### embedding 不是纯本地函数

[`infrastructure/retrieval/embedding.py`](../../tech_doc_agent/app/infrastructure/retrieval/embedding.py) 负责 embedding provider、重试、形状/数值校验和错误分类。更换 embedding 模型可能改变 dimension，旧 FAISS index 不能直接追加。需要显式重建或版本化索引，不能遇到 `vector_dimension_mismatch` 就忽略。

## FAISS snapshot 与落盘一致性

位置：[`infrastructure/persistence/faiss_snapshot.py`](../../tech_doc_agent/app/infrastructure/persistence/faiss_snapshot.py)。

目录：

```text
DATA_PATH/faiss_store/
  current.json
  generations/<generation>/
    index.faiss
    documents.json
    chunk_metadata.json
```

保存步骤与 learning snapshot 类似，但要原子协调三个文件：

1. 创建新 generation；
2. 构造 manifest：dimension、vector/document/chunk counts；
3. 校验内存 snapshot；
4. 原子写 FAISS 临时文件并 `fsync + replace`；
5. 原子写 documents 和 chunk metadata JSON；
6. 从磁盘重新加载 exact bytes；
7. 校验 `index.ntotal == len(chunk_metadata)`、每个 chunk 指向已有 doc、counts/dimension 一致；
8. 最后发布 `current.json`。

只要 manifest 没切换，读者仍加载旧的完整 generation。旧格式 `index.faiss + documents.json + chunk_metadata.json` 只有三者齐全时才读取；缺一份会报 `vector_store_corrupt`，不会凑合加载。

`FaissStore.save()` 成功后还会用规范化后的 documents/chunks 替换内存视图。`load()` 也是先完整读取验证，再一次性替换活动状态。

## 本地保存的失败边界

`save_docs` 当前是“先更新内存，后保存磁盘，再 refresh”。如果 `add_documents` 成功但 `save()` 失败，`FaissStore` 进程内候选已经发布，磁盘仍是旧 generation。下一次请求在同进程可能搜到尚未持久化的文档。

这是与 learning UoW 不同的边界，修改时必须知道。若业务要求严格的提交语义，可进一步把 `FaissStore.add_documents` 改成 prepare candidate，由 snapshot repository 保存成功后再发布活动 state；不能只在 tool 里捕获异常并说“失败”，因为内存状态仍可能已变。

## 外部 Web Search 的调用链

位置：[`infrastructure/retrieval/web_search.py`](../../tech_doc_agent/app/infrastructure/retrieval/web_search.py)。

```text
web_search tool
  -> WebSearchBackend.search(query)
       -> can_use_tavily?
            -> RetryExecutor(Tavily, before_attempt=reserve quota)
            -> usable non-empty results? return
       -> RetryExecutor(DuckDuckGo)
       -> normalized usable results
```

### Tavily 额度为什么在每次 attempt 前预留

`_reserve_tavily_quota` 在每个网络尝试前：

1. 检查 API key；
2. 按本地日期重置计数；
3. 检查每日上限；
4. 计数加一并原子写 `web_search/tavily_usage.json`。

重试也是实际 provider 调用，应消耗额度。若只在整次 retry 成功后加一，就会低估请求数。写 usage 失败时回滚内存计数并让请求失败，避免磁盘和内存额度分叉。

### provider fallback

- Tavily 未配置/达到本地额度：直接尝试 DDG；
- Tavily typed error：记录安全的 degraded event，再试 DDG；
- Tavily 返回空的可用结果：也试 DDG；
- DDG 单独失败且之前没尝试/没失败过 Tavily：保留 DDG 的原 typed error；
- 两个 provider 都失败：抛 `web_search_unavailable`。

结果统一成：

```python
{"title": ..., "url": ..., "snippet": ..., "provider": ...}
```

后处理会压缩空白、截断 title/snippet、去重 URL，并过滤缺字段、snippet 太短或明显是链接目录的结果。不要在 Agent prompt 里再依赖 provider 原始响应字段。

## 修改时最容易踩的坑

### 把 `search_related_docs(k)` 直接接到 FAISS

现在它经过 `HybridRetriever` 的 semantic ranker，返回**完整文档**、RRF envelope 和 matched chunk，不是原始 chunk 列表。绕过 retriever 会改变 tool 输出 contract、metadata filtering 和 SSE/tool result 展示。

### 改 chunk size 后只继续 append

旧 chunk 与新 chunk 会使用不同切分策略，评测和 matched chunk 解释变得不一致。通常应重建整个 generation，并记录 embedding/chunk 配置版本。

### 误把 L2 distance 当“越大越相关”

FAISS `IndexFlatL2` distance 越小越近。项目把它转为 `1/(1+distance)` 仅作路内解释；融合最终还是看 rank。

### 在 metadata 中加入任意嵌套对象

signature、JSON 序列化、过滤比较和 chunk fallback 都假设当前固定规范字段。新增字段应决定：是否参与 signature、是否传播到 chunk、是否可过滤、如何比较、旧文档如何补值。

### hybrid 降级后不留痕

hybrid 可以在 embedding 故障时返回关键词结果，这是可用性策略，不代表一切正常。必须保留 `retrieval.semantic.skipped` 日志/指标，否则线上召回质量下降却看不出依赖故障。

### 把知识库 tenant metadata 当访问控制

目前 tool 文档明确说明共享知识库，query 不自动绑定 tenant。若有敏感私有资料，不能依赖调用方“记得传 filter”，应在 port/use case 边界强制范围策略。

## 建议跟着跑的测试与评测

```powershell
rg -n "HybridRetriever|reciprocal_rank_fusion|FaissStore|metadata|WebSearch" tests
```

测试重点：

- exact/BM25/semantic 单路排名和稳定 tie-break；
- hybrid 中 semantic typed failure 退化，vector 模式上抛；
- filter 在三路都生效，广义 category 转 tag；
- 同一文档多个 chunk 不重复成多份文档结果；
- RRF signal 和最多两个 matched chunks 的输出形状；
- embedding shape、dimension mismatch、NaN/非法响应；
- append/build 失败不破坏活动 FAISS index；
- snapshot 三文件/count/doc-chunk 引用一致性；
- legacy snapshot 缺文件明确失败；
- `save_docs` 的 add/save/refresh 顺序；
- Tavily 每个 retry attempt 计额度、跨日重置、DDG fallback 和双 provider failure。

单元测试之外，检索改动应跑固定 query-document 评测集，至少观察 Recall@K、MRR/nDCG 和 semantic degradation 情况。检索代码“都通过类型检查”并不等于召回质量没退化。

下一章是 [09 - 前端状态、SSE 归约与会话恢复](09-frontend-stream-and-state.md)，会把这些 tool result 如何成为 Studio 工具卡片串起来。
