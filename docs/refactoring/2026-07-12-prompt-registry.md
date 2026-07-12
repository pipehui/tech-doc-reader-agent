# Package-resource PromptRegistry 与无损 prompt 迁移

## 本批目标

六个 assistant 模块原先同时承担两种职责：定义数十到上百行 system prompt，以及声明该 role 的 safe/sensitive/
control tools。每个文件还重复 `ChatPromptTemplate.from_messages(...) + messages placeholder + time partial`，prompt
内容没有稳定 ID、内容 hash 或启动校验。代码重构与 prompt 文案修改也很难从 diff 和 eval 中分开归因。

本批完成 B6 中不依赖模型选型的结构工作：把 prompt 迁入 package resources，建立 manifest 驱动的
`PromptRegistry`，在 composition root 启动期校验资源、hash 和 placeholder；primary 按原段落拆成 16 个 section。
本批不改任何 prompt 字符，且不声称已完成 model ID/eval artifact 全链路。

## 迁移基线

切换运行路径前，先从六个现有 `ChatPromptTemplate` 运行时对象提取 system template，并固定以下 SHA-256：

| Role | 字符数 | 行数 | Prompt ID | SHA-256 |
|---|---:|---:|---|---|
| primary | 4587 | 107 | `tech-doc-reader.primary.v1` | `034f2970a0d7fead2f8efdca693dbb6c59585c2400b839a0dc3dc9e9609ba9a3` |
| parser | 2991 | 76 | `tech-doc-reader.parser.v1` | `208e4aa024549388a23d403f7f238cd470f45b5c2899607bc3f2458aba7d5873` |
| relation | 1672 | 69 | `tech-doc-reader.relation.v1` | `303cfb81a144940efef6ab7ea36881bb7bb6251c8897dff986f7da98881c634e` |
| explanation | 968 | 44 | `tech-doc-reader.explanation.v1` | `052b3f2de5cc1759b217dc4f4aff5a7e3d4e74ac9f6cb013d66d5412fd3a72e0` |
| examination | 2299 | 84 | `tech-doc-reader.examination.v1` | `c9e3b3c334ca71a36873d2751e8d7adfcdfeadea4df876d4c2fb9562d61a908b` |
| summary | 1741 | 63 | `tech-doc-reader.summary.v1` | `497c761fb817c51e6487dc0d19b16f4cb6d3a61213a7444d34aaae783ab3198d` |

迁移中曾让旧 Python prompt 与新 registry 同时存在，逐 role 比较 system 内容、input variables、optional variables、
partial variables 和 hash；全部相等后才删除内联字符串。最终测试继续把上述 pre-migration hash 作为 golden contract。

## 最终边界

### 1. manifest 是 prompt 身份与组合清单

`assistants/prompts/manifest.json` 使用 schema version 1，每个 role 声明：

- 稳定 prompt ID；
- 预期 SHA-256；
- 有顺序的 resource paths；
- 必需 placeholder 的精确集合。

registry 拒绝：未知 schema、缺失/多余 role、重复 prompt ID、空字段、重复资源、不安全的绝对/反斜杠/`..`
路径、资源缺失、hash 漂移、非法 braces，以及 placeholder 缺失或意外增加。所有六个 role 会一次性加载和校验，
不会等到某个 workflow 第一次路由到对应 assistant 才失败。

### 2. primary 是可组合 section，不是一个 100 行资源

primary 原 system 内容按已经存在的双换行段落无损拆成 16 个命名文件，例如：

```text
00-role
01-principles
02-learning-records
05-user-profile
09-adaptive-paths
11-planning-rules
15-runtime-context
```

manifest 固定 section 顺序，registry 使用原来的 `\n\n` 分隔符组合。其他五个较短 prompt 当前各使用一个资源文件；
后续如果继续拆分，只需调整 manifest resources，并为有意的内容变化更新 ID/hash。

### 3. ChatPromptTemplate 构造只有一个实现源

`PromptRegistry` 集中构造：

```text
system resource(s)
  + optional {messages} placeholder
  + partial {time}
  -> ChatPromptTemplate
```

六个 role 模块现在只声明工具组合并接收 `PromptArtifact`，不再 import `ChatPromptTemplate`、`datetime`，也不读取
package resource。架构测试禁止这些内联/加载模式回归。

composition root 的依赖链变为：

```text
build_prompt_registry()
  -> build_assistant_registry(models, tools, prompts)
  -> role builder
  -> AssistantDefinition
```

import assistant 模块仍不会读取 Settings、创建 model client 或访问 prompt 文件；实际资源 I/O 和校验发生在生产 graph
composition 时。

### 4. prompt identity 进入 runnable metadata

`AssistantDefinition` 保存 `prompt_id` 与 `prompt_sha256`。构造的 LangChain runnable 也带有：

```text
assistant_role
prompt_id
prompt_sha256
```

现有 callback/Langfuse runnable 链可以取得这组 metadata。B6 的“trace/eval 同时记录 prompt ID 和 model ID”仍未完整：
model provider 尚未提供稳定 model identity，offline eval artifact 也尚未统一写入这两个字段，因此相关 TODO 保持未完成。

## 实施中遇到的问题

### 问题 A：人工复制 prompt 无法证明行为未变

六个模板包含约 14K 字符，手工搬运很容易丢空行、中文标点或 placeholder，并且普通单测未必触发所有 role。

处理：通过旧运行时模板自动生成 apply-patch 资源内容和 manifest；切换前执行旧/新逐字比较；切换后使用固定 hash、
input/optional/partial variables 和完整 graph composition 测试继续守护。

### 问题 B：资源文件结尾换行会改变 hash

源码字符串不含文件结尾 newline，但文本资源通常必须以 newline 结束；Windows checkout 还可能使用 CRLF。直接
`read_text()` 后拼接会给每个 section 多加字符，primary 的双换行也会变成三换行。

处理：resource reader 只移除一个文件终止换行，再按 manifest 用 `\n\n` 组合。Python universal-newline 读取会把
CRLF 归一化为 LF，因此同一 manifest hash 在 Windows checkout 与 Linux Docker 中一致。hash 等价测试验证了该规则。

### 问题 C：messages 在 LangChain 中是 optional/partial，不在 system 文本内

只用 `Formatter.parse(system_template)` 会看到 `time/user_info/learning_target`，却看不到结构层的 `{messages}`；
只看最终 `input_variables` 又会漏掉已经 partial 的 time 和 optional messages。

处理：manifest 的 required placeholders 表达完整 prompt contract。registry 将 system fields 与结构性 messages
合并后做精确集合比较；测试另外固定最终 input/optional/partial variables。

### 问题 D：全局 registry 会把资源 I/O 重新带回 import time

如果在模块顶层创建 singleton，虽然不再创建模型，import role/registry 仍会读文件并可能失败，破坏现有可测试性边界。

处理：只提供无副作用的类型和 factory；`build_prompt_registry()` 由 composition root 显式调用。role builder 必须接收
artifact，不能自己向 registry/service locator 查询。

### 问题 E：stable prompt metadata 不等于完成可归因 eval

把 prompt ID/hash 写进 runnable metadata 后，很容易误把对应清单整体勾完，但当前 model provider 仍只有 primary/backup
client，没有 role-specific model ID/version；eval artifacts 也没有统一 manifest。

处理：本批只记录实际完成的 prompt identity 与 callback metadata，保留 model/eval 后续项，不虚构可观测性闭环。

## 测试与门禁

新增/扩展覆盖：

- 六个 package prompt 与 pre-migration SHA、变量集合一致；
- primary manifest 确实引用 16 个 section；
- hash drift、缺 placeholder、缺 role、不安全路径、缺 manifest 启动失败；
- prompt ID 唯一并进入 AssistantDefinition/runnable metadata；
- 六个 role 的 safe/sensitive/control tool 顺序不变；
- role 模块不重新定义或加载 prompt；
- composition 和 graph compile/topology 仍可离线构建。

| 验证 | 结果 |
|---|---|
| prompt/assistant/composition/graph/architecture 聚焦 pytest | 27 passed |
| 全量后端 pytest（禁用本机不可写 cache） | 390 passed，3 个既有第三方 deprecation warning |
| Ruff（`tech_doc_agent tests evals scripts`） | passed |
| 既有 CI mypy gate | passed，12 source files |
| 本批 direct mypy（`--follow-imports=skip`） | passed，14 source files |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules transformed |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| `git diff --check` | passed |

本批没有前端源码或样式变化，因此不重复浏览器视觉 smoke。三条 pytest warning 仍来自
LangGraph/Starlette 的既有弃用提示。

## 保持不变与后续工作

保持不变：六个 system prompt 的每个字符、messages optional 语义、time partial 格式、primary 的 user_info input、
examination/summary 的 learning_target input、tool 绑定及顺序、parallel_tool_calls=False、模型 primary/backup fallback、
graph topology 和运行时输出。

后续仍需独立完成：role-specific model/timeout/provider 配置、streaming/usage metadata contract、model ID 与 prompt ID
共同进入 trace/eval artifact。任何 prompt 内容改动都应升级稳定 ID/hash，并与代码结构重构分批验证；不能在本批
“无损迁移”提交中顺手改写文案。
