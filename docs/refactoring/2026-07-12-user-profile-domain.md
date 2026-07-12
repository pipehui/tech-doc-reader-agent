# User Profile 领域模型、应用服务与 versioned repository

## 本批目标

原 `services/user_profile.py` 约 350 行，同时承担默认值、payload 归一化、画像更新规则、topic 去重、摘要格式、memory
查询、tenant 路径编码、legacy 路径选择、JSON 读写、service facade 和兼容自由函数。Profile 从文件到 tool/API 全程传递
`dict[str, Any]`，任何一层都需要知道 `experience_level / known_topics / status` 等字符串 key。

本批完成 D7 的 profile 子范围：建立不可变 `UserProfile / UserProfileUpdate / UserProfileUpdateResult`，把业务更新规则
放进领域模型；提取无 adapter 依赖的 application service 与 repository/memory ports；增加 versioned JSON repository；
让 composition、tool 与 API 使用 typed 主路径。原自由函数保留为 dict compatibility facade。

## 最终边界

### 1. 画像状态与更新规则由领域模型拥有

`application/profile_models.py` 定义：

- `UserProfile`：tenant、默认偏好、known/weak topic tuple、备注、更新证据/时间和 profile version；
- `UserProfileUpdate`：trim 后的可选文本与去重 topic tuple；
- `UserProfileUpdateResult`：不可变 profile 与显式 `updated | unchanged` status。

`UserProfile.from_payload()` 是 legacy payload 入口；`to_payload()` 是 JSON/tool/API 输出 adapter。`apply()` 返回新对象，
不原地修改旧画像，并集中保持以下既有规则：

- 空字符串不清空已有偏好；
- known topics 追加去重，并自动从 weak topics 移除；
- resolved weak topics 按 casefold key 移除；
- evidence 非空即视为一次明确更新并刷新 `updated_at`，即使文本与上次相同；
- 无有效变化返回同一 profile 对象与 `unchanged`，application service 不写磁盘。

topic 与输出 list 使用 tuple/copy 隔离；调用方修改 `to_payload()` 返回的 list 不会改变领域对象。

### 2. Application service 只编排 ports

`application/profile_service.py` 定义 `UserProfileRepositoryPort` 与 typed `ProfileMemoryReaderPort`。service 负责：

```text
strict tenant parse
  -> repository.get(UserProfile)
  -> UserProfile.apply(UserProfileUpdate)
  -> changed 时 repository.save
```

用户上下文摘要读取 `MemoryFragment`，直接访问 `kind/topic/content`，不再依赖 memory dict key。摘要格式作为纯函数与
profile service 同层，repository 不了解中文展示文案。

application dependency gate 会扫描新文件，禁止其 import infrastructure/services/tools/api。

### 3. JSON 路径与存储 schema 收敛到 infrastructure

`JsonUserProfileRepository` 独占：tenant path percent-encoding、default namespace 的旧 `{user_id}.json` fallback、原子写和
storage envelope。新写入格式为：

```json
{
  "schema_version": 1,
  "profile": {
    "profile_version": 1,
    "user_id": "user-a",
    "experience_level": "进阶"
  }
}
```

`namespace` 仍由路径表达，不重复写入 payload；`status` 只属于 update result，永不落盘。旧 flat JSON（包括当前路径与
默认 namespace legacy 路径）继续读取，但 read/startup 不会重写文件；只有用户明确更新后才在新 tenant path 写 envelope。
这不是启动时隐式批量 migration。

未知 schema、缺失 profile object、非 object document 与非法 JSON 会稳定返回 `user_profile_corrupt`，避免把损坏数据
静默当作默认画像后又被后续更新覆盖。

### 4. Composition 与 delivery 使用 typed 主路径

`AppResources` 现在显式组合 `JsonUserProfileRepository -> application.UserProfileService`，不再依赖兼容 services facade。
`UserProfilePort` 返回 `UserProfile / UserProfileUpdateResult`；profile tools 和 Learning profile API 只在输出点调用
`to_payload()`。

`services/user_profile.py` 缩为兼容层：保留旧 Settings 构造器与 `get_user_profile / update_user_profile / summary` 自由函数，
但内部立即委托 application service。旧自由函数继续返回相同 dict schema；strict tenant validation 没有因兼容默认值
处理而退化。

### 5. Tool -> API tenant 闭环

新增真实 adapter 的端到端测试：在 `(user-a, namespace-a)` trace context 下调用 `update_user_profile` tool，再通过
`GET /learning/profile` 分别读取 namespace-a 与 namespace-b。前者看到更新，后者仍是默认画像。D0 中缺失的
API/profile tool tenant 闭环因此完成。

## 实施中遇到的问题

### 问题 A：服务拆分容易顺手改变更新语义

第一眼看，evidence 与旧值相同时似乎可以判为 unchanged；也可能允许空字符串清空 notes。但原实现只要收到非空
evidence 就会刷新 `updated_at`，且所有空文本都被忽略。这些行为可能参与 agent 的“用户明确要求更新”审计。

处理：先固定原测试与代码分支，再把相同规则逐项搬进 `UserProfile.apply()`；新增专门测试证明相同 evidence 仍是一次
update，无参数则是 unchanged 且 repository 不保存。

### 问题 B：profile version 不等于 storage schema version

原 payload 有 `profile_version`，但它描述画像业务结构，文件本身没有 envelope；无法区分未来 repository metadata 与
profile fields，也无法拒绝未知存储版本。

处理：保留 API 的 `profile_version`，另加 storage-level `schema_version`。repository 同时支持旧 flat payload 和新
envelope；不在普通读取时写回，从而避免把兼容读取伪装成未经确认的批量 migration。

### 问题 C：旧 JSONDecodeError catch 已经失效

旧 `_load_user_profile()` 捕获 `JSONDecodeError/OSError`，但统一 `read_json()` 已经把底层异常映射为 `ApplicationError`，
所以该 catch 实际不会执行。继续照搬会让代码看起来有容错，运行时却走另一条错误模型。

处理：repository 识别 mapped error 的 `cause_type=JSONDecodeError` 并转成稳定 `user_profile_corrupt / InvalidProfileJson`；
permission/IO dependency failure 不吞掉。非法 JSON、非 object 与错误 schema 都有独立测试。

### 问题 D：兼容 facade 不能重新成为 production 依赖

保留 `services.user_profile.UserProfileService(Settings)` 能减少仓外破坏，但如果 `AppResources` 继续构造它，Settings 与
repository 选择仍会藏在 service 内部，新的 application port 只是表面分层。

处理：production composition 直接注入 repository；兼容 constructor 只供旧调用方/测试使用。架构测试明确禁止
`services/resources.py` 重新 import `services.user_profile`。

### 问题 E：Profile 摘要仍会把 memory 拉回裸 dict

learning/memory 上一批已经有 typed query，但旧 profile summary 仍调用 `read_by_query/read_recent`，使 dict facade 反向
进入 application 用例。

处理：ProfileMemoryReaderPort 只允许 `query_memories/recent_memories -> MemoryFragment`；真实 MemoryStore 与测试 fake
统一走 typed path。

## 测试与门禁

新增/扩展覆盖：

- legacy payload 默认值、topic 去重和 payload copy；
- immutable update、known/weak/resolved reconciliation、evidence 与 unchanged 语义；
- application service unchanged 不 save；
- versioned envelope round-trip，namespace/status 不落 payload；
- flat legacy read 不自动重写；
- 非法 JSON、非 object、未知 schema 与错误 profile object；
- strict tenant compatibility facade；
- typed memory context summary；
- tool JSON/API response 兼容与跨 namespace 端到端隔离；
- application 依赖方向、composition 与 delivery typed path 架构门禁。

| 验证 | 结果 |
|---|---|
| profile/repository/tool/API/resources/architecture 聚焦 pytest | 60 passed |
| 全量后端 pytest（禁用本机不可写 cache） | 411 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy | passed，8 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式变化，因此不重复浏览器视觉 smoke。三条 pytest warning 仍来自 LangGraph/Starlette 的既有
弃用提示。

## 保持不变与后续工作

保持不变：默认画像文案、summary 文本、topic merge/remove 规则、evidence/status/updated_at 行为、tenant path 编码、
legacy 只对 default namespace 可见、tool JSON 与 HTTP response schema。新 storage envelope 是显式更新后的持久化格式，
旧文件仍可读且普通读取不改盘。

D7 的 domain-model 子项还剩 approval 的分层归位：`GuardrailApprovalRequest` 已是不可变模型，但仍定义在 runtime，导致
Redis infrastructure 反向 import runtime。后续应移到 application/domain 并把 repository port 一并归位。migration CLI
的 dry-run/backup/summary、跨 JSON/未来 SQLite 的 repository contract suite、retention/备份策略仍未完成。
