# Redis Durable Guardrail Approval 与 Production Bootstrap

## 1. 要解决的正确性问题

LangGraph tool interrupt 已由 Redis checkpoint 保存，但 medium-risk input guardrail 发生在 graph 运行前。原实现把原始输入放在 `ChatRuntime` 进程内 dict：

- 进程重启后 `/chat/approve` 找不到原消息。
- 多 worker 下，创建审批和处理审批可能落到不同进程。
- 两个并发 approve 可能都认为自己取得了 pending request。

上一批已抽出 `ApprovalRepository` port，本批为 production composition 提供 Redis adapter，并保留 `ChatRuntime()` 的无网络默认构造。

## 2. Composition 决策

不能把 `ChatRuntime` 默认 repository 直接改成 Redis。大量 unit/component test 会在构造 runtime 后注入 fake graph，而没有进入 FastAPI lifespan；构造对象就隐式连接外部服务会破坏可测试性，也让依赖来源不可见。

最终组装：

```text
unit tests / explicit fake graph
  -> ChatRuntime() -> InMemoryApprovalRepository

FastAPI lifespan / CLI
  -> app.bootstrap.build_chat_runtime(settings)
       -> RedisApprovalRepository.from_url(...)
       -> ChatRuntime(settings=..., approval_repository=...)
```

FastAPI 与 CLI 本来就必须连接同一 Redis 来创建 LangGraph checkpointer，因此 production composition 选择 Redis approval adapter 不增加新的基础设施前提。

## 3. Redis 记录格式

key：

```text
tech_doc_agent:guardrail_approval:{user_id}:{namespace}:{session_id}
```

value 是 versioned JSON envelope：

```json
{
  "schema_version": 1,
  "status": "pending",
  "created_at": "2026-07-11T08:00:00+00:00",
  "expires_at": "2026-07-11T08:15:00+00:00",
  "request": {
    "session_id": "...",
    "user_input": "...",
    "user_id": "...",
    "namespace": "...",
    "source": "chat.message",
    "risk_level": "medium",
    "findings": ["..."]
  }
}
```

`SET ... EX <ttl>` 写入，默认 TTL 900 秒，由 `GUARDRAIL_APPROVAL_TTL_SECONDS` 配置且必须大于 0。读取时校验 schema/status/timestamp timezone/字段类型；未知版本或损坏 JSON 不会静默当作“无审批”。

## 4. 原子 resolve

`pop()` 使用 Redis `GETDEL`：取值和删除由 Redis 在一个命令中完成。两个 worker 同时处理同一个 key 时，只会有一个得到 request，另一个得到 `None`，从而避免原消息被重复送入 graph。

项目 Docker 使用 Redis 8，当前 Python 环境 redis-py 7.4.1 提供 `GETDEL`。若部署到 Redis 6.2 之前的外部实例，该命令不可用；这应作为部署版本前提，而不是用非原子的 `GET` + `DEL` fallback 降级正确性。

## 5. 生命周期所有权

`RedisApprovalRepository.from_url()` 只创建 lazy redis-py client，不在 object construction 时发起网络 I/O。repository 由 `ChatRuntime` context 拥有：正常退出和启动失败都会 close；close 失败只记录结构化错误，不覆盖主异常。

同时修正了原 cleanup 顺序：即使 `shutdown_langfuse` 抛错，checkpointer、全局 resources 和 approval client 仍会进入 `finally` 清理。

## 6. 实际问题与解决方案

### 问题 A：package re-export 会制造隐式重依赖

最初把 Redis adapter re-export 到 `infrastructure.persistence.__init__`。但原子 JSON 调用方也 import 这个 package，这会让它们无端加载 Redis 和 runtime domain。

进一步检查发现 `runtime/__init__` 本身也是 eager barrel；即使直接 import `runtime.approvals`，Python 仍先执行 package init 并加载 execution/config/sessions。

解决：

- persistence package 入口继续只暴露原子 JSON helper。
- bootstrap 直接 import 具体 Redis adapter。
- runtime package init 改成轻量说明文件，内部依赖都从 owning module 导入。

### 问题 B：TTL 不等于敏感数据已加密

批准后必须重放原始输入，因此当前 adapter 将原文保存在 Redis value 中，但从不写入结构化日志，并通过 TTL 限制逻辑保留时间。

这不等于加密或物理擦除：Docker Redis 开启 AOF，过期 key 的历史命令可能在 AOF rewrite 前仍出现在磁盘。生产环境应限制 Redis 访问、启用传输/磁盘保护，并在有明确密钥管理方案后增加 application-level encryption 或安全引用。该安全项仍保留为未完成任务。

### 问题 C：pending storage 不是审批审计日志

envelope 有 created/expires/status/schema version，但成功 `GETDEL` 后 pending record 会消失。它解决恢复和一次性消费，不保存 approved/rejected 的长期审计状态。

处理：继续依靠脱敏 structured event 记录 resolve 结果；若合规要求长期审计，应另建 append-only audit sink，不能把 pending key 兼作审计表。

### 问题 D：损坏 payload 的 resolve 行为

`get()` 会保留损坏值并抛明确 data error；`pop()` 的原子语义决定它先由 Redis 删除再在应用层反序列化，因此损坏值会被消费后报错。这样不会让一个坏 key 永久阻塞会话，但需要通过错误日志告警和人工重发原消息恢复。

## 7. 测试覆盖

- 两个 repository/client 共享 backend，模拟进程 A 写、进程 B 读。
- 两个线程并发 `GETDEL`，恰好一个返回 request。
- 两个独立 `ChatRuntime` 共享 Redis backend，B 可拒绝 A 创建的 guardrail request。
- TTL 参数、schema/status/created/expires envelope。
- 非法 JSON、未知 schema version、非正 TTL。
- factory 使用 `decode_responses=True` 且 runtime 退出关闭 client。
- production bootstrap 显式传递 Redis URL 和 TTL；`ChatRuntime()` 默认仍不联网。

## 8. 尚未完成

- 原始输入 application-level encryption / safe reference。
- approved/rejected append-only audit repository。
- input guardrail 与 tool HITL 更完整的 API-facing pending detail（当前共享 `pending_interrupt`，resume strategy 已分离）。
- 在真实 Redis 进程上验证自然过期时间与 AOF/重启行为；当前离线测试验证 `EX`、共享可见性和原子消费。

## 9. 验证

| 检查 | 结果 |
|---|---|
| durable approval/bootstrap/settings/SSE targeted tests | 33 passed |
| Ruff（本批文件） | passed |
| mypy（4 个直接 source，`--follow-imports=skip`） | passed |
| 全量 pytest | 189 passed，3 个第三方 deprecation warnings |
| 全量 Ruff | passed |
| frontend production build | passed，2013 modules transformed |
