# 数字版 1.1.2 · 多地点路线到位核验修订

日期：2026-09-28。代码复核提交：`d46ec55eb83408f77bfe7d200b2b7b27ce062cd2`。发布清单：[edition.json](edition.json)。[1.1.1 恢复说明](editions/1.1.1/README.md)保留上版清单、发布记录和合订本哈希。

## 本版修改

- 第5章与第17章同步保守语法完整识别的多地点路线：整句形成一个 `robot.task`，每个冻结目的地分别导航和验证，未知附加动作仍拒绝。
- 附录C记录首轮 case10 对原句的拒绝，以及修复后 case18 在同一 Gazebo 家庭场景的独立任务：走廊、客厅两个不同的 `verify_arrival=CONFIRMED`。矩阵现为18条诊断请求、8个通过任务；保留其他拒绝、运行失败和超预算记录，不将修复前后样本用作成功率估计。
- 原 1.1.1 的源码快照与出版身份原样归档；本版源码快照前移到本次修复提交。未做 Orin/GPU、实体机器人或目标规模机群认证。

## 验证与边界

`go test ./...`、`make lint`、`make generate-check`、`make book-check` 通过；`tests/docs` 与 `tests/book` 共26项通过。Gazebo case18 的冻结计划、两个独立到位事件、原始文件哈希见[目标矩阵报告](../docs/experiments/2026-09-28-natural-language-goal-matrix.md)。数字版由 `scripts/build_book.py` 生成合订 Markdown、离线 HTML 和 EPUB，并进行结构及链接校验。书籍可发布不等于实机放行。
