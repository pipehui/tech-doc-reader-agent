# 前端 legacy 清理与生产静态边界

## 本批目标

仓库同时存在两个前端实现痕迹：

- 当前 React/Vite 入口：`frontend/index.html -> /src/main.tsx`；
- 无任何入口引用的 `frontend/app.js`，约 2060 行旧版原生 JavaScript。

FastAPI 还在 `dist/index.html` 缺失时返回 Vite 源码入口，并把整个 `frontend/` 挂载为 `/static`。浏览器拿到 `/src/main.tsx` 后不会由 FastAPI 编译 TSX，因此响应虽然是 `200`，页面实际上不可执行。

本批只清理静态交付边界，不改 React UI、路由或状态行为。

## 实际改动

### 1. 删除未引用 legacy bundle

删除 `frontend/app.js`。仓库搜索确认 HTML、Vite、FastAPI、文档和当前 TypeScript 源码均没有引用它；删除前后 `npm run build` 生成相同的 JS/CSS asset hash，证明它不在当前构建图中。

新增测试固定：

- `frontend/app.js` 不应重新出现；
- dev `index.html` 的唯一模块入口仍是 `/src/main.tsx`。

### 2. FastAPI 只服务 production dist

新增 `api/frontend.py`，集中负责：

- 安装 `/assets` dist static mount；
- 安装 `/`、`/studio`、`/inspector`、`/learner` SPA index routes；
- 保留 `/graphs` static mount；
- dist 缺失时返回明确 `503` 和 build/Vite dev 提示。

删除整个源码目录的 `/static` mount，也不再把 `frontend/index.html` 当 production fallback。这样 source tree 不会被后端意外公开，缺 build artifact 也不会伪装成成功页面。

`install_frontend(app, dist_dir, graphs_dir)` 可用临时目录独立测试，不需要启动 production lifespan、Redis 或 LangGraph runtime。

### 3. 容器构建真正生成 dist

进一步审计发现，旧 Dockerfile 只执行 `COPY frontend /app/frontend`，没有安装 Node 依赖或运行 Vite build；Compose 又将宿主 `./frontend` 覆盖到 `/app/frontend`。远端干净 checkout 构建出的容器没有 dist，旧 fallback 同样无法运行。

处理：

- Dockerfile 增加 `node:22-alpine` frontend builder stage；
- builder 执行 `npm ci` 与 `npm run build`；
- Python stage 只复制 `/frontend/dist`；
- Compose 删除遮蔽镜像 dist 的 frontend volume；
- `.dockerignore` 排除宿主 `node_modules`、`dist` 与 TypeScript build cache。

`docker compose config --quiet` 已通过。当前 Docker Desktop daemon 未运行，因此本地没有执行实际 image build；CI 和后续可用 Docker 环境仍需执行该层验证。

### 4. CI 增加 build 后 FastAPI static smoke

前端 job 在 Vite build 后安装最小 Python static-test 依赖，并运行 `tests/test_frontend_static.py`。该测试从真实 `frontend/dist/index.html` 提取 hashed asset URL，再通过 FastAPI `StaticFiles` 请求该 asset。

后端 job 没有 dist 时，同一测试明确 skip 真实 build case，同时验证缺失 dist 的四个 SPA route 都返回 503。前端 job build 完成后则不 skip。

## 实施中遇到的问题

### 问题 A：`200 index.html` 不代表前端可运行

旧 fallback 返回的源码 `index.html` 本身合法，因此只断言 HTTP 200 无法发现问题。关键是它引用 `/src/main.tsx`，而 production FastAPI 没有 Vite transform pipeline。

处理：测试同时固定 dist-only index、hashed `/assets` 可访问，以及 `/static/src/main.tsx` 为 404。缺 dist 时必须是 503，不再用“能返回一个 HTML 文件”冒充 readiness。

### 问题 B：直接测试 production app 会触发无关 lifespan

`api.server.app` 的 TestClient 会启动 Redis checkpointer、resources 和 graph，静态资源测试会被外部依赖污染。

处理：把静态安装逻辑抽为独立 composition function，在最小 FastAPI app 上测试。production server 只调用同一个 installer，不复制路由逻辑。

### 问题 C：Compose volume 会覆盖镜像构建结果

即使只补 Docker multi-stage build，`./frontend:/app/frontend` 仍会在容器启动时盖掉镜像中的 dist；宿主 clean checkout 没有 ignored `frontend/dist`，问题依然存在。

处理：删除该 volume。前端开发继续使用独立 Vite 5173 + proxy；Compose 的 8000 端口使用镜像内 production dist。

### 问题 D：浏览器 smoke 的后端边界

Vite preview 能验证 build 产物和 SPA fallback，但不提供 dev-server proxy。直接访问 Studio 时，state/history 请求没有后端而返回错误；UI 将其正确转成“会话恢复失败”系统消息，路由壳、session/user/namespace 控件仍正常渲染。

处理：landing 用于无后端的视觉与 console smoke；Studio 用于验证 deep link 和 tenant/session 恢复，不把预期 API 失败误判为 asset failure。FastAPI dist/asset 行为由 component tests 验证。

## 验证结果

| 验证 | 结果 |
|---|---|
| FastAPI static 定向测试 | `9 passed` |
| 全量后端测试 | `234 passed` |
| Ruff 全仓检查 | passed |
| frontend installer/server mypy（`--follow-imports=skip`） | passed，2 个 source files |
| `npm run build` | passed，2013 modules transformed |
| 删除 legacy 前后 build asset hash | 相同 |
| in-app browser landing DOM | rendered |
| landing console warning/error | 0 |
| Studio deep link | session/user/namespace 与三栏 shell 正常渲染 |
| `docker compose config --quiet` | passed |
| Docker image build | not run，Docker daemon 未启动 |

## 保持不变与有意变化

保持不变：

- Vite dev 仍使用 `127.0.0.1:5173` 和现有 API proxy；
- React route、视觉布局、session/tenant query 参数；
- production `/`、`/studio`、`/inspector`、`/learner` URL；
- `/assets` 与 `/graphs` URL。

有意变化：

- dist 缺失从不可执行的 `200 source index` 改为明确 `503`；
- `/static` 不再暴露整个 frontend source；
- Docker/Compose 交付镜像内 Vite build，不依赖宿主 ignored dist；
- 无入口 legacy `app.js` 被删除。

## 后续工作

- `App.tsx`、`store.ts` 和 `useChatStream.ts` 仍是下一批 feature/slice/reducer 拆分对象；
- 前端尚未引入 Vitest/React Testing Library；
- static job 只验证交付边界，不替代交互 integration 或 visual regression；
- 有 Docker daemon 的环境应补跑完整 image build 与容器 `/ready`/`/assets` smoke。
