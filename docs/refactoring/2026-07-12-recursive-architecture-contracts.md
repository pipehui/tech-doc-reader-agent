# 递归 Architecture Contracts 与 message scope 归位

## 本批结论

本批把“低耦合”从代码审查约定推进为可执行门禁：

- 将 `services/message_scope.py` 迁到 `graph/message_scope.py`，消除当前唯一的 `graph -> services` 反向依赖；
- 新增不执行应用代码的 Python AST import graph；
- 递归扫描 app 下所有子包，不再只看目录第一层 `*.py`；
- 正确解析相对 import、package `__init__` 和 `from package import module`；
- 对 core、application、runtime、graph、infrastructure、API delivery 建立六组明确 contract；
- `bootstrap.py` 与 `composition.py` 作为明确 composition roots，不进入普通向内层规则；
- 保留按目录/文件查询能力，让已有细粒度 assistant/retrieval contract 共用同一个事实源。

没有引入第三方 import-linter 依赖。当前规则需要的是稳定、可测试的 Python module 边界，内置 AST 已能完整覆盖；等需要 namespace package、动态 import 或跨 distribution contract 时再评估专用工具。

## 原门禁为什么不够可靠

旧 `_dependency_violations()` 存在两个结构性盲区：

1. `directory.glob("*.py")` 只扫描第一层，`core/subpackage/module.py` 可以绕过 core contract；
2. `ast.ImportFrom.module` 对 `from .inference import x` 只返回 `inference`，拿它与 `tech_doc_agent.app...` 前缀比较永远不会命中。

因此旧测试虽然绿色，却不能证明所有真实 import 都遵守边界。尤其 retrieval 的若干“单向依赖”测试使用相对 import，原实现实际上没有检查到完整 module name。

新分析器先从文件路径构造 importer module/package，再按 Python relative level 解析绝对目标。它还建立仓库内已知 module 集合，用于区分：

- `from package import submodule`：记录 `package.submodule`；
- `from package.module import ClassName`：记录 `package.module`，不把 class 错当 module。

contract 的失败格式固定为 `relative/path.py:line imports target.module`，CI 不需要开发者重新运行调试脚本才能定位。

## message_scope 为什么属于 graph

`message_scope.py` 的输入是 graph `State`，职责包括：

- 构造 scoped Agent 的受控 task view；
- 选择当前 Agent 自己的 tool history；
- 决定 conversation summary 如何临时进入 full Agent prompt；
- 根据 examination context 与当前用户消息决定继续答题路由。

这些都是 graph message visibility/routing policy，不是 provider service。原路径导致 `graph/nodes.py` 与 `graph/routing.py` 反向 import `services`，也让 eval/test 把 graph 行为误认为 service API。

迁移后 production、context-compaction eval 与测试全部改用 `graph.message_scope`；没有保留旧 re-export。该模块没有仓库外公开兼容承诺，继续保留 facade 只会让新调用方沿错误路径增长。

## 当前 Contract

| Contract | 允许的核心方向 | 阻断重点 |
|---|---|---|
| core isolation | core 内部 | application/API/graph/runtime/services/tools/infrastructure |
| application isolation | application -> core | delivery、orchestration、adapter、composition |
| runtime isolation | runtime -> application/core | API、graph、services、tools、infrastructure |
| graph isolation | graph -> core/graph | API、runtime、services、tools、infrastructure |
| infrastructure isolation | infrastructure -> application/core | delivery、graph、runtime、services、tools |
| API delivery | API contract/runtime facade/core | graph、persistence/retrieval backend、tools |

这里没有把所有目录粗暴排成一个全序。当前 `services` 仍混有 assistant/provider、resource container 和 `ChatRuntime` 兼容 facade；若直接声明 `services` 在某一层，只能产生大量随意 allowlist。正确顺序是先迁出职责，再逐步收紧 contract。

## 实施中遇到的问题

### 问题 A：相对 import 的 level 取决于 importer package

普通 module 与 `__init__.py` 的当前 package 不同。若统一从完整 module name 向上裁剪，`graph/__init__.py` 的 `from .builder` 会错误解析到 app 根。

处理：扫描阶段同时保存 `module` 和 `package`；普通文件使用父 module，`__init__.py` 使用自身作为 package，再应用 `level - 1` 的父级回退。

### 问题 B：`from package import name` 的 name 可能是 module，也可能是 symbol

一律拼接会把 `from graph.specs import GraphSpec` 错记为不存在的 `graph.specs.GraphSpec`；一律只记 base 又会漏掉 `from app import services`。

处理：先收集当前 package 下所有已知 module/package。只有 `base.name` 是已知 module 时才扩展，否则保留 base。

### 问题 C：全局 layer 排序会掩盖真实迁移债务

`services` 目前同时依赖 graph、runtime、infrastructure、tools，也被 API 当作 `ChatRuntime` facade 使用。为了让一个理想化 layers 表变绿而加入几十条例外，比没有门禁更危险。

处理：只声明已经稳定、含义明确的 source contracts；composition roots 明确豁免，混合 `services` 记录为后续迁移范围，不加入静默 allowlist。

### 问题 D：已有细粒度 contract 不能退化

原文件还检查 assistant model factory、retrieval taxonomy、resource locator 等具体文件。若新工具只提供 package-level检查，这些测试仍会保留另一套旧 AST 解析逻辑。

处理：`PythonImportGraph.dependencies_for_paths()` 支持递归目录或精确文件集合，旧 contract 统一改为查询同一个已解析 graph；只有“包 init 不应 eager import”这类需要检查 import 数量的测试继续直接看 AST。

## 验证范围

定向验证覆盖：

- 三种跨层写法：深层相对 import、`from app import layer`、absolute import；
- local relative 与标准库 import 不误报；
- composition-root exclusion 的匹配语义；
- 六组真实 app layer contracts；
- 原 assistant/retrieval/runtime/API AST contracts；
- message scope、examination routing 与 context-compaction 行为不变；
- Ruff 与 `git diff --check`。

| 验证 | 结果 |
|---|---|
| architecture/message-scope/context targeted pytest | 47 passed；3 个既有第三方/pytest-cache warning |
| 全量后端 pytest | 691 passed；4 个既有第三方/pytest-cache warning |
| 全仓 Ruff | passed |
| app + evals mypy | 148 source files，0 issues |
| 前端全量测试 | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | 2042 modules transformed |
| npm audit | 0 vulnerabilities |
| `git diff --check` | passed |

## 明确保留到后续

- `ChatRuntime` 仍位于 `services`。它的默认构造同时引用 Redis、resources、composition 与 assistant identity；物理移动到 `runtime` 前必须先把这些具体 factory 完全注入，不能只换路径制造 `runtime -> infrastructure/services` 新倒置；
- assistant/provider/resource 仍共处 `services`，当前没有对该混合包声明总层级；
- tools 的 retrieval DTO/filter 仍来自 `services.retrieval`，需先把稳定 contract model 下沉到 application/core；
- 动态 `importlib` 字符串不在静态 AST contract 范围内；当前 app composition 未使用它绕过层级，未来若引入 plugin loader 需单独建规则。
