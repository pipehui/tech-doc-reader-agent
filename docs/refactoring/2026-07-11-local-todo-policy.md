# 本地任务单与 Git 历史隔离记录

## 1. 问题现象

`.gitignore` 已包含 `todo/`，但 `docs/todo/*.md` 仍会出现在 commit 中。

根因不是 ignore 规则失效，而是这些文件此前已经进入 Git index；基线提交还使用过 `git add -f docs/todo/*.md`，显式绕过了 ignore。`.gitignore` 只决定“未跟踪文件是否自动成为候选”，不会把已跟踪文件自动移出 index。

## 2. 远端洁净要求

要求不是“远端最新 tree 看不到任务单”，而是“推送到远端的提交历史中也不携带任务单”。因此不能只追加一个 `git rm --cached` 提交：那样文件仍存在于更早的提交对象中。

处理时本地 `main` 比 `origin/main` 领先 7 个提交，且尚未推送，所以可以只重建这段未发布历史，不影响任何已共享 commit。

## 3. 实际处理

1. 在 `refs/backup/pre-todo-purge-20260711` 保存清理前 HEAD。该引用不是 branch 或 tag，不会被普通 `git push`、`git push --all` 或 `git push --tags` 带走。
2. 对 `origin/main..HEAD` 的 7 个提交按原顺序读取完整 tree。
3. 在隔离的临时 index 中删除 `docs/todo`，保留作者、提交者、时间和 commit message，使用新的 parent 链重建 commit。
4. 将本地 `main` 指向重建后的 HEAD，刷新真实 index；工作区文件不删除。

第一次临时 index 操作被 Git 拒绝，因为工作区中的任务单比最早 commit 更新，普通 `git rm --cached` 会触发安全检查。解决方式是在隔离 index 中使用 `git rm --cached -f`；真实 index、分支和工作区在失败尝试中没有被修改。

## 4. 验证证据

- 清理前后排除 `docs/todo` 的 `git diff --name-status` 为空，说明代码和其他文档零差异。
- `git log origin/main..HEAD -- docs/todo` 为空，待推送历史不包含任务单。
- `git ls-tree -r HEAD -- docs/todo` 与 `git ls-files docs/todo` 均为空。
- 本地仍有 8 个任务单文件。
- `git check-ignore --no-index -v docs/todo/00-roadmap-audit.md` 命中 `.gitignore` 的 `todo/` 规则。
- 分支仍为 `main...origin/main [ahead 7]`，未执行 push。

## 5. 后续约束

- 不再对 `docs/todo` 使用 `git add -f`。
- 本地执行进度可继续更新 `docs/todo`，但可共享的实施结果、问题和解法写入 `docs/refactoring`。
- 推送前必须再次运行 `git log origin/main..HEAD -- docs/todo` 和 `git ls-tree -r HEAD -- docs/todo`；两者都应为空。
- 本地备份引用在确认新历史稳定后可手动删除；在此之前不要显式推送 `refs/backup/*`。
