# 显式 legacy persistence migration 命令

## 本批目标

learning/memory 与 User Profile 已有 versioned repository，也都能兼容读取旧 JSON；但兼容读取不是可审计 migration。
项目缺少一个能在部署前回答“会改哪些文件、备份在哪里、迁移了多少、重跑是否安全”的显式命令。若把迁移塞进
应用启动，任何 worker 启动都可能在未知时点批量改盘，也无法先发现某个损坏 Profile 会让跨 domain 迁移停在中间。

本批完成 D7 的 migration command 子项：新增默认 dry-run 的 `migrate_legacy_persistence.py`，只处理当前有真实旧格式的
learning/memory 双 JSON 与 User Profile flat/root JSON；提供 apply、backup、versioned summary、完整预检与幂等重跑。
approval 没有待迁移旧格式，不纳入命令。

## 使用方式

默认只规划，不写数据或备份：

```powershell
conda activate agent
python scripts/migrate_legacy_persistence.py --data-path .\tech_doc_agent\data
```

显式应用；未指定 backup 目录时使用 `DATA_PATH/migration_backups/<UTC timestamp>`：

```powershell
python scripts/migrate_legacy_persistence.py `
  --data-path .\tech_doc_agent\data `
  --apply `
  --summary-output .\migration-report.json
```

也可指定独立备份位置：

```powershell
python scripts/migrate_legacy_persistence.py `
  --data-path .\tech_doc_agent\data `
  --apply `
  --backup-dir D:\Backups\tech-doc-reader-agent
```

report 自身带 `schema_version: 1`，包含 `dry_run`、实际 backup dir、按状态汇总和逐项 source/target/detail。状态为：

- `planned`：dry-run 中将执行；
- `migrated`：本次 apply 已完成；
- `current`：目标已经是 versioned 格式；
- `shadowed`：旧 root Profile 已被 tenant-scoped 文件覆盖，保留原文件且不覆盖新目标。

## 最终边界

### 1. 只迁移被证实存在的两类 legacy 数据

LearningState：

```text
learning_store/records.json
memory_store/memories.json
  -> learning_state/generations/<id>/state.json
  -> learning_state/current.json
```

两个旧文件由现有 `LearningStateSnapshotRepository` 一次读取成 typed snapshot，再用现有 generation/manifest 协议发布。
只存在其中一个文件也支持；旧文件不删除。

User Profile：

```text
user_profiles/<encoded-user>/<encoded-namespace>.json  (flat)
  -> 同路径 schema_version/profile envelope

user_profiles/<user>.json  (root legacy, only default namespace)
  -> user_profiles/<encoded-user>/tech_docs.json
```

路径 segment 使用 unquote + strict `TenantContext` 验证，再由 repository 生成规范 target。若 root legacy 的 tenant target
已经存在，repository 的真实读取优先级本来就会选 target；migration 将 root 标成 `shadowed`，不会用更旧数据覆盖它。

### 2. 计划与 apply 分为两个阶段

`LegacyPersistenceMigrator.run()` 先完成所有 domain 的扫描和解析，构造 typed `LearningStateSnapshot / UserProfile` actions。
任何非法 JSON、未知 Profile schema、非法 tenant 或 repository corruption 都在创建 backup/target 之前失败。

只有完整计划成功且调用方传 `--apply` 后才会：

1. 再校验每个 source fingerprint 与 target existence；
2. 复制所有 source 到保持相对目录结构的 backup root；
3. 再次校验当前 action；
4. 调用正式 repository 保存 typed value；
5. 返回 `migrated` report。

public run 把非业务异常接入统一 file-repository error model；已是 `ValidationError/Conflict` 的错误保持稳定 code/cause。

### 3. Backup 是 apply 的强制步骤

apply 有 action 时一定创建 backup；没有“跳过备份”开关。目标备份文件已存在时：内容 hash 相同则复用，不同则以
`migration_backup_conflict` 在任何 target 写入前停止。backup root 不能等于 DATA_PATH 本身，避免 source/destination
重叠。

旧源不删除，因此备份提供独立恢复副本，而 legacy 文件本身还保留一次人工回滚窗口。删除/retention 不属于 migration
命令，继续等待统一策略。

### 4. Fingerprint 防止规划后并发覆盖

每个 source 在 plan 时保存 SHA-256。apply 前、backup 前和每个 action 写入前都会复核；root target/learning manifest
在 plan 时不存在的，apply 时若突然出现则拒绝。这样命令不会基于 A 版本预览，却把同时被 worker 改成 B 版本的文件
覆盖成旧结果。

该检查不等价于跨进程锁：普通应用 writer 不认识 migration lock，因此生产 apply 仍应在停止 writer/维护窗口执行。

### 5. 幂等重跑

首次成功后：

- learning current manifest 存在，repository 只验证并报告 `current`，不再发布 generation；
- tenant Profile envelope 报告 `current`，不重写文件；
- 保留的 root legacy 因 default tenant target 已存在而报告 `shadowed`；
- 没有 action 时不创建第二个 backup 目录。

测试固定 manifest 文本、generation 数和 Profile 文本，证明第二次 apply 没有状态变化。

## 实施中遇到的问题

### 问题 A：root legacy 与 tenant-scoped Profile 同时存在时不能“合并”

旧 root 文件没有 namespace 证据；tenant-scoped default 文件一旦存在，它就是 repository 当前真值。盲目把 root 复制到
target 会造成数据回退，按字段 merge 又没有业务证据决定谁更新。

处理：严格复用 repository precedence。target 存在时 root 只报 `shadowed` 并保留，交由人工审计/retention 后续处理。

### 问题 B：只在每个 action 前验证会产生可避免的半迁移

如果先迁 learning，再发现第三个 Profile schema 已损坏，尽管重跑可恢复，仍制造了不必要的跨 domain partial state。

处理：先解析并验证全部 action，再备份/写入。故障测试把有效 learning legacy 与未知 Profile schema 放在一起，断言
learning manifest 和 backup 都未创建。

### 问题 C：dry-run 与 apply 之间数据可能变化

用户可能先保存 dry-run report，数分钟后 apply；或者 apply 自己在 plan/backup 期间遇到后台 writer。仅凭路径列表无法
证明写的是预览版本。

处理：每次 apply 自己重新 plan，并为该次 plan 保存 source hash/target expectation。测试在 backup hook 前篡改 records，
命令以 `migration_source_changed` 停止且零 target 写入。

### 问题 D：backup 目录重复使用可能静默覆盖审计证据

用户显式指定同一 backup dir 时，简单 `copy2` 会覆盖上一轮文件，破坏“备份对应哪个源版本”的可信度。

处理：已存在且 hash 相同视为可重用；内容不同则稳定 conflict。测试预置不同 records backup，断言任何 generation/
Profile target 都未写。

### 问题 E：并行门禁触发 frontend/dist 读写竞态

第一次全量门禁把 backend pytest 与 `npm run build` 放在同一工作区并行执行。静态文件测试先确认 `dist/index.html`
存在，Vite 随后清理 dist 重建，FileResponse 在短窗口内找不到文件；其余 422 个测试已通过。

处理：等待 build 完成后串行重跑 backend，423 个测试全部通过。后续本地门禁不能并行执行“读取 dist 的 backend test”
与“删除/重建 dist 的 frontend build”；CI 若使用隔离 job/workspace则没有该共享目录竞态。

## 测试与门禁

新增覆盖：

- 三项 migration 的 dry-run 零写入；
- apply 的四个 source backup 与三个 versioned target；
- typed learning/profile reload；
- 第二次 apply 无新 generation、无 Profile rewrite、无 backup；
- 全量 plan 中损坏 schema 导致零写入；
- backup 内容冲突；
- source fingerprint 竞态；
- CLI 默认 dry-run、stdout 与原子 summary-output 等价、report schema version。

| 验证 | 结果 |
|---|---|
| migration/learning/profile 聚焦 pytest | 37 passed |
| 全量后端 pytest（frontend build 后串行复跑） | 423 passed，3 个既有第三方 deprecation warning |
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

保持不变：应用启动不会执行 migration；legacy loaders 仍可读取旧数据；learning generation/Profile envelope schema、
tenant precedence 和原子单文件/manifest 发布不变；旧源不会由命令删除。

限制：learning 与多个 Profile repository 之间没有全局事务。完整预检消除了 schema/冲突类可预防半迁移，真实磁盘故障
仍可能发生在某个 repository 已提交后；备份、旧源保留与幂等重跑是恢复机制。apply 应在 writer 停止的维护窗口执行。
后续仍需可复用于 JSON/未来 SQLite adapter 的 repository contract suite，以及 retention、删除、恢复演练与备份保留策略。
