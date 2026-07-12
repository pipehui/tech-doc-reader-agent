# Phase 4 重构日志：Profile Ports 与 Service 边界

## 1. 重构范围

`application/profile_service.py` 原先同时拥有：

- `UserProfileRepositoryPort`，供 persistence adapter/contract tests 使用；
- `UserProfileServicePort`，供 tools 与 Learning API 使用；
- `ProfileMemoryReaderPort` compatibility alias；
- `UserProfileService` use case；
- Agent prompt context 的 profile/memory summary formatter。

不同 consumer 为一个 Protocol 被迫依赖整个 service module。本批新增 `application/profile_ports.py` 作为 repository/service/memory capability 的唯一定义源，迁移仓内 persistence、tools、API、contract tests 与 legacy services facade 的 type imports。`profile_service.py` 继续拥有 service 与 context formatter。

```text
tools / Learning API --------> profile_ports.UserProfileServicePort
repository contract tests ---> profile_ports.UserProfileRepositoryPort
legacy facade / service ------> profile_ports.ProfileMemoryReaderPort
concrete composition ---------> profile_service.UserProfileService
profile context generation ---> profile_service.format_user_profile_summary
```

对外 profile JSON、HTTP schema、tool schema、summary 文本、tenant path、update/merge/save 行为均未改变。

## 2. 为什么 formatter 仍归 service

`format_user_profile_summary()` 不是 FastAPI response serializer。`UserProfileService.context_summary()` 直接使用它构造 Agent 可见的稳定 profile + memory context；把 formatter 移入 API/delivery 会造成 application 反向依赖 delivery，移入 compatibility facade 又会让 production service 依赖 legacy namespace。

因此本批按职责而非“函数都要单独成文件”处理：port contract 独立，service 与其纯 context projection 保持内聚。

## 3. 实际遇到的问题与解决

### 问题 A：既有日志承诺了 type import 兼容

初次迁移内部调用方后，`ProfileMemoryReaderPort` 只存在于新 `profile_ports.py`。复查历史重构日志时发现此前明确记录：`application.profile_service.ProfileMemoryReaderPort` 暂时保留，删除需先完成仓外 type import 审计。直接删除虽然仓内测试可通过，却会违反已写下的兼容承诺。

解决：

- 三个 port 的 class/alias 唯一定义都在 `profile_ports.py`；
- 所有仓内 production caller 直接 import 新事实源；
- `profile_service.py` 通过 import + `__all__` 保留 type-only re-export，不复制 Protocol；
- 删除条件仍是明确 deprecation 或仓外 import 审计完成。

Architecture test 用 AST 扫描 app imports，禁止仓内模块再通过 service 取得 ports；允许的 service symbols 只有 `UserProfileService` 与 `format_user_profile_summary`。兼容出口存在，但不再扩散内部耦合。

### 问题 B：不应为表面对称拆 ApprovalRepository

相邻 `approval_models.py` 也同时包含 request model 与 repository Protocol，但 repository 的唯一聚合就是 `GuardrailApprovalRequest`，两者共同演进且没有 service implementation 混入。为了目录外观把它们拆开只会增加跳转和 import，不会缩窄 consumer 依赖。

解决：本批只处理有独立 consumer/变化轴证据的 Profile ports，Approval 保持现状。

### 问题 C：ProfileMemoryReaderPort 不能再复制一份 Protocol

Profile memory reader 与 learning `MemoryReaderPort` 完全同形。重新定义会产生第二个签名事实源，并让未来返回类型变化需要同步修改。

解决：`ProfileMemoryReaderPort = MemoryReaderPort` alias 移到 `profile_ports.py`；只有一个 Protocol definition，Profile 只提供语义化名字。

## 4. 架构守卫

- `profile_ports.py` 必须拥有 repository/service Protocol definitions。
- `profile_service.py` 不得重新定义任何 Protocol。
- Tools dependencies 与 Learning API 直接引用 `profile_ports`。
- 所有 app module 从 `profile_service` 只能 import service 或 formatter；port import 会令 AST contract 失败。
- Legacy `services/user_profile.py` 仍是受控 compatibility facade，只委托 application service 与 infrastructure repository。

## 5. 验证结果

| 检查 | 结果 |
|---|---|
| Profile model/service/tool/API/repository/compatibility/architecture 定向测试 | 77 passed，4 warnings |
| Profile 相关 mypy | passed，5 个 source files |
| 全量 pytest | 715 passed，4 warnings |
| 全仓 Ruff | passed |
| app + evals mypy | passed，157 个 source files |
| 前端 Vitest | 20 files / 85 tests passed |
| 前端 TypeScript check | passed |
| 前端 production build | passed，2042 modules transformed |
| npm audit | 0 vulnerabilities |

四条 pytest warning 中三条是 LangGraph/LangChain/Starlette 第三方弃用提示，另一条是本机 `.pytest_cache` 无写权限；测试用例自身全部通过。

## 6. 后续约束

- 新 Profile consumer contract 进入 `profile_ports.py`，不要定义在 tools/API 或 concrete service。
- Profile model 的 merge/validation/version 规则仍由 `profile_models.py` 拥有。
- `profile_service.py` compatibility re-export 不得增加新名字；删除需有明确证据。
- 若 context formatter 出现多个独立 consumer/格式版本，再提取 application projection；当前不为文件大小过度拆分。

本批提交主题：`refactor: separate profile application ports`。
