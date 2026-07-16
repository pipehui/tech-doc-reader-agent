# 15 - 项目术语与易混概念

本章只解释**本项目里的实际含义**。同一个词在 LangChain、LangGraph、前端或一般架构文章里可能有更宽的定义；读源码时以这里和具体 contract 为准。

## 架构与依赖

### Owner

某条规则/数据结构的唯一权威实现位置。例如 profile merge 规则 owner 是 `application/profile_models.py`，JSON repository 只能调用它，compat facade 只能委托它。

### Contract

调用双方都依赖的稳定输入输出/行为，不只等于 type annotation。包括 Pydantic schema、Protocol、event list、tool name、checkpoint state、文件 envelope 和错误语义。

### Projection

从事实源推导出的展示/传输视图。例如 runtime 从 graph snapshot 的 dialog stack、guardrail 和 messages 投影 session state；projection 不应成为第二份可独立写 truth。

### Composition root

唯一允许选择并连接 concrete implementation 的边界。本项目是 `bootstrap.py`、`composition.py` 和 infrastructure resource factory。application/tool/graph 不应自己 new 外部 adapter。

### Port

内层定义的 capability interface，通常是 Protocol，例如 `DocumentRetrieverPort`、`UserProfileRepositoryPort`。它表达“能做什么”，不包含 Redis/FAISS/文件细节。

### Adapter

Port 的 concrete implementation 或 delivery 翻译器，例如 `HybridRetriever`、`JsonUserProfileRepository`、Redis approval repository、FastAPI route、LangChain tool。

### Facade

给调用方的窄入口。`ChatRuntime` 是当前 runtime facade；`services/retrieval` 是兼容 facade。Facade 可委托多个对象，但不应复制业务规则。

### DTO / Payload / Domain model

- DTO/payload：跨 HTTP/SSE/JSON 边界的可序列化形状；
- domain model：应用内部带验证/行为的 typed value，如 `LearningRecord`；
- 不要让 dict payload 从 repository 一路穿到 domain 决策，也不要把 frozen dataclass 直接 JSON dump。

## 启动与运行时

### `AppResources`

一个 runtime 实例持有的 concrete store/retriever/service/provider aggregate。`AppResources.create` 负责构造并触发各 adapter 的加载/初始化；它目前没有通用 `close()`，运行时可用期由 `RuntimeLifecycle` 管理。它也不是让任意模块随便取依赖的 service locator。

### `RuntimeLifecycle`

按顺序启动 resources、RedisSaver、graph，并在失败/退出时逆序释放的对象。它拥有“何时可用”，不拥有聊天业务。

### `ChatRuntime`

API/CLI 面向的稳定 facade。它委托 lifecycle、execution、approval 和 session query 服务，不直接实现所有 graph/checkpoint 细节。

### Checkpointer / Checkpoint

LangGraph 把一次 thread 的 messages、workflow、dialog stack、预算等 State 保存到 Redis 的机制/快照。它是后端会话恢复事实，不等于浏览器 transcript。

### Thread ID

LangGraph checkpoint key 的逻辑身份：

```text
<user_id>:<namespace>:<session_id>
```

同一 session ID 在不同 tenant 下不是同一 thread。

### Session

用户可继续对话的一条运行线，外部用 session_id 表示；真正隔离要连 tenant 一起看。前端 recent sessions 也是 tenant+ID 条目。

### Tenant / User ID / Namespace

- tenant 是 `user_id + namespace`；
- user ID 区分用户；
- namespace 区分同用户下的知识/使用域；
- 它们参与 checkpoint、学习/profile 查询、approval key、URL 和 localStorage key。

知识文档库目前逻辑上共享，不因 metadata 含 tenant 字段就自动私有隔离。

## Graph 与 Agent

### State

LangGraph checkpoint 中流经节点的 TypedDict。只放跨节点/跨请求必须恢复的事实或可观测累计值，不放 repository、tracker、model client 等运行对象。

### Primary Agent

入口/监督角色：规划 workflow、选择 handoff、读取/写学习画像，并在步骤间路由。它不应复制各专业子 Agent 的具体任务。

### Subagent

parser、relation、explanation、examination、summary 五个专门角色。它们由 dialog stack 标识当前 step，并通过 finish/leave 回到后续流程。

### Assistant

本项目中封装 prompt + model runnable + empty-output retry + LLM usage 捕获的执行对象。它不是 Agent 的全部图节点；graph wrapper 还加 budget/context/route。

### `AssistantDefinition`

Agent 构建后的声明：Assistant、safe/sensitive tools 和 execution identity。Control tools 只在构建 runnable 时绑定，不保存在定义对象里；message scope、completion/result key 和 graph tool node policy 位于 `AgentSpec`。

### Handoff

Primary 发出 `To*Assistant` tool call，把控制权和 dialog stack 推入某个 subagent。它是图路由命令，不是真正访问外部资源的 tool side effect。

### `dialog_state`

当前嵌套对话/Agent step 栈。enter push、finish/leave pop。session 当前 Agent 优先从栈顶投影。

### `workflow_plan`

由 `PlanWorkflow` 产生的有序步骤列表，例如 parser -> relation -> explanation。它描述计划，不等于 dialog stack。

### `plan_index`

下一个/当前计划推进位置。正常 finish 加一；leave/escalate 会清理或回 primary；不要把它当消息序号。

### Finish

子 Agent 正常完成：保存相应 result、pop dialog、plan_index+1，然后按计划进入下一步；计划耗尽时直接结束本轮，不再回 primary 重新判断。

### Leave / Escalate

子 Agent 用 `CompleteOrEscalate` 表示无法/无需按正常结果完成，pop 并把控制权交回 primary，通常清理当前 workflow。它和 finish 的状态语义不同。

### Safe tool

无需人审即可执行的工具节点。仍受 tool policy、execution budget 和错误/reflection 约束。

### Sensitive tool

对应 ToolNode 在 graph `interrupt_before` 集合中；模型生成 call 后先 checkpoint/等待批准，批准才执行。

### Control tool

改变图控制流而非访问业务依赖的 tool，例如 handoff、PlanWorkflow、CompleteOrEscalate。不要把它和 safe/sensitive 外部操作混为一谈。

### Message scope

某 Agent 真正传给 prompt 的 messages 视图。局部 Agent 通常只看当前 step 相关交换；summary 看全 history。Checkpoint messages 多不等于每次模型都看全部。

### Node name / Agent name

node 是图执行位置，agent 是角色。多个 node（assistant/safe_tools/sensitive_tools/finish）可属于同一 agent。SSE 用 mapping 从 node/metadata 推断 agent，不能把两者当同一个字符串。

## 工具、审批与错误恢复

### LangChain tool

模型可调用的 schema + Python adapter。它负责解析模型参数/注入 config，把请求委托给 application port/service，并把结果序列化为 ToolMessage 内容。

### `ToolBundle`

11 个项目工具的稳定 typed 目录。Agent definition 从字段选择工具，不在全局按名字查找。

### Tool call ID

AIMessage 中每次 tool call 的关联 ID；ToolMessage 必须用同一 ID 返回。学习写入还把它用于幂等 command identity。

### Tool policy

执行前的局部防循环规则：parser retrieval 总次数、连续相同 name+args。Block 会生成 error ToolMessage，不等于抛未知 Python exception。

### Interrupt

LangGraph 在指定 node 之前暂停并保存 checkpoint。当前主要用于 sensitive tool。`pending_interrupt` 是 session projection，不说明具体 tool 一定存在于浏览器缓存。

### Guardrail input approval

中风险用户原始输入先存 Redis approval repository，尚未进入 graph。批准后消费原输入并启动 graph；拒绝生成受控反馈。

### Sensitive tool approval

AI tool call 已在 graph checkpoint 中，暂停在 ToolNode 前。批准 `stream(None)` 继续；拒绝注入成对 error ToolMessage 再恢复。

### Reflection

工具 error 后的有限修复状态机。仅对 allowlist code 给模型有限次数改参数；否则要求基于已有证据 finalization，继续违规会 terminal。

### Safe error

`ApplicationError` 暴露的 code/retryable/safe_message/dependency/tool/cause_type。它故意不含原始异常文本；“安全”指适合跨边界，不代表操作成功或无风险。

## 请求、SSE 与前端

### Graph part

Runtime 从 LangGraph stream 得到的 `(mode, data)` 片段。主要 mode 是 `messages`（token/message）和 `updates`（node state delta），还不是 wire-format SSE。

### SSE event

后端把 graph part 翻译成带事件名和 JSON payload 的 Server-Sent Event。event name 与 payload schema都是前后端 contract。

### Snapshot event

包含当前完整视图，如 `session_snapshot`。重复应用通常应覆盖/merge 到同一状态，适合恢复。

### Delta event

只描述本次变化，如 usage/context/provider retry delta。消费者通常还会收到/维护累计状态，不能把 delta 当总量。

### Token

模型输出增量文本。前端追加到 responseId+agent 的 assistant message，不进入 Inspector event 列表。

### Agent message

后端最终 AIMessage 内容。前端用它的 finalContent 覆盖已经拼接的 token 草稿，保证最终文本与 checkpoint 一致。

### `responseId`

前端为一次 `/chat` 或 `/approve` stream 生成的本地关联 ID，把同一响应内的多 Agent 消息/tool card 归组。它不是 session ID、trace ID 或 LangChain message ID。

### Message ID

后端 LangChain message 的 ID，用于 checkpoint/history/summary source range。前端 bootstrap 缺失时可生成展示 ID；不要与 tool call ID 混用。

### Trace ID

一次 operation 的观测关联 ID，可从 body/header生成；进入日志、SSE context、可选 Langfuse。不决定 checkpoint identity。

### Transcript

浏览器 versioned localStorage 快照：messages + Inspector events + toolCalls。它优化展示恢复，不是后端事实源。

### History

后端从 checkpoint messages 投影的会话历史。当前能恢复聊天文本，但不能完整重建浏览器 tool card/event association。

### Inspector

前端观察 SSE/trace 事件的视图。忽略 token、最多保留最近 3000 events；它是调试投影，不应控制 graph 业务状态。

### Learner

前端读取 learning overview、计划和考试相关消息的视图。它不直接读本地 learning JSON。

## 学习状态与持久化

### Learning record

某 tenant 对一个 `knowledge` 的学习事实：timestamp、score、reviewtimes。再次精确命中同知识点时 reviewtimes+1。

### Memory fragment

较长期的学习轨迹片段：learned、stuck_point、misconception、review_hint。以 tenant+kind+topic upsert，包含置信度和来源 session。

### User profile

稳定偏好/主题集合：经验、解释风格、深度、语言、known/weak topics、notes。独立于 learning snapshot 保存。

### Learning overview

API/前端对学习记录的统计投影（总数、平均分、需复习等），不是另一份写存储。

### Unit of Work (UoW)

在进程内 clone 当前 learning snapshot，应用一次完整 mutation，repository save 成功后才替换活动状态。保证记录、memory 和幂等结果同提交。

### Snapshot

一个逻辑时刻完整一致的数据集合。Learning snapshot 含 records/memories/processed commands；FAISS snapshot 含 index/documents/chunks。

### Generation

不可变的新版本目录 ID。写入在新目录完成并回读校验，最后 current manifest 原子切换到它。

### Manifest

`current.json`：指出活动 generation，并保存 schema、count/dimension 等验证信息。它是 publication pointer，不是全部数据。

### Atomic JSON write

在同目录写临时文件，flush/fsync，再 `os.replace`。保证单文件读者看到旧完整版本或新完整版本，不看到半份 JSON。

### Idempotency key

标识“这是同一次命令”的稳定摘要。学习命令使用 tenant+session+tool call ID。

### Fingerprint

标识该命令全部业务参数内容。相同 key 但 fingerprint 不同是冲突；相同则重放旧结果。

### Process-local lock

`threading.Lock` 只协调同一 Python 进程内线程。多个 worker 共享本地文件时不等于分布式事务/文件锁。

### Legacy fallback

没有新 manifest/path 时读取旧格式；一旦新 current 存在，损坏也不回退旧数据。Fallback 让服务能启动旧数据，不替代显式 migration/backup。

### Shadowed profile

同一 default tenant 已有新路径 profile 时，旧 root profile 不再被读取。迁移保留旧文件并标记 shadowed，不覆盖更新的数据。

## 文档检索

### Document

知识库返回的完整逻辑资料：id/title/content/source/metadata。目前共享知识库。

### Chunk

为 embedding/向量检索把 document content 切出的片段，带 doc_id、chunk index/text 和规范化 metadata。Semantic 命中 chunk 后再映射回 document。

### Embedding

provider 把文本转为固定 dimension 浮点向量。更换模型/dimension通常需要重建 FAISS index。

### FAISS `IndexFlatL2`

对已存向量做精确 L2 距离搜索的本地 index。distance 越小越相近；索引类型的“精确”不等于语义召回 100%。

### Exact ranking

query 完整字符串是否为 title/content 子串的字面排名。不是 token fuzzy match。

### BM25

基于 title+content token 的词频/文档频率相关性。适合关键词与专有名词。

### Semantic ranking

query embedding 与 chunk vectors 的 L2 Top-K，再映射/去重成 document ranking。filter 当前通过扩大候选后 post-filter。

### Hybrid retrieval

同时运行 exact、BM25、semantic，再用 RRF 融合。Semantic typed failure 时可降级到关键词路线。

### Vector mode

只运行 semantic ranking。依赖失败时不降级，因为调用方明确要求语义结果。

### RRF

Reciprocal Rank Fusion：每路按 `1/(k+rank)` 贡献，不直接相加量纲不同的原始 score。

### Signal

SearchResult 中每条检索路线的解释信息：rank、路内 score、distance等。顶层 score 是 RRF，不是 signal score。

### Metadata normalization

把 user/namespace/category/tags 补齐为固定形状，并从标题/内容推断缺失 category/tags。既影响 filter 也影响 index signature。

### Broad category

用户查询“RAG”“LangGraph”时，filter 转为广义 tag，而非不存在的精确细 category，从而覆盖 basic/advanced/core 等子类。

## 重试、预算与上下文

### Transport retry

外部 provider/网络 operation 的有限重试。只有 typed error 可重试、调用声明幂等且 attempts 未耗尽时发生。

### Empty-output retry

Assistant 对模型返回空 content/tool calls 的有限再次调用。它不同于网络失败 retry，也会产生 LLM usage。

### Provider retry usage

经过通用 RetryExecutor 的 embedding/web operation ledger：attempts、retries、wait、outcome。不是 LLM call ledger。

### LLM usage

ChatModel 每次 transport attempt 的 calls/input/output/total token 与可估成本，进入 workflow budget。

### Request budget

本次 HTTP/CLI operation 的 monotonic elapsed time window。等待审批后新 approve operation 有自己的 request window，但 workflow usage 随 checkpoint 延续。

### Workflow budget

跨节点、可 checkpoint 的 LLM/tool/tokens/cost 上限。0 setting 当前表示关闭该维度。

### Before check / After check

操作前用当前+投影使用量决定是否允许；操作后用实际上报量决定是否终止下一跳。两者缺一会浪费调用或越限继续。

### Usage unreported

配置 token/cost 上限但 provider 没报告对应 usage。系统不能证明安全余量，因此 fail-closed 停止，不按 0 处理。

### Context metrics

checkpoint message/bytes 与实际 prompt message/bytes/token 的可观测数据，用于判断 message scope 和 compaction效果。

### Context compaction

在新 Human turn、安全闭合工具交换且无 active workflow/dialog/reflection 时，把较早 closed turns 变成确定性摘要，保留最近 N turns。

### Conversation summary

带 source ranges/hash/predecessor/generator/schema 的持久摘要，不只是一个随意字符串。它在 prompt 中补充被移除 messages 的信息。

## Eval 与质量门禁

### Characterization test

重构前后锁定当前行为/拓扑/import/object identity 的测试。它证明“行为没意外变”，不自动证明行为本身最优。

### Contract test

验证跨层/跨语言/跨版本约定，例如 SSE event/payload、repository behavior、port/public export。

### Architecture test

扫描 import graph/文件集合/关键源码模式，阻止层级反向依赖和 compatibility namespace 再增长。

### Eval case / Corpus

Eval case 是查询/预期；retrieval corpus 是被检索的版本化文档集合。空 corpus 跑完所有 case 只说明 runner 能跑，不说明质量。

### Baseline

经过审查、绑定 dataset/settings/identity 的历史结果，用于比较 candidate。不能把当前失败输出无审查地覆盖为 baseline。

### Artifact manifest

记录 commit、prompt/model/runtime identity、dataset hash、settings 等，让结果可追溯并判断能否与 baseline 比较。

### Regression policy

定义 candidate 相对 baseline 哪些指标允许怎样变化的机器规则。质量变化有意时，应审查新 baseline/policy，而不是只放宽到 CI 通过。

## 最常混淆的十组词

| 不要混为一谈 | 区别 |
| --- | --- |
| session ID vs thread ID | thread ID 还包含 tenant |
| checkpoint vs transcript | 后端 graph 事实 vs 浏览器展示缓存 |
| history vs transcript | 后端 messages 投影 vs messages+tools+events |
| Agent vs node | 角色 vs 图执行位置 |
| workflow plan vs dialog stack | 未来步骤列表 vs 当前嵌套控制栈 |
| finish vs leave | 正常产出并推进 vs 退出/escalate |
| guardrail approval vs tool approval | graph 之前的原始输入 vs checkpoint 中待执行 tool |
| tool call ID vs response ID | 后端 call/result 关联 vs 前端一次 stream 分组 |
| RRF score vs semantic score/distance | 跨路排名融合 vs 单路解释量 |
| retry budget vs workflow budget | 网络重试策略/统计 vs 全工作流资源上限 |

回到 [研读文档总索引](README.md)。
