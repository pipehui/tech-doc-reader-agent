# 日志、Langfuse 与 Eval Artifact 统一脱敏

## 本批目标

R0 已阻止 raw exception message 进入 ToolMessage、SSE 和现有 telemetry，但业务字段仍可能携带凭据或 PII：trace context 有 user/session/namespace，任意 `log_event` field 可能包含 URL 或 header，Langfuse callback 自动捕获模型输入输出，eval/benchmark JSONL 会保存 query、answer 和最近 SSE payload。

如果每个出口各写一套正则，规则和 false-positive 修复会迅速漂移。本批实现 R4 P0 的可落地部分：一个纯核心 recursive redaction policy，被当前存在的日志、Langfuse、online/retrieval eval、concurrency benchmark 和 doc-seed artifact 共同调用。

## 最终边界

### 1. `RedactionPolicy` 是唯一规则源

`core/redaction.py` 同时支持：

- dict 字段名：Authorization/cookie、API key、password/secret、access/refresh/id token；
- 文本值：Bearer/Basic Authorization、JWT、OpenAI/Anthropic 风格 `sk-*`、Tavily、GitHub、Google、Slack key、通用 credential assignment、带密码 URI；
- 常见邮箱；
- 中国大陆手机号和带 `+` 国家码的国际号码；
- list/tuple/set/frozenset/mapping 递归处理；
- bytes 不转储；
- recursive container cycle 安全终止。

规则刻意不把所有 UUID 视为 PII。普通 trace/session UUID、版本号、token count、`sklearn` 和技术文本保持不变，并有 negative fixtures 保护。

### 2. 稳定假名必须使用受控 HMAC key

可选环境变量：

```bash
TELEMETRY_PSEUDONYM_KEY=<至少 16 字节的随机密钥>
```

配置后，名为 `user_id` 的字段使用 `HMAC-SHA256(scope + value)` 生成稳定 `pseudonym:<digest>`，便于跨事件关联。未配置时不对普通 opaque user id 做无密钥摘要；其中若包含邮箱/电话，仍由模式规则脱敏。

- Settings 对非空 key 强制至少 16 UTF-8 bytes；
- direct pseudonym API 同样拒绝空/短 key；
- 不使用无盐 SHA；
- 文档只称 keyed pseudonymization，不声称匿名化；
- session/trace UUID 不默认 pseudonymize。

### 3. 各出口复用同一 policy

| 出口 | 接入点 | 时机 |
|---|---|---|
| structured logs | `observability.log_event` | JSON serialization 前 |
| non-JSON log object | `_json_default` | `str(value)` 后再次做文本脱敏 |
| Langfuse | SDK `Langfuse(mask=policy.redact)` | SDK export 前，包括 callback input/output |
| agent eval JSONL | `evals.artifacts.write_jsonl` | 写文件前 |
| retrieval report/JSONL | shared artifact module | render/write 前 |
| concurrency benchmark | shared writer + safe console text | print/write 前 |
| doc seed run artifact | shared row redaction | append/print 前 |

Eval runner 的 judge 始终读取原始内存对象；只有控制台和持久化 artifact 脱敏。这样邮箱或 key 相关的 adversarial case 仍能正确评分，输出文件不会保留敏感原文。redaction 返回新容器，不修改 judge 使用的 rows。

当前仓库没有 replay recorder/sink。共享 policy 已可供 R6 使用，但本批不虚构“replay 已接入”，本地 TODO 的 replay 子项继续保留。

## 实际改动

### Core 与配置

- 新增 `RedactionPolicy`、`redact_text`、`pseudonymize` 和 `telemetry_redaction_policy`；
- Settings 增加 SecretStr 类型的 `TELEMETRY_PSEUDONYM_KEY` 和强度校验；
- `.env.example`、development/observability docs 说明 opt-in key；
- `log_event` 在统一 JSON encoder 前递归脱敏；
- Langfuse client 使用完全相同的 policy bound method 作为 SDK mask。

### Eval 与脚本

- 新增 `evals/artifacts.py`，集中 `write_jsonl/redact_artifact_rows/safe_artifact_text`；
- 删除 agent/retrieval runner 各自重复的 JSONL writer；
- Markdown report 函数自身先创建 redacted copy，避免调用者绕过；
- benchmark JSONL、query/error/recent-event console 统一脱敏；
- doc seed append/dry-run/topic/error output 统一脱敏；
- architecture test 防止 runner 恢复本地 writer。

## 实施中遇到的问题

### 问题 A：不能在 judge 前脱敏

最直接的实现是在 `run_case` 得到 event 后立刻 redact。但 boundary eval 正在检查回答是否泄露 key/system prompt；提前把 answer 改成 marker 会让 judge 得到假阴性或假阳性，评测失去意义。

处理：区分 computation data 与 external artifact。run/judge/summarize 使用原始 rows，`render_markdown_report` 和 `write_jsonl` 各自创建 redacted copy；测试断言原 rows 未被修改。

### 问题 B：Langfuse callback 自动采集，改 metadata 不够

只对 `langfuse_metadata` 调用 redact 无法覆盖 LangChain callback 自动上传的 prompt、tool input 和 model output。

处理：检查当前环境实际安装的 Langfuse 4.11.0 签名，确认 client 支持 `mask: MaskFunction`，并确认 CallbackHandler 通过 `get_client(public_key)` 复用 client。mask 在 client 初始化时注入；fake SDK 测试直接调用该 mask，验证 user pseudonym、Authorization redaction 和 UUID 保留。

### 问题 C：Authorization 正则误伤普通英文

首版 case-insensitive `Bearer <token>` 会把 “bearer capacity” 当凭据；过宽的 `Basic <word>` 也可能误伤技术说明。

处理：完整 `Authorization:`/`Authorization=` header 仍大小写不敏感；脱离 header 的 scheme 只匹配惯用大小写，并为 Bearer/Basic credential 设最小长度/Base64 形状。新增 “Basic concepts and bearer capacity” negative fixture，修复由测试驱动。

全量回归又发现宽泛的“三个长点分段”JWT 规则把真实事件名 `assistant.empty_response.exhausted` 误判成 JWT。处理：常见 JWT header 是 Base64URL JSON，收紧为 `eyJ...` 首段，同时把该事件名加入 negative fixture；日志事件语义恢复且真实 JWT fixture仍被拦截。

### 问题 D：pytest 通过，但直接脚本入口损坏

eval helpers 位于仓库根 package。pytest 会自动把 root 加入 `sys.path`，所以 import 测试全绿；实际运行 `python scripts/benchmark_latency.py --help` 时，Python 只把 `scripts/` 放入 import path，报 `ModuleNotFoundError: evals`。

处理：仅当脚本以文件入口启动（`__package__` 为空）时，把解析后的仓库根加入 path；作为 package import 时不修改。新增真实 subprocess `--help` 测试覆盖两个脚本入口。

### 问题 E：online eval 不止 `evals/run_eval.py`

进一步搜索 JSONL writer 后发现 concurrency benchmark 保存 query/recent payload，doc seed runner 也在 `eval_results/` 下追加 topic/error。只改两个 eval runner 会留下明显旁路。

处理：benchmark 复用 shared writer，seed append 使用 shared row redaction；所有动态 console 诊断也经过 safe text。源码扫描现在只剩共享 writer 和已先 redacted 的 seed append 写 JSONL。

### 问题 F：未知对象会绕过递归 policy

日志字段可能是 Path、SDK object 或其他非 JSON 类型，`json.dumps(default=str)` 会在 recursive redaction 之后才把对象变成文本，从而绕过字符串正则。

处理：`_json_default` 对 `str(value)` 再调用同一 `redact_text`；fixture 用自定义 object 返回 credential assignment，确认原文不进入最终日志。

## 测试与门禁

新增 positive fixtures 覆盖 Authorization、API key、JWT、URI credential、email、国内/国际 phone；negative fixtures 覆盖 UUID、sklearn、token counters、版本/时间、普通英文和短数字。另有 recursive container、cycle、sensitive key、HMAC 稳定性/不同 key、弱 key 拒绝、日志、Langfuse、eval JSONL/report、benchmark/seed architecture 与 direct CLI entrypoint 测试。

| 验证 | 结果 |
|---|---|
| 全量后端 pytest | 318 passed，4 个既有第三方/本机 cache warning |
| redaction/Langfuse/eval/script focused pytest | 52 passed |
| Ruff（含 `scripts/` 扩展范围） | passed |
| 既有 mypy gate | passed，12 source files |
| eval/scripts direct mypy（`--follow-imports=skip`） | passed，5 source files；2 条既有 untyped-body note |
| benchmark/seed direct `--help` | exit 0 / exit 0 |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码/样式改动，因此未重复浏览器视觉 smoke。第三方 LangGraph/Starlette warning 与本机 `.pytest_cache` 权限 warning 仍为既有环境提示。

## 保持不变与后续工作

保持不变：SSE 面向当前用户的业务内容、Agent/judge 的原始内存输入、eval 分数、Langfuse trace/session 关联、UUID traceability，以及未配置 pseudonym key 时普通 opaque user id 的值。

后续：R6 replay 实现时必须调用同一个 `RedactionPolicy`；R4 P2/P3 的确定性输出检查和模型型 factuality/safety judge 仍未开始。正则脱敏是 defense-in-depth，不替代最小采集、访问控制、加密和 TTL。
