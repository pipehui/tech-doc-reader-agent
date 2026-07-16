# 13 - 兼容层、版本与数据迁移

重构后“旧名字还存在”不一定是重复实现；它可能是受控 compatibility facade。反过来，“新代码能读旧 JSON”也不代表迁移已经完成。本章把兼容分为四类：import、网络协议、磁盘数据和浏览器缓存，并说明各自的删除条件。

## 先区分四种兼容

| 类型 | 保护对象 | 当前机制 | 典型删除风险 |
| --- | --- | --- | --- |
| Python import | 仓内/仓外旧导入路径 | re-export / constructor facade | 外部脚本 import 立即失败 |
| API/SSE | 已部署前后端/客户端 | strict schema、unknown event 策略、稳定字段 | 滚动升级协议断裂 |
| 磁盘数据 | 旧 learning/profile/FAISS 文件 | fallback reader + generation/envelope | 用户历史/索引无法加载 |
| 浏览器缓存 | 旧 session/transcript key | legacy key、version gate | 本地展示历史丢失或串 tenant |

同一批重构可以删除内部 import，却必须保留数据 reader。不要用“一次全删干净”处理不同生命周期的兼容。

## `services` 不是当前业务层

目录：[`tech_doc_agent/app/services`](../../tech_doc_agent/app/services)。

当前只允许三个 Python 文件：

```text
services/__init__.py
services/retrieval/__init__.py
services/user_profile.py
```

这个文件集合被 [`tests/test_architecture_dependencies.py`](../../tests/test_architecture_dependencies.py) 固定。production 的 core/application/runtime/graph/infrastructure/API/tools/agents、bootstrap/composition/main 都不能反向 import services。

因此读代码时看到 `services`，应理解为“旧调用方入口”，不是让新实现继续放进去的目录。

### Retrieval facade

[`services/retrieval/__init__.py`](../../tech_doc_agent/app/services/retrieval/__init__.py) 只 re-export：

```text
HybridRetriever
MetadataFilter
RetrievalMode
SearchQuery
SearchResult
```

真实 contract 在 `application/retrieval.py`，真实 implementation 在 `infrastructure/retrieval/`。新代码必须直接 import owner；facade 本身不保存算法、filter 或 model 定义。

[`tests/test_retrieval_contracts.py`](../../tests/test_retrieval_contracts.py) 刻意从旧 facade import，验证它指向同一 contract/implementation。这不是鼓励 production 继续使用，而是证明迁移窗口内旧入口没变质。

### User profile facade

[`services/user_profile.py`](../../tech_doc_agent/app/services/user_profile.py) 比纯 re-export 稍厚，因为旧 API 曾允许：

```python
UserProfileService(settings, memory_store?)
get_user_profile(...)
update_user_profile(...)
get_user_profile_summary(...)
get_user_context_summary(...)
```

facade 的 `_build_service` 负责按旧构造方式创建 `ApplicationUserProfileService + JsonUserProfileRepository`，随后全部委托 typed application service。它保留旧返回 dict/free function contract，但不复制 profile merge 规则。

生产 `AppResources` 不经过它；资源由 composition 注入 application service。相关兼容测试是 [`tests/test_user_profile.py`](../../tests/test_user_profile.py) 和 [`tests/test_user_profile_tools.py`](../../tests/test_user_profile_tools.py)。

### 什么不能加回 services

- 新 repository/provider；
- 新 application use case；
- graph/runtime orchestration；
- tool factory；
- resources singleton；
- 为规避依赖方向而做的“万能 manager”。

如果旧调用方需要一个新能力，应先判断这是否仍属于兼容范围。facade 不应演化成第二套 public API，否则永远无法删除。

## 包级 `__init__` 与兼容 facade 的区别

[`graph/__init__.py`](../../tech_doc_agent/app/graph/__init__.py)、[`tools/__init__.py`](../../tech_doc_agent/app/tools/__init__.py)、[`api/sse/__init__.py`](../../tech_doc_agent/app/api/sse/__init__.py) 提供当前包的受控 public surface。它们未必是 deprecated；目的是避免调用方依赖每个内部文件布局。

判断规则：

- owner package 的稳定导出：可以作为当前 API；
- relocated old namespace 的导出：compatibility facade；
- 私有 `_name` alias：只为 staged refactor/tests，不应成为新 public API。

例如 [`infrastructure/retrieval/hybrid.py`](../../tech_doc_agent/app/infrastructure/retrieval/hybrid.py) 底部的 `_tokenize/_rank_exact/_reciprocal_rank_fusion` 等私有 alias 指向拆分后的 owning modules。它们让原有 characterization tests 在阶段迁移中证明对象一致，但新代码应直接 import `tokenization/exact/fusion`。

[`infrastructure/retrieval/metadata.py`](../../tech_doc_agent/app/infrastructure/retrieval/metadata.py) 也是包内兼容 facade：旧的单文件职责已经拆到 taxonomy、inference、normalization、filters。新规则只能加在 owner，metadata.py 仅 re-export。

## Import facade 的删除条件

删除一个 facade 前至少满足：

1. `rg` 确认仓内 production、tests、evals、scripts、docs 无旧 import（专门的兼容测试除外）；
2. 发布/包使用方、notebook、运维脚本或下游仓库完成审计；
3. 若项目有 public version，经历声明的 deprecation window；
4. 新路径已文档化；
5. 删除对应兼容测试/architecture allowlist；
6. 全量 collection、mypy、runtime import smoke 通过；
7. 检查 monkeypatch/importlib resource 字符串，不只搜 `from ... import`。

只有“本仓库没人 import”不足以证明仓外没人依赖。若没有仓外使用承诺，也应在变更记录里明确这是假设。

## Monkeypatch 路径是隐藏兼容 contract

Python 函数从 A 移到 B 后：

```python
# consumer.py
from B import operation
```

测试 patch `B.operation` 不一定影响 consumer 已绑定的本地名字；应 patch `consumer.operation`。旧测试若 patch A，facade re-export 同一对象也不保证运行时 lookup 经过 A。

拆模块时应检查：

```powershell
rg -n "monkeypatch|patch\(" tests
```

如果旧 patch path 是外部扩展点，应提供明确 injection point，而不是长期靠 import alias 的偶然绑定行为。

## 网络协议的兼容策略

### REST schema

后端 Pydantic request/response 与前端 runtime decoder 都是 contract。新增 response 字段通常可向前兼容；删除/改类型/把 optional 变 required 会破坏旧客户端。当前 Chat/Approve 请求模型没有设置 `extra="forbid"`，所以旧后端会忽略新客户端发送的未知字段；这可能避免 422，却也意味着客户端不能仅凭请求成功断言新字段已生效。若未来改成严格拒绝未知字段，滚动部署应先升级服务端，再启用客户端发送。

tenant 同时可能来自 header/body/query，resolution 规则必须稳定，不能让旧客户端缺一处就被归到不同 tenant。

### SSE event

旧前端收到**未知事件名**时记录开发 warning 并忽略，给新增非关键 event 留出向前兼容空间。但已知事件 payload 无效会 fatal，因为这表示同名 contract 被破坏。

安全升级顺序：

1. 先部署能忽略/解析新形状的前端；
2. 再让后端开始发送关键新字段/event；
3. 或保持新字段 optional/nullable，等全部客户端升级后再收紧。

如果新事件对完成语义必不可少，不能依赖 unknown-ignore；应设计协议版本/能力协商。

### 稳定 node/tool/agent 名

它们看似内部字符串，却出现在 checkpoint、SSE、Inspector、prompt、eval 和 pending tool call。重命名需要兼容旧 checkpoint 的映射或明确清理/迁移，不是普通变量 rename。

## Learning/Memory 旧数据兼容

当前新格式：

```text
DATA_PATH/learning_state/current.json
DATA_PATH/learning_state/generations/<id>/state.json
```

旧格式：

```text
DATA_PATH/learning_store/records.json
DATA_PATH/memory_store/memories.json
```

[`LearningStateSnapshotRepository.load`](../../tech_doc_agent/app/infrastructure/persistence/learning_state_repository.py) 的规则：

- current manifest 存在：只读新 generation；损坏则明确失败；
- 无 manifest 且任一旧文件存在：读取旧数据为一个内存 snapshot；
- 都不存在：返回 None。

它不会在新 manifest 损坏时回退旧文件。否则旧数据会 shadow 新数据，用户会误以为最近记录丢失。

一次正常 `save` 就会把当前 snapshot 发布成新 generation，但正式迁移更推荐用显式脚本，先 dry-run/backup/报告。

## User profile 旧数据兼容

新路径与 envelope：

```text
user_profiles/<urlencoded user>/<urlencoded namespace>.json
{"schema_version": 1, "profile": {...}}
```

兼容两种旧形态：

1. tenant 路径已有 flat profile，无 envelope；repository reader 接受；
2. 根目录 `user_profiles/<user>.json`；只作为 default namespace fallback。

若新 tenant path 已存在，旧 root profile 被 shadow，不应覆盖新文件。迁移报告会标为 `shadowed` 并保留旧源。

## FAISS 旧 snapshot 兼容

新格式：generation 下三文件 + current manifest。旧格式直接放在 `faiss_store/`：

```text
index.faiss
documents.json
chunk_metadata.json
```

[`FaissSnapshotRepository.load`](../../tech_doc_agent/app/infrastructure/persistence/faiss_snapshot.py) 只有在三份旧文件全部存在时才加载；部分存在报 `IncompleteLegacySnapshot`。加载后下一次 `FaissStore.save()` 会发布新 generation。

迁移 FAISS 时不能只复制 JSON：index vector count、chunk metadata 数、doc references、dimension 必须一致。更换 embedding/chunk 配置通常应从原文档重建，而不是格式复制。

## 显式 legacy migration 工具

入口：

- [`scripts/migrate_legacy_persistence.py`](../../scripts/migrate_legacy_persistence.py)
- [`infrastructure/persistence/legacy_migration.py`](../../tech_doc_agent/app/infrastructure/persistence/legacy_migration.py)

默认是 dry-run：

```powershell
conda activate agent
python scripts/migrate_legacy_persistence.py
```

指定数据目录并输出机器报告：

```powershell
python scripts/migrate_legacy_persistence.py `
  --data-path D:\path\to\data `
  --summary-output migration-plan.json
```

确认后应用：

```powershell
python scripts/migrate_legacy_persistence.py `
  --data-path D:\path\to\data `
  --apply
```

`--apply` 会在 `DATA_PATH/migration_backups/<UTC timestamp>` 创建 backup；也可用 `--backup-dir` 指定。backup 目录不能等于 data root。

### 报告状态

| status | 含义 |
| --- | --- |
| `planned` | dry-run 发现需要迁移 |
| `migrated` | 已备份并发布新格式 |
| `current` | 已有新 generation/envelope，不需要迁移 |
| `shadowed` | 旧 profile 被已有新 tenant profile 覆盖，保留不动 |

### 为什么 plan 后还要校验 digest

每个 source 在规划时记录 SHA256。apply 前、backup 前和每个 action 执行前都会确认文件仍存在且 digest 未变；目标要求不存在时也验证未突然出现。这样 dry-run/人工确认期间若线上进程更新数据，会报 `migration_source_changed/target_changed`，而不是用过期计划覆盖新状态。

backup 已存在且内容相同可复用；内容不同报 conflict。迁移不是“尽量复制”，而是可审计的 compare-and-apply。

### 迁移顺序与回滚

建议：

1. 停止会写这些本地文件的服务，或确保只读窗口；
2. dry-run 保存 report；
3. 检查 planned/current/shadowed；
4. apply 并保留 backup/report；
5. 用 repository/API 读取验证；
6. 启动服务做 learning/profile smoke；
7. 一段观察期后再决定是否归档旧源。

generation publication 本身可通过切回旧 manifest 回滚，但应使用经过校验的运维步骤；不要手工编辑一半 JSON。profile rollback 可从 backup 恢复，但先停写并校验 target 是否在迁移后又被更新。

## Browser storage 兼容

Session repository 先读新版 `tech-doc-agent.context`，失败时读旧 `tech-doc-agent.session` 并补默认 tenant；保存 context 时仍双写旧 session ID。

Transcript 不做猜测式 migration：只有 `version === TRANSCRIPT_VERSION` 才加载。旧/坏 cache 返回 null，随后从后端 state/history 恢复。这是一种“可丢体验缓存”的兼容策略，成立的前提是关键会话事实在后端 checkpoint。

但后端 history 当前不能重建全部 tool card/Inspector event，所以提升 transcript version 会牺牲本地细节。版本升级说明里要写清影响。

删除 legacy session key 双写前，确认所有已发布前端版本不再依赖、用户已有 context 已迁移或接受回退生成新 ID。

## Schema version 不是装饰字段

项目中多个独立版本：

- learning snapshot schema；
- FAISS snapshot schema；
- user profile envelope；
- conversation summary；
- retry/budget/context state；
- frontend transcript。

它们不能共用一个“项目总版本”。每个聚合独立演进、独立 reader/migration。新增字段时决定：

- reader 能否用 default 向后兼容；
- writer 是否保持原版本；
- 语义变化是否必须 bump；
- checkpoint/manifest/eval compatibility 如何判定。

只 bump 常量而不提供 reader/migration 会把所有旧数据标成损坏；只加字段不 bump 也可能让旧 reader 误解新语义。

## Architecture tests 如何防兼容层反向生长

[`tests/test_architecture_dependencies.py`](../../tests/test_architecture_dependencies.py) 做两类门禁：

1. AST/import graph 检查各层 forbidden dependencies；
2. 源码/文件集合 contract，例如 services 只能有三文件、production composition 不 import services、tools 必须走 application service。

[`tests/architecture/import_graph.py`](../../tests/architecture/import_graph.py) 支持嵌套/相对 import，不只是简单文本搜索。

兼容 facade 可以依赖 application/core/infrastructure owner，但不能依赖 API/runtime/graph/tools/composition，否则旧入口会重新成为高耦合中枢。

修改 facade 时至少运行：

```powershell
python -m pytest tests/test_architecture_dependencies.py tests/test_architecture_import_graph.py -q
```

## 何时应该保留，何时应该删除

保留：

- 有明确旧调用方或旧数据；
- facade 很薄、单向委托、受 architecture test 约束；
- 删除会造成不可恢复数据/协议断裂；
- 有可观察的 deprecation/migration 计划。

删除：

- 全部调用方已迁移并有审计证据；
- compatibility 本身阻止 owner contract 演进；
- 旧格式已有备份/迁移完成且超过支持窗口；
- 测试只是在维护无人使用的偶然实现细节。

不要以文件数量作为判断。“多一个两行 re-export”可能比强迫所有外部使用者同日迁移更低风险；“保留一个能创建 concrete resources 的旧 service”则可能重新引入整层耦合。

下一章 [14 - 源码与测试索引](14-source-and-test-index.md) 提供从包、函数和测试三种入口查找代码的总表。
