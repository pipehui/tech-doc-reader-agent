# 2026-07-12：Assistant execution identity 与 trace metadata 收口

## 本批结论

本批完成 B6 的 trace identity 子项：

- 新增 immutable `AssistantExecutionIdentity`；
- role、prompt ID/hash、model provider、configured primary/backup model ID 成为一个值对象；
- `AssistantDefinition` 持有 identity，不再分别保存两个 prompt 字符串；
- runnable metadata 统一由 identity 生成，LangChain/Langfuse child run 可同时看到 prompt 与 model route；
- model factory 创建的真实 client model 参数与 provider 暴露的 model identity 由测试锁定；
- Assistant role 与 PromptArtifact role 不一致时在 composition 阶段立即失败；
- `AssistantRegistry.identities()` 以稳定 role 顺序暴露六个 identity，供后续 runtime manifest 使用。

本批没有宣称远程 eval identity 已完成；runner 仍缺少从“实际被测服务”读取 manifest 的握手。

## 重构前的真实问题

Prompt registry 已为六个角色提供稳定 ID 和 SHA-256，`build_assistant_definition()` 也把这些字段放入 runnable metadata。但身份仍被拆散在多个对象中：

- `AssistantDefinition.prompt_id`；
- `AssistantDefinition.prompt_sha256`；
- `Assistant.name`；
- `AssistantModelProvider.provider_id`；
- model factory 内部传给 `ChatOpenAI(model=...)` 的 primary/backup 字符串。

因此 trace 能定位 prompt，却不能从同一 metadata 判断配置的模型路由；如果以后增加 role-specific model，调用方还会继续复制这些字段。

另外，`build_assistant_definition(name="parser", prompt=primary_prompt)` 在类型上可通过，错误 role/prompt 组合不会在 graph 组装时被拒绝。

## Identity 结构

`AssistantExecutionIdentity` 包含：

```text
role
prompt_id
prompt_sha256
model_provider_id
primary_model_id?
backup_model_id?
```

约束：

- prompt ID 与 provider ID 必须是非空、trimmed string；
- prompt hash 必须是 64 位小写 SHA-256；
- model ID 为 `None` 或非空、trimmed string；
- metadata 不输出未配置的 model 字段，避免用 `unknown` 冒充真实配置。

`to_metadata()` 是 runnable trace 字段的唯一构造点。后续如果字段命名或版本变化，不需要同时修改六个 Assistant builder。

## Model route 与实际命中模型的边界

model factory 先解析最终 primary model ID，再使用同一个值：

```text
ChatOpenAI(model=primary_model_id)
AssistantModelProvider.primary_model_id
```

backup 只有在 `BACKUP_MODEL` 与 `BACKUP_API_KEY` 同时存在、真实 backup client 被创建时才进入 identity。

这里记录的是 configured route，不是“本次调用实际使用了哪个模型”。实际命中模型仍从 provider response metadata 进入 `LlmUsage`、budget telemetry 与 usage SSE；不能因为 trace 中存在 `backup_model_id` 就声称 fallback 已发生。

## 兼容策略

`AssistantDefinition` 改为持有 `identity`，但保留只读 `prompt_id` 与 `prompt_sha256` property。现有 composition、测试和可能的仓外只读调用不需要同步迁移；身份数据本身只有一个存储源。

没有在 `assistants/__init__.py` 增加 eager re-export，保持“导入 assistant_base 不加载全部角色定义”的既有架构门禁。

## 实施中遇到的问题

### 1. 不能用 runner 本地 settings 代表远端服务

eval runner 可以轻易读取本地 prompt manifest 和 `.env`，但它可能在机器 A 上评测机器 B 的 API。本地 fingerprint 看似完整，实际可能与被测 deployment 完全不同。

因此本批只建立服务端可复用的 typed identity；下一步应由被测 runtime 暴露 versioned、无 secret 的 identity manifest，再让 eval artifact 记录该响应。未完成握手前，TODO 保持未勾选。

### 2. configured backup 不代表 fallback usage

将 primary/backup 都写进 trace 有助于复现实验，但指标必须区分“配置存在”和“实际调用”。本批字段明确命名为 `primary_model_id/backup_model_id`，不使用 `model_used`；实际模型继续由 response usage metadata 负责。

### 3. 测试注入的 fake model 没有天然 model ID

`AssistantModelProvider` 仍允许测试只注入 runnable 而不伪造 model ID；identity 会省略未提供字段。需要验证 trace model 字段的测试显式注入 ID，避免让生产类型强迫所有轻量 fake 模拟第三方 client 属性。

## 验证状态

| 验证 | 结果 |
|---|---|
| assistant identity/registry/prompt targeted tests | 17 passed，2 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| assistants package Ruff | passed |
| assistants package mypy | 14 source files，0 issues |
| model client argument == exposed identity | primary/backup 均 passed |
| 全量 backend pytest | 614 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 全量 Ruff / mypy | passed；mypy 138 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未出现在 HEAD、origin/main 差异或待提交文件中 |

## 下一步

1. 定义 versioned runtime identity manifest，只包含 role/prompt hash/provider/model route/fingerprint，不包含 key、base URL 或 prompt 正文。
2. eval runner 从目标服务读取 manifest，并与 commit、dataset hash、settings fingerprint 一起写独立 artifact manifest。
3. role-specific model override 落地时，让每个 `AssistantExecutionIdentity` 记录该角色实际 configured route，不退回全局字符串复制。
