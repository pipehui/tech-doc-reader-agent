# 11 - 常见改动逐文件手册

本章按需求而不是目录组织。每个 recipe 都给出：先改哪一层、会影响哪些 contract、最小验证是什么。遇到新需求时，先找最接近的 recipe，再回到前面章节理解细节。

## Recipe 1：新增一个只读工具

假设要新增 `read_topic_outline(topic) -> str`。

### 第一步：先定义 capability 所属层

判断工具最终访问什么：

- 纯业务查询：在 `application/` 定义 input/result/port/use case；
- 外部文件、HTTP、数据库：concrete adapter 放 `infrastructure/`；
- 只组合已有 port：可以直接写 application service；
- `@tool` 只负责模型 schema、tenant/session/config 解析和结果序列化。

不要从 [`tools/documents.py`](../../tech_doc_agent/app/tools/documents.py) 直接 import `FaissStore`。正确依赖方向是 tool -> port，composition -> concrete adapter。

### 第二步：扩展依赖和工具目录

依次检查：

1. [`tools/dependencies.py`](../../tech_doc_agent/app/tools/dependencies.py)：是否需要新增窄 port 字段；
2. 对应 `tools/*.py`：在 factory 闭包中定义 `@tool`；
3. [`tools/bundle.py`](../../tech_doc_agent/app/tools/bundle.py)：增加稳定字段，并把 tool 放进 `names()`/构造结果；
4. [`infrastructure/resources.py`](../../tech_doc_agent/app/infrastructure/resources.py) 与 [`tools/dependencies.py`](../../tech_doc_agent/app/tools/dependencies.py)：让 concrete resource 实现并暴露该 capability；`composition.py` 仍只调用 `ToolDependencies.from_container(...)` 和 bundle factory；
5. 对应的 `agents/*_assistant.py`：只把工具加入真正需要的角色 safe tool 集；[`agents/definition.py`](../../tech_doc_agent/app/agents/definition.py) 是通用构建器，通常不因单个角色加工具而修改；
6. prompt：如果模型必须知道何时使用，更新对应 prompt 和 manifest hash；
7. policy/预算：它是否应计入 parser retrieval budget、sensitive approval 或特殊 retry。

`ToolBundle` 字段名、LangChain tool name 和 prompt 里的名字应一致。只改 Python factory 函数名但保留 decorator 暴露名时，要明确兼容目的并加测试。

### 输入输出设计

- 参数用模型能稳定生成的标量/list/object，不传 infrastructure 对象；
- 输出优先稳定 JSON string 或简短文本，不把 dataclass repr 暴露给模型；
- 对外错误抛 `ApplicationError`，让 ToolNode 统一生成 error ToolMessage；
- tenant 从 `RunnableConfig` 解析，不作为模型自由参数；
- 有写副作用时 tool_call_id 也应注入，而非模型填写。

### 最小验证

- tool factory 单测：schema、调用 port、序列化、typed failure；
- [`tests/test_tool_bundle.py`](../../tests/test_tool_bundle.py)：字段/名字顺序；
- Agent tool matrix 测试（如 [`tests/test_primary_assistant_tools.py`](../../tests/test_primary_assistant_tools.py)）；
- graph compile，确保 bind_tools 能接受；
- prompt registry hash/role set；
- 若 tool result 有特殊 UI，再跑 SSE/frontend 测试。

## Recipe 2：把现有工具改成敏感操作

“敏感”不是在工具函数里弹确认框，而是 graph compile 时把对应 ToolNode 放进 `interrupt_before`。

修改顺序：

1. 对应的 `agents/*_assistant.py`：从该角色的 `safe_tools` 移到 `sensitive_tools`；
2. [`graph/specs.py`](../../tech_doc_agent/app/graph/specs.py) / composition：确认该 Agent 的 sensitive node 仍存在；
3. [`graph/builder.py`](../../tech_doc_agent/app/graph/builder.py)：compile 的 interrupt node 集应由 spec 派生；
4. topology/compile 测试更新；
5. ApprovalDrawer/文案若需要识别新操作，更新前端；
6. 文档、eval 和安全测试。

不要在 tool 内先执行一半再请求审批。LangGraph interrupt 必须发生在 ToolNode 之前，checkpoint 才保存“待执行 call”而不是半完成副作用。

从 sensitive 改回 safe 风险更高：这等同取消用户确认。应同时审查 prompt 是否能自动触发、tool 是否幂等、权限边界与历史 checkpoint 中 pending calls。

## Recipe 3：新增一个写工具并支持重放

参考学习写入链，而不是简单 `repository.save()`。

建议构造：

```text
@tool + InjectedToolCallId + RunnableConfig
  -> typed Command(tenant, session_id, tool_call_id, arguments)
  -> Application Service
  -> Unit of Work / transactional repository
  -> typed Result
```

至少决定：

- 幂等 identity 包含哪些执行上下文；
- fingerprint 包含哪些参数；
- 同 key 不同参数是 Conflict 还是允许覆盖；
- command result 是否与业务状态同事务保存；
- checkpoint 审批后/进程崩溃后会怎样重放；
- provider timeout 是“肯定没写”还是“结果未知”。

仅仅让 RetryExecutor `idempotent=True` 不会自动使写操作幂等。必须先有业务/存储层证据。

## Recipe 4：新增一个 Agent / 工作流步骤

假设新增 `citation` Agent。

### 必须新增/修改的对象

1. prompt 文件：`agents/prompts/citation.md`；
2. [`agents/prompt_registry.py`](../../tech_doc_agent/app/agents/prompt_registry.py)：扩展 `AssistantRole` 与 `ASSISTANT_ROLES`，再在 [`agents/prompts/manifest.json`](../../tech_doc_agent/app/agents/prompts/manifest.json) 声明相对路径、SHA256 和 placeholders；
3. 新增 `agents/citation_assistant.py` 这类薄角色 builder，调用通用 `build_assistant_definition(...)` 绑定 safe/sensitive/control tools；没有共享 contract 变化时无需修改 `agents/definition.py`；
4. [`agents/registry.py`](../../tech_doc_agent/app/agents/registry.py)：增加字段、构造顺序和 identity 顺序；
5. [`agents/identity.py`](../../tech_doc_agent/app/agents/identity.py) 及 [`api/schemas.py::AssistantExecutionIdentityResponse`](../../tech_doc_agent/app/api/schemas.py)：同步完整角色集合、稳定顺序和 `assistant_role` 的 `Literal`；
6. 新 handoff command（`ToCitationAssistant`）及 primary tool 集；
7. [`core/state.py`](../../tech_doc_agent/app/core/state.py) 的 `WorkflowStep`（若它是 workflow step）、[`graph/specs.py`](../../tech_doc_agent/app/graph/specs.py) 与 composition：加入 `AgentSpec`，在这里声明 message scope、completion/result key 和 graph tool policy；
8. [`graph/builder.py`](../../tech_doc_agent/app/graph/builder.py)：应尽量由 spec 循环生成 node/edge，不复制一整段；
9. routing：primary handoff 映射、subagent finish/leave result；
10. state：若有独特 result 字段，定义 reducer/projection；
11. SSE/前端：同步 [`api/sse/agent_metadata.py`](../../tech_doc_agent/app/api/sse/agent_metadata.py) 的已知 node、[`frontend/src/types.ts`](../../frontend/src/types.ts) 的 `AgentKey`、[`frontend/src/agentColors.ts`](../../frontend/src/agentColors.ts) 的 label/color/normalize 规则，以及需要展示的新 structured result/event；
12. prompt、registry、topology、route、finish、tool matrix、frontend 测试。

### 先回答三个设计问题

1. 它为什么不能是现有 Agent 的一个 prompt 分支？
2. 它需要看到全会话还是只看本 step 的消息？
3. 它完成后是“finish 并推进 workflow”还是“leave/escalate 回 primary”？

如果只是格式化已有 parser result，不需要独立工具/对话栈/审批，通常应做纯 application formatter，而不是增加 Agent。

### Agent identity 不能从 node 字符串随便猜

后端 SSE metadata、前端 `normalizeAgent`、颜色和测试都有已知角色集。增加角色时应扩展明确 mapping，不能依赖 `node.replace("_assistant", "")` 这类脆弱规则。

## Recipe 5：修改 Agent 的工具权限

只改 prompt 说“不要调用”不构成权限。真正可调用集合来自 model `bind_tools` 和 `AssistantDefinition`。

检查：

```text
AssistantDefinition safe/sensitive tools + build 时绑定的 control tools
  -> AssistantRegistry 构建 runnable
  -> GraphSpec safe/sensitive node
  -> interrupt_before
  -> tool policy / budget
```

删除工具时还要考虑历史 checkpoint：旧 AIMessage 可能已经发出该 tool call。若恢复时新 ToolNode 不再认识它，需要兼容 error ToolMessage/迁移策略，而不是让 graph 崩溃。

## Recipe 6：新增或修改 Graph State 字段

先分类字段：

- checkpoint 事实：跨请求恢复，放 State；
- 本次请求增量：通常另有 `*_delta`，SSE 发出后下一请求 reset；
- 可从 messages/其他字段确定推导：优先 projection，不重复存；
- 仅某函数局部：不要放 graph state。

修改点：

1. [`core/state.py`](../../tech_doc_agent/app/core/state.py)：类型与 reducer；[`graph/state.py`](../../tech_doc_agent/app/graph/state.py) 只是 graph-facing re-export，不应复制字段；
2. 哪个 node 初始化/reset；
3. 哪个 wrapper 更新；
4. routing 是否依赖；
5. checkpoint 旧值缺失时默认；
6. [`runtime/sessions.py`](../../tech_doc_agent/app/runtime/sessions.py) 是否投影到 session state；
7. SSE payload/translator；
8. API schema、前端 decoder/store；
9. state serialization/contract tests。

避免同一个概念存两份可独立修改的字段。例如 current agent 能从 dialog stack/guardrail 状态投影时，就不要再由每个 node 手动维护第三份 truth，除非有明确版本/一致性规则。

### Reducer 是 contract

messages 用 `add_messages`，dialog stack 用 push/pop reducer，usage/metrics 可能整份替换。字段加到 TypedDict 并不会自动决定合并语义。错误 reducer 会在并行/多次 update 时丢状态，必须用连续 update 测试。

## Recipe 7：修改 Graph 路由

路由函数应是“State -> 有限 route label”的纯判断，边和 node 名映射在 builder/spec。

修改前画出优先级。例如 primary 当前先看 budget terminating，再看最后消息/tool call/目标 handoff。新增条件时回答：

- 它是否应该压过预算终止？通常不应该；
- 它是在 tool call 前还是 tool result 后判断；
- 没有 messages、最后不是 AI、多个 tool calls 时怎样；
- unknown tool 是安全结束、error 还是回 primary；
- checkpoint 恢复在该点会不会重复执行 side effect。

验证三层：route unit test、compiled topology test、最小 graph stream/integration test。只测函数返回 label 不能证明 builder 里该 label 指向正确 node。

## Recipe 8：新增 SSE 事件

完整同步表：

### 后端

1. [`api/sse/contract.py`](../../tech_doc_agent/app/api/sse/contract.py)：事件常量/列表；
2. [`api/sse/payloads.py`](../../tech_doc_agent/app/api/sse/payloads.py)：strict Pydantic payload；
3. message/update/parts translator 中选择唯一发射位置；
4. [`api/sse/events.py`](../../tech_doc_agent/app/api/sse/events.py) / encoder：envelope；
5. `tests/test_sse_payloads.py`、`test_sse_events.py`、`test_sse_contract.py`。

### 前端

1. [`frontend/src/sseContract.ts`](../../frontend/src/sseContract.ts)；
2. [`frontend/src/streaming/ssePayloads.ts`](../../frontend/src/streaming/ssePayloads.ts) runtime decoder；
3. [`frontend/src/streaming/sseReducer.ts`](../../frontend/src/streaming/sseReducer.ts) pure action；
4. 必要时 `storeAdapter`、slice、type、组件；
5. Inspector filter / transcript version；
6. payload/reducer/adapter/integration tests。

### 事件设计问题

- 是增量 (`delta`) 还是完整 snapshot；
- 重复收到是否可安全归约；
- 是否必须含 node/agent/session/trace context；
- 是否进入 Inspector；token 类高频事件是否应聚合；
- 结束前最后一份事件能否恢复最终状态；
- schema 增字段是 required、nullable 还是 optional。

不要直接 `yield {"event": ..., "data": arbitrary_dict}` 绕过 payload model。否则后端内部能跑，前端只会得到 invalid protocol。

## Recipe 9：修改 Chat/Approve API schema

位置：

- [`api/schemas.py`](../../tech_doc_agent/app/api/schemas.py)
- [`api/routes/chat.py`](../../tech_doc_agent/app/api/routes/chat.py)
- [`api/chat_delivery.py`](../../tech_doc_agent/app/api/chat_delivery.py)

请求模型当前依靠字段长度、pattern 和类型约束收口输入，但没有设置 `extra="forbid"`；Pydantic 默认会忽略未知字段。新增字段必须决定：由客户端提供、header/tenant dependency 提供，还是 runtime 自己生成。若要改成拒绝未知字段，应把它作为 API 行为变化添加配置与测试，不能顺手收紧。session/tenant/trace/budget identity 不应重复由多个来源互相覆盖。

修改 `/chat/approve` 时要同时覆盖两种 approval kind：Redis 中风险原始输入与 checkpoint sensitive tool。不要只用当前 UI 的最新 pending tool 推断后端状态。

HTTP 失败和 SSE error 不同：流开始前的 validation/high-risk 可返回 JSON 4xx；流开始后的 runtime failure 必须是安全 SSE error。改变分界时同步前端 `onopen` 与流错误测试。

## Recipe 10：给学习状态增加字段或命令

参考 [07 - 持久化](07-learning-profile-and-persistence.md)。按顺序：

1. domain model + validation；
2. `to_payload/from_payload`；
3. command/fingerprint/identity；
4. service mutation；
5. UoW snapshot/repository schema + manifest count（若新增集合）；
6. 旧 snapshot migration/read compatibility；
7. tool schema/result；
8. learning API overview；
9. frontend contract/slice/view；
10. transactional/idempotency/legacy tests。

### 字段是否进入 fingerprint

会改变业务结果的输入通常必须进入。纯 trace/debug metadata 若进入 fingerprint，重放时 trace ID 改变会造成假冲突；若不进入，则不能由它控制业务行为。

### 修改匹配键

学习记录目前 `tenant + knowledge` 精确匹配，memory 是 `tenant + kind + topic`。改成 normalized key 时，要处理已有重复项的合并顺序、ID/createdAt 保留和幂等结果引用。

## Recipe 11：修改用户画像

位置：

- [`application/profile_models.py`](../../tech_doc_agent/app/application/profile_models.py)
- [`application/profile_service.py`](../../tech_doc_agent/app/application/profile_service.py)
- [`infrastructure/persistence/user_profile_repository.py`](../../tech_doc_agent/app/infrastructure/persistence/user_profile_repository.py)
- [`tools/profiles.py`](../../tech_doc_agent/app/tools/profiles.py)

稳定偏好/主题规则属于 domain `apply`；读 memory 并组合上下文属于 service；路径/envelope 属于 repository；模型参数和 JSON 输出属于 tool。

新增字段还要同步 primary prompt、profile summary、API/schema（若暴露）、旧 envelope default 和前端 Learner（若展示）。无变化时不应写新文件/更新时间。

## Recipe 12：新增检索 filter/category

一个 filter 需要穿过：

```text
tool 参数
  -> SearchQuery.filters
  -> normalize_filter
  -> filter_documents / semantic post-filter
  -> metadata normalization/chunk propagation
  -> result contract / tests / eval corpus
```

若只有 BM25 路应用 filter，hybrid 会从 semantic/exact 混回不合规文档。三路都要用同一规范化规则。

新增 category 时修改 taxonomy/rules/aliases/broad tags，并决定旧 `uncategorized` 文档何时重新归类。仅改 inference 只影响新 normalize；磁盘旧 metadata 在 load normalization 时可能变化，signature 和检索评测也会跟着变。

## Recipe 13：更换 embedding 或 chunking

这是数据迁移，不只是换 env：

1. 记录新 model/dimension/chunk 参数；
2. 用原始 documents 全量 `build_index`；
3. 生成新 FAISS generation；
4. 回读校验 vector/chunk/doc counts；
5. 跑 retrieval eval；
6. 原子发布 current manifest；
7. 保留/清理旧 generation 的回滚策略。

不能把新 dimension vectors append 到旧 IndexFlatL2；不能让一份 index 混用两种 chunk size 后仍把结果当同一评测 baseline。

## Recipe 14：新增外部 provider

例如增加一个搜索 provider：

1. application port 若能力未变，通常不改；
2. infrastructure adapter 负责 SDK、响应归一化和 `classify_error`；
3. 明确 retry 的幂等性、quota `before_attempt`、timeout/Retry-After；
4. fallback 顺序放在 backend policy，不放 Agent prompt；
5. settings/secret/env example；
6. redaction marker；
7. usage/retry observability；
8. fake provider 单测：成功、空、坏 payload、retry、quota、fallback、全失败。

如果 provider 返回的数据 contract 不同，先归一化为 application model，再让 tool 序列化。不要让模型知道每家 SDK 的原始字段名。

## Recipe 15：新增前端页面或状态 slice

新增 page：

1. `features/<feature>/` 组件；
2. `AppRouter` route 与 `routing.ts` 名称；
3. Topbar/导航；
4. 明确是否需要 session bootstrap；
5. 复用 store selector，不在页面复制 fetch/SSE state；
6. component/routing/CSS architecture tests。

新增 slice：

1. `store/contracts.ts` 定义 data/actions；
2. `store/<name>Slice.ts` factory + dependencies；
3. `createAppStore` 组合；
4. reset/session switch 是否清理；
5. 是否落 transcript/preferences/独立 repository；
6. fake dependency 和 slice tests。

仅某组件内部的展开/输入框状态不必放全局 store；跨 Studio/Inspector/Learner 或需要持久化/会话切换的状态才值得提升。

## Recipe 16：改变浏览器持久化结构

不要只改 interface。先选择：

- backward compatible：reader 接受旧字段并填 default，writer 仍写当前版；
- breaking：提升 `TRANSCRIPT_VERSION`，旧 cache 返回 null，从后端 history 恢复；
- 显式 migration：按 version 转换并写回。

storage key 必须保留 tenant+session。migration 失败应丢弃体验缓存并记录 warning，不能阻止正常后端会话加载。

如果新 UI 严重依赖旧 checkpoint 中不存在的 trace/tool details，应升级后端 history contract，而不是把 localStorage 当永久数据库。

## Recipe 17：新增 Settings

检查清单：

1. `Settings` 字段类型、default、Field constraint；
2. 跨字段 validator；
3. `build_*` factory 把 raw setting 转成 domain policy；
4. composition 注入；
5. `.env.example`；
6. docker compose/CI/deployment secret；
7. README/config docs；
8. disabled/invalid/boundary tests；
9. 日志中不要输出 secret。

避免把 Settings 实例传到所有 domain model。adapter/factory 读取 setting 后构造窄 policy/value，domain 只依赖它真正需要的字段。

## Recipe 18：移动或拆分模块

大规模重构时按阶段保留行为：

1. 先写/确认 contract tests；
2. 建新 owning module；
3. 原路径做薄 compatibility re-export；
4. 内部调用逐步迁到新路径；
5. architecture import test 禁止新代码反向依赖 facade；
6. 搜索所有 import、monkeypatch path、docs；
7. 给删除旧 facade 设明确条件，而不是永久遗留。

Python monkeypatch 绑定的是“被使用处 import 的名字”，移动函数后旧测试 patch 原定义模块可能不再生效。兼容 facade 是否保留同一对象/patch 行为要显式测试。

详见 [13 - 兼容层与迁移](13-compatibility-and-migration.md)。

## 每次修改前后的通用检查表

### 修改前

- 写一句新的输入输出 contract；
- 找到当前唯一 owner；
- 列出同步投影：graph state、API、SSE、frontend、persistence；
- 判断是否改变 checkpoint/磁盘/localStorage 兼容；
- 先运行最接近的现有测试，确认 baseline。

### 修改后

- 用 `rg` 搜旧名字和重复逻辑；
- 跑目标单测；
- 跑 architecture/contract tests；
- 跑全后端与前端测试/build；
- 检查文档链接和示例；
- 手工走一次涉及审批/恢复/失败的关键路径；
- `git diff --check` 检查空白/冲突 marker；
- 查看 `git diff`，确认没有把兼容数据、todo、生成产物误提交。

下一章 [12 - 调试与测试](12-debugging-and-tests.md) 给出从症状反推层级的排障路线和本项目的实际命令。
