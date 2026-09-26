# 《深入理解分布式机器人 Agent 系统》

**基于 Tangying Robot AgentOS 的原理、实现与云边部署 · 数字版 1.0.0 · 2026-09-26**

本书从命令回执、动作证据与结果未知讲起，介绍工具、多 Agent、编排、持久状态、分布式故障、仿真、评测和云端/边缘部署。面向能阅读 Go 或 Python 的机器人、后端与 Agent 工程师。

本版完成内容复核与电子出版构建。**书籍可发布不表示案例系统已获生产或实机放行**：Orin NX、GPU 模型服务、实体安全和目标规模机群仍需现场验证。详见[出版说明](front/01-publication-notes.md)与[现场就绪审计](../docs/production/field-readiness-2026-09-26.md)。

## 阅读入口

- [合订本](book.md)：可点击目录的完整 Markdown，由分章自动生成。
- [出版说明](front/01-publication-notes.md)：授权、版本与证据边界。
- [前言](front/00-preface.md)：核心问题与阅读方法。
- [本版审校与发布记录](RELEASE.md)：修订项、验证与保留的历史资料。

| 部分 | 内容 |
| --- | --- |
| 起点 | [1 物理约束](chapters/ch01-physical-constraints.md)、[2 架构演进](chapters/ch02-architecture-evolution.md) |
| 内核 | [3 闭环契约](chapters/ch03-closed-loop-contract.md)、[4 世界模型](chapters/ch04-world-model.md) |
| 执行组织 | [5 工具层](chapters/ch05-tool-layer.md)、[6 多 Agent](chapters/ch06-multi-agent-runtime.md)、[7 编排](chapters/ch07-orchestration.md) |
| 状态与故障 | [8 分布式](chapters/ch08-distributed-fault-assumptions.md)、[9 本地单机](chapters/ch09-local-single-machine.md) |
| 验证与落地 | [10 Sim2Real](chapters/ch10-sim2real.md)、[11 观测恢复](chapters/ch11-observability-recovery.md)、[12 评测训练](chapters/ch12-evaluation-training.md)、[13 部署运维](chapters/ch13-install-deploy-ops.md)、[14 从零搭建](chapters/ch14-build-from-zero.md) |
| 边界与当前架构 | [15 未来方向](chapters/ch15-future-directions.md)、[16 Coding Agent 的33项对照](chapters/ch16-coding-agent-contrast.md)、[17 云端与边缘 Harness](chapters/ch17-cloud-edge-agent-harness.md) |
| 附录 | [A 源码地图与历史统计](appendix/A-source-map.md)、[B 检查表](appendix/B-checklists.md)、[C 实验索引](appendix/C-experiment-index.md)、[D 术语与假设](appendix/D-glossary-and-assumptions.md)、[E 参考资料](appendix/E-references.md) |

快速阅读：前言 → 1 → 3 → 16 → 17。自行搭建：2 → 3 → 5 → 8 → 14。实验研究：3、11、12 与附录 C。部署云边：17 与对应版本的安装指南。

## 版本与证据

当前能力复核固定到源码 `2edd1c1ff07634765c1a671b6d803c679b3b7a5f`（软件 VERSION 0.7.0；书籍版号独立）。第1–15章包含原 v0.6.0/v0.7.0 演进案例，历史数字保留原日期。原统计工作区含未提交变化，不能仅从 `8b9683be8` 重建；详情留在附录 A。

未绑定提交的旧文件行号已移除，按路径与标识符导航。旧 CHANGELOG 引用保留 `CHANGELOG.md@774bd2a2f`，可通过 `git show` 读取。`research/` 保留原写作笔记，可能有本版已纠正的结论，作为历史资料保留，**不纳入正式发布正文**。

## 构建发布文件

在仓库根目录运行（Python 3.11+）：

```bash
# 只校验源文件、链接和合订本同步，不需要安装出版依赖
make book-check

# 独立出版虚拟环境，不安装机器人运行依赖
make book-setup
make book-release

# 从分章重新生成合订本；改书后应先运行此命令
python3 scripts/build_book.py
```

输出位于 `artifacts/book/1.0.0/`，构建产物不进入 Git；CI 提供可下载的完整发布包：

| 文件 | 用途 |
| --- | --- |
| `index.html` | 离线浏览，无外部字体/脚本/CDN；窄屏表格可横滚，支持打印样式 |
| `book.epub` | EPUB 3 电子书，章节导航、许可与阅读顺序 |
| `book.md` | 可点击目录的完整 Markdown |
| `LICENSE` | 分发必须保留的项目许可 |
| `provenance.json` | 版本、源码快照、章节/工具哈希与渲染器版本 |
| `SHA256SUMS` | 发布文件完整性校验 |

HTML/EPUB 的章节交叉链接指向包内内容；仓库引用固定到源码提交，需要网络才能打开。练习答案在导出格式中展开，分章 Markdown 仍保留可折叠版本。发布构建校验 XHTML、资源与锚点，CI 另用固定版本 EPUBCheck 验证格式。构建相同版本不加入机器路径或当前时间，产物可以比较哈希。

## 维护与反馈

编辑分章源，运行生成与校验，再构建发布包。不得手工修改 `book.md`；不得以覆盖旧实验、升级规范或研究笔记的方式“更新”历史。修订报告保留问题、依据和验证结果。

MIT License · [项目仓库](https://github.com/SUSTechWLA/tangying-robot-agent-os)。问题反馈请附书籍版本、章节、源码提交/制品和复现步骤，并移除不必要的个人数据与密钥。
