# 前端 Session Bootstrap 与 Topbar 边界拆分

## 本批目标

`App.tsx` 同时负责 URL/session/tenant 对齐、local transcript 恢复、三个 REST 请求、历史消息转换、顶部导航和 session selector。快速切换 session 或 tenant 时，旧实现只设置 `cancelled` 布尔值：旧结果不会写回，但请求本身仍继续占用连接，且这条约束没有独立测试。

本批保持页面、query 参数、Store action 和 transcript 优先级不变，将路由、会话恢复用例、React 协调 Hook 与 Topbar 分成明确边界，并补齐双层竞态保护。

## 实际改动

### 1. 路由模型离开根组件

新增 `app/routing.ts`，集中定义：

- `AppView` / `ExperienceView`；
- pathname 到 view 的容错映射；
- tenant-scoped experience URL 生成；
- GitHub URL 常量。

路由 helper 有独立 Vitest，不再只能通过渲染整个 `App.tsx` 间接验证。

### 2. 会话恢复成为纯用例

新增 `features/session/sessionBootstrap.ts`。`loadSessionContext` 只依赖两个窄 port：

- `SessionBootstrapApi`：state/history/learning 三个查询；
- `SessionBootstrapStore`：hydrate 与五个必要写 action。

它不 import React、router、API 实现或 Store singleton。历史响应到 `ChatMessage[]` 的转换也移入同文件，并允许注入 id/clock，因此可确定性测试 fallback message id、agent normalization 与时间戳。

当前 transcript source-of-truth 规则被明确固定：先尝试 tenant + session scoped local transcript；有可用缓存时保留 local messages/events/tool calls，只用服务端刷新 session state 与 learning；无缓存时才用 server history 创建 messages。该规则保持原行为，后续若改 merge policy 必须修改用例测试。

### 3. Hook 只协调 URL 和生命周期

新增 `features/session/useSessionBootstrap.ts`，顺序为：

1. URL 有明确 context 且与 Store 不同，执行一次 `resetForContext`；
2. URL 缺字段时补齐 session/user/namespace，完整对齐后才记入 session directory；
3. 只有 URL 与 Store 的三个 identity 字段完全一致时才启动 hydration；
4. effect cleanup 取消当前请求。

这样页面初次补 query 时不再发起一轮马上被导航取消的重复请求。

### 4. API 支持真实取消

`fetchJson` 及三个查询增加兼容的可选 `FetchJsonOptions`，把 `AbortSignal` 传给 browser `fetch`。既有调用不需要修改。

竞态采用两层防线：

- Hook cleanup 调用 `AbortController.abort()`，释放支持取消的网络请求；
- 纯用例在 `Promise.all` 返回后再次检查 `signal.aborted`，即便测试 fake 或未来 HTTP adapter 忽略 signal，旧 state/history/learning 仍不能写回新 context。

### 5. Topbar 成为独立 shell 组件

新增 `app/Topbar.tsx`，只负责全局导航、theme、session/tenant draft 与 context switch。它不加载服务端状态，也不调用 bootstrap Hook。`App.tsx` 不再包含 Topbar 实现、session hydration 或 history conversion，从 1208 行初始基线降到 967 个物理源码行（PowerShell 内容统计为 910 行；两种统计受换行处理影响）。

## 实施中遇到的问题

### 问题 A：AbortController 不是完整的竞态保证

只把 signal 传给 `fetch` 看似足够，但 fake、缓存 adapter 或未来替换的客户端可能忽略它。如果旧请求在 abort 后仍成功返回，直接写 Store 仍会串 session。

处理：把 stale-response 检查放在写边界之前，并用“API 完全忽略 abort、延迟成功返回”的测试证明任何 Store setter 都不会被调用。

### 问题 B：URL 默认 tenant 与缺失 tenant 不是同一状态

`tenantFromSearchParams` 会把缺字段归一为默认 tenant；如果 hydration 只比较归一结果，缺少 `user_id/namespace` 的 URL 会被误判为完整，导致 query normalization 与请求同时进行。

处理：增加 `isUrlContextReady`，要求原始 query 中 session/user/namespace 三项与 Store 精确一致。归一化仍用于 context switch，readiness 则检查完整性。

### 问题 C：路由测试最初使用了非法 tenant 字符

测试用 `user a`、`docs/zh` 验证 URL 编码，但 tenant value object 的合法字符集会主动回退到 `default/tech_docs`，测试失败不是路由 bug。

处理：测试改用合法但仍需编码的 `docs:zh`，同时保留 session slash 与 prompt 特殊字符的编码断言。测试尊重 domain normalization，不绕开它构造无效预期。

### 问题 D：production preview 没有后端代理

本地 4173 production preview 能验证 UI 和路由，但 session REST 请求返回 500 system message；这是未启动 backend/preview proxy 的环境结果，不是本批 UI crash。

处理：运行态验证聚焦本批边界：landing 渲染、Studio 跳转、query context、连续两次 session switch、输入框最终 identity 和 console warning/error。后端 contract 由 244 个 pytest 覆盖，不把 preview 的服务不可用误报为前端回归。

## 测试与门禁

新增 10 个 Vitest：

- route fallback 与 tenant-scoped URL 编码；
- API signal/header/query 透传与非 2xx error；
- history message conversion；
- 有/无 local transcript 的 hydration 规则；
- API 忽略 abort 时的 stale-response guard；
- 非取消错误映射；
- URL context 完整性。

Python architecture gate 增加三组约束：

- 根 App 必须委托 Topbar/session bootstrap，禁止重新出现 history/reset orchestration；
- 纯 bootstrap 用例禁止依赖 React/router/API concrete/Store singleton/browser storage；
- Hook 必须拥有 AbortController、URL sync 和三个查询，Topbar 不得反向加载 session state。

## 验证结果

| 验证 | 结果 |
|---|---|
| `npm run check` | passed |
| `npm test` | 11 files，52 tests passed |
| `npm run build` | passed，2025 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| frontend architecture focused pytest | 8 passed |
| 全量后端 pytest | 244 passed，4 warnings |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，10 source files |
| in-app browser production preview | landing/Studio rendered；连续 `smoke-race-a -> smoke-race-b` 后 URL/输入框均为 B；dark theme；console warning/error 0 |
| `git diff --check` | passed |

浏览器 tab 与 4173 preview 已清理。pytest warning 仍为既有第三方弃用和本机 `.pytest_cache` 权限提示。

## 保持不变与后续工作

保持不变：路由名称、query key、tenant normalization、Store 对外 action、local transcript 优先级、页面布局和 Topbar 操作。

后续继续：

- 把 Routes/app shell 抽成 `AppRouter`；
- 按 chat/approval/learner/inspector 拆剩余 28 个组件/helper；
- 将通用 JSON client 移入 `shared/api` 并统一 error payload；
- 引入 component/integration 测试覆盖真实审批和 fake SSE 全链路。
