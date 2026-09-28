# 数字版 1.1.1 · 自然语言目标闭环修订

日期：2026-09-28。代码复核提交：`cf57c25d09ca9bd7d99d2dbb59b35b97407d048c`。发布清单：[edition.json](edition.json)。[1.1.0 恢复说明](editions/1.1.0/README.md)保留上版清单、发布记录和合订本哈希。

## 本版修改

- 第5章同步能力目录 `oneOf` 校验、巡检与限距探索参数、冻结导航目标核对。
- 第17章同步 Nav2 未就绪的有界重试、未对账物理步骤的取消限制、旧 RGB-D 拒绝、探索总里程核对。
- 附录C新增[2026-09-28 自然语言目标矩阵](../docs/experiments/2026-09-28-natural-language-goal-matrix.md)：17 次诊断请求，7 个成功任务、5 个预期拒绝、2 个审批前阻断、2 个运行期失败、1 个虽自报成功但超预算的失败验收。样本不构成统计成功率。
- 保留 1.1.0 的历史数字、上版说明、架构规格和原始失败记录；本轮没有实机认证。

## 验证与边界

本版由 `scripts/build_book.py` 从分章生成合订 Markdown、离线 HTML 与 EPUB；`make book-check` 与 `tests/book` 校验章节、链接、确定性及发布文件。软件门禁、CI 和实际生成结果另见[目标矩阵报告](../docs/experiments/2026-09-28-natural-language-goal-matrix.md)与最终提交。书籍可发布不代表 Orin NX、GPU 模型服务、实体机器人或目标规模机群已获现场放行。
