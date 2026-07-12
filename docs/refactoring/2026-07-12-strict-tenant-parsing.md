# Tenant 严格解析与 Legacy 兼容归一化

## 本批目标

原 `tenant_from_values()` 对缺失、空字符串、非法字符和非字符串使用同一种处理：回退 `default / tech_docs`。HTTP body 的 Pydantic schema 能拦住一部分非法值，但 header、query、RunnableConfig、ContextVar、后台调用和未来 JWT subject 不一定经过该 schema。非法 subject 静默变成默认租户会把请求路由到真实存在的 default 数据，而不是安全失败。

本批完成 D5：删除含糊入口，建立严格 `parse_tenant()` 与仅用于旧数据的 `normalize_tenant()`，并逐个迁移调用方。

## 最终边界

### 1. `TenantContext` 自身保持有效

严格规则与 API schema 使用同一个 `TENANT_ID_PATTERN`：

```text
^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$
```

- `None` 表示字段未提供，可以使用开发环境默认值；
- 显式空字符串、首尾空格、路径分隔符、非法字符和非字符串都返回 `ValidationError(code="invalid_tenant")`；
- `TenantContext` 的 `__post_init__` 执行同一检查，调用方不能通过直接构造 value object 绕过 parser；
- strict parser 不做 trim/coerce，避免输入在授权边界悄悄改变含义。

### 2. `parse_tenant` 用于所有活动身份路径

以下路径已迁移为严格解析：

- FastAPI body/query/header tenant resolution；
- `SessionConfigFactory` 生成 checkpoint thread key；
- chat/approval execution 与 session query；
- durable guardrail approval key；
- graph user-info node；
- learning/memory 的活动 read/upsert；
- user profile read/update；
- tool `RunnableConfig` / trace context。

`tenant_from_config()` 仍保留有语义的名称，但内部调用 strict parser。metadata 显式提供非法值时不会再退回一个有效 ContextVar；只有字段确实缺失时才按 `metadata -> ContextVar -> default` 取值。

### 3. `normalize_tenant` 只服务 legacy data

兼容归一化只在 LearningStore/MemoryStore 读取旧 records/memories 时使用：

- 旧数据没有 tenant：补 default；
- 旧数据含可安全转换的标量/首尾空格：按历史规则转换；
- 旧数据 tenant 已损坏：落到 default，以保持现有迁移行为。

该函数不再支持 `prefer_context`，避免授权代码借 compatibility helper 读取 ambient identity。活动请求传入同样的非法值会抛错；测试同时覆盖“旧记录可归一化、请求值必须拒绝”。

### 4. HTTP 只有“缺失”才默认

新增共享 `api/tenant.py::resolve_request_tenant()`，chat/session/learning routes 不再各写一套解析优先级：

1. body/query 参数只要不是 `None` 就优先；
2. 否则读取 `x-user-id` / `x-namespace`；
3. 两者均缺失才使用开发默认值；
4. 显式非法值返回稳定的 HTTP 422：

```json
{
  "detail": {
    "code": "invalid_tenant",
    "message": "The tenant identifier is invalid."
  }
}
```

合法显式 query/body 会覆盖 header；显式空值不会因为 Python `or` 被非法 header 或 default 掩盖。

## 实施中遇到的问题

### 问题 A：`or` 同时破坏优先级和严格性

原 route 使用 `user_id or header`，config 使用 `metadata.get(...) or context.get(...)`。空字符串等显式非法值会被当成“未提供”，然后退到另一个身份源；测试只能看到最终有效 tenant，无法发现输入被吞掉。

处理：身份源选择统一改成 `is not None`。是否缺失与值是否合法分两步判断；选中的显式值必须通过 strict parser。

### 问题 B：header/query 不共享 Pydantic body 约束

`ChatRequest` 对 body tenant 有 pattern，但 learning/session query 是普通 `str | None`，header 更不会自动套 Pydantic field constraint。只保留 schema 校验仍会留下旁路。

处理：所有 route 复用同一个 resolver；Pydantic 是第一层，core parser 是所有入口都必须经过的最终边界。

### 问题 C：全量严格会破坏旧数据迁移

旧 learning/memory JSON 可能没有 tenant，也可能含历史时期未校验的值。如果 normalization 也改为 strict，应用可能无法启动或读取旧默认数据。

处理：先按调用语义分类。只有 `_normalize_record/_normalize_memory` 使用 compatibility API；read/upsert 参数、runtime 和 profile 全部 strict。架构测试禁止 `tenant_from_values` 名称回归。

### 问题 D：value object 直接构造仍可绕过函数

即使所有当前调用都改用 parser，未来代码仍可能直接 `TenantContext(user_id="../x")`，随后把它交给 checkpoint、approval 或 path encoder。

处理：把不变量放进 `TenantContext.__post_init__`；parser 与直接构造具有相同失败语义。

### 问题 E：422 常量在依赖版本间命名漂移

首版使用 `HTTP_422_UNPROCESSABLE_ENTITY`，当前 Starlette 对该名称给出弃用 warning，推荐 `HTTP_422_UNPROCESSABLE_CONTENT`；但项目允许 FastAPI `>=0.115`，较早依赖组合未必提供新常量。

处理：HTTP 语义没有变化，直接使用稳定数值 `422`，既不产生当前 warning，也不提高最低依赖版本。

## 测试与门禁

新增/扩展测试覆盖：

- strict parser 对空、空格、路径字符、非字符串的拒绝；
- `TenantContext` 直接构造不允许绕过；
- compatibility normalizer 的 legacy coercion/default；
- config 显式非法 metadata 不回退有效 ContextVar；
- runtime config 在创建 thread key 前拒绝非法显式/ambient tenant；
- query/header 非法值返回 422，显式合法 query 优先于非法 header；
- learning/memory 旧记录可兼容归一化，但活动查询参数严格失败；
- 全 app 禁止 `tenant_from_values` 回归。

| 验证 | 结果 |
|---|---|
| tenant/API/store focused pytest | 33 passed |
| 全量后端 pytest（禁用本机不可写 cache） | 371 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，11 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式改动，因此不重复浏览器视觉 smoke。pytest 三条 warning 仍来自 LangGraph/Starlette 依赖弃用提示；新增 422 测试没有引入新的 deprecation warning。

## 保持不变与后续工作

保持不变：字段缺失时的本地开发默认租户、tenant 字符集、thread key 格式、profile 路径编码、旧 learning/memory 数据读取，以及当前 body/query/header 的 dev-mode tenant 输入方式。

本批不等于真实鉴权。D6 仍需：从可信 Authorization token 注入 subject、限制 body/query tenant、授权 namespace、验证跨用户访问拒绝。Strict parsing 只保证“非法身份不会静默变 default”，不证明“合法格式的身份有权访问该 tenant”。
