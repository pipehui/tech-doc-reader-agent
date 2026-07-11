# 前端 App feature 边界拆分

## 本批目标

上一批已把 session bootstrap 与 Topbar 从 `App.tsx` 抽离，但根文件仍有 967 个物理源码行，继续集中承载 Landing、Studio、Chat、Approval、Learner、Inspector、trace export 与 learning refresh。任何页面变更仍需进入同一个文件，也无法在不加载根组件的情况下测试 timeline、event summary 等业务逻辑。

本批完成前端 TODO F1：保持现有 DOM class、路由、Store action 与视觉不变，按页面/交互职责建立 feature 边界；根 `App.tsx` 只作为 app router facade。

## 最终结构

```text
frontend/src/
├── App.tsx                         # 6 行，只渲染 AppRouter
├── app/
│   ├── AppRouter.tsx               # 44 行，shell/theme/routes/bootstrap
│   ├── Topbar.tsx                  # 165 行，纯导航和控件渲染
│   └── routing.ts
├── features/
│   ├── approval/ApprovalDrawer.tsx
│   ├── chat/ChatPane.tsx
│   ├── inspector/
│   │   ├── Inspector.tsx
│   │   ├── inspectorModel.ts
│   │   ├── inspectorModel.test.ts
│   │   └── traceExport.ts
│   ├── landing/Landing.tsx
│   ├── learner/Learner.tsx
│   ├── session/
│   │   ├── refreshSessionContext.ts
│   │   ├── useRefreshLearning.ts
│   │   └── useSessionControls.ts
│   └── studio/Studio.tsx
└── shared/components/AgentBadge.tsx
```

## 实际改动

### 1. App 变为真正的 facade

`App.tsx` 从 967 行降到 6 行，只 import/render `AppRouter`。`AppRouter` 是唯一 shell composition：

- 根据 pathname 计算 view；
- 启停 session bootstrap；
- 同步 document theme；
- 组装 Topbar、四条 route、fallback 与 toast host。

页面 feature 不 import 根 App；architecture gate 固定根文件少于 20 行、router shell 少于 100 行，并检查四个页面都由 router 显式组装。

### 2. Landing/Studio/Learner 各自拥有页面状态

`features/landing` 只读取当前 tenant 和导航，不依赖 chat/inspector。三组静态 card 配置提升为模块常量，避免每次 render 重建。

`features/studio` 拥有 session rail、当前 session 指标与 tool timeline，只复用 chat workspace 和 plan stepper。

`features/learner` 拥有 overview、knowledge/review rails、review ranking 和 examination takeover。是否进入测验模式、是否显示 plan 由 Learner 自己选择 Store state，不再由通用 ChatPane 判断 Learner 业务。

### 3. Chat 与 Approval 分开

`features/chat/ChatPane.tsx` 集中消息列表、Markdown message、tool card、plan、composer 和 chat workspace；`features/approval/ApprovalDrawer.tsx` 单独拥有 pending tool 选择、feedback 和 approve/reject 操作。

ChatPane 改为接收 children 作为消息区域：Studio 传 `MessageList`，Learner 根据 examination state 传 `QuizTakeover` 或 `MessageList`。其 header/plan/approval/composer DOM 顺序保持不变。

### 4. Inspector 的算法与浏览器副作用分离

`features/inspector/Inspector.tsx` 只做 Store selection、scroll preservation 和 UI 渲染。以下逻辑进入无 React/Store/browser 依赖的 `inspectorModel.ts`：

- token/type event filtering；
- stream start + event timestamp bounds；
- 时间到百分比坐标与 clamp；
- lane marker class；
- event summary。

`traceExport.ts` 将 export payload/filename 与 Blob/download side effect 分开；纯 payload 可注入 clock 并做确定性测试。

### 5. Topbar session 控制移入 Hook

Topbar 不再读取 location、归一化 tenant 或直接 reset context。`useSessionControls` 拥有三组 draft、view navigation、session switch 和 tenant switch；`sessionSwitchSearch` / `tenantSwitchContext` 是纯函数，测试固定：

- prompt 在 context switch 时删除；
- 无关 query 保留；
- session/user/namespace 更新；
- 非法 tenant draft 回退到 domain default。

### 6. 消除 state + learning refresh 重复

原来 `useChatStream.refreshStateAndLearning` 与 `App.tsx.useRefreshLearning` 各维护一份相同 `Promise.all(getSessionState, getLearningOverview)` 和 Store 写入。

新增纯 `refreshSessionContext` 用例，stream 结束刷新和按钮刷新共用同一实现、同一错误消息策略。成功/失败都有 fake port 单测；UI 点击失败不再产生未处理 Promise rejection，而是写入 system transcript。

## 实施中遇到的问题

### 问题 A：直接移动 ChatPane 会制造 feature 循环依赖

原 ChatPane 内部直接渲染 `QuizTakeover`，而 Learner 页面又需要渲染 ChatPane。若机械拆文件，会形成 `chat -> learner -> chat` 循环，文件变多但耦合更差。

处理：ChatPane 接收消息区域 children；Learner 自己决定 examination takeover。依赖方向变为 `learner -> chat`，chat 不知道 Learner 的存在。

### 问题 B：AgentBadge 不能归 Chat 私有

Chat message、plan 和 Inspector swim lane 都使用 AgentBadge。最初让 Inspector 从 `features/chat/ChatPane` import 它，会令完全独立的可观测页面依赖聊天聚合文件。

处理：把 AgentBadge 下沉到 `shared/components`。Inspector architecture gate 明确禁止反向 import chat feature。

### 问题 C：只搬 Inspector JSX 会继续隐藏不可测逻辑

timeline bounds 默认用 `Date.now()`，event summary/classification 与 filter 夹在组件中；机械迁移后依旧只能通过完整页面测试。

处理：先定义纯 model 边界，空 timeline 的 clock 可注入，新增 5 个测试覆盖过滤、stream bounds、零宽扩展、坐标 clamp、summary/class 与 trace payload。

### 问题 D：旧 architecture gate 锁定了旧职责位置

上一批门禁要求 session bootstrap/Topbar 直接出现在 `App.tsx`。正确引入 AppRouter 后，该断言会与目标架构冲突。

处理：门禁改为分层验证：根 App 只组合 AppRouter；AppRouter 拥有 shell/bootstrap/routes；Topbar 委托 session controls；纯 model/use case 禁止 React、Store singleton 或 browser globals。门禁锁责任，不锁历史文件位置。

### 问题 E：页面拆分暴露了 refresh 的隐式行为差异

stream 结束刷新会捕获错误并写 system message，手动“刷新”按钮则直接返回 rejected Promise。两份近似代码不仅重复，失败语义也不一致。

处理：统一为一个 port-based use case，所有入口都返回 `loaded/failed`，错误统一进入 transcript。

## 测试与门禁

本批新增 9 个 Vitest：

- Inspector filter/bounds/position/summary/class；
- trace export payload/filename；
- shared session refresh 成功/失败；
- session/tenant switch query model。

Architecture tests 从 8 个扩展为 10 个，新增：

- App/AppRouter 行数与 composition 责任；
- 固定 feature 目录集合；
- 所有 feature 禁止 import 根 App 或直接访问 localStorage；
- Landing/Inspector 反向依赖限制；
- Inspector model 纯度；
- Topbar -> useSessionControls 委托。

## 验证结果

| 验证 | 结果 |
|---|---|
| `npm run check` | passed |
| `npm test` | 14 files，61 tests passed |
| `npm run build` | passed，2038 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| frontend architecture focused pytest | 10 passed |
| 全量后端 pytest | 246 passed，4 warnings |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，10 source files |
| in-app browser production preview | Landing/Studio/Inspector/Learner 均渲染；Learner plan toggle 正常；console warning/error 0 |
| `git diff --check` | passed |

浏览器 tab 与 4173 preview 已清理。pytest warning 仍为既有第三方弃用和本机 `.pytest_cache` 权限提示。

## 保持不变与后续工作

保持不变：四条 route/query、CSS class、页面 DOM 主结构、Store action、session/tenant switch、审批、消息/tool/plan、Learner review 与 Inspector export 行为。

后续继续：

- `shared/api/client.ts` 统一 JSON error mapping 与 endpoint adapters；
- tool result 后端协议提供显式 status/error，删除自然语言错误猜测；
- React component tests 覆盖 ApprovalDrawer、PlanStepper、MessageBubble 与 Inspector filter；
- fake SSE integration 覆盖 send -> tool -> interrupt -> approve -> done；
- 组件边界稳定后按 shell/chat/inspector/learner/approval 拆 CSS。
