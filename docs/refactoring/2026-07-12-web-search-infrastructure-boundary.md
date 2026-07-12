# Web Search Infrastructure 边界

## 本批结论

本批将旧 `services/vectordb` 中最后一个 concrete implementation 归位：

- `services/vectordb/web_search_backend.py` -> `infrastructure/retrieval/web_search.py`；
- `tests/test_web_search_backend.py` -> `tests/test_web_search.py`；
- resource factory 与 retry wiring allowlist 使用新路径；
- 删除空的 `services/vectordb/__init__.py`，该 package 不再包含 tracked code；
- 新增 ownership gate，禁止 resource factory 或旧路径重新依赖 vectordb；
- Tavily/DuckDuckGo module monkeypatch 迁到新 concrete module；
- API/health/resource 字段 `web_search_backend` 保持不变。

Provider 顺序、daily Tavily quota、local usage cache、RetryExecutor、fallback provenance、typed errors 和 tool port 行为均未改变。

## 为什么属于 Retrieval Infrastructure

WebSearchBackend 的职责是实现外部检索 capability：

```text
WebSearchPort
  <- WebSearchBackend
       -> Tavily provider
       -> DuckDuckGo fallback
       -> provider retry executor
       -> daily usage state / atomic JSON cache
```

它没有向量索引、embedding 或 vector database 语义。旧目录名把“网页搜索 provider”与“向量存储”错误地绑定在一起，也让 services 成为没有稳定含义的兜底目录。

迁入 `infrastructure/retrieval` 后，tools 继续只依赖 `WebSearchPort`，composition resource factory 选择 concrete adapter，infrastructure contract 保证 adapter 不反向依赖 services、graph、runtime、API、tools 或 agents。

## 保持不变的外部行为

- 有 Tavily key 且未超 daily limit 时先调用 Tavily；
- Tavily typed failure/retry exhausted 后按既有策略尝试 DuckDuckGo；
- provider 均失败时返回稳定 `DependencyUnavailable`/`RateLimited` 等应用错误；
- Retry-After、attempt count、wait、recovered/exhausted 进入既有 retry ledger；
- usage state 继续写入 `{DATA_PATH}/web_search/tavily_usage.json`；
- usage cache 继续使用 atomic JSON adapter 与进程内 lock；
- query/result shape 与 Tool WebSearchPort 不变；
- health endpoint 的 `web_search_backend` component name 不变。

内部模块文件改名为 `web_search.py`，但没有机械重命名 API JSON、resource attribute 或 telemetry 中已形成契约的 backend 字段。

## Compatibility 策略

旧 `tech_doc_agent.app.services.vectordb.web_search_backend` 没有 facade。该路径是 concrete adapter implementation，不是 application/tool/API contract；仓内唯一 production caller、测试与 retry guardrail 已全部迁移。

保留空 `services.vectordb` package 只会继续暗示新 provider 应放入该目录。稳定依赖面是 `WebSearchPort`；需要显式构造 concrete adapter 的 composition 代码使用：

```python
from tech_doc_agent.app.infrastructure.retrieval.web_search import WebSearchBackend
```

## 实施中遇到的问题

### 问题 A：测试依赖 module object，而不只依赖 class

Web search tests 会 monkeypatch 模块级 `TavilyClient`、`DDGS` 和 clock/settings helper。只更新 class import 会让 patch 仍指向已删除模块，可能误触真实 provider。

处理：测试同时导入新 `web_search` module object 与 class，所有 patch target 跟随 concrete implementation；定向测试在无网络下覆盖 fallback 与错误路径。

### 问题 B：删除 tracked package 不等于磁盘目录立即消失

运行测试后旧目录可能仍有 ignored `__pycache__`，用 `Test-Path services/vectordb` 作为架构 gate 会在不同机器产生不稳定结果。

处理：ownership test 检查旧 Python implementation 文件不存在，Git 审计检查该路径无 tracked code；不把运行时 cache 当作源码。

### 问题 C：内部重命名不能扩散到 delivery contract

Health API 与 resource bundle 使用 `web_search_backend` 字段。为了“命名统一”把它们同时改成 `web_search` 会形成无关的 schema 变更。

处理：只调整 Python module ownership，外部字段和 tool dependency name保持；health/tool tests继续锁定现有输出。

### 问题 D：Retry 安全门禁保存精确路径

和 embedding 相同，WebSearchBackend 是允许使用有限 transport retry 的少数模块之一。移动后 retry allowlist 必须精确迁移，不能扩大到整个 infrastructure 目录。

处理：将单一路径更新为 `app/infrastructure/retrieval/web_search.py`，保留“tool node/写路径不得随意包 retry”的集合等式测试。

### 问题 E：Provider cache 是 adapter state，不应搬进 application

Tavily daily call count、文件路径、atomic JSON 与 proxy 都是 concrete provider 细节。把 WebSearchBackend 放 application 会让用例层依赖 filesystem/provider SDK。

处理：完整 adapter 留在 infrastructure，tools/application 只保留窄 port；本批不拆出没有第二实现需求的抽象 factory。

## 验证范围

定向验证覆盖 Tavily success/failure/rate limit、DuckDuckGo fallback、双 provider failure、daily usage cache、retry usage、resource/tool/health wiring 与 architecture contract。

| 验证 | 结果 |
|---|---|
| web-search/resources/retry/tools/health/architecture targeted pytest | 87 passed；4 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 702 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 150 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- Circuit breaker、half-open probe 和跨 worker shared state 仍未设计，不能把 transport retry/fallback 当作 breaker；
- Web fallback provenance 已有 provider result/telemetry，但跨所有 ToolMessage/最终答案的一致 provenance 仍需另批验证；
- usage cache 只有进程内 lock，多 worker 下 daily quota 不是强一致全局计数；
- provider routing、timeout 与 proxy 仍由 Settings 直接配置，出现更多 provider 后再评估 registry；
- `services` 现在只剩 resource container、user-profile compatibility 与 retrieval package facade，下一步可处理 concrete resource factory 归位。
