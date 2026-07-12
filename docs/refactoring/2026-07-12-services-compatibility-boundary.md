# Services Compatibility Boundary

## 本批结论

完成 concrete implementation 迁出后，本批把 `services` 从“混合兜底目录”正式收缩为可执行的 compatibility boundary：

- tracked Python 文件精确固定为：
  - `services/__init__.py`；
  - `services/retrieval/__init__.py`；
  - `services/user_profile.py`；
- 新增递归 `SERVICES_COMPATIBILITY_CONTRACT`；
- compatibility facade 禁止依赖 agents、API、bootstrap、composition、graph、main、runtime、tools；
- facade 只允许向 application/core/infrastructure 委托；
- bootstrap、composition、main 额外禁止 import services；
- core/application/runtime/graph/infrastructure/API/tools/agents 原有 contracts 已全部禁止反向 import services；
- production app 中 services inbound import 当前为零。

这不是把 services 升格为长期架构层，而是阻止临时兼容代码重新长成第二套实现。

## 当前只允许的 Facade

### Retrieval package facade

`services/retrieval/__init__.py` re-export：

- application 的 `SearchQuery / SearchResult / RetrievalMode / MetadataFilter`；
- infrastructure 的 `HybridRetriever`。

它不定义新 class，不包含 BM25/filter/metadata/ranker 深层模块，且 identity tests 证明 re-export 与事实源是同一个 Python object。

### User Profile facade

`services/user_profile.py` 保留：

- 旧 `UserProfileService(Settings, memory_store=...)` constructor；
- `get_user_profile / update_user_profile / summary` 自由函数；
- legacy dict payload projection 与默认常量。

它立即构造/委托 application `UserProfileService` 与 infrastructure `JsonUserProfileRepository`。Production resources、tools 和 API 不经过该 facade。

### Root package init

`services/__init__.py` 为空，只用于保留现有 Python package namespace，不执行 eager import 或 wiring。

## 九组真实 Architecture Contracts

当前 source contracts 为：

1. core；
2. application；
3. runtime；
4. graph；
5. infrastructure；
6. API delivery；
7. tools；
8. agents；
9. services compatibility。

Services contract 不是一个“层级顺序”规则。兼容 adapter 天然需要从旧 API 委托到新 application/infrastructure，因此允许向内/具体事实源 import；真正约束是：

- 文件集合不能增长；
- 不能依赖 delivery/orchestration/runtime/composition；
- production 不能反向经过 facade；
- facade 不得复制实现或类型。

## 实施中遇到的问题

### 问题 A：只看 import direction 不能阻止新增独立文件

开发者可以在 services 新建一个只依赖 core 的 provider/store；它不会违反 compatibility dependency contract，但会让兜底目录重新增长。

处理：增加精确 `.py` 文件集合测试。任何新增 service module 即使 import 方向“合法”，也必须先明确归属或显式修改 compatibility policy。

### 问题 B：只约束普通 layers 会漏掉 composition roots

Bootstrap/composition 被有意排除普通向内层 contract，因为它们需要组装 concrete implementations。这也意味着它们可能重新 import compatibility services 而不触发现有八组规则。

处理：对 `bootstrap.py / composition.py / main.py` 增加精确 services forbidden import 检查；composition roots 可以 import infrastructure/agents/runtime，但不能把 services facade 当事实源。

### 问题 C：Compatibility facade 合法依赖 infrastructure

若机械套用“services 不得依赖 infrastructure”，现有 profile/retrieval facade 必须复制实现或增加逐文件 allowlist，反而更差。

处理：定义符合真实用途的 contract：允许 application/core/infrastructure，禁止外部 delivery/orchestration/runtime。没有把所有目录强排成虚假全序。

### 问题 D：旧磁盘目录仍有 ignored cache

此前迁移后，`services/tools`、`services/vectordb` 等目录可能因 `__pycache__` 暂时存在。用目录 existence 作为“源码已清理”证据会被本机运行历史干扰。

处理：file-set gate 只统计 `.py` source，Git 审计验证 tracked 文件；ignored `.pyc` 不参与 architecture truth。

### 问题 E：不能立即删除有明确兼容承诺的两个 facade

User-profile 重构记录明确保留仓外旧 constructor/free functions，retrieval contract 记录也明确保留 package facade。为了追求 services 目录为零而直接删除会违反已记录迁移策略。

处理：保留最小 facade 并设置不可增长门禁；删除由仓外使用审计或明确 deprecation 周期触发，不与 concrete implementation 清理混在一起。

## 验证范围

定向验证覆盖九组 architecture contracts、services 精确文件集合、composition roots 禁止回引、retrieval object identity、user-profile legacy constructor/free functions、tenant/profile tools compatibility。

| 验证 | 结果 |
|---|---|
| architecture/retrieval-facade/profile-facade targeted pytest | 48 passed；4 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 706 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 150 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 删除 Facade 的前置条件

- 搜索并确认仓外脚本/部署代码不再 import 对应旧路径，或提供明确 deprecation release；
- retrieval 调用方改用 application contract 或 infrastructure concrete path；
- user-profile 旧调用方改用 injected application service/repository，不再依赖 Settings constructor 与自由函数；
- 删除对应 compatibility tests/file-set entry；
- 最后删除空 services namespace，并把 architecture contracts 中的 services 前缀从“compatibility target”转为全局 forbidden package。

在这些条件完成前，services 保持最小、只读式兼容角色，不接收新实现。
