# Phase 0 重构日志：统一原子 JSON 持久化

## 1. 重构范围

新增 `app/infrastructure/persistence/atomic_json.py`，将以下组件统一到同一个 JSON 读写 adapter：

- LearningStore
- MemoryStore
- User Profile
- WebSearchBackend 的 Tavily usage state

原来前三个组件直接用 `open(..., "w") + json.dump` 覆盖目标文件；WebSearch 单独维护固定 `.json.tmp` 的原子写实现。

## 2. 实施方案

`write_json_atomic(path, value)` 的提交顺序：

1. 在目标文件同目录创建唯一临时文件。
2. JSON 序列化写入临时文件。
3. `flush + fsync` 确保文件内容提交给操作系统。
4. 关闭临时文件后调用 `os.replace` 原子替换目标。
5. 序列化或 replace 失败时删除临时文件，保留原目标文件。

临时文件必须与目标位于同一目录，否则跨文件系统 replace 不一定具备原子语义。

## 3. 实际遇到的问题

### 问题 A：现有“正确示例”仍有并发命名冲突

WebSearch 已经使用 tmp + replace，但临时路径固定为 `tavily_usage.json.tmp`。两个并发 writer 可能同时操作同一个临时文件。

解决：使用 `NamedTemporaryFile(delete=False, dir=target.parent)` 生成同目录唯一文件，而不是复制固定 tmp 文件名。

### 问题 B：异常清理不能只覆盖 replace

`json.dump` 也可能因为值不可序列化而在临时文件中途失败。如果 cleanup 只写在 replace 附近，会遗留半成品 tmp 文件。

解决：整个 serialize/flush/replace 过程放在 `try/finally`，只要尚未成功完成 replace，就删除临时文件。

### 问题 C：原子文件替换不等于事务

该 adapter 保证单个文件不会因中断变成半截 JSON，但不能阻止两个进程同时完成“读取旧值 -> 分别修改 -> 后写覆盖先写”的 lost update，也不能让 learning + memory 两个文件形成同一事务。

处理：本批不虚构多进程安全结论。process lock、repository transaction、组合写幂等仍保留在后续 TODO。

## 4. 新增故障注入测试

- 正常创建父目录并替换已有文件。
- JSON 序列化失败时旧文件保持不变，临时文件被清理。
- `os.replace` 失败时旧文件保持不变，临时文件被清理。

## 5. 验证结果

| 检查 | 结果 |
|---|---|
| atomic/store/profile/resources 定向测试 | 25 passed |
| 全量 pytest | 137 passed，3 个第三方 deprecation warnings |
| 全仓 ruff | passed |
| 扩展 mypy（18 source files） | passed |

本批提交主题：`refactor: centralize atomic json persistence`。

## 6. 后续边界

- LearningStore/MemoryStore 的读改写锁与 multi-worker 策略。
- `upsert_learning_state` 的 learning + memory 原子事务和 idempotency key。
- FAISS index/documents/chunk metadata 的 generation manifest。
- durable approval repository。
