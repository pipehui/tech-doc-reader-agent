# 可复用 repository contract suites 与 InMemory approval adapter 归位

## 本批目标

LearningState、UserProfile 和 Approval 已有 application ports 与当前 adapter，但测试仍按实现文件分散：JSON 测试关心路径/
manifest，Redis 测试关心 TTL/envelope，进程内 approval 则只在 runtime service 测试中间接覆盖。未来增加 SQLite 时，
如果复制现有测试，很容易把 JSON 路径或 Redis TTL 当成通用 repository 语义，也可能漏掉 overwrite、detached result、
atomic pop 等领域不变量。

本批完成 D7 的 repository contract 项：建立三个可继承的 contract suites，当前 JSON/InMemory/Redis adapter 通过 factory
接入；未来 SQLite 只需新增 concrete test class。与此同时将 `InMemoryApprovalRepository` 从 runtime 移到
infrastructure/persistence，让 runtime 只保留审批执行 service。

## 最终边界

### 1. Contract suite 与 concrete adapter 分开

`tests/contracts/repositories.py` 定义三个不以 `Test` 开头的可复用 suite：

- `LearningStateRepositoryContract`；
- `UserProfileRepositoryContract`；
- `ApprovalRepositoryContract`。

suite 只依赖 application port/domain model；`tests/test_repository_contracts.py` 才导入 concrete adapter，并通过 pytest
factory fixture 绑定 backing store。未来 SQLite 的接入形态为：

```python
class TestSqliteUserProfileRepositoryContract(UserProfileRepositoryContract):
    @pytest.fixture
    def profile_repository_factory(self, tmp_path):
        return lambda: SqliteUserProfileRepository(tmp_path / "state.db")
```

无需复制 contract 断言，也不需要修改已有 JSON test class。

### 2. LearningState contract

共享契约验证：

1. fresh repository `load()` 返回 `None`；
2. typed records/memories/processed command snapshot 能 round-trip；
3. `save()` 不修改调用方 candidate，返回已发布 generation；
4. 新 repository 实例读取同一 backing store 能取得相同状态；
5. 修改 `save()` 返回的 records/memories/nested command result 不污染持久化状态；
6. 第二次 save 发布不同 generation，之后 load 只返回 latest；
7. reviewed score/reviewtimes 与 idempotency result 被完整保留。

JSON-specific 的 manifest counts、generation 目录、故障注入、legacy migration 仍留在原测试；未来 SQLite 不需要制造
文件 manifest，但必须提供 port 所要求的 version/latest 语义。

### 3. UserProfile contract

共享契约验证：

- missing tenant 返回带该 tenant 的默认 profile；
- save 后由新 repository 实例读取仍相等；
- 同 user 不同 namespace 互不影响；
- `to_payload()` 返回 list 被修改不会污染 repository；
- overwrite 保留 known topics，更新 level/weak/evidence/updated_at。

JSON-specific 的 percent-encoded path、root legacy precedence、schema envelope 与损坏文件仍由 adapter 测试负责。

### 4. Approval contract 同时约束两个实现

同一 `ApprovalRepositoryContract` 当前运行两次：

- `InMemoryApprovalRepository`；
- `RedisApprovalRepository + FakeRedisBackend`。

契约验证 missing get、put/get round-trip、相同 key overwrite、不同 key 隔离、两个线程竞争 pop 只有一个获得 request、
后续 get/pop 均为空，以及 close 可安全调用。Redis 另有跨 repository/runtime GETDEL、TTL、envelope 和 transport error
测试，继续证明 durable/multi-worker 特性。

Contract 没有要求两个 InMemory repository 实例共享状态，因为该 adapter 明确定义为 process-local instance；跨 worker
共享属于 Redis/未来 durable adapter 的实现级能力。原子 one-shot pop 则是所有 approval repository 都必须满足的 port
语义。

### 5. InMemory adapter 不再属于 runtime

`InMemoryApprovalRepository` 移到 `infrastructure/persistence/in_memory_approval_repository.py`。它只 import application
approval model，runtime approvals 不再定义或导出 concrete persistence adapter。

`ChatRuntime` 的兼容默认构造仍创建 InMemory adapter；production bootstrap 仍显式注入 Redis。变化只涉及定义/import
位置，不改变默认运行行为。架构测试禁止 InMemory class 回到 runtime，并禁止两个 approval adapter import runtime。

共享 `FakeRedisBackend/FakeRedisClient/FailingRedisClient` 也从单个 Redis 测试提取到 `tests/fakes/redis.py`，避免 contract
测试再复制一套并发 fake。`tests` 增加 package marker，使 contracts/fakes 使用稳定绝对 import，不依赖 pytest 临时修改
模块搜索路径。

## 实施中遇到的问题

### 问题 A：所谓“通用 contract”很容易夹带 adapter 细节

把 manifest schema、Profile 文件 path 或 Redis TTL 放进共享 suite，会让未来 SQLite 为通过测试而伪造无意义概念；
反过来只测 save/load 又无法守住原子 pop、tenant isolation 和 detached state。

处理：以 application port 和调用方真正依赖的语义为边界。backing-store 格式、故障模式和 durability tier 继续在 concrete
adapter tests；round-trip/latest/isolation/atomicity 放共享 suite。

### 问题 B：Approval 的“跨实例共享”不是所有 adapter 的共同保证

Redis 必须跨 runtime/worker 共享，InMemory 的用途却是单进程测试和非生产 composition。若 contract 强制所有 factory
新实例共享，会迫使 InMemory 使用全局状态，重新引入测试耦合。

处理：共享 contract 对同一 repository 实例要求线程安全 one-shot pop；Redis-specific 测试继续使用两个 client/runtime
验证跨实例 GETDEL。未来 SQLite 是否属于 durable tier，应在 concrete test 另加多实例断言。

### 问题 C：返回对象相等不代表没有共享可变引用

repository save/load 的 happy-path equality 无法发现 adapter 把 snapshot list 或 nested processed command dict 直接缓存；
调用方之后 clear/mutate 会篡改 source of truth。

处理：Learning contract 主动清空返回 records/memories，并修改 nested result，再用新 repository load 验证磁盘状态。Profile
contract 修改 `to_payload()` 的 topic list后重载，验证 frozen model/copy 边界。

### 问题 D：把 InMemory adapter 留在 runtime 会让 contract 分类继续含糊

它实现的是 application repository port，却与 ApprovalService/AIMessage rejection logic 放在同一 runtime 文件。未来找
adapter、加 contract 或替换默认实现都必须进入执行层。

处理：移动到 infrastructure；runtime 只承担 key/use-case/graph update/telemetry。现有内部 import 全部一次迁移，不保留
runtime concrete-adapter re-export，避免旧层次继续成为事实 API。

## 测试与门禁

新增/扩展覆盖：

- InMemory 与 Redis 各运行 2 个 Approval contract tests；
- JSON LearningState 运行 fresh/round-trip/latest/detached contract；
- JSON UserProfile 运行 default/tenant/overwrite/detached contract；
- 共享 Redis fakes 继续通过原 TTL/error/cross-runtime tests；
- runtime/bootstrap/lifecycle 使用新 InMemory adapter 路径；
- approval adapter/runtime import 方向架构门禁。

| 验证 | 结果 |
|---|---|
| repository/approval/runtime/learning/profile 聚焦 pytest | 41 passed |
| 全量后端 pytest（frontend build 后串行） | 429 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，3 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式变化，因此不重复浏览器视觉 smoke。三条 pytest warning 仍来自 LangGraph/Starlette 的既有
弃用提示。

## 保持不变与后续工作

保持不变：Learning/Profile/Approval port signatures、JSON/Redis storage schema、Redis TTL/GETDEL、ChatRuntime 默认
InMemory 与 production Redis composition、tenant isolation、generation/latest 和 API/tool 行为。

D7 现在只剩 retention/删除/备份策略。Contract suite 不等于已经实现 SQLite；它为未来 adapter 提供可执行验收入口。
当 SQLite 出现时还需增加 transaction/concurrency/durability-specific tests，不能只继承共享 happy-path contract 就宣称
生产可用。

后续同日批次已完成本日志指出的职责分离：Approval repository use case 位于 application，LangGraph rejection projection 位于 runtime；repository contract 与 Redis adapter 行为不变。见 [2026-07-12-approval-service-application-boundary.md](2026-07-12-approval-service-application-boundary.md)。
