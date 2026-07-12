# Typed retrieval 边界与原子 BM25 snapshot

## 本批目标

Retrieval ranker 内部已经使用 `IndexedDocument / RankedCandidate / FusedCandidate`，但 facade 入口仍接收
`query + top_k + mode + filters` 四组松散参数，formatter 又立即把候选转回裸 dict。结果是 tool、eval 和测试
都依赖字符串 key；同时 `HybridRetriever` 用 `_signature / _documents / _bm25_index` 三个字段分别发布缓存，
并发 refresh 时没有一个可证明的一致 snapshot。

本批继续完成 B5：增加 typed `SearchQuery / SearchResult`，把 dict 序列化限制在输出 adapter；把 BM25 cache
改成锁保护的候选构建与单对象原子发布；审计并关闭已过时的 broad-except 清单项。

## 最终边界

### 1. 内部端口使用 SearchQuery 与 SearchResult

`retrieval/models.py` 新增：

- `SearchQuery`：`query / top_k / mode / filters`；
- `SearchResult`：文档字段、typed match types、score、signals 与 matched chunks；
- `MatchType`：`exact | bm25 | semantic`。

`DocumentRetrieverPort` 现在只暴露：

```text
retrieve(SearchQuery) -> list[SearchResult]
```

document tools 在调用端构造 query；`read_docs` 和 `search_related_docs` 共用一个 serializer，在返回 ToolMessage
JSON 前才调用 `SearchResult.to_dict()`。eval runner 的评分、title 和 match-type 统计也直接读取 typed result，
只有生成 artifact row 时才转 dict。

`SearchQuery` 在构造时复制 filters，防止调用方随后修改原 dict；未知 mode 在进入 store/ranker 前稳定失败。
`SearchResult` 复制 metadata、nested signals 和 matched chunks，formatter 返回后即使 fused candidate 继续变化，
已经形成的结果也不漂移。

### 2. 保留 HybridRetriever.search 外部兼容

清单要求 `HybridRetriever.search()` 外部行为兼容。直接把它改成 dataclass return 会破坏仓外 Python 调用方，
因此 facade 分成两层：

- `retrieve(SearchQuery) -> list[SearchResult]`：项目内部 typed 主路径；
- `search(str, *, top_k, mode, filters) -> list[dict]`：薄兼容/外部边界，只负责构造 query 和序列化结果。

兼容测试比较 `search()` 输出与 typed results 的逐项 `to_dict()`，原字段 `id/title/content/source/metadata/
match_type/score/retrieval/matched_chunks`、RRF score 舍入和 matched chunk 上限均保持不变。

### 3. 三个 cache 字段收敛为一个 snapshot

原实现依次修改 `_signature`、`_documents`、`_bm25_index`。本批改为 frozen `_IndexSnapshot`：

```text
signature + tuple[IndexedDocument] + BM25Index
```

`_ensure_index_snapshot()` 在 `_index_lock` 内完成：

1. 读取当前 document list reference 并计算 signature；
2. cache hit 直接返回已发布 snapshot；
3. normalize documents 并构建候选 BM25Index；
4. 全部成功后一次赋值发布 `_index_snapshot`。

构建失败不会修改旧 snapshot。每次 retrieve 把返回的 snapshot 保存在局部变量中，后续 exact/BM25/semantic
ranker 使用同一 documents 版本；并发 refresh 发布新 snapshot 不会让正在执行的请求混用新 documents 和旧 index。

`refresh()` 使用 `force_rebuild=True`，明确承担 mutation 后主动 cache invalidation；普通 retrieve 仍通过 signature
检测未显式 refresh 的 document 变化。无 filter 时复用 snapshot BM25Index；有 filter 时为过滤后的只读 documents
构造请求局部 BM25Index，不写共享 cache。

锁只覆盖本地 document normalization 与 BM25 candidate build/publish，不覆盖 exact ranking、semantic embedding/provider
调用或 result formatting，避免慢外部依赖占住 refresh lock。`BM25Index` 和 ranker 输入统一为 `Sequence`，snapshot
使用 tuple，减少内部集合被意外追加的机会。

### 4. 异常传播审计

清单中的“`search_related_docs` 使用 broad `except Exception: results=[]`”已经不符合当前代码：

- `SemanticRanker` 只捕获 typed `ApplicationError`；
- vector-only mode 设置 `degrade_on_failure=False`，错误传播到统一 ToolNode fallback；
- hybrid mode 只对 typed dependency failure 做有 `error_code/retryable/dependency/tool` telemetry 的语义降级；
- 未知普通异常不会被 retrieval service 吞掉；
- eval runner 保留 `except Exception`，用途是把单个离线 case 标成 error artifact，不是生产 retrieval fallback。

本批通过全路径 `rg` 和现有 error tests 复核后，将该过时项标记完成，没有再增加第二套错误处理。

## 实施中遇到的问题

### 问题 A：typed search 与外部兼容要求冲突

第一版直接让 `HybridRetriever.search(SearchQuery)` 返回 `SearchResult`。项目内测试可以全部迁移，但这违反了
TODO 中 facade 外部行为兼容的验收，也会让未纳入仓库的调用方从 dict 突然收到 dataclass。

处理：typed 主路径改名 `retrieve()`，保留原签名 `search()` 作为最薄的外部 adapter。tool/eval ports 只依赖
`retrieve`，兼容代码不会向内扩散。

### 问题 B：`or` 让显式 0 配置失效

原构造器和 search 使用 `top_k or default`。因此显式 `top_k=0` 会偷偷改成默认值，后面的 `top_k <= 0`
短路永远无法生效；constructor 的 `bm25_top_k/vector_top_k/rrf_k=0` 也有同样问题。

处理：全部改成 `is None` 选择默认值。新增测试证明 `SearchQuery(top_k=0)` 在索引构建前返回空结果。

### 问题 C：frozen dataclass 仍可能共享内部 mutable dict

仅给 `SearchResult` 加 `frozen=True` 不能阻止它引用 candidate 的 signals/chunk dict；candidate 后续变化时，结果仍会
被动改变，typed 边界只是表面不可变。

处理：`__post_init__` 复制 metadata、每个 signal 和 chunk；`to_dict()` 再为外部 adapter 创建独立容器。测试在
format 后故意修改 fused candidate，断言 result 保留原值。

### 问题 D：给 refresh 加锁仍可能发布半成品

如果先清空旧 cache，再在锁内构建新 index，构建异常会让所有后续请求失去可用版本；锁只能防并发，不能自动
提供事务语义。

处理：旧 snapshot 一直保留，normalize/BM25 都在局部 candidate 中完成，最后才替换引用。fault injection 让
BM25 constructor 抛错，验证 `_index_snapshot` 仍是上一对象。

### 问题 E：并发测试不能只断言“没有异常”

普通并发 smoke 即使没有 lock 也可能因 GIL 偶然通过，无法证明构建被串行化。

处理：测试注入计数型 BM25Index，记录同时处于 constructor 的数量，并发触发四次 refresh；断言最大并行构建
数为 1。测试只 sleep 10ms 放大重叠窗口，不依赖外部服务。

## 测试与门禁

新增/扩展覆盖：

- typed query filter snapshot、mode validation 与显式 top_k=0；
- typed result 字段、序列化 schema 和 candidate mutation 隔离；
- 原 `search()` dict contract 与 typed `retrieve()` 逐项等价；
- read/related document tools 构造正确 typed query，JSON 输出不变；
- eval scoring 在 typed result 上运行，artifact 仍为 dict；
- document mutation signature rebuild；
- 四线程 refresh 串行构建；
- refresh 构建失败保留上一 snapshot；
- hybrid typed degradation 与 vector typed failure 传播。

| 验证 | 结果 |
|---|---|
| retrieval/tool/eval/resources 聚焦 pytest | 46 passed |
| 全量后端 pytest（禁用本机不可写 cache） | 384 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，18 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式变化，因此不重复浏览器视觉 smoke。三条 pytest warning 仍来自
LangGraph/Starlette 的既有弃用提示。

## 保持不变与后续工作

保持不变：`HybridRetriever.search()` 参数/返回 dict schema、ranker 顺序、exact/BM25/semantic score、RRF tie-break、
filter 语义、hybrid typed-error degradation、vector error propagation、tool JSON 和 offline eval artifact schema。

仍未完成：真实版本化 corpus 的 before/after quality/latency baseline。当前本地 corpus 为空，继续运行 60-case runner
只会得到全 0，不能把这批结构重构宣称为质量提升。`FaissStore` 仍以进程内 lock 和 list-reference replacement 发布
document state；如果未来允许其他 adapter 原地修改 documents 或多进程共享 mutable cache，需要把 store snapshot port
和跨进程写约束单独设计，不能把本批 `HybridRetriever` lock 误当成 multi-worker lock。
