# 2026-07-12：Runtime deployment commit identity

## 本批结论

本批把远端代码构建身份纳入 runtime/eval 证据链：

- `RuntimeExecutionIdentity` 从 schema v1 升级为 v2；
- v2 fingerprint 同时绑定 deployment commit、六个 prompt identity 与 model route；
- API/eval validator 仍能读取历史 v1，但 v1 因没有 deployment identity 只能用于诊断，不能通过严格 baseline compatibility；
- `DEPLOYMENT_COMMIT_SHA` 支持运行时显式注入，Docker image 支持 baked `IMAGE_COMMIT_SHA`；
- online report 展示 deployment status/commit；
- `--require-runtime-identity` 现在同时要求 runtime manifest 有效且 deployment commit 已配置；
- compatibility gate 会区分 deployment mismatch、deployment unavailable 与旧 schema 缺失。

服务端不会执行 `git rev-parse`，也不会用 runner 的本地 commit 猜远端 commit。

## Schema v2

新增字段：

```json
{
  "schema_version": 2,
  "deployment": {
    "status": "configured",
    "commit_sha": "<40-or-64-char-lowercase-git-sha>"
  },
  "assistants": [],
  "fingerprint": "<sha256>"
}
```

没有可验证 commit 时使用：

```json
{"deployment": {"status": "unavailable"}}
```

`configured` 必须携带完整 lowercase SHA；`unavailable` 禁止携带 SHA。Pydantic API schema、domain value object 与 Settings 都执行该约束。

## 单一 revision 规则源

初版实现时，完整 Git SHA 的 40/64 长度与 lowercase-hex 判断分别出现在 Settings、runtime identity 和 eval helper。这会让未来 SHA-256 Git repository 支持或错误文案修正发生漂移。

本批在自审中将规则收口到 `core/revisions.py`：

- `FULL_GIT_COMMIT_PATTERN` 供 Pydantic response schema 使用；
- `is_full_git_commit_sha()` 供 Settings、domain identity、eval manifest/provenance 复用；
- runner commit 与 deployment commit 使用同一个格式定义，但在 manifest 中仍是两个不同职责字段。

## 配置优先级与 Docker bake

identity 使用：

```text
DEPLOYMENT_COMMIT_SHA (runtime override)
  -> IMAGE_COMMIT_SHA (image build metadata)
  -> unavailable
```

两者同时存在时必须相同；冲突会在 Settings validation 阶段阻止启动，避免一个 runtime 用 override 静默掩盖镜像实际 metadata。

Docker Compose 把宿主的 `DEPLOYMENT_COMMIT_SHA` 作为 build arg `IMAGE_COMMIT_SHA`；Dockerfile 在应用代码复制后写入 ENV，避免 commit 变化使 Python/Node dependency layer 无谓失效。

这里没有直接把 build arg 写成 `DEPLOYMENT_COMMIT_SHA`。原因是常见 `.env` 模板会保留空的 runtime override；容器的 `env_file` 空值可能覆盖镜像中已 bake 的同名 ENV。分离 runtime override 与 image metadata 后，即使 `.env` 中 override 为空，仍能回退到镜像 commit。

## Compatibility 语义

online baseline/candidate：

- deployment commit 相同、runtime fingerprint 相同、runner provenance clean：`compatible`；
- deployment commit 不同：`incompatible`，issue code 为 `deployment_commit_mismatch`；
- 两边都没有 deployment commit：`unverified`，不是 compatible；
- 历史 schema v1：合法可读取，但 `deployment_identity_missing`，结果 `unverified`；
- 一边 configured、一边 unavailable：已知 workload identity 状态不同，结果 `incompatible`。

runner commit 仍允许 baseline/candidate 不同，因为它们代表要比较的代码 before/after；远端 deployment commit 必须相同，才能把指标变化归因 runner 代码或 case orchestration，而不是被测目标换版本。

## 实施中遇到的问题

### 1. `LANGFUSE_RELEASE` 不能代替 deployment commit

它是 telemetry grouping 配置，可使用版本标签或任意 release 名，且没有完整 Git SHA 约束。复用它会耦合观测命名和构建身份，并可能产生假精度，因此新增独立字段。

### 2. 直接把可选字段塞进 v1 会破坏版本语义

deployment 会改变 canonical fingerprint 和“可比较”的定义。最终生成端明确升级到 v2；读取端使用条件 schema 同时接受严格 v1/v2，避免升级 runner 后立刻无法读取旧服务，又不会把旧服务误判为已验证。

### 3. Runtime identity available 不等于 deployment 已验证

只检查 endpoint HTTP 200 会让 `deployment.status=unavailable` 通过受信 eval。现在默认运行仍可生成诊断 artifact，但 `--require-runtime-identity` 会在 case 前返回 exit code 2；compatibility gate 同样返回 `unverified`。

## 验证状态

| 验证 | 结果 |
|---|---|
| settings/identity/API/runtime/eval targeted tests | 86 passed，3 个既有 dependency deprecation warning |
| targeted Ruff / mypy | passed |
| Docker Compose config | passed |
| Docker image build | not run；Docker Desktop Linux engine 当前未运行 |
| 全量 backend pytest | 658 passed，3 个既有 dependency deprecation warning |
| 全量 Ruff / mypy | passed；mypy 143 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未进入版本树或待提交差异 |

## 后续约束

1. 实际 deployment workflow 必须传完整 commit；代码支持不等于每个环境已经配置。
2. 如果未来使用非 Git artifact digest，应新增独立、版本化字段，不放宽 `commit_sha` 接受任意 tag。
3. production identity endpoint 仍需受信网络或鉴权；commit SHA 不是 secret，但 prompt/model inventory 仍属于运维元数据。
