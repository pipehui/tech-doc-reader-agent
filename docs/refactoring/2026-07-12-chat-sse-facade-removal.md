# Phase 4 重构日志：删除 Chat Route 的 SSE 兼容出口

## 1. 重构范围

本批收束 [SSE contract、翻译与编码边界](2026-07-11-sse-boundary.md) 拆分时留下的临时兼容层：

- `tests/test_sse_events.py` 与 `tests/test_observability.py` 改为直接从 `tech_doc_agent.app.api.sse` import SSE helper。
- 删除 `api/routes/chat.py` 的 SSE helper `__all__`。
- route 不再用 `from ...api.sse import helper` 把 helper 带入自身 module namespace，而是通过私有模块依赖 `_sse` 调用真实实现。
- 增加 architecture contract，禁止 `chat.py` 恢复 helper 直导入或 `__all__` 兼容出口。

外部 HTTP endpoint、SSE event name、payload、编码格式与执行顺序均未改变。`chat.py` 从本批开始不再承担 SSE facade 职责，`api/sse/` 是唯一事实源。

## 2. 依赖变化

```text
before
tests/other callers --> api.routes.chat --compat export--> api.sse
api.routes.chat --------------------------runtime use-----> api.sse

after
tests/other callers --------------------------------------> api.sse
api.routes.chat ----------------private module dependency-> api.sse
```

仅删除 `__all__` 并不能彻底删除兼容出口：Python 仍允许调用方显式 import route module namespace 中的 imported name。因此 route 采用 `from tech_doc_agent.app.api import sse as _sse`，调用点写成 `_sse.sse_event(...)`、`_sse.aiter_with_trace_context(...)` 等。这样既保留 route 对 SSE 能力的真实依赖，也不再把每个 helper 暴露为 `routes.chat` 的同名属性。

## 3. 实际遇到的问题与解决

### 问题 A：第一次清理误删了 route 的真实依赖

最初根据临时 `__all__` 清单删除 import 时，把 `aiter_with_trace_context` 误判成只供测试使用的兼容名。定向门禁立即出现 5 个失败：4 个 chat/guardrail route 测试在运行时抛出 `NameError`，另一个新 architecture test 因为错误地禁止源文件出现 helper 名称而失败。

实际调用审计显示：

- `aiter_with_trace_context` 在普通 chat、中风险 guardrail 等 3 个 response path 中真实使用；
- `astream_parts_as_sse`、`event_source_response`、`sse_event` 也由 route 真实消费；
- `iter_update_events`、`iter_with_trace_context`、`stream_parts_as_sse` 才是仅由旧测试通过 route 间接 import 的名字。

解决：恢复真实能力依赖，但改成 `_sse.<helper>` 私有模块调用；architecture test 约束“不得直导入/再导出”，不再错误约束“route 不得调用”。修正后的同一组定向测试为 78 passed。

### 问题 B：测试 import 不等于 route 应承诺公共 Python API

旧测试从 `routes.chat` import helper，会让临时迁移措施看起来像需要长期维护的 API。但项目公开边界是 FastAPI endpoint 与 SSE wire contract；helper 实现属于 `api.sse`，仓内没有生产调用方依赖旧路径。

解决：测试与 observability 测试直接依赖事实源，同时用 architecture contract 固化路径。对于未知的仓外 Python import，本批不声明无条件兼容；若未来确需发布 Python SDK，应在独立稳定 package 中设计 public surface，而不是借 HTTP route module 暴露内部 helper。

## 4. 修改文件

- `tech_doc_agent/app/api/routes/chat.py`
- `tests/test_sse_events.py`
- `tests/test_observability.py`
- `tests/test_architecture_dependencies.py`
- `docs/architecture.md`
- `tech_doc_agent/README.md`
- `docs/refactoring/2026-07-11-sse-boundary.md`

`chat.py` 从 350 行降为 345 行。更重要的变化不是行数，而是依赖语义：route 是 SSE consumer，不再同时扮演 SSE compatibility facade。

## 5. 验证结果

| 检查 | 结果 |
|---|---|
| SSE/observability/architecture 定向测试 | 78 passed，4 warnings |
| 全量 pytest | 710 passed，4 warnings |
| 全仓 Ruff | passed |
| app + evals mypy | passed，151 个 source files |
| 前端 Vitest | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | passed，2042 modules transformed |
| npm audit | 0 vulnerabilities |

四条 pytest warning 中三条来自 LangGraph/LangChain/Starlette 的第三方弃用提示；另一条是工作区 `.pytest_cache` 无写权限。测试用例自身全部通过。

## 6. 后续约束

- 新 SSE event、translator 或 encoder 必须加入 `api/sse/`，不能放回 route。
- route 可以消费 SSE public surface，但只能通过私有模块依赖，不得重新形成 helper facade。
- 跨端 payload 变化继续由 Pydantic/TypeScript contract 与 golden parity tests 共同守卫。
- 后续拆分 guardrail/chat/approval application use case 时，保持 HTTP route 与 SSE protocol 两条边界独立。

本批提交主题：`refactor: remove chat SSE compatibility exports`。
