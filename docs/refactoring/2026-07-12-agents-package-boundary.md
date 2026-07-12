# Agents Package 边界与 Prompt 资源无损迁移

## 本批结论

本批将 role 执行相关职责从混合的 `services/assistants` 物理迁到顶层 `agents`：

- 迁移 assistant base、六个 role definition、registry、model provider、prompt registry 与 execution identity；
- 将 23 个 prompt/manifest package resources 一同迁到 `agents/prompts`；
- package 内部调用改为相对 import，避免模块自引用绑定到物理根路径；
- composition、bootstrap、eval 与测试统一改用 `tech_doc_agent.app.agents`；
- `PromptRegistry` 的动态 resource package 更新为 `tech_doc_agent.app.agents.prompts`；
- 没有保留 `services.assistants` 兼容 re-export，避免旧的错误依赖方向继续增长；
- 新增 agents 递归 architecture contract，并让 core/application/runtime/graph/infrastructure/API/tools 同时禁止反向 import agents。

迁移没有修改 prompt 文案、manifest 内容、role 名称、tool binding、model route 或 identity 计算规则。

## 为什么不是继续放在 Services

原目录名把三类不同概念压进了同一个 `services` 桶：

| 原职责 | 实际含义 | 本批归属 |
|---|---|---|
| 六个 assistant role | Agent 行为与工具绑定定义 | `agents` |
| PromptRegistry / prompt resources | Agent 输入契约与版本化身份 | `agents` |
| Model provider / execution identity | Agent 执行依赖与可追溯身份 | `agents` |

这些模块不消费 retrieval、resource container、persistence 或 runtime service。反而由 composition root 把模型、工具和 prompt 组合成 graph 所需的 registry。继续放在 `services` 会让目录结构暗示不存在的 service-layer 关系，并允许 graph/tools/runtime 通过旧路径重新依赖具体 role。

## 最终依赖方向

```text
bootstrap / composition
  -> agents model provider / prompt registry / role registry / identity
  -> graph spec and runtime lifecycle

agents
  -> core budget/settings/error primitives
  -> graph command types
  -> bound ToolBundle
  -X services / runtime / API / infrastructure / composition roots
```

composition roots 是具体装配点，因此可以 import agents。其他 app layer 由递归 import graph 阻止反向依赖具体 role。agents 自身也有独立 contract，不能借深层文件或相对 import 绕回 services。

## Prompt 与 Identity 不变量

Prompt 是 `importlib.resources` 动态加载的 package data，普通 AST import graph 看不到这个字符串依赖。仅移动 Python 文件而不更新 `PROMPT_PACKAGE` 会在运行时构造 registry 时失败，即使静态 import 全部通过。

本批使用三层证据保护无损迁移：

1. 将 `PROMPT_PACKAGE` 更新为新 package，而不改 manifest 或 Markdown；
2. 对新目录下 23 个非缓存资源逐个比较 `git hash-object` 与移动前 `HEAD` blob，全部相同；
3. 运行 PromptRegistry 的 manifest/schema/role/resource/SHA/placeholder 测试，以及 assistant/runtime identity 与 eval manifest compatibility 测试。

Package 物理路径不参与 prompt ID、prompt SHA、model route identity 或 assistant fingerprint。相关输入字段与算法没有改动，因此这次目录重排不会把代码重构伪装成 prompt/model 实验。

## Compatibility 策略

仓内所有 production、eval 与测试调用方已一次迁移到新路径。旧 `services.assistants` 没有保留 facade：

- facade 会使 services 继续公开 agents，破坏依赖方向的唯一答案；
- architecture contract 必须为兼容路径增加例外，随后新调用方仍可能继续引用旧层级；
- 当前变更链尚未作为稳定外部 Python SDK 发布，不需要为仓内私有 import 增加永久兼容债务。

如果存在未纳入本仓测试的外部脚本直接 import 旧路径，它需要同步迁移；这是本批明确接受的内部模块路径变更。

## 实施中遇到的问题

### 问题 A：静态 import 全清零不代表资源可加载

`PromptRegistry` 使用字符串形式的 package 名。批量替换 Python import 后，旧字符串仍可绕过 Ruff、mypy 和 architecture graph。

处理：单独检索 `services.assistants.prompts`，更新 resource package，并把 prompt registry/identity/eval compatibility 测试放进定向门禁。

### 问题 B：文件移动与内容编辑让 Git 暂时显示 add/delete

移动后同时改 import，未重新暂存前 Git 无法稳定显示 rename；只看 `git diff --summary` 容易误以为 prompt 被重建。

处理：不依赖 rename 百分比证明无损，直接比较每个新资源与旧 `HEAD` 路径的 blob hash。最终暂存后再复核 rename 和 diff。

### 问题 C：新增顶层包不会自动进入已有 layer contract

原六组 contract 建立时不存在顶层 `agents`。若只给 agents 自身加规则，core、graph、runtime 或 API 未来仍可反向 import concrete role。

处理：新增 `AGENTS_CONTRACT`，同时把 agents 加入 core/application/runtime/graph/infrastructure/API/tools 的 forbidden prefixes，形成 composition-root 之外的双向隔离。

### 问题 D：测试也保存了物理路径假设

retry wiring 测试通过允许文件集合锁定 `RetryExecutor` 的使用位置；architecture tests 也直接读取 assistants 目录。它们不是 production import，却会在移动后产生错误失败或失去覆盖。

处理：更新路径型断言到 `app/agents`，保留原测试语义，不通过扩大 allowlist 绕过失败。

### 问题 E：开发文档仍可能把后续修改引回旧目录

源码 README 更新后，`docs/development.md` 的 Prompt Resources 指南仍指向旧路径。

处理：全仓检索非历史文档中的旧路径并同步更新；历史重构日志保留当时事实，不做机械改写。

## 验证范围

定向验证覆盖 PromptRegistry、assistant identity/base/registry、eval manifest、graph budget/context、health route、retry wiring 与 architecture contracts；全量门禁覆盖后端、前端和静态分析。

| 验证 | 结果 |
|---|---|
| prompt resource blob 对比 | 23 files unchanged |
| agents/prompt/identity/graph/architecture targeted pytest | 119 passed；4 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 697 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 150 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- `services` 仍混合 resource container、embedding/web provider、retrieval implementation 以及少量兼容模块，尚不能声明单一 layer contract；
- `agents/model_factory.py` 仍直接构造当前 ChatOpenAI-compatible client。role-specific provider/timeout 配置需结合真实 usage/cost/latency 另批设计；
- agents 当前消费完整 `ToolBundle`，后续若 role 数量或插件能力扩大，可评估 role-scoped tool capability protocol，但本批不为未来假设制造更多接口；
- 仓外脚本若直接 import 私有旧路径，需要显式迁移，没有隐藏兼容 fallback；
- 本批不修改 prompt 内容，也不据此声称模型质量、成本或延迟改善。
