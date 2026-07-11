# Runtime 配置、序列化与会话查询拆分记录

## 1. 本批目标

原 `ChatRuntime` 同时承担 Redis/graph 生命周期、session config、消息执行、两类审批、checkpoint 查询以及 API view 序列化。即使只修改 history payload，也必须进入一个接近 900 行且依赖 Redis、LangGraph execution、Langfuse 的类。

本批先移动不改变 graph 执行语义的职责：

- `SessionConfigFactory`：tenant thread id、metadata、Langfuse callback、recursion limit。
- `MessageSerializer`：LangChain-style message 到完整 history / 精简 history view 的转换。
- `SessionQueryService`：snapshot、history、history view、session state 及其 async adapter。
- `ChatRuntime`：保留原方法签名，通过 facade 委托新组件。

send/resume、approval repository 和 Redis lifecycle 留在后续独立批次，避免一次提交同时改变查询投影与执行时序。

## 2. 行为刻画

拆实现前新增 facade 级测试，固定以下既有契约：

- checkpoint 中 human/ai/tool/system 消息的 role、raw type、tool call 和多段文本序列化。
- 完整 history 保留 system/tool，history view 丢弃 system、空文本 AI，并由 `include_tools` 控制 tool result。
- checkpoint 中保存的 user/namespace 优先于请求 tenant；读取 config 仍使用请求 tenant 的 thread id。
- `snapshot.next` 会映射为 `pending_interrupt`。
- state view 的 `exists/current_agent/workflow_plan/plan_index` 语义。
- 同步和异步 session state 返回相同结果。

拆分前相关测试为 11 passed；拆分后同一组仍为 11 passed。

## 3. 依赖与实现边界

### `runtime/config.py`

factory 只依赖 settings、trace context、tenant 值对象和 Langfuse adapter。`ChatRuntime.build_config()` 每次使用当前 `self.settings` 创建 factory，而不是在 `__init__` 缓存 settings 快照；这样保留测试覆盖和运行时显式替换 settings 的既有能力。

### `runtime/serialization.py`

serializer 是无状态对象，不依赖 graph、Redis 或 FastAPI。它保留原有的文本提取规则，没有在搬移时顺带改变多模态/未知 content block 策略。

### `runtime/sessions.py`

query service 通过三个窄 callable 注入依赖：graph provider、config builder、pending guardrail checker。它不 import `ChatRuntime`，因此依赖方向是 facade -> query service。

三个 view 原先重复执行 tenant 归一化、pending guardrail 查询、snapshot 读取和 values fallback；现在由 `_read()` 统一定义。完整 history 和精简 history 仍分别表达不同投影，避免用大量布尔参数制造一个通用 serializer。

## 4. 实际问题与解决方案

### 问题 A：原测试 monkeypatch 位置属于实现细节

原测试 patch `services.chat_runtime.build_langfuse_trace`。实现迁移后该符号的定义源变成 `runtime.config`，继续在 facade 留一个无业务用途的 re-export 会制造假耦合。

解决：测试改为 patch 真正的定义/调用模块 `runtime.config`；对外 `ChatRuntime.build_config` 的返回契约不变。

### 问题 B：query service 不能持有 graph 的静态快照

`ChatRuntime` 在构造后、进入 context manager 时才创建 graph；单测也会在构造后注入 fake graph。若 query service 在初始化时保存 `self.graph`，它会永久保存 `None`。

解决：注入绑定的 `_require_graph` provider，每次查询时动态读取当前 graph，并继续保留“未初始化时抛出明确 RuntimeError”的行为。

### 问题 C：pending 状态的 tenant 归一化容易漂移

guardrail approval key 与 checkpoint thread id 都依赖 user/namespace。三个 view 各自复制解析逻辑时，后续很容易只改其中一个。

解决：`_read()` 先归一化一次 tenant，然后用同一组值查询 pending guardrail 和 graph snapshot。

### 问题 D：mypy 全量递归暴露既存债务

直接检查 `chat_runtime.py` 会沿 import 进入 assistant/message_scope，报告 11 个旧类型错误，包括 ChatOpenAI 参数 stub、State 返回类型和空 sensitive-tool list 注解。这些错误与本批无关，但说明当前全量 mypy 还不能作为门禁。

处理：本批使用 `--follow-imports=skip` 对 4 个直接修改的 runtime source 做边界检查并通过；既存 11 项不在本批静默修复，也不宣称全仓 mypy 已通过。

## 5. 结果

- `ChatRuntime` 从 894 行降到 730 行；减少的是 query/serialization/config 实现，不以行数本身作为完成标准。
- config、消息投影和 session read 现在各有单一实现源。
- API route、CLI 及所有现有调用方仍使用同一个 `ChatRuntime` facade。
- graph send/resume 的 sync/async 镜像仍存在，是下一 runtime 批次的主要目标。

## 6. 验证

| 检查 | 结果 |
|---|---|
| runtime config/query targeted tests | 11 passed |
| Ruff（本批文件） | passed |
| mypy（4 个 runtime source，`--follow-imports=skip`） | passed |
| 全量 pytest | 172 passed，3 个第三方 deprecation warnings |
| 全量 Ruff | passed |
| frontend production build | passed，2013 modules transformed |
