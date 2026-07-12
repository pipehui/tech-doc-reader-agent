# 2026-07-12：Context-compaction PR regression gate

## 本批结论

本批把“manifest 可比”推进到实际 CI metrics 阻断：

- 新增 versioned threshold policy model；
- 新增 runner summary adapter 与 result regression comparator；
- 新增 `python -m evals.check_result_regression`；
- 从 clean commit `3559223b5fd7af9be5464e04ae23ee1ab2c55113` 生成并跟踪 context-compaction v1 baseline；
- backend CI 每次运行 6-case/10-iteration candidate，再先检查 manifest compatibility、后检查 metrics；
- policy 每个指标同时包含 absolute limit 与 max regression delta。

本批只接入 deterministic context-compaction。Retrieval 当前没有真实版本化 corpus，online eval 需要受信 secrets/target；二者没有被塞进普通 PR job，也没有以 skip-success 冒充通过。

## 模块边界

### `evals/thresholds.py`

只负责 policy schema：

- `policy_id` / runner / schema version；
- metric direction：`higher` 或 `lower`；
- absolute limit；
- max regression；
- canonical policy fingerprint。

它不知道 JSONL、runner summary 或 CI。

### `evals/result_comparison.py`

负责比较 use case：

1. 调用已有 manifest compatibility gate；
2. 验证 baseline/candidate rows、唯一 case ID 和集合一致性；
3. 按 runner 选择现有 `summarize_results()` adapter；
4. 对 higher/lower 指标计算 absolute 与 regression 两个判定；
5. 输出 `passed/failed/not_comparable/invalid`。

### `evals/check_result_regression.py`

只负责文件 I/O、JSON 输出与进程 exit code：

- `0`：可比且全部阈值通过；
- `1`：结果集或指标回归；
- `2`：manifest 不可比、policy/JSONL/输入无效。

CLI 不会绕过 manifest comparator 重复实现一套弱判断。

## Baseline 与 policy

tracked baseline：

- 6 cases；
- 10 iterations；
- runner Git clean；
- dataset/settings/runtime-not-applicable identity 完整；
- baseline correctness 1.00；
- compacted correctness / answer consistency 0.8333；
- policy expectation 1.00；
- checkpoint reduction 0.6277；
- prompt-byte reduction 0.6931；
- approximate input-token reduction 0.4008。

0.8333 不是漂亮数字，而是已知 `raw_tool_dependency` 反例的真实 `5/6`。policy 不会把它改写成 1.00；它防止当前保真度进一步下降，同时保留后续显式修复该反例的空间。

size/token-proxy 指标既有绝对下限，也允许小幅 delta：

- checkpoint：absolute 0.60，max regression 0.03；
- prompt bytes：absolute 0.65，max regression 0.05；
- approximate tokens：absolute 0.35，max regression 0.06。

correctness、case count 与 error count 不允许回归。compaction latency 不阻断，因为它受共享 CI runner 调度影响；provider latency/tokens 也不在这套完全离线 policy 中。

## 实施中遇到的问题

### 1. Manifest compatible 不是 metrics passed

上一批只回答“两个结果是否来自同一 workload”。如果 CI 只运行该命令，即使 candidate correctness 归零，manifest 仍会正确地返回 compatible。最终把 compatibility 作为 comparator 的前置条件，而不是最终结论。

### 2. 只设绝对阈值会掩盖缓慢退化

例如 baseline 0.69、absolute floor 0.60，连续多个 PR 每次下降 0.02 仍可能长期绿。每项 policy 因此同时检查 candidate absolute 与相对当前 baseline 的 regression amount。

### 3. 只比较平均值会漏掉结果集不完整

candidate 少跑一个困难 case 可能让均值上升。comparator 在 summarize 前验证 row ID 唯一，并要求 baseline/candidate case ID 集合相同；case 缺失是 gate failure。

### 4. Baseline manifest 必须来自 clean tree

baseline 在开始本批代码修改前生成。manifest 记录 commit `3559223...` 与 `dirty=false`；随后才把 artifact 加入工作树。不能先改代码再手工把 dirty 改成 false。

### 5. 本地开发 candidate 天然 dirty

本批真实 smoke 使用 `--allow-dirty` 比较当前开发 candidate，仅用于实现验证；tracked baseline 自比较不使用该开关并严格通过。CI checkout clean，不传 `--allow-dirty`。

## 真实 smoke

### Strict baseline self-check

- manifest status：compatible；
- policy fingerprint：`45748c7d508e4a6e274fde9d8eea0db4cfcd4bf68006fc6c8323e31c072b7b5e`；
- 10 checks，0 failed；
- exit code 0。

### Current candidate

在当前 dirty development tree 重新执行 6 cases / 10 iterations，并显式允许 dirty：

- manifest status：compatible；
- answer consistency：baseline/candidate 都为 0.8333；
- checkpoint reduction：baseline/candidate 都为 0.6277；
- 10 checks，0 failed；
- exit code 0。

## 验证状态

| 验证 | 结果 |
|---|---|
| threshold/result/manifest/context targeted tests | 29 passed，2 个既有 dependency deprecation warning |
| targeted Ruff / mypy | passed |
| strict baseline self-check | passed，10/10 checks |
| dirty candidate CLI smoke | passed，10/10 checks |
| CI-equivalent command | 命令已接入 workflow；本地因开发 tree dirty 使用显式诊断开关 |
| CI workflow YAML/step contract | parsed；regression gate step/command present |
| 全量 backend pytest | 672 passed，3 个既有 dependency deprecation warning |
| 全量 Ruff / mypy | passed；mypy 146 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未进入版本树或待提交差异 |

## Baseline 更新纪律

1. 行为、dataset、settings 或 policy 有意变化时创建新版本目录/文件，不原地覆盖 v1。
2. 新 baseline 必须来自 clean commit，manifest integrity/compatibility 必须先通过。
3. 阈值调整要在重构记录说明事实依据；不能只因 CI 红而放宽。
4. Retrieval gate 等真实 corpus 可寻址后再接；不能复用 context policy 或空 corpus artifact。
