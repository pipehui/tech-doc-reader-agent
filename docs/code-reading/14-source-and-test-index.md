# 14 - 源码、函数与测试索引

本章是查表页。想理解完整链路，从 [README](README.md) 选择阅读路线；已经知道文件/症状时，直接在这里找 owner、主要符号、消费者和测试。

## 按问题快速定位

| 你看到的问题 | 第一份源码 | 然后看 | 首选测试 |
| --- | --- | --- | --- |
| 服务起不来 | `runtime/lifecycle.py` | `bootstrap.py`, `resources.py`, `server.py` | lifecycle/bootstrap/resources |
| `/chat` 不是 SSE | `api/chat_delivery.py` | route/schema/guardrail | API schema + SSE tests |
| SSE 字段不匹配 | `api/sse/payloads.py` | frontend `ssePayloads.ts` | `test_sse_contract.py` + Vitest payload |
| session 恢复错 | `runtime/sessions.py` | config/serialization/frontend bootstrap | runtime query + session bootstrap |
| 审批卡住 | `runtime/execution.py` | application approval service/repository/checkpoint；`runtime/approvals.py` 仅兼容入口 | runtime approval tests |
| Agent 路由错 | `graph/routing.py` | builder/specs/commands | route + topology + compile |
| 工具重复/循环 | `graph/tool_policy.py` | reflection/message_scope | tool policy + reflection |
| 预算提前停 | `graph/budgeting.py` | core execution budget | execution + graph budget |
| tool 不存在/权限错 | `agents/definition.py` | registry/tool bundle/graph spec | tool matrix + registry + compile |
| 学习数据重复 | `application/learning_unit_of_work.py` | command/service/repository | transaction + model tests |
| 重启后数据丢 | snapshot repository | generation/atomic JSON | persistence + legacy migration |
| 本地检索空 | `retrieval/hybrid.py` | FaissStore/metadata/rankers | hybrid/ranker/metadata |
| vector 失败但 hybrid 有结果 | `retrieval/semantic.py` | embedding/FaissStore | embedding + hybrid degradation |
| 前端文本重复 | `streaming/sseReducer.ts` | transcript slice/chat stream | reducer + integration |
| 切会话串消息 | `useSessionBootstrap.ts` | session/transcript repository | bootstrap + storage tests |
| 分层依赖反向 | architecture tests | owner/compat facade | architecture dependency/import graph |

## Composition roots 与进程入口

| 文件 | 主要符号/职责 | 直接消费者 | 对应章节/测试 |
| --- | --- | --- | --- |
| [`app/main.py`](../../tech_doc_agent/app/main.py) | CLI loop、`build_chat_runtime`、审批输入 | 命令行用户 | [01](01-startup-and-composition.md), script entrypoint |
| [`app/api/server.py`](../../tech_doc_agent/app/api/server.py) | FastAPI app、lifespan、router/CORS/static 安装 | ASGI server | [01](01-startup-and-composition.md), health/static |
| [`app/bootstrap.py`](../../tech_doc_agent/app/bootstrap.py) | `build_chat_runtime`，选择 concrete resources/Redis approval/lifecycle | API/CLI | bootstrap, architecture |
| [`app/composition.py`](../../tech_doc_agent/app/composition.py) | `CompositionResources`、tool/agent/spec/graph 组装 | bootstrap/tests | composition, graph compile |
| [`infrastructure/resources.py`](../../tech_doc_agent/app/infrastructure/resources.py) | `AppResources.create`，并在创建时加载/初始化 store、service、provider aggregate | lifecycle/bootstrap | resources, persistence/retrieval |

## Core：无业务/adapter 依赖的基础 contract

| 文件 | 所有权 | 关键符号 | 主要测试 |
| --- | --- | --- | --- |
| [`core/settings.py`](../../tech_doc_agent/app/core/settings.py) | 配置 schema/cache | `Settings`, `get_settings` | `test_settings.py` |
| [`core/tenant.py`](../../tech_doc_agent/app/core/tenant.py) | tenant 值、严格/宽松解析、config lookup | `TenantContext`, `parse_tenant`, `normalize_tenant`, `tenant_from_config`, `tenant_thread_id` | `test_tenant.py`, runtime config |
| [`core/errors.py`](../../tech_doc_agent/app/core/errors.py) | typed safe error taxonomy | `ApplicationError`, subclasses, `classify_error`, `safe_error_fields` | `test_error_model.py` |
| [`core/redaction.py`](../../tech_doc_agent/app/core/redaction.py) | 递归凭证/PII 清洗与 user pseudonym | `RedactionPolicy`, `redact_text`, `pseudonymize` | `test_redaction.py` |
| [`core/observability.py`](../../tech_doc_agent/app/core/observability.py) | trace ContextVar、结构化 event、node timing | `trace_context`, `log_event`, `timed_node` | `test_observability.py` |
| [`core/logger.py`](../../tech_doc_agent/app/core/logger.py) | 旧式 CLI logger 配置 | `logger` | CLI/import smoke；新结构化路径用 observability |
| [`core/retry.py`](../../tech_doc_agent/app/core/retry.py) | 同步/异步有限 transport retry | `RetryPolicy`, `RetryExecutor`, `build_retry_executor` | `test_retry.py` |
| [`core/retry_usage.py`](../../tech_doc_agent/app/core/retry_usage.py) | retry operation ledger/ContextVar collector | `RetryUsage`, `RetryUsageLedger`, `capture_retry_usage` | `test_retry_usage.py` |
| [`core/budget.py`](../../tech_doc_agent/app/core/budget.py) | LLM/workflow usage typed state | `LlmUsage`, `BudgetUsage` | `test_budget_usage.py` |
| [`core/model_pricing.py`](../../tech_doc_agent/app/core/model_pricing.py) | model/token 成本估算 | `ModelPriceTable`, `CostEstimate` | `test_model_pricing.py` |
| [`core/execution_budget_models.py`](../../tech_doc_agent/app/core/execution_budget_models.py) | request window、decision、termination error contract | `RequestBudgetWindow`, `BudgetDecision`, `ExecutionBudgetExceeded` | execution budget tests |
| [`core/execution_budget.py`](../../tech_doc_agent/app/core/execution_budget.py) | before/after/resume 预算决策 | `ExecutionBudget`, `build_execution_budget` | `test_execution_budget.py` |
| [`core/context_metrics.py`](../../tech_doc_agent/app/core/context_metrics.py) | context snapshot/累计指标 typed state | `ContextSnapshot`, `ContextMetrics` | `test_context_metrics.py` |
| [`core/context_serialization.py`](../../tech_doc_agent/app/core/context_serialization.py) | 可恢复的序列化大小和 SHA 估算 | `estimate_serialized_bytes`, `serialized_sha256`, `measure_context` | context metrics/compaction |
| [`core/context_compaction.py`](../../tech_doc_agent/app/core/context_compaction.py) | 是否能在 turn 边界压缩的纯 planner | `ContextCompactionPolicy`, `plan_context_compaction` | `test_context_compaction.py` |
| [`core/conversation_summary.py`](../../tech_doc_agent/app/core/conversation_summary.py) | 摘要 lineage/schema/hash | `ConversationSummary`, `SummarySourceRange` | compaction tests/eval |
| [`core/guardrails.py`](../../tech_doc_agent/app/core/guardrails.py) | regex risk detection | `InputRisk`, `detect_prompt_injection`, `record_input_risk` | `test_guardrails.py` |
| [`core/structured_outputs.py`](../../tech_doc_agent/app/core/structured_outputs.py) | parser/relation Markdown heading 解析 | `ParserResult`, `RelationResult`, `parse_structured_result` | `test_structured_outputs.py` |
| [`core/state.py`](../../tech_doc_agent/app/core/state.py) | graph State 定义的 owning contract | `State`, `WorkflowStep`, `update_dialog_stack` | graph state/topology/message scope |
| [`core/revisions.py`](../../tech_doc_agent/app/core/revisions.py) | full Git SHA 校验 | `is_full_git_commit_sha` | settings/identity |
| [`core/langfuse_tracing.py`](../../tech_doc_agent/app/core/langfuse_tracing.py) | optional Langfuse client/trace/flush | `build_langfuse_trace`, `langfuse_metadata`, `flush/shutdown` | `test_langfuse_tracing.py` |

[`graph/state.py`](../../tech_doc_agent/app/graph/state.py) 只是从 `core.state` re-export graph-facing state，不应再复制字段。

## Application：用例、domain model 与 port

| 文件 | 主要职责 | 关键输入输出 | 适配器/测试 |
| --- | --- | --- | --- |
| [`application/input_guardrails.py`](../../tech_doc_agent/app/application/input_guardrails.py) | 把 core risk 转成 allow/warn/approval/block disposition | 输入文本/source -> decision | chat delivery, `test_guardrails.py` |
| [`application/approval_models.py`](../../tech_doc_agent/app/application/approval_models.py) | 中风险待审批 envelope/key/decision | pending input/consume result | Redis adapter, `test_approval_models.py` |
| [`application/approval_service.py`](../../tech_doc_agent/app/application/approval_service.py) | save/consume input approval use case | tenant/session/message/risk -> pending/decision | runtime, approval tests |
| [`application/learning_models.py`](../../tech_doc_agent/app/application/learning_models.py) | 学习记录/记忆 domain values | `LearningRecord`, `MemoryFragment` | store/repository, learning model tests |
| [`application/learning_commands.py`](../../tech_doc_agent/app/application/learning_commands.py) | 幂等 command/result | `UpdateLearningStateCommand/Result` | learning service/tool, transaction tests |
| [`application/learning_ports.py`](../../tech_doc_agent/app/application/learning_ports.py) | reader/updater/command Protocol | capability signatures | tools/resources, architecture |
| [`application/learning_unit_of_work.py`](../../tech_doc_agent/app/application/learning_unit_of_work.py) | clone-mutate-save-publish、command ledger | `LearningStateSnapshot`, `LearningStateUnitOfWork` | snapshot repo, transaction tests |
| [`application/learning_state.py`](../../tech_doc_agent/app/application/learning_state.py) | 一次记录+可选 memory 的业务 mutation | command -> result | learning tool, transaction tests |
| [`application/profile_models.py`](../../tech_doc_agent/app/application/profile_models.py) | profile defaults/update/merge rules | `UserProfile`, `UserProfileUpdate/Result` | profile service, model tests |
| [`application/profile_ports.py`](../../tech_doc_agent/app/application/profile_ports.py) | profile repository/service/memory reader Protocol | typed capability | tools/resources |
| [`application/profile_service.py`](../../tech_doc_agent/app/application/profile_service.py) | profile get/update/context summary | typed profile/update result/string summary | repository/tools/API |
| [`application/retrieval.py`](../../tech_doc_agent/app/application/retrieval.py) | 跨层本地检索 contract | `SearchQuery`, `SearchResult`, `DocumentRetrieverPort` | HybridRetriever/tools/eval |
| [`application/conversation_summarizer.py`](../../tech_doc_agent/app/application/conversation_summarizer.py) | 确定性 closed-turn 摘要 policy | previous+messages+limit -> text | graph compactor/eval |

详见 [06 - Application 边界](06-tools-and-application-boundaries.md) 与 [07 - 持久化](07-learning-profile-and-persistence.md)。

## Tools：模型可见 schema 与序列化

| 文件 | 内容 | 下游 capability | 测试 |
| --- | --- | --- | --- |
| [`tools/dependencies.py`](../../tech_doc_agent/app/tools/dependencies.py) | `ToolDependencies`, `ToolResourceContainer` | application ports | architecture/resources |
| [`tools/bundle.py`](../../tech_doc_agent/app/tools/bundle.py) | 11 tool stable bundle/factory | agents/composition | `test_tool_bundle.py` |
| [`tools/documents.py`](../../tech_doc_agent/app/tools/documents.py) | web/read/save/related docs | retriever/store/web port | doc/retrieval/web tests |
| [`tools/learning.py`](../../tech_doc_agent/app/tools/learning.py) | 读 history/memory、幂等学习写入 | learning readers/service | learning/summary/primary tool tests |
| [`tools/profiles.py`](../../tech_doc_agent/app/tools/profiles.py) | profile read/update | profile service | user profile tool tests |
| [`tools/__init__.py`](../../tech_doc_agent/app/tools/__init__.py) | 当前 public factory/types | callers | import/type gates |

工具权限矩阵、输入输出逐项见 [05](05-agents-prompts-and-models.md) 和 [06](06-tools-and-application-boundaries.md)。

## Agents：角色、prompt、模型与 identity

| 文件/组 | 主要职责 | 关键符号 | 测试 |
| --- | --- | --- | --- |
| [`agents/definition.py`](../../tech_doc_agent/app/agents/definition.py) | 构建时绑定 role 与 safe/sensitive/control tools；保存 Assistant、safe/sensitive tools 和 identity | `AssistantDefinition` | registry/tool matrix |
| [`agents/registry.py`](../../tech_doc_agent/app/agents/registry.py) | 构造/查询六角色 Assistant | `AssistantRegistry` | `test_assistant_registry.py` |
| [`agents/assistant_base.py`](../../tech_doc_agent/app/agents/assistant_base.py) | 空输出 retry、usage 捕获、模型调用 | `Assistant` | `test_assistant_base.py` |
| [`agents/model_factory.py`](../../tech_doc_agent/app/agents/model_factory.py) | primary/backup model provider | provider factory | registry/composition |
| [`agents/prompt_registry.py`](../../tech_doc_agent/app/agents/prompt_registry.py) | manifest/path/SHA/placeholder/role 检查 | `PromptRegistry` | `test_prompt_registry.py` |
| [`agents/identity.py`](../../tech_doc_agent/app/agents/identity.py) | prompt/model/provider execution identity | identity manifest | `test_assistant_identity.py`, eval manifest |
| [`agents/primary_assistant.py`](../../tech_doc_agent/app/agents/primary_assistant.py) | primary prompt/tool binding wrapper | primary construction | primary tool tests |
| [`agents/parser_assistant.py`](../../tech_doc_agent/app/agents/parser_assistant.py) | parser role wrapper | parser construction | registry/structured output |
| [`agents/relation_assistant.py`](../../tech_doc_agent/app/agents/relation_assistant.py) | relation role wrapper | relation construction | registry/structured output |
| [`agents/explanation_assistant.py`](../../tech_doc_agent/app/agents/explanation_assistant.py) | explanation role wrapper | explanation construction | registry |
| [`agents/examination_assistant.py`](../../tech_doc_agent/app/agents/examination_assistant.py) | examination role wrapper | examination construction | registry |
| [`agents/summary_assistant.py`](../../tech_doc_agent/app/agents/summary_assistant.py) | summary role wrapper | summary construction | `test_summary_assistant_tools.py` |
| [`agents/prompts/manifest.json`](../../tech_doc_agent/app/agents/prompts/manifest.json) | prompt resource allowlist + digest | PromptRegistry/identity | prompt registry/eval compatibility |
| `prompts/{parser,relation,explanation,examination,summary}.md` | 子 Agent 指令 | role runtime | prompt hash + eval |
| `prompts/primary/00..15` | primary 的角色、学习、审批、规划、保密和 runtime context | primary prompt assembly | manifest/order/identity |

Prompt 内容改变即 execution identity 改变；不能只跑 Python 单测而忽略 eval compatibility。

## Graph：状态编排与节点包装器

| 文件 | Owner | 关键符号/行为 | 测试 |
| --- | --- | --- | --- |
| [`graph/specs.py`](../../tech_doc_agent/app/graph/specs.py) | graph 的 declarative spec/policies | `GraphSpec`, `AgentSpec`, primary/tool/reflection/completion policy | compile/topology/composition |
| [`graph/builder.py`](../../tech_doc_agent/app/graph/builder.py) | StateGraph node/edge/interrupt compile | `create_graph_builder`, `build_multi_agentic_graph` | compile/topology |
| [`graph/commands.py`](../../tech_doc_agent/app/graph/commands.py) | model tool commands/handoffs | `PlanWorkflow`, `To*`, `CompleteOrEscalate` | routes/tool matrix |
| [`graph/routing.py`](../../tech_doc_agent/app/graph/routing.py) | primary/subagent/next-step 纯路由 | `make_primary_router`, `make_subagent_router`, route functions | `test_graph_routes.py` |
| [`graph/nodes.py`](../../tech_doc_agent/app/graph/nodes.py) | fetch user info、enter/leave/finish node factories | node closures | finish/topology |
| [`graph/messages.py`](../../tech_doc_agent/app/graph/messages.py) | Agent/tool message helper/规范 | helper functions | graph/message tests |
| [`graph/message_scope.py`](../../tech_doc_agent/app/graph/message_scope.py) | 子 Agent prompt message 视图 | scoped message selectors | `test_message_scope.py` |
| [`graph/assistant_execution.py`](../../tech_doc_agent/app/graph/assistant_execution.py) | Assistant invocation + budget/context usage wrappers | `assistant_node` | assistant/budget/context tests |
| [`graph/tool_nodes.py`](../../tech_doc_agent/app/graph/tool_nodes.py) | policy -> ToolNode -> log/budget/reflection/retry 顺序 | tool node factory/execution | `test_graph_tool_nodes.py` |
| [`graph/tool_policy.py`](../../tech_doc_agent/app/graph/tool_policy.py) | parser retrieval/identical repeat block | `evaluate_tool_policy` | `test_graph_tool_policy.py` |
| [`graph/reflection.py`](../../tech_doc_agent/app/graph/reflection.py) | repair/finalize/terminal 有限状态 | `apply_reflection_policy`, route | `test_graph_reflection.py` |
| [`graph/budgeting.py`](../../tech_doc_agent/app/graph/budgeting.py) | graph usage ledger、before/after checks | `WorkflowBudgetTracker`, request wrapper | `test_graph_budgeting.py` |
| [`graph/budget_termination.py`](../../tech_doc_agent/app/graph/budget_termination.py) | termination update/node/closed tool messages | termination helpers | graph budget/routes |
| [`graph/context_metrics.py`](../../tech_doc_agent/app/graph/context_metrics.py) | 每次 prompt input measure/record | `ContextMetricsTracker` | `test_graph_context_metrics.py` |
| [`graph/provider_retries.py`](../../tech_doc_agent/app/graph/provider_retries.py) | tool provider retry ledger -> state delta | `ProviderRetryUsageTracker` | `test_graph_provider_retries.py` |
| [`graph/context_compaction.py`](../../tech_doc_agent/app/graph/context_compaction.py) | planner+summarizer -> RemoveMessage update | `ContextCompactor` | compaction/eval tests |
| [`graph/state.py`](../../tech_doc_agent/app/graph/state.py) | State re-export | `State`, `WorkflowStep` | architecture |
| [`graph/__init__.py`](../../tech_doc_agent/app/graph/__init__.py) | 当前 graph public surface | specs/commands/builder/routes exports | import gates |

拓扑、路由优先级和节点命名详见 [04 - Graph](04-graph-topology-and-routing.md)。

## Runtime：对 Graph 的稳定 facade

| 文件 | 主要职责 | 关键符号 | 测试 |
| --- | --- | --- | --- |
| [`runtime/chat_runtime.py`](../../tech_doc_agent/app/runtime/chat_runtime.py) | facade，把公开方法委托给 approval/session/execution 协作者，并把进入退出交给 lifecycle | `ChatRuntime` | runtime facade tests |
| [`runtime/lifecycle.py`](../../tech_doc_agent/app/runtime/lifecycle.py) | resources/RedisSaver/graph start-close | `RuntimeLifecycle` | `test_runtime_lifecycle.py` |
| [`runtime/config.py`](../../tech_doc_agent/app/runtime/config.py) | tenant thread ID、metadata、callbacks、request budget | `SessionConfigFactory` | `test_chat_runtime_config.py` |
| [`runtime/execution.py`](../../tech_doc_agent/app/runtime/execution.py) | sync/async stream、approval resume/reject | `GraphExecutionService` | `test_chat_runtime_execution.py` |
| [`runtime/sessions.py`](../../tech_doc_agent/app/runtime/sessions.py) | snapshot read、history/state projection | `SessionQueryService` | `test_chat_runtime_queries.py` |
| [`runtime/approvals.py`](../../tech_doc_agent/app/runtime/approvals.py) | 旧 runtime approval import 的兼容 wrapper；生产 `ChatRuntime` 直接使用 application service | exports/application mapping | runtime approvals |
| [`runtime/approval_projection.py`](../../tech_doc_agent/app/runtime/approval_projection.py) | guardrail reject graph part | `guardrail_rejection_part` | execution/SSE |
| [`runtime/serialization.py`](../../tech_doc_agent/app/runtime/serialization.py) | LangChain/checkpoint values -> safe API shapes | message/state serializers | runtime queries/API |
| [`runtime/telemetry.py`](../../tech_doc_agent/app/runtime/telemetry.py) | operation trace/session/tenant context | trace factory/context | execution/observability |
| [`runtime/identity.py`](../../tech_doc_agent/app/runtime/identity.py) | runtime execution identity view | identity builder | health/identity tests |
| [`runtime/__init__.py`](../../tech_doc_agent/app/runtime/__init__.py) | runtime public exports | facade symbols | import/type tests |

详见 [03 - Runtime](03-runtime-sessions-and-approval.md)。

## API 与 SSE delivery

| 文件 | 主要职责 | 输入输出 | 测试 |
| --- | --- | --- | --- |
| [`api/schemas.py`](../../tech_doc_agent/app/api/schemas.py) | 带类型、长度与 pattern 约束的 request/response Pydantic models | Chat/Approve/State/History | `test_api_schemas.py` |
| [`api/tenant.py`](../../tech_doc_agent/app/api/tenant.py) | header/body/query tenant resolution | Request values -> TenantContext | API/tenant tests |
| [`api/routes/chat.py`](../../tech_doc_agent/app/api/routes/chat.py) | `/chat`, approve, session history/state | HTTP -> delivery/runtime | API/SSE tests |
| [`api/routes/learning.py`](../../tech_doc_agent/app/api/routes/learning.py) | learning overview API | typed records -> response | `test_learning_overview.py` |
| [`api/routes/health.py`](../../tech_doc_agent/app/api/routes/health.py) | health/readiness/runtime identity | safe status JSON | `test_health_routes.py` |
| [`api/chat_delivery.py`](../../tech_doc_agent/app/api/chat_delivery.py) | guardrail、trace、StreamingResponse/JSON error 分界 | runtime parts -> response | guardrail/SSE tests |
| [`api/frontend.py`](../../tech_doc_agent/app/api/frontend.py) | dist assets、SPA routes、503 fallback | static files | `test_frontend_static.py` |
| [`api/sse/contract.py`](../../tech_doc_agent/app/api/sse/contract.py) | 后端事件名 contract | Literal/list | `test_sse_contract.py` |
| [`api/sse/payloads.py`](../../tech_doc_agent/app/api/sse/payloads.py) | strict event payload models | dict -> validated payload | `test_sse_payloads.py` |
| [`api/sse/events.py`](../../tech_doc_agent/app/api/sse/events.py) | `sse_event` 构造 | name+payload -> validated `ServerSentEvent` | `test_sse_events.py` |
| [`api/sse/encoder.py`](../../tech_doc_agent/app/api/sse/encoder.py) | EventSourceResponse/JSON wire 编码 | event iterator -> response | SSE events/delivery |
| [`api/sse/parts.py`](../../tech_doc_agent/app/api/sse/parts.py) | runtime graph part 类型/拆包 | `(mode, data)` 规范化 | streaming tests |
| [`api/sse/message_translator.py`](../../tech_doc_agent/app/api/sse/message_translator.py) | message chunk/content 的安全文本提取辅助 | provider content -> text | SSE tests |
| [`api/sse/update_translator.py`](../../tech_doc_agent/app/api/sse/update_translator.py) | updates-mode state delta -> events | node update -> typed SSE | SSE payload/events |
| [`api/sse/agent_metadata.py`](../../tech_doc_agent/app/api/sse/agent_metadata.py) | node/metadata -> agent identity | metadata/node -> agent | architecture/SSE |
| [`api/sse/streaming.py`](../../tech_doc_agent/app/api/sse/streaming.py) | parts 合并为 sync/async SSE iterator | runtime parts -> event stream | SSE streaming |
| [`api/sse/context.py`](../../tech_doc_agent/app/api/sse/context.py) | 每次 next/anext 恢复 trace context | iterator wrappers | observability/SSE |
| [`api/sse/__init__.py`](../../tech_doc_agent/app/api/sse/__init__.py) | SSE public helpers | re-exports | import compatibility |

详见 [02 - Chat API 与 SSE](02-chat-api-and-sse.md)。

## Infrastructure persistence

| 文件 | 主要职责 | 一致性边界 | 测试 |
| --- | --- | --- | --- |
| [`persistence/atomic_json.py`](../../tech_doc_agent/app/infrastructure/persistence/atomic_json.py) | UTF-8 read、temp+fsync+replace | 单 JSON 文件原子替换 | `test_atomic_json.py` |
| [`persistence/generations.py`](../../tech_doc_agent/app/infrastructure/persistence/generations.py) | draft/current manifest/inventory | generation publication | `test_generation_store.py` |
| [`persistence/learning_state_repository.py`](../../tech_doc_agent/app/infrastructure/persistence/learning_state_repository.py) | records+memories+commands snapshot | 一份 generation | transaction/persistence |
| [`persistence/learning_store.py`](../../tech_doc_agent/app/infrastructure/persistence/learning_store.py) | record query/prepare upsert/compat dict view | 共享 UoW | learning store/tests |
| [`persistence/memory_store.py`](../../tech_doc_agent/app/infrastructure/persistence/memory_store.py) | memory query/prepare dedupe | 共享 UoW | memory store/tests |
| [`persistence/text_match.py`](../../tech_doc_agent/app/infrastructure/persistence/text_match.py) | learning/memory 简单文本匹配 | pure helper | store tests |
| [`persistence/user_profile_repository.py`](../../tech_doc_agent/app/infrastructure/persistence/user_profile_repository.py) | tenant path/envelope/legacy fallback | 单 profile JSON | profile repository tests |
| [`persistence/faiss_snapshot.py`](../../tech_doc_agent/app/infrastructure/persistence/faiss_snapshot.py) | index+docs+chunks generation | 三文件一致发布 | FAISS persistence |
| [`persistence/approval_repository.py`](../../tech_doc_agent/app/infrastructure/persistence/approval_repository.py) | Redis TTL envelope/atomic GETDEL | 单 pending input | Redis approval tests |
| [`persistence/in_memory_approval_repository.py`](../../tech_doc_agent/app/infrastructure/persistence/in_memory_approval_repository.py) | approval fake/本地 adapter | process memory | approval service tests |
| [`persistence/legacy_migration.py`](../../tech_doc_agent/app/infrastructure/persistence/legacy_migration.py) | dry-run/digest/backup/apply/report | compare-and-migrate | legacy migration tests |
| [`persistence/__init__.py`](../../tech_doc_agent/app/infrastructure/persistence/__init__.py) | atomic JSON public exports | package surface | import tests |

详见 [07](07-learning-profile-and-persistence.md) 与 [13](13-compatibility-and-migration.md)。

## Infrastructure retrieval

| 文件 | 主要职责 | 关键符号 | 测试 |
| --- | --- | --- | --- |
| [`retrieval/faiss_store.py`](../../tech_doc_agent/app/infrastructure/retrieval/faiss_store.py) | document ID/chunk/embedding/IndexFlatL2/search/save/load | `FaissStore` | doc store/FAISS persistence |
| [`retrieval/hybrid.py`](../../tech_doc_agent/app/infrastructure/retrieval/hybrid.py) | cache/filter/mode/rank/fusion/format 总编排 | `HybridRetriever` | hybrid retriever |
| [`retrieval/models.py`](../../tech_doc_agent/app/infrastructure/retrieval/models.py) | internal indexed/ranked/fused candidate + store port | candidate dataclasses | ranker tests |
| [`retrieval/documents.py`](../../tech_doc_agent/app/infrastructure/retrieval/documents.py) | raw -> IndexedDocument、signature/filter | document helpers | hybrid/metadata |
| [`retrieval/tokenization.py`](../../tech_doc_agent/app/infrastructure/retrieval/tokenization.py) | 英文/Camel/CJK tokens | `tokenize` | ranker/hybrid |
| [`retrieval/exact.py`](../../tech_doc_agent/app/infrastructure/retrieval/exact.py) | query substring ranking | `rank_exact` | ranker tests |
| [`retrieval/bm25.py`](../../tech_doc_agent/app/infrastructure/retrieval/bm25.py) | BM25 index/score | `BM25Index` | ranker tests |
| [`retrieval/semantic.py`](../../tech_doc_agent/app/infrastructure/retrieval/semantic.py) | chunk->doc、filter、degrade policy | `SemanticRanker` | hybrid/embedding |
| [`retrieval/fusion.py`](../../tech_doc_agent/app/infrastructure/retrieval/fusion.py) | RRF、signals/chunks | `reciprocal_rank_fusion` | ranker tests |
| [`retrieval/formatting.py`](../../tech_doc_agent/app/infrastructure/retrieval/formatting.py) | FusedCandidate -> SearchResult | `format_result` | contracts/rankers |
| [`retrieval/chunking.py`](../../tech_doc_agent/app/infrastructure/retrieval/chunking.py) | recursive character splitting | chunk helper | doc store |
| [`retrieval/embedding.py`](../../tech_doc_agent/app/infrastructure/retrieval/embedding.py) | embedding HTTP/provider/retry/shape validation | `generate_embedding` | embedding errors |
| [`retrieval/normalization.py`](../../tech_doc_agent/app/infrastructure/retrieval/normalization.py) | document/chunk metadata canonicalization | normalize helpers | metadata tests |
| [`retrieval/filters.py`](../../tech_doc_agent/app/infrastructure/retrieval/filters.py) | filter/category alias/tag subset matching | `normalize_filter`, `metadata_matches` | metadata/hybrid |
| [`retrieval/inference.py`](../../tech_doc_agent/app/infrastructure/retrieval/inference.py) | title/content category/tag inference | `infer_category/tags` | metadata |
| [`retrieval/taxonomy.py`](../../tech_doc_agent/app/infrastructure/retrieval/taxonomy.py) | category rules/aliases/broad tag semantics | constants/helpers | metadata/eval |
| [`retrieval/metadata.py`](../../tech_doc_agent/app/infrastructure/retrieval/metadata.py) | staged compatibility re-export | no new logic | architecture/metadata |
| [`retrieval/web_search.py`](../../tech_doc_agent/app/infrastructure/retrieval/web_search.py) | Tavily quota/retry -> DDG fallback/normalize | `WebSearchBackend` | `test_web_search.py` |
| [`retrieval/__init__.py`](../../tech_doc_agent/app/infrastructure/retrieval/__init__.py) | implementation public export | `HybridRetriever` | contracts |

详见 [08 - 检索](08-retrieval-and-document-store.md)。

## Compatibility namespace

| 文件 | 保留的旧入口 | 新 owner | 测试 |
| --- | --- | --- | --- |
| [`services/retrieval/__init__.py`](../../tech_doc_agent/app/services/retrieval/__init__.py) | retrieval query/result/HybridRetriever | application + infrastructure retrieval | retrieval contracts |
| [`services/user_profile.py`](../../tech_doc_agent/app/services/user_profile.py) | 旧构造函数/free functions/dict return | application profile + JSON repository | user profile compatibility |
| [`services/__init__.py`](../../tech_doc_agent/app/services/__init__.py) | namespace marker | 无实现 | architecture file-set gate |

详见 [13 - 兼容与迁移](13-compatibility-and-migration.md)。

## Frontend 入口、API 与状态

| 文件/组 | 主要职责 | 消费/测试 |
| --- | --- | --- |
| [`frontend/src/main.tsx`](../../frontend/src/main.tsx), [`App.tsx`](../../frontend/src/App.tsx) | React/Router 入口 | build/static smoke |
| [`app/AppRouter.tsx`](../../frontend/src/app/AppRouter.tsx), [`routing.ts`](../../frontend/src/app/routing.ts), [`Topbar.tsx`](../../frontend/src/app/Topbar.tsx) | route/view/navigation/bootstrap gate | routing/component tests |
| [`store.ts`](../../frontend/src/store.ts) | inject repositories/clock/IDs 并组合五 slices | store persistence/slice tests |
| [`store/contracts.ts`](../../frontend/src/store/contracts.ts), [`defaults.ts`](../../frontend/src/store/defaults.ts) | AppStore 和初始 state contract | typecheck/slice tests |
| [`store/sessionSlice.ts`](../../frontend/src/store/sessionSlice.ts) | session/tenant/recent/reset/delete | slices/session bootstrap |
| [`store/transcriptSlice.ts`](../../frontend/src/store/transcriptSlice.ts) | messages/tool cards/stream finalization/persist | slices/stream integration |
| [`store/traceSlice.ts`](../../frontend/src/store/traceSlice.ts) | Inspector event/filter/3000 cap | slices/Inspector |
| [`store/learningSlice.ts`](../../frontend/src/store/learningSlice.ts) | overview/Learner toggle | Learner/session refresh |
| [`store/uiSlice.ts`](../../frontend/src/store/uiSlice.ts) | running/error/theme/tool expansion | slices/components |
| [`shared/api/client.ts`](../../frontend/src/shared/api/client.ts) | base URL、tenant headers、JSON client | client tests |
| [`shared/api/contracts.ts`](../../frontend/src/shared/api/contracts.ts) | REST runtime decoders | contract tests/backend REST contract |
| [`shared/api/sessionApi.ts`](../../frontend/src/shared/api/sessionApi.ts) | state/history/learning endpoints | session API tests |
| [`tenant.ts`](../../frontend/src/tenant.ts) | tenant normalize/key/query params | routing/storage tests |
| [`types.ts`](../../frontend/src/types.ts) | UI/session/message/tool/trace types | whole frontend |

## Frontend SSE 与会话恢复

| 文件 | 主要职责 | 首选测试 |
| --- | --- | --- |
| [`useChatStream.ts`](../../frontend/src/useChatStream.ts) | concrete stream dependencies | integration |
| [`streaming/chatStream.ts`](../../frontend/src/streaming/chatStream.ts) | send/approve、HTTP/SSE lifecycle、finish/refresh/error | `chatStream.integration.test.ts` |
| [`sseContract.ts`](../../frontend/src/sseContract.ts) | 事件名/status/error fields | backend `test_sse_contract.py` |
| [`streaming/sseEnvelope.ts`](../../frontend/src/streaming/sseEnvelope.ts) | JSON/double JSON/unknown-invalid-event parse | payload/reducer tests |
| [`streaming/ssePayloads.ts`](../../frontend/src/streaming/ssePayloads.ts) | 每事件 runtime decoder | `ssePayloads.test.ts` |
| [`streaming/sseReducer.ts`](../../frontend/src/streaming/sseReducer.ts) | pure event -> state/actions | `sseReducer.test.ts` |
| [`streaming/storeAdapter.ts`](../../frontend/src/streaming/storeAdapter.ts) | actions -> store methods | `storeAdapter.test.ts` |
| [`features/session/useSessionBootstrap.ts`](../../frontend/src/features/session/useSessionBootstrap.ts) | URL/store 对齐、effect/abort | hook tests |
| [`features/session/sessionBootstrap.ts`](../../frontend/src/features/session/sessionBootstrap.ts) | local cache + 三 API 并行恢复 | bootstrap tests |
| [`features/session/refreshSessionContext.ts`](../../frontend/src/features/session/refreshSessionContext.ts) | 流后 state+learning refresh | refresh tests |
| [`features/session/useSessionControls.ts`](../../frontend/src/features/session/useSessionControls.ts) | UI session actions/navigation | controls tests |
| [`features/session/useRefreshLearning.ts`](../../frontend/src/features/session/useRefreshLearning.ts) | learning refresh hook | component/session tests |

## Frontend repositories 与视图

| 文件/组 | 主要职责 | 测试 |
| --- | --- | --- |
| [`storage/keyValueStorage.ts`](../../frontend/src/storage/keyValueStorage.ts) | browser storage 安全读写/失败回调 | repository/store tests |
| [`storage/sessionRepository.ts`](../../frontend/src/storage/sessionRepository.ts) | context/recent sessions + legacy ID | session repository |
| [`storage/transcriptRepository.ts`](../../frontend/src/storage/transcriptRepository.ts) | tenant+session versioned messages/events/tools | transcript/store persistence |
| [`storage/preferenceRepository.ts`](../../frontend/src/storage/preferenceRepository.ts) | theme preference | preference tests |
| [`features/chat/ChatPane.tsx`](../../frontend/src/features/chat/ChatPane.tsx) | messages/tool cards/composer 展示 | component boundaries |
| [`features/approval/ApprovalDrawer.tsx`](../../frontend/src/features/approval/ApprovalDrawer.tsx) | pending interrupt approve/reject | component/integration |
| [`features/studio/Studio.tsx`](../../frontend/src/features/studio/Studio.tsx) | chat workspace/session/sidebar | component tests |
| [`features/inspector/Inspector.tsx`](../../frontend/src/features/inspector/Inspector.tsx) | trace timeline/detail/replay UI | inspector model/components |
| [`features/inspector/inspectorModel.ts`](../../frontend/src/features/inspector/inspectorModel.ts) | Inspector 纯筛选/展示模型 | `inspectorModel.test.ts` |
| [`features/inspector/traceExport.ts`](../../frontend/src/features/inspector/traceExport.ts) | trace export | inspector/component tests |
| [`features/learner/Learner.tsx`](../../frontend/src/features/learner/Learner.tsx) | learning overview/plan/exam view | component tests |
| [`features/landing/Landing.tsx`](../../frontend/src/features/landing/Landing.tsx) | landing/入口 | routing/components |
| [`shared/components/AgentBadge.tsx`](../../frontend/src/shared/components/AgentBadge.tsx), [`agentColors.ts`](../../frontend/src/agentColors.ts) | agent identity visual normalization | component/typecheck |
| [`utils.ts`](../../frontend/src/utils.ts) | ID/session ID helpers | injected in tests |

样式文件按组件域拆成 tokens/base/shell/chat/composer/approval/inspector/learner/landing/responsive，由 [`styles/index.css`](../../frontend/src/styles/index.css) 统一导入；结构边界由 backend 的 `test_frontend_css_architecture.py` 检查。

详见 [09 - 前端链路](09-frontend-stream-and-state.md)。

## 后端测试按 owner 分组

### 架构、装配与入口

```text
test_architecture_dependencies.py
test_architecture_import_graph.py
test_typecheck_gate.py
test_bootstrap.py
test_composition.py
test_resources.py
test_runtime_lifecycle.py
test_script_entrypoints.py
```

### API、SSE 与前端静态 contract

```text
test_api_schemas.py
test_health_routes.py
test_sse_contract.py
test_sse_payloads.py
test_sse_events.py
test_frontend_rest_contract.py
test_frontend_architecture.py
test_frontend_css_architecture.py
test_frontend_static.py
```

### Runtime、会话与审批

```text
test_chat_runtime_config.py
test_chat_runtime_execution.py
test_chat_runtime_queries.py
test_runtime_approvals.py
test_approval_models.py
test_redis_approval_repository.py
test_guardrails.py
```

### Agent、Prompt 与 Graph

```text
test_assistant_base.py
test_assistant_identity.py
test_assistant_registry.py
test_prompt_registry.py
test_primary_assistant_tools.py
test_summary_assistant_tools.py
test_graph_compile.py
test_graph_topology.py
test_graph_routes.py
test_graph_finish_nodes.py
test_graph_tool_nodes.py
test_graph_tool_policy.py
test_graph_reflection.py
test_message_scope.py
test_structured_outputs.py
```

### 预算、重试、上下文与观测

```text
test_budget_usage.py
test_execution_budget.py
test_graph_budgeting.py
test_retry.py
test_retry_usage.py
test_graph_provider_retries.py
test_context_metrics.py
test_graph_context_metrics.py
test_context_compaction.py
test_context_compaction_eval.py
test_observability.py
test_redaction.py
test_langfuse_tracing.py
test_error_model.py
test_model_pricing.py
test_tenant.py
```

### 学习、画像与持久化

```text
test_learning_models.py
test_learning_store.py
test_memory_store.py
test_learning_state_transaction.py
test_learning_overview.py
test_profile_models.py
test_user_profile.py
test_user_profile_repository.py
test_user_profile_tools.py
test_atomic_json.py
test_generation_store.py
test_repository_contracts.py
test_legacy_persistence_migration.py
```

### 检索、文档与 Web

```text
test_doc_store.py
test_faiss_store_persistence.py
test_embedding_errors.py
test_hybrid_retriever.py
test_retrieval_contracts.py
test_retrieval_metadata.py
test_retrieval_rankers.py
test_retrieval_eval_runner.py
test_eval_retrieval_corpus.py
test_web_search.py
test_seed_doc_store_script.py
```

### Eval 与 benchmark

```text
test_eval_artifacts.py
test_eval_judges.py
test_eval_manifest_compatibility.py
test_eval_manifests.py
test_eval_result_comparison.py
test_eval_runner.py
test_eval_thresholds.py
test_recovery_metrics.py
test_benchmark_latency.py
```

共享 fixture/fakes/contracts：

- [`tests/conftest.py`](../../tests/conftest.py)：全后端 fixture；
- [`tests/fakes/chat_runtime.py`](../../tests/fakes/chat_runtime.py)、[`redis.py`](../../tests/fakes/redis.py)：delivery/runtime fake；
- [`tests/contracts/repositories.py`](../../tests/contracts/repositories.py)：repository contract suite；
- [`tests/architecture/import_graph.py`](../../tests/architecture/import_graph.py)：AST import graph。

## Frontend 测试对应表

| 功能 | 测试文件 |
| --- | --- |
| route/session URL | `app/routing.test.ts`, `app/sessionRouting.component.test.tsx` |
| API runtime decode | `shared/api/client.test.ts`, `contracts.test.ts`, `sessionApi.test.ts` |
| SSE | `ssePayloads.test.ts`, `sseReducer.test.ts`, `storeAdapter.test.ts`, `chatStream.integration.test.ts` |
| Store | `store/slices.test.ts`, `storePersistence.test.ts` |
| Storage | session/transcript/preference repository tests |
| Session lifecycle | bootstrap/hook/refresh/controls tests |
| Components | `features/componentBoundaries.test.tsx` |
| Inspector pure model | `features/inspector/inspectorModel.test.ts` |

运行全集：

```powershell
Push-Location frontend
npm run check
npm run test
npm run build
Pop-Location
```

## Scripts 与 evals

| 入口 | 用途 | 读前置说明 |
| --- | --- | --- |
| [`scripts/migrate_legacy_persistence.py`](../../scripts/migrate_legacy_persistence.py) | learning/profile 旧持久化 dry-run/apply | [13](13-compatibility-and-migration.md) |
| [`scripts/migrate_doc_metadata.py`](../../scripts/migrate_doc_metadata.py) | 旧文档 metadata 规范化 | [08](08-retrieval-and-document-store.md) |
| [`scripts/seed_doc_store.py`](../../scripts/seed_doc_store.py) | seed 本地文档库并记录 artifact | `docs/evaluation.md` |
| [`scripts/benchmark_latency.py`](../../scripts/benchmark_latency.py) | 并发/延迟 benchmark | `docs/evaluation.md` |
| `evals/run_eval.py` | Agent case runner | evaluation docs/identity |
| `evals/run_retrieval_eval.py` | retrieval cases/corpus/filter runner | 有效版本化 corpus |
| `evals/run_context_compaction_eval.py` | deterministic compaction runner | CI baseline |
| `evals/check_manifest_compatibility.py` | artifact/runtime identity compatibility | eval manifest |
| `evals/check_result_regression.py` | candidate vs baseline policy gate | CI |

Eval 结果、报告和 manifest 是生成 artifact，不应因“测试跑过”就自动当新 baseline 提交。先看 [`docs/evaluation.md`](../evaluation.md) 的前置条件和解释。

## 文档章节覆盖矩阵

| 章节 | 主要源码覆盖 |
| --- | --- |
| [00](00-system-map.md) | 全局目录、两条入口、全请求、四类状态 |
| [01](01-startup-and-composition.md) | main/server/bootstrap/composition/resources/lifecycle |
| [02](02-chat-api-and-sse.md) | API route/delivery/schema/SSE 全包 |
| [03](03-runtime-sessions-and-approval.md) | runtime 全包、approval/checkpoint/session projection |
| [04](04-graph-topology-and-routing.md) | graph 全包/state/route/topology |
| [05](05-agents-prompts-and-models.md) | agents/prompts/model/identity/role matrix |
| [06](06-tools-and-application-boundaries.md) | tools + application ports/use cases |
| [07](07-learning-profile-and-persistence.md) | learning/profile/persistence generation |
| [08](08-retrieval-and-document-store.md) | retrieval/FAISS/Web search |
| [09](09-frontend-stream-and-state.md) | frontend entry/store/SSE/session/storage/views |
| [10](10-cross-cutting-policies.md) | settings/tenant/error/retry/budget/context/observability |
| [11](11-change-recipes.md) | 常见跨文件修改路径 |
| [12](12-debugging-and-tests.md) | 症状定位/CI/测试命令 |
| [13](13-compatibility-and-migration.md) | services/facades/schema/legacy data/browser cache |
| 本章 | 源码、测试、脚本反向索引 |
| [15](15-glossary.md) | 项目语义词典与易混概念 |

## 查符号的实用命令

```powershell
# 文件
rg --files tech_doc_agent/app frontend/src tests | rg "关键词"

# 定义和调用
rg -n "class Symbol|def symbol|symbol\(" tech_doc_agent tests

# import 迁移
rg -n "from .*old|import .*old" tech_doc_agent tests evals scripts

# 前后端 contract 同名字段
rg -n '"event_name"|field_name' tech_doc_agent/app/api frontend/src tests

# 某 setting 的完整消费链
rg -n "SETTING_NAME" . -g "!frontend/node_modules/**" -g "!frontend/dist/**"
```

查到 private helper 后先看它所在文件的 owner；不要因为一个测试 import 了 `_helper` 就把它当 public API。最后一章是 [15 - 术语表](15-glossary.md)。
