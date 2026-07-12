# 2026-07-12：长会话 Context Compaction 离线对照评估

## 本批结论

本批为安全上下文压缩增加了独立、可重复、无需外部服务的长会话 eval，并实际运行了 6-case baseline。

结果证明了两件不同的事：

1. 当前 reducer/summary 机制能显著降低长会话 checkpoint 与 tool-heavy prompt 体积；
2. 当前 `extractive-closed-turns-v1` 不复制旧 ToolMessage raw content，因此不能保证依赖历史工具原文的回答一致性。

基于这组证据，继续保持：

```text
CONTEXT_COMPACTION_MAX_MESSAGES=0
CONTEXT_COMPACTION_MAX_SERIALIZED_BYTES=0
```

即机制已实现，但默认关闭。不能因为 62.8% 的平均 checkpoint byte reduction 就忽略 0.83 的信息一致性代理。

## 为什么没有直接复用单轮 Agent Eval

现有 `evals/run_eval.py` 面向已启动服务的单轮 case，主要比较 workflow plan、关键词、行为规则与请求 latency。长会话 compaction 需要额外控制：

- 同一会话连续 30-80 个 turn；
- 同一消息序列的 compaction off/on 双路状态；
- 固定 message threshold、keep-turns 与 summary limit；
- checkpoint/prompt bytes 的同源测量；
- 多次 compaction 的 lineage 与累计耗时；
- 刻意构造旧 raw ToolMessage 依赖。

把这些逻辑硬塞进单轮 live runner 会让 case schema、服务状态和指标含义混在一起。因此本批拆成：

| 文件 | 职责 |
|---|---|
| `evals/context_compaction_eval.py` | case schema、synthetic session 构造、off/on simulation、measurement 与 recall proxy |
| `evals/run_context_compaction_eval.py` | CLI、异常隔离、汇总、Markdown/JSONL artifact |
| `evals/context_compaction_cases.json` | 6 个可审查的长会话 fixture |
| `tests/test_context_compaction_eval.py` | schema、风险反例、指标与报告契约 |

artifact 继续复用 `evals/artifacts.py` 的统一 redaction；本地结果写入已经被 `.gitignore` 忽略的 `eval_results/` 与 `eval_reports/`。

## Case 设计

### 1. Closed text recall

分别把 marker 放在早期 HumanMessage 与早期 AIMessage 中。压缩后 marker 应进入独立 conversation summary，最终 recall proxy 仍应命中。

### 2. Recency precedence

早期 assistant 给出旧 marker，靠近会话末尾的 human 给出修正 marker。probe 按 prompt 消息顺序选择最后出现的 marker，用来验证：摘要位于 prompt 前部，而 retained 新消息仍具有更高优先级。

### 3. Raw tool dependency

marker 只存在于早期 ToolMessage content，后续 assistant 不复述。完整历史能命中；当前安全摘要只记录 tool 名与 success/error，不复制 raw content，因此压缩后返回 unknown。

这个 case 不是为了让 eval 全绿，而是为了锁定已知信息损失边界。

### 4. Tool result restatement

同一个 marker 同时出现在 ToolMessage 和随后 assistant 的自然语言结果中。即使 raw tool payload 被排除，assistant restatement 仍进入摘要，压缩后保持一致。

### 5. Summary bound

80 个 turn、每条带较长 filler，早期 human marker 必须经过多次增量摘要与 12000 字符上限。case 验证三段截断策略仍保留早期头部 marker。

## 指标定义

| 指标 | 当前定义 | 不能声称什么 |
|---|---|---|
| answer consistency | full/compacted prompt 上 deterministic latest-marker recall 是否相同 | 不能等同真实模型回答一致性 |
| checkpoint bytes | `estimate_serialized_bytes(state)` | 不是 Redis 实际 memory/存储占用 |
| prompt bytes | full Agent prompt messages 的规范化 JSON bytes | 不是 provider wire payload 的精确字节数 |
| approximate input tokens | LangChain `count_tokens_approximately` | 不是模型 `usage_metadata.input_tokens` |
| compaction latency | synthetic session 中所有 compactor 调用累计耗时，多次迭代后统计 | 不是 HTTP E2E、Redis I/O 或 LLM latency |

结果中把 `provider_input_tokens` 与 `model_answer_consistency` 显式写为 `null`，防止 artifact 使用者把 proxy 当成真实 usage 或模型质量。

## 实际运行

命令：

```powershell
D:\Tools\miniconda3\envs\agent\python.exe -m evals.run_context_compaction_eval --iterations 10
```

策略：

```text
max_messages=12
max_serialized_bytes=0
keep_recent_turns=3
summary_max_chars=12000
```

总结果：

| Metric | Result |
|---|---:|
| Cases / done / error | 6 / 6 / 0 |
| Baseline task correct | 1.00 |
| Compacted task correct | 0.83 |
| Answer consistency proxy | 0.83 |
| Policy expectation match | 1.00 |
| Checkpoint byte reduction avg | 62.8% |
| Prompt byte reduction avg | 69.3% |
| Approx. input-token reduction avg | 40.1% |
| Compaction latency p50 | 4.107ms |
| Compaction latency p95 | 13.555ms |

Case 结果：

| Case | Turns | Baseline -> Compacted marker | Checkpoint bytes | Approx. tokens | Consistent |
|---|---:|---|---:|---:|---:|
| early user fact | 30 | USER-ORBIT-731 -> USER-ORBIT-731 | 15575 -> 7604 | 1424 -> 1342 | 1 |
| early assistant fact | 30 | ASSISTANT-LATTICE-204 -> ASSISTANT-LATTICE-204 | 15887 -> 7706 | 1427 -> 1344 | 1 |
| newer correction wins | 32 | NEW-CACHE-902 -> NEW-CACHE-902 | 18026 -> 9803 | 1778 -> 1695 | 1 |
| tool-only fact | 30 | TOOL-ONLY-DELTA-488 -> unknown | 37755 -> 9053 | 6015 -> 1480 | 0 |
| tool fact restated | 30 | RESTATED-SIGMA-552 -> RESTATED-SIGMA-552 | 38052 -> 9147 | 6028 -> 1485 | 1 |
| bounded long summary | 80 | LONG-HORIZON-ALPHA-007 -> LONG-HORIZON-ALPHA-007 | 63108 -> 14941 | 9056 -> 2402 | 1 |

## 实际发现的问题

### 1. Byte reduction 不等于 token reduction

两个纯文本 30-turn case 的 checkpoint bytes 减少约 51%，但 approximate input tokens 只减少约 5.8%。主要原因是 checkpoint JSON 中 message ID、类型和 metadata 的固定开销被大量删除，而 extractive summary 仍保留了大部分人类/assistant 文本。

因此不能用 checkpoint size reduction 推导模型成本下降百分比。

### 2. Tool-heavy 会话收益大，但也是信息风险最大处

tool-heavy case 的 approximate token reduction 约 75.4%，因为 raw tool payload 被排除；同一个设计也导致 tool-only marker 丢失。

这说明“减少最多的内容”恰好也是“最需要定义保留策略的内容”。不能通过简单调低 threshold 解决语义问题。

### 3. Assistant restatement 是当前可保留边界

如果 tool 的关键结论由 assistant 重新表达，deterministic summary 能保留；如果事实只存在于 raw tool output，则不能。

这为后续方案提供了三个可比较方向：

1. provider-backed conversation summarizer，对 tool output 做受控事实提取；
2. 为可压缩 tool 定义窄的 public summary/projection，而不是复制 raw payload；
3. 让 tool result schema 显式提供 `summary_for_context`，同时进行 redaction 与大小约束。

任何方向都需要防 prompt injection、secret leakage 与工具 schema 漂移，不能在本批凭空选择。

### 4. 本地微延迟不是线上 SLO

4.107ms/13.555ms 只包含 Python 侧 synthetic compactor，不含 Redis checkpoint I/O、HTTP、模型或 tool latency。它能证明策略不是明显的 CPU 热点，但不能直接写成生产 p95。

## 决策

当前不修改默认阈值，也不把 offline proxy 写成“回答质量保持 83%”。准确表述是：

- 5/6 synthetic retention cases 的 full/compacted marker recall 一致；
- 唯一不一致是明确设计的 raw ToolMessage-only dependency；
- provider answer、provider tokens 与真实 request latency 尚未测量。

## 验证状态

| 验证 | 结果 |
|---|---|
| offline runner | 6/6 done，0 error |
| targeted pytest | 10 passed，3 个既有 warning |
| Ruff | passed |
| direct mypy | 2 source files，passed |
| CI mypy core/schema | 21 source files，passed |
| 全量后端 pytest | 583 passed；3 个既有 deprecation warning 与 1 个本机 pytest cache 权限 warning |
| 前端 test/build/audit | 19 files / 74 tests；2041 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed，任务单未进入 HEAD、origin/main 差异或待提交集合 |

## 下一步

1. 设计 provider-backed off/on runner 或受控双实例运行方式，保证模型、prompt、dataset 和 settings fingerprint 一致。
2. 从 `ContextMetrics` 读取真实 provider input tokens，不用 approximate count 替代。
3. 为 raw tool dependency 比较“tool public projection”与“provider summarizer”两种策略的安全性和保真度。
4. 只有 live answer consistency 和 usage/latency 均满足门槛后，才讨论默认启用。
