# 2026-07-12：Eval baseline compatibility 与 retrieval corpus identity

## 本批结论

本批补上了 eval 指标比较之前的身份门禁，并解决 retrieval runner 对整套应用资源的无关依赖：

- retrieval manifest 现在记录文档、分块和 FAISS 向量索引的内容指纹；
- retrieval settings fingerprint 记录实际生效的 BM25/vector candidate Top K、RRF K 和 query embedding 目标；
- 新增 manifest schema/integrity 校验与 baseline/candidate compatibility comparator；
- 新增 `python -m evals.check_manifest_compatibility`，为后续 PR metrics diff 提供可独立执行的前置门禁；
- 新增 `RetrievalResources`，离线 retrieval eval 不再初始化 learning、memory、profile、web search 等无关资源。

本批没有把“版本化真实 corpus 已准备好”标为完成。本机 smoke corpus 仍为空；现在只是已经能够可靠识别 corpus 是否相同，并阻止不同 corpus 的指标被直接比较。

## Corpus identity 的边界

`evals/retrieval_corpus.py` 生成 version 1 `retrieval_corpus` subject identity。artifact 只保存：

- 有序 documents 的 count 与 canonical JSON SHA-256；
- 有序 chunk metadata 的 count 与 canonical JSON SHA-256；
- FAISS serialized index 的 count、dimension 与 SHA-256；
- chunk size / overlap；
- 以上字段的总 fingerprint。

原始文档内容、分块文本和本地路径不会写入 manifest。顺序被纳入 hash，因为 BM25 tie-break、document position 和 FAISS vector position 都可能受顺序影响。

如果 index 存在但 vector count 与 chunk count 不一致，runner 在执行 case 前失败，不会为不一致快照生成一个貌似可信的 identity。没有 index 时显式记录：

```json
{"vector_index": {"status": "absent"}}
```

它与“存在一个 0-vector FAISS index”不是同一状态。

## Settings identity 的补强

旧 retrieval manifest 只记录 CLI 的 `mode/top_k/vector_top_k`。当 `--vector-top-k` 未传时，它记录的是 `null`，而实际运行使用 `Settings.HYBRID_RAG_VECTOR_TOP_K`；BM25 candidate Top K 和 RRF K 则完全没有进入 identity。

现在 fingerprint 覆盖：

- mode 与最终 result Top K；
- effective BM25 candidate Top K；
- effective vector candidate Top K；
- RRF K；
- case limit / disabled-case policy；
- vector/hybrid 模式的 embedding model ID 与安全 endpoint identity。

endpoint 只保留 scheme、path 和 host SHA-256，不保留 hostname、userinfo、query token。BM25 模式不绑定无关的 embedding 配置。

## Compatibility gate

`compare_eval_run_manifests()` 先校验 manifest 自身，再判断是否可比。判定结果有四种：

| 状态 | 含义 | CLI exit code |
|---|---|---:|
| `compatible` | 身份相同且 provenance 可验证，可以继续比较 metrics | 0 |
| `incompatible` | runner、dataset、settings、runtime 或 subject identity 已知不同 | 1 |
| `unverified` | 没有发现已知差异，但 dirty/unknown Git、缺 corpus 或取不到 runtime identity | 2 |
| `invalid` | manifest schema 或内部 fingerprint 校验失败 | 2 |

比较规则刻意不要求 baseline 与 candidate 的 runner commit 相同：PR eval 的目标正是比较两个不同代码提交。它要求两边 commit 都已知、worktree 默认 clean；commit 是 provenance，不是 workload identity。必要的本地诊断可以显式传 `--allow-dirty`，CI 不应使用这个开关掩盖来源不明的 artifact。

online runner 必须取得同一个远端 runtime fingerprint。offline runner 必须声明 `not_applicable`；retrieval runner 还必须有合法 `retrieval_corpus` identity。旧版 retrieval manifest 缺 corpus 时结果是 `unverified`，不会被误判为 compatible，也不会把“证据缺失”说成“已知内容不同”。

## 资源组合边界

实施时发现 `run_retrieval_eval` 通过 `AppResources.create()` 获取检索器，因此一次纯 retrieval eval 会额外：

- 加载或创建 learning state；
- 构造 memory/profile/web search；
- 读取 model price table。

这既增加副作用，也让 retrieval eval 的失败面包含无关模块。本批新增最小 `RetrievalResources(settings, faiss_store, hybrid_retriever)` composition，并让 `AppResources` 复用它。测试用故障注入证明 retrieval-only composition 不会调用 learning-state initializer。

## 实施中遇到的问题与解决

### 1. Generation ID、路径和 count 都不能代表 corpus 内容

同一路径的内容可变化，不同 generation 可保存相同内容，相同文档数也可能是完全不同的语料。最终以 canonical content hash 为主，并额外 hash 实际 FAISS index bytes；storage generation 不参与 compatibility。

### 2. 当前 embedding 配置不等于建索引时的 embedding 身份

把当前 `EMBEDDING_MODEL` 填进 corpus identity 会错误暗示该模型一定构建了现有 index。最终将两件事分开：

- corpus identity hash 现有 vector index bytes，证明两次评测使用相同向量；
- settings identity 记录本次 query embedding 的 model/endpoint，证明查询侧配置相同。

未来如果需要从源数据重建完全相同的 index，仍应在持久化 snapshot manifest 增加 build-time embedding provenance；本批不伪造该事实。

### 3. 已知不兼容与无法验证不能混成一个布尔值

dirty worktree、旧 manifest 缺 corpus、runtime endpoint disabled 都不等于“内容一定不同”。如果只返回 `False`，CI 和人工排查无法区分修配置、重跑还是接受真实 workload 变化。因此 comparator 保留 `incompatible/unverified/invalid`，并输出稳定 issue code/path。

### 4. Manifest 字段也需要验证自己的 fingerprint

仅比较保存的 fingerprint 会让被手工修改但未重算的 artifact 进入比较。本批会重算 settings、runtime 和 subject fingerprint；篡改结果为 `invalid`，不会被当作普通回归。

## 真实 CLI smoke

使用临时 `DATA_PATH`、seed disabled、BM25、`--limit 0`：

- 未读写项目数据目录；
- manifest runner 为 `offline_retrieval_eval`；
- runtime status 为 `not_applicable`；
- corpus 为 0 documents / 0 chunks / vector `absent`；
- 生成稳定 corpus fingerprint；
- manifest 与自身在 `--allow-dirty` 本地诊断模式下判定 `compatible`。

临时目录在 smoke 后删除。空 corpus 只验证 artifact plumbing，不是质量 baseline。

## 验证状态

| 验证 | 结果 |
|---|---|
| manifest/corpus/compatibility/retrieval/resources targeted tests | 35 passed，2 个既有 dependency deprecation warning |
| evals + resource targeted Ruff | passed |
| evals + resource targeted mypy | passed |
| retrieval temp-data CLI smoke | passed |
| compatibility CLI smoke | passed |
| 全量 backend pytest | 645 passed，3 个既有 dependency deprecation warning |
| 全量 Ruff / mypy | passed；mypy 142 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未进入版本树或待提交差异 |

## 后续约束

1. 准备受版本控制或可寻址的真实 retrieval corpus，再跑 BM25/vector/hybrid before/after baseline。
2. metrics diff/threshold 工具必须先消费 compatibility gate 的 exit code；不能只在报告里展示 warning。
3. 部署产物补 target/deployment commit 后，online manifest 分开记录 runner commit 与远端 target commit。
4. 若要求“可重建相同向量索引”，扩展 FAISS snapshot manifest 保存 build-time embedding provider/model/version；不能用评测时配置倒推。
