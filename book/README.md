# 《深入理解分布式机器人 Agent 系统》

最新能力补充：[已知地图覆盖、多层支撑与独立消融](updates/2026-10-18-spatial-coverage-upgrade.md)（2026-10-18），解释巡检目标、预测与完成、规划插件和机制评价；配合[实际软件实验、350 次对照与完整证据](../docs/experiments/2026-10-18-spatial-coverage-upgrade.md)阅读，仍保留数字版 2.2.0 出版身份。

最新研究补充：[异构三维探索、覆盖账本与配对消融](updates/2026-10-17-heterogeneous-spatial-exploration.md)（设计 2026-10-17，[实际软件实验](../docs/experiments/2026-10-18-spatial-exploration.md) 2026-10-18），讲解共同世界模型、本体约束、端侧闭环、团队分工与量化方法；当前是软件研究增量，未形成新的 Gazebo/实机三维探索结论。

最新系统补充：[首次未知场景主动扫描与探索](updates/2026-10-17-exploration-first-scan.md)（2026-10-17），讲解实测转角闭环、frontier、故障与有限证据窗口；保留数字版 2.2.0 原出版身份。

**分布式机器人 AgentOS 的原理、体系、实现与工程实践 · 数字版 2.2.0 · 2026-10-16**

面向软件行业、机器人行业、科研工作者和具备编程基础的大学读者的教材与工程参考书。四篇、19 章、9 附录，共 31 份出版源：从问题域、完成契约和世界事实，到能力、多 Agent、云边通讯、异构接入、长程上下文、评测与运行治理。

本版整合云端粗目标委托、Local/Worker 共享边缘 Host、边缘自主规划、持久预算、上下文与恢复，并保留点云语义、设备数据交换和 ROS 2 二维/地形/三维导航，分清通用原理、当前实现、案例证据与教学建议；以 Tangying 的固定源码为可检查案例，提供 57 道分层习题、12 组实验、16 周课程方案和行业工程记录模板。书籍版号独立于软件 v0.7.0，代码复核固定到 821659df。

## 阅读与使用

| 入口 | 用途 |
| --- | --- |
| [前言](front/00-preface.md)、[学习导读](front/02-study-guide.md) | 背景准备、四篇知识地图、跨行业路线 |
| [合订本](book.md) | 完整 Markdown；按篇和章连续阅读 |
| [源码地图 A](appendix/A-source-map.md)、[术语 D](appendix/D-glossary-and-assumptions.md) | 快速理解全貌、按问题查实现 |
| [实验 F](appendix/F-learning-labs.md) | 从软件合同到仿真的十二组可检查实践 |
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

## 当前架构的阅读入口

云端或操作者通过 `/v1/objectives` 提交粗目标及授权包络，批准后由 Local 或 Worker 中同一 `edgeagent.Host` 组织 Task 规划、Ops 诊断与 Recovery 提案，Executor 调用 Runtime 并验证。两者是部署入口选择，同一台机器人只能有明确的执行所有者。旧 `/v1/tasks` 和现有 UI 保持冻结能力计划路径；不是换模型地址就获得边缘自治。

这条主线已融入第 2/5–9/11/17–19 章；接口和实验入口分别见[目标委托](../docs/guides/delegated-edge-goals.md)、[单机长程报告](../docs/experiments/2026-10-15-single-robot-robustness.md)。原[补充章](updates/2026-10-15-hierarchical-edge-objectives.md)保留为演进记录，读者不再需要它来补齐正文。

按实际工作查阅[学习路线](../docs/learning/README.md)、[开发升级](../docs/development/README.md)、[部署运维](../docs/operations/README.md)和[研究与论文](../docs/research/README.md)，全部专题见[分类目录](../docs/catalog.md)。阅读更新指南时先核对提交；历史实验仍按原身份引用。

## 变更、版本与历史

[出版说明](front/01-publication-notes.md)区分书稿、软件、代码与实验身份；[RELEASE](RELEASE.md)记录本版范围；[本轮更新记录](../docs/development/2026-10-16-architecture-book-refresh.md)解释结构选择、逐章变更和验证。

[2.1.0 恢复档案](editions/2.1.0/README.md)保存上一版 31 份源、合订本、README、生成器与固定依赖的 SHA；[2.0.0 恢复档案](editions/2.0.0/README.md)保存上一教材版 31 份源、合订本、生成器与固定依赖的来源和 SHA。[1.2.0 恢复档案](editions/1.2.0/README.md)保存原 27 份正文、合订本、README 与生成器的 SHA 和固定提交。1.0.0–1.1.3 档案、research 和原失败继续保留。历史长源码/规模统计移出本版主线，按原时点可恢复，不删除原证据。

单机器人长程、双机交接、异构协议与实体现场分别说明覆盖；当前按序协作不是通用异构最优调度，Ops/Recovery 主要为确定性角色。本轮同步最新架构与教学资料，引用已登记的软件、Gazebo 和空间证据，没有新采集硬件证据。未知写入停止对账，完成待投递只补传回执，云端 claim 失效不授权离线续跑。

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

发布文件位于 `artifacts/book/2.2.0/`，不进入 Git；CI 同版次制品提供完整包。

| 文件 | 用途 |
| --- | --- |
| index.html | 离线阅读、篇/章/节导航、窄屏表格与打印 |
| book.epub | EPUB3；嵌套目录、节导航、顺序阅读与许可 |
| book.md | 生成的合订 Markdown |
| LICENSE | 分发保留完整项目许可 |
| provenance.json | 正文/代码/生成器/依赖/渲染器身份 |
| SHA256SUMS | 发布文件完整性 |

HTML/EPUB 不依赖外部脚本、字体或 CDN；源码链接固定到 GitHub 实现快照；配套文档固定到 `d88158ad`，其完整提交另存 `edition.json` 的 `documentation_snapshot`。相同源和固定依赖可重复构建。维护时改分章与清单，再生成、测试、校验 EPUB；勿手改导出或覆盖旧版。

MIT License · [项目仓库](https://github.com/SUSTechWLA/tangying-robot-agent-os)。
