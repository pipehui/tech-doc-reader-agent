# 前端 REST Client、运行时响应契约与错误边界

## 本批目标

原 `frontend/src/api.ts` 同时负责 URL、tenant query/header、fetch、HTTP error 和三个 endpoint，并通过 `response.json() as Promise<T>` 直接信任网络数据。TypeScript 类型只约束调用方编译期，不会在后端字段缺失、类型漂移或返回非 JSON 时保护 Store。

本批完成前端 TODO F4 剩余部分：transport、endpoint 和 response contract 分层；所有 state/history/learning 成功响应在进入 session use case 前做运行时校验；FastAPI/代理错误统一映射为稳定且安全的消息。

## 最终结构

```text
frontend/src/shared/api/
├── client.ts              # URL/header/fetch/error/decoder boundary
├── client.test.ts
├── contracts.ts           # state/history/learning runtime decoders
├── contracts.test.ts
├── sessionApi.ts          # endpoint paths + decoder binding
└── sessionApi.test.ts
```

根 `frontend/src/api.ts` 与旧测试已删除。调用方现在只依赖所需层：

- SSE transport 从 `shared/api/client` 取 base URL/tenant headers；
- session bootstrap、manual refresh 和 stream completion 从 `sessionApi` 取 endpoint adapters；
- feature/use case 不直接调用 browser fetch。

## 实际改动

### 1. 可注入的 JSON transport

`createJsonClient` 接收 `baseUrl` 与 `fetchImpl`，对每个请求统一：

- tenant normalization；
- query `user_id/namespace`；
- `x-user-id/x-namespace` headers；
- Accept header；
- AbortSignal；
- JSON parse；
- response decoder。

默认 browser fetch 使用动态 wrapper，而不是在模块初始化时捕获 `globalThis.fetch`，因此测试 stub、未来 polyfill 或运行时 instrumentation 都能生效。

### 2. 结构化 HTTP error

新增 `HttpError`，保留 status 与原始 payload。错误消息优先级为：

1. JSON `message`；
2. FastAPI string `detail`；
3. FastAPI validation `detail[].msg` 聚合；
4. JSON `error`；
5. 短纯文本；
6. HTTP status text。

HTML/doctype/body 错误页和超过 500 字符的非 JSON body 不进入消息，防止反向代理页面、stack trace 或大响应污染 chat transcript。

### 3. 成功响应运行时校验

`contracts.ts` 为三个 REST response 实现 decoder：

- `decodeSessionState`；
- `decodeHistoryResponse`（含 HistoryItem 与 role enum）；
- `decodeLearningOverview`（含 LearningRecord）。

decoder 检查 object/array、string/nullable string、boolean、finite number、integer 和 message role。返回值由 decoder 构造，因此 `Promise<SessionState>` 等类型来自已经验证过的数据，不再使用泛型 cast 假装网络 payload 安全。

JSON 解析失败或 decoder 失败统一包装为带 endpoint path 的 `ApiContractError`，与 HTTP failure 可区分。

### 4. Endpoint adapter 只绑定路径与 decoder

`createSessionApi(JsonClient)` 负责：

- session ID path encoding；
- state/history/learning endpoint；
- history `include_tools=true`；
- decoder 选择。

它不理解 Store，也不处理 UI error；测试可注入 fake client，session use case 仍可注入 fake API port。

### 5. 后端 schema 与跨语言漂移门禁

后端 `HistoryMessage.role` / `HistoryViewItem.role` 从无约束 `str` 收紧为 `Literal["user", "assistant", "system", "tool"]`，OpenAPI/Pydantic response schema 与前端 decoder 使用同一枚举语义。

新增 Python contract test，固定五个 Pydantic response/nested model 的字段集合与 history role enum。后端增删/改字段时 CI 会先失败，要求同步更新前端 runtime decoder 和 fixture，而不是等线上数据进入 Store 才暴露。

Architecture gate 还固定：

- 根 `api.ts` 不得恢复；
- shared API 目录文件集合；
- 三个 decoder 必须被 endpoint adapter 绑定；
- 非测试前端源码只有 shared client 可以直接调用 `fetch`；
- 旧 `./api` / `../../api` import 禁止重新出现。

## 实施中遇到的问题

### 问题 A：编译期泛型不等于网络类型安全

`fetchJson<SessionState>()` 看似有类型，实际 `response.json() as Promise<T>` 没有任何检查。后端若把 `message_count` 变成字符串，TypeScript 仍会把它当 number 传入 Store。

处理：JsonClient 不接受裸泛型承诺，调用必须提供 decoder；decoder 成功构造的值才获得业务类型。

### 问题 B：FastAPI 的错误形状不止一种

资源未初始化是 `{"detail": "..."}`，validation error 是 `{"detail": [{"msg": ...}]}`，guardrail 使用 `message/error`，代理还可能返回 plain text。

处理：错误 payload 解析集中在 transport，按明确优先级映射并保留结构化原值。feature 不再重复猜测 error shape。

### 问题 C：文本 fallback 会把 HTML 页面写进 transcript

初版映射把所有非 JSON text 当用户消息。production preview/反向代理错误可能返回整页 HTML，这会产生数 KB system message，甚至把不可信 markup 原样保存到 local transcript。

处理：只接受不含 HTML 标记且不超过 500 字符的短文本；其余回退 status text。浏览器 smoke 明确验证页面显示 `会话恢复失败：500 Internal Server Error` 且 snapshot 中没有 doctype/html。

### 问题 D：默认 fetch 在模块初始化时捕获会破坏替换能力

若 singleton client 直接保存当时的 `globalThis.fetch`，测试在 import 后 stub fetch 不生效，运行时后装 instrumentation 也无法拦截。

处理：默认 `browserFetch` 每次调用时解析 `globalThis.fetch`；显式依赖仍可通过 `createJsonClient({ fetchImpl })` 注入。

### 问题 E：前后端 schema 各自维护仍可能漂移

运行时 decoder 能安全失败，但若 CI 不关联后端 schema，字段改动只能在运行时首次请求发现。

处理：增加 Pydantic field/enum contract gate。当前没有引入 codegen 工具链，保持轻量；未来若 API 扩大，可用 OpenAPI codegen 替换显式 field gate。

## 测试覆盖

新增 7 个前端 test cases，覆盖以下 8 类场景：

- tenant URL/header/signal/decoder；
- FastAPI detail/payload；
- validation messages；
- plain text 与 HTML/超长 body fallback；
- invalid JSON 与 decoder failure；
- state/history/learning valid fixtures；
- missing/unsupported/nested invalid fields；
- endpoint path encoding 与 decoder binding。

新增 2 个 Python contract tests：backend response field sets、history role enum。

## 验证结果

| 验证 | 结果 |
|---|---|
| `npm run check` | passed |
| `npm test` | 16 files，66 tests passed |
| `npm run build` | passed，2040 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| frontend architecture + REST contract focused pytest | 13 passed |
| 全量后端 pytest | 249 passed，4 warnings |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，10 source files |
| in-app browser production preview | Studio rendered；HTML 500 body 映射为简洁 status；无 HTML 泄漏；console warning/error 0 |
| `git diff --check` | passed |

浏览器 tab 与 4173 preview 已清理。pytest warning 仍为既有第三方弃用和本机 `.pytest_cache` 权限提示。

## 保持不变与后续工作

保持不变：endpoint、query/header identity、AbortSignal 行为、session use-case ports、Store 类型和调用流程。

有意变化：非 2xx 显示后端具体 detail/message；成功但不符合契约的响应不再进入 Store，而是产生可定位到 endpoint 的 contract error。

后续继续：

- tool result SSE payload 增加显式 status/error；
- component/integration tests 覆盖审批与完整 fake SSE；
- API 范围扩大时评估 OpenAPI codegen，替代显式字段漂移门禁。
