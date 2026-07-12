# 2026-07-12：Versioned runtime identity manifest

## 本批结论

本批把上一阶段的单 Assistant identity 提升为部署级可复现身份：

- 新增 `ModelRouteIdentity`，model factory 与 manifest 共用同一个 active route 解析函数；
- 新增 schema version 1 的 `RuntimeExecutionIdentity`；
- 六个 Assistant identity 以固定 role 顺序聚合并计算 canonical SHA-256 fingerprint；
- `ChatRuntime` 启动时构建一次 identity，所有 graph config 的 root metadata 携带同一 manifest；
- 新增 `GET /runtime/identity`，供受信 eval/运维客户端读取目标服务事实；
- endpoint 默认关闭，必须显式设置 `RUNTIME_IDENTITY_ENDPOINT_ENABLED=true`；
- API response 由 Pydantic schema 校验，且不包含 prompt 正文、API key 或 provider base URL。

eval runner 尚未消费此 endpoint；本批只建立安全、可验证的服务端事实源。

## 为什么不能让 eval runner 直接读本地配置

live eval 的 runner 与被测 API 可能不在同一机器、容器、commit 或 `.env`。runner 本地读取到的：

```text
PRIMARY_MODEL
BACKUP_MODEL
prompt manifest
```

不能证明远端 deployment 使用相同值。把本地 fingerprint 写入报告会制造比“没有 identity”更危险的假可复现性。

因此 identity 必须由实际被测 runtime 生成，再由 eval 客户端读取和固化。

## Manifest 结构

schema version 1：

```text
schema_version
fingerprint
assistants[]
  assistant_role
  prompt_id
  prompt_sha256
  model_provider_id
  primary_model_id?
  backup_model_id?
```

fingerprint 对不含 fingerprint 自身的 canonical JSON 计算 SHA-256：UTF-8、key sort、固定 separators。相同 prompt/model route 产生相同 fingerprint；active primary model、active backup model 或 prompt hash 变化都会改变 fingerprint。

HTTP response 对未配置的 optional model ID 使用“字段缺省”而不是 `null`，与 fingerprint 输入完全一致；component test 会从真实响应移除 fingerprint、重新 canonicalize 并验证 hash 相等。

backup 只有在 model ID 与 API key 同时配置、真实 backup client 会被创建时才属于 active route。仅填写 `BACKUP_MODEL` 但没有 key，不影响 fingerprint。

## 单一来源

### Model route

`build_model_route_identity(settings)` 同时服务：

- `ChatOpenAI(model=...)` client construction；
- `AssistantModelProvider` trace fields；
- `RuntimeExecutionIdentity` manifest。

测试精确比较真实传给 primary/backup client 的 `model` 参数与暴露 identity，避免“client 用 A、manifest 写 B”。

### Prompt identity

runtime manifest 直接从已校验的 `PromptRegistry` 读取六个 PromptArtifact。它不重新读取或重新计算另一套 ID 规则；manifest 只聚合 registry 已验证的 ID/hash。

### Root trace

`SessionConfigFactory` 接受普通 `Mapping`，不反向依赖 assistants service。`ChatRuntime` 把 versioned payload 注入 `runtime_execution_identity` metadata；runtime 层仍保持 `runtime -> services` 禁止依赖方向。

child runnable 继续携带当前 role 的精确 identity，root trace 携带整个 deployment fingerprint/manifest。

## 安全边界

manifest 明确排除：

- primary/backup API key；
- provider base URL；
- prompt system template 与资源正文；
- tenant、session、用户输入与工具结果。

测试使用私密哨兵 key/base URL，并断言它们不出现在 payload 或 HTTP response。

即便如此，model ID 与 prompt ID 仍属于部署元数据。当前项目还没有管理员 AuthN/AuthZ，因此 endpoint 默认关闭；关闭时返回 `404`，runtime 未就绪时返回 `503`。生产环境只应在受信内网或受保护网关后临时/显式启用。

## 实施中遇到的问题

### 1. Runtime 原来会丢失 registry identity

composition 构造 PromptRegistry、AssistantRegistry 和 graph 后，只保留 compiled graph；delivery/runtime 无法从 graph 稳定提取原始 identity。尝试依赖 compiled graph 私有属性会把复现协议绑到 LangGraph 实现细节。

最终用同一组纯构造规则在 `ChatRuntime` 启动时生成 deployment identity。prompt registry 与 model route 都有单一解析源，结果由 fingerprint 和 client-argument tests 约束。

### 2. 兼容测试会在构造后重新赋值 settings

已有测试和部分本地调用先构造 `ChatRuntime()`，再替换 `runtime.settings`。如果 identity 只在 `__init__` 计算，会出现 config 用新 settings、manifest 仍是旧 model 的漂移。

为保持兼容，settings setter 在没有显式 identity override 时同步重建 identity；生产正常路径仍只在启动时构建一次。显式注入 identity 的测试/特殊 runtime 不会被后续 settings 赋值悄悄覆盖。

### 3. 不能公开 endpoint 后再等待 Auth 补救

最初方案直接暴露无 secret manifest；审查后确认这仍会增加部署侦察面。最终在同批加入 default-off 配置，而不是把安全约束留成文档建议。

### 4. Configured route 仍不是 actual model usage

manifest 用于复现实验配置，不用于统计 fallback 命中。实际模型、token 与成本继续来自 provider response metadata 和 `LlmUsage`；后续 eval artifact 应同时保留 runtime identity 与实际 usage 指标。

## 验证状态

| 验证 | 结果 |
|---|---|
| identity/registry/config/API/architecture targeted tests | 57 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 修改范围 Ruff | passed |
| identity/runtime/config/API/settings mypy | 19 source files，0 issues |
| fingerprint deterministic/change/inactive-backup cases | passed |
| endpoint enabled/disabled/runtime-missing cases | passed |
| secret/base URL/prompt content absence | passed |
| 全量 backend pytest | 621 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 全量 Ruff / mypy | passed；mypy 138 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未出现在 HEAD、origin/main 差异或待提交文件中 |

## 下一步

1. eval runner 在执行 case 前读取目标 `/runtime/identity`，失败时明确 `not_available/disabled`，不能回退到本地 settings 并假装等价。
2. 写独立 eval manifest：远端 runtime identity、git commit、dataset SHA-256、runner settings fingerprint、时间与 endpoint。
3. baseline 比较先要求 identity/dataset/settings 兼容；不兼容时标记为环境变化，不直接归因代码回归。
