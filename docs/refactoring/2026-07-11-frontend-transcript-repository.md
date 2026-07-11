# 前端 transcript repository 与安全存储端口

## 本批目标

`store.ts` 原来同时拥有 transcript key、版本、JSON 编解码、tenant 隔离和 `localStorage` CRUD。消息、事件或 tool call 每次变化都会由 Zustand action 直接写浏览器全局对象，无法替换为 fake；quota、SecurityError 或损坏的持久化数据也可能让 UI action 直接抛错。

本批先抽离 persistence 边界，为后续 transcript/session/trace slices 提供稳定端口，不在同一步重写全部 Zustand 状态。

## 实际改动

### 1. 建立最小 key-value storage port

新增 `storage/keyValueStorage.ts`，定义只包含 `getItem/setItem/removeItem` 的 `KeyValueStorage`。`readStorage`、`writeStorage` 和 `deleteStorage` 捕获浏览器存储异常，向可注入 handler 报告失败，并返回安全结果。

`resolveBrowserStorage` 把浏览器全局访问集中在这一处；全局对象不可用或访问 getter 本身抛出异常时使用 no-op fallback。因此 Store 模块可在无 DOM 环境加载，普通 UI action 也不会因为持久化不可用而崩溃。

### 2. transcript key/version/serialization 归属 repository

新增 `storage/transcriptRepository.ts`，独占：

- transcript prefix、当前 version 和 tenant/session key；
- snapshot 的 load/save/delete；
- malformed JSON、旧版本和缺失 collection 的处理；
- JSON 序列化失败及 storage read/write/delete 失败。

相同 `session_id` 在不同 user/namespace 下生成不同 key。读取当前版本但缺 collection 的 payload 时归一化为空数组/对象；格式错误或非当前版本返回 `null`，不把不兼容数据灌入 Store。

### 3. Store 改为显式 composition factory

新增 `createAppStore(dependencies)`，可以注入 storage、TranscriptRepository 和 failure handler；现有 `useAppStore` 仍由默认浏览器依赖创建，React 调用点无需变化。

Store 的 hydrate/persist/delete 只调用 repository，不再知道 transcript key 或 version。会话列表、context 和 theme 也通过安全 storage helpers 访问，`store.ts` 不再直接引用 `localStorage`。

会话列表只在 Store 创建时从 storage hydrate。之后 `rememberSession/deleteSession` 以当前内存 state 为 source of truth，再尝试持久化；即使连续写入都失败，本次页面生命周期内的 session list 仍保持完整。

### 4. 增加 persistence 单元与架构测试

新增 9 个前端测试，覆盖：

- 同 session/不同 tenant 的 round-trip 隔离；
- malformed JSON、旧 version、缺 collection；
- 精确删除目标 tenant transcript；
- storage read/write/delete 全部抛 SecurityError；
- tool args 循环引用导致 JSON 序列化失败；
- Store 使用注入的 fake repository 完成 hydrate/save/delete；
- 损坏 session/context/theme 的安全回退；
- 所有存储操作失败后，主题、消息和内存 session action 仍可用。

新增两个 Python architecture gates，禁止 `store.ts` 重新直接访问 `localStorage`，并固定 transcript load/save/delete 必须通过 repository。

## 实施中遇到的问题

### 问题 A：只抽 repository 仍无法替换生产 Store 依赖

如果在 `store.ts` 顶层创建固定 repository，repository 自身虽可单测，但 Zustand Store 仍无法注入 fake，验收只能靠源码搜索。

处理：把 Store 创建改为 `createAppStore(dependencies)` composition factory，再导出默认 hook。测试直接创建隔离的 Store 实例，不修改全局对象，也不会在测试之间共享状态。

### 问题 B：`localStorage` 不只是写入可能失败

浏览器策略、opaque origin 或隐私模式下，读取 `globalThis.localStorage`、`getItem`、`setItem` 和 `removeItem` 都可能分别抛异常。只在 transcript save 外围加 try/catch 不能覆盖模块初始化和会话删除。

处理：全局解析和三种操作都进入 storage adapter；Store 的 context/session/theme 同样使用这层。开发环境输出带 operation/key 的 warning，生产环境保持 UI 可用。

### 问题 C：合法 JSON 不代表合法 session list

旧 `safeJson<SessionEntry[]>` 只是 TypeScript cast。若持久化值是合法 JSON object，随后调用 `.flatMap` 仍会抛错。theme 也会把任意字符串 cast 成 `dark | light`。

处理：session list 先做 `Array.isArray`，每个 entry 再按字段窄化；context 先验证 record/session_id；theme 只有精确 `light` 才采用 light，其余回退 dark。

### 问题 D：持久化失败后重新读取会丢内存 session

旧 action 每次从 `localStorage` 重建 sessions。安全 adapter 返回空列表后，连续 remember 会让后一条覆盖前一条，UI 虽不崩溃但状态退化。

处理：初始化后以内存 `get().sessions` 为 action 输入，storage 仅作为持久化输出。跨 tab 同步若未来需要，应通过显式 `storage` event/use case 实现，而不是在每次 action 中隐式重读。

## 验证结果

| 验证 | 结果 |
|---|---|
| `npm run check` | passed |
| `npm run test` | 4 files，27 tests passed |
| `npm run build` | passed，2013 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| in-app browser production preview | landing rendered，theme dark -> light -> reload 后仍为 light |
| browser console warning/error | 0；验证后主题恢复 dark，preview 已停止 |
| 全量后端 pytest | 238 passed，4 warnings |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，10 source files |

4 条 pytest warning 与上一批相同：LangGraph/Starlette 第三方弃用提示，以及本机 `.pytest_cache` 写权限警告；没有测试失败。

## 保持不变与有意变化

保持不变：

- localStorage key 字符串、transcript version 2 和 payload shape；
- 默认浏览器应用仍导入同一个 `useAppStore`；
- tenant/session transcript 隔离、token event hydrate 过滤和写入时机；
- Store 对外 action 名称与 React 调用方式。

有意变化：

- storage 异常从 UI action throw 改为开发 warning + 内存继续运行；
- 损坏 session/context/theme 不再引发崩溃或非法主题；
- session action 初始化后以内存 Store 为 source of truth；
- transcript persistence 可替换为 in-memory fake。

## 后续工作

- 把 session list/context/theme 进一步抽为各自 repository，Store 只组合业务 actions；
- 将 Store 拆成 transcript、session、trace、learning 和 UI slices；
- 为未来 transcript version 增加显式 migration pipeline，而不只拒绝旧版本；
- 明确 storage 失败的用户提示策略和跨 tab 同步需求；
- Set/Map 等非 JSON 状态继续保持不持久化，后续 slice 中把该规则写成类型/测试。
