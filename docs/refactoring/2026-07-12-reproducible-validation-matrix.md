# Phase 4 重构日志：可复现验证矩阵与无密钥 Baseline

## 1. 本批目标

后端 TODO 要求同时记录 quick/full agent eval、retrieval eval、frontend check 的可复现命令，并在没有模型密钥时至少保存 unit/offline baseline。仓库已有 `docs/evaluation.md`，但审计发现：

- 命令按评测类型分散，没有一张说明 Redis、provider key、runtime identity、corpus 前置条件的矩阵；
- README/评测文档仍把 2026-04-29/30 数字称为“当前”，但当前 checkout 没有 companion manifest/artifact；
- 当前本地 document store 为 0 documents / 0 chunks，BM25 runner 全 0 不能解释成质量回归；
- `docs/development.md` 的项目结构仍列出已删除的 `services/assistants` 与 `services/vectordb`；
- 无密钥时哪些 gate 能完整执行、哪些必须 `not_run` 没有统一说明。

本批不改生产代码，建立可复现事实边界并修正文档漂移。

## 2. Clean baseline identity

所有新 offline runner 在 clean commit：

```text
f6e0f6bfda829b55c91b34f35250d2010ed8eb60
```

执行。Context-compaction 与 BM25 manifest 均记录：

```text
runner_git.dirty = false
runtime_identity.status = not_applicable
```

`not_applicable` 表示 runner 不调用远端 agent runtime，不是缺失 identity。输出写入受 `.gitignore` 保护的 `eval_results/` / `eval_reports/`，没有加入提交。

## 3. 无密钥结果

### Backend / Frontend gates

| 检查 | 结果 |
|---|---|
| Backend pytest | 717 passed，4 warnings |
| Ruff | passed |
| Mypy | 162 source files，0 issues |
| Frontend Vitest | 20 files / 85 tests passed |
| Frontend TypeScript | passed |
| Frontend production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |

这些检查不需要 Redis、LLM、embedding 或网络 provider。四条 pytest warning 中三条来自 LangGraph/LangChain/Starlette 第三方弃用提示，另一条是本机 `.pytest_cache` 无写权限。

### Deterministic context-compaction

```text
dataset sha256  = 7ef6f01dfdc0b28fea9bc04619be62bb15d28f48bf1564bf563fccc813a4019a
settings sha256 = 91e0a68195891ce4a56dcdba7628ef41c7c1464d7535d784d92b3747cd24e65c
cases           = 6/6 done
consistency     = 0.83
checkpoint      = 62.8% reduction
```

反例仍是 raw-tool-only fact：summarizer 不复制旧 ToolMessage raw payload，因此该 case consistency 为 0；这支持继续默认关闭 compaction。Marker recall 与 approximate tokens 只是 deterministic proxy，不能替代真实模型回答、provider tokens 或 latency。

### Empty-corpus BM25 diagnostic

```text
dataset sha256  = 2343745d221113401a11e87a9181d0ecf0376fe6de8291943eb2c2dca4f1f00c
corpus sha256   = f1551a21a1f95631e1c262a96533fda66a6da53e5e2c9f2a7979576677a7df1e
documents       = 0
chunks          = 0
vector index    = absent
cases           = 60/60 done，0 errors
recall/MRR/etc. = 0
```

这证明 runner、manifest 与空库路径可执行，不是 retrieval quality baseline。没有真实版本化 corpus 时：

- 不把全 0 与旧 4 月数字比较；
- 不跑 vector/hybrid 后宣称模型质量；
- 不设置虚构 regression threshold；
- 先准备 corpus + companion manifest，再跑 before/after/filter eval。

## 4. 明确未执行

本批将以下项目记录为 `not_run`：

- quick/full agent eval；
- provider-backed vector/hybrid retrieval；
- async concurrency smoke。

原因不是代码失败，而是缺少受信的运行中 Redis/API、真实 provider 配置、非空版本化 corpus，以及可验证 deployment/runtime identity。Unit/offline 通过不能替代这些 online 结果。

## 5. 历史指标处理

README 与 `docs/evaluation.md` 中 2026-04-29/30 的 agent/retrieval/concurrency 表格没有 companion artifact 跟随当前 checkout。本批：

- 保留数字，避免删除历史；
- 全部改标为 historical/unverified；
- 明确禁止用于当前 regression attribution；
- 把 2026-07-12 clean baseline 放在文档最前并链接复现命令。

未来 README 新增“当前结果”必须同时有日期、dataset/settings/subject identity、runner/deployment commit 和可访问的脱敏 artifact/manifest。

## 6. 开发文档修正

- `docs/development.md` 使用 `python -m` 命令，Windows 指明 `conda activate agent` 与可选 UTF-8 环境变量；
- 质量门禁加入 frontend test/check/build/audit，并明确 `npm audit` 尚非 CI blocking job；
- 项目结构改为 agents/application/infrastructure/runtime/services-compatibility 当前边界；
- `docs/evaluation.md` 增加按前置条件分类的验证矩阵。

本地 `docs/todo` 同步勾选命令/baseline 条目，但仍受 `.gitignore` 保护，不进入公开提交。

## 7. 后续约束

- Offline manifest 的 `dirty=false` 只证明 runner worktree 干净，不证明旧线上 deployment identity。
- Ignored local artifact 不能自动成为团队 baseline；共享时必须 version 化 results + manifest + policy + README。
- 空 corpus 指标永远只标 diagnostic。
- Live eval 缺条件时写 `not_run`，不允许用“CI 绿”推断模型质量。

本批提交主题：`docs: record reproducible validation baseline`。
