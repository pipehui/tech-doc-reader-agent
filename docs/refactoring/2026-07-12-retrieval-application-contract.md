# Retrieval Application Contract 与 Tool Adapter 解耦

## 本批结论

本批消除 `tools -> services.retrieval` 依赖，同时没有把 retrieval 实现细节整体搬进 application：

- 新增 `application/retrieval.py`，定义 `SearchQuery`、`SearchResult`、`DocumentRetrieverPort` 及其公共类型；
- `tools/dependencies.py` 直接依赖 application port，不再自己复制 retrieval protocol；
- `tools/documents.py` 只构造 query 和序列化 result，不 import filter/taxonomy/model implementation；
- filter normalization 的所有权收敛到 retrieval 实现，tool adapter 不再提前执行同一规则；
- `services/retrieval/models.py` 只保留 Indexed/Ranked/Fused candidate 与 store/semantic 内部 port；
- ranker/formatter/hybrid 实现分别从 application contract 与内部 model 获取所需类型；
- eval、tool tests 和 hybrid tests 改用 application contract；
- `services.retrieval` package facade 暂时 re-export 同一个 application 类型对象，不存在第二份 class 定义；
- 新增 tools architecture contract，递归禁止 tools import services/API/graph/runtime/infrastructure/composition。

## 为什么只拆公共 Contract

原 `services/retrieval/models.py` 混合两类模型：

| 类别 | 类型 | 正确归属 |
|---|---|---|
| 跨层 use-case contract | `SearchQuery`、`SearchResult`、`DocumentRetrieverPort` | `application` |
| 排序实现状态 | `IndexedDocument`、`RankedCandidate`、`FusedCandidate` | `services.retrieval` 实现内部 |
| backend capability | `SemanticSearchPort`、`RetrievalStorePort` | retrieval 实现边界 |

把整个文件移动到 application 会让 application 知道 RRF candidate、semantic backend 和内部 mutable fusion state；只复制 SearchQuery/SearchResult 又会形成两套类型，破坏 `isinstance`、type hint 与序列化单一来源。本批按使用范围拆分，而不是按文件名搬运。

## 最终调用方向

```text
tools.documents
  -> application.retrieval.SearchQuery / SearchResult
  -> application.retrieval.DocumentRetrieverPort

services.retrieval.HybridRetriever
  -> implements DocumentRetrieverPort structurally
  -> canonicalizes filters at the retrieval boundary
  -> internal Indexed/Ranked/Fused candidates
  -> application SearchResult
```

tools 不知道 BM25/vector/RRF、taxonomy alias、metadata inference 或 cache。HybridRetriever 不知道 LangChain tool schema 或 JSON ToolMessage 文本。

## Filter normalization 单一来源

旧 `tools/documents._build_filters()` 先调用 `services.retrieval.filters.normalize_filter()`；`HybridRetriever.retrieve()` 收到 query 后又调用一次同一函数。这既形成跨层 import，也让“谁保证 filter 合法”有两个答案。

本批让 tool adapter 只做 wire 参数清理：

- 丢弃 `None`、空字符串和空 tags；
- 复制 tags list，避免调用方后续 mutation；
- 保留用户提供的 category/tags/source 语义。

category alias、宽泛 category -> tags、metadata normalization 与 matching 全部由 retrieval implementation 处理。定向测试锁定 tool 传递 raw `RAG`/`Hybrid Search` filter，而原 metadata/hybrid tests 继续证明 implementation 会正确规范化并匹配。

## Compatibility 策略

`services/retrieval/__init__.py` 仍可导入：

- `HybridRetriever`；
- `SearchQuery` / `SearchResult`；
- `RetrievalMode` / `MetadataFilter`。

后四者直接来自 application module，兼容 facade 与新路径是同一个 class/type alias。内部 `services.retrieval.models.SearchQuery` 路径没有保留；该文件现在明确是 ranker implementation models，不应继续被跨层调用。

## 实施中遇到的问题

### 问题 A：models.py 不是一个单一职责文件

最初看起来只需移动 `models.py`，但调用图显示 SearchResult 与 FusedCandidate 的生命周期完全不同。前者跨 tool/eval，后者只在 RRF pipeline 内修改。

处理：按跨层稳定性拆 public contract 与 internal working state，避免 application 被实现 DTO 污染。

### 问题 B：filter.py 也不能直接下沉

`filters.py` 依赖 taxonomy、normalization 和 inference；把它移进 application 会连带搬迁整个 metadata domain，改动远超本批边界。

处理：检查真实调用后发现 HybridRetriever 已有同一 normalization。删除 tool 侧重复调用即可解除耦合且保持 production 检索行为。

### 问题 C：兼容 re-export 可能悄悄复制类型

重新声明 dataclass 或通过转换 wrapper 兼容旧 package，会让 fake retriever、eval 与 implementation 使用不同 Python type。

处理：facade 直接 import/re-export application class，并用 identity assertion 测试 `services.retrieval.SearchQuery is application.retrieval.SearchQuery`。

### 问题 D：port 原来定义在 tool adapter 包

`DocumentRetrieverPort` 放在 `tools/dependencies.py` 意味着其他非 tool use case 若要依赖 retrieval，只能反向 import tool layer或再次定义 Protocol。

处理：port 与 request/result 一起下沉 application；ToolDependencies 组合对象只引用它，不再拥有 retrieval contract 定义。

### 问题 E：只改 import 不会阻止回归

开发者以后仍可能为了调用一个 helper 再从 tools import services.retrieval，单次 code review 不足以锁定。

处理：增加递归 `TOOLS_CONTRACT`，禁止 tools 指向 services 及其他外层；application contract 同时保证 retrieval.py 不能反向依赖实现。

## 验证范围

定向验证覆盖：

- application 与 compatibility facade 类型 identity；
- SearchQuery copy/validation、SearchResult serialization；
- ToolDependencies 的 structural retrieval port；
- document tools typed query、raw filter handoff、save/refresh；
- hybrid BM25/vector/RRF/filter/cache/concurrency；
- internal rankers 与 metadata normalization；
- retrieval eval runner；
- application/tools architecture contracts；
- Ruff、mypy 与 `git diff --check`。

| 验证 | 结果 |
|---|---|
| retrieval/tools/eval/architecture targeted pytest | 75 passed；3 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 696 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 150 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- taxonomy/normalization/filter/inference 仍在 `services.retrieval`，它们是纯规则但已形成内部单向依赖；是否建立独立 retrieval domain package 应与私有文档 ACL/query policy 一起评估；
- SearchResult 仍提供 `to_dict()` 作为 tool/eval delivery 兼容边界，尚未单独提取 serializer；
- `services.retrieval` package facade 仍保留 public type re-export，删除需先确认仓库外调用方；
- 真实版本化 corpus 尚未准备，不能用本批类型重构声称 Recall/MRR/latency 提升；
- DocumentStorePort/WebSearchPort 等其他 tool ports 仍在 `tools/dependencies.py`，后续应按是否存在非 tool use case 决定是否下沉，不机械搬迁。
