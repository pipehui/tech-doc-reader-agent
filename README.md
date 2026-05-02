# Tech Doc Reader Agent

[![CI](https://github.com/pipehui/tech-doc-reader-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/pipehui/tech-doc-reader-agent/actions/workflows/ci.yml)

一个面向技术文档研读场景的 LangGraph 多智能体系统。它把“理解一个陌生技术概念”拆成解析、关联、讲解、测验和沉淀，并通过 Studio / Inspector / Learner 三个视角展示同一条会话状态。

项目最初参考了 LangGraph 多助手教程中的对话状态管理思路；当前的 `PlanWorkflow`、Adaptive 路由、Hybrid RAG、HITL 审批、SSE Inspector、长期学习状态和 eval 基线为本项目围绕技术学习场景的自主扩展。

![Landing page](docs/images/landing.png)

## What It Shows

- `primary` 按任务复杂度选择 direct response、single-agent 或 `parser -> relation -> explanation` 链式研读。
- FastAPI 通过 async SSE 输出 token、tool、plan、agent transition、interrupt 等事件，前端实时渲染。
- Redis checkpointer 支持会话恢复；敏感工具在写入前触发 HITL 审批。
- 本地文档库使用 BM25 + Vector + RRF 的 Hybrid RAG，并提供检索 eval。
- 学习记录、学习轨迹 memory、长期用户画像分层存储，后续回答可读取用户上下文。
- `trace_id` 贯穿 SSE、结构化日志和 Langfuse callback，便于定位多 agent 链路问题。
- Scoped context 隔离子 Agent 可见消息，避免 `primary` 的搜索、审批和中间推理污染 parser / examination。
- 输入侧 prompt-injection guardrails 分级处置：high-risk 直接阻断，medium-risk 复用 HITL 审批通道确认后再进入 graph。

## Results

当前 full agent eval（2026-04-30，25 cases，覆盖 direct、学习状态读取、examination、multi-agent 标准链路和 boundary/refusal）：

| Cases | Done | Error | Plan Match | Keyword | Behavior | Tool Results Avg | Structured Results Avg | Interrupts |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 25 | 25 | 0 | 0.96 | 0.98 | 0.99 | 3.00 | 1.60 | 6 |

延迟与任务路径强相关，系统显式区分快速回应和深度研读：

| Path | Cases | E2E p50 | E2E p95 | Tool Results Avg | Structured Results Avg | 设计目标 |
|---|---:|---:|---:|---:|---:|---|
| Quick response: direct / 学习状态查询 / boundary | 12 | 5.90s | 13.06s | 0.67 | 0.00 | 闲聊、能力介绍、学习状态读取和边界拒绝 |
| Single-agent task: examination | 3 | 15.69s | 38.90s | 2.00 | 1.00 | 单 agent 出题和中等复杂度任务 |
| Multi-agent research chain: parser -> relation -> explanation | 10 | 150.49s | 226.71s | 6.10 | 3.70 | 深度研读，一次完成多轮检索、结构化抽取和长文讲解 |

`multi_agent_standard` 是研读模式而不是聊天模式：典型链路会完成 6-10 次本地/Web 工具结果、parser/relation 结构化输出，以及 explanation 的最终长文生成。后续优化重点是 parser 与 relation 可并行部分、结构化结果流式渲染，以及按 `learning_target` 缓存 parser result。

当前 full retrieval eval（2026-04-29，60 cases，Top K=5）：

| Mode | Recall@5 | Hit@1 | MRR | Keyword Coverage | E2E p50 | E2E p95 |
|---|---:|---:|---:|---:|---:|---:|
| BM25-only | 0.85 | 0.37 | 0.56 | 0.97 | 0.020s | 0.021s |
| Vector-only | 0.88 | 0.52 | 0.65 | 0.97 | 0.927s | 1.609s |
| Hybrid | 0.93 | 0.53 | 0.70 | 0.98 | 1.209s | 2.148s |

当前 async SSE concurrency smoke（2026-04-30，11 enabled cases，10 并发，自动拒绝 HITL 写入审批）：

| Concurrency | Valid | Error Rate | Final Interrupted | Auto-Rejected Interrupts | TTFT p50 | TTFT p95 | E2E p50 | E2E p95 | Tool Events Avg |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 11/11 | 0.0% | 0.0% | 2 | 0.70s | 4.59s | 22.76s | 225.57s | 3.18 |

更多评测命令、指标解释和边界 case 判定方式见 [docs/evaluation.md](docs/evaluation.md)。

## Architecture

![Multi-agent technical document reader architecture](graphs/tech_doc_reader_agent_architecture.svg)

核心运行路径：

1. `Client + FastAPI` 通过 `/chat` 建立 SSE 事件流。
2. `primary assistant` 识别用户目标，直接回答或生成 `PlanWorkflow`。
3. 多 agent 链路通常按 `parser -> relation -> explanation` 推进，必要时进入 `examination` 或 `summary`。
4. 工具层连接共享文档库、Hybrid retriever、Web search、Learning store、Memory store 和 User profile。
5. Redis checkpointer 保存 LangGraph thread，`user_id + namespace + session_id` 定位会话。

详细设计见 [docs/architecture.md](docs/architecture.md)。

## Quickstart

复制环境变量模板：

```bash
cp .env.example .env
```

启动 Redis 和后端：

```bash
docker compose up -d redis
PYTHONPATH=. uvicorn tech_doc_agent.app.api.server:app --reload
```

启动前端开发服务：

```bash
cd frontend
npm install
npm run dev
```

访问：

```text
http://127.0.0.1:5173
```

更多本地开发、Docker、数据目录和知识库初始化说明见 [docs/development.md](docs/development.md)。

## Documentation

| Document | Content |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 多 agent 编排、状态流转、工具层和数据层设计 |
| [docs/evaluation.md](docs/evaluation.md) | Agent eval、retrieval eval、concurrency benchmark 和指标解释 |
| [docs/learning-state.md](docs/learning-state.md) | 学习记录、学习轨迹 memory、长期用户画像的边界和写入策略 |
| [docs/observability.md](docs/observability.md) | `trace_id`、结构化日志、SSE 事件和 Langfuse tracing |
| [docs/api.md](docs/api.md) | REST / SSE API 参考 |
| [docs/development.md](docs/development.md) | 本地启动、Docker、质量检查、项目结构和运行时数据 |

## Tech Stack

- Backend: FastAPI, LangGraph, LangChain, Redis checkpointer
- Retrieval: FAISS, BM25, Vector search, RRF, metadata filter
- Observability: structured logs, SSE event stream, Langfuse callback
- Frontend: React, Vite, TypeScript
- Quality: pytest, ruff, mypy, online eval, retrieval eval, concurrency benchmark

## Limitations

当前版本的已知取舍，记录在此以便后续迭代：

- **多租户隔离来自请求 user_id / namespace 字段，未做真实鉴权**。生产部署需要在网关或 FastAPI middleware 中接入 JWT / Session，再把校验后的 user_id 注入到 RunnableConfig，而不是直接信任前端。
- **输入侧 guardrails 基于正则 + HITL 审批兜底**，没有输出侧检测，也没有引入 ML / LLM 判别。可被全角字符、Unicode 同形、拆词改写或 Base64 编码绕过。
- **parser / relation 的结构化输出依赖 markdown headings + 正则解析**，而不是 function calling / JSON schema。优先了输出长度和自然语言连贯性，但格式稳定性会随模型版本和 prompt 变化波动；解析失败时回退到 raw_text。
- **FAISS 索引使用 IndexFlatL2 穷举搜索**，文档量超过万级会出现明显延迟。当前规模下召回率 100%、部署简单，规模上去需要切换 HNSW / IVF。
- **examination 续答判定基于中文关键词列表，而不是由 primary 在 LLM 层判断意图**。最初尝试让 primary 自己识别"用户当前在答题"再 handoff 给 examination，但实际运行中 primary 经常直接代替 examination 给用户解答、纠错或给参考答案，绕过了 examination 的评估职责；改用硬约束 + 关键词白名单是规避 primary 越权抢答的妥协。

## Roadmap

下一步规划的能力升级方向，按"现有数据已经具备的价值"和"工程闭环度"排序：

- **个性化学习路径推荐**：基于 learning_store 的掌握分数、复习次数和 user_profile 的薄弱主题，由专门的 planner agent 生成"下一步学什么"序列；结合遗忘曲线（基于 timestamp + score）主动推送复习提醒。把现有学习记录从"被动查询"提升到"驱动决策"。
- **学习关系图谱可视化**：把 relation agent 多次产出的类比 / 差异 / 边界沉淀为知识图（concept 为节点，类比 / 包含 / 对比为边），前端 Learner 视图可视化"我学过的概念网络"。Relation agent 从一次性输出升级为长期沉淀。
- **代码沙箱评测**：examination agent 的代码任务从"基于用户描述判断"升级为沙箱运行（subprocess + resource limit + pytest 框架），自动判分。提升评测真实性，同时给 score 字段提供更可靠的依据。
- **多模态文档支持**：parser 扩展支持 PDF 表格、架构图（OCR + VLM 描述）、代码片段（独立 code embedding 通道）；Hybrid RAG 增加 code 检索路径，对技术文档场景的覆盖度有显著提升。
