# Phase 1 重构日志：拆分 services/utils.py

## 1. 重构范围

删除原 446 行 `app/services/utils.py`，按实际职责迁移到 graph package：

| 原职责 | 新位置 |
|---|---|
| entry/exit/finish/store-plan node factory | `graph/nodes.py` |
| 最后一条消息文本提取 | `graph/messages.py` |
| 重复 tool call 与 parser 检索预算 | `graph/tool_policy.py` |
| ToolNode sync/async wrapper、fallback、telemetry | `graph/tool_nodes.py` |

`builder.py` 现在只从 graph 内部模块组装节点，不再依赖名为 utils 的 service 聚合模块。

## 2. 实际遇到的问题

### 问题 A：关键 tool policy 原来没有直接测试

重复调用阻断和 parser 检索预算会直接影响 Agent 是否还能继续检索，但原测试只通过更上层行为间接覆盖，搜索不到针对两个函数的测试。

解决：迁移时新增纯 policy tests，固定：

- 第三次完全相同 tool + args 被阻断。
- 参数改变不算重复。
- parser 超过配置总调用数后阻断。
- 非 parser step 不套用 parser budget。

### 问题 B：是否需要兼容 re-export

TODO 原本计划先在 `services/utils.py` re-export 再删除。实际调用审计显示只有 graph builder 和一个结构化输出测试 import 该模块，没有外部稳定 API 或其他 service 使用。

解决：同一批迁移两个调用方并直接删除旧文件，避免为不存在的外部兼容需求保留一层长期 shim。删除前后均用全仓 `rg` 验证没有残留 import。

### 问题 C：sync/async ToolNode 仍有镜像代码

移动后 `tool_nodes.py` 仍分别实现 sync/async 执行和日志。这是已知重复，但若在“职责移动”同一提交中改执行模型，会扩大回归范围。

处理：本批只移动并测试 policy，保持执行语义；sync/async 收敛留到 runtime/tool execution 专门批次。

### 问题 D：错误 fallback 仍是自然语言

`handle_tool_error` 仍返回 `Error: repr(...)` 的 ToolMessage。它不具备结构化 code/retryable 分类。

处理：本批保持行为兼容；统一错误模型和 retry policy 已在可靠性 TODO 中单独排期。

## 3. 验证结果

| 检查 | 结果 |
|---|---|
| graph/tool-policy/structured-output 定向测试 | 36 passed |
| 全量 pytest | 168 passed，3 个第三方 deprecation warnings |
| 全仓 ruff | passed |
| graph direct mypy | passed，9 个 source files |
| frontend typecheck | passed |
| 旧 `services.utils` import 搜索 | 0 个 |

本批提交主题：`refactor: split graph nodes and tool policies`。

## 4. 后续工作

- tool policy 参数从函数默认值迁入 settings/spec policy。
- tool policy 返回 typed decision，而不是 `dict | None`。
- sync/async ToolNode wrapper 共用执行模板。
- 统一结构化错误模型后再增加 transport retry/reflection。
