# 前端 CSS Tokens 与 Feature 边界拆分

## 本批目标

组件拆分后，全部样式仍集中在根 `frontend/styles.css`：2023 个物理行、从 theme tokens 到 responsive rules 全部共享一个 cascade。修改 Landing 或 Inspector 仍要在单一长文件定位，且没有门禁阻止媒体查询、feature selector 再次混回任意位置。

本批完成前端 TODO F6：不改 class、不换 Tailwind/CSS-in-JS、不重做视觉，只按 tokens/base/shell/feature/responsive 建立可维护的 CSS 边界，并证明最终产物完全等价。

## 最终结构

```text
frontend/src/styles/
├── index.css          # 唯一入口，10 个有序 @import
├── tokens.css         # 43 行，dark/light design tokens
├── base.css           # 51 行，reset/body/form/svg
├── shell.css          # 528 行，app/topbar/page layout/common panels
├── chat.css           # 390 行，messages/tools/plan
├── approval.css       # 50 行，approval drawer/shared interaction inputs
├── composer.css       # 46 行，composer + Studio tool timeline
├── learner.css        # 116 行，hero/knowledge/review/quiz
├── inspector.css      # 267 行，toolbar/swim lane/event/detail/toast/a11y
├── landing.css        # 396 行，landing 全页面
└── responsive.css     # 136 行，1180px/760px 两层适配
```

`main.tsx` 从 `../styles.css` 改为只 import `./styles/index.css`，根 `frontend/styles.css` 删除。

## 实施方法与等价性

原文件没有 section comments，人工复制 2023 行很容易漏 closing brace、重复规则或改变 cascade。因此本批先按审计出的 selector 边界做一次严格机械切片：

| 原行范围 | 新文件 |
|---:|---|
| 1–43 | tokens.css |
| 44–94 | base.css |
| 95–622 | shell.css |
| 623–1012 | chat.css |
| 1013–1062 | approval.css |
| 1063–1108 | composer.css |
| 1109–1224 | learner.css |
| 1225–1491 | inspector.css |
| 1492–1887 | landing.css |
| 1888–2023 | responsive.css |

`index.css` 严格按上述顺序 import。切片后用 Git HEAD 原文件逐行比较 2023 项，第一轮即完全一致；Vite build 后 CSS asset 仍为拆分前的 `index-BS9qx5GS.css`（29.85 kB / gzip 6.18 kB），说明 bundler 合并后的内容 hash 没有变化。

## Design tokens

`tokens.css` 现在是唯一 design token 入口：

- background surface/base/subtle/elevated；
- border/text/accent/success/warning/danger；
- shadow；
- radius scale；
- sans/mono font stack；
- dark default 与 light override。

本批没有改 token 值；目标是先建立唯一责任边界，后续视觉调整不再跨 feature 搜索变量。

## Architecture gate

新增 `tests/test_frontend_css_architecture.py`，固定：

- 根 legacy styles.css 不得恢复；
- main 只 import styles/index.css；
- 10 个 partial + index 的精确文件集合；
- index import 顺序；
- partial 不得二次 import；
- tokens/base/shell/chat/approval/composer/learner/inspector/landing 各自必须拥有代表性 selector anchors；
- media query 只能出现在 responsive.css，且当前只有 1180/760 两层；
- 每个 partial 少于 600 行，防止重新形成单文件热点。

## 实施中遇到的问题

### 问题 A：按 selector 语义重排会增加无必要视觉风险

部分规则是跨 feature 合并 selector，例如 approval/composer/quiz 共用 textarea base，tool timeline 与 event code 共用 typography。若本批同时拆 selector、重排 declaration，会让“结构变化”和“视觉变化”混在一起，build hash 也无法作为等价证据。

处理：第一阶段只按连续责任区切片并保持原顺序；少量跨 selector 留在其原 cascade 位置。后续若要抽 shared form primitive，应单独提交并用 component/visual tests 验证。

### 问题 B：普通行数命令给出 1748，与源码行号不一致

PowerShell `Measure-Object -Line` 对 Get-Content 的统计与带空行物理行号不一致，若据此切片会漏掉 275 行。

处理：使用 `File.ReadAllLines` 与 `rg -n` 确认真实数组长度为 2023；split 前强制断言长度，split 后断言十段长度之和仍为 2023，再逐索引比较 Git HEAD。

### 问题 C：只看页面能否渲染不足以证明 cascade 等价

页面 DOM 正常不代表 grid/flex/overflow 等布局声明仍生效，肉眼 smoke 也可能漏掉暗色主题差异。

处理：采用三层证据：逐行规则序列一致、Vite CSS content hash 一致、浏览器读取 Landing/Studio/Inspector/Learner 关键节点 computed display/overflow。四路由控制台 warning/error 为 0。

### 问题 D：responsive 若分散回 feature 会失去全局 breakpoint 视图

把每个 media rule 跟随 feature 移动虽局部内聚，但很难审查同一 breakpoint 下页面整体如何从三列降为一列。

处理：当前只保留一个 responsive.css，以 breakpoint 为责任组织；architecture gate 禁止其他 partial 出现 @media。将来断点显著增加时再评估 feature-local container queries。

## 验证结果

| 验证 | 结果 |
|---|---|
| 原规则逐行比较 | 2023/2023 完全一致 |
| `npm run check` | passed |
| `npm test` | 19 files，72 tests passed |
| `npm run build` | passed，2041 modules；CSS hash/size 与拆分前相同 |
| `npm audit --audit-level=low` | 0 vulnerabilities |
| CSS architecture focused pytest | 3 passed |
| 全量后端 pytest | 258 passed，4 warnings |
| Ruff 全仓检查 | passed |
| 既有 mypy gate | passed，10 source files |
| in-app browser production preview | 四路由均渲染；shell/grid/flex/composer/hero computed styles 正常；console warning/error 0 |
| `git diff --check` | passed |

浏览器 tab 与 4173 preview 已清理。pytest warning 仍为既有第三方弃用和本机 `.pytest_cache` 权限提示。

## 保持不变与后续工作

保持不变：全部 CSS declaration、selector、specificity、cascade 顺序、theme token 值、breakpoint、class contract 和 production CSS 内容 hash。

F6 已完成。后续视觉改动应在所属 partial 内进行；跨 feature primitive 的进一步抽取需单独验证，不与本轮机械边界拆分混合。
