# 前端 Component、Router 与 Fake SSE Integration 测试层

## 本批目标

此前前端已有 reducer/repository/Store/use-case 纯单测和浏览器 smoke，但没有 React component test；`useChatStream` 仍硬编码 fetch-event-source、Store singleton、reducer dispatch 与结束刷新，完整 HITL 流只能依赖真实服务手工验证。

本批完成前端 TODO F5：建立 React 19 + Vitest 4 component 环境，覆盖指定组件、tenant/session router isolation，以及 send -> tool -> interrupt -> approve -> done 的无网络集成链。

## 依赖与测试环境

通过 npm registry 核对 React 19/Vitest 4/Node 22 兼容范围后，固定新增 dev dependencies：

- `@testing-library/react@16.3.2`；
- `@testing-library/dom@10.4.1`；
- `@types/react-dom@19.2.3`；
- `jsdom@29.1.1`。

只有 `.test.tsx` 文件用 `@vitest-environment jsdom`；原有纯 reducer/repository tests 继续使用快速 Node environment。测试依赖不进入 production bundle。

## 实际改动

### 1. Stream orchestration 可注入

新增 `streaming/chatStream.ts` 的 `createChatStream(dependencies)`，依赖均显式：

- `StreamTransport`；
- api base 与 tenant header builder；
- `getStore` port；
- refresh context use case；
- id factory 与 clock。

它负责 send/approve gate、SSE response 校验、typed parser/reducer/adapter、finish/refresh/error/finally。`useChatStream.ts` 收敛为 33 行 production composition：绑定 fetch-event-source、shared API、Zustand singleton 与真实 clock/id，然后返回同一个 chat stream facade。

这样集成测试直接传 fake transport 和 in-memory Store，不需要 mock 内部 module，也不会发 HTTP。

### 2. 四个 component tests

`features/componentBoundaries.test.tsx` 覆盖：

- ApprovalDrawer 选择最新 pending tool、显示 drawer、提交 reject feedback；
- PlanStepper 的 done/current/queued class 与内容；
- MessageBubble 的 Markdown、agent break 和显式 tool error 状态；
- InspectorToolbar 点击 `tool_result` filter 后通过 TraceSlice 更新 Set 与 active class。

测试通过 `useAppStore.setState` 用完整初始 state + 新 Set 重置 singleton；stream hook 在 component 边界被窄 mock，不依赖网络。

### 3. 同 session ID 的 router/tenant isolation

`app/sessionRouting.component.test.tsx` 使用 MemoryRouter、真实 `useSessionBootstrap`、真实 transcript repository 和 jsdom storage：

1. 给 `user-a/docs/shared-session` 保存 tenant-A cache；
2. 给 `user-b/docs/shared-session` 保存 tenant-B cache；
3. Store 初始指向 A，URL 明确指向 B；
4. mock server state/history/learning 返回 B；
5. 等待 bootstrap 后断言 identity 与消息均为 B cache。

同时断言 A cache 不出现、server history 不覆盖有效 B local cache、三个请求收到 B tenant 与 AbortSignal。

### 4. Fake SSE/HITL integration

`streaming/chatStream.integration.test.ts` 使用 `createAppStore` + MemoryStorage 和 fake StreamTransport。第一次 `/chat` 依次发：

```text
tool_call -> tool_result(success) -> interrupt_required
```

测试确认 user message、tool pending->done、trace events、pending interrupt 与“pending 时禁止再次 send”。随后 `/chat/approve` 发：

```text
agent_message -> done
```

最终确认 pending 清除、assistant message 完成、event 顺序、两次 refresh、URL/body/header 中 session + tenant + approval 数据完整。

### 5. 测试层进入架构门禁

Python gate 固定：

- component test 依赖与 `vitest run` script；
- 四个指定 component 名必须出现在 component suite；
- fake integration 必须含 tool_call/tool_result/interrupt/agent_message/done 与 approve；
- router suite 必须同时包含 tenant-A、tenant-B 和 server-history-not-win fixtures；
- `chatStream.ts` 禁止 import Store singleton；
- `useChatStream.ts` 少于 50 行，只做依赖组装。

## 实施中遇到的问题

### 问题 A：直接 mock fetch-event-source 仍把 Store singleton 锁进测试

初始方案是在 Vitest mock `@microsoft/fetch-event-source` 和 REST module 后调用 `useChatStream()`。这能跑通，但 orchestration 仍不能复用/独立构造，测试与模块路径、singleton 初始化顺序强耦合。

处理：先提取 `createChatStream`，通过 ports 注入 transport/store/refresh/clock/id。测试的是完整生产 orchestration，mock 只剩真正的外部边界。

### 问题 B：component test 必须重置带 action 的 Zustand state

只设置 messages/session 会残留前一测试的 filters、expanded Set 或 running flag；直接构造纯 data 又会丢失 Store actions。

处理：以 `getInitialState()` 为基底整体 replace，clone `filters/expandedToolIds`，再覆盖本测试数据。action 实例保留，mutable collection 不共享。

### 问题 C：router isolation 若只 mock hydrate，无法证明 repository key

只断言 URL 触发 `resetForContext(user-b)`，不能证明相同 session ID 下实际读取了 B cache，也不能证明 server history 没覆盖 cache。

处理：jsdom 中用真实 TranscriptRepository 写两份 tenant-scoped snapshot；只 mock server endpoint，bootstrap/cache/source-of-truth 路径均为真实实现。

### 问题 D：旧 feature directory gate 把新 test 文件当目录

architecture gate 原先对 `features_dir.iterdir()` 的所有名称做精确比较，默认根下只有目录。新增 `componentBoundaries.test.tsx` 后失败。

处理：目录集合 gate 只比较 `is_dir()`，随后仍递归扫描所有 production/test TS 文件的 root-App import 边界；component suite 自身由新增测试层 gate 固定。

### 问题 E：component test 不应拖慢全部纯测试

若把 Vitest 全局环境改成 jsdom，所有 60+ 纯逻辑测试都会创建 DOM 环境。

处理：只用 per-file environment directive。19 个 test files 总执行仍约 2.2 秒，Node pure suites 保持轻量。

## 验证结果

| 验证 | 结果 |
|---|---|
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities（255 packages audited） |
| frontend architecture/SSE focused pytest | 17 passed |
| 全量后端 pytest | 255 passed，4 warnings |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，10 source files |
| in-app browser production preview | Landing -> Studio rendered，composer/plan/session visible；console warning/error 0 |
| `git diff --check` | passed |

浏览器 tab 与 4173 preview 已清理。pytest warning 仍为既有第三方弃用和本机 `.pytest_cache` 权限提示。

## 保持不变与后续工作

保持不变：`useChatStream()` 调用 API、send/approve 返回行为、SSE parser/reducer/actions、Store singleton production composition 和页面 UI。

后续：组件边界稳定后执行 CSS 分层；可继续增加 stream HTTP error/reconnect cases 与 visual regression，但 F5 列出的 pure/component/integration/router/static/CI 六层已完成。
