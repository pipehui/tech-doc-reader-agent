# Retrieval metadata、taxonomy、filter 与 inference 拆分

## 本批目标

原 `services/retrieval/metadata.py` 同时包含：

- 300 多行 category prefix、keyword、alias 和 broad-category 规则；
- document/chunk metadata normalization；
- category/tag inference；
- query filter normalization 与 matching。

这些职责共享数据但变化原因不同。新增 taxonomy 不应迫使 filter matching 一起修改；修 tenant metadata fallback 也不应触碰 category keyword 表。本批只移动纯函数和常量，不改变检索排序或外部 metadata schema。

## 实际结构

```text
services/retrieval/
├── taxonomy.py       # category 规则、alias、broad tags、term normalization
├── inference.py      # title/content -> category/tags
├── normalization.py  # document/chunk metadata 与 tags normalization
├── filters.py        # query filter normalization 与 metadata matching
└── metadata.py       # staged compatibility facade
```

生产调用方已改为直接 import owning module：

- `HybridRetriever` 从 `filters` 与 `normalization` 取依赖；
- document tools 从 `filters` 取 query filter normalization；
- `FaissStore` 从 `normalization` 取 document/chunk normalization。

旧 `services.retrieval.metadata` import 暂时保留 re-export，避免同一批强制修改仓库外调用方。兼容 facade 不包含规则或业务实现。

## 行为刻画补充

本批在移动前先锁定以下现有行为：

- nested `metadata` 覆盖 item 顶层值，item 再覆盖 fallback；
- broad `category=RAG/LangGraph` 转为 tag filter，而标准 category alias 转为内部 category；
- nested filter 与顶层 tags 会规范化、去重并合并；
- tag filter 要求 expected tags 是 actual tags 的子集；
- 非 tag filter 的 list 表示允许值集合；
- title prefix inference 优先于 title keyword，title 又优先于 content 前 800 字符；
- tag mapping 输入只保留 truthy key，并执行 casefold/tagify；
- CamelCase 与中文 unigram/bigram tokenization；
- BM25、exact rank 和 RRF 的 tie-break 与模型可见 result shape。

其中 exact rank 的现有规则有一个容易误判的细节：content-only match 得分为 `2.0`，不是 `1.0`。characterization test 按实际实现锁定该行为，本批没有借重构之名修改权重。

## 实施中遇到的问题

### 问题 A：60-case baseline 在当前 checkout 无效

实际执行：

```powershell
D:\Tools\miniconda3\envs\agent\python.exe -m evals.run_retrieval_eval `
  --cases evals/retrieval_cases_full.json --mode bm25
```

runner 完成 60 个 case，但日志明确显示：

```text
resources.faiss.empty reason=seed_disabled
retrieval.bm25.rebuilt documents=0
```

当前 `.env` 禁止空库自动 seed，checkout 也没有本地 FAISS corpus，因此 60 个 case 的 Recall/MRR/keyword 全为 0。这只能证明 runner 能执行，不能作为检索质量或重构等价性基线。

处理：不把 0 分写成 before 指标，也不为了测试偷偷修改用户数据目录。当前结构等价性由确定性 synthetic corpus tests 保证；真正的 60-case before/after 必须在准备好版本化 corpus 后补跑。

### 问题 B：函数之间存在潜在循环依赖

`normalize_metadata` 需要 category/tag inference，`infer_tags` 与 `normalize_tags` 又共享 tagify。如果简单按函数位置切文件，会形成 `normalization <-> inference` 循环。

处理：把 category 规则和无状态 term normalization 放入 `taxonomy.py`。依赖方向固定为：

```text
taxonomy
  -> inference
  -> normalization
  -> filters
```

`filters` 可以同时读取 taxonomy、inference 与 normalization，但下层不反向依赖 filters。

### 问题 C：兼容 import 与新依赖方向需要同时满足

若所有生产代码继续从 `metadata.py` re-export 取函数，文件虽然变短，模块所有权仍不清晰；若直接删除 facade，又会造成不必要的 import break。

处理：内部调用方全部迁到 owning module，`metadata.py` 仅作为有测试保护的 staged facade。后续确认仓库外无使用者后可单独删除。

## 验证结果

完成前的定向验证：

| 验证 | 结果 |
|---|---|
| metadata/hybrid/document/resources 定向测试 | `33 passed` |
| 全量后端测试 | `219 passed` |
| Ruff 全仓检查 | passed |
| retrieval + direct consumers mypy（`--follow-imports=skip`） | passed，9 个 source files |
| 60-case BM25 runner | executed，但因 corpus 为空标记为 invalid baseline |

## 未在本批处理

- BM25、semantic adapter、exact、RRF 和 result formatter 仍在 `hybrid.py`，下一批拆分；
- 不修改任何 rank weight、RRF `k`、candidate count 或 tie-break；
- 不修 `search_related_docs`/semantic ranker 的 broad exception，错误模型应单独设计；
- 不自动 seed、生成 embedding 或改写用户的本地 FAISS 数据；
- compatibility facade 的删除条件是内部 import 已迁移且仓库外兼容窗口结束。
