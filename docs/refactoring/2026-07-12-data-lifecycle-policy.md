# 数据生命周期策略、GenerationInventory 与 processed-command ownership

## 本批目标

D7 最后一项要求在实现 GDPR-like API 前明确 retention、删除与备份策略。当前代码有 approval TTL、Redis AOF、
Learning/FAISS generations、processed command replay evidence、Profile envelope 和 migration backup，但没有统一回答：哪些数据
可以自动删、哪些必须覆盖 checkpoint replay、备份包含什么、如何恢复，以及 tenant 删除是否真的能定位所有记录。

本批建立公开 [data-lifecycle policy](../data-lifecycle.md)，并落两项立即可执行的安全约束：共享只读
`GenerationInventory`；新 processed command 的 deterministic tenant owner key。自动 generation/command/backup pruning 继续
禁用，删除 API 继续被 Auth、repository delete contract、backup/AOF 和并发前置条件阻断。

## 最终策略

### 1. 不虚构统一天数

数据按真实用途分类，而不是全局写一个任意 30/90 天：

- pending input approval：现有 Redis TTL，默认 900 秒，resolve 使用 GETDEL；
- LangGraph checkpoint：当前无 application TTL，由 Redis 部署策略控制；
- LearningState/Profile：当前状态保留到未来显式 authenticated delete；
- FAISS：共享知识库，不属于当前 tenant user-data deletion；
- non-current generation、processed outcome、legacy source、migration backup：自动 pruning 禁用；
- Web search 本地只保存日期/调用次数，不保存 query/result；
- logs/Langfuse/frontend storage：分别由外部 sink/用户设备控制，backend delete 不能声称已物理删除这些副本。

有限 processed-command retention 必须晚于 checkpoint 最大 replay window。当前 checkpoint 本身没有 TTL，因此正确策略是
retain/protect，而不是为控制文件大小先删幂等证据。

### 2. GenerationInventory 是 read-only primitive

`GenerationStore.inventory()` 返回：

```text
manifest_exists
current_generation
current_generation_present
generation_ids
non_current_generation_ids
unknown_entries
```

它不删除、不修改 manifest，也不把 non-current 命名为 orphan。测试覆盖 current、另一个合法 generation、未知目录/文件、
manifest 指向缺失目录和非法 manifest。

自动 GC 仍要求：所有 writer/GC 共用 process lock 或 single-writer 约束、per-generation publication/history metadata、
minimum version/age、备份恢复演练、dry-run/current protection，以及 publication/GC 并发 fault tests。当前唯一递归删除仍是
manifest publication 开始前的 unpublished draft cleanup。

### 3. 新 processed command 可按 tenant 定位

原 processed command map 的 key 是：

```text
SHA-256(user_id, namespace, session_id, tool_call_id)
```

该 digest 不可逆，entry 只含 fingerprint/completed/result。未来拿到合法 tenant subject 后，无法判断哪些 outcome 属于该
tenant，也无法在同一 LearningState transaction 中做 tenant delete。

`UpdateLearningStateCommand.owner_key()` 现在计算稳定 `SHA-256(user_id, namespace)`，新 entry 保存 `owner_key`。repository
允许旧 entry 缺少 owner，存在时必须是 64 位 hex digest。owner key 对同 tenant 的不同 session/tool call 相同，不同 tenant
不同；entry 不重复写 raw tenant/session/tool id。

它只是 routing/index key，不是匿名化：tenant identifier 低熵时，plain digest 可被猜测。文档与测试明确不把它称为
pseudonym/HMAC。legacy outcome 无 owner 且无法从 key 反推，因此被标记为 retention-protected，未来删除前需要显式迁移或
full-snapshot rule。

### 4. 删除 API 前置条件

Policy 固定了十项前置条件，包括：可信 Auth subject/namespace authorization、typed inventory/export/delete ports、
LearningState 一次事务过滤 records/memories/owned outcomes、Redis tenant key 删除、legacy unowned outcome 规则、backup/AOF/
logs/browser 限制披露、无内容复制的 audit、legal hold、write/delete race tests，以及 shared FAISS exclusion。

因此关闭 D7 的“明确策略”不关闭 D6 Auth，不新增 REST delete/export，也不声称 GDPR compliance。

### 5. 备份与恢复

Generation repository 必须按 `current.json + 完整 referenced generation` 备份/恢复，不能抽取 generation 内单个文件。
Migration backup/legacy source 至少保留到：新 repository load 验证、隔离目录恢复演练、rollback window 结束。Redis AOF/RDB、
host snapshot、Langfuse/log sink 和浏览器本地副本必须另行计入。

恢复流程要求停止 writer、先保留故障现场、恢复完整 repository、跑 contract/readiness/tenant sample，再记录 commit、backup、
operator 和验证结果。当前没有凭 generation 目录存在就虚构 RPO/RTO。

## 实施中遇到的问题

### 问题 A：non-current generation 无法可靠区分 history 与 orphan

current manifest 只记录当前 generation。发布开始后失败留下的目录与曾经成功 current 的旧代，在目录结构上没有足够元数据
区分；直接按“非 current 全删”可能消灭唯一可回滚版本或 publication race 中已经被指向的数据。

处理：只实现 inventory，字段使用 `non_current_generation_ids`。Policy 明确要求 publication metadata/process lock 后再做
GC；D2 的 GC TODO 保持未完成。

### 问题 B：幂等 key 保护隐私同时丢失 owner addressability

完整 identity digest 避免把 tenant/session/tool id 当 JSON key 明文，但未来 tenant delete 无法反查。把 raw tenant 再写进
每个 command 可以解决，却增加重复敏感字段。

处理：新增 tenant-only owner digest。它足以由 authenticated tenant 重算并筛选，又不重复 session/tool id；同时明确它
不是不可关联匿名化。

### 问题 C：升级 schema 与 backward compatibility 的权衡

owner key 是新增 optional metadata。若立即把 LearningState schema v1 升为 v2并强制字段，旧 processed entries 无法生成
owner，普通 reload/save 会失败或被迫伪造归属。

处理：v1 envelope 保持可读，新 entry 写 owner，repository 对 present owner 严格校验、对 missing legacy owner 保留。未来
真正改变 deletion semantics 时再用显式 migration/schema v2，而不是在本批静默给历史 outcome 猜 owner。

### 问题 D：策略文档不能掩盖未实现能力

写一份“删除政策”很容易被误读为 delete API/physical erasure 已完成，尤其 Redis AOF、备份和浏览器 storage 不由当前
repository 控制。

处理：policy 首段声明不是 compliance claim；root README/architecture/development 统一链接；D1/D2/D6 TODO 保持未完成；
架构测试反向禁止 persistence adapter 在前置条件完成前暴露 public delete/prune/purge/gc/retention method。

## 测试与门禁

新增/扩展覆盖：

- GenerationInventory current/non-current/unknown/missing-current/invalid-manifest；
- owner key 跨 session/tool 稳定、跨 tenant 不同；
- persisted command 只写 owner digest，不重复 raw identity；
- invalid owner key 被判为 corrupt command；
- legacy contract snapshot 无 owner 继续读取；
- persistence adapter 无未经批准 public deletion API 的 AST gate。

| 验证 | 结果 |
|---|---|
| generation/learning/repository/architecture 聚焦 pytest | 49 passed |
| 全量后端 pytest（frontend build 后串行） | 434 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy | passed，3 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式变化，因此不重复浏览器视觉 smoke。三条 pytest warning 仍来自 LangGraph/Starlette 的既有
弃用提示。

## 保持不变与后续工作

保持不变：approval TTL/GETDEL、checkpoint persistence、LearningState schema/load、generation publication、Profile/FAISS
storage、migration backup 和所有 API。旧 processed outcome 不被修改或删除。

D7 数据模型与迁移纪律已完成。尚未完成且不能被本策略替代：D1 approval encryption/AOF hardening，D2 multi-writer lock 与
generation GC，D6 trusted Auth/AuthZ 和 user deletion use case，真实 backup restore drill，以及 external log/Langfuse/Redis
部署 retention 配置。
