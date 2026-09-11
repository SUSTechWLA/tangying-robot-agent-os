# 分支与发布规范

本页定义唯一的长期分支、发布如何标记，以及功能分支的生命周期。目标是任何人打开仓库就能回答"现在应该看哪个分支"。

## 唯一长期分支：`main`

`main` 是默认分支，指向**最新且完整可发布**的状态。任何时候都应该满足：

- `make build && make lint && make generate-check && make test` 全绿；
- `VERSION`、`pyproject.toml`、ROS 包与发布记录描述的版本一致；
- 文档索引指向当前版本。

要基于最新代码工作，就从 `main` 开始：

```bash
git clone https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
git switch main
```

**不存在"每个版本一条长期分支"**。历史上的 `codex/v0.1` … `codex/v0.5-release` 已合并进 `main` 并删除；它们的发布身份由下面的标签保留。

## 发布用标签标记，不靠分支

一次发布 = `main` 上的一个**附注标签**加一份发布记录：

```bash
git switch main && git pull
# VERSION / pyproject.toml / ROS 包 / docs/releases/vX.Y.Z.md 同步后再打标签
git tag -a v0.6.0 -m "v0.6.0: <一句话说明>"
git push origin v0.6.0
```

- 标签名统一 `vMAJOR.MINOR.PATCH`；预发布用 `vX.Y.Z-rc.N`。
- 每个发布标签都必须在 `main` 的历史上（`git merge-base --is-ancestor vX.Y.Z main`）。
- 发布记录写在 `docs/releases/vX.Y.Z.md`，并在 `CHANGELOG.md` 与[文档索引](../README.md)中链接。
- **不要**为了"准备发布"再建一条长期分支：那正是过去"看不出哪个是最新"的原因。

## 功能分支：短命，合并即删

| 前缀 | 用途 | 示例 |
| --- | --- | --- |
| `feat/` | 新功能 | `feat/standard-tool-layer` |
| `fix/` | 缺陷修复 | `fix/ci-render-timeout` |
| `docs/` | 只改文档 | `docs/branching-policy` |
| `chore/` | 构建、依赖、清理 | `chore/deploy-layout` |

规则：

1. 从 `main` 切出，一条分支只做一件事。
2. 合并前必须跑通 `make build && make lint && make generate-check && make test`。
3. 合并后立即删除分支；仓库已开启合并后自动删除，所以通常不用手动做。
4. 分支存活期以天计，不以周计。超过一周说明该拆成更小的改动。

## 历史遗留分支的处理

合并进 `main` 的分支可以直接删除——提交仍可从 `main` 到达。**没有**合并进来的分歧分支属于另一回事：删除前先确认它独有的提交是被废弃的旧实现，还是尚未合并的工作。

```bash
# 这个分支的提交是否都已经在 main 里？
git merge-base --is-ancestor origin/<branch> main && echo "可安全删除" || echo "有 main 没有的提交"
# 有多少独有提交，以及它们是什么
git rev-list --count main..origin/<branch>
git log --oneline main..origin/<branch>
```

只在确认内容已被取代（或已明确废弃）时才删除；否则先合并或先打归档标签。

## 本地工作树

`.worktrees/` 下的工作树是本地开发用的，不属于发布流程。`git worktree list` 会显示每条工作树当前检出的分支；工作树里有未提交改动时不要删除它所属的分支。

## 自动化现状

- CI 在每次 push 与 PR 上运行 `make test` 等检查；`main` 必须保持绿。
- 合并后自动删除头分支（`delete_branch_on_merge`）已开启，避免分支再次堆积。
