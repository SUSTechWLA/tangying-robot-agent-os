# 深入理解分布式机器人 Agent 系统

**数字版 3.2.0 · 2026-10-28**
从跨域协议到模型上下文：架构、推导与故障实验

一本以 Tangying Robot AgentOS 为可检查案例的系统教材。读者从“机器人收到一句目标”出发，追踪目标如何变成受限计划、工具调用、物理观测、持久事实和下一轮决策。重点是理解设计，而不是记住组件名称。

## 读完应当能做什么

| 能力 | 重点章节 | 可以检验的学习成果 |
| --- | --- | --- |
| 把自然语言变成可检查的任务 | 1、3、7 | 为巡检任务定义目标、禁止区、完成谓词与 unknown |
| 解释架构与权限分工 | 2、5、6 | 画出云端、Host、Harness、Runtime、控制器的调用与反馈 |
| 推演失败和重启 | 8、11 | 在四个中断位置判断能否重试、对账或停止 |
| 管理长程上下文与模型计算 | 18 | 拆两消息请求、保护未知命令、推导 KV 显存/时延与压缩回本 |
| 接入异构机器人与同行数据 | 4、9、10、17 | 解释坐标、地图、定位、身份和消息版本冲突 |
| 评价一个方案是否更好 | 12、15、19 | 设计有分母、有对照、有失败记录的实验 |

## 从哪里开始

- 首次阅读：[前言](front/00-preface.md) → [学习导读](front/02-study-guide.md) → [架构](chapters/ch02-architecture-evolution.md) → [完成判据](chapters/ch03-closed-loop-contract.md)。
- 开发者重点：[工具边界](chapters/ch05-tool-layer.md)、[目标编排](chapters/ch07-orchestration.md)、[持久恢复](chapters/ch08-distributed-fault-assumptions.md)、[云边/同行通信](chapters/ch17-cloud-edge-agent-harness.md)、[长上下文](chapters/ch18-long-horizon-context.md)。
- 课堂教学：[两个无硬件小实验](labs/README.md) → [14 组项目实验](appendix/F-learning-labs.md) → [16 周课程与课堂脚本](appendix/G-course-design.md) → [习题指导](appendix/I-exercise-guide.md)。
- 研究与工程：[源码索引](appendix/A-source-map.md)、[实验来源](appendix/C-experiment-index.md)、[工程记录模板](appendix/H-engineering-playbook.md)。

全书四篇、19 章、9 附录，保留 57 道基础/工程/研究题，包含 23 幅离线 SVG 架构/时序/数据流图与 2 幅场景插画。贯穿案例是 AGV 的 A→B→C→A 巡检，再引入无人机观察与同行数据。教学参数与历史实测分开标明；最后一章拆解真实成功、失败和开发者介入。

## 五个核心问题与可检查答案

- **跨域传什么？** 第 17 章给出六条实际路径、字段/权威表、完整仓库推演、带宽和坐标误差算例。
- **模型看什么？** 第 18 章追到两条 messages、当轮 tools、规划白名单、最终请求归档与输出准入。
- **怎样利用 KV cache？** 从注意力推导缓存、1 GiB 显存、8.59 s 迁移下界，再比较前缀布局、失效和隔离；接通可选缓存提示和真实响应 usage；区分服务端计量、未知和实际性能。
- **长程上下文如何保持可信？** 区分 Host 与 managed 两条路径，说明保护状态、热冷分层、scope、哈希和超预算拒绝。
- **一致性到底保证在哪？** 第 8 章用 CAS 竞争、Outbox 中断、网络分区、因果时钟和可合并集合解释边界，避免泛称全局强一致。

本次包含运行时代码：以取消竞态、身份重配、预算耗尽和恢复记录丢失为反例，完成针对性 race 回归和重复软件故障实验。数据入口见[本轮实现与验证](../docs/development/2026-10-28-edge-brain-reliability.md)。没有新增真实模型缓存性能、Gazebo 或实机运动数据。

## 阅读与构建

[合订 Markdown](book.md)可直接阅读。完整目录与出版身份见[出版说明](front/01-publication-notes.md)，改版说明见[RELEASE](RELEASE.md)。

```bash
make book-setup
python3 scripts/build_book_figures.py --check
make book-check
make book-release
.venv-book/bin/python -m pytest -q -o addopts='' tests/book
```

产物在 `artifacts/book/3.2.0/`：`index.html`、`book.epub`、`book.md`、`assets/`、`provenance.json`、`SHA256SUMS` 和许可证。分享 HTML 时保留 assets 目录；EPUB 已内嵌所有插图。生成物不进入 Git。HTML 支持篇/章/节导航、窄屏和打印；图表无需脚本或联网渲染。

代码链接固定到 `63af99305fd8204c0a54d86145cb94d89b754326`，配套项目文档固定到 `021e99c6e8994db474c5bca6771357f6a1b3af03`。书稿身份另由源文件哈希记录。源变更不能覆盖相同版次的已发布目录，请先用新的临时目录预览。上一版 [3.1.0 的完整源身份](editions/3.1.0/README.md)已归档；历史 2.2.0 的清单与恢复步骤在 [editions/2.2.0](editions/2.2.0/README.md)，早期版本也保留。

本书提供可用于授课的教材稿与实验材料；课程效果仍需真实课堂反馈。软件、模型、Gazebo 与实机分别构成不同证据，教材重编不会新增硬件可靠性结论。
