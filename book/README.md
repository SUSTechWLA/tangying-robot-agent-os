# 《深入理解分布式机器人 Agent 系统》

**分布式机器人 AgentOS 的原理、体系、实现与工程实践 · 数字版 2.1.0 · 2026-10-14**

面向软件行业、机器人行业、科研工作者和具备编程基础的大学读者的教材与工程参考书。四篇、19 章、9 附录，共 31 份出版源：从问题域、完成契约和世界事实，到能力、多 Agent、云边通讯、异构接入、长程上下文、评测与运行治理。

本版在教材主线上补齐点云语义执行、持久运行合同、设备数据交换与 ROS 2 地形/三维导航，分清通用原理、当前实现、案例证据与教学建议；以 Tangying 的固定源码为可检查案例，提供 57 道分层习题、10 组实验、16 周课程方案和行业工程记录模板。书籍版号独立于软件 v0.7.0，代码复核仍固定到 9e5aebae。

## 阅读与使用

| 入口 | 用途 |
| --- | --- |
| [前言](front/00-preface.md)、[学习导读](front/02-study-guide.md) | 背景准备、四篇知识地图、跨行业路线 |
| [合订本](book.md) | 完整 Markdown；按篇和章连续阅读 |
| [源码地图 A](appendix/A-source-map.md)、[术语 D](appendix/D-glossary-and-assumptions.md) | 快速理解全貌、按问题查实现 |
| [实验 F](appendix/F-learning-labs.md) | 从软件合同到仿真的十组可检查实践 |
| [课程 G](appendix/G-course-design.md)、[习题指导 I](appendix/I-exercise-guide.md) | 16 周课程、综合项目、全部 57 题评价要点 |
| [工程手册 H](appendix/H-engineering-playbook.md)、[检查表 B](appendix/B-checklists.md) | 需求、能力/本体接入、恢复、升级和运行记录 |
| [案例 C](appendix/C-experiment-index.md)、[文献 E](appendix/E-references.md) | 原始实验与理论/官方规范的来源和适用范围 |

| 篇 | 章节与知识主题 |
| --- | --- |
| 第一篇 基础与体系 | 1–4：物理约束、架构/权威、完成契约、感知/世界模型 |
| 第二篇 执行与分布式机制 | 5–9：能力、Agent 通讯、规划/版本、一致性/恢复、单机 |
| 第三篇 工程与研究方法 | 10–14：异构与 Sim2Real、诊断、评测、部署、开发者上手 |
| 第四篇 扩展与系统综合 | 15–19：边界、软件 Agent 对照、云边、长上下文、完整长任务 |

第一小时可读 2→3→17→19，画出职责和一次故障因果链。软件开发读 5–9/11/18；机器人集成读 1/3–5/10/13/17；科研读 3/8/12/15/18/19。课程和工程模板可按组织改用，本书不声称教材审定或行业认证。

## 当前系统的补充阅读

2026-10-15 新增[分层目标委托与统一边缘 Agent 补充章](updates/2026-10-15-hierarchical-edge-objectives.md)：云端批准粗目标，Local/Worker 共用边缘核心自主细化与受控恢复；[接口](../docs/guides/delegated-edge-goals.md)和[测试](../docs/experiments/2026-10-15-hierarchical-edge-objectives.md)按当前系统阅读。2.1.0 正文、出版包与原固定代码身份不改写。

本版已纳入截至 2026-10-14 的点云语义、边缘与空间升级，代码复核固定到 9e5aebae。后续系统增量见[学习路线与章节对应](../docs/learning/README.md#书籍与系统增量如何一起读)。按实际工作查阅[开发升级](../docs/development/README.md)、[部署运维](../docs/operations/README.md)和[研究与论文](../docs/research/README.md)，所有专题见[分类目录](../docs/catalog.md)。阅读更新指南时先核对提交；历史实验仍按原始身份引用。

## 变更、版本与历史

[出版说明](front/01-publication-notes.md)区分书稿、软件、代码与实验身份；[RELEASE](RELEASE.md)记录本版范围；[本轮更新记录](../docs/development/2026-10-14-book-and-maintenance-review.md)解释结构选择、逐章变更和验证。

[2.0.0 恢复档案](editions/2.0.0/README.md)保存上一教材版 31 份源、合订本、生成器与固定依赖的来源和 SHA。[1.2.0 恢复档案](editions/1.2.0/README.md)保存原 27 份正文、合订本、README 与生成器的 SHA 和固定提交。1.0.0–1.1.3 档案、research 和原失败继续保留。历史长源码/规模统计移出本版主线，按原时点可恢复，不删除原证据。

单机器人长程、双机交接、异构协议与实体现场分别说明覆盖；当前按序协作不是通用异构最优调度，Ops/Recovery 主要为确定性角色。本轮补齐最新系统与教学资料，并修复出版目录覆盖问题；引用已有空间/边缘实验，没有新采集硬件证据。

## 构建与导出

```bash
# 改分章后生成合订本
python3 scripts/build_book.py
# 无渲染依赖也能核对源、链接与合订本
make book-check
# 独立出版环境
make book-setup
make book-release
.venv-book/bin/python -m pytest -q -o addopts='' tests/book
```

发布文件位于 `artifacts/book/2.1.0/`，不进入 Git；CI 同版次制品提供完整包。

| 文件 | 用途 |
| --- | --- |
| index.html | 离线阅读、篇/章/节导航、窄屏表格与打印 |
| book.epub | EPUB3；嵌套目录、节导航、顺序阅读与许可 |
| book.md | 生成的合订 Markdown |
| LICENSE | 分发保留完整项目许可 |
| provenance.json | 正文/代码/生成器/依赖/渲染器身份 |
| SHA256SUMS | 发布文件完整性 |

HTML/EPUB 不依赖外部脚本、字体或 CDN；源码链接固定到 GitHub 快照。相同源和固定依赖可重复构建。维护时改分章与清单，再生成、测试、校验 EPUB；勿手改导出或覆盖旧版。

MIT License · [项目仓库](https://github.com/SUSTechWLA/tangying-robot-agent-os)。
