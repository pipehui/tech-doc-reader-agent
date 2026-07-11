# 前端类型化 SSE 归约边界

## 本批目标

`useChatStream.ts` 原来同时负责 HTTP/SSE 连接、wire data 解析、13 类事件分支、流式元数据统计和 Zustand 写入。协议变化、状态变化与网络生命周期耦合在同一个函数中，必须依赖浏览器全局 Store 才能测试事件行为。

本批把它拆为三层：

1. `sseEnvelope.ts`：把 EventSource 的 `event + data` 转为已知的 discriminated envelope 或 unknown event；
2. `sseReducer.ts`：纯函数计算下一份 stream state 和显式 actions；
3. `storeAdapter.ts`：按顺序把 actions 写入 Zustand。

`useChatStream.ts` 只保留请求构造、响应校验、EventSource 生命周期、结束后的 refresh 和错误展示。

## 实际改动

### 1. 建立事件名与类型边界

`SsePayloadMap` 覆盖后端当前声明的 13 个事件，映射为以 `type` 为判别字段的 `SseEnvelope`。后端 Python contract test 继续校验前后端事件名集合一致；TypeScript mapped union 则保证事件名增加后必须在 payload map 和 reducer switch 中处理，否则 typecheck 失败。

payload 字段目前仍按 `unknown` 接收并由 reducer 做窄化。这是有意的兼容边界：本批没有引入运行时 schema codegen，也不把未经校验的 JSON 伪装成强类型业务对象。

parser 保留旧 wire 兼容行为：

- 普通 JSON object；
- 被再次 JSON encode 的 object/array；
- primitive 包装为 `{ value }`；
- 非 JSON 文本包装为 `{ raw, message }`；
- 未知 event 进入 forward-compatible 分支，不中断现有 stream。

### 2. 事件语义移入纯 reducer

`StreamReducerState` 只保存本次 response 的 active agent、token meta 和 tool-call snapshot。`reduceSseMessage` 不 import Zustand、React 或 localStorage，也不读取时间和随机 ID；调用方显式传入 `now` 与 `createId`，使重复、乱序和缺字段行为可确定测试。

reducer 输出的 actions 包括 event 记录、session delta、streaming message、tool call/result、protocol warning 和 stream error。错误事件仍先输出 `record_event`，再输出 `stream_error`，适配器严格按数组顺序执行，避免抛错前丢失 Inspector 证据。

### 3. 保留审批恢复时的 tool-call 连续性

`tool_result` 不能只看本轮 stream 内的 `tool_call`。审批后 `/chat/approve` 会创建新连接，但对应 pending tool call 已在上一轮写入 Store。

因此创建 reducer state 时显式传入当前 `store.toolCalls` 的浅拷贝。后续事件只读写 reducer 自己的不可变 snapshot：既能关联审批前的 tool metadata，又不在 reducer 内偷偷调用全局 `useAppStore.getState()`。乱序 result 找不到 call 时仍构造一个可展示的 fallback tool call，保持原行为。

### 4. 增加前端测试层和 CI gate

引入 Vitest，并增加 18 个测试：

- parser 的 object、double-encoded JSON、primitive、invalid text 和 unknown event；
- 后端声明的每一种 event 都能进入 reducer；
- token 重复、缺字段和最终消息 meta；
- tool call 重复、result 正序/乱序、审批前 call snapshot；
- plan、structured result、interrupt、done、guardrail、error；
- action 顺序和 Store adapter dispatch。

CI 前端顺序现在是 typecheck -> unit tests -> build -> FastAPI static smoke。

## 实施中遇到的问题

### 问题 A：纯 reducer 仍需要跨 stream 的工具状态

最初如果只保存本次连接收到的 tool call，审批恢复流收到 `tool_result` 时会丢失原始 `agent/node/tool/args/createdAt`。直接在 reducer 内读取 Store 又会重新引入隐藏依赖。

处理：把已有 tool-call map 作为 reducer 初始化输入。依赖方向变为 transport/composition -> reducer data，而不是 reducer -> global Store。

### 问题 B：时间和 fallback ID 使 fixture 不稳定

token duration、tool timestamps 和缺失 `tool_call_id` 都曾在 handler 内直接调用当前时间/UUID。测试只能做模糊断言，也难以证明重复与乱序结果。

处理：把 `now` 和 `createId` 作为每次 reduction 的显式 options，由生产 transport 注入真实值、测试注入固定值。

### 问题 C：安全版本与原 Vite 版本不兼容

最先尝试的 Vitest 2.1.9 可兼容原 Vite 5，但 `npm audit` 报告包括 Vitest、Vite、esbuild 和 React Router 的 8 个漏洞，其中包含 critical/high。仅保留旧构建链并不适合作为新的测试基线。

处理：升级到 Vite 6.4.3、Vitest 4.1.10 和 React Router 6.30.4，声明与 CI/Docker 一致的 Node 20/22/24+ engine 范围，并用非 force 的 audit fix 更新可安全升级的 Babel transitive dependency。最终 audit 为 0，类型检查、测试和 production build 全部通过。

### 问题 D：工具成功/失败仍由自然语言猜测

现有后端 `tool_result` 没有显式 status，前端只能延续 `/error|exception|traceback/i` 推断。移动到纯 reducer 后这个技术债更集中、更可测试，但尚未从协议上解决。

处理：本批保留行为以避免协议破坏，并将 `inferToolStatus` 暴露为独立纯函数。后续应先扩展后端 payload，再删除文本猜测。

## 验证结果

| 验证 | 结果 |
|---|---|
| `npm run check` | passed |
| `npm run test` | 2 files，18 tests passed |
| `npm run build` | passed，Vite 6.4.3，2011 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| 全量后端 pytest | 236 passed，4 warnings |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，10 source files |
| `git diff --check` | passed |

pytest 警告中三条是 LangGraph/Starlette 第三方弃用提示；另一条是本机 `.pytest_cache` 目录拒绝写入。它们没有造成测试失败，但第三方升级和本机 cache 权限应分开跟踪，不能写成零警告。

## 保持不变与有意变化

保持不变：

- `/chat`、`/chat/approve` 请求体、tenant headers 与流结束 refresh；
- 13 类既有事件对消息、session、tool call 和 Inspector 的可见结果；
- 审批前后 tool result 的关联；
- 后端错误被转为 Store error/system message 的外部行为。

有意变化：

- unknown event 在开发环境给出 protocol warning，生产环境静默忽略；
- 所有事件语义从 transport hook 移入可独立测试的纯 reducer；
- 前端 CI 开始运行单元测试；
- 前端构建链升级到无已知 npm audit 漏洞的版本组合。

## 后续工作

- 后端 `tool_result` 增加显式 `status/error`，删除自然语言正则推断；
- payload contract 进一步采用运行时 schema 或 codegen，减少 `unknown` 字段；
- 增加 fake EventSource 的 send -> tool -> interrupt -> approve -> done integration test；
- 继续拆分 `store.ts` 的 transcript repository/slices，以及 `App.tsx` 的 feature components。
