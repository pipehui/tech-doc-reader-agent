# Phase 4 重构日志：Learning Command、Ports、UoW 与 Service 边界

## 1. 重构范围

`application/learning_state.py` 原先同时定义四类变化原因不同的对象：

- update command/result、校验、幂等 key/fingerprint 与 result JSON contract；
- Learning/Memory reader、command、updater capability ports；
- snapshot、repository port、进程内锁与原子 commit Unit of Work；
- learning + memory 组合 mutation service。

这使不同层都依赖同一个厚模块：tool 只构造 command，API/profile 只消费 reader port，persistence 只消费 snapshot/UoW，composition 才需要 service。任意一类类型变化都会扩大 import fan-in 与审查范围。

本批按所有权拆为：

```text
application/
├── learning_models.py        # LearningRecord / MemoryFragment domain models
├── learning_commands.py      # Update command/result + idempotency/JSON contract
├── learning_ports.py         # reader/command/updater capability Protocols
├── learning_unit_of_work.py  # snapshot/repository port/lock/commit boundary
└── learning_state.py         # LearningStateService mutation use case only
```

没有保留从 `learning_state.py` re-export 旧符号的兼容 facade；所有仓内调用方在同一批迁到真实所有者。外部 API、tool schema、JSON snapshot schema、generation、幂等 key/fingerprint、异常 code 和 mutation 行为保持不变。

## 2. 调用方依赖收窄

| Consumer | 重构后依赖 |
|---|---|
| `tools/learning.py` | `learning_commands.UpdateLearningStateCommand` |
| `tools/dependencies.py` | `learning_ports` capability Protocols |
| Learning API / profile service | reader ports only |
| Learning/Memory persistence adapters | `learning_unit_of_work.LearningStateUnitOfWork` |
| Snapshot repository / legacy migration | snapshot UoW contract；result JSON contract 来自 commands |
| concrete resource composition | `LearningStateService` + `LearningStateUnitOfWork` |

`learning_state.py` 现在只公开 `LearningStateService`。Architecture test 递归解析 app imports，禁止任何 app module 再从该文件 import 除 service 以外的符号；这比依赖人工约定更能阻止厚模块回归。

## 3. 实际遇到的问题与解决

### 问题 A：RepositoryPort 不能机械归入通用 ports

最初按名字容易把 `LearningStateRepositoryPort` 放进 `learning_ports.py`。但 repository method 的入参与返回值都是 `LearningStateSnapshot`；若 snapshot 归 UoW，而 UoW 又 import repository port，会形成 `learning_ports -> learning_unit_of_work -> learning_ports` 循环。

解决：区分两类 port：

- 跨 tools/API/profile consumer 的 reader/command/updater capability 放在 `learning_ports.py`；
- 只服务事务边界的 snapshot repository port 与 snapshot、UoW 共置于 `learning_unit_of_work.py`。

这不是按技术名词统一堆放，而是按协作对象和变化原因归属。

### 问题 B：Result 同时是 service 输出和持久化 JSON contract

`UpdateLearningStateResult` 不只由 service 返回，snapshot repository 还用它校验 processed-command result payload。若把它留在 service，会让 persistence adapter 为 JSON contract 依赖 mutation implementation；若放入 UoW，又会把 command/result 拆成两个事实源。

解决：command 与 result 共置 `learning_commands.py`。UoW 和 service 都依赖这组稳定 execution contract，repository 只为 result payload validation import 同一类型。

### 问题 C：旧架构测试锁定的是文件位置，不是职责

既有测试通过搜索 `learning_state.py` 中的 Protocol 文本证明 ports 在 application。拆分后若只改字符串路径，仍无法阻止 service 文件重新 re-export 所有类型。

解决：测试分别验证 commands、ports、UoW、service 的唯一 class owner，并用 AST 扫描所有 app module 对 `application.learning_state` 的 import；只有 `LearningStateService` 被允许。

### 问题 D：类 module path 变化是否影响持久化

Command、result 和 snapshot 的 Python module path 发生变化。审计持久化代码后确认数据通过显式 dict/JSON schema 与 `from_payload` 恢复，不使用 pickle 或类全限定名，因此无需数据 migration。

定向测试覆盖 legacy migration、snapshot repository、故障注入、幂等 replay/conflict、Learning/Memory store 与 tool/API projection，验证 JSON 兼容结论。

## 4. 保持不变的事务语义

- mutation 在 candidate snapshot 上同时准备 learning record 与 memory fragment；
- repository 成功发布 generation 后才替换 active snapshot；
- 失败时上一版本保持可读；
- 相同 tenant/session/tool-call + 相同 fingerprint 返回 replay result；
- 相同 idempotency key + 不同 fingerprint 仍抛 `learning_idempotency_conflict`；
- processed command owner、completion time 与 result payload 结构不变；
- UoW lock 仍只承诺单进程互斥，不虚构 multi-worker 安全性。

## 5. 验证结果

| 检查 | 结果 |
|---|---|
| Learning/Memory/repository/migration/tool/API/architecture 定向测试 | 109 passed，4 warnings |
| 相关 application/API/tools/infrastructure mypy | passed，32 个 source files |
| 全量 pytest | 715 passed，4 warnings |
| 全仓 Ruff | passed |
| app + evals mypy | passed，156 个 source files |
| 前端 Vitest | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | passed，2042 modules transformed |
| npm audit | 0 vulnerabilities |

四条 pytest warning 中三条是 LangGraph/LangChain/Starlette 第三方弃用提示，另一条是本机 `.pytest_cache` 无写权限；测试用例自身全部通过。

## 6. 后续约束

- 新 reader/command/updater consumer contract 放入 `learning_ports.py`，不要放回 service。
- Snapshot schema、repository transaction contract 和 commit mechanics 在 `learning_unit_of_work.py` 演进；adapter-specific JSON/generation 实现仍属于 infrastructure。
- Tool/API 不得直接依赖 UoW 或 concrete store 写方法。
- 多进程 writer、retention/GC 与真实数据库事务仍是独立能力，不因文件拆分而宣称完成。
- 若确有仓外 Python type import 兼容需求，应独立发布 versioned public package；不要让 application service 文件重新承担隐式 facade。

本批提交主题：`refactor: split learning application contracts`。
