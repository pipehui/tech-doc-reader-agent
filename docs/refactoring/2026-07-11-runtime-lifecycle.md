# Runtime Lifecycle 拆分记录

## 1. 本批目标

完成 config/query/execution/approval 拆分后，`ChatRuntime` 仍直接维护：

- `AppResources.create` 与全局 publish/reset。
- RedisSaver context manager、BusyLoading retry 和 sleep。
- graph build。
- 部分启动失败与关闭顺序。

这些都是组件生命周期，不属于 session query 或 graph execution。本批提取 `RuntimeLifecycle`，让 `ChatRuntime` 只保留 context-manager facade、Langfuse shutdown、approval repository ownership 和 public method delegation。

## 2. 行为刻画

拆实现前新增并运行 facade 级测试：

- graph build 失败后，checkpointer、global resources、approval repository 按顺序清理。
- `shutdown_langfuse` 失败时，其他 cleanup 仍执行。
- 既有 Redis BusyLoading retry、第二次成功、context exit 测试继续保留。

拆分前 lifecycle/config 相关测试 10 passed；拆分后扩大到 22 passed，并增加 lifecycle 直接测试：重复 `start()` 明确报错，`close()` 幂等，公开 state 在 close 后清空。

## 3. `RuntimeLifecycle` 边界

`app/runtime/lifecycle.py` 不 import `services` 或 API，而是接收六个依赖：

| 依赖 | 用途 |
|---|---|
| `resource_factory(settings)` | 创建当前资源容器 |
| `resource_publisher(resources)` | 发布给兼容期 global locator |
| `resource_resetter()` | 清除发布状态 |
| `checkpointer_context_factory(redis_url)` | 创建 saver context manager |
| `graph_factory(checkpointer)` | 构建 compiled graph |
| `event_logger` / `sleeper` | retry telemetry 与退避，可测试替换 |

它拥有 resources、checkpointer context、checkpointer 和 graph 状态，并定义唯一的 start/retry/close 状态机。production `bootstrap.py` 显式提供 `AppResources`、`RedisSaver` 和 graph builder；`ChatRuntime` 仍保留相同默认 wiring，作为直接构造的兼容路径。

## 4. Facade 兼容

现有测试、CLI 或调用方可能在构造后执行：

```python
runtime.settings = fake_settings
runtime.graph = fake_graph
```

因此没有让 lifecycle 在构造时冻结 settings，也没有删除 `ChatRuntime.graph/checkpointer/resources`：

- `__enter__` 前把当前 `runtime.settings` 同步给 lifecycle。
- 三个状态通过 property 代理到 lifecycle；fake graph 注入方式不变。
- `_require_graph()` 的错误文本和 public send/query API 不变。

## 5. 清理语义

- `RuntimeLifecycle.start()` 自身负责失败回滚；resource publish、checkpointer setup 或 graph build 任一步失败都会 close。
- retry attempt 只关闭失败的 checkpointer，不重复创建 resources。
- `close()` 先关闭 checkpointer，再 reset 已发布 resources；内部标志保证重复 close 不重复 reset。
- `ChatRuntime.__exit__` 即使 Langfuse shutdown 失败，也会 close lifecycle，再 close approval repository。
- approval repository 不放入 lifecycle，因为它是由 composition root 注入、由 facade 独立拥有的另一个 context resource。

## 6. 实际问题与解决方案

### 问题 A：新模块的类内自引用在运行时求值

首轮导入时 `def start(self) -> RuntimeLifecycle` 触发 `NameError`，因为新文件漏了 postponed annotations。mypy 在当前配置下没有暴露该运行时导入错误，但 pytest collection 和 Ruff `F821` 立即发现。

解决：添加 `from __future__ import annotations`，重新运行全部目标检查。该失败发生在 collection 阶段，没有执行测试或改变外部状态。

### 问题 B：resource publisher 可能部分成功后抛错

若只在 publisher 返回后设置“已发布”标志，publisher 部分修改全局状态再抛错时，rollback 会误以为无需 reset。

解决：调用 publisher 前先设置 cleanup ownership 标志；即使 publish 中途失败，`close()` 也执行 reset。

### 问题 C：callback 捕获时机会影响测试替换

`RuntimeLifecycle` 的 logger/sleeper 使用 `default_factory`，在实例构造时读取模块当前实现；测试可以在构造 lifecycle 前替换 sleeper，而不会真实等待。concrete resource/checkpointer/graph callbacks同样在 `ChatRuntime` 或 bootstrap 组装时显式捕获。

### 问题 D：文件行数没有下降

拆分前 `ChatRuntime` 为 327 行，拆分后为 335 行；新增 property 代理抵消了移出的 lifecycle 代码。若以“主文件必须更短”为 gate，这一批会被误判失败。

实际收益是 107 行 lifecycle 状态机有独立依赖和测试，facade 不再实现 retry/cleanup。后续调用方迁完后可删除兼容 setter，届时行数自然下降；本批不为追求数字破坏 fake graph 注入方式。

### 问题 E：global resource locator 尚未消失

production lifecycle 仍将 `AppResources` 发布到 `services.resources._current_resources`，因为现有 tools 在函数内部调用 `get_app_resources()`。

处理：lifecycle 只把 publish/reset 变成可替换 port，没有宣称 D4 完成。下一数据/resources 批次要把 tool factory 与 repositories 显式注入，最终 production wiring 才能传入 no-op publisher 并删除 fallback 自建资源行为。

## 7. 架构结果

```text
bootstrap (concrete wiring)
  -> ChatRuntime facade
       -> RuntimeLifecycle (start/retry/close)
       -> GraphExecutionService
       -> SessionQueryService
       -> ApprovalService
```

`runtime` 的 AST 门禁继续保证所有组件不依赖 `api` 或 legacy `services`；只有 bootstrap/facade 兼容边界接触 concrete services。

## 8. 验证

| 检查 | 结果 |
|---|---|
| lifecycle/config/execution/query/health/bootstrap/architecture targeted tests | 27 passed |
| Ruff（lifecycle/facade/tests） | passed |
| mypy（lifecycle + facade + bootstrap，`--follow-imports=skip`） | passed |
| 全量 pytest | 192 passed，3 个第三方 deprecation warnings |
| 全量 Ruff | passed |
| frontend production build | passed，2013 modules transformed |
