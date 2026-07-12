# FAISS 快照代际发布与失败回滚

## 本批目标

原 `FaissStore` 同时负责文档归一化、embedding、FAISS 状态和三个文件的持久化。`save()` 依次覆盖 `index.faiss`、`documents.json`、`chunk_metadata.json`，进程在任意两步之间退出都会留下跨版本组合；`build_index()` 又会在 embedding 成功前清空当前内存状态，一次外部服务失败就能让仍可用的索引消失。

本批完成 D2 的 FAISS P0/P1 部分：把持久化抽成 generation snapshot repository，磁盘和内存都采用“先构造候选、校验完成、最后发布”的顺序。

## 最终边界

### 1. `FaissSnapshotRepository` 独占磁盘协议

新布局：

```text
faiss_store/
  current.json
  generations/
    <32-char-generation-id>/
      index.faiss
      documents.json
      chunk_metadata.json
```

`FaissStore` 不再知道三个文件如何写入，只负责构建和查询内存索引。repository 的发布顺序是：

1. 创建不可被 current manifest 引用的新 generation 目录。
2. 将 FAISS index 写入同目录临时文件，刷盘后原子替换 generation 内目标文件。
3. 用共享 `write_json_atomic` 写 documents 和 chunk metadata。
4. 从磁盘重新读取刚写出的三件套并执行一致性校验。
5. 只有全部成功，才用 `os.replace` 原子发布 `current.json`。
6. 发布前失败会尽力删除不可达 generation；即使进程直接退出留下 orphan，loader 也只读取 manifest 指向的 generation。

Manifest 带 `schema_version`、generation、创建时间、dimension，以及 vector/document/chunk 三种 count。generation 只接受 32 位小写十六进制 ID，不能通过 manifest 构造目录穿越路径。

### 2. load 明确拒绝混合或损坏快照

加载时同时验证：

- manifest schema、generation 和 count/dimension 类型；
- FAISS `ntotal` 等于 chunk metadata 数量；
- manifest 中的 vector/document/chunk count 与磁盘实际内容一致；
- document id 非空且唯一；
- 每个 chunk 的 `doc_id` 都指向存在的 document；
- 三个 generation 文件必须同时存在。

失败统一返回安全的 `ValidationError(code="vector_store_corrupt")`，不会把路径或底层异常文本暴露到边界。已有旧版根目录三件套仍能读取；下一次 `save()` 会发布首个 generation，而不是启动时隐式迁移或删除旧文件。旧三件套只存在一部分时会 fail closed，避免资源初始化把损坏库误判为空库后重新 seed。

### 3. 内存状态也使用候选快照

`build_index()` 现在先在局部变量中完成文档归一化、切块、embedding 和 FAISS 构建，再一次替换 `index/documents/chunk_metadata`；失败或空输入都保留当前可用状态。

`add_documents()` 对现有 index 使用 `faiss.clone_index`，在 clone 上追加向量，成功后才发布；documents 和 chunk metadata 也从原地 `extend` 改为成对替换。查询在短锁内取得相互匹配的 index/metadata 引用，然后释放锁再执行 embedding/search，因此不会在发布窗口看到“新 index + 旧 metadata”。

这解决单进程线程内的一致读取，但不声称解决 multi-worker 的 lost update。进程锁或单写者约束仍是独立 TODO。

### 4. 迁移脚本必须走同一发布入口

`scripts/migrate_doc_metadata.py` 原来直接覆盖 `documents_path` 和 `metadata_path`。如果只改在线 `save()`，该脚本会绕过 manifest，继续产生跨代写入。

现在迁移脚本在归一化后调用 `store.save()`，元数据迁移会生成完整新快照并原子切换 current；direct `--help` subprocess 测试也把该脚本纳入入口门禁。

## 实施中遇到的问题

### 问题 A：Windows 对只读句柄 `fsync` 报错

首版在 FAISS 写完临时文件后用 `rb` 重新打开并 `os.fsync`。当前 Windows/Miniconda 环境返回 `[Errno 9] Bad file descriptor`，所有正常保存测试都失败。

处理：用 `r+b` 打开已写出的索引文件，只做 flush-to-disk，不改内容。修复后 Windows 实际文件系统路径上的 generation 保存/回读测试通过。

### 问题 B：包级 re-export 会让普通 JSON store 也加载 FAISS

最初把 repository 加进 `infrastructure.persistence.__init__`。但 Python 导入 `persistence.atomic_json` 前会执行 package init，这会让 LearningStore、MemoryStore、Profile 等只需要 JSON helper 的模块也加载原生 FAISS 依赖。

处理：不在 package init re-export 重型 adapter，`FaissStore` 从具体子模块显式导入。这样复用 atomic JSON 不会反向耦合向量运行时。

### 问题 C：只保护磁盘仍会让当前进程处于半更新状态

只增加 manifest 后，原 `build_index()` 仍会先把 `self.index/documents/metadata` 清空；`add_documents()` 也会直接修改当前 FAISS index。磁盘可恢复并不代表在线进程仍可服务。

处理：build 使用全新 index，append 使用 cloned index，集合采用 copy-on-publish。故障测试同时断言对象引用和内容保持为旧快照。

### 问题 D：generation 清理不能脱离多进程策略单独做

每次成功发布后立即清理旧 generation 看似节省空间，但两个没有 process lock 的 writer 可能交错发布；一个 writer 的清理动作可能删除另一个 writer 刚设为 current 的目录。

处理：当前只删除本次已知发布失败且不可达的 generation，不自动删除成功历史。待 process lock 或明确 single-writer 后，再实现 retention/GC。磁盘增长风险已加入本地 TODO，而不是用不安全清理掩盖。

## 测试与门禁

新增测试覆盖：

- generation 正常发布、manifest 内容、磁盘回读和 legacy 三件套兼容迁移；
- index/documents/chunk metadata 已写后失败，以及 manifest 发布前失败，均保持旧 current 可加载；
- build embedding 失败、append clone 失败、空 build 不破坏当前内存快照；
- vector/chunk count 不一致、chunk 引用不存在、manifest count 漂移；
- generation 路径穿越和不完整 legacy store；
- metadata migration direct CLI import。

| 验证 | 结果 |
|---|---|
| FAISS persistence/resources/script focused pytest | 26 passed |
| 全量后端 pytest（禁用本机不可写 cache） | 336 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，3 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式改动，因此不重复浏览器视觉 smoke。pytest 剩余三条 warning 来自 LangGraph/Starlette 依赖弃用提示；使用 `-p no:cacheprovider` 后没有本机 `.pytest_cache` 权限 warning。

## 保持不变与后续工作

保持不变：对外 `FaissStore.build_index/add_documents/save/load/search_related` 主要调用方式、旧数据可读取、文档 metadata 归一化规则、共享知识库语义和 embedding provider。

后续工作：

- 明确本地 adapter 的 single-worker 约束，或引入跨进程锁/单写者；
- 在并发策略落地后增加 generation retention、orphan 扫描和显式 rollback/repair 工具；
- document metadata 的长期 source of truth 迁入关系型 repository，FAISS 只保留可重建检索索引；
- D3 learning + memory 的组合事务与 idempotency 仍未由本批解决。
