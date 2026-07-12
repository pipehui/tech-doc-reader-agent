# 2026-07-12：Online eval run manifest 与远端 identity 握手

## 本批结论

本批完成 B6 的 online eval identity 闭环：

- online agent eval 在执行 case 前读取实际目标服务的 `/runtime/identity`；
- 客户端重新校验 Pydantic schema、六个 role 顺序与 canonical fingerprint；
- 不可用状态明确区分 `disabled`、`unavailable` 与 `invalid`；
- 任何失败都不会回退 runner 本地 `.env` 或 prompt manifest；
- 每次 run 写独立 schema version 1 JSON manifest；
- manifest 绑定 runner git commit/dirty、dataset SHA-256、eval settings fingerprint 与远端 runtime identity；
- Markdown report 同时展示 identity 状态/fingerprint、dataset/settings hash 与 runner commit；
- `--require-runtime-identity` 可在受信 baseline/CI 中把缺失 identity 变成 case 执行前的退出码 2；
- JSON/JSONL 继续共用同一 artifact redaction boundary。

本批没有运行 live model case；用 `--limit 0` 完成真实 CLI/网络失败 smoke，验证无服务时 manifest 仍可审计地产生。

## Run manifest

结构：

```text
schema_version
runner
generated_at
runner_git
  commit
  dirty
dataset
  name
  sha256
settings
  values
  fingerprint
runtime_identity
  status
  manifest? / cause_type?
```

### Runner git

通过只读 `git -C <repo> rev-parse HEAD` 与 `status --porcelain` 记录 runner checkout。命令失败时写 `null`，不把 stderr 或文件列表写入 artifact。

该 commit 只代表 runner 代码，不代表远端 deployment commit。服务端 manifest 当前没有 deployment commit，因此比较报告时不能把两者混为一谈。

### Dataset

只记录 dataset 文件名与原始 bytes 的 SHA-256，不记录绝对路径。相同 case 内容跨机器得到相同 hash，避免泄露用户目录。

### Eval settings

记录会影响结果的 timeout、limit、disabled-case 策略、interrupt policy、最大审批轮数与 require-identity 策略。feedback 只记录 SHA-256。

endpoint 只记录：

- scheme；
- host SHA-256；
- path。

URL userinfo、password、query token 与原始 host 不进入 artifact。settings fingerprint 对这份安全 canonical payload 计算。

### Runtime identity

状态：

| 状态 | 含义 |
|---|---|
| `available` | HTTP 200，schema/roles/fingerprint 全部验证通过 |
| `disabled` | HTTP 404，目标默认关闭 identity endpoint |
| `unavailable` | 网络失败、503 或其他非 200/404 状态 |
| `invalid` | JSON、schema、role order 或 fingerprint 不合法 |

只有 `available` 会携带远端 manifest。其他状态只保留安全 `cause_type`，不写 response body 或异常原文。

## URL 推导单一来源

`approve_url_for()` 与 `identity_url_for()` 归入 shared manifest module。online execution 和 manifest settings 使用同一推导结果，不再一处请求 `/chat/approve`、另一处记录猜测 URL。

推导使用 URL parser 替换 path，并保留请求所需的 query/auth 语义。artifact 再单独安全投影，不保存凭据。

## 实施中遇到的问题

### 1. 带 query token 的 API URL 破坏字符串后缀判断

初版对完整 URL 使用 `endswith('/chat')`。当 URL 为：

```text
https://host/api/chat?token=...
```

判断失败，`/runtime/identity` 被拼到 query 后，实际 path 仍是 `/chat`。专项测试捕获后，改为 `urlsplit/urlunsplit` 结构化替换 path，并增加带 userinfo/query 的回归测试。

### 2. HTTP response 的 `null` 会让 fingerprint 无法重算

服务端 canonical identity 对未配置 model ID 使用字段缺省；Pydantic response 若自动补 `null`，客户端直接重算会得到不同 hash。

服务端 endpoint 使用 `response_model_exclude_none=True`，客户端测试从真实 HTTP response 删除 fingerprint 后重新 canonicalize，证明 hash 一致。

### 3. Manifest builder 不能假设调用方永远传安全 settings

online settings helper 当前只输出 hash/安全字段，但 shared builder 未来可能被其他 runner 使用。最终 builder 在计算 settings fingerprint 前也走 shared redaction；`write_json()` 再在落盘边界执行同一策略，形成 defense in depth。

### 4. Identity 缺失不应默认阻断开发回归

endpoint 默认关闭，本地开发可能只想跑行为 case。如果默认强制 identity，会破坏现有工作流；如果静默忽略，又会产生假可复现报告。

最终默认继续执行但 manifest/report 明确状态；受信 baseline 使用 `--require-runtime-identity` 主动升级为阻断。

## 真实 CLI smoke

执行：

```powershell
python -m evals.run_eval `
  --cases evals/cases.json `
  --limit 0 `
  --api-url http://127.0.0.1:1/chat `
  --timeout 1 `
  --output <temp>/results.jsonl `
  --report <temp>/report.md `
  --manifest <temp>/manifest.json
```

结果：

- exit code 0；
- 没有执行模型 case；
- runtime identity 为 `unavailable`，cause type `ReadError`；
- dataset hash、safe endpoint projection、settings fingerprint、runner commit 与 `dirty=true` 均写入；
- 没有读取本地 model/prompt identity 作为 fallback。

另以相同不可用目标加入 `--require-runtime-identity`：进程按预期返回 exit code 2，manifest 已写入，results/report 未生成，证明阻断发生在 case 执行前。

## 验证状态

| 验证 | 结果 |
|---|---|
| manifest/fetch/runner/artifact targeted tests | 34 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| eval manifest/run_eval Ruff | passed |
| eval manifest/run_eval mypy | 3 source files，0 issues |
| `--limit 0` unavailable-target CLI smoke | passed |
| `--require-runtime-identity` pre-case block smoke | exit 2；manifest yes；results no |
| 全量 backend pytest | 631 passed，3 个既有 deprecation warning + 1 个本机 pytest cache 权限 warning |
| 全量 Ruff / mypy | passed；mypy 139 source files，0 issues |
| 全量前端 test/check/build/audit | 20 files / 85 tests；2042 modules；0 vulnerabilities |
| `git diff --check` | passed |
| `docs/todo` 隔离 | passed；目录受 `.gitignore` 命中，未出现在 HEAD、origin/main 差异或待提交文件中 |

## 下一步

1. 把 shared run manifest 接入 retrieval 与 context-compaction runner；offline runner 的 runtime identity 应明确为 `not_applicable`，不要伪造远端配置。
2. baseline comparison 先检查 dataset/settings/runtime fingerprint compatibility，再计算代码回归 delta。
3. 通过 deployment build metadata 扩展服务端 manifest，区分 runner commit 与远端 commit。
4. E2 继续记录 actual provider/model usage、retry/fallback 成本；configured route 不能替代实际调用指标。
