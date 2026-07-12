# 2026-07-12：Offline eval shared run manifest

## 本批结论

本批把 online eval 已建立的 versioned run manifest 接入两个离线 runner：

- `run_retrieval_eval`；
- `run_context_compaction_eval`。

三类 runner 现在共用 `evals/manifests.py` 的 commit/dataset/settings identity、共用 `evals/artifacts.py` 的 JSON redaction writer，并在 Markdown report 展示同一组可复现字段。

offline runner 不依赖远端 agent runtime，因此 manifest 明确写：

```json
{"runtime_identity": {"status": "not_applicable"}}
```

这与 `disabled/unavailable` 不同：前者表示评测定义上不需要 runtime，后两者表示 online 目标身份没有取得。

## Retrieval manifest

settings fingerprint 当前覆盖：

- retrieval mode；
- Top K；
- optional vector candidate Top K；
- case limit；
- disabled-case policy。

manifest 同时记录 retrieval case dataset hash 与 runner commit/dirty。

当前没有记录本地 corpus generation/content fingerprint。项目本地 corpus 仍可能为空，且现有 FAISS manifest 主要描述存储 generation/count，不等价于稳定语义 corpus version。因此本批不把“case hash + settings hash”冒充完整 retrieval baseline identity；TODO 保持未完成，直到版本化 corpus 准备好。

## Context-compaction manifest

settings fingerprint 覆盖：

- case limit；
- iterations；
- max messages / max serialized bytes；
- recent turns；
- summary max chars；
- answer metric identity；
- token metric identity。

显式记录 metric 名称可防止未来把 deterministic marker proxy、approximate token count 与 provider-backed answer/token 指标混为一谈。

## Report 与 CLI

两个 runner 新增 `--manifest`，默认写入各自 `eval_results/*.manifest.json`。report 增加：

- dataset SHA-256；
- eval settings fingerprint；
- runner commit；
- runtime identity `not_applicable`。

manifest 在执行 case 前写入，因此资源初始化或 case 执行失败时仍有配置审计线索；结果 JSONL/report 保持原语义。

## 实施中遇到的问题

### 1. 统一 schema 不能统一成假数据

最省事的做法是让 offline runner 也构造本地 RuntimeExecutionIdentity，但这会暗示 retrieval/context proxy 依赖那些 prompts/models。最终扩展 identity status 为 `not_applicable`，保持同一 envelope 而不伪造依赖。

### 2. Retrieval settings 不等于 corpus identity

mode、Top K 与 dataset hash 可以复现 query/算法参数，却不能证明索引内容一致。将 DATA_PATH 字符串 hash 写入也没有帮助：相同 path 内容可变，不同 path 内容可同。

因此本批只写真实已知事实，并在文档/TODO 明确保留 corpus generation/content fingerprint。

### 3. Offline report 仍需兼容纯函数测试

原 report renderer 被测试和其他代码直接调用。manifest 参数设计为 keyword-only optional；不传时输出保持原格式，CLI 传入时才增加 identity 行，避免把 artifact orchestration 强塞进统计纯函数。

## 真实 CLI smoke

### Context compaction

执行 1 case / 1 iteration：

- `early_user_fact` done；
- consistency 1.00；
- checkpoint reduction 51.2%；
- approximate token proxy reduction 5.8%；
- manifest status `not_applicable`。

该数字只用于 smoke，不替代已记录的 6-case/10-iteration baseline。

### Retrieval

在临时 `DATA_PATH`、seed disabled、BM25、`--limit 0` 下运行：

- 未修改项目数据；
- CLI 正常生成 JSONL/report/manifest；
- manifest status `not_applicable`；
- 资源日志明确本地 corpus empty/seed disabled。

## 验证状态

| 验证 | 结果 |
|---|---|
| shared manifest/retrieval/context/artifact targeted tests | 27 passed，2 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| offline runner Ruff | passed |
| offline runner mypy | 3 source files，0 issues |
| context 1-case CLI smoke | passed |
| retrieval temp-data zero-case CLI smoke | passed |
| 全量 backend pytest | 632 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 全量 Ruff / mypy | passed；mypy 139 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未出现在 HEAD、origin/main 差异或待提交文件中 |

## 下一步

1. 为版本化 retrieval corpus 定义 content fingerprint；不要使用路径或文档 count 代替内容身份。
2. baseline comparator 先检查 runner/dataset/settings/runtime-or-not-applicable/corpus compatibility，再比较 metrics。
3. deployment build metadata 加入远端 commit 后，online manifest 分开记录 runner commit 与 target commit。
