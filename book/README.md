# 深入理解分布式机器人 Agent 系统

**数字版 3.0.0 · 2026-10-21**
从任务闭环到云边协作：架构推导、源码分析与故障实验

一本以 Tangying Robot AgentOS 为可检查案例的系统教材。读者从“机器人收到一句目标”出发，追踪目标如何变成受限计划、工具调用、物理观测、持久事实和下一轮决策。重点是理解设计，而不是记住组件名称。

## 读完应当能做什么

| 能力 | 重点章节 | 可以检验的学习成果 |
| --- | --- | --- |
| 把自然语言变成可检查的任务 | 1、3、7 | 为巡检任务定义目标、禁止区、完成谓词与 unknown |
| 解释架构与权限分工 | 2、5、6 | 画出云端、Host、Harness、Runtime、控制器的调用与反馈 |
| 推演失败和重启 | 8、11 | 在四个中断位置判断能否重试、对账或停止 |
| 管理长程上下文 | 18 | 区分权威账本、有界视图、完整冷档案与跨域读取 |
| 接入异构机器人与同行数据 | 4、9、10、17 | 解释坐标、地图、定位、身份和消息版本冲突 |
| 评价一个方案是否更好 | 12、15、19 | 设计有分母、有对照、有失败记录的实验 |

## 从哪里开始

- 首次阅读：[前言](front/00-preface.md) → [学习导读](front/02-study-guide.md) → [架构](chapters/ch02-architecture-evolution.md) → [完成判据](chapters/ch03-closed-loop-contract.md)。
- 开发者重点：[工具边界](chapters/ch05-tool-layer.md)、[目标编排](chapters/ch07-orchestration.md)、[持久恢复](chapters/ch08-distributed-fault-assumptions.md)、[云边/同行通信](chapters/ch17-cloud-edge-agent-harness.md)、[长上下文](chapters/ch18-long-horizon-context.md)。
- 课堂教学：[两个无硬件小实验](labs/README.md) → [14 组项目实验](appendix/F-learning-labs.md) → [16 周课程与课堂脚本](appendix/G-course-design.md) → [习题指导](appendix/I-exercise-guide.md)。
- 研究与工程：[源码索引](appendix/A-source-map.md)、[实验来源](appendix/C-experiment-index.md)、[工程记录模板](appendix/H-engineering-playbook.md)。

全书四篇、19 章、9 附录，保留 57 道基础/工程/研究题，新增 16 幅离线架构、时序和数据流图。贯穿案例是 AGV 的 A→B→C→A 巡检，再引入无人机观察与同行数据。教学参数与历史实测分开标明；最后一章拆解真实成功、失败和开发者介入。

## 阅读与构建

[合订 Markdown](book.md)可直接阅读。完整目录与出版身份见[出版说明](front/01-publication-notes.md)，改版说明见[RELEASE](RELEASE.md)。

```bash
make book-setup
python3 scripts/build_book_figures.py --check
make book-check
make book-release
.venv-book/bin/python -m pytest -q -o addopts='' tests/book
```

产物在 `artifacts/book/3.0.0/`：`index.html`、`book.epub`、`book.md`、`assets/`、`provenance.json`、`SHA256SUMS` 和许可证。分享 HTML 时保留 assets 目录；EPUB 已内嵌所有插图。生成物不进入 Git。HTML 支持篇/章/节导航、窄屏和打印；图表无需脚本或联网渲染。

代码与项目文档链接固定到 `9e6a58f6ad48c6d9441e95511ddb4d503ea6449e`。书稿身份另由源文件哈希记录。源变更不能覆盖相同版次的已发布目录，请先用新的临时目录预览。历史 2.2.0 的清单与恢复步骤在 [editions/2.2.0](editions/2.2.0/README.md)，早期版本也保留。

本书提供可用于授课的教材稿与实验材料；课程效果仍需真实课堂反馈。软件、模型、Gazebo 与实机分别构成不同证据，教材重编不会新增硬件可靠性结论。
