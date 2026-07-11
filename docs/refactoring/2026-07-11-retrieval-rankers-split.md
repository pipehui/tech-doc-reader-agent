# Retrieval rankers、fusion 与 formatter 拆分

## 本批目标

metadata 职责拆分后，`hybrid.py` 仍有 500 多行，同时维护：

- 中英文 tokenization；
- `IndexedDocument`、`RankedCandidate`、`FusedCandidate`；
- BM25 index 与 scoring；
- exact title/content rank；
- FAISS chunk 到 document 的 semantic adapter；
- reciprocal rank fusion；
- API/tool result formatting；
- document signature/cache refresh；
- mode orchestration、配置与 telemetry。

这些实现难以独立测试，也让任何 ranker 修改都必须经过整个 `HybridRetriever`。本批保留 facade 与所有结果语义，只把内部算法和 adapter 移到各自 owning module。

## 最终结构

```text
services/retrieval/
├── models.py        # typed candidates、mode/filter aliases、store ports
├── tokenization.py  # English/CamelCase 与 CJK unigram/bigram
├── bm25.py          # BM25Index
├── exact.py         # exact title/content rank
├── semantic.py      # SemanticRanker 与 chunk/document mapping
├── fusion.py        # reciprocal rank fusion
├── formatting.py    # stable result shape 与 match-type order
├── documents.py     # document normalization/filter/signature/key
└── hybrid.py        # mode orchestration、cache、settings、telemetry facade
```

拆分后 `hybrid.py` 约 206 行；各算法模块约 21-110 行。行数不是验收目标，关键是 BM25、semantic、exact、RRF、formatting 可以分别构造和测试，只有 facade 读取 settings 并决定 mode。

## 依赖方向

- `models.py` 和 `tokenization.py` 不依赖 facade；
- BM25/exact/fusion/formatting 只依赖 typed candidates 与必要的纯函数；
- semantic adapter 依赖 `SemanticSearchPort`，不知道 FAISS 具体类或 settings；
- `HybridRetriever` 是唯一组装 ranker、cache、配置与日志的入口；
- extracted components 禁止反向 import `hybrid.py` 或 `core.settings`，由 architecture test 固定。

`RetrievalStorePort` 与 `SemanticSearchPort` 替代 ranker 构造参数中的裸 `Any`。当前仍保留 dict result 作为既有 tool/API 边界，未借本批改变外部 schema。

## 保持不变的行为

- `HybridRetriever(store, settings=...)` 和 `search(query, top_k, mode, filters)`；
- `bm25`、`vector`、`hybrid` 三种 mode；
- exact/BM25/semantic 的候选顺序、RRF `k` 和最终 tie-break；
- filtered semantic search 的 `top_k * 5` overfetch；
- 同一 document 多个 semantic chunk 只保留首个 candidate；
- 无 filter 时允许 semantic chunk 形成 fallback document，有 filter 时禁止；
- semantic dependency 异常记录 `retrieval.semantic.skipped` 并降级为空 ranking；
- result 的 `match_type`、`score`、`retrieval.signals` 和最多两个 `matched_chunks`；
- document 内容或 metadata signature 变化后自动 rebuild BM25。

## 等价性验证

除 focused tests 外，本批直接加载上一提交 `61d09ef` 中的旧 `hybrid.py`，与当前 facade 使用相同 synthetic corpus 和 semantic chunks，比较：

- queries：`StateGraph`、`graph state resume`、`Depends`；
- modes：`bm25`、`vector`、`hybrid`；
- filters：无 filter、`category=langgraph_core`、`source=seed`。

共 27 组输出进行完整 dict/list 等值比较，包括 score、signal、matched chunk、metadata 和顺序，结果全部 byte-for-byte equivalent。该比较证明当前确定性 fixture 上结构拆分未改变结果；它不替代准备真实版本化 corpus 后的 60-case 质量评测。

## 实施中遇到的问题

### 问题 A：ranker 捕获 store 会改变 facade 的可变语义

第一版在 `HybridRetriever.__init__` 中永久构造 `SemanticRanker(store)`。旧实现的 `_rank_semantic` 每次读取 `self.store`；若调用方替换 `retriever.store`，BM25 会读取新 documents，但 semantic 仍访问旧 store，形成不一致。

处理：`_rank_semantic` 每次用当前 `self.store` 构造轻量 `SemanticRanker`。这不是为了鼓励运行中换 store，而是避免纯重构制造隐蔽行为差异。

### 问题 B：private helper 也已有测试/调试使用

原测试直接 import `hybrid.BM25Index`、`_tokenize`、`_rank_exact`、`_reciprocal_rank_fusion` 与 `_format_result`。直接删除会让算法模块虽然更清晰，却把同批行为测试全部改成无法证明兼容的新入口。

处理：测试主体迁到 owning modules；`hybrid.py` 暂留一层 private alias，并用单独 compatibility test 固定。它们不是新的公共 API，可在后续兼容窗口结束后删除。

### 问题 C：semantic broad catch 是错误模型债务，不是拆分机会

`SemanticRanker` 仍捕获 `search_related` 的全部异常并返回空 ranking。直接改成抛错会改变 hybrid graceful degradation、tool fallback 与 SSE 可见结果。

处理：原样迁移并增加独立 failure test，确认 timeout 仍生成相同 telemetry 与空 ranking。后续应在统一错误模型批次把 dependency unavailable/timeout/rate limit 分类，而不是在文件移动中改变语义。

### 问题 D：真实 60-case corpus 仍不可用

上一批已确认本地 corpus 为空且 seed disabled。当前可验证的是确定性算法等价，不是线上 retrieval quality 或 latency。没有填入虚构 Recall/MRR/延迟 before-after。

## 新增测试

- CamelCase 与 CJK tokenization；
- BM25 equal-score title tie-break；
- exact title/content score 与 tie-break；
- RRF multi-signal、signal metadata、semantic chunk provenance 与 final title tie-break；
- semantic duplicate document、unknown fallback、filtered overfetch、failure degradation 与 distance/rank scoring；
- extracted component import direction；
- staged private alias compatibility；
- 27 组旧/新完整输出等价比较（实施时一次性对照）。

## 验证结果

完成前定向验证：

| 验证 | 结果 |
|---|---|
| hybrid/rankers/eval-runner/document 定向测试 | `28 passed` |
| architecture + ranker 定向测试 | `29 passed` |
| 全量后端测试 | `225 passed` |
| Ruff 全仓检查 | passed |
| retrieval + direct consumers mypy（`--follow-imports=skip`） | passed，18 个 source files |
| 旧/新 deterministic result comparison | 27/27 完全等价 |
| 前端 production build | passed，2013 modules transformed |

## 后续工作

- 准备版本化 retrieval corpus 后补跑 60-case/filter eval 与 latency before-after；
- 为 query/result 引入 typed boundary model，只在 tool/API 边界转 dict；
- 统一 semantic/web/embedding 错误分类后删除 broad catch 空结果；
- 明确 refresh/index mutation 的并发锁与 cache invalidation；
- compatibility private aliases 在仓库外使用审计完成后删除；
- rank weight、reranker 或 query rewrite 属于算法改动，必须使用有效 eval 单独立项。
